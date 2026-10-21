# Progress log

For agents and people picking this project up. Newest state first. Results: [REPORT.md](../REPORT.md). Design and research: [PRD.md](PRD.md).

## Current state (2026-10-22, commit `f897167`)

The MVP submission is done:
- M0–M4 milestones, plus a hybrid parser (B0 text layer + B1 VLM cross-check).
- A command-line tool and REPORT.md.

| Approach | Cells | Signs | Page exact | Hierarchy | Time/page |
|---|---|---|---|---|---|
| B0: text layer + Camelot + rules | 0.929* | 1.000 | 0.47 | 0.93 | 18 s CPU |
| B1: Qwen3-VL-8B, Unsloth 4-bit, zero-shot, image only | 0.995 | 0.999 | 0.44 | 0.66 | ~297 s T4 |
| **Hybrid** | **0.999** | **1.000** | **0.94** | **0.975** | both |

- Scored on 85 gold pages (3,811 cells).
- \* B0's scores are optimistic by construction (gold structure comes from B0). B0 scores 0 on scans.
- B1's numbers are the clean ones.
- **Calibration:** cells where B0 and B1 agree are 92% of all cells, and all 3,521 of them are correct.

## Milestone log

| Milestone | What was built | Key files |
|---|---|---|
| PRD | Problem, Japanese filing traits, metrics, 4 approach tracks, security model, model shortlist | `docs/PRD.md` |
| M0 | Hand-made gold for the sample page; normalizer; rounding-aware sum checks | `gold/p001.json`, `tabula/normalize.py`, `tabula/checks.py` |
| M1 | Hidden-text / injection defence (visible-text check with OCR); page router | `tabula/security.py`, `tabula/router.py`, `tests/fixtures/` |
| M2 | Eval harness: M1–M6 metrics, TEDS, rows aligned by label + path (never by value) | `tabula/evaluate.py`, `tabula/run_eval.py` |
| M3 | Baselines B0 (CPU) and B1 (Kaggle T4) | `tabula/b0.py`, `tabula/b1.py`, `kaggle/` |
| M4 | 86-page eval set: TDnet + EDINET + synthetic scans, every value matched to XBRL | `tools/build_silver.py`, `tools/collect_tdnet.py`, `tools/make_scans.py`, `gold/` |
| MVP | Hybrid with per-cell status; `python -m tabula` CLI; B1 adapter fixes; 21 gold pages rebuilt; report | `tabula/hybrid.py`, `tabula/__main__.py`, `tools/by_group.py`, `tools/calibration.py`, `REPORT.md` |

Not started:
- **M5:** API tracks (ScaleDown, TypeLLM, Amos, Sqwish). Needs the user's API keys.
- **M6:** fine-tuning.
- **M7:** red-team set and the injection-robustness metric (M9).

## What's left for "plug and play in a UI"

| # | Item | PRD ref | Time |
|---|---|---|---|
| 1 | **Live VLM call.** B1 only reads saved Kaggle outputs (`runs/b1/kaggle*/b1_outputs.json`), so new PDFs get B0 only. Waiting on the user: self-hosted GPU endpoint or hosted VLM API | F1, F7 | 3–4 h |
| 2 | `parse(pdf_bytes) -> [page JSON]` function + FastAPI server | F1, F3 | 1–2 h |
| 3 | Cell `bbox` in output (B0 has word boxes; not emitted yet) | §4 | 1–2 h |
| 4 | Sum checks at runtime, from 合計 rows, without XBRL | F6 | 2–3 h |
| 5 | Latency p95 ≤ 10 s/page (VLM ~300 s; security OCR ~18 s) | §9 | 4–8 h |
| 6 | All tables on a page; B0 and gold take only Camelot's first table | F1 | 2–3 h |
| 7 | Multi-page stitching; check 資産合計 = 負債純資産合計 | F8 | 2–3 h |
| 8 | Per-row unit overrides (1株当たり 円, 株, %) | F4a | 1 h |
| 9 | HTML output (`evaluate.to_html` exists; wire it into the CLI) | F3 | 15 min |
| 10 | Keep 訂正前 and 訂正後 tables apart when both are on one page | F6a | 2 h |

## How things fit together

```
PDF page ─┬─ security.verify_page  (keep only visibly painted words; OCR cross-check)
          ├─ router.route          (statement, scope, standard, unit, periods, 訂正)
          ├─ b0.extract            (Camelot cells rebuilt from verified words → §4 JSON)
          ├─ b1.predict            (saved VLM output → §4 JSON via convert())
          └─ hybrid.merge          (B0 values; cell_status agreed/review/b0_only/vlm_only;
                                    periods filled from B1 when the router found none)
```

- **Output:** PRD §4 schema, one JSON per page. The CLI also writes a CSV.
- **Gold:** `gold/<doc_id>_p<page>.json`.
  - `gold_status` is `confirmed`, `review` or `excluded`; only `confirmed` pages are scored.
  - The sample page's gold is `gold/p001.json`.
- **Predictions:** `runs/<approach>/<doc_id>_p<page>.json`. Score history is in `runs/results.csv`.

