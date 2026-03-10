# Roadmap

## Milestones

| # | Milestone | Status | Script |
|---|---|---|---|
| 1 | Beneish M-Score screen | Complete | `beneish_screen.py` |
| 2 | CB/BW timelines | Implemented, runnable — output quality improved (session 61) | `cb_bw_timelines.py` |
| 3 | Timing anomalies | Implemented, runnable — output quality improved (session 61) | `timing_anomalies.py` |
| 4 | Officer network graph | Implemented, runnable — output quality improved (session 61) | `officer_network.py` |

## Phase 2 Data (extracted)

| Table | Description |
|---|---|
| `cb_bw_events.parquet` | CB/BW issuance events from DART DS005; 11 cols including issue_amount, maturity_date, refixing_floor, board_date, warrant_separable |
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

1. **SEIBRO repricing data** — API key registered but inactive (resultCode=99, KI-012). Blocking `permutation_repricing_peak.py` and `survival_repricing.py`. SEIBRO provides the only structured source of per-event repricing dates/prices and exercise batches (Flags 1 & 2). For commercial use, KSD 별도이용허락 required (call 051-519-1420). See doc 54 (`54_DART_SEIBRO_Technical_Comparison.md`) for verified signal-by-signal assessment — DART cannot replace SEIBRO for these signals.
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
| A2 | Pipeline freshness checker | `krff audit` command added; DAG encodes 6 stages; detects stale outputs via mtime comparison; propagates staleness downstream; 7 new tests; 230 total pass |
| A3 | Statistical test orchestrator | `krff stats` command added; `STATS_DAG` (14 nodes); `--dry-run` + `--verbose`; 8 new tests; 250 total pass — **Complete (Session 62)** |

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
| S6 | Run `extract_seibro_repricing.py` → re-run `permutation_repricing_peak.py` + `survival_repricing.py` | SEIBRO API key activation only |

## Statistical Layer Methodological Fixes — Session 64

Three methodological problems in the supervised statistical layer fixed (session 64, Mar 9 2026).
Findings remain directional until scripts are re-run with new CI values recorded.

| # | Problem | Fix | Scripts |
|---|---------|-----|---------|
| 1 | Row-level bootstrap inflated effective n, narrowed CI artificially | `cluster_bootstrap_sample()` helper — resamples at company level | `bootstrap_threshold.py` |
| 2 | `auto_controls()` used `m_score < -2.5` to pick controls — same metric being calibrated | External criteria only: no CB/BW events, ≥3 scoreable years, neutral sort | all 3 scripts |
| 3 | Standard k-fold / LOO mixed same-company years across train/test | `GroupKFold(corp_code)` with `groups=` argument | `lasso_beneish.py`, `rf_feature_importance.py` |

8 new invariant tests added (261 total). Re-run scripts to update calibration values in MEMORY.md.

## Chapter 6 Methodological Additions — Session 65

Two Chapter 6 (ISL) outputs added to `lasso_beneish.py` (session 65, Mar 9 2026).

| # | Addition | Output | Detail |
|---|---------|--------|--------|
| 4 | Regularization path | `lasso_path.csv` + `lasso_path.png` | `compute_lasso_path()` helper using `sklearn.lasso_path()`; 100-alpha × 8-component matrix; Plotly line chart; shows entry order as penalty decreases |
| 5 | EPV check | EPV printed at startup; `epv` column in `lasso_coefficients.csv` | `events_per_variable()` helper; 17/8 = 2.1 (below accepted minimum 10–15); WARNING printed when EPV < 10 |

6 new invariant tests added (267 total). `lasso_beneish.py` re-run required to generate `lasso_path.csv` / `lasso_path.png`.

## Chapter 8 Methodological Additions — Session 66

Two Chapter 8 (ISL — Tree-Based Methods) outputs added to `rf_feature_importance.py` (session 66, Mar 9 2026).

| # | Addition | Output | Detail |
|---|---------|--------|--------|
| 6 | Impurity vs. permutation importance comparison | `rf_importance_comparison.csv` + `rf_importance_comparison.png` | `compare_importance_methods()` helper; returns DataFrame with rf_rank, perm_rank, rank_divergence; scatter plot (rf_rank vs perm_rank) shows method agreement/disagreement; pure function — takes precomputed arrays |
| 7 | RF EPV check | EPV printed at startup; `epv` column in `rf_importance.csv` | `rf_events_per_variable()` helper; denominator is dynamic feature count (not fixed 8); WARNING when EPV < 10 (expected: 17/~15 ≈ 1.1) |

