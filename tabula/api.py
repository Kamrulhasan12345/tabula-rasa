"""Library entry point: parse a PDF into one §4 table per statement page, ready for a UI.

    from tabula.api import parse_pdf
    result = parse_pdf("filing.pdf")  # {"doc_id", "reader", "crosscheck", "demo", "reader_calls", "table_pages",
                                      #  "image_only_pages", "statements", "pages": [...]}

Each page: PRD §4 JSON from tabula.hybrid, plus
  rows[].bbox / rows[].cell_bbox   [x0, top, x1, bottom] in PDF points, origin top-left (B0 pages only;
                                   VLM-only scan pages have none), page_size = [width, height]
  rows[].cell_status               agreed / review / b0_only / reader_only
  checks, check_results            sum checks inferred from 合計 / 小計 rows, run with the page's rounding rule
  warnings, status                 "ok" | "review" | "failed"
Document level: statements = pages grouped by continues_on_next_page, with 資産合計 = 負債純資産合計 checked.
"""
import csv
import html
import io
import os
import re
from pathlib import Path

import pdfplumber

from tabula.checks import infer_checks, run_checks
from tabula.hybrid import parse

WORST = ["review", "reader_only", "b0_only", "agreed"]


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


def locate_cells(pdf_path, doc):
    """Boxes for a VLM-only (scan) page: OCR the render, find each row's label line top to bottom, then the
    OCR boxes on that line whose digits equal each value's digits, left to right. Unfound cells stay None."""
    from rapidfuzz.distance import Levenshtein
    from tabula.security import DPI, _norm, ocr_lines, render
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[doc["page"] - 1]
        lines, size = ocr_lines(render(page, DPI)), [round(float(page.width), 1), round(float(page.height), 1)]
    k = 72 / DPI
    pt = lambda o: [round(float(v) * k, 1) for v in o[1:]]
    digits = lambda t: re.sub(r"\D", "", t or "")
    cols = [c["id"] for c in doc.get("columns") or []]
    y = -1
    for r in doc.get("rows") or []:
        lab = _norm(r["label"])
        cands = [o for o in lines if (o[2] + o[4]) / 2 > y and lab and Levenshtein.normalized_distance(lab, o[0][:len(lab) + 2]) <= 0.3]
        if not cands:
            continue
        lo = min(cands, key=lambda o: o[2])
        y = (lo[2] + lo[4]) / 2
        # scans are skewed up to ~1.5°: allow the row to drift 3% of the horizontal distance from its label
        drift = lambda o: 0.03 * (o[1] - lo[3])
        same_line = sorted((o for o in lines if o is not lo and o[1] > lo[1]
                            and o[2] - drift(o) <= y <= o[4] + drift(o)), key=lambda o: o[1])
        r["cell_bbox"] = {}
        for cid in cols:
            d = digits((r.get("raw") or {}).get(cid))
            hit = next((o for o in same_line if d and digits(o[0]) == d), None)
            r["cell_bbox"][cid] = pt(hit) if hit else None
            if hit:
                same_line = same_line[same_line.index(hit) + 1:]  # columns run left to right
        r["bbox"] = pt(lo)
    doc["page_size"] = size
    doc["bbox_source"] = "ocr"


def finish_page(pdf_path, page_no, doc, meta):
    """Merged page -> UI-ready page: sum checks, warnings, boxes for reader-only pages, optional Track C evidence."""
    doc.setdefault("rounding", {"method": "unknown", "source": "none"})
    doc["warnings"] = [meta["reader_error"]] if meta.get("reader_error") else []
    doc["reader"] = meta.get("reader")
    if meta.get("skipped"):
        doc["skipped"] = meta["skipped"]
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
    if doc.get("engine") not in ("b0", "none") and not doc["engine"].startswith("b0+"):  # reader-only (scan): no word boxes
        locate_cells(pdf_path, doc)
    _decision_models(pdf_path, doc)
    return doc


def parse_page(pdf_path, page_no, doc_id):
    doc, meta = parse(pdf_path, page_no, doc_id)
    return finish_page(pdf_path, page_no, doc, meta)


def _decision_models(pdf_path, doc):
    """Optional Track C evidence (off unless configured, see tabula/remote.py): Amos checks each disputed or
    VLM-only cell against the page image -> rows[].image_check {cid: P}; Sqwish classifies row types ->
    rows[].row_type_model {type, p} (None type = abstained). Evidence only: values and statuses don't change."""
    from tabula import remote
    from tabula.b1 import _png
    rows = doc.get("rows") or []
    cols = [c["id"] for c in doc.get("columns") or []]
    cells = [((r["id"], cid), r["label"], cols.index(cid), (r.get("raw") or {}).get(cid) or str(r["values"].get(cid)))
             for r in rows for cid, st in (r.get("cell_status") or {}).items() if st in ("review", "reader_only") and cid in cols]
    try:
        probs = remote.amos_verify(_png(pdf_path, doc["page"], dpi=150), cells) if cells and os.environ.get("TABULA_AMOS") == "1" else None
        kinds = remote.sqwish_row_types([r for r in rows if not r.get("tainted")])
    except OSError as e:
        doc["warnings"].append(f"decision model unavailable: {type(e).__name__}")
        return
    for r in rows:
        if probs:
            r["image_check"] = {cid: p for (rid, cid), p in probs.items() if rid == r["id"]}
        if kinds and r["id"] in kinds:
            r["row_type_model"] = {"type": kinds[r["id"]][0], "p": kinds[r["id"]][1]}


