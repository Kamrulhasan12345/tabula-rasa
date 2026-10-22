"""PRD §8 S1–S3: trust a text-layer word only if it is visibly painted where it claims to be.

The rendered image is the source of truth. Each word must pass, in order:
  tiny          font size >= 4pt
  low-contrast  darkest pixel in its box is clearly darker than the box's typical pixel
                (catches white / band-coloured / render-mode-3 / off-page / covered text)
  overlap       no other text-layer word overlaps it (catches a fake value laid over a real one)
  ocr           RapidOCR (PP-OCRv6) reads the same text at that position
                (catches remapped ToUnicode glyphs: digits exact, kanji variant-tolerant;
                lone dashes skip this, OCR drops thin glyphs)
A flagged word is not used for extraction; a page with flags goes to human review.
Metadata, annotations, form fields and attachments are never read, so they need no check.
"""
import re
import threading
import unicodedata

import numpy as np
import opencc
from rapidfuzz.distance import Levenshtein
from rapidocr import RapidOCR

DPI = 200
MIN_SIZE = 4
MIN_CONTRAST = 80  # gray levels; clean black text ~200+, band-coloured fake ~30, white 0
DASHES = set("-－―—ー−")

_ocr = None
_t2jp = opencc.OpenCC("t2jp")  # OCR reads 産 as 產; fold variants before comparing


def _norm(t):
    return _t2jp.convert(unicodedata.normalize("NFKC", t)).replace(" ", "")


def _same(t, h):
    """Word t vs OCR line h. Digits must appear exactly and in order (tampered numbers matter);
    other characters may differ by OCR variant glyphs (単/单, 舗/舖), normalized edit distance <= 0.25."""
    if t in h:
        return True
    td, hd = re.findall(r"\d+", t), re.findall(r"\d+", h)
    if td and not any(hd[k:k + len(td)] == td for k in range(len(hd) - len(td) + 1)):
        return False
    a, b = re.sub(r"[\d,.]", "", t), re.sub(r"[\d,.]", "", h)
    return a in b or Levenshtein.normalized_distance(a, b) <= 0.25


def _overlaps(a, b):
    return a["x0"] < b["x1"] and b["x0"] < a["x1"] and a["top"] < b["bottom"] and b["top"] < a["bottom"]


RENDER_LOCK = threading.Lock()


def render(page, dpi):
    """pdfplumber page -> PIL image. pdfium (pypdfium2) is not thread-safe, and reader calls / the server's image
    endpoint render from other threads, so every render in the project goes through this lock."""
    with RENDER_LOCK:
        return page.to_image(resolution=dpi).original


def ocr_lines(img):
    """PIL page render -> [(normalized text, x0, top, x1, bottom)] in render pixels."""
    global _ocr
    _ocr = _ocr or RapidOCR()
    r = _ocr(np.asarray(img.convert("RGB")))
    return [(_norm(t), b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()) for t, b in zip(r.txts or (), r.boxes if r.boxes is not None else ())]


def verify_page(page):
    """page: pdfplumber Page -> (words, flags). Each word gets "verified": bool."""
    img = render(page, DPI)
    gray = np.asarray(img.convert("L"))
    ocr = ocr_lines(img)
    s = DPI / 72
    words = page.extract_words(extra_attrs=["size"])
    flags = []
    for w in words:
        x0, y0, x1, y1 = (v * s for v in (w["x0"], w["top"], w["x1"], w["bottom"]))
        box = gray[max(int(y0), 0):max(int(y1) + 1, 0), max(int(x0), 0):max(int(x1) + 1, 0)]
        contrast = int(np.median(box)) - int(box.min()) if box.size else 0
        t = _norm(w["text"])
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hits = [o[0] for o in ocr if o[1] - 4 <= cx <= o[3] + 4 and o[2] - 4 <= cy <= o[4] + 4]
        if w["size"] < MIN_SIZE:
            reason = "tiny"
        elif contrast < MIN_CONTRAST:
            reason = "low-contrast"
        elif any(o is not w and _overlaps(o, w) for o in words):
            reason = "overlap"
        elif t and set(t) <= DASHES:
            reason = None
        elif not hits:
            reason = "not-visible"
        elif not any(_same(t, h) for h in hits):
            reason = "ocr-differs"
        else:
            reason = None
        w["verified"] = reason is None
        if reason:
            flags.append({"reason": reason, "text": w["text"], "bbox": [w["x0"], w["top"], w["x1"], w["bottom"]], "ocr": hits})
    return words, flags
