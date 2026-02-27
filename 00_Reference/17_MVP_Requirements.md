# MVP Requirements — Korean Financial Anomaly Pipeline

> **Scope:** Phase definitions (1–3), AC1–AC7 acceptance criteria, schema contracts, and column-level provenance for all pipeline outputs.
> **Canonical for:** Acceptance criteria; phase scope boundaries; column definitions.
> **Prerequisites:** `04_Technical_Architecture.md`
> **See also:** `22_Phase1_Completion_Record.md` (Phase 1 sign-off against these criteria)

*Drafted February 2026. Verified against live DART API, KSIC reference data, WICS API, and a 200-company KOSDAQ empirical sample. All technical assumptions are confirmed unless noted otherwise. Phase 1 is the initial GitHub release. Phases 2 and 3 are future milestones included here for architectural continuity, not immediate implementation.*

> **Marimo interactive UI deferred.** References in this document to `marimo run`, `marimo edit`, and interactive notebook acceptance criteria are not current targets. Analysis scripts run as plain Python (`python 03_Analysis/beneish_screen.py`). Marimo-specific acceptance criteria (criterion 8 in Phase 1) are skipped until further notice.

---

## 1. What "Done" Looks Like

Phase 1 is one bounded deliverable: a reproducible Beneish M-Score screen for KOSDAQ companies, covering five annual periods (2019–2023). It produces one Parquet dataset and one interactive notebook. That is the full scope of the GitHub release.

The pipeline is organized so that each phase is independently useful before the next phase begins. Data requirements at each phase are determined by what real users can act on — not by a goal of maximum coverage.

**Phase 1 is the initial GitHub release.** Phases 2 and 3 are documented here for architectural continuity. They are future work.

---

## 2. User Personas

Three personas determine what "useful" means at each phase:

| Persona | Goal | Minimum useful output |
|---|---|---|
| **Investigative journalist** | Find KOSDAQ companies worth investigating against a deadline | Ranked anomaly table: company name, sector, M-Score, DART link — filtered to top 30–50 candidates |
| **Forensic analyst** | Find sector-relative outliers; separate genuine signals from sector-normal patterns | M-Score with peer-group percentile; sector filter; trend over multiple years |
| **Academic researcher** | Reproduce methodology on full market; extend to new metrics | Clean, documented Parquet dataset; column-level provenance; code that runs end-to-end with a single command |

For the academic researcher persona, Phase 1 alone must satisfy the "single command" requirement: one `pipeline.py` invocation followed by `marimo run beneish_screen.py` is the complete user journey.

Regulators are not a primary target for Phases 1–3. They have access to non-public data and will not rely on a public screen as primary evidence.

---

## 3. Phase Overview

| Phase | Scope | Primary deliverable | Persona served | Key new data sources |
|---|---|---|---|---|
| **1 — Beneish Screen** | KOSDAQ, annual 2019–2023 (5 years, 4 calculable score periods) | `beneish_scores.parquet` + interactive Marimo notebook | Journalist, analyst | DART financials via OpenDartReader; WICS/KRX sector |
| **2 — CB/BW Timelines** | Phase 1 flagged companies only (~100–400) | CB/BW issuance-to-exercise timeline; timing anomaly CSV | Analyst | KRX OHLCV via PyKRX; DART DS005 endpoints; SEIBRO scraping |
| **3 — Officer Network** | Phase 2 flagged companies only (~50) | Officer network graph + centrality report | Analyst | DART officer holdings; KFTC (large conglomerates only) |

Phase 4 (KOSPI + quarterly refresh, live monitoring) is out of scope for this document.

This document specifies Phase 1 in full detail. Sections 5 and 6 specify Phases 2 and 3 at acceptance-criteria level only.

---

## 4. Phase 1 — KOSDAQ Beneish Screen

### 4.1 Company Universe

- **Market:** KOSDAQ only
- **Listing status:** Active during 2019–2023; do not exclude companies that were later delisted (excluding them creates survivorship bias that overstates data quality)
- **Expected count:** ~1,600–1,700 active KOSDAQ companies

**Market filter implementation:** `dart.corp_codes` does not contain a `stock_market` column — it has exactly 5 columns (`corp_code`, `corp_name`, `corp_eng_name`, `stock_code`, `modify_date`). KOSDAQ filtering requires a PyKRX join:

```python
from pykrx import stock
kosdaq_tickers = set(stock.get_market_ticker_list("20231229", market="KOSDAQ"))
kosdaq_corps = dart.corp_codes[dart.corp_codes["stock_code"].isin(kosdaq_tickers)]
```

**Required exclusions** (not optional — Beneish ratios are structurally undefined for these entities):

