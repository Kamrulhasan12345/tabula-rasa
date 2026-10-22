"""Track D (MVP): B0 where the text layer works, B1 (VLM, image only) where it doesn't, and a
cell-by-cell cross-check where both ran.

Per value cell, row["cell_status"][cid] is one of:
  agreed   - B0 (verified text layer) and B1 (pixels) read the same number
  review   - they disagree, or B0's cell held unverified (possibly hidden) text
  b0_only  - no VLM output for this cell
  reader_only - page has no usable text layer (scan) or B0 failed; B1's reading only
The value kept is B0's when B0 is usable (it is the filer's own text), else B1's.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from tabula import config
from tabula.evaluate import _col_map, align


def run_b0(pdf_path, page_no):
    """B0, retried on a qpdf-decrypted copy (EDINET PDFs set permission flags that stop Camelot)."""
    from tabula.b0 import extract
    try:
        return extract(pdf_path, page_no)
    except Exception:
        pass
    try:
        with tempfile.TemporaryDirectory() as d:
            dec = Path(d) / Path(pdf_path).name
            subprocess.run(["qpdf", "--decrypt", str(pdf_path), str(dec)], check=True, capture_output=True)
            doc = extract(dec, page_no)
            doc["doc_id"] = Path(pdf_path).stem
            return doc
    except Exception as e:  # scans, pages without a table
        return {"rows": [], "columns": [], "error": f"{type(e).__name__}: {e}"}


def usable(doc):
    return bool(doc and doc.get("columns") and any(isinstance(v, int) for r in doc["rows"] for v in r["values"].values()))


def merge(b0, b1, reader="b1"):
    if not usable(b0):
        if not usable(b1):
            return {**(b0 or {}), "engine": "none", "status": "failed"}
        doc = {**b1, "engine": reader}
        for r in doc["rows"]:
            r["cell_status"] = {cid: "reader_only" for cid, v in r["values"].items() if v is not None}
        doc["status"] = "review"
        return doc
    doc = {**b0, "engine": f"b0+{reader}" if usable(b1) else "b0"}
    m = align(b0["rows"], b1["rows"]) if usable(b1) else {}
    cm = _col_map(b0, b1) if usable(b1) else {}
    b1cols = {c["id"]: c for c in (b1 or {}).get("columns") or []}
    doc["columns"] = [dict(c) for c in b0["columns"]]
    for c in doc["columns"]:  # the router often finds no header dates; the VLM reads them
        v = b1cols.get(cm.get(c["id"]))
        if not c.get("period_end") and v and v.get("period_end"):
            c.update({k: v[k] for k in ("period_type", "period_start", "period_end") if v.get(k)}, period_source="vlm")
    for i, r in enumerate(doc["rows"]):
        p = b1["rows"][m[i]] if i in m else None
        st = {}
        for cid, raw in r["raw"].items():
            v = r["values"].get(cid)
            if raw is None and v is None:
                continue
            pv = (p.get("values") or {}).get(cm.get(cid)) if p else None
            if v is None:  # text present but tainted / unparsable
                st[cid] = "review"
            elif not usable(b1):
                st[cid] = "b0_only"
            else:
                st[cid] = "agreed" if pv == v else "review"
        r["cell_status"] = st
    statuses = [s for r in doc["rows"] for s in r["cell_status"].values()]
    # unverified words outside the table (OCR reading a header differently) stay listed in security_flags but
    # can't reach a value: cells are built from verified words only. A row with unverified text is reviewed.
    doc["status"] = "review" if "review" in statuses or any(r.get("tainted") for r in doc["rows"]) else "ok"
    return doc


CHAIN = ["typellm", "scaledown", "vlm"]  # fallback order after the chosen reader (best measured first)
ROOT = Path(__file__).parent.parent


def reader_name(settings=None):
    """TABULA_READER = typellm | scaledown | vlm | none; default typellm when its key is set, else vlm."""
    return config.get("TABULA_READER", settings) or ("typellm" if config.get("TYPELLM_API_KEY", settings) else "vlm")


def demo(settings=None):
    """TABULA_DEMO=1: every reader replays saved replies (eval pages), never touches the network or needs keys."""
    return config.get("TABULA_DEMO", settings) == "1"


def _fetch(reader, key, png, settings=None):
    """One reader's raw table for a page, cheapest source first:
    demo replay (runs/<reader>/raw/<key>.json, Kaggle outputs for vlm) -> reply cache keyed by the page image's
    hash (and, for a VLM, its endpoint + model) -> live call. None = this reader has nothing."""
    import hashlib
    from tabula import remote, vlm
    from tabula.b1 import _json
    if demo(settings):
        if reader == "vlm":
            rec = vlm._saved().get(key + ".png")
            return _json(rec["text"]) if rec else None
        f = ROOT / f"runs/{reader}/raw/{key}.json"
        return json.loads(f.read_text())["raw"] if f.exists() else None
    if not config.available(reader, settings):
        return None
    img = png()
    tag = reader
    if reader == "vlm":  # different endpoints / models must not share replies
        tag += "/" + hashlib.sha256(f"{config.get('TABULA_VLM_URL', settings)}|{config.get('TABULA_VLM_MODEL', settings)}".encode()).hexdigest()[:12]
    cache = Path(config.get("TABULA_CACHE", settings, str(ROOT / "runs/cache"))) / tag / (hashlib.sha256(img).hexdigest()[:32] + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    if reader == "vlm":
        rec = vlm._call(img, settings)
        raw = _json(rec["text"]) if rec else None
    else:
        raw = remote.READERS[reader](img, settings)
    if raw:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw, ensure_ascii=False))
    return raw


def read_second(pdf_path, page_no, doc_id, reader, settings=None):
    """-> (§4 doc or None, meta). Tries the chosen reader, then (TABULA_FALLBACK, default on) the rest of CHAIN,
    until one answers. reader "none" = text layer only. Image-only readers: the PDF text layer never reaches them."""
    from functools import lru_cache
    from tabula.b1 import _png, convert
    if reader == "none":
        return None, {"reader": None, "skipped": "reader set to none"}
    png = lru_cache(maxsize=1)(lambda: _png(pdf_path, page_no))  # rendered once, only if a live call needs it
    chain = [reader] + ([c for c in CHAIN if c != reader] if config.get("TABULA_FALLBACK", settings, "1") != "0" else [])
    errors = []
    for r in chain:
        try:
            raw = _fetch(r, f"{doc_id}_p{page_no}", png, settings)
        except (OSError, ValueError, KeyError) as e:  # API down / timeout / unreadable reply: try the next reader
            errors.append(f"{r}: {type(e).__name__}: {str(e)[:120]}")
            continue
        if raw:
            meta = {"reader": r, **({"reader_error": "; ".join(errors)} if errors else {})}
            return convert(raw, doc_id, page_no), meta
    return None, {"reader": None, **({"reader_error": "; ".join(errors)} if errors else {})}


def needs_reader(b0):
    """TABULA_CROSSCHECK=needed: call the paid reader only when B0 can't vouch for the page on its own:
    no usable table (scan), unverified text, a failing sum check, or a column without a period."""
    from tabula.checks import infer_checks, run_checks
    if not usable(b0) or any(r.get("tainted") for r in b0["rows"]) or any(v is None and raw for r in b0["rows"]
                                                                           for v, raw in zip(r["values"].values(), r["raw"].values())):
        return True
    if any(not c.get("period_end") for c in b0["columns"]):
        return True
    doc = {**b0, "rounding": b0.get("rounding") or {"method": "unknown"}, "checks": infer_checks(b0)}
    return not doc["checks"] or any(st == "fail" for *_, st, _ in run_checks(doc))


def parse(pdf_path, page_no, doc_id=None, b0=None, second=None, reader=None, settings=None):
    """second: a precomputed (doc, meta) from the second reader (eval caches / parallel prefetch); else called now."""
    reader = reader or reader_name(settings)
    b0 = b0 if b0 is not None else run_b0(pdf_path, page_no)
    b1, meta = second if second is not None else read_second(pdf_path, page_no, doc_id or Path(pdf_path).stem, reader, settings)
    used = meta.get("reader", reader)
    return merge(b0, b1, "b1" if used in ("vlm", None) else used), meta
