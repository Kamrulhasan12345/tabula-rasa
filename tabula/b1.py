"""Baseline B1: Qwen3-VL-8B zero-shot on Kaggle T4 x2 (kaggle/b1/b1_qwen3vl.py).

The model sees the page image only, so hidden text-layer content can't reach it.
This adapter turns its raw JSON into the PRD §4 schema with the same normalizer as every track.
    kaggle kernels output kamrulhasan12345/tabula-rasa-b1-qwen3vl -p runs/b1/kaggle
"""
import json
import re
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


def convert(raw, doc_id, page_no):
    cols = [{"id": f"c{i}", "label": normalize_label(c.get("label") or "")[0], "period_type": "instant",
             "period_end": _date(c.get("date"))} for i, c in enumerate(raw.get("columns") or [])]
    rows = []
    for i, r in enumerate(raw.get("rows") or [], 1):
        label, refs = normalize_label(r.get("label") or "")
        vals = list(r.get("values") or [])
        rawv = {c["id"]: (vals[k] if k < len(vals) and vals[k] else None) for k, c in enumerate(cols)}
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
