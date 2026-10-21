# Tabula Rasa: report

**Task.** Turn each page of a Japanese financial filing PDF into a correct table, define "correct", and measure it.

**Answer.** A hybrid parser. Each value is read twice: once from the PDF's verified text layer (B0, CPU, rules) and once from the page image by a VLM (B1, Qwen3-VL-8B, zero-shot). The two readings are compared cell by cell. Results on 85 eval pages (3,811 numeric cells, all checked against the filer's own XBRL):

| | Hybrid | PRD target |
|---|---|---|
| Cell accuracy (M1) | **0.999** | ≥ 0.995 ✔ |
| Sign accuracy | **1.000** | ≥ 0.999 ✔ |
| Page exact (M2) | **0.94** (80/85) | ≥ 0.90 ✔ |
| Hierarchy (M4) | 0.975 | ≥ 0.98 ✘ (close) |
| Label CER (M3) | 0.07% | ≤ 1% ✔ |
| Cells both readers agree on | 92% of cells, **100% correct** (3,521/3,521) | calibrated abstention |
| Hidden-text / injection attacks | caught 11/11 fixtures; 3 public scanners each missed ≥ 1 | 100% ✔ |
| Cost | 18 s CPU + ~300 s Kaggle T4 per page | ≤ $0.01/page ✘ (VLM speed) |

Run it: `.venv/bin/python -m tabula FILE.pdf` (see [README](README.md)). Design and research: [docs/PRD.md](docs/PRD.md).

---

## 1. What "correct" means

A page is correct when **every number lands in the right row and column, with the right sign and scale**. A wrong number is a financial error; a slightly different label is not. So:

- **M1 cell accuracy** (headline): the normalized integer in each (row path, period column) slot equals gold. Rows are matched to gold by label, path and position (min-cost assignment), **never by value**, so a wrong number can't find a friendlier row. Columns are matched by period end date.
- **M2 page exact**: every cell right, no extra value rows, unit right, every column's period right.
- **Sign, unit, period** broken out, since these are the errors that matter most in finance: △ dropped, 千円 vs 百万円, prior vs current year swapped.
- **M6 arithmetic**: subtotals must foot within the filing's rounding rule. Needs no labels. Totals don't foot exactly: 切り捨て (truncation) leaves `total − Σitems` in `0..n−1`. Found on the sample page; PRD §1.2.
- Label CER, hierarchy (path) accuracy, TEDS (comparable with OmniDocBench) are reported but not headline.

Output contract: one JSON per page with statement type, scope (連結/個別), standard, 訂正前/後, unit, rounding, typed period columns, and rows with path, level, row type (item/contra/net/subtotal/total), raw string and parsed value (PRD §4). CSV is derived from it.

## 2. Eval set: 85 pages, XBRL-checked

| Source | Pages | Cells |
|---|---|---|
| Sample page (hand-labelled) | 1 | 74 |
| TDnet 決算短信, 22 filings: J-GAAP 連結/非連結/四半期, IFRS, REIT, 訂正 | 52 | 2,287 |
| EDINET 有価証券報告書, 9 filings: 連結/個別 BS and PL, IFRS, 訂正 | 26 | 1,159 |
| Synthetic scans of TDnet pages (rotated, blurred, noisy, no text layer) | 6 | 291 |

**How gold is made ("silver gold").** B0 drafts the row structure. Then **every numeric cell must equal an XBRL fact** from the same filing (value × unit, same period, scope dimension allowed). Sum checks come from the filer's XBRL calculation linkbase. A page only counts when all its cells match XBRL and all its sums foot. Otherwise it goes to a review queue: 3 pages are there now, plus 1 excluded.

**Caveat, stated plainly.**
- Values are independently verified by XBRL. Row structure is B0's.
- So **B0's own scores on born-digital pages are optimistic.** Pages where B0 got a value wrong never entered gold. B0's failure rate shows up as the review queue (4 of 88 candidate pages), not as its score.
- **B1's scores are clean:** it never saw the text layer or the gold builder.

