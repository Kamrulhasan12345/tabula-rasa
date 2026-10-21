"""Hybrid merge logic (no OCR): agreement, disagreement, tainted cells, scan fallback."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from tabula.hybrid import merge

COLS = [{"id": "c0", "period_end": "2025-03-31"}, {"id": "c1", "period_end": "2026-03-31"}]


def row(label, raw, values):
    return {"label": label, "path": ["資産の部"], "raw": raw, "values": values}


b0 = {"columns": COLS, "rows": [
    row("現金及び預金", {"c0": "1,000", "c1": "2,000"}, {"c0": 1000, "c1": 2000}),
    row("売掛金", {"c0": "300", "c1": "△40"}, {"c0": 300, "c1": None}),  # c1 tainted in B0
]}
b1 = {"columns": COLS, "rows": [
    row("現金及び預金", {}, {"c0": 1000, "c1": 2900}),  # VLM misreads c1
    row("売掛金", {}, {"c0": 300, "c1": -40}),
]}

d = merge(b0, b1)
assert d["engine"] == "b0+b1" and d["status"] == "review"
assert d["rows"][0]["cell_status"] == {"c0": "agreed", "c1": "review"}
assert d["rows"][1]["cell_status"] == {"c0": "agreed", "c1": "review"}
assert d["rows"][0]["values"]["c1"] == 2000  # B0's value is kept

ok = merge({**b0, "rows": b0["rows"][:1]}, {**b1, "rows": [row("現金及び預金", {}, {"c0": 1000, "c1": 2000})]})
assert ok["status"] == "ok"

alone = merge(b0, None)
assert alone["engine"] == "b0" and alone["rows"][0]["cell_status"]["c0"] == "b0_only"

scan = merge({"rows": [], "columns": [], "error": "no table"}, b1)
assert scan["engine"] == "b1" and scan["rows"][0]["cell_status"] == {"c0": "vlm_only", "c1": "vlm_only"}

assert merge({"rows": [], "columns": []}, None)["status"] == "failed"
print("ok")