| Category | Reason | Filter |
|---|---|---|
| Financial companies (은행, 증권, 보험, 저축은행, 지주회사, etc.) | AQI, GMI, SGAI meaningless for financial balance sheets | KSIC codes 640–669 |
| REITs (부동산투자회사) | Pass-through real estate vehicles; same structural problem as banks | KSIC code 68200 |
| SPACs (기업인수목적회사) | No operating history; all inputs undefined | Company name contains "기업인수목적" OR no financial data returned |
| Preferred share listings (우선주) | Duplicate economic entity | Include common stock class only |

Note: `지주회사` (holding companies) is KSIC code 64992 — already inside the 640–669 range. REITs are KSIC code 68200 (Section L — Real Estate), outside Section K, and require a separate exclusion line.

**On the 23% no-filing population:** Approximately 23% of KOSDAQ tickers return status 013 (no data) for a given year. This population includes SPACs, companies listed after the fiscal year-end, and a smaller number of genuine OFS-only filers. Status 013 is an expected condition, not a data error. These companies are simply absent from the output dataset.

---

### 4.2 Date Range and Statement Type

- **Analysis years:** 2019–2023 (5 annual periods)
- **Why 5 years:** Beneish requires two consecutive years per M-Score calculation; 5 years yields 4 calculable score periods per company (2019→2020, 2020→2021, 2021→2022, 2022→2023)
- **Report type:** Annual only (사업보고서, `reprt_code = 11011`); no quarterly data in Phase 1
- **Statement preference:** Consolidated (연결재무제표, `fs_div = "CFS"`) when available; fall back to separate (별도재무제표, `fs_div = "OFS"`) otherwise

**CFS/OFS filing rates (empirically verified, 200-company KOSDAQ sample, 2022):**
- 77% return CFS data on the first attempt
- 23% return status 013 — mix of SPACs, recent listings, and OFS-only filers

**Two-pass implementation required:**

```python
df = dart.finstate_all(corp_code, year, fs_div="CFS")
if df is None or df.empty:
    df = dart.finstate_all(corp_code, year, fs_div="OFS")
    fs_type = "OFS" if (df is not None and not df.empty) else "no_filing"
else:
    fs_type = "CFS"
```

**Critical:** OpenDartReader v0.2.3 does **not** automatically fall back from CFS to OFS. The `fs_div` column is also absent from `finstate_all` responses in v0.2.3 — the pipeline must record which `fs_div` was passed as a call parameter, not infer it from the response.

Record `fs_type` per company-year. Flag any company where `fs_type` switches between CFS and OFS across years in the same 5-year window — this creates ratio noise in multi-year M-Score trends.

---

### 4.3 Financial Data Requirements

Ten financial accounts per company per year are required to compute the 8 Beneish ratios. They come from two different DART endpoints:

**Group 1 — Balance Sheet + Income Statement** (available from both `finstate_all` and the batch `fnlttMultiAcnt`):

| Field | Korean label | DART account_id | Nullable |
|---|---|---|---|
| `receivables` | 매출채권 | `ifrs-full_TradeAndOtherCurrentReceivables` or `dart_ShortTermTradeReceivables` | No |
| `revenue` | 매출액 | `ifrs-full_Revenue` | No |
| `cogs` | 매출원가 | `ifrs-full_CostOfSales` — check `sj_div IN ('IS', 'CIS')` | Nullable if nature method |
| `sga` | 판매비와관리비 | `dart_SellingGeneralAdministrativeExpenses` | Nullable if nature method |
| `ppe` | 유형자산 | `ifrs-full_PropertyPlantAndEquipment` | No |
| `total_assets` | 자산총계 | `ifrs-full_Assets` | No |
| `lt_debt` | 장기차입금 | `dart_LongTermBorrowingsGross` (primary); `dart_BondsIssued` (secondary) | Yes |
| `net_income` | 당기순이익 | `ifrs-full_ProfitLoss` | No |

**Group 2 — Cash Flow Statement** (`finstate_all` per-company only; `fnlttMultiAcnt` does not include the CF statement):

| Field | Korean label | DART account_id | Nullable |
|---|---|---|---|
| `depreciation` | 감가상각비 | `ifrs-full_AdjustmentsForDepreciationExpense` | No |
| `cfo` | 영업활동현금흐름 | `ifrs-full_CashFlowsFromUsedInOperatingActivities` | No |

> **The batch endpoint cannot be the sole data source.** `fnlttMultiAcnt` covers Balance Sheet and Income Statement (주요계정과목) only. The Beneish DEPI ratio requires `depreciation` and TATA requires `cfo` — both from the Cash Flow Statement, which is only in `finstate_all`. Use `finstate_all` per company for all fields to keep the logic uniform.

