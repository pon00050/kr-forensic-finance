# Pipeline Improvement Areas

> **Scope:** Open backlog items (H3, M1–M3, PR1–PR5) and fixed items (C1, C2, H1–H2, L1) with investigation notes.
> **Canonical for:** Technical backlog; known pipeline limitations by ID.
> **See also:** `ROADMAP.md` (backlog summary with priorities), `22_Phase1_Completion_Record.md` (what was fixed)

*Last updated: February 2026. Based on first end-to-end test run (>50 min before abort).*

---

## Critical — Blocks Correct Test Runs

### C1: `transform.py` ignores `--sample`

**Status:** Fixed in session 19.

`build_company_financials()` loads `company_list.parquet` (all 1,723 rows) and iterates every
company regardless of how many financial parquets were extracted. With `--sample 50`, the DART
stage correctly writes 50 company parquets, but the transform stage still iterates 1,723
companies — silently skipping the 1,673 that have no parquet — and logs misleading counts.

**Fix:** Accept `--sample N` in `transform.py` CLI and `build_company_financials()`. Truncate
`company_list` to `N` before the iteration loop, matching `extract_dart`'s
`companies.head(sample)` logic.

### C2: `pipeline.py` does not propagate `--sample` to transform

**Status:** Fixed in session 19.

`run_stage_transform()` accepted only `start` and `end`; `sample` was never passed from the
pipeline CLI. So even after C1 is fixed in `transform.py`, a `pipeline.py --sample N` run
would still iterate all 1,723 companies in the transform stage.

**Fix:** Add `sample: int | None = None` parameter to `run_stage_transform()` and pass it
through from `pipeline.run()`.

---

## High — Causes Long Runs or Silent Failures

### H1: No hard timeout guard

**Status:** Fixed in session 19 (`--max-minutes`).

A stalled API call or missed rate-limit error keeps the process running indefinitely.
There is no wall-clock deadline on `fetch_all_financials`.

**Fix:** `--max-minutes N` CLI flag. Deadline computed as `monotonic() + N*60` at loop entry.
Checked before each company fetch; breaks cleanly with a warning log if exceeded.
Default: `None` (no limit) — production behavior unchanged.

### H2: No ETA in progress logs

**Status:** Fixed in session 19.

`[356/1723]` tells position but not time-to-complete. With a 0.5s sleep and 2 API calls per
company-year, a 1,723 × 5-year run takes ~2.4 hours. Without an ETA, operators cannot decide
whether to let it run or abort.

**Fix:** Rate-based ETA computed from elapsed time. Log line format:
`[i/total] name (code) | X.X c/s | ETA ~Xm Xs`

### H3: No exponential backoff on DART Error 020 (rate limit)

**Status:** ✅ Complete (Feb 2026). Implemented as `_finstate_with_backoff()` in `extract_dart.py`.

When DART returns Error 020 (rate limit exceeded), `OpenDartReader` raises an exception that
the current code catches and logs as a debug message. The company's parquet is written as
`no_filing`, which silently corrupts the resumability layer — a re-run will skip the company
because the marker file exists.

**Recommended fix:**
- Detect Error 020 in the exception message string.
- Retry with exponential backoff: 2s, 4s, 8s, 16s (4 attempts).
- Only write a `no_filing` marker if all retries are exhausted.
- Log a warning (not debug) on each retry.

---

## Medium — Correctness Risks

### M1: `run_summary.json` is always overwritten on resume

**Status:** ✅ Complete (Feb 2026). Implemented as `_merge_run_summaries()` in `pipeline.py`.

If a run is interrupted and resumed, the summary JSON reflects only the resumed batch — losing
counts from prior partial runs. `full_data` and `partial_data` lists are incomplete.

**Recommended fix:** Load existing summary on startup, merge lists, then overwrite. Or append
to a JSONL log instead of overwriting a single JSON file.

### M2: WICS snapshot uses dynamic "today" date

**Status:** Not yet fixed.

`_find_wics_snapshot_date()` probes backwards from `datetime.today()`. For a multi-day run,
each session may use a different snapshot date — introducing inconsistency. WICS membership
changes quarterly, so a March run and an April run will produce different sector assignments.

**Recommended fix:** Pin snapshot date to the last trading day of `end_year` (e.g., 20231229
for `--end 2023`). Note: as of Feb 2026, WICS only returns data for recent dates (not
historical), so pinning is aspirational — document the limitation clearly.

### M3: Silent CFS→OFS shift not flagged

**Status:** ✅ Complete (Feb 2026). Implemented in `transform.py` as `fs_type_shift` boolean column (note: column named `fs_type_shift`, not `_fs_type_changed` as suggested here).

