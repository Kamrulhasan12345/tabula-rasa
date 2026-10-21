"""M4: find statement pages in data/tdnet/*/doc.pdf (text-layer scan, selection only) -> data/tdnet/pages.json."""
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).parent.parent))
from tabula.router import STATEMENTS

pages, radical_docs = [], collections.Counter()
for d in sorted(Path("data/tdnet").iterdir()):
    if not d.is_dir():
        continue
    meta = json.loads((d / "meta.json").read_text())
    with pdfplumber.open(d / "doc.pdf") as pdf:
        for i, p in enumerate(pdf.pages, 1):
            txt = p.extract_text() or ""
            rad = sum(1 for c in txt if 0x2E80 <= ord(c) <= 0x2FDF)
            if rad:
                radical_docs[meta["name"]] += rad
            norm = unicodedata.normalize("NFKC", txt)
            head = "\n".join(norm.splitlines()[:10])
            kinds = [k for k, pat in STATEMENTS if k != "summary" and re.search(r"^\s*(\(\d+\)|[①-⑩]|\d+\.)?\s*(要約)?(四半期|中間)?(連結|個別)?(" + pat + ")", head, re.M)]
            img_only = not txt.strip() and bool(p.images)
            if kinds or img_only:
                unit = re.search(r"単位[:]?\s*(千円|百万円|円)", norm)
                pages.append({"doc": d.name, "name": meta["name"], "stratum": meta["stratum"], "page": i, "kinds": kinds,
                              "unit": unit and unit.group(1), "img_only": img_only, "landscape": p.width > p.height,
                              "teisei": "訂正前" in norm or "訂正後" in norm, "radicals": rad})
Path("data/tdnet/pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1))
print(len(pages), "statement pages from", len({p["doc"] for p in pages}), "docs")
print("by kind:", collections.Counter(k for p in pages for k in p["kinds"]))
print("by unit:", collections.Counter(p["unit"] for p in pages))
print("landscape:", sum(p["landscape"] for p in pages), "| image-only:", sum(p["img_only"] for p in pages), "| 訂正前/後:", sum(p["teisei"] for p in pages))
print("docs with radical code points:", dict(radical_docs))
print("pages per stratum:", dict(collections.Counter(p["stratum"] for p in pages)))
