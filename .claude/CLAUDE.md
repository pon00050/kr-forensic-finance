# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Identity

Korean capital markets data pipeline. Builds public infrastructure for detecting anomalies in KOSPI/KOSDAQ listed companies using exclusively open, publicly available data. Outputs are **hypotheses for human review** — not fraud conclusions.

---

## Development Environment

This project uses `uv` for package management. Analysis scripts live in `03_Analysis/` as plain `.py` files.

> **Marimo interactive notebooks deferred.** The `03_Analysis/` scripts are written in Marimo cell format (compatible with `marimo edit/run`) but are run as plain Python scripts for now. Do not spend time configuring or debugging Marimo's interactive UI until this note is removed.

```bash
# Install uv (if not already installed)
pip install uv

# Install all dependencies
uv sync

# Run an analysis script (plain Python — no Marimo UI)
python 03_Analysis/beneish_screen.py

# When Marimo is re-enabled (future):
# uv run marimo edit 03_Analysis/beneish_screen.py
# uv run marimo run 03_Analysis/beneish_screen.py
```

---

## Development Workflow

**Test-driven development:** For every new behaviour or schema contract, write the test
first. The test must fail before the implementation, then pass after. All existing
invariant tests (`tests/test_pipeline_invariants.py`) and acceptance criteria
(`tests/test_acceptance_criteria.py`) must continue passing after each change.
New tests live in `test_pipeline_invariants.py` (schema/formula invariants) or
`test_acceptance_criteria.py` (end-to-end output quality).

---

## Pipeline Architecture

The pipeline uses a three-layer architecture (Layer 1: Python automation, Layer 2: AI-assisted review, Layer 3: human judgment). See `00_Reference/04_Technical_Architecture.md` for the full diagram. This project implements Layers 1 and 2 only.

**Data sources → unified schema:**

| Source | Library | Data |
|---|---|---|
| OpenDART (`opendart.fss.or.kr`) | `opendartreader`, `dart-fss` | Financials, CB/BW events, officer holdings, disclosures |
| KRX (`data.krx.co.kr`) | `pykrx` | OHLCV, short selling balances |
| SEIBRO (`seibro.or.kr`) | scraping (no API) | CB/BW conversion terms and exercise history |
| KFTC (`egroup.go.kr`) | bulk download | 재벌 cross-shareholding network |

**Primary join key:** `corp_code` (DART 8-digit) — stable across relistings. A `corp_code ↔ ticker` mapping table with effective date ranges bridges DART and KRX data.

**Storage conventions:**
- `01_Data/raw/` — downloaded as-is; **never modify**
- `01_Data/processed/` — Parquet (columnar, pandas-friendly) or SQLite for cross-table queries; one file/table per logical entity
- **Phase 1 produces one table: `company_financials.parquet`.** The remaining six tables defined in `04_Technical_Architecture.md` (cb_bw_events, price_volume, officer_holdings, disclosures, kftc_network, corp_ticker_map) are Phase 2–4 targets — they do not exist yet.

**Four analysis milestones (in `03_Analysis/`):**
1. `beneish_screen.py` — **Complete (Phase 1).** Beneish M-Score per KOSDAQ company-year; threshold -1.78
2. `cb_bw_timelines.py` — **Planned (Phase 2).** CB/BW issuance → repricing → exercise → price/volume impact chain; stub file only
3. `timing_anomalies.py` — **Planned (Phase 3).** Material disclosure timestamps vs. same-day price/volume movement; stub file only
4. `officer_network.py` — **Planned (Phase 4).** `networkx` graph of individuals appearing across flagged companies; stub file only

---

## Claude API Usage Rules

### Role
Classification, entity clustering, and inconsistency flagging only. Never provide financial analysis, investment conclusions, or fraud determinations.

### Won Benchmark Constraint (arXiv 2503.17963)
Open-ended Korean financial questions: model accuracy 0.01–0.04 (near-zero).
Reliable tasks: binary classification, structured extraction, inconsistency flagging.

### Model Routing (ENFORCED)
- News/DART RSS/entity classification → `claude-haiku-4-5`
- Narrative analysis, synthesis, orchestration → `claude-sonnet-4-6`
- `claude-opus-4-6`: **NEVER in production at current scope.** Raise `ValueError` if called. Re-evaluate only if narrative synthesis tasks exceed ~500K tokens per invocation — see `00_Reference/07_Automation_Assessment.md` (Claude Technical Capabilities section) for the full rationale.

