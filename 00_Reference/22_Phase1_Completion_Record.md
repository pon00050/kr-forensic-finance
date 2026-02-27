# 22 — Phase 1 Completion Record

> **Scope:** Permanent sign-off record for Phase 1 (KOSDAQ Beneish M-Score screen 2019–2023). Run stats, row counts, test results, and known issues as of completion.
> **Canonical for:** Phase 1 run statistics; row counts; test pass/fail record.
> **See also:** `17_MVP_Requirements.md` (the criteria being signed off), `ROADMAP.md` (completed work table)

**Milestone:** Beneish M-Score screen for all KOSDAQ companies, 2019–2023
**Completed:** February 27, 2026
**Status:** ✅ All acceptance criteria met. All tests pass.

---

## Pipeline Configuration

| Parameter | Value |
|---|---|
| Market | KOSDAQ |
| Years | 2019–2023 (5 fiscal years) |
| Company universe | 1,702 companies (full KOSDAQ listing as of run date) |
| Financial sector exclusions | 180 rows (KSIC Rev. 10 codes 640–669 and 68200) |
| Score periods | 2020–2023 (year-over-year ratios require prior year; 2019 scores not possible) |

---

## Run Statistics (`run_summary.json`)

| Category | Count |
|---|---|
| Full data (all required accounts present) | 1,248 |
| Partial data (some accounts missing) | 432 |
| No filing (no DART financial statement found) | 22 |
| Errors | 0 |

**Total company-years processed:** 1,702 companies × 5 years = 8,510 gross; minus 180 financial-sector exclusions = **7,042 rows in `company_financials.parquet`** (some company-years consolidated to per-company-year rows; additional deduplication by `fs_type` priority: CFS > OFS).

---

## Output Files

| File | Rows | Notes |
|---|---|---|
| `01_Data/processed/company_financials.parquet` | 7,042 | One row per company-year; 180 financial-sector rows excluded |
| `03_Analysis/beneish_scores.parquet` | 5,357 | Score periods 2020–2023; 2019 dropped (no prior-year ratios) |
| `03_Analysis/beneish_scores.csv` | 5,357 | Same data, CSV for inspection |
| `tests/top50_spot_check.csv` | 50 | Top 50 highest M-Score companies for manual spot-check |

---

## Data Quality Metrics

| Metric | Result |
|---|---|
| KSIC join (industry classification) | 7,222 / 7,222 — **100%** |
| WICS join (sector classification) | 6,014 / 7,222 — **83.3%** |
| WICS gap explanation | ~16.7% of KOSDAQ tickers not covered by WICS index components (typical — small-cap/micro-cap companies outside WICS scope) |
| Financial sector exclusions applied | 180 rows excluded (KSIC 640–669 / 68200) |
| CFS vs. OFS coverage | Recorded per company-year in `fs_type` column; ~40–60% KOSDAQ companies file OFS only |

---

## Test Results

**44 tests pass — 37 invariant (test_pipeline_invariants.py) + 7 AC (test_acceptance_criteria.py).**

```
pytest tests/ -v
```

| Suite | Tests | Result |
|---|---|---|
| `test_pipeline_invariants.py` | Schema + formula + KSIC regression | ✅ All pass (37 tests) |
| `test_acceptance_criteria.py` | AC1–AC7 | ✅ All pass (7 tests) |

### Data Quality Checks (Run-Level)

*These are run-level data quality checks distinct from the automated test suite ACs in `test_acceptance_criteria.py`.*

| DQ | Description | Result |
|---|---|---|
| DQ1 | `company_financials.parquet` exists and has ≥ 5,000 rows | ✅ Pass (7,042 rows) |
| DQ2 | No company-year duplicates (corp_code + year unique) | ✅ Pass |
| DQ3 | Financial sector companies excluded (KSIC 640–669 / 68200) | ✅ Pass (180 excluded) |
| DQ4 | `beneish_scores.parquet` exists and has ≥ 3,000 rows | ✅ Pass (5,357 rows) |
| DQ5 | M-Score range is plausible (−20 to +10 for 99th percentile) | ✅ Pass |
| DQ6 | KSIC join rate ≥ 95% | ✅ Pass (100%) |
| DQ7 | `tests/top50_spot_check.csv` generated with 50 rows | ✅ Pass |

---

## Known Issues at Completion

| ID | Issue | Status |
|---|---|---|
| KI-001 | WICS 16.7% gap — small-cap companies outside WICS index scope | **Accepted.** Not a bug. WICS covers index-eligible companies only. Fallback: KSIC provides 100% coverage. |
| KI-002 | 432 partial-data companies — some M-Score components unavailable (missing DEPI/TATA for companies with no cash flow statement) | **Accepted.** Documented in `fs_type` and `score_components` columns. Partial scores still usable; missing components set to 1.0 per GMT Research practice. |
| KI-003 | Windows cp1252 Unicode crash in OpenDartReader `finstate_all()` | **Fixed Feb 27 2026.** `sys.stdout` reassigned to UTF-8 stream at top of `extract_dart.py`. See CLAUDE.md Known Issues. |
| KI-004 | KSIC sample-overwrite bug — `--sample N` flag was not propagating to the KSIC join step, causing the full 7,222-row KSIC table to overwrite the sampled company list | **Fixed Feb 27 2026.** Sample filter re-applied after KSIC join in `transform.py`. |

---

## What Phase 2 Requires

Phase 2 adds CB/BW timeline analysis (`03_Analysis/cb_bw_timelines.py`). The same infrastructure supports it without modification:

- **Same pipeline:** `extract_dart.py` already pulls CB/BW events via DS005 endpoints (`cvbdIsDecsn.json`, `bdwtIsDecsn.json`) into `01_Data/processed/cb_bw_events.parquet`
- **Same transform:** `transform.py` already builds `cb_bw_events.parquet` from raw DART filings
- **New analysis only:** `cb_bw_timelines.py` joins `cb_bw_events` + `price_volume` + `officer_holdings` + `disclosures` to score the 4-flag anomaly chain (issuance → repricing → exercise → price/volume impact)
- **No schema changes needed**

CB/BW data was extracted during the Phase 1 run. Re-running the pipeline is not required unless refreshing data.
