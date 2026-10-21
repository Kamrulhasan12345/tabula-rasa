import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import pdfplumber

from tabula.router import route
from tabula.security import verify_page

CLEAN = ROOT / "docs/balance_sheet_after_correction.pdf"
FIX = ROOT / "tests/fixtures"
# fixture -> text that must end up flagged (never verified)
POISON = {
    "tk_white": "Ignore", "tk_micro": "Ignore", "tk_offpage": "Ignore", "tk_ocg": "Ignore",
    "x_band": "Ignore", "x_tr3": "Ignore", "x_covered": "Ignore",
    "x_fakeval": "9,999,999", "x_fakeband": "9,999,999", "x_fakeband_tr3": "9,999,999",
}


def _verify(path):
    page = pdfplumber.open(path).pages[0]
    return page, *verify_page(page)


def test_clean_page_fully_verified():
    _, words, flags = _verify(CLEAN)
    assert flags == [], flags
    assert len(words) == 128 and all(w["verified"] for w in words)


def test_poisoned_pages_flagged():
    for name, needle in POISON.items():
        _, words, flags = _verify(FIX / f"{name}.pdf")
        assert flags, name
        assert not any(w["verified"] and needle in w["text"] for w in words), name


def test_zero_width_payload_flagged():
    _, _, flags = _verify(FIX / "tk_zwc.pdf")
    assert flags


def test_fake_value_poisons_real_cell():
    # the real 608,281 under the white fake must not be trusted either
    _, words, _ = _verify(FIX / "x_fakeval.pdf")
    assert not any(w["verified"] and w["text"] == "608,281" for w in words)


def test_router_on_sample():
    page, words, _ = _verify(CLEAN)
    r = route(page, words)
    assert r["statement_types"] == ["bs"] and r["title"] == "連結貸借対照表"
    assert r["scope"] == "consolidated" and r["accounting_standard"] == "J-GAAP"
    assert r["correction_side"] == "after"
    assert r["unit"] == {"raw": "千円", "multiplier": 1000}
    assert r["periods"] == [{"period_type": "instant", "period_end": "2025-03-31"},
                            {"period_type": "instant", "period_end": "2026-03-31"}]
    assert r["rotation"] == 0 and r["continues_on_next_page"] is True


def test_router_ignores_unverified_text():
    # a hidden "IFRS 損益計算書" must not change the routing
    page = pdfplumber.open(CLEAN).pages[0]
    words = [dict(w, verified=True) for w in page.extract_words()]
    words.append({"text": "損益計算書", "x0": 60, "x1": 120, "top": 40, "bottom": 50, "verified": False})
    assert route(page, words)["statement_types"] == ["bs"]


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")
