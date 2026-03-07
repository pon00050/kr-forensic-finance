# Roadmap

## Milestones

| # | Milestone | Status | Script |
|---|---|---|---|
| 1 | Beneish M-Score screen | Complete | `beneish_screen.py` |
| 2 | CB/BW timelines | Implemented, runnable | `cb_bw_timelines.py` |
| 3 | Timing anomalies | Implemented, runnable | `timing_anomalies.py` |
| 4 | Officer network graph | Implemented, runnable | `officer_network.py` |

## Phase 2 Data (extracted)

| Table | Description |
|---|---|
| `cb_bw_events.parquet` | CB/BW issuance events from DART DS005 |
| `price_volume.parquet` | OHLCV ±60 day windows around events |
| `corp_ticker_map.parquet` | corp_code ↔ ticker mapping |
| `officer_holdings.parquet` | Officer holding changes |
| `disclosures.parquet` | DART filing listings — 921 corps / 271,504 rows (expanded from 58 corps in session 31) |
| `major_holders.parquet` | 5%+ ownership threshold filings |
| `bondholder_register.parquet` | CB bondholder names from 사채권자명부 |
| `revenue_schedule.parquet` | Revenue by customer/segment from 매출명세서 |
| `bond_isin_map.parquet` | 1,859 validated bond ISINs / 656 corp_codes via FSC API (dataset 15043421); required by SEIBRO StockSvc extractor |

## Codebase Cleanup — Completed (Sessions 34–35)

**Session 34:** 22 issues across 3 phases (bugs/security, performance, consolidation).

| Phase | Scope | Key changes |
|---|---|---|
| A (bugs/security) | 4 items | ServiceKey casing fix (KI-022), DuckDB SQL escaping (KI-023), narrowed except blocks, file handle leak |
| B (performance) | 5 items | Pre-grouped price lookups (~900M comparisons eliminated), lazy WICS probe, DataFrame concat, cached parquet reads, lazy plotly import |
| C (consolidation) | 5 items | 4 duplicate functions → `_pipeline_helpers.py`, DART status constants, `src/constants.py`, `src/_paths.py`, removed 13 redundant `sys.path.insert` |

**Session 35:** 3 phases addressing remaining structural issues.

| Phase | Scope | Key changes |
|---|---|---|
| D (constants adoption) | 3 files | 8 flag literals + 3 threshold literals → `src/constants.py` imports |
| E (scoring extraction) | 3 files | ~150-line scoring logic deduplicated into `03_Analysis/_scoring.py`; fixed Marimo missing `flag_count` + conditional `peak_date` (KI-025) |
| F (loader consolidation) | 2 files | 7 report loaders → 2 generic + 2 special; removed dead `_load_financials()`; fixed double beneish parquet read |

168 tests pass. See `CHANGELOG.md` and `KNOWN_ISSUES.md` KI-022 through KI-025 for full details.

## What's Next

1. **SEIBRO repricing data** — extractor built; ISIN map populated (1,859 ISINs / 656 corps); `holdings_flag` fixed (session 32, now 156 events / 27 double-flagged); SEIBRO API key still pending (re-probed session 32, still `resultCode=99`); once key activates, full activation runbook in KI-012
2. **Populate paid-tier tables** — run paid-tier extractors at scale for flagged companies
3. **Statistical analysis layer** — 10 ISL-grade scripts written; S1–S5 complete (session 24); findings in `FINDINGS.md`

## Statistical Analysis — Completed (Session 25)

