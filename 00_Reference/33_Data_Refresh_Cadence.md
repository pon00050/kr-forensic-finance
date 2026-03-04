# 19 — Data Refresh Cadence

> **Scope:** Recommended update frequencies by data type; staleness tolerances; pipeline scheduling guidance.
> **Canonical for:** Data refresh intervals; staleness tolerances.
> **See also:** `02_Data_Sources.md` (source details), `08_Continuous_Monitoring_System.md` (real-time monitoring)

Different data sources have fundamentally different update lifetimes. The pipeline should not blindly re-download everything on each run.

---

## Cadence Table

| Data | Source | Cadence | Rationale |
|---|---|---|---|
| Historical financials (2019–2023) | DART `fnlttSinglAcntAll` | **One-time** | Past annual reports are permanent. Already pulled. Never re-download unless `--force`. |
| New annual reports (2024+) | DART `fnlttSinglAcntAll` | **Annual — run in April/May** | 사업보고서 for fiscal year N must be filed by March 31 of year N+1. Run pipeline for the new year in April or May. |
| Quarterly reports | DART `fnlttSinglAcntAll` (reprt_code 11013/11012/11014) | **Quarterly — August, November, February, May** | 반기보고서 (H1, filed by August 14), 분기보고서 (Q1 May, Q3 November). Phase 2 scope. |
| KOSDAQ company universe | PyKRX `get_market_ticker_list` | **Annual — alongside new-year financial pull** | New listings and delistings. Re-run with `--force` to refresh `company_list.parquet`. |
| WICS sector classification | WISEindex `GetIndexComponets` | **Annual — alongside company universe refresh** | Reclassifications are rare but do happen. |
| KSIC codes | DART `company()` | **On-demand only** | Very stable. No scheduled refresh. Re-pull only when a company's KSIC visibly changes. |
| CB/BW issuances | DART `cvbdIsDecsn` / `bdwtIsDecsn` | **Weekly or event-driven** | New issuances filed as they happen. Key signal for Phase 2 real-time monitoring. |
| Officer holding changes | DART disclosures | **Weekly or event-driven** | 임원·주요주주 특정증권등 소유상황보고서. Filed within 5 business days of change. |
| KRX price/volume (OHLCV) | PyKRX | **Daily (Phase 2)** or **Annual (Phase 1 historical)** | Phase 1 needs historical data only for timing anomaly analysis. Phase 2 needs daily for real-time signals. |
| SEIBRO conversion history | SEIBRO scrape | **Monthly** | Conversion exercise history updates as conversions occur. |
| KFTC cross-shareholding | KFTC bulk download | **Annual — April/May** | Published once per year by KFTC, typically in spring after 사업보고서 season. |

---

## Practical Schedule (Phase 1)

```
May of each year (after 사업보고서 season):
    python pipeline.py --market KOSDAQ --start 2024 --end 2024   # add new fiscal year
    python beneish_screen.py                                      # refresh scores
```

## Practical Schedule (Phase 2 — when CB/BW monitoring is active)

```
Weekly:
    python pipeline.py --stage dart --data-type cb_bw
    python pipeline.py --stage dart --data-type officers

Daily:
    python pipeline.py --stage krx --data-type ohlcv
```

---

## Refresh Manifest (Phase 2 scope — not yet implemented)

To support incremental refresh, the pipeline will write a lightweight manifest to `01_Data/raw/refresh_manifest.json` that records what was last downloaded and when:

```json
{
  "financials": {"last_year": 2023, "pulled_at": "2025-05-01"},
  "company_list": {"last_pulled_at": "2025-05-01"},
  "wics": {"last_pulled_at": "2025-05-01"},
  "cb_bw": {"last_pulled_at": "2025-05-01"},
  "kftc": {"last_pulled_at": "2025-04-15"}
}
```

The manifest allows the pipeline to skip data types that are already current and only fetch what has changed. It will be uploaded to R2 alongside the processed parquet files so the VPS and laptop stay in sync.

The `--data-type` flag required to support per-source refresh is **Phase 2 scope**. Document the cadence now; implement the mechanism when Phase 2 begins.

---

## Design Implication for VPS Runs

Because historical financials are one-time pulls, the full pipeline run (~2.5–3 hrs) only needs to happen once per new fiscal year added. Subsequent runs for CB/BW and officer monitoring are fast (single data type, ~minutes).

Raw files on the VPS are local-only cache and can be wiped after each run — only the processed parquet outputs go to R2 and persist.
