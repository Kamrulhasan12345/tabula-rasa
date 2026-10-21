# Tabula Rasa

Japanese financial-filing PDF page → correct, typed table (決算短信, 有価証券報告書, 訂正 filings).
Take-home for Ur AI (Document Intelligence & VLM R&D). Results and recommendation: [REPORT.md](REPORT.md). Design: [docs/PRD.md](docs/PRD.md).

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Linux also needs `qpdf` and a CJK font (e.g. `noto-fonts-cjk`): many EDINET PDFs don't embed their fonts.

```bash
.venv/bin/python -m tabula docs/balance_sheet_after_correction.pdf -o out/
```

For every statement page this writes `out/<doc>_p<n>.json` (PRD §4 schema), `.csv` and `.html`. Each value cell carries a status:

| status | meaning |
|---|---|
| `agreed` | text layer and VLM read the same number |
| `review` | they disagree, or the cell held hidden/unverified text: send to a human |
| `b0_only` | text layer only (no VLM output for this page yet) |
| `vlm_only` | no usable text layer (scan): VLM reading only |

Each page also gets:
- `cell_bbox` per cell: `[x0, top, x1, bottom]` in PDF points, top-left origin. Text-layer pages only.
- `check_results`: sum checks inferred from the 合計 / 小計 rows, run with the filing's rounding rule.
- `warnings` and a page `status` of `ok` or `review`.

## VLM: live or demo

The VLM reader (`tabula/vlm.py`) speaks the OpenAI-compatible chat API. That's what `vllm serve Qwen/Qwen3-VL-8B-Instruct` and most hosted VLMs expose.

| env | effect |
|---|---|
| `TABULA_VLM_URL=http://host:8000/v1` | live: each page image is sent there |
| `TABULA_VLM_MODEL`, `TABULA_VLM_KEY` | model name, bearer token |
| unset (default) | demo: replays saved Kaggle outputs (`runs/b1/kaggle*/`) for the 86 eval pages; other PDFs get the text-layer reader alone |

## HTTP API (for a UI)

```bash
.venv/bin/uvicorn tabula.server:app --port 8000
```

Interactive docs: http://localhost:8000/docs

| call | what |
|---|---|
| `POST /documents` (multipart `file`, optional `name`) | upload; returns `id`, page count, detected table pages |
| `POST /documents/{id}/parse?pages=3,5` | start parsing in the background (202) |
| `GET /documents/{id}` | `state`: uploaded / queued / running / done / failed, plus `pages_done` |
| `GET /documents/{id}/pages/{n}` | page JSON |
| `GET /documents/{id}/pages/{n}.csv`, `.html` | derived views; HTML cells carry `class="<cell_status>"` |
| `GET /documents/{id}/pages/{n}/image.png?dpi=100` | page image; box px = points × dpi / 72 |

More env vars:
- `TABULA_STORE`: upload folder.
- `TABULA_CORS_ORIGINS`: UI origins allowed to call the API.

In Python: `from tabula.api import parse_pdf`.

## Evaluate

```bash
.venv/bin/python -m tabula.run_eval b0 b1 hybrid
```

```bash
.venv/bin/python tools/by_group.py b0 b1 hybrid
```

```bash
.venv/bin/python tools/calibration.py
```

Gold: `gold/*.json`, 86 pages. Every number is matched to the filing's own XBRL; see PRD §5.2.

## Layout

| path | what |
|---|---|
| `tabula/security.py` | trusts only text that is visibly painted on the page (OCR cross-check); blocks hidden-text prompt injection |
| `tabula/normalize.py`, `checks.py` | △/－/radicals/和暦 normalization; rounding-aware sum checks |
| `tabula/router.py` | statement type, scope, standard, unit, periods, 訂正前/後 |
| `tabula/b0.py` | text layer + Camelot + rules (CPU) |
| `tabula/b1.py` | VLM output → §4 schema |
| `tabula/hybrid.py` | B0 + B1 cross-check, per-cell status |
| `tabula/vlm.py` | VLM client: live OpenAI-compatible endpoint, or demo replay |
| `tabula/api.py`, `server.py` | `parse_pdf()` library call; FastAPI server |
| `tabula/evaluate.py`, `run_eval.py` | metrics M1–M6, TEDS |
| `tools/` | data collection (TDnet, EDINET), silver-gold builder, synthetic scans, report tables |
| `tests/` | `test_m0.py` … `test_hybrid.py`, `test_api.py` (fake VLM endpoint), poisoned-PDF fixtures |
