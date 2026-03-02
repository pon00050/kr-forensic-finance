# Phase 2 Development Status

> **Scope:** CB/BW timeline pipeline — scaffold state, remaining gaps, implementation notes.
> **Canonical for:** Phase 2 current status; SEIBRO scraping guidance; scoping filter spec.
> **See also:** `17_MVP_Requirements.md` §5 (Phase 2 acceptance criteria),
>               `04_Technical_Architecture.md` (Milestone 2 spec),
>               `22_Phase1_Completion_Record.md` (Phase 1 baseline)

*Created: March 2, 2026. Phase 2 scaffold complete; 3 gaps remain before analysis script
can produce real output. (Gaps 3 and 4 completed Mar 2, 2026.)*

---

## What Was Scaffolded (Session 2, Mar 2, 2026 — commit bb3ed4c)

| File | Status | What it does |
|---|---|---|
| `02_Pipeline/extract_cb_bw.py` | ✅ Created | DART DS005 CB/BW fetch; `_parse_dart_response()`; backoff on Error 020 |
| `02_Pipeline/extract_price_volume.py` | ✅ Created | PyKRX ±60-day OHLCV per CB/BW event; laptop-only |
| `02_Pipeline/extract_officer_holdings.py` | ✅ Created | DART elestock endpoint per corp_code |
| `02_Pipeline/pipeline.py --stage cb_bw` | ✅ Wired | Calls the three extractors in sequence |
| `tests/test_pipeline_invariants.py` | ✅ Tests added | TestCbBwSchema (9 tests — 7 skip until parquets exist, 2 parse unit tests pass) |

**What these produce when run:**
- `01_Data/processed/cb_bw_events.parquet` — (corp_code, issue_date, bond_type, exercise_price, repricing_history, exercise_events)
- `01_Data/processed/price_volume.parquet` — (ticker, date, open, high, low, close, volume)
- `01_Data/processed/officer_holdings.parquet` — (corp_code, date, officer_name, change_shares)

**Current state of repricing_history and exercise_events:** Always `[]` (empty JSON arrays).
These fields require SEIBRO scraping, which is not yet implemented. This means Flags 1 and 2
in `cb_bw_timelines.py` will never fire until SEIBRO is added.

---

## 3 Remaining Gaps (Priority Order)

### Gap 1 — Actually run the pipeline (unblocks 7 skipping tests)

```bash
python 02_Pipeline/pipeline.py --stage cb_bw --sleep 0.5
```

Requires: DART API key, laptop (PyKRX geo-blocked on VPS). Will populate the 3 parquets
and turn 7 currently-skipping schema contract tests GREEN.

**Blocker:** PyKRX geo-block means this cannot run from the VPS. Run from laptop only.

---

### Gap 2 — Implement SEIBRO scraping (Flags 1 + 2)

**What:** Granular repricing events and individual exercise records from `seibro.or.kr`.

**Why:** Without SEIBRO data, `repricing_history` and `exercise_events` columns in
`cb_bw_events.parquet` are always `[]`. This permanently disables:
- Flag 1: Repricing below market price (리픽싱)
- Flag 2: Exercise clustering within 5 days of price peak

**Implementation complexity:** HIGH. SEIBRO uses WebSquare JS rendering — plain HTTP returns
an unusable shell page. Playwright or Selenium required.

**Implementation notes:**
- SEIBRO API (`api.seibro.or.kr`) provides aggregate statistics only; granular records
  require scraping `seibro.or.kr` web interface.
- Cache all raw HTML — never re-scrape if cache exists (treat as fragile).
- Target pages: 사채현황 → 권리조정내역 (repricing), 권리행사내역 (exercise history).
- New file: `02_Pipeline/extract_seibro.py`
- Uncomment `"playwright"` in `pyproject.toml` when implementing.

**Dependency:**
```toml
# Uncomment in pyproject.toml when implementing:
# "playwright",   # SEIBRO scraping (WebSquare JS rendering)
```

---

### Gap 3 — ✅ COMPLETED: Apply Phase 2 scoping filter

See "Completed Gaps" section below.

---

### Gap 4 — ✅ COMPLETED: Build `corp_ticker_map.parquet`

See "Completed Gaps" section below.

---

### Gap 5 — Implement `timing_anomalies.py`

**What:** AC-P2-3 from `17_MVP_Requirements.md §5.3` requires:
> `timing_anomalies.csv` contains at least 20 events where `price_move_pct > 5%` precedes
> a material disclosure on the same day.