def _bs_key(label):
    s = re.sub(r"\s", "", label)
    return "assets" if s == "資産合計" else "liab_eq" if re.fullmatch(r"負債(及び|・)?純資産(の部)?合計", s) else None


def stitch(pages):
    """Group pages into statements (a page flagged continues_on_next_page joins the next one) and check
    資産合計 = 負債純資産合計 across the group, exactly (both are printed from the same ledger)."""
    groups, cur = [], None
    for d in sorted(pages, key=lambda d: d["page"]):
        if cur and cur["pages"][-1]["continues_on_next_page"] and d["page"] == cur["pages"][-1]["page"] + 1:
            cur["pages"].append(d)
        else:
            cur = {"pages": [d]}
            groups.append(cur)
    out = []
    for g in groups:
        ps = g["pages"]
        st = {"statement_type": next((d.get("statement_type") for d in ps if d.get("statement_type")), None),
              "pages": [d["page"] for d in ps], "checks": []}
        rows = {}
        for d in ps:
            for r in d.get("rows") or []:
                if _bs_key(r["label"]):
                    rows[_bs_key(r["label"])] = (d, r)
        if len(rows) == 2:
            (da, ra), (dl, rl) = rows["assets"], rows["liab_eq"]
            for i, ca in enumerate(da.get("columns") or []):  # same period, else same position
                cl = next((c for c in dl.get("columns") or [] if ca.get("period_end") and c.get("period_end") == ca["period_end"]),
                          (dl.get("columns") or [None] * (i + 1))[i] if i < len(dl.get("columns") or []) else None)
                a, l_ = ra["values"].get(ca["id"]), rl["values"].get(cl["id"]) if cl else None
                ok = isinstance(a, int) and isinstance(l_, int)
                st["checks"].append({"rule": "資産合計 = 負債純資産合計", "column": ca.get("period_end") or ca["id"],
                                     "status": ("ok" if a == l_ else "fail") if ok else "skip", "diff": a - l_ if ok else None})
                if ok and a != l_:
                    for d in ps:
                        d["warnings"].append(f"balance_mismatch: 資産合計 - 負債純資産合計 = {a - l_} ({st['checks'][-1]['column']})")
                        d["status"] = "review" if d["status"] == "ok" else d["status"]
        out.append(st)
    return out


def parse_pdf(pdf_path, pages=None, doc_id=None, on_page=None, settings=None):
    """pages: 1-based list, default auto-detect. doc_id: key for saved replies (demo mode).
    on_page(doc): called after each page, for progress reporting.
    settings: per-request overrides of the env-var options (tabula/config.py), e.g. {"TABULA_READER": "vlm",
              "TABULA_VLM_URL": ..., "TABULA_CROSSCHECK": "needed"}.

    Cost / latency policy:
      TABULA_CROSSCHECK=all (default)  every page is read by the second reader too: every cell gets agreed/review.
      TABULA_CROSSCHECK=needed         the reader is called only for pages B0 can't vouch for (hybrid.needs_reader):
                                       scans, unverified text, failing sums, undated columns. Other pages: b0_only.
    Reader calls (network) run in a thread pool while B0 (CPU, OCR; one at a time for memory) works through the
    pages, so a document costs ~max(B0 total, slowest reader batch), not their sum."""
    from concurrent.futures import ThreadPoolExecutor
    from tabula import config
    from tabula.hybrid import demo, merge, needs_reader, read_second, reader_name, run_b0
    doc_id = doc_id or Path(pdf_path).stem
    tables, image_only = (list(pages), []) if pages else find_pages(pdf_path)
    todo = tables + image_only
    reader = reader_name(settings)
    every = config.get("TABULA_CROSSCHECK", settings, "all") != "needed" and reader != "none"
    out = []
    with ThreadPoolExecutor(int(config.get("TABULA_READER_CONCURRENCY", settings, "4"))) as pool:  # TypeLLM allows 4 in flight
        futs = {n: pool.submit(read_second, pdf_path, n, doc_id, reader, settings) for n in todo} if every else {}
        b0s = {}
        for n in todo:
            b0s[n] = run_b0(pdf_path, n)
            if n not in futs and reader != "none" and needs_reader(b0s[n]):
                futs[n] = pool.submit(read_second, pdf_path, n, doc_id, reader, settings)
        for n in todo:
            second = futs[n].result() if n in futs else (None, {"reader": None, "skipped": "reader set to none" if reader == "none"
                                                                 else "B0 passed every check"})
            used = second[1].get("reader")
            doc = finish_page(pdf_path, n, merge(b0s[n], second[0], "b1" if used in ("vlm", None) else used), second[1])
            out.append(doc)
            if on_page:
                on_page(doc)
    return {"doc_id": doc_id, "reader": reader, "crosscheck": "all" if every else "needed", "demo": demo(settings),
            "reader_calls": len(futs), "table_pages": tables, "image_only_pages": image_only,
            "statements": stitch(out), "pages": out}


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