**`lt_debt` fallback chain:**
1. `dart_LongTermBorrowingsGross` — confirmed present in DART XBRL responses
2. `dart_BondsIssued` — corporate bonds; add to numerator if present and no borrowings line
3. If neither found: set `lt_debt = null`; compute LVGI as null rather than zero
4. ❌ `ifrs-full:NoncurrentPortionOfLongtermBorrowings` — does not exist in DART XBRL
5. ❌ `dart_NoncurrentBorrowings` — does not exist in DART XBRL (empirically verified)
6. ❌ `비유동부채` / `ifrs_NoncurrentLiabilities` — far too broad; includes deferred tax, lease liabilities, provisions

**Income statement expense method:**
Companies using the "nature of expense" method (성격별 분류) do not disclose a COGS line — `cogs` and `sga` will be absent. Detection: check for `매출원가` in rows where `sj_div IN ('IS', 'CIS')`. Checking `'IS'` alone misses companies that report a combined Comprehensive Income Statement (`sj_div = 'CIS'`) without a separate `'IS'`. For nature-method companies (~12.2% of KOSDAQ, empirically verified): set `cogs = null`, `sga = null`, `gmi = 1.0`, `sgai = 1.0` (neutral substitution), and set `expense_method = 'nature'`.

---

### 4.4 Sector Data Requirements

Sector context is required — without it, the ranked M-Score list is unactionable because biotech companies structurally score differently from manufacturers.

| Field | Source | Implementation notes |
|---|---|---|
| `wics_sector_code` | WISEindex `GetIndexComponets` | Use `https://` only — HTTP actively refused as of Feb 2026. Headers required: `User-Agent`, `Referer: https://www.wiseindex.com/`, `X-Requested-With: XMLHttpRequest`. No `market` field in response; cross-reference PyKRX to separate KOSPI/KOSDAQ. |
| `wics_sector` | WISEindex | Human-readable sector name from same endpoint |
| `ksic_code` | DART `company.json` → `induty_code` | DART uses KSIC Rev.10 as of Feb 2026 (confirmed: Samsung `induty_code=264`). Join to `KSIC_10.csv.gz` from `github.com/FinanceData/KSIC`; use `dtype=str`. |
| `krx_sector` | KRX MDCSTAT03901 | Fallback if WICS unavailable |

**WICS peer group adequacy by industry group (KOSDAQ, as of 2024-12-30):**

Use industry group level (4-digit codes, 25 groups) as the default. Eight groups have fewer than 10 KOSDAQ constituents — fall back to sector level for these:

| Industry group | Code | KOSDAQ count | Action |
|---|---|---|---|
| 의류/의복 | G2530 | 9 | Fall back to G25 sector |
| 식품/음료 | G3010 | 5 | Fall back to G30 sector |
| 식품유통/약국 | G3030 | 5 | Fall back to G30 sector |
| 다각화금융 | G4020 | 4 | Fall back to G40 sector |
| 통신서비스 | G5010 | 2 | Fall back to G50 sector |
| 전기 | G5510 | 2 | Fall back to G55 sector |
| 은행 | G4010 | 1 | Skip peer scoring |
| 가스 | G5520 | 0 | Skip peer scoring |

---

### 4.5 Schema Contract — `company_financials.parquet`

| Column | Type | Nullable | Source / notes |
|---|---|---|---|
| `corp_code` | str | No | DART 8-digit — primary join key |
| `ticker` | str | No | DART `stock_code` |
| `company_name` | str | No | DART `corp_name` |
| `market` | str | No | `KOSDAQ` (Phase 1 only) |
| `year` | int | No | Fiscal year of the annual report |
| `fs_type` | str | No | `CFS`, `OFS`, or `no_filing` |
| `dart_api_source` | str | No | `finstate_all_CFS`, `finstate_all_OFS`, or `finstate_all_no_filing` — records which call produced data |
| `expense_method` | str | No | `function` or `nature` — derived from presence of `매출원가` in IS/CIS rows |
| `receivables` | float | No | |
| `revenue` | float | No | |
| `cogs` | float | Yes | Null if `expense_method = 'nature'` |
| `sga` | float | Yes | Null if `expense_method = 'nature'` |
| `ppe` | float | No | |
| `depreciation` | float | No | From CF statement via `finstate_all` |
| `total_assets` | float | No | |
| `lt_debt` | float | Yes | Null if `dart_LongTermBorrowingsGross` not separately disclosed |
| `net_income` | float | No | |
| `cfo` | float | No | From CF statement via `finstate_all` |
| `wics_sector_code` | str | Yes | |
| `wics_sector` | str | Yes | |
| `ksic_code` | str | Yes | 3-digit KSIC Rev.10 string |
| `krx_sector` | str | Yes | |
| `fs_type_shift` | bool | No | `True` if `fs_type` changes between any two consecutive years for this company (CFS→OFS or OFS→CFS); set in `transform.py` |
| `extraction_date` | str | No | ISO 8601 date string (e.g., `"2026-02-27"`); set by `transform.py` at run time |

