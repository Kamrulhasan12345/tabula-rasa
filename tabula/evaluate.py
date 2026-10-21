"""PRD §5.1 metrics: score one predicted page (§4 schema) against its gold page.

Rows are matched by label + path + position, never by value, so a wrong value can't
"find" a better-matching row. Columns are matched by period_end, falling back to order.
"""
import unicodedata

import numpy as np
from rapidfuzz.distance import Levenshtein
from scipy.optimize import linear_sum_assignment
from table_recognition_metric import TEDS

from tabula.checks import run_checks

MAX_COST = 0.5  # pairs costlier than this stay unmatched
_teds, _teds_s = TEDS(), TEDS(structure_only=True)


def _n(s):
    return "".join(unicodedata.normalize("NFKC", s or "").split())


def _dist(a, b):
    return Levenshtein.normalized_distance(_n(a), _n(b))


def align(gold_rows, pred_rows):
    """-> {gold_index: pred_index} via min-cost assignment."""
    if not gold_rows or not pred_rows:
        return {}
    cost = np.empty((len(gold_rows), len(pred_rows)))
    for i, g in enumerate(gold_rows):
        for j, p in enumerate(pred_rows):
            cost[i, j] = (0.6 * _dist(g["label"], p.get("label"))
                          + 0.3 * _dist("/".join(g["path"]), "/".join(p.get("path") or []))
                          + 0.1 * abs(i / len(gold_rows) - j / len(pred_rows)))
    gi, pj = linear_sum_assignment(cost)
    return {i: j for i, j in zip(gi, pj) if cost[i, j] <= MAX_COST}


def _col_map(gold, pred):
    """gold column id -> pred column id."""
    pcols = pred.get("columns") or []
    by_end = {c.get("period_end"): c["id"] for c in pcols}
    return {g["id"]: by_end.get(g.get("period_end")) or (pcols[k]["id"] if k < len(pcols) else None)
            for k, g in enumerate(gold["columns"])}


def to_html(doc):
    cols = doc.get("columns") or []
    head = "<tr><td></td>" + "".join(f"<td>{c.get('label', '')}</td>" for c in cols) + "</tr>"
    body = "".join("<tr><td>" + r.get("label", "") + "</td>"
                   + "".join(f"<td>{(r.get('raw') or {}).get(c['id']) or ''}</td>" for c in cols) + "</tr>"
                   for r in doc.get("rows") or [])
    return f"<html><body><table>{head}{body}</table></body></html>"


def score(gold, pred):
    grows, prows = gold["rows"], pred.get("rows") or []
    m, cm = align(grows, prows), _col_map(gold, pred)
    cells = ok = sign_ok = sign_n = 0
    by_status = {}  # hybrid: cell_status -> [cells, correct]; shows whether "agreed" can skip human review
    hier = cer = 0.0
    for i, g in enumerate(grows):
        p = prows[m[i]] if i in m else None
        cer += _dist(g["label"], p["label"]) if p else 1.0
        hier += bool(p) and [_n(x) for x in p.get("path") or []] == [_n(x) for x in g["path"]]
        if g["row_type"] == "header":
            continue
        for cid, gv in g["values"].items():
            if not isinstance(gv, int):  # gold cell without a number (blank / unparsed) is not scored
                continue
            pv = (p.get("values") or {}).get(cm.get(cid)) if p else None
            cells += 1
            ok += pv == gv
            st = (p.get("cell_status") or {}).get(cm.get(cid)) if p else None
            if st or (p and "cell_status" in p):
                b = by_status.setdefault(st or "missing", [0, 0])
                b[0] += 1
                b[1] += pv == gv
            if isinstance(pv, int) and gv:
                sign_n += 1
                sign_ok += (pv < 0) == (gv < 0)
    extra = [j for j in range(len(prows)) if j not in m.values() and any(v is not None for v in (prows[j].get("values") or {}).values())]
    # M6 on the prediction, using gold's check structure mapped onto matched rows
    mapped = {"rounding": gold["rounding"], "columns": gold["columns"], "checks": gold["checks"],
              "rows": [{"id": g["id"], "values": {cid: (prows[m[i]].get("values") or {}).get(cm.get(cid)) if i in m else None
                                                   for cid in g["values"]}} for i, g in enumerate(grows)]}
    checks = run_checks(mapped)
    unit = (pred.get("unit") or {}).get("multiplier") == gold["unit"]["multiplier"]
    periods = [c.get("period_end") for c in pred.get("columns") or []] == [c["period_end"] for c in gold["columns"]]
    return {
        "cell_acc": ok / cells,                                  # M1
        "sign_acc": sign_ok / sign_n if sign_n else 1.0,          # M1s
        "unit_ok": unit,
        "periods_ok": periods,
        "page_exact": ok == cells and not extra and unit and periods,  # M2
        "label_cer": cer / len(grows),                            # M3
        "hierarchy_acc": hier / len(grows),                       # M4
        "teds": _teds(to_html(pred), to_html(gold)),              # M5
        "teds_s": _teds_s(to_html(pred), to_html(gold)),
        "m6_pass": sum(c[2] == "ok" for c in checks) / len(checks) if checks else None,  # M6; None = page has no checks
        "missing_rows": len(grows) - len(m),
        "extra_rows": len(extra),
        "by_status": by_status,
    }
