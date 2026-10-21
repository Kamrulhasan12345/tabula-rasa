"""Library entry point: parse a PDF into one §4 table per statement page, ready for a UI.

    from tabula.api import parse_pdf
    result = parse_pdf("filing.pdf")          # {"doc_id", "vlm_mode", "table_pages", "image_only_pages", "pages": [...]}

Each page: PRD §4 JSON from tabula.hybrid, plus
  rows[].bbox / rows[].cell_bbox   [x0, top, x1, bottom] in PDF points, origin top-left (B0 pages only;
                                   VLM-only scan pages have none), page_size = [width, height]
  rows[].cell_status               agreed / review / b0_only / vlm_only
  checks, check_results            sum checks inferred from 合計 / 小計 rows, run with the page's rounding rule
  warnings, status                 "ok" | "review" | "failed"
"""
import csv
import html
import io
import re
from pathlib import Path

import pdfplumber

from tabula import vlm
from tabula.checks import infer_checks, run_checks
from tabula.hybrid import parse

WORST = ["review", "vlm_only", "b0_only", "agreed"]


def find_pages(pdf_path):
    """-> (pages with a numeric table, image-only pages). Cheap text test, no OCR."""
    tables, image_only = [], []
    with pdfplumber.open(pdf_path) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            t = p.extract_text() or ""
            if "単位" in t and len(re.findall(r"\d{1,3}(?:,\d{3})+", t)) >= 5:
                tables.append(i)
            elif not t.strip() and p.images:
                image_only.append(i)
    return tables, image_only


def parse_page(pdf_path, page_no, doc_id):
    doc, meta = parse(pdf_path, page_no, doc_id)
    doc.setdefault("rounding", {"method": "unknown", "source": "none"})
    doc["warnings"] = [meta["vlm_error"]] if meta.get("vlm_error") else []
    doc["checks"] = infer_checks(doc) if doc.get("rows") else []
    labels = {r["id"]: r["label"] for r in doc.get("rows") or []}
    doc["check_results"] = [{"total": c["total"], "label": labels.get(c["total"]), "column": cid, "status": st, "diff": d}
                            for c, cid, st, d in run_checks(doc)]
    for r in doc["check_results"]:
        if r["status"] == "fail":
            doc["warnings"].append(f"sum_mismatch: {r['label']} {r['column']} diff={r['diff']}")
            if doc["status"] == "ok":
                doc["status"] = "review"
    doc["page"] = page_no
    return doc


def parse_pdf(pdf_path, pages=None, doc_id=None, on_page=None):
    """pages: 1-based list, default auto-detect. doc_id: key for saved VLM outputs (demo mode).
    on_page(doc): called after each page, for progress reporting."""
    doc_id = doc_id or Path(pdf_path).stem
    if pages:
        tables, image_only = list(pages), []
    else:
        tables, image_only = find_pages(pdf_path)
    out = []
    for n in tables + image_only:
        out.append(parse_page(pdf_path, n, doc_id))
        if on_page:
            on_page(out[-1])
    return {"doc_id": doc_id, "vlm_mode": vlm.mode(), "table_pages": tables, "image_only_pages": image_only, "pages": out}


def row_status(r):
    st = (r.get("cell_status") or {}).values()
    return min(st, key=WORST.index) if st else ""


def to_csv(doc):
    cols = doc.get("columns") or []
    unit = (doc.get("unit") or {}).get("raw") or "?"
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(["path", "label", "row_type"] + [f"{c.get('period_end') or c.get('label') or c['id']} ({unit})" for c in cols] + ["status"])
    for r in doc.get("rows") or []:
        w.writerow([" / ".join(r.get("path") or []), r["label"], r.get("row_type")]
                   + [r["values"].get(c["id"]) for c in cols] + [row_status(r)])
    return f.getvalue()


def to_html(doc):
    """Plain table; each value cell carries class="<cell_status>" and title="<raw>" for the UI to style."""
    cols = doc.get("columns") or []
    e = lambda s: html.escape(str(s if s is not None else ""))
    head = "<tr><th></th>" + "".join(f"<th>{e(c.get('label'))}<br>{e(c.get('period_end'))}</th>" for c in cols) + "</tr>"
    body = []
    for r in doc.get("rows") or []:
        st = r.get("cell_status") or {}
        cells = "".join(f'<td class="{e(st.get(c["id"], ""))}" title="{e((r.get("raw") or {}).get(c["id"]))}">'
                        f'{e(r["values"].get(c["id"]))}</td>' for c in cols)
        body.append(f'<tr class="{e(r.get("row_type"))}" data-level="{e(r.get("level"))}"><th>{e(r["label"])}</th>{cells}</tr>')
    unit = e((doc.get("unit") or {}).get("raw"))
    return f'<table data-unit="{unit}" data-status="{e(doc.get("status"))}">{head}{"".join(body)}</table>'