| ID | Description | Outcome |
|---|---|---|
| S9 | Cross-screen: PC3 top-decile × flagged CB/BW events | **170 double-flagged company-years; 143 unique secondary companies** (updated session 33 with holdings_flag live); **8 high-priority secondaries** (PC3≥95th AND flag_count≥2) — was 0 in all prior runs; top lead: 캔버스엔 (00550082, PC3_rank 0.9984) |
| S8 | Run `extract_depreciation_schedule.py` for 5 Tier 1 leads | All 15 rows = parse_error or no_filing; DART sub_docs keyword matching returns wrong table type for these companies; Category 20 tests flip from 8 skipped → **8 passed**; FINDINGS.md §4 updated with root cause |
| S10a | Extract disclosures for 50 unflagged control companies | `disclosures.parquet` expanded from 8 → **58 corp_codes** (3,581 → 27,486 rows; +23,905 control rows) |
| S10b | Rebuild FDR null from control disclosures × price data | Control null: 2,000 quiet events; **687/687 test events trivially survive BH** — KI-021 diagnosed: pre-filtering makes any clean null give p≈0; valid test requires unfiltered input → S11 |
| S11 | Proper FDR disclosure leakage test (fixes KI-021) | `fdr_disclosure_leakage.py` written; **2/822 events survive BH at q=0.043** — 피씨엘 2021-01-18 (+287%) and 프로브잇 2021-06-14 (+143%); p-value distribution shows mild enrichment near 0; KI-021 RESOLVED. **Revised in session 31** (disclosures expanded 58→921 corps): **0/822 survivors** — previous 2-survivor result was artifact of weak null (50 corps); with 811 control corps the signal doesn't survive BH; mild p-value enrichment near 0 persists (72 vs 41 expected) |

## Statistical Analysis — Completed (Session 24)

| ID | Description | Outcome |
|---|---|---|
| S1 | Fix `cluster_peers.py` z-score contamination (KI-020) | **50 cluster-relative flags** (was 0); KI-020 resolved |
| S2 | Investigate 김형석 and 박정우 | Confirmed 4 and 2 flagged companies respectively; no Tier 1 lead overlap; 박정우 confirmed as 전무이사 at 우리기술 with CB acquisition; see `FINDINGS.md` §5a |
| S3 | Redesign FDR null distribution | timing_anomalies.csv pre-filtered (all extreme events); clean null requires full disclosures.parquet join — new blocker documented |
| S4 | PC3 as alternative manipulation screen | 531 top-decile company-years; 6 of 18 Tier 1 lead company-years in top decile; `pca_pc3_scores.csv` output added |
| S5 | Depreciation extractor for Tier 1 leads | `extract_depreciation_schedule.py` written; Category 20 schema test added; ready to run |

## Statistical Analysis — Remaining Action Items

### Completed (Session 38)

| ID | Description | Outcome |
|----|-------------|---------|
| — | Session 39: label expansion + blind spot docs | 아스트 (FSC 22억 fines) + 휴림로봇 (검찰 기소) fraud=1; labels 28→30 (17 fraud=1); bootstrap −1.85 (CI [−2.85, −0.90]); RF AUC 0.738±0.201; FINDINGS.md §10 blind spots detailed; KI-026 (refresh --sample destructive) |
| — | krff reports for 8 high-priority secondaries | Generated 8 HTML reports (캔버스엔, 스피어, 알티캐스트, 아스트, 라닉스, 휴림로봇, 엑시온그룹, 유일에너테크); all dual-flagged (holdings_decrease + volume_surge); SEIBRO still resultCode=99 (day 4) |
| — | Label coverage analysis | `label_coverage_analysis.py` written; 13/14 Beneish (93%); 10/14 CB/BW (71%); 6/14 dual (43%); 에코앤드림 Beneish blind spot; §10 in FINDINGS.md |
| — | Label expansion — 알티캐스트 | Web search confirmed CEO 서정규 배임 기소 2023-12-19 (특경법); added as fraud=1; labels 27→28 (15 fraud=1); bootstrap −1.75 stable; RF AUC 0.740; TATA −0.101 |
| A1 | Automate recurring data refresh | `krff refresh` command added to `cli.py`; 6-stage wrapper; `--sample` + `--skip-analysis` flags; 168 tests pass |

### Completed (Session 36)

