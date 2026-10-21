"""Extraction approaches. Each: (pdf_path, page_no, gold) -> (pred_doc, meta).
pred_doc follows PRD §4; meta may hold cost_usd, first_pass (pred before any repair loop).
Only "oracle" reads gold; it exists to sanity-check the harness (must score 1.0 everywhere).
"""


def oracle(pdf_path, page_no, gold):
    return gold, {"cost_usd": 0.0}


def b0(pdf_path, page_no, gold):
    from tabula.b0 import extract
    return extract(pdf_path, page_no), {"cost_usd": 0.0}


def b1(pdf_path, page_no, gold):
    from tabula.b1 import predict
    return predict(pdf_path, page_no, gold["doc_id"])


APPROACHES = {"oracle": oracle, "b0": b0, "b1": b1}
