"""Parse a Japanese financial-filing PDF into one table per statement page.

    .venv/bin/python -m tabula FILE.pdf [-p 3,5] [-o out/] [--doc-id ID]
Writes out/<doc>_p<n>.json (PRD §4 schema + per-cell status) and out/<doc>_p<n>.csv.
Without -p, pages that print 単位 and comma-grouped numbers are parsed; image-only pages are listed
for the VLM run (kaggle/b1u). VLM outputs already in runs/b1/kaggle*/ are cross-checked automatically.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pdfplumber

from tabula.hybrid import parse

WORST = ["review", "vlm_only", "b0_only", "agreed"]


def find_pages(pdf_path):
    tables, image_only = [], []
    with pdfplumber.open(pdf_path) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            t = p.extract_text() or ""
            if "単位" in t and len(re.findall(r"\d{1,3}(?:,\d{3})+", t)) >= 5:
                tables.append(i)
            elif not t.strip() and p.images:
                image_only.append(i)
    return tables, image_only


def write_csv(doc, path):
    cols = doc.get("columns") or []
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads the Japanese
        w = csv.writer(f)
        w.writerow(["path", "label", "row_type"] + [c.get("period_end") or c.get("label") or c["id"] for c in cols] + ["status"])
        for r in doc.get("rows") or []:
            st = (r.get("cell_status") or {}).values()
            w.writerow([" / ".join(r.get("path") or []), r["label"], r.get("row_type")]
                       + [r["values"].get(c["id"]) for c in cols] + [min(st, key=WORST.index) if st else ""])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tabula", description=__doc__.splitlines()[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-p", "--pages", help="comma-separated 1-based pages (default: auto-detect)")
    ap.add_argument("-o", "--out", type=Path, default=Path("out"))
    ap.add_argument("--doc-id", help="id the VLM outputs are keyed by (default: file stem)")
    a = ap.parse_args(argv)
    doc_id = a.doc_id or a.pdf.stem
    if a.pages:
        pages, image_only = [int(x) for x in a.pages.split(",")], []
    else:
        pages, image_only = find_pages(a.pdf)
    a.out.mkdir(parents=True, exist_ok=True)
    print(f"{a.pdf}: {len(pages)} table pages {pages}" + (f", image-only (need VLM): {image_only}" if image_only else ""))
    for n in pages + image_only:
        doc, _ = parse(a.pdf, n, doc_id)
        stem = a.out / f"{doc_id}_p{n}"
        stem.with_suffix(".json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        write_csv(doc, stem.with_suffix(".csv"))
        cells = [s for r in doc.get("rows") or [] for s in (r.get("cell_status") or {}).values()]
        print(f"  p{n}: {doc.get('statement_type') or '-'} | {len(doc.get('rows') or [])} rows, {len(cells)} cells, "
              f"{cells.count('review')} to review | engine {doc['engine']} | {doc['status']} -> {stem}.csv")
        for flag in doc.get("security_flags") or []:
            print(f"    security: {flag}")


if __name__ == "__main__":
    sys.exit(main())