---

### 4.6 Schema Contract — `beneish_scores.parquet`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `corp_code` | str | No | |
| `ticker` | str | No | |
| `company_name` | str | No | |
| `year_t` | int | No | Year T — the second year in the T-1 → T pair |
| `fs_type` | str | No | `CFS` or `OFS` — for year T |
| `fs_type_switched` | bool | No | `True` if `fs_type` differs between year T-1 and T |
| `expense_method` | str | No | Carried from `company_financials.parquet` |
| `dsri` | float | Yes | Days Sales Receivables Index |
| `gmi` | float | Yes | Gross Margin Index — 1.0 if `expense_method = 'nature'` |
| `aqi` | float | Yes | Asset Quality Index |
| `sgi` | float | No | Sales Growth Index |
| `depi` | float | Yes | Depreciation Index |
| `sgai` | float | Yes | SG&A Index — 1.0 if `expense_method = 'nature'` |
| `lvgi` | float | Yes | Leverage Index — null if `lt_debt` null in either year |
| `tata` | float | No | Total Accruals to Total Assets |
| `m_score` | float | Yes | Null if more than 2 component ratios are null (GMI and SGAI substituted with 1.0 for nature-method companies do not count as null) |
| `flag` | bool | No | `True` if `m_score > -1.78` |
| `high_fp_risk` | bool | No | `True` if `wics_sector_code IN ('G3510', 'G3520')` — biotech/pharma and medical devices have structurally elevated scores for legitimate growth-stage reasons |
| `wics_sector_code` | str | Yes | |
| `wics_sector` | str | Yes | |
| `sector_percentile` | float | Yes | M-Score percentile within same WICS industry group; null if peer group has < 10 companies in that year |
| `dart_link` | str | No | Direct URL to DART 사업보고서 filing for year T |
| `risk_tier` | str | No | `Critical` / `High` / `Medium` / `Low` — Beneish-only tier in Phase 1 (see PR3) |
| `extraction_date` | str | No | ISO 8601 date string; set by `beneish_screen.py` at run time |

---

### 4.7 Acceptance Criteria — Phase 1

All criteria must pass before Phase 1 is closed:

1. **Coverage:** ≥ 80% of KOSDAQ companies active during 2019–2023 (excluding SPACs and financial-sector exclusions) have at least 3 of 5 years with all non-nullable fields populated.
2. **Sector enrichment:** ≥ 80% of rows in `beneish_scores.parquet` have a non-null `wics_sector_code` (see KNOWN_ISSUES.md KI-001 — threshold intentionally set at 80% due to WICS source coverage ceiling, not 95%).
3. **Score computability:** ≥ 70% of company-year pairs have a non-null `m_score`.
4. **Financial sector exclusion:** Zero rows with `ksic_code` in `640`–`669` or `ksic_code = '68200'` in `beneish_scores.parquet`.
5. **Market purity:** Zero rows with a ticker that appears in `pykrx.stock.get_market_ticker_list('20231229', market='KOSPI')`.
6. **Expense method flag:** `expense_method` is populated for 100% of rows. Rows with `expense_method = 'nature'` have `gmi = 1.0` and `sgai = 1.0` (not null).
7. **Reproducibility:** Running `pipeline.py --market KOSDAQ --start 2019 --end 2023 --stage dart` then `transform.py` produces bit-identical output on re-run, given identical raw input files in `01_Data/raw/`.
8. **Notebook:** `uv run marimo edit 03_Analysis/beneish_screen.py` renders without error and shows the ranked table with sector filter and year filter widgets.
9. **Export:** `uv run python 03_Analysis/beneish_screen.py` writes `03_Analysis/beneish_scores.csv`.

---

### 4.8 Definition of Done — Phase 1

Phase 1 is complete when the GitHub repository contains all of the following, and all acceptance criteria above pass:

| Artifact | Location | Note |
|---|---|---|
| DART extraction script | `02_Pipeline/extract_dart.py` | |
| Transform script | `02_Pipeline/transform.py` | |
| Pipeline orchestrator | `02_Pipeline/pipeline.py` | |
| Beneish analysis notebook | `03_Analysis/beneish_screen.py` | Marimo `.py` format |
| Financials dataset | `01_Data/processed/company_financials.parquet` | Gitignored; documented in README |
| Scores dataset | `01_Data/processed/beneish_scores.parquet` | Gitignored; documented in README |
| CSV export | `03_Analysis/beneish_scores.csv` | Gitignored |
| Known issues log | `KNOWN_ISSUES.md` | At project root |

`KNOWN_ISSUES.md` must document: companies with zero data, XBRL field mapping failures, sectors below coverage threshold, any companies where `fs_type` switched between CFS and OFS across years.

