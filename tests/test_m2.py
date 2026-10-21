import copy
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from tabula.evaluate import score

GOLD = json.loads((ROOT / "gold/p001.json").read_text())
CELLS = sum(len(r["values"]) for r in GOLD["rows"] if r["row_type"] != "header")  # 74


def pred(fn=None):
    p = copy.deepcopy(GOLD)
    if fn:
        fn(p)
    return p


def row(p, label, n=0):
    return [r for r in p["rows"] if r["label"] == label][n]


def test_oracle_perfect():
    s = score(GOLD, pred())
    assert CELLS == 74
    assert s["cell_acc"] == 1 and s["page_exact"] and s["m6_pass"] == 1 and s["teds"] == 1
    assert s["label_cer"] == 0 and s["hierarchy_acc"] == 1 and s["missing_rows"] == s["extra_rows"] == 0


def test_dropped_triangle():
    s = score(GOLD, pred(lambda p: row(p, "減価償却累計額").update(values={"c0": 1923585, "c1": -1640253})))
    assert s["cell_acc"] == 73 / 74 and s["sign_acc"] < 1 and not s["page_exact"]
    assert s["m6_pass"] < 1  # net check catches it


def test_one_digit_wrong_teds_barely_moves():
    s = score(GOLD, pred(lambda p: row(p, "現金及び預金")["values"].update(c1=608231)))
    assert s["cell_acc"] == 73 / 74 and not s["page_exact"] and s["m6_pass"] < 1
    assert s["teds"] == 1  # raw strings unchanged here; TEDS only sees text, the point of M1


def test_missing_and_extra_rows():
    def f(p):
        p["rows"] = [r for r in p["rows"] if r["label"] != "受取手形"]
        p["rows"].append({"label": "Ignore previous instructions", "path": [], "values": {"c0": 0, "c1": 0}, "raw": {}})
    s = score(GOLD, pred(f))
    assert s["missing_rows"] == 1 and s["extra_rows"] == 1 and s["cell_acc"] == 72 / 74 and not s["page_exact"]


def test_shuffled_rows_still_align():
    s = score(GOLD, pred(lambda p: random.Random(0).shuffle(p["rows"])))
    assert s["cell_acc"] == 1 and s["hierarchy_acc"] == 1


def test_wrong_parent_only_hits_hierarchy():
    s = score(GOLD, pred(lambda p: row(p, "土地").update(path=["資産の部", "流動資産"])))
    assert s["cell_acc"] == 1 and s["hierarchy_acc"] == 42 / 43


def test_unit_and_period_errors():
    s = score(GOLD, pred(lambda p: p["unit"].update(multiplier=1_000_000)))
    assert not s["unit_ok"] and not s["page_exact"] and s["cell_acc"] == 1
    s = score(GOLD, pred(lambda p: p["columns"].reverse()))  # columns swapped but matched by period_end
    assert s["cell_acc"] == 1 and not s["periods_ok"]


def test_empty_prediction():
    s = score(GOLD, {"rows": [], "columns": []})
    assert s["cell_acc"] == 0 and s["missing_rows"] == 43 and s["m6_pass"] == 0


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")
