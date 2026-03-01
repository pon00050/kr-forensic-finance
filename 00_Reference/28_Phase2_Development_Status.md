# Phase 2 Development Status

> **Scope:** CB/BW timeline pipeline — scaffold state, remaining gaps, implementation notes.
> **Canonical for:** Phase 2 current status; SEIBRO scraping guidance; scoping filter spec.
> **See also:** `17_MVP_Requirements.md` §5 (Phase 2 acceptance criteria),
>               `04_Technical_Architecture.md` (Milestone 2 spec),
>               `22_Phase1_Completion_Record.md` (Phase 1 baseline)

*Created: March 2, 2026. Phase 2 scaffold complete; 5 gaps remain before analysis script
can produce real output.*

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

## 5 Remaining Gaps (Priority Order)

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

### Gap 3 — Apply Phase 2 scoping filter

**What:** `extract_cb_bw.py` currently iterates all 1,702 KOSDAQ companies. Per `17_MVP_Requirements.md §5.1`, Phase 2 runs on:
- Phase 1 top 100 companies by M-Score (any year), AND
- All KOSDAQ companies with ≥1 CB/BW issuance on DART since 2018

This narrows the target to approximately 200–400 companies.

**Why it matters:** Running all 1,702 companies × 2 endpoints = ~3,404 DART API calls for
CB/BW alone (plus elestock calls). The scoping filter cuts this to ~400–800 calls.

**Implementation:** Before the main loop in `fetch_cb_bw_events()`:
1. Load `beneish_scores.parquet` → get top-100 corp_codes by M-Score (any year)
2. Run a first pass of DS005 for all companies (accept all returns) → companies with `len > 0`
3. Union the two sets → filtered `corp_codes`

Or: implement as a pre-filter flag `--scoped` that applies the Phase 2 universe restriction.

---

### Gap 4 — Build `corp_ticker_map.parquet`

**What:** `cb_bw_timelines.py` line 74 references `processed/corp_ticker_map.parquet`:
```python
df_map = pd.read_parquet(processed / "corp_ticker_map.parquet") if (...).exists() else pd.DataFrame()
```

If this file is absent, `map_lookup` is empty and all events are silently skipped
(no ticker → no price window → no anomaly scoring). This is a silent total failure mode.

**Current behavior:** Falls back to empty DataFrame; all corp_code → ticker lookups fail;
zero rows scored. Analysis script produces empty output with no error.

**Minimum viable fix (Phase 2):** Generate `corp_ticker_map.parquet` from `company_list.parquet`
(which already has corp_code + stock_code). New file: `02_Pipeline/extract_corp_ticker_map.py`.

Schema per `04_Technical_Architecture.md`:
```
(corp_code, ticker, corp_name, market, effective_from, effective_to)
```

For Phase 2 minimum: effective_from/effective_to can be null (no history tracking yet).

**New file:** `02_Pipeline/extract_corp_ticker_map.py` (simple — just reads company_list.parquet
and writes the renamed/reformatted version to processed/).

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

## Flags 3 and 4 — Ready to Fire

Flags 3 (volume surge) and 4 (officer holdings decrease) in `cb_bw_timelines.py` will
fire as soon as Gaps 1 and 4 are resolved:

- **Flag 3** needs: `price_volume.parquet` (Gap 1) + corp → ticker mapping (Gap 4)
- **Flag 4** needs: `officer_holdings.parquet` (Gap 1) + corp → ticker mapping (Gap 4)

Flags 1 and 2 need Gap 2 (SEIBRO) in addition.

---

## Phase 2 Acceptance Criteria Status

| AC | Requirement | Status |
|---|---|---|
| AC-P2-1 | ≥99% of active KOSDAQ tickers have complete OHLCV 2018–2024 | ❌ Not yet run |
| AC-P2-2 | Pipeline captures ≥95% of disclosed CB/BW events | ❌ Not yet run |
| AC-P2-3 | `timing_anomalies.csv` contains ≥20 events with >5% price move before disclosure | ❌ Not implemented (Gap 5) |

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