The top 50 flagged companies (by M-Score in any single year) must be manually spot-checked against their DART filings to confirm no systematic account mapping errors before publishing.

---

## 5. Phase 2 — CB/BW Timelines and Price Context

### 5.1 Scope

Phase 2 does not run on all KOSDAQ companies. It runs on:
- All companies in Phase 1's top 100 by M-Score in any year, AND
- All KOSDAQ companies with ≥1 CB or BW issuance on DART since 2018

This narrows the target to approximately 200–400 companies.

### 5.2 Additional Data Required

| Source | Data | Implementation |
|---|---|---|
| KRX / PyKRX | Daily OHLCV + short selling balance, all KOSDAQ tickers, 2018–2024 | ~1,700 tickers × ~1,500 trading days |
| DART | CB/BW issuance events | `dart.list(kind='B', kind_detail='B001', final=False)` for filing index; DS005 endpoints `cvbdIsDecsn.json` (CB) and `bdwtIsDecsn.json` (BW) via direct `requests.get()` — not wrapped by OpenDartReader |
| SEIBRO | Granular conversion/exercise history; individual repricing events | SEIBRO API (`api.seibro.or.kr`) provides aggregate statistics only — granular records require Playwright/Selenium scraping of `seibro.or.kr` (WebSquare JS rendering; plain HTTP will not work) |

### 5.3 Acceptance Criteria — Phase 2

1. **KRX coverage:** ≥ 99% of active KOSDAQ tickers have complete OHLCV for 2018–2024.
2. **CB/BW capture:** Pipeline captures ≥ 95% of disclosed CB/BW events in DART for the Phase 2 company list.
3. **Timing anomaly output:** `timing_anomalies.csv` contains at least 20 events where `price_move_pct > 5%` precedes a material disclosure on the same day.

---

## 6. Phase 3 — Officer Network

### 6.1 Scope

Phase 3 applies to flagged companies only — approximately the 50 companies that score high on both Beneish (Phase 1) and CB/BW timing (Phase 2). It is not run on the full KOSDAQ universe.

### 6.2 Additional Data Required

- DART officer holdings (임원·대주주 주식 소유현황) for all Phase 3 companies
- KFTC cross-shareholding data for any Phase 3 companies belonging to 대규모기업집단 (designated groups ≥5 trillion KRW). Note: most KOSDAQ CB/BW manipulation targets are small-caps below the 5 trillion threshold — KFTC data will be absent for most Phase 3 targets.

### 6.3 Acceptance Criteria — Phase 3

1. Officer network graph includes ≥ 80% of disclosed officers for Phase 3 flagged companies.
2. Person name normalization applied: whitespace stripped, common spacing variants merged; birth-date deduplication where DART discloses it.
3. Output: one interactive HTML graph per flagged company (pyvis) and a centrality report CSV.

---

## 7. Cross-Cutting Requirements

### Rate Limits and Runtime

| Source | Confirmed limit | Implementation |
|---|---|---|
| OpenDART API | Error 020 at ~20,000 requests. Window type undocumented; calendar-day reset at 00:00 KST assumed. Operate conservatively at 10,000 calls/day. | 0.5s sleep between per-company `finstate_all` calls. At 0.5s: 1,700 companies × 5 years × 0.5s ≈ 70 min. Well within daily quota. |
| KRX / PyKRX | Undocumented | 0.5s sleep between tickers. On HTTP 429: abort current call, wait 60s, retry once. |
| SEIBRO | Undocumented | Cache raw HTML; never re-scrape if cache exists. Treat as fragile. |
| WICS API | Undocumented | Use `https://` only. Send browser headers on every request. 10 calls per refresh cycle — trivial volume. |

> **DART bulk download** (`opendart.fss.or.kr/disclosureinfo/fnltt/dwld/main.do`) requires an authenticated FSS web session. It is not an open API endpoint. Direct unauthenticated access returns a login wall. Do not use it in the pipeline.

> **Batch endpoint note:** `fnlttMultiAcnt` (up to 100 companies per call) covers Balance Sheet and Income Statement only — no Cash Flow Statement. Since `depreciation` and `cfo` require the full `finstate_all` call per company anyway, use `finstate_all` uniformly for all fields to keep logic simple. Do not build a two-tier batch/single architecture for Phase 1.

### Error Handling

- A failed fetch for one company must not abort the run for subsequent companies.
- Log all failures with: `corp_code`, company name, year, `fs_div` attempted, exception message.
- On completion, write `01_Data/raw/run_summary.json` containing: total companies attempted, companies with full data, companies with partial data (which fields missing), companies with zero data.

### Storage

