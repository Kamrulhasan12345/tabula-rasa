"""M4: build "silver" gold pages from TDnet filings.

    .venv/bin/python -I tools/build_silver.py            # all selected pages
Structure (rows, labels, hierarchy) is drafted by B0. Every numeric cell must match an XBRL fact
(value_in_unit x multiplier == fact value, same period); that is the independent check.
Sum checks come from the filer's own XBRL calculation linkbase.
Output: gold/<doc>_p<page>.json (status "confirmed" or "review") and gold/REVIEW.md.
Caveat: hierarchy/labels are B0's, so B0's structure scores on these pages are optimistic.
"""
import collections
from datetime import date, timedelta
import re
import unicodedata
from decimal import Decimal
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import csv
import io
import subprocess
import zipfile
import pdfplumber
import tdnet
from xbrl_core.linkbase.calculation import parse_calculation_linkbase

from tabula.b0 import extract
from tabula.checks import run_checks
from tabula.normalize import infer_rounding

PER_DOC = 3
PRIORITY = ["bs", "pl", "pl_ci", "cf", "ss", "ci", "segment"]


def select(pages):
    by_doc = collections.defaultdict(list)
    for p in pages:
        if p["unit"]:  # statement pages print 単位; tables of contents don't
            by_doc[p["doc"]].append(p)
    out = []
    for ps in by_doc.values():
        ps.sort(key=lambda p: (min(PRIORITY.index(k) for k in p["kinds"]) if p["kinds"] else 99, p["page"]))
        out += ps[:PER_DOC]
    return out


SCOPE_AXIS = "ConsolidatedOrNonConsolidatedAxis"


def plain(f):
    """Facts without dimensions, or whose only dimension is consolidated/non-consolidated scope."""
    return all(d.axis.endswith(SCOPE_AXIS) for d in f.dimensions)


def period_start(item):
    return str(getattr(item.period, "start_date", None) or "")


def period_end(item):
    p = item.period
    return str(getattr(p, "instant", None) or getattr(p, "end_date", None) or "")


def load_source(page):
    """-> (pdf path, meta, numeric facts, calculation linkbase, pdf pages to scan for the rounding note)."""
    if page["doc"].startswith("S100"):  # EDINET 有価証券報告書
        z = zipfile.ZipFile(ROOT / "data/edinet" / page["doc"] / "xbrl.zip")
        pub = [n for n in z.namelist() if "/PublicDoc/" in n]
        st = tdnet.parse_ixbrl_files({Path(n).name: z.read(n) for n in pub if n.endswith(".htm")})
        cal = next(n for n in pub if n.endswith("_cal.xml"))
        row = list(csv.reader(io.StringIO(z.read(next(n for n in z.namelist() if n.endswith("XbrlSearchDlInfo.csv"))).decode("cp932"))))[2]
        meta = {"name": row[3], "title": row[1], "stratum": page["stratum"]}
        return (ROOT / f"data/edinet/{page['doc']}.pdf", meta, st, parse_calculation_linkbase(z.read(cal)),
                slice(max(page["page"] - 3, 0), page["page"]))  # the note sits in the statements' notes, near the page
    d = ROOT / "data/tdnet" / page["doc"]
    st = tdnet.parse_zip((d / "xbrl.zip").read_bytes())
    return d / "doc.pdf", json.loads((d / "meta.json").read_text()), st, st._calculation_linkbase, slice(0, 3)


