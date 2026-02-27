# Pipeline — Operational Reference

Full run commands, CLI flags, stage descriptions, and operational notes for the
`02_Pipeline/` ETL pipeline. This is the canonical location for pipeline operational
detail. `README.md` and `CLAUDE.md` point here rather than duplicating.

---

## Run Commands

```bash
# Smoke test (5 companies, fast — always run this first)
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2021 --end 2023 --sample 5 --sleep 0.1 --max-minutes 3

# 100-company validation run (~8 min)
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2021 --end 2023 --sample 100

# Full run (~2.5–3 hrs at default sleep)
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023

# Single stage only
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023 --stage dart

# Beneish analysis (run after pipeline)
python 03_Analysis/beneish_screen.py   # outputs 03_Analysis/beneish_scores.csv
```

---

## CLI Flags

| Flag | Description |
|---|---|
| `--sample N` | Limit to first N companies (smoke testing); propagates end-to-end: extract → transform |
| `--max-minutes N` | Hard deadline guard; exits cleanly after N minutes |
| `--sleep S` | Override default inter-request sleep in seconds (default 0.5; use 0.1 for smoke tests) |
| `--force` | Extract stage: re-fetch raw files (company_list.parquet, wics.parquet, etc.). Transform stage: delete and rebuild `company_financials.parquet`. |
| `--stage dart\|transform` | Run a single stage only |

---

## Stage Descriptions

**Stage: dart** (`extract_dart.py`)
Downloads raw financials, sector data, and company list from OpenDART and WISEindex.
Writes per-company parquet files to `01_Data/raw/financials/` and lookup tables to
`01_Data/raw/sector/`. Also writes `01_Data/raw/run_summary.json`.

`extract_dart.py` and `transform.py` are not meant to be called directly — use
`pipeline.py`, which handles both in the correct order and propagates `--sample`,
`--start`, and `--end` flags consistently across both stages. Note: sub-stage options
`company-list`, `financials`, and `sector` are `extract_dart.py --stage` options for
direct script use only, not `pipeline.py --stage` options.

**Stage: transform** (`transform.py`)
Reads all raw files from `01_Data/raw/`, joins KSIC and WICS sector data, normalizes
schemas, and writes `01_Data/processed/company_financials.parquet` (Phase 1).

---

## Resumability

The pipeline is resumable. Re-running `pipeline.py` skips raw files that already
exist on disk; only missing company-years are re-fetched from DART.

`beneish_screen.py` can be re-run at any time after the pipeline without re-running
the pipeline — it reads from the already-processed parquet.

---

## Marimo Deferral Note

The `03_Analysis/` scripts are written in Marimo cell format (compatible with
`marimo edit/run`) but are run as plain Python scripts for now. Do not spend time
configuring or debugging Marimo's interactive UI until this note is removed from
`CLAUDE.md`.

When Marimo is re-enabled (future):
```bash
uv run marimo edit 03_Analysis/beneish_screen.py
uv run marimo run 03_Analysis/beneish_screen.py
```

---

## Tests

```bash
pytest tests/test_pipeline_invariants.py -v   # schema + formula + KSIC regression (no pipeline needed)
pytest tests/test_acceptance_criteria.py -v   # AC1–AC7 (requires pipeline + beneish_screen.py first)
pytest tests/ -v                              # run all
```
