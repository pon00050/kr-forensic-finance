# 21 — Test Suite

Documentation for the project's automated test infrastructure. Covers design philosophy, each test file, each test category, the rationale behind each check, and when to run what.

---

## Philosophy

This project borrows its testing philosophy directly from the accounting controls mindset documented in the career development notes on programming parallels. The key principle: **you are not trying to prove the system is correct — you are trying to identify the conditions under which it produces the wrong answer.**

Three properties guide which tests are worth writing at this stage:

1. **Guards real failure modes, not obvious ones.** A test that asserts `2 + 2 == 4` has no value. A test that asserts a sample run doesn't silently destroy a previously-complete dataset has real value — because that exact failure already happened once.

2. **Fails loudly on genuine breakage, not on expected limitations.** AC2 tests that WICS coverage is ≥80%, not ≥95%, because 85% is the real ceiling of the source data. A threshold that can never be met is not a control — it's noise.

3. **Documents intent as executable specification.** The Beneish formula spot-check does not just verify the output is "reasonable." It asserts specific ratios to four decimal places against hand-calculated expected values, with the arithmetic shown in comments. Anyone reading the test understands exactly what formula is being implemented.

The acceptance criteria suite (`test_acceptance_criteria.py`) acts as a **trial balance** — a final reconciliation run after all the work is done, asserting that the system as a whole produced coherent output. The invariants suite (`test_pipeline_invariants.py`) acts as **internal controls** — unit-level guards that run independently of data and catch regressions before they propagate.

---

## Test Files

```
tests/
├── conftest.py                    pytest configuration — adds 02_Pipeline/ to sys.path
├── test_pipeline_invariants.py    Self-contained unit tests (no pipeline data needed)
├── test_acceptance_criteria.py    Integration tests (requires pipeline output on disk or R2)
└── top50_spot_check.csv           Generated artifact — top 50 most anomalous companies
```

---

## `test_pipeline_invariants.py`

**Can run at any time — no pipeline data required (except Categories 10–11 schema fixtures).**

Seven test categories, each targeting a distinct failure mode. Categories 1–3 are original Phase 1 guards; Categories 10–13 were added in Session 2 (Mar 2, 2026).

---

### Category 1 — KSIC Sample Preservation (`TestKsicSamplePreservation`)

**What it guards:**
A `--sample N` pipeline run must not destroy the full `ksic.parquet` that a prior complete run built.

**Why it exists:**
On February 27, 2026, a smoke test (`--sample 5`) run after the full pipeline completed reduced `ksic.parquet` from 1,702 rows to 5 rows. The root cause was in `fetch_ksic()`: the function loaded existing entries into memory for deduplication but only wrote the N-entry `rows` list back to disk, silently discarding the rest. The subsequent full transform showed "KSIC join: 16/7,222 rows" — anomalously low — which surfaced the bug. See `KNOWN_ISSUES.md` KI-004.

**How it works:**
Uses pytest's `tmp_path` fixture to create a temporary directory that mimics the `01_Data/raw/sector/` structure. `monkeypatch` redirects `extract_dart.RAW_SECTOR` to the temp directory and stubs `dart.company()` to return a fake response. This isolates the test completely from real data and real API calls.

**What the tests assert:**

| Test | Given | When | Then |
|---|---|---|---|
| `test_sample_run_preserves_existing_entries` | `ksic.parquet` has 10 entries | `fetch_ksic()` called with `sample=3` | Written parquet still has 10 rows; all corp_codes preserved |
| `test_full_run_writes_all_companies` | No existing `ksic.parquet` | `fetch_ksic()` called with `sample=None`, 5 companies | Written parquet has exactly 5 rows |

---

### Category 2 — Schema Contracts (`TestSchemaContracts`)

**What it guards:**
`company_financials.parquet` must always have the expected columns, correct numeric dtypes, no financial sector rows, and KOSDAQ-only market values.

**Why it exists:**
Schema drift is the most common source of silent downstream failures in data pipelines. If a column is renamed, dropped, or changes dtype during a refactor of `transform.py`, the Beneish calculation in `beneish_screen.py` will silently produce wrong results (e.g. treating a string column as NaN instead of a number). These tests catch that class of breakage at the contract boundary between the pipeline and the analysis layer.

**Prerequisites:**
Requires `01_Data/processed/company_financials.parquet` to exist. If the file is not present, all tests in this class are **skipped** (not failed) — a fresh clone or a pre-pipeline environment will not produce a misleading red.

**What the tests assert:**