9 new invariant tests added (276 total). `rf_feature_importance.py` re-run required to generate `rf_importance_comparison.csv` / `rf_importance_comparison.png`.

Deferred Ch. 8 additions (future session):
- OOB score as bootstrap-consistent internal estimate (`oob_score=True` in `RandomForestClassifier`)
- Importance stability across seeds (Spearman rank correlation across N `random_state` values)

## Chapter 12 Methodological Additions — Session 67

Two Chapter 12 (ISL — Unsupervised Learning) outputs added (session 67, Mar 9 2026).

| # | Addition | Script | Output | Detail |
|---|---------|--------|--------|--------|
| 8 | PCA loading interpretation | `pca_beneish.py` | `pca_top_loadings.csv` | `pca_top_loadings()` helper; pure function; top-3 features per PC by |loading|; replaces hardcoded PC3 comment; 24 rows (8 PCs × 3 features) |
| 9 | GMM AIC/BIC vs k-means | `cluster_peers.py` | `cluster_gmm_fit.csv` | `gmm_aic_bic()` helper; fits GMM at K_VALUES=[6,8,10]; reports AIC/BIC per k; directly addresses Problem 6 (k-means wrong for hyperspherical data) |

6 new invariant tests added (282 total). Both scripts need re-run to generate new outputs.

## Chapter 13 Methodological Additions — Session 68

Two Chapter 13 (ISL — Multiple Testing) outputs added (session 68, Mar 9 2026).

| # | Addition | Script | Output | Detail |
|---|---------|--------|--------|--------|
| 10 | Storey π₀ estimator | `fdr_timing_anomalies.py` | `fdr_timing_summary.csv` | `pi0_estimate()` helper; Storey (2002) method; `#{p>λ}/(m·(1−λ))`; clipped to [0,1]; reports proportion of true nulls and expected false discoveries; 1-row summary CSV |
| 11 | Bonferroni vs BH comparison | `fdr_disclosure_leakage.py` | `fdr_bonferroni_compare.csv` | `bonferroni_compare()` helper; ISL §13.3 (FWER) vs §13.4 (FDR); per-test rejection flags for both methods; agreement column; BH guaranteed ≥ Bonferroni rejections |

6 new invariant tests added (288 total). Both scripts need re-run to generate new outputs.

## Output Quality Issues — Session 62 Review

Identified by `/review-pipeline` on 2026-03-08. Address before next statistical test run.

| Priority | ID | Issue | Impact | Status |
|---|---|---|---|---|
| **High** | OQ1 | SEIBRO still inactive (resultCode=99, KI-012) — Flags 1 & 2 (repricing_below_market, exercise_at_peak) are effectively off; near-zero True counts in `repricing_flag` and `exercise_cluster_flag` | CB/BW screen ~50% blind; 756 flagged events are almost entirely single-flag volume surges | Blocked on API key activation |
| **High** | OQ2 | FDR timing p-values all = 0.0001 (floor) — `fdr_timing_anomalies.csv` shows `p_value.nunique() == 1`; the FDR computation is not producing meaningfully different values across events | `fdr_timing_anomalies.py` result is statistically invalid | **Resolved (session 63)** — removed `quiet_mask` filter; null now uses all control events; p-value range restored |
| **Medium** | OQ3 | `gap_hours` = 2.5 for all rows in `timing_anomalies.csv` — fixed constant, not computed per-event | Timing anomaly methodology understates precision; all events treated as identical lag | **Resolved (session 63)** — gap_hours now 2.5 for same_day, 15.0 for prior_day |
| **Medium** | OQ4 | Bootstrap F1 plateau — F1 range ≈ 0.04 across the full −3.5 to 0.0 threshold sweep; no sharp elbow | Threshold selection (−2.45 / −1.78) is weakly determined by the data; note as calibration limitation in any writeup | **Acknowledged limitation** — SEIBRO activation marginally narrows F1 CI; expanding labels (OQ6) partially helps; with only 30 labels and 1,700+ companies no threshold produces a sharp peak. Document as calibration caveat in any writeup. Not a code fix. |
| **Low** | OQ5 | Cluster silhouette ≈ 0.25 (k=6,8,10) — no natural cluster structure in KOSDAQ Beneish data | k-means may not be appropriate; consider DBSCAN or hierarchical clustering, or report as null result | **Acknowledged finding** — silhouette ≈ 0.25 across k=6,8,10; no natural cluster structure. Try DBSCAN or hierarchical clustering as alternatives; if they confirm weak structure, report as a market-specific null result. Not a data quality issue. |
| **Low** | OQ6 | Label set tiny (30 labeled + 20 auto-controls) — RF AUC CI = ±0.192 | Model reliability caveat; expand labels before publishing RF/Lasso findings | Open |