| ID | Description | Outcome |
|----|-------------|---------|
| S13 | Expand `labels.csv` with confirmed Korean fraud cases | 15→27 labels; 4 new fraud=1 (초록뱀그룹 CB배임 cases + 셀리버리); 8 new fraud=0. Bootstrap threshold: −0.75→−1.75 (near US −1.78). RF AUC: 0.670→0.786 ± 0.182. TATA negative coefficient confirmed as stable KOSDAQ pattern. FINDINGS.md §9 added. |
| S14 | Pipeline validation against confirmed fraud companies | All 4 confirmed fraud companies caught by M-score (≥1 year above −1.78). 초록뱀그룹: flag_count=1 (volume_surge). 셀리버리: flag_count=0 (disclosure fraud, not CB abuse). Cross-Script Synthesis updated. SEIBRO still resultCode=99. |

### Ready now (no external dependencies)

*(none — all non-blocked items complete)*

### Completed (Session 28)

| ID | Description | Outcome |
|---|---|---|
| S6a | Run `build_isin_map.py --sample 50` | **0 ISINs found** — DART CB/BW filings don't contain bond ISINs; approach invalid; need KRX/SEIBRO alternative |
| S7 | Expand `labels.csv` to ≥10 rows; run 3 blocked scripts | **15 labels** (10 fraud=1, 5 fraud=0); bootstrap median=-0.75 (CI [-2.55,-0.50], US -1.78 inside); Lasso: DSRI/TATA/SGI/GMI active; RF AUC=0.670 |

### Completed (Session 26)

| ID | Description | Outcome |
|---|---|---|
| S12 | Fix `extract_seibro_repricing.py` (4 endpoint/param bugs + ISIN join key); write `build_isin_map.py` | **extractor now uses StockSvc/getXrcStkOptionXrcInfoN1 + getXrcStkStatInfoN1 with bondIsin param; `build_isin_map.py` extracts ISINs from DART CB filings via regex** |
| S12b | Probe `extract_seibro.py` websquare endpoints | All 4 return HTML shell (545 chars, JS redirect) — WebSquare requires browser session; **superseded by data.go.kr REST API** |

### Completed (Session 29)

| ID | Description | Outcome |
|---|---|---|
| S6a | Populate `bond_isin_map.parquet` | **RESOLVED.** DART approach failed (ISINs not in filings); switched to FSC 금융위원회 채권발행정보 API (dataset 15043421, `getIssuIssuItemStat`). Full run (session 30): **2,718 ISINs across 685 corp_codes** (of 919 queried). All 5 Tier 1 leads have ISINs. |

### Blocked (external dependencies)

| ID | Description | Blocked by |
|---|---|---|
| S6 | Run `extract_seibro_repricing.py` → re-run `permutation_repricing_peak.py` + `survival_repricing.py` | SEIBRO API key activation only (ISIN map blocker resolved — see S6a above) |

## Phase 3 — Continuous Monitoring

| ID | Description | Status |
|----|-------------|--------|
| M1 | Event-driven re-scoring on new regulatory filings | Planned |
| M2 | Market surveillance signal integration | Planned |
| M3 | Regulatory enforcement feed and automated evidence staging | Planned |

Phase 3 extends the pipeline from periodic batch processing to continuous monitoring.
Detection runs incrementally as new data arrives rather than on a fixed schedule,
reducing time-to-signal from weeks to hours. Full specification in internal documentation.

Phase 3 engineering prerequisites (before Phase 4 website):
- **FastAPI readiness refactoring — Complete (Session 43).** `src/data_access.py` (reusable loaders),
  `src/models.py` (Pydantic response shapes), env var config overrides, public API functions
  (`get_company_summary`, `get_report_html`). All scoring constants consolidated in `src/constants.py`.
  A developer can now write `from src.report import get_company_summary` in a FastAPI endpoint.
