"""Parse a Japanese financial-filing PDF into one table per statement page.

    .venv/bin/python -m tabula FILE.pdf [-p 3,5] [-o out/] [--doc-id ID]
Writes out/<doc>_p<n>.json (PRD §4 schema + per-cell status, boxes, sum checks), .csv and .html.
Without -p, pages that print 単位 and comma-grouped numbers are parsed, plus image-only pages (VLM only).
Options (--reader, --crosscheck, --demo, --vlm-url, ...) override the same-named settings in .env (see .env.example).
"""
import argparse
import json
import sys
from pathlib import Path

from tabula.api import parse_pdf, to_csv, to_html


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tabula", description=__doc__.splitlines()[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-p", "--pages", help="comma-separated 1-based pages (default: auto-detect)")
    ap.add_argument("-o", "--out", type=Path, default=Path("out"))
    ap.add_argument("--doc-id", help="id saved replies are keyed by, for --demo on eval filings (default: file stem)")
    ap.add_argument("--reader", choices=["typellm", "scaledown", "vlm", "none"], help="image reader (default: typellm if its key is set, else vlm)")
    ap.add_argument("--crosscheck", choices=["all", "needed"], help="read every page from the image too, or only pages the text layer can't vouch for")
    ap.add_argument("--demo", action="store_true", help="replay saved replies (eval pages): no keys, no network")
    ap.add_argument("--no-fallback", action="store_true", help="don't try other readers when the chosen one fails")
    ap.add_argument("--vlm-url", help="OpenAI-compatible base URL, e.g. https://api.openai.com/v1 (key: TABULA_VLM_KEY)")
    ap.add_argument("--vlm-model", help="model name for --vlm-url, e.g. gpt-4o")
    a = ap.parse_args(argv)
    settings = {k: v for k, v in {"TABULA_READER": a.reader, "TABULA_CROSSCHECK": a.crosscheck, "TABULA_DEMO": "1" if a.demo else None,
                                  "TABULA_FALLBACK": "0" if a.no_fallback else None, "TABULA_VLM_URL": a.vlm_url,
                                  "TABULA_VLM_MODEL": a.vlm_model}.items() if v}
    a.out.mkdir(parents=True, exist_ok=True)

    def report(doc):
        stem = a.out / f"{res_id}_p{doc['page']}"
        stem.with_suffix(".json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        stem.with_suffix(".csv").write_text(to_csv(doc), encoding="utf-8-sig")  # BOM so Excel reads the Japanese
        stem.with_suffix(".html").write_text(to_html(doc))
        cells = [s for r in doc.get("rows") or [] for s in (r.get("cell_status") or {}).values()]
        fails = sum(c["status"] == "fail" for c in doc["check_results"])
        print(f"  p{doc['page']}: {doc.get('statement_type') or '-'} | {len(doc.get('rows') or [])} rows, {len(cells)} cells, "
              f"{cells.count('review')} to review | sums {len(doc['check_results']) - fails} ok / {fails} fail | "
              f"engine {doc['engine']} | {doc['status']} -> {stem}.csv")
        if doc.get("skipped"):
            print(f"    reader skipped: {doc['skipped']}")
        for w in doc["warnings"] + [f"security: {f}" for f in doc.get("security_flags") or []]:
            print(f"    {w}")

    res_id = a.doc_id or a.pdf.stem
    pages = [int(x) for x in a.pages.split(",")] if a.pages else None
    res = parse_pdf(a.pdf, pages, res_id, on_page=report, settings=settings)
    print(f"{a.pdf}: reader {res['reader']}{' (demo replay)' if res['demo'] else ''} | crosscheck {res['crosscheck']}, "
          f"{res['reader_calls']} reader calls | table pages {res['table_pages']}"
          + (f" | image-only {res['image_only_pages']}" if res["image_only_pages"] else ""))


if __name__ == "__main__":
    sys.exit(main())
