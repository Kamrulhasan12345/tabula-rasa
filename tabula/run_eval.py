"""PRD F10: run approaches over every gold page and print the §6.1 comparison table.

    .venv/bin/python -m tabula.run_eval oracle [more approaches...]
Predictions -> runs/<approach>/<doc_id>_p<page>.json; one summary row per approach -> runs/results.csv.
"""
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tabula.approaches import APPROACHES
from tabula.evaluate import score

ROOT = Path(__file__).parent.parent
COLS = ["cell_acc", "sign_acc", "page_exact", "label_cer", "hierarchy_acc", "teds", "m6_pass", "m6_first_pass", "missing_rows", "extra_rows"]


def run(name):
    out = ROOT / "runs" / name
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for gp in sorted((ROOT / "gold").glob("*.json")):
        gold = json.loads(gp.read_text())
        if gold.get("gold_status") in ("review", "excluded"):  # silver pages wait for review before they count
            continue
        pdf = ROOT / (gold.get("source", {}).get("pdf") or f"docs/{gold['doc_id']}.pdf")
        t0 = time.perf_counter()
        pred, meta = APPROACHES[name](pdf, gold["page"], gold)
        secs = time.perf_counter() - t0
        if pred is None:  # approach has no output for this page
            continue
        (out / f"{gold['doc_id']}_p{gold['page']}.json").write_text(json.dumps(pred, ensure_ascii=False, indent=1))
        s = score(gold, pred)
        s["m6_first_pass"] = score(gold, meta["first_pass"])["m6_pass"] if meta.get("first_pass") else s["m6_pass"]
        rows.append(s | {"secs": meta.get("gpu_secs", secs), "cost_usd": meta.get("cost_usd")})  # remote runs report their own time
    mean = {}
    for k in COLS + ["secs"]:
        vals = [float(r[k]) for r in rows if r[k] is not None]  # pages without sum checks have no M6
        mean[k] = sum(vals) / len(vals) if vals else None
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    mean["usd_per_page"] = sum(costs) / len(costs) if costs else None
    return mean, len(rows)


def main(names):
    results = ROOT / "runs/results.csv"
    results.parent.mkdir(exist_ok=True)
    new = not results.exists()
    with results.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["when", "approach", "pages"] + COLS + ["secs", "usd_per_page"])
        print("| approach | pages | " + " | ".join(COLS + ["s/page", "$/page"]) + " |")
        print("|" + "---|" * (len(COLS) + 4))
        for n in names:
            mean, pages = run(n)
            vals = [mean[k] for k in COLS + ["secs", "usd_per_page"]]
            w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), n, pages] + vals)
            print(f"| {n} | {pages} | " + " | ".join("–" if v is None else f"{v:.4g}" for v in vals) + " |")


if __name__ == "__main__":
    main(sys.argv[1:] or ["oracle"])