**SEIBRO dependency matrix:**

| Issue | SEIBRO fixes it? | Can fix now? |
|-------|-----------------|--------------|
| OQ1   | Yes, directly   | No — external |
| OQ2   | No              | Yes ✓ |
| OQ3   | No              | Yes ✓ |
| OQ4   | Marginally      | Partially (via more labels) |
| OQ5   | No              | Partially (try other algorithms) |
| OQ6   | No              | Yes, but labor-intensive |

## Phase 3 — Continuous Monitoring

| ID | Description | Status |
|----|-------------|--------|
| M1 | Event-driven re-scoring on new regulatory filings | Planned |
| M2 | Market surveillance signal integration | Planned |
| M3 | Regulatory enforcement feed and automated evidence staging | Planned |

Phase 3 extends the pipeline from periodic batch processing to continuous monitoring.
Detection runs incrementally as new data arrives rather than on a fixed schedule,
reducing time-to-signal from weeks to hours. Full specification in internal documentation.

### Phase 3 prerequisites

| ID | Description | Status |
|----|-------------|--------|
| P1 | DuckDB analytics layer (`src/db.py`): connection factory over existing parquet files | Complete (Session 46) |
| P2 | Pydantic models for alerts/monitoring (`AlertEvent`, `MonitorStatus`, `AlertList`) | Complete (Session 46) |
| P3 | Monitor package skeleton (`02_Pipeline/monitor/`) | Complete (Session 46) |
| P4 | CLI stubs (`krff monitor`, `krff alerts`) | Complete (Session 46) |
| P5 | API stubs (`/api/alerts`, `/api/monitor/status`) | Complete (Session 46) |
| P6 | Alert schema + SQLite operational state | Planned (deferred until M1 needs persistent state) |
| P7 | Label candidates schema for automated staging | Planned |

### Phase 3 engineering prerequisites (before Phase 4 website)

- **FastAPI readiness refactoring — Complete (Session 43).** `src/data_access.py` (reusable loaders),
  `src/models.py` (Pydantic response shapes), env var config overrides, public API functions
  (`get_company_summary`, `get_report_html`). All scoring constants consolidated in `src/constants.py`.
  A developer can now write `from src.report import get_company_summary` in a FastAPI endpoint.
- **FastAPI HTTP layer — Complete (Session 44).** `app.py` (6 endpoints: `/api/status`,
  `/api/quality`, `/api/companies/{corp_code}/summary`, `/api/companies/{corp_code}/report`,
  `/api/alerts`, `/api/monitor/status`);
  `krff serve` CLI command (`uvicorn`-backed); Typer input validation on all commands (`run`, `report`,
  `refresh`); try/except error wrapping on all commands; `fastapi>=0.115.0` + `uvicorn[standard]>=0.30.0`
  added to deps. Start with `krff serve` → Swagger UI at `http://127.0.0.1:8000/docs`.
- **DuckDB integration — Complete (Session 46).** `src/db.py` (connection factory, parameterized queries
  over parquet); `data_access.py` and `quality.py` migrated to DuckDB internals; no data migration needed.
- Minimal orchestrator: Poll → Normalize → Dedup → Dispatch → Execute → Publish → Log
- SQLite operational state (deferred until M1 needs persistent job/alert state).
  Analytics stays in parquet/DuckDB — operational state in SQLite when activated.

### Multi-user readiness gate (deliberately deferred until DB is integrated)

The current `app.py` is correct and complete for single-analyst use. The following issues
are **not bugs today** but must be resolved before the API serves multiple simultaneous users.
They are structurally solved by the DuckDB + SQLite integration above — listed here for reference.

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
- Once SQLite is the operational layer, `get_status()` and `get_quality()` can read from
  DuckDB views + SQLite state instead of live parquet scans, eliminating both the latency
  and the caching problem.

## Phase 4 — Public Website (ultimate goal)

Institutions consume signals and reports in a familiar web interface.
No code execution required from end users — they read, not operate.