## 3. Results

### 3.1 Comparison (`python -m tabula.run_eval b0 b1 hybrid`)

| Approach | Pages | Cells | Sign | Page exact | Hierarchy | TEDS | M6 pass | Time/page | Where |
|---|---|---|---|---|---|---|---|---|---|
| B0: text layer + Camelot + rules | 85 | 0.929 | 1.000 | 0.47 | 0.929 | 0.93 | 0.91 | 18 s | CPU |
| B1: Qwen3-VL-8B 4-bit (Unsloth), zero-shot, image only | 85 | **0.995** | 0.999 | 0.44 | 0.658 | 0.74 | 0.98 | 297 s | T4 |
| **Hybrid (B0 + B1 cross-check)** | 85 | **0.999** | **1.000** | **0.94** | **0.975** | **0.99** | 0.98 | both | CPU + T4 |

### 3.2 By group (`tools/by_group.py`)

| Group | B0 cells | B1 cells | Hybrid cells | Hybrid page exact |
|---|---|---|---|---|
| TDnet J-GAAP | 1.000* | 0.999 | 1.000 | 41/41 |
| TDnet IFRS | 1.000* | 1.000 | 1.000 | 6/6 |
| TDnet REIT | 1.000* | 1.000 | 1.000 | 2/5 |
| EDINET 有報 | 1.000* | 0.991 | 1.000 | 26/26 |
| Synthetic scans | 0 (no text layer) | 0.986 | 0.986 | 4/6 |

\* optimistic by construction (§2).

### 3.3 Per-cell confidence: does "agreed" mean correct? (`tools/calibration.py`)

| Cell status | Share of cells | Accuracy |
|---|---|---|
| `agreed`: text layer and VLM read the same number | 92.1% | **1.000** (3,521/3,521) |
| `vlm_only`: scan, no text layer | 7.6% | 0.986 |
| `review`: readers disagree, or the cell held unverified text | 0.3% | B0 was right in all 13 |

This is the abstention rule:
- **Agreed cells skip human review.** Two independent readers (characters in the PDF, pixels on the page) rarely make the same mistake.
- **Disagreements go to a person.** That is 0.3% of cells.
- **Scan pages go to a person, or to the sum checks** (§5).

## 4. What went wrong, and what fixed it

**B1's errors were mostly in our adapter, not the model.** Its first score was 0.837. Each fix below was checked against the page images:

| Fix | Cells fixed | Cell accuracy after |
|---|---|---|
| Model also transcribes the 注記 (note number) column (`7`, `7,27`): value columns now need real thousands groups | ~450 | 0.973 |
| 有報 prints note marks beside values (`※1 49,533`): stripped before parsing, in the shared normalizer | 34 | |
| Header list includes the label column (`資産の部`, `注記番号`): headers are right-aligned to value columns | 58 | 0.995 |
| P/L periods `自 X 至 Y` were read as ending at X: now `duration` start X, end Y | (periods) | page exact 0.26 → 0.36 |

Remaining B1 errors after the fixes: a few **row shifts** on a noisy scan (values slide down one row), one blank subtotal, and **hierarchy** (0.66). For hierarchy, B1 nests （純額）rows under their gross row, where gold puts them beside it. That reading of the indentation is defensible, so it's a convention mismatch more than an error.

**B0's errors:**
- It can't read scans at all (no text layer).
- Its table finder takes only the first table on a page.
- It drops a column whose period it can't find. This happened on 21 gold pages, which were rebuilt.

**Hybrid page-exact misses (5/85):**
- 3 REIT pages: the prior-period column header has no date the router or VLM could parse.
- 1 scan: row shift.
- 1 scan: the VLM also read a second table on the page that gold doesn't cover.

