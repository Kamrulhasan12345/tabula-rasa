"""Hybrid calibration: accuracy of cells by status (agreed / review / vlm_only / b0_only) over runs/hybrid.

    .venv/bin/python tools/calibration.py
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from tabula.evaluate import score

golds = {f"{g['doc_id']}_p{g['page']}": g for g in (json.loads(p.read_text()) for p in (ROOT / "gold").glob("*.json"))}
tot = collections.defaultdict(lambda: [0, 0])
pages = collections.Counter()
for pp in sorted((ROOT / "runs/hybrid").glob("*.json")):
    pred = json.loads(pp.read_text())
    gold = golds[pp.stem]
    for st, (n, ok) in score(gold, pred)["by_status"].items():
        tot[st][0] += n
        tot[st][1] += ok
    pages[pred.get("status")] += 1
n_all = sum(n for n, _ in tot.values())
print("| cell status | cells | share | accuracy |\n|---|---|---|---|")
for st, (n, ok) in sorted(tot.items(), key=lambda kv: -kv[1][0]):
    print(f"| {st} | {n} | {n / n_all:.1%} | {ok / n:.4f} |")
print("pages by status:", dict(pages))
