"""VLM reader (B1) behind one call: read_page(key, render) -> {"text", "secs", "tokens"} or None.

The model gets the rendered page image only, never the PDF text layer (hidden-text injection can't reach it).
Modes, picked by environment:
  TABULA_VLM_URL    OpenAI-compatible base URL, e.g. http://gpu-host:8000/v1 from
                    `vllm serve Qwen/Qwen3-VL-8B-Instruct`, or a hosted VLM API  -> live call
  TABULA_VLM_MODEL  model name in the request (default Qwen/Qwen3-VL-8B-Instruct)
  TABULA_VLM_KEY    bearer token, if the endpoint wants one
  (unset)           demo: replay saved Kaggle outputs (runs/b1/kaggle*/b1_outputs.json) for pages
                    we've run; other pages get None and the pipeline continues on B0 alone
"""
import base64
import json
import os
import time
import urllib.request
from functools import lru_cache
from pathlib import Path

from tabula import config

RUNS = Path(__file__).parent.parent / "runs/b1"
TIMEOUT = 900  # s; an 8B VLM on one T4 needs ~300 s for a dense page

# Same prompt as kaggle/b1u/b1_unsloth.py (Kaggle scripts run standalone, so it's copied, not imported).
PROMPT = """This image is one page of a Japanese financial statement (e.g. 連結貸借対照表).
Transcribe the table into JSON. Rules:
- Copy every cell EXACTLY as printed. Keep △ / ▲ (negative), － (nil), commas and full-width characters. Do not convert numbers.
- One entry per printed row, top to bottom, including section heading rows that have no numbers (e.g. 資産の部, 流動資産).
- "path": the section headings this row sits under, outermost first (e.g. ["資産の部", "流動資産"]). For 減価償却累計額, include the asset row it belongs to.
- "values": one string per value column, left to right; "" for an empty cell.
- "unit": the unit text printed above the table (e.g. 千円).
Output only JSON, no markdown:
{"title": "...", "unit": "...", "columns": [{"label": "...", "date": "..."}], "rows": [{"label": "...", "path": ["..."], "values": ["...", "..."]}]}"""


def mode(settings=None):
    return "live" if config.get("TABULA_VLM_URL", settings) else "demo"


@lru_cache(maxsize=1)
def _saved():
    recs = {}
    for f in sorted(RUNS.glob("kaggle*/b1_outputs.json")):  # main run + EDINET-only run
        recs |= json.loads(f.read_text())
    return recs


def _call(png, settings=None):
    url = config.get("TABULA_VLM_URL", settings).rstrip("/") + "/chat/completions"
    body = {"model": config.get("TABULA_VLM_MODEL", settings, "Qwen/Qwen3-VL-8B-Instruct"),
            "temperature": 0, "max_tokens": 6000,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                {"type": "text", "text": PROMPT}]}]}
    headers = {"Content-Type": "application/json"}
    if config.get("TABULA_VLM_KEY", settings):
        headers["Authorization"] = "Bearer " + config.get("TABULA_VLM_KEY", settings)
    t0 = time.perf_counter()
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        resp = json.load(r)
    return {"text": resp["choices"][0]["message"]["content"], "secs": time.perf_counter() - t0,
            "tokens": (resp.get("usage") or {}).get("completion_tokens")}


def read_page(key, render, settings=None):
    """key: "<doc_id>_p<page>.png" (demo lookup); render: () -> PNG bytes (called only in live mode)."""
    if mode(settings) == "demo":
        return _saved().get(key)
    return _call(render(), settings)
