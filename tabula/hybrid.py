"""Track D (MVP): B0 where the text layer works, B1 (VLM, image only) where it doesn't, and a
cell-by-cell cross-check where both ran.

Per value cell, row["cell_status"][cid] is one of:
  agreed   - B0 (verified text layer) and B1 (pixels) read the same number
  review   - they disagree, or B0's cell held unverified (possibly hidden) text
  b0_only  - no VLM output for this cell
  reader_only - page has no usable text layer (scan) or B0 failed; B1's reading only
The value kept is B0's when B0 is usable (it is the filer's own text), else B1's.
"""
import os
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


def reader_name():
    """Second reader: TABULA_READER = vlm | typellm | scaledown; default typellm when its key is set, else vlm."""
    return os.environ.get("TABULA_READER") or ("typellm" if os.environ.get("TYPELLM_API_KEY") else "vlm")


def read_second(pdf_path, page_no, doc_id, reader):
    """-> (§4 doc or None, meta). Image-only readers: the PDF text layer never reaches them."""
    if reader == "vlm":
        from tabula.b1 import predict
        return predict(pdf_path, page_no, doc_id)
    from tabula.b1 import _png, convert
    from tabula.remote import READERS
    try:
        raw = READERS[reader](_png(pdf_path, page_no))
    except OSError as e:  # API down / timeout: the page continues on B0 alone
        return None, {"reader_error": f"{reader}: {type(e).__name__}: {e}"}
    return (convert(raw, doc_id, page_no) if raw else None), {}


def parse(pdf_path, page_no, doc_id=None, b0=None, second=None, reader=None):
    """second: a precomputed (doc, meta) from the second reader (eval caches); else it is called now."""
    reader = reader or reader_name()
    b0 = b0 if b0 is not None else run_b0(pdf_path, page_no)
    b1, meta = second if second is not None else read_second(pdf_path, page_no, doc_id or Path(pdf_path).stem, reader)
    return merge(b0, b1, "b1" if reader == "vlm" else reader), meta