- Raw files use `.tmp` extension during write; renamed to final on success (atomic writes).
- Processed Parquet is fully rewritten by `transform.py` on each run (idempotent).
- SQLite is not introduced in Phase 1 or 2 — Parquet suffices. Introduce only in Phase 3 if cross-table joins require it.

---

## 8. Verification Status

All technical assumptions in this document were verified empirically in February 2026 against the live DART API, KSIC Rev.10 reference data, the WISEindex WICS API, and a 200-company random KOSDAQ sample. Verification scripts are in `00_Reference/verify/`. Raw results are in `00_Reference/verify/results/`. The resolution of each original open question (OQ-A through OQ-F) is documented in `00_Reference/18_Research_Findings.md`.

---

## 10. Folder Structure

```
kr-forensic-finance/
│
├── .env                          DART_API_KEY (gitignored)
├── .env.example                  Template
├── .gitignore
├── pyproject.toml                uv manifest — Phase 1 deps only
├── README.md
├── KNOWN_ISSUES.md               Created at Phase 1 close
│
├── 00_Reference/
│   ├── verify/                   One-time empirical verification scripts (complete)
│   └── *.md                      Research and architecture docs
│
├── 01_Data/
│   ├── raw/                      gitignored — written by extract_dart.py
│   │   ├── company_list.parquet  KOSDAQ universe (~1,700 rows)
│   │   ├── financials/           One parquet per company-year (resumable)
│   │   │   ├── 00126380_2019.parquet
│   │   │   └── ...
│   │   ├── sector/
│   │   │   ├── wics.parquet      WICS industry group memberships (snapshot)
│   │   │   └── ksic.parquet      KSIC code per company
│   │   └── run_summary.json      Written on completion
│   └── processed/                gitignored — written by transform.py
│       ├── company_financials.parquet
│       └── beneish_scores.parquet
│
├── 02_Pipeline/
│   ├── extract_dart.py           Phase 1 — WRITE
│   ├── transform.py              Phase 1 — WRITE
│   ├── pipeline.py               Phase 1 — WRITE
│   ├── extract_krx.py            Phase 2 — DEFER (OHLCV)
│   ├── extract_seibro.py         Phase 2 — DEFER (CB/BW exercise history)
│   └── extract_kftc.py           Phase 3 — DEFER (재벌 network)
│
└── 03_Analysis/
    ├── beneish_screen.py         Phase 1 — WRITE (Marimo notebook)
    ├── cb_bw_timelines.py        Phase 2 — DEFER
    ├── timing_anomalies.py       Phase 2 — DEFER
    └── officer_network.py        Phase 3 — DEFER
```

The deferred Phase 2/3 scripts remain in the repo as stubs — they are not invoked by Phase 1.

---

## 11. Phase 1 Scripts — Responsibilities and Interfaces

### `02_Pipeline/extract_dart.py`

**One job: fetch from external sources and write to `01_Data/raw/`. No computation.**

Stages (all resumable — skips existing files unless `--force`):

1. **Company list** — PyKRX `get_market_ticker_list("20231229", market="KOSDAQ")` joined to `dart.corp_codes` on `stock_code`. Writes `01_Data/raw/company_list.parquet`.

2. **Financials** — For each company × year: `dart.finstate_all(corp_code, year, fs_div="CFS")`, then retry with `"OFS"` if empty. Saves full raw DataFrame to `01_Data/raw/financials/{corp_code}_{year}.parquet`. Does NOT map accounts or filter rows.

3. **WICS sector** — 25 HTTP GET calls, one per WICS industry group code (G1010 through G5520). Writes `01_Data/raw/sector/wics.parquet`. Requires `https://`, browser headers.

4. **KSIC codes** — `dart.company(corp_code)` per company to get `induty_code`. Writes `01_Data/raw/sector/ksic.parquet`.

5. **run_summary.json** — Written on completion: companies attempted, successful, partial, failed.

CLI:
```
python 02_Pipeline/extract_dart.py --market KOSDAQ --start 2019 --end 2023
python 02_Pipeline/extract_dart.py --market KOSDAQ --start 2019 --end 2023 --force
python 02_Pipeline/extract_dart.py --stage company-list
python 02_Pipeline/extract_dart.py --stage sector
```

---

### `02_Pipeline/transform.py`

**One job: raw parquet → `company_financials.parquet`. Idempotent.**

Steps:
1. Load all `01_Data/raw/financials/*.parquet` files
2. For each file: extract 10 schema fields via `account_id` exact match (primary) then `account_nm` Korean label match (fallback). Filter `sj_div` appropriately per field (BS, IS/CIS, CF).
3. Detect expense method: `매출원가` in `sj_div IN ('IS', 'CIS')` → `'function'`; absent → `'nature'`
4. Apply `lt_debt` fallback chain: `dart_LongTermBorrowingsGross` → `dart_BondsIssued` → null
5. Join `01_Data/raw/sector/wics.parquet` (via ticker → stock_code) and `01_Data/raw/sector/ksic.parquet` (via corp_code)
6. Apply financial exclusions: drop rows where KSIC in 640–669 or = 68200x
7. Record `dart_api_source` per row from the filename (`CFS` vs `OFS` vs `no_filing`)
8. Write `01_Data/processed/company_financials.parquet`

