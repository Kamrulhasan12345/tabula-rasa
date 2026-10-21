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