`timing_anomalies.py` is currently a stub (Marimo cell structure with no logic). It is
listed as Phase 2 in `04_Technical_Architecture.md` but Phase 3 in `CLAUDE.md` — this
inconsistency should be resolved.

**Resolution:** Per `17_MVP_Requirements.md`, timing anomaly output IS part of Phase 2
scope (same section, §5). `CLAUDE.md` milestone numbering is informally organized; the
authoritative scope is `17_MVP_Requirements.md`.

**Additional data required:** DART filing timestamps (not just filing dates) — `filed_at`
with HH:MM:SS precision to detect post-market-close filings. Source: DART RSS or bulk
download. New table: `disclosures.parquet`.

---

## Completed Gaps

### ✅ Gap 3 — Phase 2 scoping filter (completed Mar 2, 2026)

`extract_cb_bw.py` now has `--scoped` flag support via `build_scoped_universe()`:
- Loads `beneish_scores.parquet` → top-N corp_codes by M-Score (any year)
- Unions with all corp_codes that returned CB/BW events in the discovery pass
- Narrows universe from ~1,702 to ~200–400 companies

CLI: `python pipeline.py --stage cb_bw --scoped --top-n 100`

### ✅ Gap 4 — `corp_ticker_map.parquet` (completed Mar 2, 2026)

`02_Pipeline/extract_corp_ticker_map.py` builds the mapping from `company_list.parquet`.
Schema: `(corp_code, ticker, corp_name, market, effective_from, effective_to)`.
`effective_from`/`effective_to` are null for Phase 2 (no history tracking yet).
Wired into `run_stage_cb_bw()` in `pipeline.py`.

**Unblocked by Gap 4:** Flags 3 and 4 in `cb_bw_timelines.py` can now fire once
Gap 1 (actual pipeline run) produces the parquets.

---

## Flags 3 and 4 — Ready to Fire

Flags 3 (volume surge) and 4 (officer holdings decrease) in `cb_bw_timelines.py` will
fire as soon as Gap 1 is resolved (Gap 4 is now complete):

- **Flag 3** needs: `price_volume.parquet` (Gap 1) — corp → ticker mapping now available
- **Flag 4** needs: `officer_holdings.parquet` (Gap 1) — corp → ticker mapping now available

Flags 1 and 2 need Gap 2 (SEIBRO) in addition.

---

## Phase 2 Acceptance Criteria Status

| AC | Requirement | Status |
|---|---|---|
| AC-P2-1 | ≥99% of active KOSDAQ tickers have complete OHLCV 2018–2024 | ❌ Not yet run |
| AC-P2-2 | Pipeline captures ≥95% of disclosed CB/BW events | ❌ Not yet run |
| AC-P2-3 | `timing_anomalies.csv` contains ≥20 events with >5% price move before disclosure | ❌ Not implemented (Gap 5) |

*Note: Gap 4 (corp_ticker_map) now complete — Flags 3 and 4 unblocked pending Gap 1 (actual run).*

---

## Test Status

| Test | Status | Unblocked by |
|---|---|---|
| `test_cb_bw_required_columns` | ⏭ Skip | Gap 1 (run pipeline) |
| `test_cb_bw_bond_type_values` | ⏭ Skip | Gap 1 |
| `test_cb_bw_issue_date_parseable` | ⏭ Skip | Gap 1 |
| `test_cb_bw_no_duplicate_events` | ⏭ Skip | Gap 1 |
| `test_price_volume_required_columns` | ⏭ Skip | Gap 1 |
| `test_price_volume_date_parseable` | ⏭ Skip | Gap 1 |
| `test_officer_holdings_required_columns` | ⏭ Skip | Gap 1 |
| `test_parse_cb_response_status_013_returns_empty` | ✅ Pass | Already passing |
| `test_parse_cb_response_valid_returns_rows` | ✅ Pass | Already passing |

---

## pyproject.toml — Phase 2 Dependency State

```toml
# Currently commented (uncomment as each data source is implemented):
# "dart-fss",     # DS005 helpers — not needed (direct requests.get used instead)
# "playwright",   # SEIBRO scraping — uncomment when Gap 2 is implemented
# "scipy",        # Statistical tests for timing anomaly — uncomment when Gap 5 is implemented
```

`dart-fss` is not needed — `extract_cb_bw.py` calls DART DS005 directly via `requests.get`.