| Test | Assertion |
|---|---|
| `test_required_columns_present` | All 19 expected columns exist |
| `test_numeric_columns_are_float` | The 10 financial figure columns are `float64` |
| `test_year_is_integer` | `year` column is an integer dtype |
| `test_no_financial_sector_rows` | Zero rows with KSIC 640–669 or 68200 (financial sector exclusion applied correctly) |
| `test_no_empty_corp_code` | No null `corp_code` values |
| `test_market_column_values` | All rows are KOSDAQ (Phase 1 scope guard) |

---

### Category 3 — Beneish Formula Spot-Check (`TestBeneishFormula`)

**What it guards:**
The 8-variable Beneish M-Score formula is implemented with the correct coefficients, signs, and neutral-value substitutions.

**Why it exists:**
The formula coefficients are easy to accidentally change during a refactor — a sign flip on SGAI or a wrong coefficient on TATA would produce systematically wrong M-Scores across the entire output with no obvious visible signal. This test documents the formula as executable specification: here are the inputs, here is the arithmetic, here is the expected output. If the formula changes, the test fails loudly.

**How it works:**
Constructs a synthetic two-year DataFrame for one fictitious company entirely in memory — no files, no API, no fixtures. The input values are chosen to produce round, hand-calculable ratios. The expected values are shown with full arithmetic in comments so the test is self-documenting.

**Synthetic input values (KRW millions):**

| Field | T-1 (2019) | T (2020) |
|---|---|---|
| revenue | 1,000 | 1,200 |
| receivables | 100 | 130 |
| cogs | 600 | 700 |
| ppe | 400 | 420 |
| depreciation | 50 | 55 |
| total_assets | 1,500 | 1,700 |
| lt_debt | 200 | 260 |
| sga | 80 | 110 |
| net_income | — | 100 |
| cfo | — | 80 |

**Expected ratios and derivations:**

| Ratio | Formula | Expected |
|---|---|---|
| DSRI | (130/1200) / (100/1000) | 1.0833 |
| GMI | (1−600/1000) / (1−700/1200) = 0.40 / 0.4167 | 0.9600 |
| AQI | (1280/1700) / (1100/1500) = 0.7529 / 0.7333 | 1.0267 |
| SGI | 1200 / 1000 | 1.2000 |
| DEPI | (50/450) / (55/475) = 0.1111 / 0.1158 | 0.9596 |
| SGAI | (110/1200) / (80/1000) = 0.0917 / 0.0800 | 1.1458 |
| LVGI | (260/1700) / (200/1500) = 0.1529 / 0.1333 | 1.1471 |
| TATA | (100−80) / 1700 | 0.01176 |
| **M-Score** | −4.84 + 0.920×1.0833 + 0.528×0.9600 + 0.404×1.0267 + 0.892×1.2000 + 0.115×0.9596 − 0.172×1.1458 + 4.679×0.01176 − 0.327×1.1471 | **−2.26** |

**What the tests assert:**

| Test | Assertion |
|---|---|
| `test_m_score_known_input` | Each of the 8 ratios matches expected to ±0.001; M-Score matches −2.26 to ±0.02 |
| `test_nature_method_sets_gmi_sgai_to_one` | With `expense_method='nature'`, GMI=1.0 and SGAI=1.0 |
| `test_first_year_has_no_m_score` | Year T-1 row (no lag data available) has null `m_score` |

---

---

### Category 10 — WICS Snapshot Date Pinning (`TestWicsSnapshotDate`)

**What it guards:** `_last_trading_day_of_year(year)` returns a valid YYYYMMDD string and
`fetch_wics(year=N)` pins the snapshot to that date.

**Tests:** `test_last_trading_day_of_year_returns_yyyymmdd`,
`test_last_trading_day_of_year_fallback`, `test_fetch_wics_records_snapshot_date_in_parquet`.
All mock `requests.get` — no network required.

---

### Category 11 — match_method Lineage (`TestMatchMethodLineage`)

**What it guards:** `company_financials.parquet` carries `match_method_*` columns with values
`exact_id` | `korean_substring` | null. `_extract_field()` returns a `(value, method)` tuple.

**Tests:** `test_match_method_columns_exist` (requires parquet on disk),
`test_match_method_values_valid` (requires parquet), `test_extract_field_returns_tuple`
(pure unit test — no data needed).

---

### Category 12 — CB/BW Schema Contracts (`TestCbBwSchema`)

**What it guards:** `cb_bw_events.parquet`, `price_volume.parquet`, `officer_holdings.parquet`
have required columns, parseable dates, no duplicate events. Parse-logic unit tests verify
`_parse_dart_response()` handles DART status 013 (no data) and valid responses.

**Skip behavior:** All fixture-based tests skip (not fail) when Phase 2 parquets don't exist —
7 tests currently skipping. Parse unit tests run without data.

---

