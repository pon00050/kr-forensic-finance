# kr-forensic-finance

Korean capital markets data pipeline — public infrastructure for detecting anomalies in KOSPI/KOSDAQ listed companies using exclusively open, publicly available data.

## What This Is

A reproducible ETL pipeline that joins multiple Korean public financial data sources into a unified, queryable dataset, then applies documented screening methodologies to surface companies warranting further investigation.

This project does not investigate specific targets. It builds the infrastructure layer — clean data, documented methodology, reproducible outputs — that researchers, journalists, and regulators can build on.

**Milestone 1 (complete):** Beneish M-Score screen across all KOSDAQ-listed companies, 2019–2023. Outputs a ranked anomaly table with DART filing links.

## What This Is Not

- Not a trading system
- Not legal or investment advice
- Not a whistleblower submission (though outputs may inform one)
- Not comprehensive — it surfaces anomaly signals, not confirmed fraud

## Quickstart

```bash
git clone https://github.com/pon00050/kr-forensic-finance
cd kr-forensic-finance
uv sync                        # uv is the sole package manager; no requirements.txt
cp .env.example .env           # add your DART API key (free: opendart.fss.or.kr)
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023
python 03_Analysis/beneish_screen.py
# outputs: 03_Analysis/beneish_scores.csv
```