def build(page):
    pdf_path, meta, st, calc, note_pages = load_source(page)
    facts = [f for f in st._items if isinstance(f.value, Decimal) and not f.is_nil]  # ponytail: private attr, tdnet 0.3.2 has no public "all facts"
    by_val = collections.defaultdict(list)
    for f in facts:
        by_val[abs(int(f.value))].append(f)  # abs: IFRS 売上原価 is positive in XBRL (weight -1), △ in the PDF

    src = pdf_path
    if page["doc"].startswith("S100"):  # camelot/playa refuses PDFs whose permission flags forbid text extraction; strip them
        src = ROOT / "data/edinet/_dec" / pdf_path.name
        src.parent.mkdir(exist_ok=True)
        if not src.exists():
            subprocess.run(["qpdf", "--decrypt", str(pdf_path), str(src)], check=True)
    doc = extract(src, page["page"])
    mult = (doc.get("unit") or {}).get("multiplier")
    review = []
    if not mult:
        review.append("no unit found")
    # cells -> facts; column periods are inferred from the matched facts when the router found none
    col_periods = collections.defaultdict(collections.Counter)
    matches = {}
    for r in doc["rows"]:
        for cid, v in r["values"].items():
            if isinstance(v, int) and v != 0 and mult:
                cands = [f for f in by_val.get(abs(v) * mult, []) if plain(f)]
                if cands:
                    matches[(r["id"], cid)] = cands
                    for f in cands:
                        col_periods[cid][period_end(f)] += 1
    starts = collections.defaultdict(collections.Counter)
    for (rid, cid), fs in matches.items():
        for f in fs:
            if period_start(f):
                starts[cid][period_start(f)] += 1
    for c in doc["columns"]:
        if starts[c["id"]]:
            c["period_start"] = starts[c["id"]].most_common(1)[0][0]
        if col_periods[c["id"]]:
            best = col_periods[c["id"]].most_common(1)[0][0]
            if c.get("period_end") not in (None, best):
                review.append(f"column {c['id']} period {c['period_end']} != XBRL {best}")
            c["period_end"] = best
    confirmed = unconfirmed = 0
    for r in doc["rows"]:
        concepts = collections.Counter()
        r["xbrl"] = {}
        for cid, v in r["values"].items():
            if not isinstance(v, int) or v == 0:
                continue
            col = next(c for c in doc["columns"] if c["id"] == cid)
            # instants may sit at the period end, or the day before it starts (CF 期首残高)
            ok_dates = {col["period_end"]}
            if col.get("period_start"):
                ok_dates.add(str(date.fromisoformat(col["period_start"]) - timedelta(days=1)))
            fs = [f for f in matches.get((r["id"], cid), []) if period_end(f) in ok_dates]
            if fs:
                confirmed += 1
                concepts[fs[0].local_name] += 1
                r["xbrl"][cid] = fs[0].local_name
                r["xbrl_sign"] = 1 if (fs[0].value < 0) == (v < 0) else -1  # -1: PDF shows △ where XBRL is positive
            else:
                unconfirmed += 1
                review.append(f"{r['id']} {r['label']} {cid}={r['raw'].get(cid)} not in XBRL")
        r["concept"] = concepts.most_common(1)[0][0] if concepts else None
    # sum checks from the calculation linkbase, only where the parent and every child with a value are rows here
    row_of = {r["concept"]: r["id"] for r in doc["rows"] if r["concept"]}
    checks = []
    consolidated = doc.get("scope") != "non_consolidated" and "個別" not in (doc.get("statement_type") or "")
    for role in calc.role_uris:
        if ("Consolidated" in role) != consolidated:  # a 個別 page must not borrow the 連結 calc tree
            continue
        for parent, rid in row_of.items():
            arcs = calc.children_of(parent, role_uri=role)
            if len(arcs) < 2 or any(a.child not in row_of for a in arcs):
                continue
            sign = {r["id"]: r.get("xbrl_sign", 1) for r in doc["rows"]}
            # XBRL weights apply to XBRL signs; convert to the PDF's displayed signs
            chk = {"type": "sum", "total": rid, "items": [row_of[a.child] for a in arcs],
                   "weights": [int(a.weight) * sign[row_of[a.child]] * sign[rid] for a in arcs]}
            if chk not in checks:
                checks.append(chk)
    doc["checks"] = checks
    # rounding: the filing's own note (summary page: 百万円未満切捨て) wins; else infer from all-positive sums only
    with pdfplumber.open(pdf_path) as pdf:
        note = re.search(r"未満(切り?捨て?|切捨|四捨五入)", unicodedata.normalize("NFKC", "".join((pg.extract_text() or "") for pg in pdf.pages[note_pages])))
    diffs = []
    for c in checks:
        for col in doc["columns"]:
            vals = [doc["rows"][int(i[1:]) - 1]["values"].get(col["id"]) for i in [c["total"]] + c["items"]]
            items = [w * v for w, v in zip(c["weights"], vals[1:])] if all(isinstance(v, int) for v in vals) else None
            if items and all(v >= 0 for v in items):
                diffs.append(vals[0] - sum(items))
    if note:
        doc["rounding"] = {"method": "round" if "四捨五入" in note.group(1) else "truncate", "source": "note"}
    else:
        doc["rounding"] = {"method": infer_rounding(diffs) if diffs else "unknown", "source": "inferred_from_sums"}
    fails = [x for x in run_checks(doc) if x[2] == "fail"]
    review += [f"check fails: total {c['total']} {col} diff {d}" for c, col, _, d in fails]
    doc.update({"doc_id": page["doc"], "page": page["page"], "source": {"company": meta["name"], "title": meta["title"],
                "stratum": meta["stratum"], "pdf": str(pdf_path.relative_to(ROOT))},
                "gold_status": "confirmed" if not review and confirmed else "review", "review": review,
                "xbrl_confirmed_cells": confirmed, "xbrl_unconfirmed_cells": unconfirmed})
    doc.pop("security_flags", None)
    return doc


