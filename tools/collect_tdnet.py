"""M4 data collection: sample TDnet 決算短信 filings by stratum, download PDF + XBRL zip.

    .venv/bin/python -I tools/collect_tdnet.py <candidates.json> data/tdnet
candidates.json comes from tdnet.documents(day, has_xbrl=True) over the last 31 days (TDnet keeps 31 days).
Polite: one request every 2 s; files stay local (data/tdnet is git-ignored, filings are not redistributed).
Uses the `tdnet` package (AGPL-3.0) for data collection only; the parser does not import it.
"""
import json
import random
import sys
import time
import unicodedata
from pathlib import Path

import httpx

QUOTA = {"JGAAP-連結-年度": 6, "JGAAP-連結-四半期": 4, "JGAAP-非連結": 3, "IFRS": 2, "訂正-IFRS": 1, "REIT": 2, "訂正-JGAAP": 4}


def stratum(title):
    t = unicodedata.normalize("NFKC", title)
    std = "IFRS" if "IFRS" in t else "US" if "米国" in t else "REIT" if "REIT" in t else "JGAAP"
    if "訂正" in t:
        return f"訂正-{std}"
    if std != "JGAAP":
        return std
    if "非連結" in t or "連結" not in t:
        return "JGAAP-非連結"
    return "JGAAP-連結-" + ("四半期" if "四半期" in t or "中間" in t else "年度")


def main(cand_path, out):
    cands = json.loads(Path(cand_path).read_text())
    rng = random.Random(42)
    picked = []
    for s, n in QUOTA.items():
        pool = [c for c in cands if stratum(c["title"]) == s]
        picked += [c | {"stratum": s} for c in rng.sample(pool, min(n, len(pool)))]
    out = Path(out)
    with httpx.Client(timeout=60, follow_redirects=True) as http:
        for c in picked:
            d = out / c["doc_id"]
            d.mkdir(parents=True, exist_ok=True)
            # document_url of an XBRL filing points at the zip (0812…); the PDF shares the suffix (1401…)
            urls = {"doc.pdf": f"https://www.release.tdnet.info/inbs/1401{c['doc_id'][4:]}.pdf", "xbrl.zip": c["xbrl"]}
            for name, url in urls.items():
                if url and not (d / name).exists():
                    r = http.get(url); r.raise_for_status(); (d / name).write_bytes(r.content); time.sleep(2)
            assert (d / "doc.pdf").read_bytes()[:4] == b"%PDF", d
            (d / "meta.json").write_text(json.dumps(c, ensure_ascii=False, indent=1))
            print(c["stratum"], c["name"], c["title"][:50])
    print(len(picked), "filings")


if __name__ == "__main__":
    main(*sys.argv[1:])