- **FastAPI HTTP layer — Complete (Session 44).** `app.py` (4 endpoints: `/api/status`,
  `/api/quality`, `/api/companies/{corp_code}/summary`, `/api/companies/{corp_code}/report`);
  `krff serve` CLI command (`uvicorn`-backed); Typer input validation on all commands (`run`, `report`,
  `refresh`); try/except error wrapping on all commands; `fastapi>=0.115.0` + `uvicorn[standard]>=0.30.0`
  added to deps. Start with `krff serve` → Swagger UI at `http://127.0.0.1:8000/docs`. 182 tests pass.
- FastAPI backend serving JSON endpoints over PostgreSQL operational state
- Minimal orchestrator: Poll → Normalize → Dedup → Dispatch → Execute → Publish → Log
- PostgreSQL for operational state (source_events, jobs, alerts, company_snapshots, artifacts)
  Keep parquet for analytics — do not migrate analytical tables to SQL

### Multi-user readiness gate (deliberately deferred until DB is integrated)

The current `app.py` is correct and complete for single-analyst use. The following issues
are **not bugs today** but must be resolved before the API serves multiple simultaneous users.
They are structurally solved by the PostgreSQL integration above — listed here for reference.

**Typer CLI — minimal changes needed:**
- Concurrent `krff run` writes to shared `01_Data/processed/` will race. Each user must set
  their own `KRFF_DATA_DIR` env var (already supported via `src/_paths.py`).
- DART API key exhaustion (20K req/day) is per key. Multiple users on one key will collide.
  Fix: separate keys per user, or a shared rate-limiting wrapper.

**FastAPI — resolved by DB integration:**
- `get_quality()` loads every parquet in full on every `/api/quality` request. Acceptable for
  one analyst; under concurrent load, multiple full-DataFrame reads spike memory and latency.
  Fix: cache with TTL, or restructure to read only PyArrow parquet footer statistics (no full
  DataFrame load needed for null counts).
- All routes are sync `def`, run in FastAPI's default thread pool
  (`min(32, cpu_count + 4)` threads). Under concurrent disk-heavy requests, threads queue.
  Fix: switch to `async def` + `asyncio.to_thread()` for disk reads, or set explicit
  `threadpool_size` in uvicorn config.
- No authentication. Any host that can reach the port can call any endpoint.
  Fix: API key header middleware (simple), or OAuth (public-facing).
- Once the DB is the operational layer, `get_status()` and `get_quality()` read from DB
  instead of live parquet scans, eliminating both the latency and the caching problem.

## Phase 4 — Public Website (ultimate goal)

Institutions consume signals and reports in a familiar web interface.
No code execution required from end users — they read, not operate.

| ID | Description | Status |
|----|-------------|--------|
| W1 | FastAPI backend + company/alert endpoints | Planned |
| W2 | Static or server-rendered public website | Planned |
| W3 | Company pages with signal history and report links | Planned |
| W4 | Alert feed with severity levels and source links | Planned |
| W5 | Admin review layer (false-positive flagging, label staging) | Planned |

Design principles:
- Frontend reads only published state from operational DB (atomic publish pattern)
- Public language: "signal", "anomaly", "pattern" — never "fraud confirmed" or "criminal"
- Infrastructure works without AI; AI enhances triage and summarization but does not gate the pipeline

Multi-agent design (Phase 4 target):
- Ingestion/triage agent — classify relevance, identify corp_code
- Analysis operator agent — call existing scripts via fixed action menu
- QA/validation agent — check output completeness and publish-safety
- Publisher agent — generate website-ready summaries (signal language only)
- Adversary/refutation agent — actively find benign explanations; challenge severity before publication

## Open Backlog

| ID | Description | Phase | Effort |
|---|---|---|---|
| PR5 | Historical backfill 2014–2018 | 4 | Medium |
| ~~A1~~ | ~~Automate recurring data refresh~~ — **Complete (Session 38):** `krff refresh` command added to `cli.py`; runs 6 stages in sequence (DART → transform → beneish_screen → cb_bw → timing → network); `--sample N` and `--skip-analysis` flags | 2 | Low |
| I1 | Verify PyKRX from hosted IPs | 5 | Low |