### Output Schemas
- News/DART classification: single letter (`A`/`B`/`C`/`D`/`E`/`F`) only. No explanation.
- Entity resolution: `{"cluster_id": ..., "names": [...], "confidence": ..., "match_basis": ...}`
- Narrative flags: `[{"source_quote": ..., "flag_type": ..., "severity": "low"|"medium"|"high"}]`
- Return only what the prompt requests. No unsolicited analysis.

### Cost Rules
- Batch API for 사업보고서, entity resolution, synthesis (non-urgent).
- `cache_control: ephemeral` on all system prompts in direct API calls.
- No Claude calls for tasks Python handles (filtering, math, sorting).
- See `00_Reference/09_Claude_Cost_Optimization.md` before adding new API patterns.

---

## Reference Docs

| File | Contents |
|---|---|
| `00_Reference/04_Technical_Architecture.md` | Full pipeline diagram, unified schema, four milestones, tech stack |
| `00_Reference/07_Automation_Assessment.md` | Automation ceiling, false positive rates, Claude capability limits |
| `00_Reference/08_Continuous_Monitoring_System.md` | Real-time news + DART + market monitoring; 3-way match validation |
| `00_Reference/09_Claude_Cost_Optimization.md` | API cost patterns — read before adding new Claude calls |
| `00_Reference/10_Multi_Agent_Architecture.md` | Orchestrator-worker design, agent schemas, batch vs. real-time, trust boundaries |

---

## Running the Pipeline

See `README.md` for the execution diagram, run commands, and CLI flags. Full operational detail (all flags, stage descriptions, Marimo deferral note, resumability) is in `00_Reference/pipeline-details.md`.

Quick smoke test:
```bash
python 02_Pipeline/pipeline.py --market KOSDAQ --start 2021 --end 2023 --sample 5 --sleep 0.1 --max-minutes 3
python 03_Analysis/beneish_screen.py
```

---

## Commit and Push Protocol

**For any commit that touches source code (`.py`, `pyproject.toml`, `.github/workflows/`):**

1. Run tests locally before committing:
   ```bash
   python -m pytest tests/test_pipeline_invariants.py -v
   ```
   All invariant tests must pass before proceeding.

2. Only then commit and push:
   ```bash
   git add <specific files>
   git commit -m "..."
   git push
   ```

3. After pushing, confirm GitHub Actions CI goes green before declaring the task done.

**For doc-only commits** (`.md` files, no code changes): tests are not required before committing, but CI must still go green after push.

**Never use `git add -A` or `git add .`** — always stage specific files by name to avoid accidentally committing `.env`, log files, or data files.

---

## Business Intelligence Privacy

This is a **public GitHub repository**. Technical infrastructure docs are intentionally
public. Business intelligence documents — monetization strategy, stakeholder analysis,
competitive positioning, career connection, and raw research — are **private** and must
never be committed to git.

**Private content locations (all gitignored):**
- `00_Reference/00_*.md` — career and feature analysis docs (prefix `00_`)
- `00_Reference/12_Business_Model_Research.md`, `00_Reference/13_Monetization_Research.md`
- `00_Reference/stakeholder_dossiers/` — all contents
- `00_Reference/research/` — raw research and brainstorm transcripts
- `00_Reference/25_GitHub_Repo_Assessment.md` — internal repo quality notes

**Rules for Claude Code:**
1. **Never create** files containing monetization, pricing, stakeholder, competitive,
   or career-strategy content in any tracked location.
2. **New private docs** must go in `00_Reference/research/` or use the `00_` prefix
   in `00_Reference/`. Confirm the file is gitignored before writing.
3. **Before any commit**, verify that staged files do not contain business intelligence.
   If a staged file references pricing, revenue, stakeholders, or monetization strategy,
   warn the user and do not proceed until confirmed.
4. **Reference docs that summarize public methodology** (e.g., Beneish components,
   regulatory framework) are fine to commit. The test is: would this content give a
   competitor or stakeholder insight into the project's business strategy? If yes, it
   is private.

**If a private file is accidentally committed:**
```bash
pip install git-filter-repo
git filter-repo --path <file-path> --invert-paths
git push --force-with-lease
```
This rewrites history to remove the file from all commits, then force-pushes the
clean history. `--force-with-lease` is safer than `--force` — it fails if someone
else has pushed in the meantime.

---

## Known Issues

See `BUGS.md` (project root) for documented bugs with investigation paths and fixes.
Before modifying pipeline code, also read `00_Reference/18_Research_Findings.md` for
verified API behaviors and confirmed workarounds.