def select_edinet(pages):
    """Per doc: first consolidated BS, first consolidated PL, first 個別 BS."""
    out = []
    for doc in dict.fromkeys(p["doc"] for p in pages):
        ps = [p for p in pages if p["doc"] == doc]
        for scope, kind in [("consolidated", "bs"), ("consolidated", "pl"), ("non_consolidated", "bs")]:
            out += next(([p] for p in ps if p["scope"] == scope and p["kinds"] == [kind]), [])
    return out


def main():
    edinet = "--edinet" in sys.argv
    pages = (select_edinet if edinet else select)(json.loads((ROOT / f"data/{'edinet' if edinet else 'tdnet'}/pages.json").read_text()))
    lines = ["# Silver gold review queue", "", "| page | stratum | kinds | rows | xbrl ✓ | ✗ | checks | status | first issue |", "|---|---|---|---|---|---|---|---|---|"]
    redo_all = "--all" in sys.argv
    only = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--only=")), None)  # doc_pN,... to rebuild
    for p in pages:
        if only and f"{p['doc']}_p{p['page']}" not in only:
            continue
        out = ROOT / "gold" / f"{p['doc']}_p{p['page']}.json"
        if out.exists() and not redo_all and not only and json.loads(out.read_text()).get("gold_status") in ("confirmed", "excluded"):
            doc = json.loads(out.read_text())  # keep confirmed pages; rebuild only review/missing ones
            lines.append(f"| {p['doc']} p{p['page']} | {p['stratum']} | {','.join(p['kinds'])} | {len(doc['rows'])} | {doc['xbrl_confirmed_cells']} | "
                         f"{doc['xbrl_unconfirmed_cells']} | {len(doc['checks'])} | {doc['gold_status']} | |")
            continue
        try:
            doc = build(p)
        except Exception as e:  # keep going; a crash is itself a finding about B0
            lines.append(f"| {p['doc']} p{p['page']} | {p['stratum']} | {','.join(p['kinds'])} | – | – | – | – | ERROR | {type(e).__name__}: {e} |")
            print("ERROR", p["doc"], p["page"], e, flush=True)
            continue
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        lines.append(f"| {p['doc']} p{p['page']} | {p['stratum']} | {','.join(p['kinds'])} | {len(doc['rows'])} | {doc['xbrl_confirmed_cells']} | "
                     f"{doc['xbrl_unconfirmed_cells']} | {len(doc['checks'])} | {doc['gold_status']} | {(doc['review'] or [''])[0][:80]} |")
        print(p["doc"], p["page"], doc["gold_status"], doc["xbrl_confirmed_cells"], doc["xbrl_unconfirmed_cells"], flush=True)
    if not only:  # a partial rebuild doesn't rewrite the whole queue
        (ROOT / f"gold/REVIEW{'_edinet' if edinet else ''}.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