| ID | Description | Status |
|----|-------------|--------|
| W1 | FastAPI backend + frontend shell + legal pages + deploy configs | **Implementation complete (Session 79) — public/paid tier routing added — pending go-live** |
| W2 | Static or server-rendered public website | Planned |
| W3 | Company pages with signal history and report links | Planned |
| W4 | Alert feed with severity levels and source links | Planned |
| W5 | Admin review layer (false-positive flagging, label staging) | Planned |
| W6 | Natural language query interface (DuckDB + LLM → SQL) | Planned |
| W7 | AI-agent-only content model (agents post signals/summaries autonomously) | Planned |

### W1 — Public Demo MVP

Goal: credibility signal for institutional discovery, not traffic.
Success KPI: **1 institutional conversation** (자산운용사 리스크팀, 증권사 리서치팀, or 회계법인 감사팀).

**Implementation complete (Session 78–79).** Files created/modified:
- `app.py` — rewritten: CORS, Jinja2, StaticFiles, TTL cache, async routes, lifespan preload, 8 new web routes; Session 79: `_public_corps`, `_classify_corp()`, index SQL filter, `report_clean` branch, demo corp_name fix
- `static/css/main.css` — extracted from mockup.html
- `templates/` — base, index, demo, about, contact, report_shell, privacy, terms; Session 79: `report_clean.html` (new)
- `deploy/Dockerfile`, `deploy/Caddyfile`, `deploy/krff-api.service`
- `pyproject.toml` — added `jinja2>=3.1.0`, `cachetools>=5.3.0`, `httpx>=0.24.0` (dev); `uv.lock` updated

**Public/paid tier (Session 79):**
- `PUBLIC_CORPS` env var (`.env`) — comma-separated corp_codes for public allowlist; empty = cold-start mode (all flagged companies accessible)
- `_classify_corp()` — routes `/report/{corp_code}` to `report_shell.html` (flagged) or `report_clean.html` (clean) or 404 (not in allowlist)
- User fills in `PUBLIC_CORPS` after curating sample; paid-tier auth middleware is additive over existing `_flagged_corps` universe

**Before go-live checklist (blocking):**

- [ ] **Web3Forms access key** — get from web3forms.com; set as `WEB3FORMS_KEY` env var on server (currently placeholder `YOUR_ACCESS_KEY` in template)
- [ ] **Operator name** — replace `[OPERATOR_NAME]` in `templates/privacy.html` (개인정보 보호책임자 성명)
- [ ] **Contact email** — replace `[CONTACT_EMAIL]` in `templates/base.html`, `privacy.html`, `terms.html`, `contact.html`
- [ ] **Domain name** — replace `krff.example.com` in `deploy/Caddyfile`; update CORS `allow_origins=["*"]` → real domain in `app.py`
- [ ] **Demo companies B and C** — set `DEMO_CORPS` env var with additional corp_codes once legal review is complete (currently only `00550082` 캔버스엔)
- [ ] **Hetzner CX33 provisioning** — create server, install Caddy + systemd, copy `deploy/krff-api.service` and `deploy/Caddyfile`, mount parquet volume
- [ ] **Smoke test** — `curl https://krff.example.com/` returns HTML; `/api/status` returns JSON; `/demo/00550082/report` renders iframe; `/privacy` and `/terms` render

**Non-blocking (post-launch):**
- [ ] Restrict CORS origin from `["*"]` to real domain
- [ ] KSD 별도이용허락 (051-519-1420) — required for commercial SEIBRO use; activates Flags 1 & 2

### W2 — Content Marketing

Platform sequencing: website → video → LinkedIn → Substack/브런치 → institutional outreach.

- [ ] YouTube demo video (KR primary: "대한민국 상장사 1700개를 자동으로 감시하는 시스템"; EN secondary)
- [ ] LinkedIn post with website + video links (institutional discovery)
- [ ] Substack or 브런치 methodology article ("KOSDAQ Accounting Anomaly Study 2019–2024")
- [ ] "Top 20 Anomaly Companies" free sample report (drives inbound; excludes Tier 1 leads)
- [ ] Pilot offer: 3-month free/discounted license → reference case → case study

Design principles:
- Frontend reads only published state from operational DB (atomic publish pattern)
- Public language: "signal", "anomaly", "pattern" — never "fraud confirmed" or "criminal"
- Infrastructure works without AI; AI enhances triage and summarization but does not gate the pipeline

### W6 — Natural Language Query Interface

Goal: Let users query the corpus in plain Korean or English without knowing DuckDB or the parquet schema.

