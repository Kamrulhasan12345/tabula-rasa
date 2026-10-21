"""M4: find statement pages in data/edinet/*.pdf (text-layer scan, one PDF at a time) -> data/edinet/pages.json.
Selection: per doc consolidated BS, consolidated PL, one 個別 BS (<= 3 pages)."""
import collections
import csv
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).parent.parent
TITLE = re.compile(r"^[①-⑩\d.()\s]*【((?:連結)?(?:損益及び包括利益計算書|損益計算書|貸借対照表|財政状態計算書|包括利益計算書|キャッシュ・?フロー計算書|株主資本等変動計算書|持分変動計算書))】", re.M)
KIND = [("pl_ci", "損益及び包括利益"), ("ci", "包括利益"), ("pl", "損益"), ("bs", "貸借対照表|財政状態"), ("ss", "変動計算書"), ("cf", "キャッシュ")]
pages = []
for pdf_path in sorted((ROOT / "data/edinet").glob("S100*.pdf")):
    doc = pdf_path.stem
    with zipfile.ZipFile(ROOT / f"data/edinet/{doc}/xbrl.zip") as z:
        name = next(n for n in z.namelist() if n.endswith("XbrlSearchDlInfo.csv"))
        row = list(csv.reader(io.StringIO(z.read(name).decode("cp932"))))[2]
    ifrs, found = False, []
    with pdfplumber.open(pdf_path) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            norm = unicodedata.normalize("NFKC", p.extract_text() or "")
            head = "\n".join(norm.splitlines()[:8])
            m = TITLE.search(head)
            if not m:
                continue
            t = m.group(1)
            unit = re.search(r"単位[:]?\s*(千円|百万円|円)", norm)
            if not unit:
                continue
            kinds = [k for k, pat in KIND if re.search(pat, t)][:1]
            ifrs |= "財政状態" in t or "持分変動" in t
            found.append({"doc": doc, "name": row[3], "title": row[1], "page": i, "kinds": kinds, "title_text": t,
                          "scope": "consolidated" if t.startswith("連結") else "non_consolidated", "unit": unit.group(1),
                          "landscape": p.width > p.height, "teisei": "訂正前" in norm or "訂正後" in norm})
    strat = "EDINET-訂正" if "訂正" in row[1] else "EDINET-IFRS" if ifrs else "EDINET-JGAAP"
    for f in found:
        f["stratum"] = strat
    pages += found
    print(doc, strat, [(f["page"], f["title_text"]) for f in found], flush=True)
(ROOT / "data/edinet/pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1))
print(len(pages), collections.Counter(p["stratum"] for p in pages))