### Category 13 — Sector Percentile Market Isolation (`TestSectorPercentileMarket`)

**What it guards:** After PR4, `sector_percentile` is computed within `(sector, year, market)`
groups — KOSDAQ companies are not ranked against KOSPI peers.

**Test:** `test_sector_percentile_isolates_market` — pure in-memory; constructs synthetic
mixed-market DataFrame and asserts KOSDAQ/KOSPI percentiles are computed independently.

---

## `test_acceptance_criteria.py`

**Requires pipeline output on disk (or R2 credentials). Run after the full pipeline and `beneish_screen.py`.**

This is the end-to-end reconciliation suite. Each test maps to one acceptance criterion from `00_Reference/17_MVP_Requirements.md`. Together they assert that the full pipeline output meets the Phase 1 release bar.

If processed files are not present and R2 credentials are not configured, tests are **skipped** rather than failed.

**Side effect:** Generates `tests/top50_spot_check.csv` — the 50 most anomalous companies by M-Score, for manual spot-checking. Written as a `module`-scoped autouse fixture so it runs once per session regardless of which individual tests are invoked.

**What the tests assert:**

| Test | Criterion | Threshold | Rationale for threshold |
|---|---|---|---|
| `test_ac1_coverage` | ≥80% of companies have ≥3 scoreable years | 80% | Guards against systematic DART extraction failure |
| `test_ac2_sector_enrichment` | ≥80% of rows have `wics_sector_code` | 80% | WICS source ceiling is ~85%; see `KNOWN_ISSUES.md` KI-001 |
| `test_ac3_score_computability` | ≥70% of company-years have non-null `m_score` | 70% | Guards against systematic lag/join failure |
| `test_ac4_financial_exclusion` | Zero financial-sector rows in `company_financials` | 0 | Hard requirement — financial cos structurally inflate M-Score |
| `test_ac5_market_purity` | Zero KOSPI tickers in output | 0 | Phase 1 is KOSDAQ only. Now parametrized via `PIPELINE_MARKET` env var (default: `KOSDAQ`); when `PIPELINE_MARKET=KOSPI`, asserts no KOSDAQ rows instead. |
| `test_ac6_expense_method` | `expense_method` 100% populated; nature rows have GMI=SGAI=1.0 | 100% / exact | Guards neutral-substitution logic for nature-method companies |
| `test_ac7_reproducibility` | `beneish_scores.parquet` exists; md5 recorded | file exists | Presence is a proxy; true reproducibility requires a manual re-run |

**Note on AC4:** AC4 in `test_acceptance_criteria.py` checks `company_financials.parquet` (which carries `ksic_code`), not `beneish_scores.parquet` (which does not). This is an improvement over the original `check_acceptance_criteria.py`, which soft-passed because it looked in the wrong file. See `KNOWN_ISSUES.md` KI-003.

---

## Run Commands

```bash
# Self-contained invariants — run any time
pytest tests/test_pipeline_invariants.py -v

# Acceptance criteria — run after full pipeline + beneish_screen.py
pytest tests/test_acceptance_criteria.py -v

# Full suite
pytest tests/ -v

# Stop at first failure (useful during debugging)
pytest tests/ -v -x
```

---

## When to Run Each Suite

| Situation | Run |
|---|---|
| After any change to `extract_dart.py` | `test_pipeline_invariants.py` — KSIC category |
| After any change to `transform.py` | `test_pipeline_invariants.py` — Schema category |
| After any change to Beneish formula logic | `test_pipeline_invariants.py` — Formula category |
| After a full pipeline run | `test_acceptance_criteria.py` |
| Before a GitHub release | Both suites in full |
| On a fresh clone (no data) | `test_pipeline_invariants.py` only — AC suite will skip gracefully |

---

## Adding New Tests

When adding a new test, place it in the file that matches its scope:

- **Self-contained, no data dependency** → `test_pipeline_invariants.py`
- **End-to-end, requires pipeline output** → `test_acceptance_criteria.py`
- **New pipeline stage (Phase 2+)** → create `tests/test_<stage>_invariants.py` following the same three-category pattern

> **Note:** The "new pipeline stage → new test file" guideline applies when a stage is fully
> implemented and producing real output. For scaffold-phase tests (schema contracts on
> parquets not yet produced), adding to `test_pipeline_invariants.py` with `pytest.skip`
> guards is acceptable. Create `tests/test_cb_bw_invariants.py` when Phase 2 is complete.

Follow the existing naming convention: `TestKsicSamplePreservation`, `TestSchemaContracts`, `TestBeneishFormula` — class per logical group, `test_` prefix per assertion. Include the expected value derivation in comments for any numeric assertion that isn't self-evident.
