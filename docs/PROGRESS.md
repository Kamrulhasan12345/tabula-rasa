# Progress log

For agents and people picking this project up. Newest state first. Results: [REPORT.md](../REPORT.md). Design and research: [PRD.md](PRD.md).

## Current state (2026-10-22)

**Pipeline policy + demo (latest).**
- `hybrid.read_second` takes the cheapest source first: demo replay (`TABULA_DEMO=1`, all readers), then the reply cache keyed by image hash (`runs/cache`), then a live call.
- Fallback chain: TypeLLM, then ScaleDown, then VLM.
- `TABULA_CROSSCHECK=needed` skips the reader on pages B0 can vouch for (`hybrid.needs_reader`): 27% fewer calls on eval.
- `api.parse_pdf` runs reader calls in a thread pool while B0 runs.
- **pdfium is not thread-safe.** Every render goes through `security.render` (one lock). Without it: native crash, `free(): chunks in smallbin corrupted`.
- Demo viewer at `/` (`tabula/static/index.html`), with `/samples` for one-click eval filings. Launch config: `.claude/launch.json` (`tabula-demo`).
- **RAM:** don't run the demo server, the browser pane and an OCR test at the same time (exit 137).


**Tracks B/C + whole-page gold (latest).**
- TypeLLM and ScaleDown run on all 85 pages (keys in `.env`). Gold is whole-page now: 4,619 cells.
- Hybrid B0 + TypeLLM (default reader): 1.000 cells, 0.93 page exact, 0.987 hierarchy, ≈ $0.0001/page.
- TypeLLM alone: 0.996. B1: 0.995. ScaleDown: 0.720.
- Full tables are in REPORT §3.
- Second reader is chosen by `TABULA_READER` (`hybrid.reader_name`). Eval approach: `hybrid_typellm`.
- Bug fixed: Camelot returns overlapping tables, which doubled rows when B0 read every table (verified on the page image). Each word now counts once.


**API milestone (latest).** The VLM no longer depends on Kaggle files at runtime:
- `tabula/vlm.py` calls any OpenAI-compatible endpoint (`TABULA_VLM_URL`), or replays saved outputs in demo mode (the default). No real endpoint exists yet; `tests/test_api.py` stands one up as a fake.
- `tabula/api.py` provides `parse_pdf()`. Output adds `cell_bbox`, inferred sum checks (`check_results`), `warnings` and HTML.
- `tabula/server.py` is the FastAPI server: upload → background parse → poll → page JSON / CSV / HTML / PNG.
- The inferred sum checks were validated on gold values: 356 pass, 10 false alarms (2.7%), from a cost-of-goods schedule with subtracted rows and gold pages missing rows.

**MVP milestone** (commit `f897167`):

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
| 1 | **VLM endpoint:** out of scope for now (user decision 2026-10-22). Demo replay stays the default; client ready (`TABULA_VLM_URL`) | F1, F7 | – |
| 2 | ~~Library function + FastAPI server~~ done (`api.py`, `server.py`) | F1, F3 | – |
| 3 | ~~Cell `bbox`~~ done. Text-layer pages: from verified words. Scans: OCR line matching (`api.locate_cells`), 261/291 cells correct on the synthetic scans (92% located, 98% of those right) | §4 | – |
| 4 | ~~Runtime sum checks~~ done. 合計/小計 structure + J-GAAP profit lines (`checks.infer_checks`). On gold values: 478 pass, 10 false alarms (2%); profit lines 122/122. IFRS P/L not covered | F6 | – |
| 5 | Latency p95 ≤ 10 s/page. Security OCR ~18 s/page dominates without a VLM | §9 | 4–8 h |
| 6 | ~~All tables on a page~~ done in B0 (same-layout tables merge; other layouts dropped, `ponytail:` note). Gold for affected pages needs a rebuild | F1 | – |
| 7 | ~~Multi-page stitching~~ done: `api.stitch` groups pages by `continues_on_next_page`, checks 資産合計 = 負債純資産合計 exactly | F8 | – |
| 8 | ~~Unit overrides~~ done: 1株当たり / (円) → 円 (decimals parsed), 株, %; heading context carries to rows below | F4a | – |
| 9 | ~~HTML output~~ done (`api.to_html`, CLI and server) | F3 | – |
| 10 | Keep 訂正前 and 訂正後 apart when both are on one page. Not built: across all downloaded filings only S100Z4C8 p192 and p198 mention both, and neither is an eval page. TDnet 訂正 PDFs print each version on its own page | F6a | 2 h |

### Tracks B/C (hosted APIs), `tabula/remote.py`

| Service | Role | Status |
|---|---|---|
| ScaleDown Extract | table reader (OCR then extract) | **Live.** Key in `.env`. Approach `scaledown` in `run_eval`; replies cached in `runs/scaledown/raw/` |
| TypeLLM | table reader (typed arrays) | **Live.** Key in `.env`. Approach `typellm`. Values come as one `" | "`-joined string, since nested arrays are unsupported |
| Amos 1 | yes/no check of disputed cells vs image | Adapter only; opt-in `TABULA_AMOS=1` (no key; English-only model) |
| Sqwish Kettei | row_type classifier with abstention | Adapter only; needs `SQWISH_API_KEY` (possible waitlist) |

- **Keys** live in `.env`, which is git-ignored and loaded by `tabula/__init__.py`. Never print or commit them.
- **Budget:** TypeLLM $5 credit, ScaleDown 50M free tokens. Each page is called once and the reply is cached.
- **Tests:** `tests/test_remote.py` clears the keys and uses a fake server, so it never spends credit.

## How things fit together

```
PDF page ─┬─ security.verify_page  (keep only visibly painted words; OCR cross-check)
          ├─ router.route          (statement, scope, standard, unit, periods, 訂正)
          ├─ b0.extract            (Camelot cells rebuilt from verified words → §4 JSON)
          ├─ b1.predict            (saved VLM output → §4 JSON via convert())
          └─ hybrid.merge          (B0 values; cell_status agreed/review/b0_only/reader_only;
                                    periods filled from B1 when the router found none)
api.parse_page = hybrid.parse + checks.infer_checks/run_checks + warnings
server.py      = upload / background parse (one at a time, in-process lock) / page JSON, CSV, HTML, PNG
b1.predict     = vlm.read_page (live HTTP or demo replay) + b1.convert
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
- `tests/test_api.py` (~30 s, OCR; runs the live path against a fake VLM endpoint)

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
