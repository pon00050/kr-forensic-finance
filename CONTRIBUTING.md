# Contributing

Contributions that extend the pipeline or improve data quality are welcome.

## Extending a Milestone

The four analysis milestones live in `03_Analysis/`:

| Script | Status | Description |
|---|---|---|
| `beneish_screen.py` | Complete (Phase 1) | Beneish M-Score screen |
| `cb_bw_timelines.py` | Planned (Phase 2) | CB/BW issuance → repricing → exercise chain |
| `timing_anomalies.py` | Planned (Phase 3) | Disclosure timestamps vs. price/volume movement |
| `officer_network.py` | Planned (Phase 4) | Officer network graph across flagged companies |

To implement a planned milestone:
1. Read the stub file and the corresponding section in [`00_Reference/04_Technical_Architecture.md`](00_Reference/04_Technical_Architecture.md)
2. Add any new data extraction to `02_Pipeline/extract_dart.py` or a new `extract_*.py` file
3. Add transform logic to `02_Pipeline/transform.py` if new Parquet tables are needed
4. Write analysis output to `03_Analysis/` as CSV + Parquet (same pattern as `beneish_screen.py`)

## Tests

All new features require tests. Run the test suite with:

```bash
pytest tests/ -v
```

Two test files:
- `tests/test_pipeline_invariants.py` — self-contained schema/logic tests, no live data needed
- `tests/test_acceptance_criteria.py` — end-to-end checks, run after a full pipeline run

Add invariant tests for any new Parquet schema columns. Add acceptance-criteria tests for any new acceptance criteria defined in [`00_Reference/17_MVP_Requirements.md`](00_Reference/17_MVP_Requirements.md).

## Coding Conventions

- **Package manager:** `uv` — add dependencies to `pyproject.toml`, then `uv sync`
- **Storage:** Parquet for tabular data (`01_Data/processed/`); raw files to `01_Data/raw/` unmodified
- **Credentials:** Never hardcode API keys. Use `.env` (see `.env.example`). The `.gitignore` excludes `.env` and all of `01_Data/`
- **Pipeline calls:** Use `pipeline.py` as the entry point. It propagates `--sample`, `--start`, `--end`, `--force`, and `--sleep` consistently across stages
- **Error handling:** Log errors and continue; write `no_filing` marker files for resumability

## Roadmap

See [`ROADMAP.md`](ROADMAP.md) for the full backlog with priorities, and [`00_Reference/19_Pipeline_Improvement_Areas.md`](00_Reference/19_Pipeline_Improvement_Areas.md) for the technical backlog.

## Repository Topics

This repo is tagged with the following GitHub topics for discoverability:

`kosdaq` `dart` `beneish` `financial-forensics` `korea` `open-data`

If you add a significant new capability (e.g. CB/BW timelines, officer network), consider whether additional topics are warranted — update them via GitHub Settings → General → Topics.
