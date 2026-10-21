"""Track D (MVP): B0 where the text layer works, B1 (VLM, image only) where it doesn't, and a
cell-by-cell cross-check where both ran.

Per value cell, row["cell_status"][cid] is one of:
  agreed   - B0 (verified text layer) and B1 (pixels) read the same number
  review   - they disagree, or B0's cell held unverified (possibly hidden) text
  b0_only  - no VLM output for this cell
  vlm_only - page has no usable text layer (scan) or B0 failed; B1's reading only
The value kept is B0's when B0 is usable (it is the filer's own text), else B1's.
"""
import subprocess
import tempfile
from pathlib import Path

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


def merge(b0, b1):
    if not usable(b0):
        if not usable(b1):
            return {**(b0 or {}), "engine": "none", "status": "failed"}
        doc = {**b1, "engine": "b1"}
        for r in doc["rows"]:
            r["cell_status"] = {cid: "vlm_only" for cid, v in r["values"].items() if v is not None}
        doc["status"] = "review"
        return doc
    doc = {**b0, "engine": "b0+b1" if usable(b1) else "b0"}
    m = align(b0["rows"], b1["rows"]) if usable(b1) else {}
    cm = _col_map(b0, b1) if usable(b1) else {}
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
    doc["status"] = "review" if "review" in statuses or doc.get("security_flags") else "ok"
    return doc


def parse(pdf_path, page_no, doc_id=None, b0=None):
    from tabula.b1 import predict
    b0 = b0 if b0 is not None else run_b0(pdf_path, page_no)
    b1, meta = predict(pdf_path, page_no, doc_id)
    return merge(b0, b1), meta
