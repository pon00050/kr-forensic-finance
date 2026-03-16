# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See `.claude/CLAUDE.md` for full workflow conventions, commit protocol, privacy rules, and pipeline run commands.

## Ecosystem

Part of the Korean forensic accounting toolkit.
- Hub: `../forensic-accounting-toolkit/` | [GitHub](https://github.com/pon00050/forensic-accounting-toolkit)
- Task board: https://github.com/users/pon00050/projects/1
- Role: Platform (integrates all foundation and analysis libraries)
- Depends on: kr-company-registry, kr-trading-calendar, kr-beneish, kr-derivatives, jfia-forensic
- Consumed by: end users (CLI, FastAPI, MCP server)

---

## Common Commands

```bash
# Install / sync
uv sync --extra dev

# Run tests
python -m pytest tests/ -v

# Run a single test
python -m pytest tests/test_pipeline_invariants.py::test_function_name -v

# Full pipeline (smoke test)
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2021 --end 2023 --sample 5 --sleep 0.1

# Re-run everything including analysis
krff refresh --sample 5

# Status / audit
krff status -v
krff audit --verbose
```

---

## `src/` Module Map

| Module | Purpose |
|--------|---------|
| `_paths.py` | `PROJECT_ROOT`, `PROCESSED_DIR` constants; overridable via `KRFF_PROJECT_ROOT` / `KRFF_DATA_DIR` env vars |
| `constants.py` | Shared thresholds: `BENEISH_THRESHOLD = -1.78`, CB/BW flag names, `PRICE_WINDOW_TRADING_DAYS = 60` (used by `03_Analysis/_scoring.py` only, not CLI-exposed) |
| `db.py` | In-memory DuckDB over parquet files; `query()`, `read_table()` — no persistent `.duckdb` file |
| `data_access.py` | `load_parquet()`, `load_csv()`, `load_company_name()` — reusable loaders extracted from `report.py` |
| `models.py` | Pydantic models documenting dict-returning function contracts; not yet enforced at runtime |
| `trading_calendar.py` | KRX trading-day logic via `exchange_calendars` (XKRX); `trading_day_offset()`, `is_trading_day()` |
| `coverage_index.py` | Coverage adequacy computations (used by health monitor, not forensic pipeline) |
| `audit.py` | DAG-based freshness checker; `get_audit()` → used by `krff audit` |
| `stats_runner.py` | 14-node stats DAG; `get_stats_audit()` → used by `krff stats` |
| `analysis.py` | Thin wrapper: loads `beneish_scores.parquet` for `krff analyze` |
| `status.py` | Artifact inventory formatter for `krff status` |
| `quality.py` | Null-rate / coverage gap checker for `krff quality` |
| `report.py` | Per-company HTML report generator; calls Claude API for narrative synthesis |
| `mcp_server.py` | FastMCP server (10 tools); mounted at `/mcp/` by `app.py`; all tools must return via `mcp_utils.sanitize_for_json()` |
| `mcp_utils.py` | JSON serialization helpers: converts numpy types, `NaN`, `pd.Timestamp` — required for all MCP tool returns |
| `charts.py` | Beneish visualizations → `beneish_viz.html` |
| `pipeline.py` | Lazy proxy to `02_Pipeline/pipeline.py`; avoids heavy imports at CLI startup |
| `review.py` | Review queue CRUD (SQLite); used by `krff queue/surface/hide/assess` |

---

## Known Gaps

| Gap | Why | Status |
|-----|-----|--------|
| Phase 3 stubs (`monitor/dart_rss.py`, `cli.py monitor/alerts`, `app.py alerts/monitor`) | Phase 3 not started | Deferred — Phase 3 |
| Pydantic models in `src/models.py` not enforced at runtime | Functions return raw dicts; FastAPI validates at serialization boundary only | Deferred — Phase 3 |
| `extract_bondholder_register.py:91` — pagination missing | Only fetches first page of CB filings (>100 not handled) | Unblocked — low priority |
| 3 standalone extractors not wired into `pipeline.py` | `extract_bondholder_register`, `extract_depreciation_schedule`, `extract_revenue_schedule` produce parquets but can't be triggered via `krff refresh` | By design — standalone scripts |
| WICS sector data has no historical snapshots (`extract_dart.py:157`) | WICS serves recent dates only; joins to historical financials use current sector | By design — upstream limitation |

---

## Architecture Shape

Two separate entry points both read from the same parquet layer:

```
02_Pipeline/pipeline.py  →  01_Data/processed/*.parquet  ←  src/db.py
                                                          ←  src/data_access.py
03_Analysis/run_*.py     →  03_Analysis/*.csv

app.py (FastAPI)  →  src/mcp_server.py  →  10 tools over parquets + CSVs
cli.py (krff)     →  src/* thin wrappers
```

`src/db.py` is the canonical query layer. Do not read parquets directly with `pd.read_parquet` in new code — use `db.read_table()` or `data_access.load_parquet()` so path resolution and DuckDB registration are consistent.
