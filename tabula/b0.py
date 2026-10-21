"""Baseline B0: text layer + rules, no ML extractor.

Camelot (stream flavor) finds rows and columns. Every cell is then rebuilt from M1-verified
words inside the cell box, so text the page doesn't visibly show never becomes a value.
Hierarchy comes from each label's x position; row types from Japanese label conventions.
"""
import re
import unicodedata

import camelot
import pdfplumber

from tabula.normalize import normalize_label, parse_value
from tabula.router import route
from tabula.security import verify_page

CONTRA = re.compile(r"減価償却累計額|減損損失累計額|引当金$")
NET = re.compile(r"純額")


def _in(w, x1, x2, top, bottom):
    cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
    return x1 <= cx <= x2 and top <= cy <= bottom


def _underlined(page, w):
    return any(r["top"] - 3 <= w["bottom"] <= r["top"] + 1 and r["x0"] >= w["x0"] - 1 and r["x1"] <= w["x1"] + 1
               for r in page.rects + page.lines if r["bottom"] - r["top"] < 2)


def _box(ws):
    """Union of word boxes -> [x0, top, x1, bottom] in PDF points, origin top-left (pdfplumber)."""
    return [round(min(w["x0"] for w in ws), 1), round(min(w["top"] for w in ws), 1),
            round(max(w["x1"] for w in ws), 1), round(max(w["bottom"] for w in ws), 1)] if ws else None


def extract(pdf_path, page_no=1):
    page = pdfplumber.open(pdf_path).pages[page_no - 1]
    words, flags = verify_page(page)
    meta = route(page, words)
    table = camelot.read_pdf(str(pdf_path), flavor="stream", pages=str(page_no))[0]
    h = float(page.height)

    raw_rows = []
    for cells in table.cells:
        out = []
        for c in cells:
            top, bottom = h - c.y2, h - c.y1
            inside = [w for w in words if _in(w, c.x1, c.x2, top, bottom)]
            out.append({"words": [w for w in inside if w["verified"]], "tainted": any(not w["verified"] for w in inside)})
        raw_rows.append(out)

    # value columns: the ones whose verified cells parse as numbers; header rows sit above the first label row
    ncols = len(raw_rows[0])
    text = lambda cell: "".join(w["text"] for w in sorted(cell["words"], key=lambda w: w["x0"]))
    vcols = [k for k in range(1, ncols) if sum(isinstance(parse_value(text(r[k])), int) for r in raw_rows) >= 3]
    # IFRS statements have a 注記 (note number) column: small bare integers, never comma-grouped or △
    vcols = [k for k in vcols if any(re.search(r"\d{1,3}(,\d{3})+", text(r[k])) for r in raw_rows)]  # note refs like "7,27" are not
    # one column per numeric column; period from the page header when the router found it, else unknown
    periods = meta["periods"][:len(vcols)] if len(meta["periods"]) >= len(vcols) else []
    columns = [{"id": f"c{i}", "label": "", "period_type": periods[i]["period_type"] if periods else None,
                "period_end": periods[i]["period_end"] if periods else None} for i in range(len(vcols))]
    for i, k in enumerate(vcols):  # column label = first non-date header text in that column
        hdr = [text(r[k]) for r in raw_rows if text(r[k]) and not isinstance(parse_value(text(r[k])), int) and "月" not in text(r[k])]  # skip date lines
        if i < len(columns) and hdr:
            columns[i]["label"] = unicodedata.normalize("NFKC", hdr[0])

    rows = []
    for r in raw_rows:
        label_cell = r[0]
        if not label_cell["words"]:
            continue
        label, refs = normalize_label(text(label_cell))
        x0 = min(w["x0"] for w in label_cell["words"])
        raw = {columns[i]["id"]: (text(r[k]) or None) for i, k in enumerate(vcols) if i < len(columns)}
        values = {cid: (None if r[vcols[i]]["tainted"] else parse_value(v)) for i, (cid, v) in enumerate(raw.items())}
        vwords = {columns[i]["id"]: r[k]["words"] for i, k in enumerate(vcols) if i < len(columns)}
        rows.append({"label": label, "x0": x0, "raw": raw, "values": values, "footnote_refs": refs,
                     "underlined": {cid: any(_underlined(page, w) for w in ws) for cid, ws in vwords.items()},
                     "bbox": _box(label_cell["words"] + [w for ws in vwords.values() for w in ws]),
                     "cell_bbox": {cid: _box(ws) for cid, ws in vwords.items()},
                     "tainted": label_cell["tainted"] or any(r[k]["tainted"] for k in vcols)})

    # levels: cluster label x0 (within 3pt)
    xs = []
    for x in sorted(r["x0"] for r in rows):
        if not xs or x - xs[-1] > 3:
            xs.append(x)
    for r in rows:
        x0 = r.pop("x0")
        r["level"] = max(i for i, x in enumerate(xs) if x0 >= x - 3)

    # row types and paths
    stack, last_item = [], None
    for i, r in enumerate(rows, 1):
        has_values = any(v is not None for v in r["values"].values())
        if not has_values and not any(r["raw"].values()):
            r["row_type"] = "header"
        elif CONTRA.search(r["label"]):
            r["row_type"] = "contra"
        elif NET.search(r["label"]):
            r["row_type"] = "net"
        elif r["label"].endswith("合計"):
            r["row_type"] = "total" if r["level"] <= 1 else "subtotal"
        else:
            r["row_type"] = "item"
        stack = [s for s in stack if s["level"] < r["level"]]
        r["path"] = [s["label"] for s in stack]
        if r["row_type"] == "contra" and last_item and last_item["level"] < r["level"]:
            r["path"].append(last_item["label"])
        if r["row_type"] == "header":
            stack.append(r)
            r["values"] = {cid: None for cid in r["values"]}
        if r["row_type"] == "item":
            last_item = r
        r["id"] = f"r{i:02d}"

    statement = (meta["statement_types"] or [None])[0]
    doc = {
        "doc_id": str(pdf_path).rsplit("/", 1)[-1].removesuffix(".pdf"), "page": page_no,
        "statement_type": {"bs": f"{meta['scope'] or 'unknown'}_balance_sheet"}.get(statement, statement),
        "scope": meta["scope"], "accounting_standard": meta["accounting_standard"],
        "is_corrected": meta["correction_side"] is not None, "correction_side": meta["correction_side"],
        "unit": meta["unit"] and {**meta["unit"], "currency": "JPY"},
        "columns": columns, "rows": rows,
        "continues_on_next_page": meta["continues_on_next_page"],
        "page_size": [round(float(page.width), 1), h],
        "rounding": {"method": meta["rounding_note"] or "unknown", "source": "note" if meta["rounding_note"] else "none"},
        "security_flags": flags,
    }
    return doc