CLI:
```
python 02_Pipeline/transform.py
```

---

### `03_Analysis/beneish_screen.py`

**Marimo notebook: loads processed parquet, computes scores, renders interactive UI, writes CSV.**

Cells / functions:
1. Load `company_financials.parquet`
2. Compute 8 Beneish ratios (lag via per-company sort + shift); neutral 1.0 for GMI/SGAI where `expense_method = 'nature'`
3. Compute M-Score; set `flag`, `high_fp_risk`, `fs_type_switched`
4. Compute `sector_percentile` within WICS industry group (≥10 peers required)
5. Write `01_Data/processed/beneish_scores.parquet` and `03_Analysis/beneish_scores.csv`
6. Interactive controls: threshold slider, sector filter, year filter, high-FP-risk toggle
7. Display: ranked table + M-Score distribution histogram

Run modes:
```
uv run marimo edit 03_Analysis/beneish_screen.py     # interactive development
uv run marimo run 03_Analysis/beneish_screen.py      # web app (read-only)
uv run python 03_Analysis/beneish_screen.py          # script mode, writes CSV
```

---

### `02_Pipeline/pipeline.py`

**Thin CLI orchestrator — no business logic.**

```
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023 --stage dart
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023 --stage transform
```

Calls `extract_dart.run()` then `transform.run()` in order. `--stage` runs only one.

---

### `pyproject.toml` — Phase 1 deps only

Phase 2/3 dependencies (`networkx`, `pyvis`, `scipy`, `dart-fss`, `feedparser`, `matplotlib`) are removed from `pyproject.toml`. They will be added back when Phase 2 implementation begins. Phase 1 actual imports:

```toml
dependencies = [
    "opendartreader",
    "pykrx",
    "pandas",
    "pyarrow",
    "marimo",
    "plotly",
    "requests",
    "python-dotenv",
]
```

---

## 9. Phase 2+ Commercial Requirements

*Added Feb 27, 2026. Derived from stakeholder demand analysis in `00_Reference/00_Feature_Analysis.md`. These requirements are not in scope for Phase 1 or the current Milestone 2–4 pipeline scripts. They are documented here so they can be tracked as named, testable acceptance criteria rather than informal notes.*

---

### Technical Data Gaps

#### PR1 — Data Lineage Column

