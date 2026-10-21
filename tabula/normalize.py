import re
import unicodedata
from pathlib import Path

from jeraconv import jeraconv

NIL = set("－―—-ー−")  # U+FF0D U+2015 U+2014 - U+30FC U+2212
NEG = "△▲−-－"
NUM = re.compile(r"^(\d{1,3}(,\d{3})*|\d+)$")


def _radicals():
    t = {}
    for line in (Path(__file__).parent.parent / "data/EquivalentUnifiedIdeograph.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        src, dst = (x.strip() for x in line.split(";"))
        lo, _, hi = src.partition("..")
        for cp in range(int(lo, 16), int(hi or lo, 16) + 1):
            t[cp] = chr(int(dst, 16))
    return t


RADICALS = _radicals()


def parse_value(raw):
    s = (raw or "").strip()
    if not s:
        return None
    if s in NIL:
        return 0
    neg = False
    if s[0] in NEG:
        neg, s = True, s[1:]
    elif s[0] in "(（" and s[-1] in ")）":
        neg, s = True, s[1:-1]
    s = unicodedata.normalize("NFKC", s).strip()
    if not NUM.match(s):
        return "unparsed"
    n = int(s.replace(",", ""))
    return -n if neg else n


def normalize_label(raw):
    s = unicodedata.normalize("NFKC", raw.translate(RADICALS))
    refs = re.findall(r"[※*]\s*(\d+)", s)
    s = re.sub(r"[※*]\s*\d+", "", s)
    return " ".join(s.split()), refs


def parse_jp_date(s):
    s = "".join(unicodedata.normalize("NFKC", s).split())
    m = re.search(r"(令和|平成|昭和|大正|明治)(元|\d+)年", s)
    year = jeraconv.J2W().convert(m.group(0)) if m else int(re.search(r"(\d{4})年", s).group(1))
    mo, d = re.search(r"(\d+)月(\d+)日", s).groups()
    return f"{year:04d}-{int(mo):02d}-{int(d):02d}"


def infer_rounding(diffs):
    if all(d >= 0 for d in diffs) and any(d > 0 for d in diffs):
        return "truncate"
    if any(d < 0 for d in diffs) and any(d > 0 for d in diffs):
        return "round"
    return "unknown"
