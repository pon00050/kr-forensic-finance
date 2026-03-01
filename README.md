# kr-forensic-finance

![Python](https://img.shields.io/badge/python-3.11+-blue)
![Tests](https://img.shields.io/badge/tests-44%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

Public infrastructure for systematic anomaly screening across Korean listed companies — built entirely on open data.

공개된 데이터만으로 한국 상장사의 이상 징후를 체계적으로 스크리닝하는 오픈 인프라입니다.

## Purpose / 이 프로젝트를 만든 이유

Korea's public disclosure system (DART) contains the full footprint of documented capital markets manipulation schemes: CB/BW issuances, conversion repricing, officer holding changes, false 신사업 (new business line) announcements, and the price/volume patterns that follow. The data exists. The patterns are documented. What doesn't exist publicly is a reproducible pipeline that joins these sources and surfaces companies warranting investigation.

This project builds that infrastructure layer — so that researchers, journalists, analysts, and regulators don't each have to rebuild it from scratch.

---

한국의 공시 시스템(DART)에는 자본시장 조작 패턴의 흔적이 고스란히 남아 있습니다. CB/BW 발행, 전환가액 조정, 임원 보유 주식 변동, 허위 신사업 공시, 그리고 뒤따르는 주가·거래량의 비정상적 움직임까지. 데이터는 이미 있고, 패턴도 문서화되어 있습니다. 없었던 건 이 데이터를 하나로 엮어 조사 우선순위를 뽑아내는 재현 가능한 파이프라인이었습니다.

이 프로젝트는 바로 그 인프라 레이어를 만듭니다 — 연구자, 저널리스트, 애널리스트, 규제기관 누구든 처음부터 새로 만들 필요 없이 바로 쓸 수 있도록.

## Current State / 현재 상태

**Milestone 1 is complete:** Beneish M-Score screen across all KOSDAQ-listed companies, 2019–2023.

**마일스톤 1 완료:** 코스닥 전 상장사 대상 Beneish M-Score 스크린, 2019–2023년.

| Output | Location | EN | 한국어 |
|---|---|---|---|
| `beneish_scores.csv` | `03_Analysis/` | Ranked anomaly table with DART links — main deliverable | DART 링크 포함 이상 징후 순위표 — 주요 산출물 |
| `beneish_scores.parquet` | `01_Data/processed/` | All 8 M-Score components, sector percentiles, CFS/OFS provenance (5,357 rows) | M-Score 8개 구성 요소, 섹터 백분위, CFS/OFS 출처 (5,357행) |
| `company_financials.parquet` | `01_Data/processed/` | 5-year financials, all KOSDAQ companies (7,042 rows) | 5개년 재무제표, 코스닥 전 상장사 (7,042행) |
| `dart_xbrl_crosswalk.csv` | `00_Reference/` | XBRL element → variable mapping; audit trail | XBRL 요소 → 재무 변수 매핑; 감사 추적 |
| [`beneish_viz.html` ↗](https://raw.githack.com/pon00050/kr-forensic-finance/main/03_Analysis/beneish_viz.html) | `03_Analysis/` | Self-contained visual summary of Phase 1 results (5 Plotly charts) | Phase 1 결과 시각적 요약 — 5개 Plotly 차트, 단독 실행 가능 HTML |

**Visual summary (no Python required):** [beneish_viz.html — Phase 1 결과 보기](https://raw.githack.com/pon00050/kr-forensic-finance/main/03_Analysis/beneish_viz.html) — interactive Plotly charts, no Python required. / Python 없이 바로 보기.

**Open analytical questions from Phase 1:** [`00_Reference/27_Phase1_Analytical_Research_Questions.md`](00_Reference/27_Phase1_Analytical_Research_Questions.md) — four investigative threads pursuable with existing data, no pipeline advancement required: component co-occurrence patterns among Critical flags, genuine vs. structurally-elevated sectors, repeat offenders across years, and CFS/OFS basis effects on flag distribution.

Planned (not yet implemented): CB/BW timelines, disclosure timing anomalies, officer network graph.

향후 계획 (미구현): CB/BW 타임라인, 공시 시점 이상 징후, 플래그 기업 간 인물 네트워크 그래프.

## Quickstart / 빠르게 시작하기

```bash
git clone https://github.com/pon00050/kr-forensic-finance
cd kr-forensic-finance
uv sync
cp .env.example .env           # add DART API key / DART API 키 입력 (free / 무료: opendart.fss.or.kr)
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023
python 03_Analysis/beneish_screen.py
# output / 출력 → 03_Analysis/beneish_scores.csv
```

**DART API key:** Free at [opendart.fss.or.kr](https://opendart.fss.or.kr). No approval required.
**DART API 키:** [opendart.fss.or.kr](https://opendart.fss.or.kr)에서 무료 발급. 별도 심사 없음.

**Runtime:** ~2.5–3 hours for the full KOSDAQ universe (2019–2023). Resumable — re-running skips already-downloaded files.
**실행 시간:** 코스닥 전체 약 2.5–3시간. 재시작 가능하며 이미 받은 파일은 건너뜁니다.

**Smoke test (5 companies, ~3 min):**
```bash
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2021 --end 2023 --sample 5 --sleep 0.1 --max-minutes 3
python 03_Analysis/beneish_screen.py
```

## Limitations and Disclaimer / 한계 및 면책 고지

Outputs are ranked anomaly hypotheses for human review, **not fraud findings**.
출력물은 사람이 직접 검토해야 할 **이상 징후 가설**입니다.

- **False positive rate ~40% / 위양성 약 40%:** Most flagged companies have legitimate explanations (growth-stage investment, accounting transitions, sector norms). 플래그된 기업 대부분은 정당한 이유가 있습니다.
- **Biotech/pharma scores high / 바이오·제약은 구조적 고점수:** Elevated SGI, AQI, DSRI normal for growth-stage biotech; flagged separately. 성장 단계 바이오의 SGI, AQI, DSRI가 높은 건 정상이며, 별도 분류됩니다.
- **Nature-of-expense filers ~19% / 성격별 분류 ~19%:** GMI and SGAI cannot be computed; set to 1.0 (neutral). GMI·SGAI 산출 불가, 1.0(중립) 처리.
- **Small-cap gaps / 소형주 공백:** ~23% have no CB/BW history (DART status 013 — expected). 약 23%는 CB/BW 이력 없음 (오류 아님).
- **CFS vs. OFS mixing / 연결·별도 혼재:** 40–60% file OFS only; switching introduces noise, flagged in outputs. 40–60%가 별도만 제출, 전환 기업은 노이즈 발생, 플래그 표시.

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

### Folder Structure

```
kr-forensic-finance/
├── README.md
├── KNOWN_ISSUES.md            Known data gaps and mapping limitations
├── pyproject.toml             uv manifest — requires-python >=3.11
├── .env.example               DART_API_KEY + R2 credentials template
├── 00_Reference/              Context, architecture, data source notes
├── 01_Data/
│   ├── raw/                   From APIs, unmodified (gitignored)
│   └── processed/             Cleaned, joined (gitignored)
├── 02_Pipeline/
│   ├── pipeline.py            CLI orchestrator — start here
│   ├── extract_dart.py        OpenDartReader — financials, CB/BW, officers
│   ├── transform.py           raw → company_financials.parquet
│   └── ...                    extract_krx, extract_seibro, extract_kftc
├── 03_Analysis/
│   ├── beneish_screen.py      Milestone 1 — Beneish M-Score
│   ├── beneish_viz.py         Visual summary — 5 Plotly charts → beneish_viz.html
│   └── beneish_viz.html       Generated output — open in any browser
└── tests/
    ├── test_pipeline_invariants.py   Self-contained (run any time)
    └── test_acceptance_criteria.py   End-to-end (run after pipeline)
```

### How the Scripts Fit Together

`pipeline.py` orchestrates `extract_dart.py` → `transform.py` in sequence. Don't call them directly — `pipeline.py` propagates flags (`--sample`, `--start`, `--end`) consistently. After the pipeline finishes, run `beneish_screen.py` separately. The pipeline is resumable: re-running skips files already on disk.

### Pipeline Flags

| Flag | Description |
|---|---|
| `--sample N` | Limit to first N companies (smoke testing) |
| `--max-minutes N` | Hard deadline guard; exits cleanly after N minutes |
| `--sleep S` | Inter-request sleep in seconds (default 0.5; use 0.1 for smoke tests) |
| `--force` | Extract stage: re-fetch raw files (company_list.parquet, wics.parquet, etc.). Transform stage: delete and rebuild `company_financials.parquet`. |
| `--stage dart\|transform` | Run a single stage only |

### Testing

```bash
pytest tests/test_pipeline_invariants.py -v   # No pipeline data needed
pytest tests/test_acceptance_criteria.py -v   # After full pipeline + beneish_screen.py
```

See `00_Reference/21_Test_Suite.md` for per-test documentation.

### Output Schemas

**`beneish_scores.csv`** (main deliverable, `03_Analysis/`)

| Column | Description |
|---|---|
| `corp_code` | DART 8-digit company identifier |
| `ticker` | KRX 6-digit ticker |
| `company_name` | Korean company name |
| `year_t` | Fiscal year (score period; e.g. 2023 uses 2022–2023 data) |
| `m_score` | Beneish M-Score (8-variable; threshold: −1.78) |
| `flag` | `True` if M-Score > −1.78 (possible manipulator) |
| `risk_tier` | `"high"` / `"medium"` / `"low"` based on score range |
| `high_fp_risk` | `True` for biotech/pharma (structural false positive risk) |
| `wics_sector` | WICS sector name (e.g. "건강관리", "IT") |
| `sector_percentile` | Company's M-Score percentile within its WICS sector |
| `dart_link` | Direct URL to company's annual report on DART |
| `extraction_date` | Date this row's data was extracted from DART |

Full schema spec: [`00_Reference/17_MVP_Requirements.md §4.6`](00_Reference/17_MVP_Requirements.md)

**`top50_spot_check.csv`** (`tests/`) — Top 50 companies by M-Score with `corp_code`, `ticker`, `company_name`, `year_t`, `m_score`, `flag`.

### Further Reading

`00_Reference/` has architecture notes, API findings, and methodology docs. See [`reference-index.md`](00_Reference/reference-index.md) for the full index.

- **[Technical Architecture](00_Reference/04_Technical_Architecture.md)** — pipeline diagram, unified schema, four milestones, tech stack
- **[Research Findings](00_Reference/18_Research_Findings.md)** — verified API behaviors, confirmed bugs, workarounds
- **[Pipeline Details](00_Reference/pipeline-details.md)** — all CLI flags, stage descriptions, resumability

For remote execution with Cloudflare R2 storage, see [`24_VPS_Setup_Procedure.md`](00_Reference/24_VPS_Setup_Procedure.md). R2 is optional — all scripts fall back to local files.

