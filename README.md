# krff-shell

![Python](https://img.shields.io/badge/python-3.11+-blue)
![Tests](https://github.com/pon00050/krff-shell/actions/workflows/test.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-green)

> Previously known as **kr-forensic-finance**. Split into kr-dart-pipeline, kr-anomaly-scoring, kr-stat-tests, kr-forensic-core, and krff-shell in March 2026.

Public infrastructure for systematic anomaly screening across Korean listed companies — built entirely on open data.

공개된 데이터만으로 한국 상장사의 이상 징후를 체계적으로 스크리닝하는 오픈 인프라입니다.

## Purpose / 이 프로젝트를 만든 이유

Korea's public disclosure system (DART) contains the full footprint of documented capital markets manipulation schemes: CB/BW issuances, conversion repricing, officer holding changes, false 신사업 (new business line) announcements, and the price/volume patterns that follow. The data exists. The patterns are documented. What doesn't exist publicly is a reproducible pipeline that joins these sources and surfaces companies warranting investigation.

This project builds that infrastructure layer — so that researchers, journalists, analysts, and regulators don't each have to rebuild it from scratch.

---

한국의 공시 시스템(DART)에는 자본시장 조작 패턴의 흔적이 고스란히 남아 있습니다. CB/BW 발행, 전환가액 조정, 임원 보유 주식 변동, 허위 신사업 공시, 그리고 뒤따르는 주가·거래량의 비정상적 움직임까지. 데이터는 이미 있고, 패턴도 문서화되어 있습니다. 없었던 건 이 데이터를 하나로 엮어 조사 우선순위를 뽑아내는 재현 가능한 파이프라인이었습니다.

이 프로젝트는 바로 그 인프라 레이어를 만듭니다 — 연구자, 저널리스트, 애널리스트, 규제기관 누구든 처음부터 새로 만들 필요 없이 바로 쓸 수 있도록.

## Current State / 현재 상태

**Milestones 1–4 complete. All four analysis milestones runnable. FastAPI web/API layer and MCP server added.**

**마일스톤 1–4 완료. 4개 마일스톤 모두 실행 가능. FastAPI 웹/API 레이어 및 MCP 서버 추가.**

| Output | Location | EN | 한국어 |
|---|---|---|---|
| `beneish_scores.csv` | `03_Analysis/` | Ranked anomaly table with DART links — main deliverable | DART 링크 포함 이상 징후 순위표 — 주요 산출물 |
| `beneish_scores.parquet` | `01_Data/processed/` | All 8 M-Score components, 2018–2023, sector percentiles, CFS/OFS provenance | M-Score 8개 구성 요소, 2018–2023, 섹터 백분위, CFS/OFS 출처 |
| `company_financials.parquet` | `01_Data/processed/` | 2017–2023 financials, all KOSDAQ companies | 2017–2023 재무제표, 코스닥 전 상장사 |
| `cb_bw_events.parquet` | `01_Data/processed/` | CB/BW issuance events — 11 cols including issue_amount, refixing_floor, maturity_date, board_date, warrant_separable | CB/BW 발행 이벤트 — 발행금액·리픽싱하한·만기일·이사회일·분리형 여부 포함 11개 컬럼 |
| `price_volume.parquet` | `01_Data/processed/` | OHLCV price/volume windows around CB/BW events | CB/BW 이벤트 전후 OHLCV 주가/거래량 |
| `corp_ticker_map.parquet` | `01_Data/processed/` | corp_code ↔ ticker mapping | corp_code ↔ 종목코드 매핑 |
| `officer_holdings.parquet` | `01_Data/processed/` | Officer holding changes | 임원 보유 주식 변동 |
| `disclosures.parquet` | `01_Data/processed/` | 271,504 DART filings across 921 corps — wired into pipeline automatically | DART 공시 목록 271,504건, 921개사 — 파이프라인에 자동 연결 |
| `major_holders.parquet` | `01_Data/processed/` | 5%+ ownership threshold filings from DART majorstock.json | 대량보유상황보고서 — 5% 이상 지분 신고 이력 |
| `bondholder_register.parquet` | `01_Data/processed/` | CB bondholder names and face values from 사채권자명부 sub-documents | CB 사채권자명부 — 권리자명·채권금액 |
| `revenue_schedule.parquet` | `01_Data/processed/` | Revenue by customer/segment from 매출명세서 in 사업보고서 | 매출명세서 — 고객·품목별 매출 |
| `bond_isin_map.parquet` | `01_Data/processed/` | 1,859 CB/BW ISINs mapped to 656 corp_codes via FSC API | FSC API로 수집한 CB/BW ISIN 1,859건 — 656개사 연결 |
| `dart_xbrl_crosswalk.csv` | `tests/fixtures/` | XBRL element → variable mapping; audit trail | XBRL 요소 → 재무 변수 매핑; 감사 추적 |
| [`beneish_viz.html` ↗](https://raw.githack.com/pon00050/krff-shell/master/03_Analysis/beneish_viz.html) | `03_Analysis/` | Self-contained visual summary of Phase 1 results (5 Plotly charts) | Phase 1 결과 시각적 요약 — 5개 Plotly 차트, 단독 실행 가능 HTML |
| `<corp_code>_report.html` | `03_Analysis/reports/` | Per-company forensic HTML report (all 4 milestones + AI synthesis) | 기업별 포렌식 HTML 보고서 |

**Visual summary (no Python required):** [beneish_viz.html — Phase 1 결과 보기](https://raw.githack.com/pon00050/krff-shell/master/03_Analysis/beneish_viz.html) — interactive Plotly charts, no Python required. / Python 없이 바로 보기.

## Quickstart / 빠르게 시작하기

```bash
git clone https://github.com/pon00050/krff-shell
cd krff-shell
uv sync                        # production dependencies
uv sync --extra dev            # + dev/test dependencies (needed to run tests)
cp .env.example .env           # add DART API key / DART API 키 입력 (free / 무료: opendart.fss.or.kr)
```

**Option A — `krff` CLI:**

| Command | Description |
|---|---|
| `krff run` | ETL pipeline — DART extraction + transform |
| `krff refresh` | Full pipeline + all 4 analysis milestones in one command |
| `krff analyze` | Print beneish_scores.parquet summary |
| `krff charts` | Write 03_Analysis/beneish_viz.html |
| `krff status` | Artifact inventory (rows, sizes, dates) |
| `krff status -v` | + DART run summary |
| `krff quality` | Data quality metrics (null rates, coverage) |
| `krff audit` | Pipeline freshness check — exits 1 if stale |
| `krff stats` | Run stale statistical tests (14-node DAG) |
| `krff report <corp_code>` | Generate per-company HTML report |
| `krff batch_report` | Generate reports for all flagged companies |
| `krff serve` | Start FastAPI + MCP server on :8000 |
| `krff queue` / `krff surface` / `krff hide` | Review queue management |
| `krff version` | Print version |
| `krff --help` | List all commands |

```bash
# Typical first run
krff audit                     # check pipeline freshness before running
krff run --market KOSDAQ --start 2019 --end 2023
python 03_Analysis/beneish_screen.py   # compute M-scores → beneish_scores.parquet
krff analyze                   # print score summary
krff charts                    # write 03_Analysis/beneish_viz.html
```

**MCP server (for Claude Code / Claude Desktop):**
```bash
krff serve                     # starts REST API + MCP at http://127.0.0.1:8000
# Claude Code auto-connects via .mcp.json (included in repo)
# Or add manually:
claude mcp add --transport http kr-financial-statements http://localhost:8000/mcp/
```

**Option B — direct scripts (unchanged):**
```bash
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023
python 03_Analysis/beneish_screen.py
# output / 출력 → 03_Analysis/beneish_scores.csv
```

**DART API key:** Free at [opendart.fss.or.kr](https://opendart.fss.or.kr). No approval required.
**DART API 키:** [opendart.fss.or.kr](https://opendart.fss.or.kr)에서 무료 발급. 별도 심사 없음.

**Runtime:** Resumable — re-running skips already-downloaded files.
**실행 시간:** 재시작 가능하며 이미 받은 파일은 건너뜁니다.

**Smoke test (5 companies, ~3 min):**
```bash
krff refresh --sample 5 --sleep 0.1 --max-minutes 3
```

## Limitations and Disclaimer / 한계 및 면책 고지

Outputs are ranked anomaly hypotheses for human review, **not fraud findings**.
출력물은 사람이 직접 검토해야 할 **이상 징후 가설**입니다.

- **False positives expected / 위양성 존재:** Most flagged companies have legitimate explanations (growth-stage investment, accounting transitions, sector norms). 플래그된 기업 대부분은 정당한 이유가 있습니다.
- **Biotech/pharma scores high / 바이오·제약은 구조적 고점수:** Elevated SGI, AQI, DSRI normal for growth-stage biotech; flagged separately. 성장 단계 바이오의 SGI, AQI, DSRI가 높은 건 정상이며, 별도 분류됩니다.
- **Nature-of-expense filers / 성격별 분류:** Some companies cannot compute GMI and SGAI; set to 1.0 (neutral). 일부 기업은 GMI·SGAI 산출 불가, 1.0(중립) 처리.
- **Small-cap gaps / 소형주 공백:** Some companies have no CB/BW history (DART status 013 — expected). 일부 기업은 CB/BW 이력 없음 (오류 아님).
- **CFS vs. OFS mixing / 연결·별도 혼재:** Many companies file OFS only; switching introduces noise, flagged in outputs. 별도만 제출하는 기업이 많으며, 전환 기업은 노이즈 발생, 플래그 표시.

**Outputs are not investment advice, legal opinion, or conclusions about any specific company.**
**이 프로젝트의 출력물은 투자 조언, 법률 의견, 또는 특정 기업에 대한 결론이 아닙니다.**

## Data Sources / 데이터 출처

All data is publicly available and free. 사용된 데이터는 모두 무료로 공개된 자료 입니다.

| Source / 출처 | EN | 한국어 |
|---|---|---|
| OpenDART API (`opendart.fss.or.kr`) | Financial statements, CB/BW issuances, officer holdings, major shareholder changes | 재무제표, CB/BW 발행, 임원 보유 주식, 주요 주주 변동 |
| KRX (`data.krx.co.kr`) | OHLCV price/volume, short selling balances (Phase 2) | OHLCV 주가/거래량, 공매도 잔고 (Phase 2) |
| SEIBRO (`seibro.or.kr`) | CB/BW issuance terms, conversion/exercise history | CB/BW 발행 조건, 전환/행사 이력 |
| KFTC (`egroup.go.kr`) | 재벌 cross-shareholding, internal transactions | 재벌 내부 순환출자, 내부 거래 현황 |

---

## For Developers

See also: [CONTRIBUTING.md](CONTRIBUTING.md) · [ROADMAP.md](ROADMAP.md)

### Web API + MCP Server

`krff serve` starts a FastAPI application at `http://localhost:8000` that exposes both a REST API and an MCP (Model Context Protocol) server — a standard interface that lets Claude and other AI clients call tools directly against live pipeline data.

**REST API (`http://localhost:8000`):**

| Endpoint | Description |
|---|---|
| `GET /api/status` | Artifact inventory (same as `krff status`) |
| `GET /api/quality` | Data quality metrics |
| `GET /api/companies/{corp_code}/summary` | All signals for one company |
| `GET /api/companies/{corp_code}/report` | Trigger per-company HTML report generation |
| `GET /api/alerts` | Recent anomaly alerts |
| `GET /api/monitor/status` | Monitor pipeline health |

Swagger UI at `/docs`. Web routes at `/`, `/demo`, `/about`, `/datasets`, `/contact`, `/privacy`, `/terms`.

**MCP Server (`http://localhost:8000/mcp/`):**

10 tools are available to any MCP-compatible client (Claude Code, Claude Desktop, etc.):

| Tool | Description |
|---|---|
| `lookup_corp_code` | Name or ticker → corp_code. **Call this first** — all other tools require corp_code. |
| `get_company_summary` | All signals for one company in one call |
| `get_beneish_scores` | M-Score history with all 8 components (2018–2023) |
| `get_cb_bw_events` | CB/BW events with manipulation flag counts |
| `get_price_volume` | OHLCV for a date range (paginated) |
| `get_officer_holdings` | Officer holding changes from DART |
| `get_timing_anomalies` | Disclosure timing vs. price/volume anomalies |
| `get_major_holders` | 5%+ block-holding filings (대량보유) |
| `get_officer_network` | Cross-company officer centrality |
| `search_flagged_companies` | Ranked anomaly screen by M-Score (paginated) |

Connect from Claude Code:
```bash
krff serve   # must be running first
# Auto-connects via .mcp.json (included in repo)
# Or manually: claude mcp add --transport http kr-financial-statements http://localhost:8000/mcp/
```

Once connected, ask questions in plain language — Claude calls the right tools, joins
results across datasets, and returns a synthesized answer. No SQL, no Excel, no manual
cross-referencing of DART filings.

See **[MCP_EXAMPLES.md](MCP_EXAMPLES.md)** for worked examples including multi-signal
cross-table screens, price history investigation, and a full query reference.

See **[MCP_DAY_IN_LIFE.md](MCP_DAY_IN_LIFE.md)** for a side-by-side comparison of the
traditional Excel/DART workflow vs. MCP for a realistic analyst screening task (~2.5 hours
→ ~4 minutes).

### Folder Structure

```
krff-shell/
├── README.md
├── CONTRIBUTING.md
├── ROADMAP.md
├── LICENSE
├── pyproject.toml
├── cli.py                         krff CLI entry point
├── app.py                         FastAPI app — REST API + MCP server (krff serve)
├── .mcp.json                      MCP client config (Claude Code / Claude Desktop)
├── .env.example                   API keys + optional cloud storage template
├── 00_Reference/                  Local reference docs (not committed)
├── 01_Data/
│   ├── raw/                       From APIs, unmodified (gitignored)
│   └── processed/                 Cleaned, joined (gitignored)
├── 02_Pipeline/
│   ├── pipeline.py                CLI orchestrator — start here
│   ├── extract_dart.py            OpenDartReader — financials, sector codes
│   ├── extract_cb_bw.py           DART DS005 — CB/BW issuance events
│   ├── extract_corp_ticker_map.py corp_code ↔ ticker mapping
│   ├── extract_price_volume.py    KRX/FDR/yfinance OHLCV
│   ├── extract_officer_holdings.py DART officer holding changes
│   ├── extract_disclosures.py     DART filing listings (list.json)
│   ├── extract_major_holders.py   DART majorstock.json → major_holders.parquet
│   ├── extract_bondholder_register.py  DART sub_docs → 사채권자명부 HTML parse
│   ├── extract_revenue_schedule.py     DART sub_docs → 매출명세서 HTML parse
│   ├── extract_seibro_repricing.py SEIBRO CB/BW repricing via data.go.kr API
│   ├── _pipeline_helpers.py       Shared utilities (API key, corp_code normalization, HTML parsers)
│   ├── extract_krx.py             KRX short selling balances
│   ├── extract_kftc.py            KFTC cross-shareholding
│   └── transform.py               raw → company_financials.parquet
├── 03_Analysis/
│   ├── beneish_screen.py          Milestone 1 — Beneish M-Score
│   ├── beneish_viz.py             Visual summary → beneish_viz.html
│   ├── beneish_viz.html           Generated output — open in any browser
│   ├── phase1_research_questions.py  Open analytical threads from Phase 1
│   ├── cb_bw_timelines.py         Milestone 2 — CB/BW event chains (Marimo app; run via run_cb_bw_timelines.py)
│   ├── timing_anomalies.py        Milestone 3 — Disclosure timing (Marimo app; run via run_timing_anomalies.py)
│   ├── officer_network.py         Milestone 4 — Officer graph (Marimo app; run via run_officer_network.py)
│   ├── run_cb_bw_timelines.py     Standalone runner → cb_bw_summary.csv
│   ├── run_timing_anomalies.py    Standalone runner → timing_anomalies.csv
│   ├── run_officer_network.py     Standalone runner → centrality_report.csv
│   ├── _scoring.py                Shared CB/BW scoring logic
│   ├── statistical_tests/         10 statistical validation scripts (local only)
│   ├── officer_network/           Output: centrality_report.csv
│   ├── reports/                   Generated per-company HTML reports (gitignored)
│   └── company_dives/             Per-company forensic scripts (local only, not committed)
├── krff/
│   ├── __init__.py
│   ├── _paths.py                  Centralized path constants
│   ├── analysis.py                Beneish screen wrapper
│   ├── audit.py                   Pipeline freshness DAG (krff audit)
│   ├── charts.py                  Plotly chart generation
│   ├── constants.py               Threshold + flag literals
│   ├── data_access.py             Parquet/CSV loader layer
│   ├── db.py                      DuckDB connection factory
│   ├── mcp_server.py              FastMCP server — 10 tools
│   ├── mcp_utils.py               JSON serialization helpers
│   ├── models.py                  Pydantic response models
│   ├── pipeline.py                Pipeline wrapper for CLI/API
│   ├── quality.py                 Data quality metrics (krff quality)
│   ├── report.py                  Per-company HTML report generator
│   ├── review.py                  Visibility queue (public/paid tiers)
│   ├── stats_runner.py            14-node STATS_DAG orchestrator
│   └── status.py                  Artifact inventory (krff status)
└── tests/
    ├── conftest.py                Shared fixtures (sys.path setup)
    ├── test_pipeline_invariants.py Schema/logic tests (run any time)
    ├── test_acceptance_criteria.py End-to-end checks (after pipeline)
    ├── test_cli.py                CLI smoke tests
    ├── test_e2e_synthetic.py      End-to-end tests with synthetic data
    ├── test_mcp_server.py         MCP tool tests (skip when parquets absent)
    └── top50_spot_check.csv       Spot-check reference data
```

### How the Scripts Fit Together

`pipeline.py` orchestrates `extract_dart.py` → `transform.py` in sequence. Don't call them directly — `pipeline.py` propagates flags (`--sample`, `--start`, `--end`) consistently. After the pipeline finishes, run `beneish_screen.py` separately. The pipeline is resumable: re-running skips files already on disk.

### Standalone Paid-Tier Extractors

Three scripts fetch deeper confirmation data for flagged companies. They are **not run by `pipeline.py`** — invoke them directly after the main pipeline has populated `cb_bw_events.parquet` and `beneish_scores.parquet`.

| Script | Output | What it fetches |
|---|---|---|
| `extract_major_holders.py` | `major_holders.parquet` | 대량보유상황보고서 — full 5%+ ownership threshold filing history per company |
| `extract_bondholder_register.py` | `bondholder_register.parquet` | 사채권자명부 — CB bondholder names and face values from DART sub-documents |
| `extract_revenue_schedule.py` | `revenue_schedule.parquet` | 매출명세서 — revenue by customer/segment from 사업보고서 |

```bash
# Major holders — wired into pipeline; also runnable standalone
python 02_Pipeline/extract_major_holders.py --sample 20

# Bondholder register — target specific companies by corp_code
python 02_Pipeline/extract_bondholder_register.py --corp-codes <corp_code1>,<corp_code2>

# Revenue schedule — defaults to beneish-flagged companies (m_score > -1.78)
python 02_Pipeline/extract_revenue_schedule.py --corp-codes <corp_code1>,<corp_code2> --years 2021,2022,2023
```

All three support `--force`, `--sample N`, `--sleep S`, `--max-minutes M`. HTML sub-documents are cached to `01_Data/raw/dart/` so re-runs skip already-fetched filings.

### Pipeline Flags

| Flag | Description |
|---|---|
| `--sample N` | Limit to first N companies (smoke testing) |
| `--max-minutes N` | Hard deadline guard; exits cleanly after N minutes |
| `--sleep S` | Inter-request sleep in seconds (default 0.5; use 0.1 for smoke tests) |
| `--force` | Extract stage: re-fetch raw files (company_list.parquet, wics.parquet, etc.). Transform stage: delete and rebuild `company_financials.parquet`. |
| `--stage dart\|transform\|cb_bw\|beneish\|analysis` | Run a single stage only (default: dart + transform) |

### Testing

```bash
uv run python -m pytest tests/ -v                                               # 317 tests
uv run python -m pytest tests/test_pipeline_invariants.py tests/test_e2e_synthetic.py -v  # no pipeline data needed
```

Data-dependent tests auto-skip on CI (no parquets present); all 317 tests run locally after a full pipeline run.

### Output Schemas

**`beneish_scores.csv`** (main deliverable, `03_Analysis/`)

| Column | Description |
|---|---|
| `corp_code` | DART 8-digit company identifier |
| `ticker` | KRX 6-digit ticker |
| `company_name` | Korean company name |
| `year` | Fiscal year (score period; e.g. 2023 uses 2022–2023 data) |
| `m_score` | Beneish M-Score (8-variable; threshold: −1.78) |
| `flag` | `True` if M-Score > −1.78 (possible manipulator) |
| `risk_tier` | `"Critical"` / `"High"` / `"Medium"` / `"Low"` based on score range |
| `high_fp_risk` | `True` for biotech/pharma (structural false positive risk) |
| `wics_sector` | WICS sector name (e.g. "건강관리", "IT") |
| `sector_percentile` | Company's M-Score percentile within its WICS sector |
| `dart_link` | Direct URL to company's annual report on DART |
| `extraction_date` | Date this row's data was extracted from DART |

**`top50_spot_check.csv`** (`tests/`) — Top 50 companies by M-Score with `corp_code`, `ticker`, `company_name`, `year`, `m_score`, `flag`.

### Generating Per-Company Reports

```bash
krff report 01051092              # → 03_Analysis/reports/01051092_report.html
krff report 01051092 --skip-claude  # skip AI synthesis (no API key needed)
krff batch_report                 # generate reports for all flagged companies
```

Reports are self-contained HTML files. Run pipeline and analysis scripts first, then generate:

```bash
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2021 --end 2023
python 03_Analysis/beneish_screen.py
python 03_Analysis/run_cb_bw_timelines.py
python 03_Analysis/run_timing_anomalies.py
python 03_Analysis/run_officer_network.py
krff report <corp_code>
```

Set `ANTHROPIC_API_KEY` in `.env` to enable the AI synthesis section (claude-sonnet-4-6).

**Data coverage:** Reports reflect 2017–2023 data. Re-run the pipeline to update.

### Further Reading

Architecture notes, API research findings, and methodology documentation are maintained locally in `00_Reference/` (not committed to the public repository). Clone the repo and run the pipeline — the code is self-documenting.

S3-compatible cloud storage is optional — all scripts fall back to local files.
