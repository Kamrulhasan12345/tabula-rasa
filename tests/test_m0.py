import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import pdfplumber

from tabula.checks import run_checks
from tabula.normalize import infer_rounding, normalize_label, parse_jp_date, parse_value

GOLD = json.loads((ROOT / "gold/p001.json").read_text())
ROWS = {r["id"]: r for r in GOLD["rows"]}


def test_negatives():
    assert parse_value("△1,234") == -1234
    assert parse_value("▲1,234") == -1234
    assert parse_value("(1,234)") == -1234
    assert parse_value("（1,234）") == -1234
    assert parse_value("△0") == 0


def test_nil_blank_unparsed():
    for d in "－―—-ー−":
        assert parse_value(d) == 0, d
    assert parse_value("－") is not None and parse_value("－") != "unparsed"
    assert parse_value("  ") is None and parse_value("") is None
    assert parse_value("２，００２，４２９") == 2002429
    assert parse_value("12,34") == "unparsed"
    assert parse_value("abc") == "unparsed"


def test_labels():
    assert normalize_label("⽇") == ("日", [])
    assert normalize_label("⻑") == ("長", [])
    assert normalize_label("※1 建物") == ("建物", ["1"])
    assert normalize_label("建物　＊２　その他") == ("建物 その他", ["2"])


def test_dates():
    assert parse_jp_date("2025 年３月 31 日") == "2025-03-31"
    assert parse_jp_date("令和７年３月31日") == "2025-03-31"
    assert parse_jp_date("令和元年5月1日") == "2019-05-01"
    assert parse_jp_date("平成30年12月31日") == "2018-12-31"


def test_rounding():
    assert infer_rounding([3, 2, 1, 2, 0]) == "truncate"
    assert infer_rounding([1, -1]) == "round"
    assert infer_rounding([0, 0]) == "unknown"


def test_gold_values():
    for r in GOLD["rows"]:
        if r["row_type"] != "header":
            assert all(isinstance(r["values"][c], int) for c in ("c0", "c1")), r["id"]
    assert GOLD["columns"][0]["period_end"] == "2025-03-31" and GOLD["columns"][1]["period_end"] == "2026-03-31"


def by(label, n=0):
    return [r for r in GOLD["rows"] if r["label"] == label][n]["id"]


def test_gold_checks():
    res = run_checks(GOLD)
    assert len(res) == 2 * len(GOLD["checks"])
    assert all(s == "ok" for _, _, s, _ in res), [x for x in res if x[2] != "ok"]
    d = {(c["total"], col): diff for c, col, _, diff in res if c["type"] == "sum"}
    for label, col, want in [("流動資産合計", "c0", 3), ("流動資産合計", "c1", 2), ("有形固定資産合計", "c0", 1),
                             ("固定資産合計", "c0", 2), ("投資その他の資産合計", "c1", 1), ("資産合計", "c0", 0)]:
        assert d[(by(label), col)] == want, (label, col)
    nets = {(c["net"], col): diff for c, col, _, diff in res if c["type"] == "net"}
    ones = {(by("リース資産(純額)"), "c0"), (by("建物及び構築物(純額)"), "c1"), (by("リース資産(純額)"), "c1")}
    assert len(nets) == 8
    for k, v in nets.items():
        assert v == (1 if k in ones else 0), k
    assert infer_rounding([x for x in d.values()]) == GOLD["rounding"]["method"]


def test_gold_flags():
    assert [(r["label"], c) for r in GOLD["rows"] for c, u in r["underlined"].items() if u] == [
        ("投資有価証券", "c1"), ("関係会社株式", "c1")]
    assert ROWS[by("建設仮勘定")]["raw"]["c0"] == "－" and ROWS[by("建設仮勘定")]["values"]["c0"] == 0


def test_gold_vs_pdf():
    page = pdfplumber.open(ROOT / "docs/balance_sheet_after_correction.pdf").pages[0]
    texts = {w["text"] for w in page.extract_words()}
    labels = {normalize_label(t)[0] for t in texts}
    for r in GOLD["rows"]:
        assert r["label"] in labels, r["label"]
        for raw in r["raw"].values():
            assert raw is None or raw in texts, raw
        assert unicodedata.normalize("NFKC", r["label"]) == r["label"]


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")
