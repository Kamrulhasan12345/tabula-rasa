"""Baseline B1: Qwen3-VL-8B zero-shot on Kaggle T4 x2 (kaggle/b1/b1_qwen3vl.py).

The model sees the page image only, so hidden text-layer content can't reach it.
This adapter turns its raw JSON into the PRD §4 schema with the same normalizer as every track.
    kaggle kernels output kamrulhasan12345/tabula-rasa-b1-qwen3vl -p runs/b1/kaggle
"""
import json
import re
import unicodedata
from pathlib import Path

from tabula.b0 import CONTRA, NET
from tabula.normalize import normalize_label, parse_jp_date, parse_value

OUTPUTS = Path(__file__).parent.parent / "runs/b1/kaggle/b1_outputs.json"
UNITS = {"千円": 1000, "百万円": 1_000_000, "円": 1}


def _json(text):
    """Model output -> dict; tolerates markdown fences and trailing prose."""
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def _date(s):
    try:
        return parse_jp_date(s or "")
    except (AttributeError, ValueError):
        return None


def _period(*texts):
    """Column header -> (period_type, start, end). "自 X 至 Y" is a duration ending at Y."""
    for t in texts:
        t = "".join(unicodedata.normalize("NFKC", t or "").split())
        if "至" in t:
            a, _, b = t.partition("至")
            return "duration", _date(a), _date(b)
        if _date(t):
            return "instant", None, _date(t)
    return "instant", None, None


def convert(raw, doc_id, page_no):
    # Models also transcribe 注記番号 / label columns: keep only columns holding real thousands groups
    # (same rule as B0), and take the period from the label when "date" is empty.
    rawcols, rawrows = raw.get("columns") or [], raw.get("rows") or []
    vals = lambda k: [(r.get("values") or [])[k] for r in rawrows if k < len(r.get("values") or []) and (r.get("values") or [])[k]]
    keep = [k for k in range(max([len(rawcols)] + [len(r.get("values") or []) for r in rawrows]))
            if any(re.search(r"\d{1,3}(,\d{3})+", str(v)) for v in vals(k))]
    # headers may include a label / 注記 column, and 内訳 sub-columns have no header: when there are at least as
    # many headers as value columns, the value columns take the rightmost ones in order
    hdrs = rawcols[-len(keep):] if keep and len(rawcols) >= len(keep) else None
    cols = []
    for i, k in enumerate(keep):
        c = hdrs[i] if hdrs else (rawcols[k] if k < len(rawcols) else {})
        ptype, start, end = _period(c.get("date"), c.get("label"))
        cols.append({"id": f"c{i}", "label": normalize_label(c.get("label") or "")[0], "period_type": ptype,
                     "period_end": end, **({"period_start": start} if start else {}), "_src": k})
    rows = []
    for i, r in enumerate(raw.get("rows") or [], 1):
        label, refs = normalize_label(r.get("label") or "")
        rv = list(r.get("values") or [])
        rawv = {c["id"]: (rv[c["_src"]] if c["_src"] < len(rv) and rv[c["_src"]] else None) for c in cols}
        values = {cid: (parse_value(v) if v else None) for cid, v in rawv.items()}
        if not any(rawv.values()):
            rtype = "header"
        elif CONTRA.search(label):
            rtype = "contra"
        elif NET.search(label):
            rtype = "net"
        elif label.endswith("合計"):
            rtype = "total" if len(r.get("path") or []) <= 1 else "subtotal"
        else:
            rtype = "item"
        rows.append({"id": f"r{i:02d}", "label": label, "path": [normalize_label(p)[0] for p in r.get("path") or []],
                     "row_type": rtype, "raw": rawv, "values": values, "footnote_refs": refs})
    for c in cols:
        c.pop("_src")
    unit = re.search(r"千円|百万円|円", raw.get("unit") or "")
    return {"doc_id": doc_id, "page": page_no, "unit": unit and {"raw": unit.group(0), "multiplier": UNITS[unit.group(0)], "currency": "JPY"},
            "columns": cols, "rows": rows}


def predict(pdf_path, page_no, doc_id=None):
    doc_id = doc_id or Path(pdf_path).stem
    key = f"{doc_id}_p{page_no}.png"
    recs = {}
    for f in sorted(OUTPUTS.parent.parent.glob("kaggle*/b1_outputs.json")):  # main run + EDINET-only run
        recs |= json.loads(f.read_text())
    if key not in recs:
        return None, {}  # not run yet: the harness skips this page
    rec = recs[key]
    try:
        raw = _json(rec["text"])
    except json.JSONDecodeError:
        raw = None
    doc = convert(raw, doc_id, page_no) if raw else {"rows": [], "columns": [], "parse_error": True}
    return doc, {"cost_usd": 0.0, "gpu_secs": rec["secs"], "output_tokens": rec["tokens"]}
