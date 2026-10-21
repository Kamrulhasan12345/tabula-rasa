"""Hosted extraction / decision APIs (PRD Tracks B and C), built from their docs (2026-10-22). No keys yet:
each adapter returns None when its key is unset, so the pipeline and eval skip it.

| service          | role here                                   | env (key / base URL override)                |
|------------------|---------------------------------------------|----------------------------------------------|
| ScaleDown Extract| table reader (OCR then extract), Track B    | SCALEDOWN_API_KEY / TABULA_SCALEDOWN_URL     |
| TypeLLM          | table reader (typed arrays), Track C        | TYPELLM_API_KEY / TABULA_TYPELLM_URL         |
| Amos 1           | yes/no check of disputed cells vs the image | TABULA_AMOS=1 (no key) / TABULA_AMOS_URL     |
| Sqwish Kettei    | row_type classifier with abstention         | SQWISH_API_KEY / TABULA_SQWISH_URL           |

All of them get the rendered page image or our verified words, never the raw PDF text layer (hidden-text
injection, PRD §8). Readers return the same raw JSON shape as the VLM, so tabula.b1.convert maps them to §4.
Docs: Amos is English-only; none of the four documents Japanese support. Expect weaker results until tested.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

TIMEOUT = 120
ROW_TYPES = {"header": "section heading row with no numbers (e.g. 資産の部, 流動資産)",
             "item": "ordinary line item with numbers", "contra": "deduction from the row above, e.g. 減価償却累計額, 貸倒引当金",
             "net": "net amount after a deduction, label contains 純額", "subtotal": "subtotal of a section (…合計 inside a part)",
             "total": "grand total of a part, e.g. 資産合計, 負債純資産合計"}
TABLE_RULES = """Transcribe the financial statement table in the page image. Copy every cell exactly as printed:
keep △ / ▲ (negative), － (nil), commas and full-width characters; do not convert numbers.
One row per printed row, top to bottom, including section heading rows without numbers.
path: the section headings the row sits under, outermost first, joined with " / " (e.g. "資産の部 / 流動資産").
values: one string per value column, left to right; "" for an empty cell. unit: the unit text above the table (e.g. 千円).
columns: one per value column, its header label and the date printed in it."""


def post_json(url, body, headers=None, timeout=TIMEOUT, tries=5):
    """POST JSON; on 429 / 503 waits Retry-After (or 2^n s) and retries, as the vendors' docs ask."""
    data = json.dumps(body, ensure_ascii=False).encode()
    for n in range(tries):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or n == tries - 1:
                raise
            time.sleep(min(float(e.headers.get("Retry-After") or 2 ** (n + 1)), 60))


def _b64(png):
    return base64.b64encode(png).decode()


def _rows_from(result):
    """Vendor result {unit, columns, rows[{label, path "a / b", values[]}]} -> VLM raw shape for b1.convert."""
    if not result:
        return None
    rows = [{"label": r.get("label") or "", "values": r.get("values") or [],
             "path": [p.strip() for p in (r.get("path") or "").split("/") if p.strip()]} for r in result.get("rows") or []]
    return {"unit": result.get("unit") or "", "columns": result.get("columns") or [], "rows": rows}


# ---- table readers --------------------------------------------------------------------------------------

def _table_schema(str_t="string"):
    obj = lambda props: {"type": "object", "properties": props}
    return {"unit": {"type": str_t, "description": "unit text printed above the table, e.g. 千円"},
            "columns": {"type": "array", "items": obj({"label": {"type": str_t}, "date": {"type": str_t}})},
            "rows": {"type": "array", "items": obj({"label": {"type": str_t}, "path": {"type": str_t},
                                                    "values": {"type": "array", "items": {"type": str_t}}})}}


def scaledown_read(png):
    key = os.environ.get("SCALEDOWN_API_KEY")
    if not key:
        return None
    base = os.environ.get("TABULA_SCALEDOWN_URL", "https://api.scaledown.xyz/v1")
    body = {"model": "extract-1",
            "messages": [{"role": "system", "content": TABLE_RULES},
                         {"role": "user", "content": "Transcribe the table in the attached page image."}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "statement_table",
                                "schema": {"type": "object", "properties": _table_schema()}}},
            # docs: OpenAI SDK's extra_body is merged into the top-level JSON
            "document": _b64(png), "document_mime_type": "image/png"}
    resp = post_json(base + "/chat/completions", body, {"x-api-key": key})
    return _rows_from(json.loads(resp["choices"][0]["message"]["content"]).get("structured_result"))