Architecture: User types "Show me everything suspicious about 피씨엘 in 2021" → LLM translates to DuckDB SQL against parquet files → results returned as structured JSON → rendered as evidence packet (signals + citations).

Implementation path:
- DuckDB connection factory already exists in `src/db.py`
- LLM routing: `claude-haiku-4-5` for query classification + SQL generation; `claude-sonnet-4-6` for result synthesis
- Schema context: Pass parquet column names + sample rows in system prompt with `cache_control: ephemeral`
- Safety: SQL is read-only; no write access to parquet; all queries parameterized
- Fallback: If LLM-generated SQL fails validation, return a structured error (not a crash)

### W7 — AI-Agent-Only Content Model

Goal: The public website generates its own content autonomously. Human posts nothing — agents post signals, summaries, and anomaly alerts on a schedule.

Concept: The website is not a blog. It is a continuously updated surveillance feed. A publisher agent runs weekly, checks for new flags (new Beneish threshold crossings, new CB/BW events, new timing anomalies), generates a 200-word summary per new signal in safe language (no fraud allegations, source citations only), and posts it to the website without human review.

Human role: Review and suppress (not review and approve). The adversary/refutation agent runs first — it tries to find benign explanations. If no benign explanation exists, the publisher agent posts. If the adversary agent finds a benign explanation, the signal is held for human review.

**Why this matters:** Collapses the analyst team requirement to zero for routine updates. One person maintains the infrastructure; agents maintain the content. This is the "self-updating intelligence platform" model — the system generates its own distribution.

Multi-agent design (Phase 4 target):
- Ingestion/triage agent — classify relevance, identify corp_code
- Analysis operator agent — call existing scripts via fixed action menu
- QA/validation agent — check output completeness and publish-safety
- Publisher agent — generate website-ready summaries (signal language only)
- Adversary/refutation agent — actively find benign explanations; challenge severity before publication

## Phase 5 — Data Expansion and Spatial Analysis

*Identified March 2026. Not sequenced — depends on Phase 3/4 completion and SEIBRO activation.*

### 5A — Additional Korean Open Datasets

Eight Korean public datasets identified as high-value for pipeline enrichment. See `00_Reference/2_Data/60_Additional_Korean_Open_Datasets.md` for full detail, access paths, and integration priority.

| Dataset | Signal added | Blocking dependency |
|---|---|---|
| FSC enforcement actions | Ground-truth label expansion | None — scraping |
| KONEPS government procurement | Revenue quality, political connection | ID bridge (사업자등록번호) |
| Court insolvency filings | Model validation (ex-post) | None |
| KoTaP tax avoidance panel | Earnings management enrichment | Academic access |
| Korea Customs trade data | Fake export revenue detection | **Commercial contract required** — public APIs aggregate only; no company-level data |
| KRED macro panel | Cyclicality research | None |
| KOSIS regional statistics | Spatial analysis (prerequisite) | Geocoding |
| KOFIA bond data | CB/BW complement | None |

**Key infrastructure prerequisite:** Unified corporate identifier table linking `corp_code` ↔ `stock_code` ↔ `사업자등록번호` ↔ `법인등록번호` ↔ ISIN. The 사업자등록번호 is available in DART company profiles (`company.json`) and can be extracted in bulk.

### 5C — Officer Network Dataset (Standalone)

Publish `officer_network_panel.parquet` as a standalone open dataset: all DART officer holding disclosures, normalized and deduplicated, with entity resolution across name variants. Enables any researcher to reconstruct the officer-company graph without running the full pipeline.

**Value:** No equivalent dataset exists publicly for Korean markets. Cross-company officer tracking is the data primitive underlying the NTS ₩260B manipulation network recovery (March 2026).

### 5D — Historical Ticker Change Dataset

Publish a full history of KOSDAQ/KOSPI ticker changes: relisting events, SPAC mergers, name changes with effective dates. Currently `corp_ticker_map.parquet` has point-in-time data; this extends it to a temporal table.

**Value:** Without ticker change history, any time-series analysis that joins on ticker will silently misattribute data across corporate events. No free Korean equivalent exists. Essential for any researcher doing multi-year event studies on KOSDAQ.

### 5E — Corporate Action Timeline

Publish a normalized corporate action table: CB/BW issuances, rights offerings, stock splits, reverse splits, tender offers — all from DART filings, joined to price/volume data with ±60 day windows. One row per event, normalized event type, consistent date formatting.

