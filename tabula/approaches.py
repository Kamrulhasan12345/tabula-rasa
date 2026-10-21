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


def _remote(name):
    """Track B/C table reader: page PNG -> vendor API -> b1.convert. Raw replies are cached in runs/<name>/raw/
    so a run with a key once can be rescored for free; no key and no cache -> page skipped."""
    def run(pdf_path, page_no, gold):
        import json
        import time
        from pathlib import Path
        from tabula.b1 import _png, convert
        from tabula.remote import READERS
        cache = Path(__file__).parent.parent / f"runs/{name}/raw/{gold['doc_id']}_p{page_no}.json"
        if cache.exists():
            rec = json.loads(cache.read_text())
        else:
            t0 = time.perf_counter()
            try:
                raw = READERS[name](_png(pdf_path, page_no))
            except OSError as e:  # HTTP error / timeout: not the model's fault; skip, retried next run
                print(f"{name} {gold['doc_id']} p{page_no}: {type(e).__name__}: {str(e)[:200]}", flush=True)
                return None, {}
            except (ValueError, KeyError, TypeError) as e:  # unusable reply: scored as an empty table
                print(f"{name} {gold['doc_id']} p{page_no}: bad reply {type(e).__name__}", flush=True)
                raw = {"rows": [], "columns": []}
            if raw is None:
                return None, {}
            rec = {"raw": raw, "secs": time.perf_counter() - t0}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(rec, ensure_ascii=False))
        return convert(rec["raw"], gold["doc_id"], page_no), {"gpu_secs": rec["secs"]}
    return run


APPROACHES = {"oracle": oracle, "b0": b0, "b1": b1, "hybrid": hybrid,
              "scaledown": _remote("scaledown"), "typellm": _remote("typellm")}
