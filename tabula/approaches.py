"""Extraction approaches. Each: (pdf_path, page_no, gold) -> (pred_doc, meta).
pred_doc follows PRD §4; meta may hold cost_usd, first_pass (pred before any repair loop).
Only "oracle" reads gold; it exists to sanity-check the harness (must score 1.0 everywhere).
"""


def oracle(pdf_path, page_no, gold):
    return gold, {"cost_usd": 0.0}


def b0(pdf_path, page_no, gold):
    from tabula.hybrid import run_b0
    return run_b0(pdf_path, page_no), {"cost_usd": 0.0}


def b1(pdf_path, page_no, gold):
    from tabula.b1 import predict
    return predict(pdf_path, page_no, gold["doc_id"])


def hybrid(pdf_path, page_no, gold):
    import json
    from pathlib import Path
    from tabula.hybrid import parse
    cached = Path(__file__).parent.parent / f"runs/b0/{gold['doc_id']}_p{page_no}.json"  # reuse B0's eval run (OCR is ~17 s/page)
    doc, meta = parse(pdf_path, page_no, gold["doc_id"], json.loads(cached.read_text()) if cached.exists() else None)
    return doc, {**meta, "gpu_secs": None}


APPROACHES = {"oracle": oracle, "b0": b0, "b1": b1, "hybrid": hybrid}
