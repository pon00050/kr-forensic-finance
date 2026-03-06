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
| `disclosures.parquet` | DART filing listings |
| `major_holders.parquet` | 5%+ ownership threshold filings |
| `bondholder_register.parquet` | CB bondholder names from 사채권자명부 |
| `revenue_schedule.parquet` | Revenue by customer/segment from 매출명세서 |
| `bond_isin_map.parquet` | Bond ISINs per corp_code via FSC API (dataset 15043421); required by SEIBRO StockSvc extractor |

## What's Next

1. **SEIBRO repricing data** — extractor built; ISIN map now populated via FSC API (session 29); SEIBRO API key pending propagation (~days); once key activates, run extractor → re-score CB/BW timelines and officer network
2. **Populate paid-tier tables** — run paid-tier extractors at scale for flagged companies
3. **Statistical analysis layer** — 10 ISL-grade scripts written; S1–S5 complete (session 24); findings in `FINDINGS.md`

## Statistical Analysis — Completed (Session 25)

| ID | Description | Outcome |
|---|---|---|
| S9 | Cross-screen: PC3 top-decile × flagged CB/BW events | **139 double-flagged company-years; 112 unique secondary companies** — all have flag_count=1 (volume_surge only); 0 high-priority secondaries (flag_count≥2 + PC3≥95th); S8 runs at default scope; `double_flagged_companies.csv` produced |
| S8 | Run `extract_depreciation_schedule.py` for 5 Tier 1 leads | All 15 rows = parse_error or no_filing; DART sub_docs keyword matching returns wrong table type for these companies; Category 20 tests flip from 8 skipped → **8 passed**; FINDINGS.md §4 updated with root cause |
| S10a | Extract disclosures for 50 unflagged control companies | `disclosures.parquet` expanded from 8 → **58 corp_codes** (3,581 → 27,486 rows; +23,905 control rows) |
| S10b | Rebuild FDR null from control disclosures × price data | Control null: 2,000 quiet events; **687/687 test events trivially survive BH** — KI-021 diagnosed: pre-filtering makes any clean null give p≈0; valid test requires unfiltered input → S11 |
| S11 | Proper FDR disclosure leakage test (fixes KI-021) | `fdr_disclosure_leakage.py` written; **2/822 events survive BH at q=0.043** — 피씨엘 2021-01-18 (+287%) and 프로브잇 2021-06-14 (+143%); p-value distribution shows mild enrichment near 0 (69 vs 41 expected in [0,0.05)); KI-021 RESOLVED; see `FINDINGS.md` §1 |

## Statistical Analysis — Completed (Session 24)

| ID | Description | Outcome |
|---|---|---|
| S1 | Fix `cluster_peers.py` z-score contamination (KI-020) | **50 cluster-relative flags** (was 0); KI-020 resolved |
| S2 | Investigate 김형석 and 박정우 | Confirmed 4 and 2 flagged companies respectively; no Tier 1 lead overlap; 박정우 confirmed as 전무이사 at 우리기술 with CB acquisition; see `FINDINGS.md` §5a |
| S3 | Redesign FDR null distribution | timing_anomalies.csv pre-filtered (all extreme events); clean null requires full disclosures.parquet join — new blocker documented |
| S4 | PC3 as alternative manipulation screen | 531 top-decile company-years; 6 of 18 Tier 1 lead company-years in top decile; `pca_pc3_scores.csv` output added |
| S5 | Depreciation extractor for Tier 1 leads | `extract_depreciation_schedule.py` written; Category 20 schema test added; ready to run |

## Statistical Analysis — Remaining Action Items

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

## Open Backlog

| ID | Description | Phase | Effort |
|---|---|---|---|
| PR5 | Historical backfill 2014–2018 | 4 | Medium |
| A1 | Automate recurring data refresh | 2 | Low |
| I1 | Verify PyKRX from hosted IPs | 5 | Low |
