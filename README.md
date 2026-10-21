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

For every statement page this writes `out/<doc>_p<n>.json` (PRD §4 schema) and `out/<doc>_p<n>.csv`. Each value cell carries a status:

| status | meaning |
|---|---|
| `agreed` | text layer and VLM read the same number |
| `review` | they disagree, or the cell held hidden/unverified text: send to a human |
| `b0_only` | text layer only (no VLM output for this page yet) |
| `vlm_only` | no usable text layer (scan): VLM reading only |

The VLM (Qwen3-VL-8B, Unsloth 4-bit) runs on a Kaggle T4: `kaggle/b1u/`. Its outputs in `runs/b1/kaggle*/b1_outputs.json` are picked up automatically.

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
| `tabula/evaluate.py`, `run_eval.py` | metrics M1–M6, TEDS |
| `tools/` | data collection (TDnet, EDINET), silver-gold builder, synthetic scans, report tables |
| `tests/` | `test_m0.py` … `test_hybrid.py`, poisoned-PDF fixtures |