def typellm_read(png):
    key = os.environ.get("TYPELLM_API_KEY")
    if not key:
        return None
    base = os.environ.get("TABULA_TYPELLM_URL", "https://api.typellm.ai/v1")
    qs = {k: {**{kk: vv for kk, vv in v.items() if kk != "description"}, "instructions": v.get("description", "")}
          for k, v in _table_schema().items()}
    # TypeLLM: "arrays inside objects are not supported yet" -> a row's values come as one " | "-joined string
    qs["rows"]["items"]["properties"]["values"] = {"type": "string"}
    qs["rows"]["instructions"] = TABLE_RULES + '\nFor TypeLLM: give values as one string, cells separated by " | " (e.g. "1,234 | △56 | ").'

    body = {"context": TABLE_RULES, "images": ["data:image/png;base64," + _b64(png)], "questions": qs,
            "options": {"temperature": 0}, "timeout": 90}  # ponytail: >200 rows needs the continue_from flow
    resp = post_json(base + "/generate", body, {"Authorization": "Bearer " + key}, timeout=100)
    res = resp.get("result") or {}
    for r in res.get("rows") or []:
        if isinstance(r.get("values"), str):
            r["values"] = [v.strip() for v in r["values"].split("|")]
    return _rows_from(res)


READERS = {"scaledown": scaledown_read, "typellm": typellm_read}


# ---- decision models --------------------------------------------------------------------------------------

def amos_verify(png, cells, model="amos-1-medium"):
    """cells: [(key, row_label, column_index, raw_text)] -> {key: P(image shows it)}; None when disabled.
    Opt-in (TABULA_AMOS=1) because it sends the page image to a third-party service."""
    if os.environ.get("TABULA_AMOS") != "1" or not cells:
        return None
    base = os.environ.get("TABULA_AMOS_URL", "https://api.amos.scira.ai/v1")
    out = {}
    for i in range(0, len(cells), 32):
        qs = {f"q{j}": {"type": "noul", "instructions":
                        f'In this table, the row labelled "{label}" shows exactly "{raw}" in value column {col + 1} (counting value columns from the left).'}
              for j, (_, label, col, raw) in enumerate(cells[i:i + 32])}
        resp = post_json(base + "/systemone", {"model": model, "state": "Page image of a Japanese financial statement table.",
                                               "questions": qs, "images": [_b64(png)]})
        for j, (k, *_) in enumerate(cells[i:i + 32]):
            out[k] = (resp.get("answers") or {}).get(f"q{j}", {}).get("noul")
    return out


def sqwish_row_types(rows, abstain_below=0.6):
    """rows: §4 rows (verified labels/values only) -> {row_id: (row_type | None if abstained, p_top)}."""
    key = os.environ.get("SQWISH_API_KEY")
    if not key or not rows:
        return None
    base = os.environ.get("TABULA_SQWISH_URL", "https://api.sqwish.ai/v1")
    table = "\n".join(f"{r['id']} | {' / '.join(r.get('path') or [])} | {r['label']} | "
                      + ", ".join(str(v) for v in (r.get('raw') or {}).values() if v) for r in rows)
    out = {}
    for i in range(0, len(rows), 128):
        decisions = [{"id": r["id"], "question": f"What kind of row is {r['id']} ({r['label']})?", "kind": "single",
                      "outcomes": ROW_TYPES, "policy": {"abstain_below": abstain_below}} for r in rows[i:i + 128]]
        resp = post_json(base + "/decide", {"model": "kettei-core", "context": "Japanese financial statement rows "
                                            "(id | section path | label | values):\n" + table, "decisions": decisions},
                         {"Authorization": "Bearer " + key})
        for rid, d in (resp.get("decisions") or {}).items():
            out[rid] = (None if (d.get("policy") or {}).get("action") == "abstain" else d.get("top"), d.get("p_top"))
    return out
