"""Hybrid merge logic (no OCR): agreement, disagreement, tainted cells, scan fallback."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from tabula.hybrid import merge

COLS = [{"id": "c0", "period_end": "2025-03-31"}, {"id": "c1", "period_end": "2026-03-31"}]


def row(label, raw, values):
    return {"label": label, "path": ["資産の部"], "raw": raw, "values": values}


b0 = {"columns": COLS, "rows": [
    row("現金及び預金", {"c0": "1,000", "c1": "2,000"}, {"c0": 1000, "c1": 2000}),
    row("売掛金", {"c0": "300", "c1": "△40"}, {"c0": 300, "c1": None}),  # c1 tainted in B0
]}
b1 = {"columns": COLS, "rows": [
    row("現金及び預金", {}, {"c0": 1000, "c1": 2900}),  # VLM misreads c1
    row("売掛金", {}, {"c0": 300, "c1": -40}),
]}

d = merge(b0, b1)
assert d["engine"] == "b0+b1" and d["status"] == "review"
assert d["rows"][0]["cell_status"] == {"c0": "agreed", "c1": "review"}
assert d["rows"][1]["cell_status"] == {"c0": "agreed", "c1": "review"}
assert d["rows"][0]["values"]["c1"] == 2000  # B0's value is kept

ok = merge({**b0, "rows": b0["rows"][:1]}, {**b1, "rows": [row("現金及び預金", {}, {"c0": 1000, "c1": 2000})]})
assert ok["status"] == "ok"

alone = merge(b0, None)
assert alone["engine"] == "b0" and alone["rows"][0]["cell_status"]["c0"] == "b0_only"

scan = merge({"rows": [], "columns": [], "error": "no table"}, b1)
assert scan["engine"] == "b1" and scan["rows"][0]["cell_status"] == {"c0": "reader_only", "c1": "reader_only"}

assert merge({"rows": [], "columns": []}, None)["status"] == "failed"
print("ok")

# reader policy (no network): fallback chain, reply cache, demo replay
import os, tempfile  # noqa: E401,E402
from tabula import hybrid, remote  # noqa: E402

for k in ("TYPELLM_API_KEY", "SCALEDOWN_API_KEY", "TABULA_DEMO", "TABULA_VLM_URL"):
    os.environ.pop(k, None)
os.environ |= {"TYPELLM_API_KEY": "x", "SCALEDOWN_API_KEY": "y", "TABULA_CACHE": tempfile.mkdtemp()}
TABLE = {"unit": "千円", "columns": [{"label": "当期", "date": "2026年3月31日"}],
         "rows": [{"label": "現金及び預金", "path": ["資産の部"], "values": ["1,000"]}]}
calls = []


def down(png, settings=None):
    raise OSError("down")


remote.READERS.update(typellm=down, scaledown=lambda png, settings=None: calls.append(1) or TABLE)
PDF = str(Path(__file__).parent.parent / "docs/balance_sheet_after_correction.pdf")
d, m = hybrid.read_second(PDF, 1, "t", "typellm")
assert m["reader"] == "scaledown" and "typellm: OSError" in m["reader_error"] and d["rows"][0]["values"] == {"c0": 1000}
d, m = hybrid.read_second(PDF, 1, "t", "typellm")
assert m["reader"] == "scaledown" and len(calls) == 1  # second time: scaledown's reply comes from the cache

# per-request settings override env: reader off, fallback off, a key given only in the request
assert hybrid.read_second(PDF, 1, "t", "none") == (None, {"reader": None, "skipped": "reader set to none"})
d, m = hybrid.read_second(PDF, 1, "t", "typellm", {"TABULA_FALLBACK": "0"})
assert d is None and m["reader"] is None and "typellm: OSError" in m["reader_error"]
os.environ.pop("SCALEDOWN_API_KEY")
os.environ["TABULA_CACHE"] = tempfile.mkdtemp()
assert hybrid.read_second(PDF, 1, "t", "scaledown", {"TABULA_FALLBACK": "0"})[0] is None  # no key: nothing to call
d, m = hybrid.read_second(PDF, 1, "t", "scaledown", {"SCALEDOWN_API_KEY": "from-request", "TABULA_FALLBACK": "0"})
assert m["reader"] == "scaledown" and len(calls) == 2
assert hybrid.reader_name({"TABULA_READER": "vlm"}) == "vlm" and hybrid.demo({"TABULA_DEMO": "0"}) is False

os.environ["TABULA_DEMO"] = "1"  # replay saved replies of an eval page; no keys or network used
d, m = hybrid.read_second("not-needed.pdf", 7, "081220261008548219", "typellm")
assert m["reader"] == "typellm" and len(d["rows"]) > 30 and len(calls) == 2  # replay: no new call
d, m = hybrid.read_second("not-needed.pdf", 1, "balance_sheet_after_correction", "vlm")
assert m["reader"] == "vlm" and len(d["rows"]) == 43
print("ok policy")