**Bugs found in the data itself** (PRD §5.2):
- EDINET PDFs don't embed fonts, so rendering needs a CJK font. Without one, the security check rightly rejects every kanji.
- Some EDINET PDFs forbid text extraction and need `qpdf --decrypt`.
- Correction filings (訂正) are common: two companies refiled 1–5 prior years within one month.

## 5. Security: hidden text and prompt injection

PDFs are untrusted. A page can carry text no human sees but a parser or LLM reads: white text, tiny text, text under a box, off-page text, hidden layers, zero-width characters, or a fake number painted in the row-band colour over the real one.

**Defence** (`tabula/security.py`): a word is trusted only if it's actually visible. It must meet all four tests:
- font size ≥ 4 pt
- contrast ≥ 80 grey levels against its own box
- no overlap with another word
- **OCR of the rendered page reads the same text at that position** (digits exact)

Cells are built only from trusted words. A cell holding an untrusted word becomes `review`, never a value. The VLM sees only pixels, so hidden text can't reach it.

Tested on 11 poisoned copies of the sample page, against 3 open-source scanners:
- Ours caught all 11.
- All 3 scanners missed the band-coloured fake number.
- 2 of them caught English instructions only by regex, which would miss a Japanese payload or a bare number.

Full table: PRD §8.3.

## 6. The other approaches

The task asked to compare fine-tuned VLMs, ScaleDown "extract", and the JevBench models (TypeLLM, Amos, Sqwish). Status:

- **Fine-tuned VLM (Track A):** not trained. Zero-shot Qwen3-VL-8B already reaches 0.995 cells once the adapter is right. What remains is hierarchy, a convention question, and speed, so fine-tuning has little headroom on values. The research shortlist (GLM-OCR, Sarashina2.2-OCR, PaddleOCR-VL, Stockmark JapanDocReader, YomiToku) and the plan (QLoRA via Unsloth on a Kaggle T4; training data from XBRL-matched pages + synthetic renders) are in PRD §6.2. The best use of fine-tuning is a **smaller, faster model** that matches B1, to hit the cost target.
- **ScaleDown, TypeLLM, Amos, Sqwish (Tracks B/C):** not run; they need API keys. The harness takes any approach as one function `(pdf, page) -> §4 JSON`, so each is about 30 lines to add (`tabula/approaches.py`). These models must get the **rendered image or verified words only**, never the raw text layer, or hidden-text injection reaches them.

## 7. Recommendation

1. **Ship the hybrid.**
   - Born-digital filings (almost all TDnet/EDINET): B0 gives exact, free values; the VLM cross-check turns that into calibrated confidence. 92% of cells are auto-approved at 100% measured accuracy.
   - Scans: the VLM reads alone (0.986), and those pages go to review.
2. **Make the VLM cheaper:** batch with vLLM, or distil into a smaller fine-tuned model. 300 s/page on a free T4 is fine for evaluation, too slow for production. Cost is the one PRD target still missed.
3. **Use sum checks on scans (M6) as a second reader.** The repair loop is designed but not built: re-read only cells in a failing sum, and accept a change only when an independent reader confirms it (PRD §6, Track D). Scans are where this pays.
4. **Next gold work:**
   - Whole-page gold: every table on the page, not just the first.
   - Multi-page statements: assets page + liabilities page.
   - Wide 変動計算書 matrices.

## 8. Reproduce

```bash
.venv/bin/python -m tabula.run_eval b0 b1 hybrid
```

```bash
.venv/bin/python tools/by_group.py b0 b1 hybrid
```

```bash
.venv/bin/python tools/calibration.py
```

Tests: `tests/test_m0.py`, `test_m1.py` (~3 min, OCR), `test_m2.py`, `test_m3.py`, `test_hybrid.py`.

- The VLM ran on Kaggle (`kaggle/b1u/`). Its raw outputs (`runs/b1/kaggle*/`) and B0's predictions (`runs/b0/`) are committed, so the scores reproduce without a GPU or the PDFs.
- Filing PDFs and XBRL are not in git (licence and size). The gold JSON, page lists and every tool to rebuild them are.
