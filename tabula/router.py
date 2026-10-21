"""PRD §6.0 P2–P5: what is on this page, read only from verified (visible) words.

No library classifies Japanese statement pages, so this is keyword rules over the page title area.
ponytail: born-digital only; scanned-page rotation (PP-LCNet doc_ori) and table cropping
(PP-DocLayout) come with tier T4, when we have scans to test on.
"""
import re
import unicodedata

from tabula.normalize import parse_jp_date

# order matters: longer / more specific names first
STATEMENTS = [
    ("pl_ci", r"損益及び包括利益計算書"),
    ("ci", r"包括利益計算書"),
    ("pl", r"損益計算書"),
    ("bs", r"貸借対照表|財政状態計算書"),
    ("ss", r"株主資本等変動計算書|持分変動計算書"),
    ("cf", r"キャッシュ・?フロー計算書"),
    ("segment", r"セグメント情報"),
    ("summary", r"決算短信"),
]
DATE = r"((?:令和|平成|昭和)?(?:元|\d+)年\d+月\d+日)"


def route(page, words):
    """page: pdfplumber Page; words: verify_page output. Uses verified words only."""
    lines = {}
    for w in words:
        if w.get("verified"):
            lines.setdefault(round(w["top"] / 3), []).append(w)
    text = [unicodedata.normalize("NFKC", "".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"]))) for _, ws in sorted(lines.items())]
    head = "\n".join(text[:8])  # titles sit in the first lines
    full = "\n".join(text)
    flat = re.sub(r"\s", "", full)

    kinds, title = [], ""
    for kind, pat in STATEMENTS:
        m = re.search(r"(連結|個別|単体)?(" + pat + ")", head)
        if m and not any(m.group(2) in t for t in title.split("|")):
            kinds.append(kind)
            title = f"{title}|{m.group(0)}" if title else m.group(0)
    if len(kinds) > 1 and "summary" in kinds:  # every page header says 決算短信
        kinds.remove("summary")
    scope = "consolidated" if "連結" in title else ("non_consolidated" if title else None)
    standard = ("IFRS" if re.search(r"財政状態計算書|持分変動計算書|IFRS|国際会計基準", full)
                else "US-GAAP" if re.search(r"米国基準|US ?GAAP", full) else "J-GAAP")
    unit = re.search(r"単位[::]?\s*(千円|百万円|円)", full)
    rounding = re.search(r"(切り?捨て|切捨|四捨五入)", full)
    instants = [parse_jp_date(d) for d in re.findall(r"\(" + DATE + r"\)", flat)]
    durations = [(parse_jp_date(a), parse_jp_date(b)) for a, b in re.findall(r"自" + DATE + r"至" + DATE, flat)]
    upright = [c["upright"] for c in page.chars]
    return {
        "statement_types": kinds,
        "title": title or None,
        "scope": scope,
        "accounting_standard": standard,
        "standard_source": "keyword" if standard != "J-GAAP" else "default",
        "correction_side": "after" if "(訂正後)" in flat else "before" if "(訂正前)" in flat else None,
        "unit": {"raw": unit.group(1), "multiplier": {"千円": 1000, "百万円": 1_000_000, "円": 1}[unit.group(1)]} if unit else None,
        "rounding_note": {"切り捨て": "truncate", "切捨て": "truncate", "切捨": "truncate", "四捨五入": "round"}.get(rounding.group(1)) if rounding else None,
        "periods": [{"period_type": "instant", "period_end": d} for d in instants]
                   + [{"period_type": "duration", "period_start": a, "period_end": b} for a, b in durations],
        "rotation": page.rotation or (90 if upright and sum(upright) / len(upright) < 0.5 else 0),
        "continues_on_next_page": "bs" in kinds and "資産合計" in flat and "負債純資産合計" not in flat,
    }