## Commands

```bash
.venv/bin/python -m tabula docs/balance_sheet_after_correction.pdf -o out/
```

```bash
.venv/bin/python -m tabula.run_eval b1 hybrid
```

```bash
.venv/bin/python tools/by_group.py b0 b1 hybrid
```

```bash
.venv/bin/python tools/calibration.py
```

**Notes on scoring:**
- `run_eval b0` re-runs OCR (~30 min for 86 pages).
- To rescore from the saved predictions, override `APPROACHES["b0"]` to load `runs/b0/*.json`. See the inline script in the `f897167` session, or add a `b0_saved` approach.
- `hybrid` already reads `runs/b0/` as a cache.

**Tests:** run one at a time; each prints `ok`.
- `tests/test_m0.py`
- `tests/test_m1.py` (~3 min, OCR)
- `tests/test_m2.py`
- `tests/test_m3.py`
- `tests/test_hybrid.py`

Rebuild specific gold pages (needs local PDFs + XBRL):
```bash
.venv/bin/python -I tools/build_silver.py --only=DOCID_pN,DOCID_pM
```
Add `--edinet` for EDINET pages. After rebuilding a TDnet page that has a `_scan` copy, refresh that scan's gold too. The scan gold is a copy with `doc_id`, `page` and `source` replaced.

## Data and access

**TDnet** (決算短信):
- Collected with the `tdnet` PyPI package (AGPL, used for data collection only, not shipped).
- Only a 31-day window is available.
- PDF URL: `1401{doc_id[4:]}.pdf`.

**EDINET** (有価証券報告書):
- **No API key:** EDINET's sign-up MFA doesn't support Bangladesh, and the terms forbid scraping.
- Files were downloaded by hand / through the browser into `data/edinet/<docID>/xbrl.zip` and `data/edinet/<docID>.pdf`.
- PDF URL: `https://disclosure2dl.edinet-fsa.go.jp/searchdocument/pdf/<docID>.pdf`.
- Labels can also come from SakanaAI/EDINET-Bench.

**Not in git:** PDFs, zips and XBRL (licence and size). A cloud session therefore can't rebuild gold. It can score, since gold, B0 predictions and raw VLM outputs are committed.

**Kaggle** (user `kamrulhasan12345`):
- Private dataset `tabula-rasa-pages`: page PNGs at 200 dpi.
- Kernels:
  - `tabula-rasa-b1-unsloth`: main run, 60 pages.
  - `tabula-rasa-b1-unsloth-edinet`: 26 pages.
  - `tabula-rasa-b1-qwen3vl`: fp16, old.
- `machine_shape: NvidiaTeslaT4` means T4 ×2, run with one worker per GPU.
- The Kaggle CLI only shows a kernel's latest version. Older outputs must be downloaded by hand.

## Gotchas (each cost real time)

**Local machine:**
- No GPU, 4 CPUs, ~5.8 GB RAM with ~1 GB free. **Run one OCR-heavy process at a time**; out-of-memory kills have happened.
- `pkill -f <pattern>` kills its own shell when the pattern appears in the command line (exit 144). Use a pattern that isn't in the command, or kill by PID.

**PDF quirks:**
- **NFKC turns `－` (U+FF0D) into `-`.** Detect nil dashes *before* NFKC (PRD §4.1).
- **EDINET PDFs don't embed fonts.** Without a system CJK font (noto-fonts-cjk), rendering shows only digits, and the security check rightly rejects all kanji.
- **Permission-locked EDINET PDFs:** Camelot refuses them. `hybrid.run_b0` retries on a `qpdf --decrypt` copy.
- **IFRS 注記 columns** (`7`, `7,27`) look like values. A value column must contain real thousands groups (`\d{1,3}(,\d{3})+`).
- 有報 prints note marks beside values (`※1 49,533`). `parse_value` strips them.
- **Totals don't foot exactly** (切り捨て): `total − Σitems ∈ 0..n−1`. Net (純額) rows are within 0..1.

**VLM output (B1 adapter):**
- Header lists include the label column, so headers are right-aligned to the value columns.
- Columns printed as `自X 至Y` are durations ending at Y.

**Gold:**
- Silver gold covers only Camelot's first table on the page, and uses B0's path convention. Low page exact or hierarchy for B1 is partly this.

**Git:**
- Push over HTTPS: `git -c credential.helper='!gh auth git-credential' push`. SSH push fails.

## Working agreements with the user

- **Reuse before building.** Research existing popular libraries before writing any component; don't hand-roll a smaller, riskier version.
- **PDFs are untrusted.** They can carry prompt injection in hidden text. Verify text against the rendered image. Never feed the raw text layer to an LLM.
- **Be token-efficient.** Hand mechanical work to cheaper agents or sessions when it helps.
- **Kaggle T4 + Unsloth** is the GPU path for inference and fine-tuning.
- **Git:** commit in meaningful chunks and push to the public repo (`Kamrulhasan12345/tabula-rasa`). No attribution lines in commit messages.
- **Reports:** user-facing write-ups lead with the action and use concrete numbers.