**Value:** Currently cb_bw_events.parquet covers CB/BW only. A full corporate action timeline enables any capital markets researcher to run event studies without building the DART extraction layer from scratch.

### 5F — KONEPS Procurement Exposure Dataset

For all KOSDAQ companies with a BRN bridge (extractable from DART company.json), query KONEPS (나라장터) procurement records and publish the results: total government contract value per company per year, contract types, counterparty agencies.

**Value:** Enables fake government revenue detection. A company reporting rapidly growing government revenue that does not appear in KONEPS records is a high-priority anomaly. Requires BRN extraction (one field from DART) + KONEPS API integration.

**Feasibility: CONFIRMED (session 72 research).** Four OpenAPI datasets on data.go.kr, all from 조달청:
- Dataset 15129466 (사용자정보서비스): Supplier registry — BRN (`사업자등록번호`) is first-class query field
- Dataset 15129427 (계약정보서비스): Contract records
- Dataset 15129397 (낙찰정보서비스): Bid results
- Dataset 15129394 (입찰공고정보서비스): Bid announcements

**Blocking dependency:** BRN extraction from DART company.json (zero marginal cost — `bizr_no` field already returned by existing API calls). Verify whether contract/bid endpoints accept BRN as input filter before building extractor.

**Note on customs data:** Korea Customs APIs (data.go.kr) return aggregate statistics only — not company-level records. Company-level trade data requires a commercial contract with the Korea Trade Statistics Promotion Institute. Customs integration is deferred indefinitely.

### 5B — Spatial Analysis Layer

Map accounting anomaly density geographically across Korea.

**Concept:** Geocode company headquarters (from DART registration data), join to KOSIS regional economic indicators, render Beneish flag density by region. Reveals whether manipulation clusters by geography, industry zone, or economic condition — a systemic insight unavailable from company-level analysis alone.

**Stack:** `geopandas` / `shapely` / `pydeck` or `kepler.gl` on top of existing parquet outputs.

**Research questions:**
- Do Seoul tech firms, Busan manufacturing, and Incheon logistics show distinct manipulation profiles?
- Does regional economic stress (unemployment, credit conditions) predict local flag density?
- Are CB/BW anomaly clusters geographically co-located with known regulatory blind spots?

**Prerequisites:** KOSIS regional data integration (Phase 5A), geocoding of DART company addresses.

## Open Backlog

| ID | Description | Phase | Effort |
|---|---|---|---|
| ~~PR5~~ | ~~Historical backfill 2014–2018~~ — **Partial-Complete (Session 50):** 2017–2018 backfill done; 2014–2016 deferred (most issuers resolved). `company_financials.parquet`: 7,042 → 9,310 rows (2019–2023 → 2017–2023). `beneish_scores.parquet`: 7,447 rows, 2018–2023, flagged 1,250. Beneish early-return Marimo bug fixed. | 4 | Medium |
| ~~A1~~ | ~~Automate recurring data refresh~~ — **Complete (Session 38):** `krff refresh` command added to `cli.py`; runs 6 stages in sequence (DART → transform → beneish_screen → cb_bw → timing → network); `--sample N` and `--skip-analysis` flags | 2 | Low |
| I1 | Verify PyKRX from hosted IPs — **Infrastructure ready (Session 49):** `--backend` option added to `krff run`/`krff refresh`; `finance-datareader`+`yfinance` as `[hosted]` optional deps; `test-hosted-backends.yml` workflow_dispatch CI workflow; trigger from GitHub Actions UI to verify | 5 | Low |
| ~~DQ1~~ | ~~XBRL unit-scale corrections~~ — **Complete (Session 48).** frmtrm_amount cross-check confirmed no unit errors. **Session 51:** `BENEISH_EXTREME_OUTLIERS` constant deprecated — replaced by component-level winsorization (Beneish 1999 methodology). Inf values replaced with NaN; 1%/99% per-year winsorization applied to all 8 components. | 1 | Low |

**DQ1 outcome (Session 48):** Cross-checked both flagged companies via DART `frmtrm_amount`. Neither is a unit-scale error:

- **피씨엘 (01051092) 2020:** Genuine COVID-19 diagnostics revenue explosion. SGI=1,499, M-score=1,335 (now winsorized to bounded values).
- **프레스티지바이오로직스 (01258428) 2022/2023:** Genuine revenue volatility. No correction needed.

Extreme values are now handled by per-year 1%/99% component winsorization in `beneish_screen.py`, not by a manual exclusion list.
