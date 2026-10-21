"""Cell accuracy / page exact per eval-set group for saved predictions in runs/<approach>/.

    .venv/bin/python tools/by_group.py b0 b1 hybrid
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from tabula.evaluate import score


def group(g):
    s = (g.get("source") or {}).get("stratum") or "sample"
    if g["doc_id"].startswith("S100"):
        return "EDINET 有報"
    if "scan" in s:
        return "TDnet scan (synthetic)"
    if "REIT" in s.upper():
        return "TDnet REIT"
    if "IFRS" in s:
        return "TDnet IFRS"
    return "sample" if s == "sample" else "TDnet J-GAAP"


names = sys.argv[1:] or ["b0", "b1", "hybrid"]
res = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0]))  # cells, ok, pages, exact
for gp in sorted((ROOT / "gold").glob("*.json")):
    g = json.loads(gp.read_text())
    if g.get("gold_status") in ("review", "excluded"):
        continue
    for n in names:
        pp = ROOT / "runs" / n / f"{g['doc_id']}_p{g['page']}.json"
        if not pp.exists():
            continue
        s = score(g, json.loads(pp.read_text()))
        cells = sum(isinstance(v, int) for r in g["rows"] if r["row_type"] != "header" for v in r["values"].values())
        for k in (group(g), "all"):
            b = res[k][n]
            b[0] += cells
            b[1] += round(s["cell_acc"] * cells)
            b[2] += 1
            b[3] += s["page_exact"]
print("| group | " + " | ".join(f"{n} cells | {n} page exact" for n in names) + " |")
print("|---|" + "---|---|" * len(names))
for k in sorted(res, key=lambda k: (k == "all", k)):
    print(f"| {k} | " + " | ".join(
        (f"{b[1] / b[0]:.3f} ({b[0]}) | {b[3]}/{b[2]}" if (b := res[k].get(n)) else "– | –") for n in names) + " |")
