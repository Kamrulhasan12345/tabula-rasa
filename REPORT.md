# Tabula Rasa: report

**Task.** Turn each page of a Japanese financial filing PDF into a correct table, define "correct", and measure it.

**Answer.** A hybrid parser. Each value is read twice:
- once from the PDF's verified text layer (B0: CPU, rules)
- once from the page image by a second reader, by default **TypeLLM**, a hosted typed-extraction API

The two readings are compared cell by cell. Results on 85 eval pages (**4,619 numeric cells, whole-page gold**, every value checked against the filer's own XBRL):

| | Hybrid (B0 + TypeLLM) | PRD target |
|---|---|---|
| Cell accuracy (M1) | **1.000** (4,619/4,619) | ≥ 0.995 ✔ |
| Sign accuracy | **1.000** | ≥ 0.999 ✔ |
| Page exact (M2) | **0.93** (79/85; all 6 misses are extra rows where gold is still partial) | ≥ 0.90 ✔ |
| Hierarchy (M4) | **0.987** | ≥ 0.98 ✔ |
| Label CER (M3) | 0.06% | ≤ 1% ✔ |
| Cells both readers agree on | 92% of cells, **100% correct** (4,266/4,266) | calibrated abstention |
| Hidden-text / injection attacks | caught 11/11 fixtures; 3 public scanners each missed ≥ 1 | 100% ✔ |
| Cost | ≈ $0.0001/page (TypeLLM) + 18 s CPU | ≤ $0.01/page ✔ |
| Latency | ~45 s/page (TypeLLM ~44 s, B0 runs alongside) | p95 ≤ 10 s ✘ |

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
| TDnet 決算短信, 22 filings: J-GAAP 連結/非連結/四半期, IFRS, REIT, 訂正 | 52 | 2,933 |
| EDINET 有価証券報告書, 9 filings: 連結/個別 BS and PL, IFRS, 訂正 | 26 | 1,275 |
| Synthetic scans of TDnet pages (rotated, blurred, noisy, no text layer) | 6 | 337 |

**Whole-page gold.**
- The first gold covered only the first table Camelot found. On 25 pages that was a fraction of the page: 548219 p9 had 5 of its 42 P/L rows.
- B0 now reads every table on a page. Those pages were rebuilt and checked against their page images.
- 20 rebuilt cleanly. 5 failed a sum check and keep their earlier partial gold.

**How gold is made ("silver gold").** B0 drafts the row structure. Then **every numeric cell must equal an XBRL fact** from the same filing (value × unit, same period, scope dimension allowed). Sum checks come from the filer's XBRL calculation linkbase. A page only counts when all its cells match XBRL and all its sums foot. Otherwise it goes to a review queue: 3 pages are there now, plus 1 excluded.

**Caveat, stated plainly.**
- Values are independently verified by XBRL. Row structure is B0's.
- So **B0's own scores on born-digital pages are optimistic.** Pages where B0 got a value wrong never entered gold. B0's failure rate shows up as the review queue (4 of 88 candidate pages), not as its score.
- **B1's scores are clean:** it never saw the text layer or the gold builder.

## 3. Results

### 3.1 Comparison (`python -m tabula.run_eval b0 b1 typellm scaledown hybrid hybrid_typellm`)

| Approach | Cells | Sign | Page exact | Hierarchy | TEDS | M6 pass | Time/page | Cost/page | Where |
|---|---|---|---|---|---|---|---|---|---|
| B0: text layer + Camelot + rules | 0.927* | 1.000 | 0.44 | 0.929 | 0.89 | 0.92 | 20 s | 0 | CPU |
| B1: Qwen3-VL-8B 4-bit (Unsloth), zero-shot, image only | 0.995 | 1.000 | 0.45 | 0.594 | 0.86 | 0.98 | 297 s | GPU | Kaggle T4 |
| **TypeLLM** (Track C), typed arrays from the page image | **0.996** | 0.9997 | 0.51 | 0.824 | 0.86 | **0.998** | 44 s | ≈ $0.0001 | API |
| ScaleDown Extract (Track B), OCR then extract | 0.720 | 0.988 | 0.28 | 0.378 | 0.68 | 0.71 | 35 s | free tier | API |
| Hybrid B0 + B1 | 0.999 | 1.000 | 0.88 | 0.972 | 0.96 | 0.99 | both | GPU | CPU + T4 |
| **Hybrid B0 + TypeLLM** | **1.000** | **1.000** | **0.93** | **0.987** | **0.96** | 0.99 | ~45 s | ≈ $0.0001 | CPU + API |

- \* B0 is optimistic by construction (§2) and scores 0 on scans.
- TypeLLM cost is estimated: ~2.5k input tokens per page image at $0.05/M; typed output isn't billed.
- Every API reply is cached in `runs/<approach>/raw/`, so rescoring is free.

### 3.2 By group (`tools/by_group.py`), cells / page exact

| Group | B0 | B1 | TypeLLM | ScaleDown | Hybrid B0+TypeLLM |
|---|---|---|---|---|---|
| TDnet J-GAAP (2,297) | 1.000* | 0.997 | 0.998 | 0.704 | 1.000 · 38/41 |
| TDnet IFRS (285) | 1.000* | 1.000 | 1.000 | 0.688 | 1.000 · 6/6 |
| TDnet REIT (351) | 1.000* | 1.000 | 1.000 | 0.521 | 1.000 · 5/5 |
| EDINET 有報 (1,275) | 1.000* | 0.991 | 0.991 | 0.765 | 1.000 · 24/26 |
| Synthetic scans (337) | 0 | 0.988 | **1.000** | 0.487 | 1.000 · 5/6 |

### 3.3 Per-cell confidence: does "agreed" mean correct? (`tools/calibration.py hybrid_typellm`)

| Cell status | Share of cells | Accuracy |
|---|---|---|
| `agreed`: text layer and image reader read the same number | 92.4% | **1.000** (4,266/4,266) |
| `reader_only`: scan, no text layer | 7.3% | 1.000 (337/337) |
| `review`: readers disagree, or the cell held unverified text | 0.3% | B0 was right in all 16 |

With B1 as the second reader the picture is the same: agreed cells 4,277/4,277, `reader_only` 0.988.

This is the abstention rule:
- **Agreed cells skip human review.** Two independent readers (characters in the PDF, pixels on the page) rarely make the same mistake.
- **Disagreements go to a person.** That is 0.3% of cells.
- **Scan pages go to a person, or are checked by sums** (§3.4).

### 3.4 Checks that need no labels

On every page, sums are checked from the table's own structure:
- 合計 = its rows, and 小計 = the rows above it.
- J-GAAP profit lines: 売上総利益, 営業利益, 経常利益, 税引前 and 純利益.

Tested on gold values, where every check should pass:
- 478 of 488 pass, a 2% false-alarm rate.
- Profit lines pass 122 of 122.
- The misses come from a cost-of-goods schedule with subtracted rows, and from pages where gold lacks rows.

Statements split across pages are joined, and 資産合計 = 負債純資産合計 is checked exactly. A failed check sends the page to review; it never changes a value.

## 4. What went wrong, and what fixed it

**B1's errors were mostly in our adapter, not the model.** Its first score was 0.837. Each fix below was checked against the page images:

| Fix | Cells fixed | Cell accuracy after |
|---|---|---|
| Model also transcribes the 注記 (note number) column (`7`, `7,27`): value columns now need real thousands groups | ~450 | 0.973 |
| 有報 prints note marks beside values (`※1 49,533`): stripped before parsing, in the shared normalizer | 34 | |
| Header list includes the label column (`資産の部`, `注記番号`): headers are right-aligned to value columns | 58 | 0.995 |
| P/L periods `自 X 至 Y` were read as ending at X: now `duration` start X, end Y | (periods) | page exact 0.26 → 0.36 |

Two more fixes came from the API readers. ScaleDown writes periods as `X - Y`, so any header with two dates is now a duration ending at Y. TypeLLM rejects arrays nested inside objects, so its row values come back as one `" | "`-joined string.

Remaining B1 errors after the fixes: a few **row shifts** on a noisy scan (values slide down one row), one blank subtotal, and **hierarchy** (0.66). For hierarchy, B1 nests （純額）rows under their gross row, where gold puts them beside it. That reading of the indentation is defensible, so it's a convention mismatch more than an error.

**ScaleDown's errors are structural, not misread digits:**

| Error | Cells |
|---|---|
| Whole rows missing | 678 |
| Columns missing (pages returned with every value empty) | 505 |
| Wrong values | 190 |

Its docs say it reads the text first and then extracts. That suits key fields better than dense tables.

**TypeLLM's errors**, 16 in 4,619 cells:

| Error | Cells |
|---|---|
| Unparseable cell | 10 |
| Blank | 4 |
| Sign | 1 |
| Wrong value | 1 |

**B0's errors:**
- It can't read scans (no text layer).
- It dropped a column whose period it couldn't find (21 gold pages, rebuilt).
- Camelot can return overlapping tables over the same rows. Reading every table then doubled a page, which the page image showed (553462 p11: one table, 38 rows, not 76). Each word now counts once.

**Hybrid (B0 + TypeLLM) page-exact misses (6/85):** every cell is correct on all 6.
- 5 are extra rows, because their gold is still partial.
- 1 is one extra row on a scan.

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

The task asked to compare fine-tuned VLMs, ScaleDown "extract", and the JevBench-family models (TypeLLM, Amos, Sqwish). Adapters were built from each vendor's docs (`tabula/remote.py`). Every one gets the **rendered page image or verified words only**, never the raw text layer, so hidden-text injection can't reach it.

| Service | What it is | Result here |
|---|---|---|
| **TypeLLM** | Typed constrained decoding; arrays of objects from an image | **Best single reader**: 0.996 cells, 0.824 hierarchy, scans 1.000, 44 s, ≈ $0.0001/page. Default second reader |
| ScaleDown Extract | OCR then description-driven extraction | 0.720: drops rows and whole columns on dense statements |
| Amos 1 | Yes/no, span, number and box questions with probabilities | Adapter only (`TABULA_AMOS=1`): checks disputed cells against the image. Docs say English-only, so not run on Japanese |
| Sqwish Kettei | Classifier with abstention | Adapter only: row-type classifier. Needs a key (possible waitlist) |
| Fine-tuned VLM (Track A) | Qwen3-VL-8B via Unsloth on Kaggle T4 | Not trained. Zero-shot already reaches 0.995 cells, and TypeLLM beats it on speed and cost. Plan and shortlist: PRD §6.2 |

## 7. Recommendation

1. **Ship the hybrid with TypeLLM as the second reader.**
   - Every PRD accuracy target is met: 1.000 cells, 0.93 page exact, 0.987 hierarchy.
   - Cost is about $0.0001 per page, and no GPU is needed.
   - 92% of cells are auto-approved at 100% measured accuracy; 0.3% go to a person.
2. **Keep B1 (VLM) as the offline option** for filings that can't leave the building (PRD §9 privacy). It is pluggable through `TABULA_VLM_URL`, any OpenAI-compatible endpoint such as `vllm serve`.
3. **Latency is the one target still missed** (~45 s against 10 s).
   - Pages are independent, so running TypeLLM calls in parallel cuts wall time per document.
   - The 18 s security OCR can run on the table region only.
4. **Next:**
   - The repair loop: re-read cells in a failing sum and accept only a confirmed value (PRD §6, Track D).
   - Gold for the 5 pages still partial.
   - Wide 変動計算書 matrices.

## 8. Reproduce

```bash
.venv/bin/python -m tabula.run_eval b1 typellm scaledown hybrid_typellm
```

```bash
.venv/bin/python tools/by_group.py b0 b1 typellm scaledown hybrid hybrid_typellm
```

```bash
.venv/bin/python tools/calibration.py hybrid_typellm
```

Tests:
- `tests/test_m0.py`
- `test_m1.py` (~3 min, OCR)
- `test_m2.py`, `test_m3.py`, `test_hybrid.py`
- `test_api.py` (fake VLM endpoint)
- `test_remote.py` (fake vendor APIs; never spends credit)

- The VLM ran on Kaggle (`kaggle/b1u/`).
- These are committed, so the scores reproduce without a GPU, API keys or the PDFs:
  - the VLM's raw outputs (`runs/b1/kaggle*/`)
  - the TypeLLM and ScaleDown replies (`runs/*/raw/`)
  - B0's predictions (`runs/b0/`)
- Filing PDFs and XBRL are not in git (licence and size). The gold JSON, page lists and every tool to rebuild them are.
