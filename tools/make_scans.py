"""M4 tier T4: synthetic scans of confirmed silver pages (no real scans in the 31-day TDnet window).

    .venv/bin/python -I tools/make_scans.py [n=6]
Renders the page at 200 dpi, rotates 0.4–1.5°, blurs, adds noise, JPEG-compresses, and saves an
image-only PDF (no text layer). Gold is copied with doc_id "<doc>_scan" since the numbers are unchanged.
"""
import json
import random
import sys
from pathlib import Path

import numpy as np
import pdfplumber
from PIL import Image, ImageFilter

ROOT = Path(__file__).parent.parent


def degrade(img, rng):
    img = img.convert("L").rotate(rng.uniform(0.4, 1.5) * rng.choice([-1, 1]), expand=True, fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 0.9)))
    a = np.asarray(img, dtype=np.float32) + np.random.default_rng(rng.randrange(1 << 30)).normal(0, 8, img.size[::-1])
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def main(n=6):
    rng = random.Random(7)
    golds = [g for g in sorted((ROOT / "gold").glob("0812*.json")) if json.loads(g.read_text()).get("gold_status") == "confirmed"]
    for gp in rng.sample(golds, min(n, len(golds))):
        gold = json.loads(gp.read_text())
        with pdfplumber.open(ROOT / gold["source"]["pdf"]) as pdf:
            img = pdf.pages[gold["page"] - 1].to_image(resolution=200).original
        out = ROOT / "data/tdnet" / gold["doc_id"] / f"scan_p{gold['page']}.pdf"
        degrade(img, rng).save(out, "PDF", resolution=200, quality=70)
        gold.update(doc_id=f"{gold['doc_id']}_scan{gold['page']}", page=1, source=gold["source"] | {"pdf": str(out.relative_to(ROOT)), "stratum": "T4-synthetic-scan"})
        (ROOT / "gold" / f"{gold['doc_id']}_p1.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1))
        print(out.relative_to(ROOT))


if __name__ == "__main__":
    main(*map(int, sys.argv[1:]))