**Demand source:** Litigation support (#4), academic researchers (#13)
**Why:** Expert reports and peer-reviewed papers must characterize whether each financial variable came from an exact XBRL element ID match or a Korean `account_nm` substring fallback. The `dart_xbrl_crosswalk.csv` documents the possible chain; this column records what actually occurred per company-year observation.

**Acceptance criterion:** `company_financials.parquet` contains per-variable match-method columns (e.g., `match_method_receivables`) with values `exact_id` | `korean_substring` | `null`. Populated by `transform.py` during extraction; no pipeline re-run required to add the column structure.

---

#### PR2 — Extraction Timestamp

**Demand source:** Compliance/KYC (#2), litigation support (#4), academic researchers (#13)
**Why:** AML/CFT regulations require documenting data currency. Litigation requires a citable extraction date. Academic datasets require provenance metadata. No timestamp is currently recorded in either output parquet.

**Acceptance criterion:** `extraction_date` column (ISO 8601 string, e.g., `"2026-02-27"`) present in both `company_financials.parquet` and `beneish_scores.parquet`, not null, set to the date `transform.py` / `beneish_screen.py` was last run.

**Implementation note:** Low-effort — add `datetime.today().date().isoformat()` as a constant column in `transform.py` and carry through in `beneish_screen.py`.

---

#### PR3 — Composite Risk Tier

**Demand source:** M&A due diligence (#1), D&O insurance underwriting (#8), institutional investors (#3), SaaS/RegTech (#15)
**Why:** Most downstream consumers need a single actionable verdict, not raw Beneish ratios. The existing `flag` boolean is a binary approximation; a four-level tier with documented criteria is required for client-facing output.

**Phase 1 acceptance criterion (Beneish-only version):**

| Tier | Criteria |
|---|---|
| `Critical` | `flag=True` AND `high_fp_risk=False` AND `m_score > -1.0` |
| `High` | `flag=True` AND `high_fp_risk=False` AND `m_score ≤ -1.0` |
| `Medium` | `flag=True` AND `high_fp_risk=True` |
| `Low` | `flag=False` |

Document that this tier is Beneish-only until Phase 2+ flags (CB/BW, timing anomaly) are integrated.

---

#### PR4 — KOSPI Coverage

**Demand source:** M&A due diligence (#1), institutional investors (#3), D&O insurance (#8), academic researchers (#13)
**Why:** KOSPI companies (~900) are the primary targets for M&A, institutional investment, and D&O insurance. Most academic studies require the full KRX universe, not KOSDAQ only.

**Acceptance criterion:** Pipeline `--market KOSPI` run completes; `beneish_scores.parquet` contains rows with `market = 'KOSPI'`; all Phase 1 AC1–AC7 pass for KOSPI rows; `sector_percentile` computed against KOSPI-specific peer groups (not pooled with KOSDAQ).

**Implementation note:** PyKRX and OpenDartReader both support KOSPI. Main new issue: WICS peer group counts differ for KOSPI; the minimum-10-peers threshold needs re-evaluation.

---

#### PR5 — Historical Backfill (2014–2018)

**Demand source:** Academic researchers (#13)
**Why:** Panel regressions in Korean capital markets research require 7–10 year windows. The KoTaP dataset (Nature Scientific Data, 2026 — the closest public comparable) covers 2011–2024. A 5-year window limits academic usability.

**Acceptance criterion:** Pipeline `--start 2014 --end 2018` run completes; `company_financials.parquet` contains 2014–2018 rows; `beneish_scores.parquet` contains 2015–2018 score periods (2014 is T-1 baseline only). XBRL element ID mapping validated against pre-2019 filing structure.

**Implementation note:** DART XBRL coverage exists back to ~2015. Pre-IFRS-era companies (pre-2012) use K-GAAP element IDs — outside scope. Main risk: element IDs may differ for some accounts in 2014–2016 filings.

---

### Commercial / Legal Blockers

These are not pipeline engineering tasks but must be completed before the corresponding revenue channels can be opened.

#### CB1 — Capital Markets Act Compliance Analysis

**Demand source:** Institutional investors (#3), SaaS/RegTech (#15)
**What:** Legal analysis of whether distributing ranked anomaly scores to institutional investors constitutes "investment advisory" under FSCMA (자본시장법). GMT Research resolved this via HK SFC registration. Korean alternatives may exist.
**Prerequisite for:** Revenue models #3 (Alt Data), #15 (SaaS)
**Responsible party:** Korean financial regulatory counsel

---

#### CB2 — PIPA-Compliant Officer Data Packaging

**Demand source:** Compliance/KYC (#2), litigation support (#4)
**What:** Legal and technical framework for packaging and distributing officer personal data (name, DOB, shareholding changes) sourced from public DART filings. The "publicly available" exemption under Korea's Personal Information Protection Act (개인정보 보호법) is narrower than it appears.
**Prerequisite for:** Milestone 4 (Officer Network) commercial distribution, revenue models #2, #4
**Responsible party:** Korean privacy counsel + technical implementation (access controls)

---

#### CB3 — GitHub Public Release

**Demand source:** Academic researchers (#13), media partners (#11), philanthropic funders (#14)
**What:** Complete `00_Reference/23_GitHub_Release_Checklist.md` and make the repository public.
**Why urgent:** Public availability is a prerequisite for grant funding applications, academic partnerships, and media credibility. The checklist is documented; execution is needed.
**Responsible party:** Project owner (engineering + documentation review)

---

#### CB4 — Law Firm Referral Structure (Whistleblower)

**Demand source:** Whistleblowers (#5)
**What:** Formal referral agreement with a licensed Korean law firm for whistleblower submission support under the Feb 2026 FSC reform. The pipeline identifies anomaly targets; legal counsel structures FSS/FSC submissions.
**Prerequisite for:** Revenue model #5
**Responsible party:** Business development

---

#### CB5 — Nonprofit Legal Entity

**Demand source:** Philanthropic funders (#14), government contracting (#10)
**What:** Register a nonprofit legal entity (사단법인 or equivalent). Most international foundations require a registered nonprofit counterpart. Korean NRF/IITP grants require institutional affiliation. Lead time: 3–6 months for Ministry approval.
**Prerequisite for:** Revenue models #10, #11, #14
**Responsible party:** Legal/organizational

---

## 10. Out of Scope — All Phases

- KONEX companies (too illiquid; anomaly signal is noise at that scale)
- KOSPI in Phase 1 (higher data quality, lower base rate of target phenomena; add in Phase 4)
- Real-time or intraday data (no public free source)
- DART bulk download as a data pipeline source (requires authenticated FSS web session)
- Narrative analysis of 사업보고서 text (Layer 2 — Claude API; separate from the ETL pipeline)
- Investment conclusions or fraud determinations — all outputs are ranked hypothesis lists for human review
