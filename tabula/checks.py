import re
import unicodedata

def check_sum(total, items, method):
    n, diff = len(items), total - sum(items)
    if method == "truncate" and any(v < 0 for v in items):
        method = "truncate-mixed"  # negative items truncate upward, so the drift can go either way
    ok = {"truncate": 0 <= diff <= n - 1, "truncate-mixed": abs(diff) <= n - 1,
          "round": abs(diff) <= n / 2}.get(method, abs(diff) <= n)
    return ok, diff


def check_net(gross, contra, net):
    d = gross + contra - net
    return 0 <= d <= 1, d


def run_checks(doc):
    """-> list of (check, column, "ok"|"fail"|"skip", diff)."""
    rows = {r["id"]: r for r in doc["rows"]}
    method = doc["rounding"]["method"]
    out = []
    for c in doc["checks"]:
        for col in doc["columns"]:
            cid = col["id"]
            if c["type"] == "sum":  # optional "weights" (XBRL calc weights, e.g. -1 for 売上原価)
                w = c.get("weights") or [1] * len(c["items"])
                vals = [rows[c["total"]]["values"][cid]] + [rows[i]["values"][cid] for i in c["items"]]
                if all(isinstance(v, int) for v in vals):
                    vals = [vals[0]] + [k * v for k, v in zip(w, vals[1:])]
            else:
                vals = [rows[c[k]]["values"][cid] for k in ("gross", "contra", "net")]
            if any(v is None or v == "unparsed" for v in vals):
                out.append((c, cid, "skip", None))
                continue
            ok, d = check_sum(vals[0], vals[1:], method) if c["type"] == "sum" else check_net(*vals)
            out.append((c, cid, "ok" if ok else "fail", d))
    return out


def _key(s):
    return unicodedata.normalize("NFKC", s or "").replace(" ", "")


def infer_checks(doc):
    """Sum checks from the table's own structure, no XBRL: "X合計" = the rows directly under section X, where a
    sub-section counts as its own "Y合計" row, a 小計 replaces the rows above it, and a gross row with a
    (純額) row is left out. ponytail: 合計/小計 only; profit lines (売上総利益 = 売上高 − 売上原価) need
    per-statement sign rules, add when PL checks matter."""
    rows = doc.get("rows") or []
    by_label = {_key(r["label"]): r for r in rows}
    out = []
    for r in rows:
        lab = _key(r["label"])
        if r.get("row_type") not in ("subtotal", "total") or not lab.endswith("合計"):
            continue
        sec = lab[:-2]
        kids = []
        for c in rows:
            if c is r or not c.get("path") or _key(c["path"][-1]) not in (sec, sec + "の部"):
                continue
            if c.get("row_type") == "header":  # sub-section: its 合計 row stands in for it
                c = by_label.get(_key(c["label"]) + "合計")
                if not c:
                    continue
            elif _key(c["label"]) == "小計":  # 小計 sums what came before it in this section
                if len(kids) >= 2:
                    out.append({"type": "sum", "total": c["id"], "items": [k["id"] for k in kids], "source": "inferred"})
                kids = []
            kids.append(c)
        nets = [_key(c["label"]) for c in kids if c.get("row_type") == "net"]
        kids = [c for c in kids if not any(n.startswith(_key(c["label"])) and n != _key(c["label"]) for n in nets)]
        if len(kids) >= 2:
            out.append({"type": "sum", "total": r["id"], "items": [c["id"] for c in kids], "source": "inferred"})
    if (doc.get("accounting_standard") or "J-GAAP") == "J-GAAP":
        out += [c for c in _pl_checks(rows) if c["total"] not in {o["total"] for o in out}]
    return out


# J-GAAP P/L profit lines, values as printed (expenses positive, losses △). (total, [(item, weight, required)]).
# 当期/中間/四半期 and 利益/損失 variants are folded by the patterns. IFRS P/Ls differ per filer, so not covered.
_P = r"(当期|中間|四半期)?"
PL_RULES = [
    (r"売上総(利益|損失)", [(r"(売上高|営業収益)(合計)?", 1, True), (r"売上原価(合計)?", -1, True)]),
    (r"営業(利益|損失)", [(r"営業総(利益|損失)", 1, True), (r"販売費及び一般管理費(合計)?", -1, True)]),  # retail: + 営業収入
    (r"営業(利益|損失)", [(r"売上総(利益|損失)", 1, True), (r"販売費及び一般管理費(合計)?", -1, True)]),
    (r"経常(利益|損失)", [(r"営業(利益|損失)", 1, True), (r"営業外収益(合計)?", 1, True), (r"営業外費用(合計)?", -1, True)]),
    (r"税(金等調整前|引前)" + _P + r"純(利益|損失)", [(r"経常(利益|損失)", 1, True), (r"特別利益(合計)?", 1, False), (r"特別損失(合計)?", -1, False)]),
    (_P + r"純(利益|損失)", [(r"税(金等調整前|引前)" + _P + r"純(利益|損失)", 1, True), (r"法人税等(合計)?", -1, True)]),
]


def _pl_checks(rows):
    def find(pat):
        hits = [r for r in rows if re.fullmatch(pat + r"(\(△?(利益|損失)\))?(又は.*)?", _key(r["label"]))
                and any(isinstance(v, (int, float)) for v in r["values"].values())]
        return min(hits, key=lambda r: ("合計" not in _key(r["label"]), rows.index(r))) if hits else None
    if any(re.search(r"金融収益|金融費用|所有者に帰属|その他の収益", _key(r["label"])) for r in rows):
        return []  # IFRS P/L (router may say J-GAAP: the keywords it looks for sit on other pages)
    out = []
    for total, parts in PL_RULES:
        t = find(total)
        if t and any(o["total"] == t["id"] for o in out):
            continue
        found = [(find(p), w) for p, w, req in parts]
        if not t or any(r is None and req for (r, _), (_, _, req) in zip(found, parts)):
            continue
        # weight -1 = "subtract this expense"; a filer that prints the expense with △ has already subtracted it
        nums = lambda r: [v for v in r["values"].values() if isinstance(v, (int, float)) and v]
        sign = lambda r: -1 if nums(r) and all(v < 0 for v in nums(r)) else 1
        items = [(r, w if w > 0 else -sign(r)) for r, w in found if r is not None]
        out.append({"type": "sum", "total": t["id"], "items": [r["id"] for r, _ in items],
                    "weights": [w for _, w in items], "source": "inferred_pl"})
    return out
