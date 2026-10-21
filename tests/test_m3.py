import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from tabula.b0 import extract
from tabula.b1 import _json, convert
from tabula.evaluate import score

GOLD = json.loads((ROOT / "gold/p001.json").read_text())


def test_b0_matches_gold():
    s = score(GOLD, extract(ROOT / "docs/balance_sheet_after_correction.pdf"))
    assert s["page_exact"] and s["hierarchy_acc"] == 1 and s["teds"] == 1


def test_b0_never_returns_hidden_values():
    d = extract(ROOT / "tests/fixtures/x_fakeval.pdf")
    cash = next(r for r in d["rows"] if r["label"] == "現金及び預金")
    assert cash["values"]["c1"] is None  # tampered cell -> no value, goes to review
    assert all("9,999,999" not in str(r["raw"]) for r in d["rows"])
    d = extract(ROOT / "tests/fixtures/x_fakeband.pdf")
    cash = next(r for r in d["rows"] if r["label"] == "現金及び預金")
    assert cash["values"]["c1"] == 608281


def test_b1_convert_model_reply():
    reply = """```json
{"title": "連結貸借対照表", "unit": "（単位：千円）",
 "columns": [{"label": "前連結会計年度", "date": "(2025 年３月 31 日)"}, {"label": "当連結会計年度", "date": "(2026年3月31日)"}],
 "rows": [{"label": "資産の部", "path": [], "values": ["", ""]},
          {"label": "建設仮勘定", "path": ["資産の部", "固定資産", "有形固定資産"], "values": ["－", "4,553"]},
          {"label": "減価償却累計額", "path": ["資産の部", "固定資産", "有形固定資産", "建物及び構築物"], "values": ["△1,923,585", "△1,640,253"]}]}
```"""
    d = convert(_json(reply), "x", 1)
    assert d["unit"]["multiplier"] == 1000
    assert [c["period_end"] for c in d["columns"]] == ["2025-03-31", "2026-03-31"]
    assert [r["row_type"] for r in d["rows"]] == ["header", "item", "contra"]
    assert d["rows"][1]["values"] == {"c0": 0, "c1": 4553}
    assert d["rows"][2]["values"] == {"c0": -1923585, "c1": -1640253}


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")