**DART API key:** Free registration at [opendart.fss.or.kr](https://opendart.fss.or.kr). No approval required.

**Runtime:** ~2.5–3 hours for the full KOSDAQ universe (2019–2023). The pipeline is resumable — re-running skips already-downloaded files. For a quick smoke test:

```bash
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2021 --end 2023 --sample 5 --sleep 0.1 --max-minutes 3
python 03_Analysis/beneish_screen.py
```

## Script Execution Order

Each script depends on the outputs of the scripts above it. Running out of order will fail with a missing file error.

```
SETUP (once)
│
├── uv sync                                   Install dependencies
└── cp .env.example .env                      Add DART_API_KEY

PIPELINE (run in sequence)
│
├─[1]─ pipeline.py ──────────────────────────  Orchestrates stages 2 and 3 below
│        │
│        ├─[2]─ extract_dart.py ─────────────  Downloads raw financials + sector data
│        │         │                            from OpenDART and WISEindex
│        │         │  writes → 01_Data/raw/financials/{corp_code}_{year}.parquet
│        │         │  writes → 01_Data/raw/sector/wics.parquet
│        │         │  writes → 01_Data/raw/sector/ksic.parquet
│        │         │  writes → 01_Data/raw/company_list.parquet
│        │         │  writes → 01_Data/raw/run_summary.json
│        │         │
│        └─[3]─ transform.py ────────────────  Joins and normalises raw files
│                   │                           into a single analysis-ready table
│                   │  reads  ← 01_Data/raw/  (all of the above)
│                   └  writes → 01_Data/processed/company_financials.parquet

ANALYSIS (run after pipeline)
│
└─[4]─ beneish_screen.py ────────────────────  Computes Beneish M-Score per company-year
          │  reads  ← 01_Data/processed/company_financials.parquet
          │  writes → 01_Data/processed/beneish_scores.parquet
          └  writes → 03_Analysis/beneish_scores.csv

TESTS
│
├─[A]─ pytest test_pipeline_invariants.py ───  No prerequisites — runs any time
│
└─[B]─ pytest test_acceptance_criteria.py ───  Requires steps [3] and [4] to have run
          reads ← 01_Data/processed/company_financials.parquet
          reads ← 01_Data/processed/beneish_scores.parquet
          writes → tests/top50_spot_check.csv
```

**Rules:**
- `extract_dart.py` and `transform.py` are not meant to be called directly — use `pipeline.py`, which handles both in the correct order and propagates `--sample`, `--start`, `--end` flags consistently across both stages.
- `beneish_screen.py` can be re-run at any time after the pipeline without re-running the pipeline — it reads from the already-processed parquet.
- The pipeline is resumable. Re-running `pipeline.py` skips raw files that already exist on disk; only missing company-years are fetched.

## Production Setup (Cloudflare R2 + Oracle Cloud Free Tier)

For running the pipeline without storing data files on your laptop:

1. Create a [Cloudflare R2](https://developers.cloudflare.com/r2/) bucket named `kr-forensic-finance` (free tier covers this project's data volume)
2. Generate an R2 API token (Object Read & Write on the bucket) and add to `.env`:
   ```
   R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_BUCKET=kr-forensic-finance
   ```
3. Provision an [Oracle Cloud Always Free](https://www.oracle.com/cloud/free/) VPS ($0/month — no expiry), clone the repo, run the pipeline via SSH — processed parquet files upload to R2 automatically. See `00_Reference/24_VPS_Setup_Procedure.md` for step-by-step instructions. **Known issue:** PyKRX returns 0 tickers from Oracle Cloud data center IPs (KRX geo-block) — run the full pipeline from a laptop on a residential IP; use the VPS only for R2-read analysis tasks.
4. On your laptop: with R2 credentials in `.env`, `beneish_screen.py` reads directly from R2 — no local `01_Data/` files needed

**R2 is optional.** All scripts fall back to local `01_Data/processed/` when R2 credentials are absent. Existing local dev and smoke test workflows are unchanged.

> **Tip:** If you just want to run the pipeline and explore the data, you don't need any of the above — simply clone the repo on your laptop and run `pipeline.py` directly. The R2 + VPS setup is only useful if you want to keep large data files off your local machine or run the pipeline unattended on a server.

## Data Not Committed

`01_Data/` is gitignored. Users must run the pipeline to generate data. The pipeline downloads directly from OpenDART and KRX public APIs — no data files are distributed with this repository.

## What It Produces

| Output | Location | Description |
|---|---|---|
| `company_financials.parquet` | `01_Data/processed/` | 5-year financial statements, all KOSDAQ companies (7,042 rows) |
| `beneish_scores.parquet` | `01_Data/processed/` | M-Scores with all 8 components, sector percentile, expense method, CFS/OFS provenance (5,357 rows) |
| `beneish_scores.csv` | `03_Analysis/` | Human-readable ranked anomaly table with DART links |
| `dart_xbrl_crosswalk.csv` | `00_Reference/` | XBRL element ID → financial variable mapping; extraction methodology audit trail |

## Limitations and Disclaimer

Outputs are ranked anomaly hypotheses for human review, **not fraud findings**.

- **False positive rate ~40%:** Most flagged companies have legitimate explanations (growth-stage investment, accounting standard transitions, sector norms).
- **Biotech/pharmaceutical structurally scores high:** Elevated SGI, AQI, and DSRI are normal for growth-stage biotech. These companies are flagged separately.
- **Nature-of-expense income statements (~19% of KOSDAQ):** GMI and SGAI cannot be computed for companies filing "성격별 분류" income statements. These ratios are set to 1.0 (neutral) rather than excluding the company.
- **Small-cap data gaps:** ~23% of KOSDAQ companies have no CB/BW history (DART status 013 — expected, not an error).
- **CFS vs. OFS mixing:** ~40–60% of KOSDAQ companies file OFS only. Multi-year trend ratios for companies that switch between consolidated and standalone statements introduce noise; these are flagged in outputs.

## Data Sources

All data used is publicly available and free:

| Source | What it provides |
|---|---|
| OpenDART API (`opendart.fss.or.kr`) | Financial statements, CB/BW issuances, officer holdings, major shareholder changes |
| KRX Data Marketplace (`data.krx.co.kr`) | OHLCV price/volume, short selling balances (Phase 2) |
| SEIBRO (`seibro.or.kr`) | CB/BW issuance terms, conversion/exercise history |
| KFTC Corporate Group Portal (`egroup.go.kr`) | 재벌 cross-shareholding, internal transactions |

## Pipeline Architecture

```
Layer 1 — Fully automated (Python)
  OpenDartReader + PyKRX → ETL (02_Pipeline/) → Parquet/SQLite (01_Data/processed/)
  → Beneish M-Score / CB/BW timeline / timing anomaly / network graph → CSV outputs

Layer 2 — AI-assisted, human-reviewed (Claude API)  [planned]
  Narrative inconsistency detection, entity resolution, disclosure change summary

Layer 3 — Human judgment  [out of scope]
  Materiality assessment, regulatory submission
```

## Analysis Milestones

| # | Script | Status | Description |
|---|---|---|---|
| 1 | `beneish_screen.py` | **Complete** (Feb 2026) | Beneish M-Score per KOSDAQ company-year (threshold -1.78). Full KOSDAQ 2019–2023: 7,042 rows, 5,357 M-Scores. 25 tests pass (18 invariant + 7 AC). |
| 2 | `cb_bw_timelines.py` | Planned | CB/BW issuance → repricing → exercise → price/volume impact chain |
| 3 | `timing_anomalies.py` | Planned | Material disclosure timestamps vs. same-day price/volume movement |
| 4 | `officer_network.py` | Planned | `networkx` graph of individuals appearing across flagged companies |

## Testing

```bash
# Self-contained — run any time, no pipeline data needed
pytest tests/test_pipeline_invariants.py -v

# End-to-end — run after the full pipeline + beneish_screen.py
pytest tests/test_acceptance_criteria.py -v

# Full suite
pytest tests/ -v
```

`test_pipeline_invariants.py` covers five categories: (1) KSIC sample-preservation regression, (2) schema contracts for `company_financials.parquet` including a KI-003 Unicode bug regression guard on `fs_type` distribution, (3) Beneish formula spot-check with hand-verified expected values, (4) `beneish_scores.parquet` output schema contract (all 8 component columns by name), and (5) reference artifact completeness (`dart_xbrl_crosswalk.csv` existence and coverage). `test_acceptance_criteria.py` checks AC1–AC7 from `00_Reference/17_MVP_Requirements.md`. See `00_Reference/21_Test_Suite.md` for full rationale and per-test documentation.

## Folder Structure

```
kr-forensic-finance/
├── README.md
├── KNOWN_ISSUES.md            Known data gaps and mapping limitations
├── pyproject.toml             uv manifest — all deps, requires-python >=3.11
├── .env.example               DART_API_KEY + R2 credentials template
├── 00_Reference/              Context, architecture, verified data source notes
├── 01_Data/
│   ├── raw/                   Downloaded from APIs, unmodified (gitignored)
│   └── processed/             Cleaned, joined, schema-normalized (gitignored)
├── 02_Pipeline/               ETL scripts
│   ├── extract_dart.py        OpenDartReader — financials, CB/BW, officers
│   ├── extract_krx.py         PyKRX — OHLCV, short selling, corp-ticker map (Phase 2; not called by pipeline.py)
│   ├── extract_seibro.py      CB/BW terms + exercise history
│   ├── extract_kftc.py        재벌 cross-shareholding
│   ├── transform.py           raw → company_financials.parquet (Phase 1)
│   └── pipeline.py            CLI orchestrator
├── 03_Analysis/               Screening scripts and outputs
│   ├── beneish_screen.py      Milestone 1 — Beneish M-Score
│   ├── cb_bw_timelines.py     Milestone 2 — CB/BW anomaly scoring
│   ├── timing_anomalies.py    Milestone 3 — disclosure timing
│   └── officer_network.py     Milestone 4 — network graph
└── tests/                     Automated test suite
    ├── conftest.py             pytest path configuration
    ├── test_pipeline_invariants.py   Self-contained unit tests
    └── test_acceptance_criteria.py   End-to-end AC1–AC7 checks
```

## Pipeline Flags

| Flag | Description |
|---|---|
| `--sample N` | Limit to first N companies (smoke testing) |
| `--max-minutes N` | Hard deadline guard; exits cleanly after N minutes |
| `--sleep S` | Inter-request sleep in seconds (default 0.5; use 0.1 for smoke tests) |
| `--force` | Re-download cached files |
| `--stage dart\|transform` | Run a single stage only |
| `--skip-seibro` | Skip SEIBRO scraping |
| `--skip-kftc` | Skip KFTC download |

## Reference Documents

`00_Reference/` contains architecture notes, verified API findings, regulatory context,
and methodology documentation. See [`00_Reference/reference-index.md`](00_Reference/reference-index.md)
for the full annotated index.

**Most important:**
- [`04_Technical_Architecture.md`](00_Reference/04_Technical_Architecture.md) — architecture, milestones, planned schema
- [`18_Research_Findings.md`](00_Reference/18_Research_Findings.md) — confirmed API patterns and DART field mappings
- [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) — known data gaps and interpretation guidance

## Origin

Project initiated February 2026 following the FSC/FSS announcement of the 신고포상금 제도개편 (whistleblower reward system reform, February 25, 2026), which removed caps on rewards for reports of stock price manipulation and accounting fraud.