`fs_type` is recorded per company-year, but `transform.py` does not validate that a company
uses the same `fs_type` across all years. A company that filed CFS in 2019–2021 and switched
to OFS in 2022 will have mixed consolidation bases in the Beneish calculation, producing
meaningless ratios.

**Recommended fix:** In `build_company_financials()`, after collecting all rows for a company,
check that `fs_type` is consistent across years. Flag inconsistent companies in a separate
`_fs_type_changed` boolean column.

---

## Low — Developer Experience

### L1: Sleep constants are hardcoded

**Status:** Fixed in session 19 (`--sleep`).

`SLEEP_FINANCIALS = 0.5`, `SLEEP_KSIC = 0.3`, `SLEEP_WICS = 1.0` are module-level constants.
For smoke tests with `--sample 5`, these add ~5 seconds of unnecessary delay per year.

**Fix:** `--sleep SECONDS` (float) CLI flag. When provided, overrides all three sleep constants.
`--sleep 0.1` is appropriate for sample tests; `--sleep 0` only with `--sample 5` or fewer.

### L2: No `--force` flag in `transform.py`

**Status:** ✅ Complete (Feb 2026). `--force` deletes and rebuilds `company_financials.parquet`. Implemented at `transform.py:569-573` (argparse) and `transform.py:546-552` (delete logic).

`transform.py` always writes `company_financials.parquet`, but there is no explicit `--force`
flag in its CLI. Reprocessing requires manually deleting the output file.

**Recommended fix:** Add `--force` flag; skip writing if output exists and `--force` is absent
(or always overwrite — current behavior — and document it).

### L3: No tqdm progress bar

**Status:** Not yet fixed.

Log lines at INFO level work but are noisy in notebooks and hard to parse visually.
`tqdm` is already in many data-science environments.

**Recommended fix:** Optional tqdm wrapper in `fetch_all_financials` and `fetch_ksic`. Guard
with `try: from tqdm import tqdm` so the fallback is the existing log-based loop.

---

## Data Quality and Provenance (Added Feb 27, 2026)

### DQ1: No data lineage column (`match_method_*`)

**Status:** Not yet implemented.

`transform.py` applies a priority-order extraction chain for each financial variable (exact XBRL element ID → secondary element ID → Korean substring fallback). The actual path taken is not recorded in the output parquet — only the final extracted value.

**Why it matters:** Litigation support and academic researchers need to know whether a given receivables figure came from a confirmed `dart_ShortTermTradeReceivable` match (low uncertainty) or a Korean `매출채권` substring fallback (higher uncertainty, possible mis-match). Without this column, every downstream consumer must assume worst-case precision.

**Recommended fix:** In `_extract_one_variable()` (or equivalent extraction function), return a tuple `(value, match_method)` where `match_method` is `"exact_id"`, `"korean_substring"`, or `None`. Write per-variable `match_method_receivables`, `match_method_revenue`, etc. columns alongside the value columns. No schema changes to downstream analysis scripts needed — these are additional columns, not replacements.

**Estimated effort:** Medium. Requires modifying the extraction inner loop in `transform.py` and the output schema. No pipeline re-run needed for the column structure, but populating historical data requires re-running the transform stage.

**Spec:** `00_Reference/17_MVP_Requirements.md` §9 PR1.

---

### DQ2: No extraction timestamp

**Status:** ✅ Complete (Feb 2026). `extraction_date` column present in both output parquets. Implemented at `transform.py:471` and `beneish_screen.py:277`.

Neither `company_financials.parquet` nor `beneish_scores.parquet` contains a timestamp recording when the data was extracted. A file written in February 2026 and re-read in 2027 has no internal record of its vintage.

**Why it matters:** AML/CFT compliance documentation requires data currency records. Expert reports in litigation require a citable extraction date. Academic datasets require provenance metadata. Commercial data products require explicit data-as-of dates.

**Recommended fix:** In `transform.py`, at the point of writing the parquet, add a constant column `extraction_date = datetime.today().date().isoformat()`. Carry through in `beneish_screen.py` as a column on `beneish_scores.parquet`. Add test assertion that the column is present and not null.

**Estimated effort:** Low. Single line addition in each script.

**Spec:** `00_Reference/17_MVP_Requirements.md` §9 PR2.

---

## Test Invocation (After Session 19 Fixes)

```bash
# Fast smoke test: 5 companies, 2 years, minimal sleep, 3-minute hard cap
python 02_Pipeline/pipeline.py \
  --market KOSDAQ \
  --start 2022 \
  --end 2023 \
  --sample 5 \
  --sleep 0.1 \
  --max-minutes 3
```

Expected: exits in < 2 minutes; `company_financials.parquet` has ≤ 5 unique `corp_code` values.
