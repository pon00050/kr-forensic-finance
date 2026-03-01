# Reference Index

Annotated index of all documents in `00_Reference/`. Each entry notes scope and
canonical ownership. Cite documents here rather than copying their content.

---

## Start Here

| Document | Read when... |
|---|---|
| `04_Technical_Architecture.md` | You need to understand the overall pipeline structure, milestone specs, or planned schema |
| `17_MVP_Requirements.md` | You need acceptance criteria, column definitions, or phase scope |
| `18_Research_Findings.md` | You are touching pipeline code — confirmed API patterns and workarounds |
| `22_Phase1_Completion_Record.md` | You want the Phase 1 sign-off: run stats, row counts, test results |

---

## Data and Sources

| Document | Scope |
|---|---|
| `02_Data_Sources.md` | **Canonical.** Endpoints, rate limits, libraries for all four data sources (OpenDART, KRX, SEIBRO, KFTC) |
| `11_Industry_Classification.md` | **Canonical.** KSIC Rev. 10 join logic; WICS taxonomy; verified API patterns; pre-implementation decisions |
| `dart_xbrl_crosswalk.csv` | XBRL element ID → financial variable mapping; extraction methodology audit trail |

---

## Pipeline Architecture

| Document | Scope |
|---|---|
| `04_Technical_Architecture.md` | **Canonical.** Three-layer architecture; Phase 1–4 milestone specs; unified dataset schema; tech stack |
| `pipeline-details.md` | Run commands, CLI flags, stage descriptions, Marimo deferral note, resumability |
| `19_Pipeline_Improvement_Areas.md` | Open backlog items by ID (H3, M1–M3, PR1–PR5); fixed items for reference |
| `20_Cloud_Infrastructure_Setup.md` | R2 + Oracle Cloud provisioning steps; architecture; status as of Feb 2026 |
| `24_VPS_Setup_Procedure.md` | Step-by-step VPS rebuild procedure; known issues (PyKRX geo-block, uv run python) |
| `25_GitHub_Repo_Assessment.md` | *(gitignored — local only)* Repo quality assessment and prioritized action items (Feb 2026) |

---

## Methodology

| Document | Scope |
|---|---|
| `07_Automation_Assessment.md` | **Canonical.** Won benchmark (arXiv 2503.17963); automation ceiling; false positive rates; Claude reliability by task type |
| `14_GMT_Research_Deep_Dive.md` | GMT Research methodology reference; Asian market Beneish adaptations |

---

## AI Layer

| Document | Scope |
|---|---|
| `09_Claude_Cost_Optimization.md` | API cost patterns and routing strategies; read before adding new Claude calls |
| `10_Multi_Agent_Architecture.md` | Orchestrator-worker design; agent schemas; batch vs. real-time routing |
| `08_Continuous_Monitoring_System.md` | DART RSS + news monitoring design; 3-way match validation architecture |

---

## Requirements and Compliance

| Document | Scope |
|---|---|
| `17_MVP_Requirements.md` | **Canonical.** Phase definitions; AC1–AC7 acceptance criteria; schema contracts; column-level provenance |
| `15_FSCMA_Regulatory_Analysis.md` | Capital Markets Act — disclosure obligations and distribution constraints |
| `16_CPA_Act_Regulatory_Analysis.md` | External audit requirements and officer data handling |
| `05_Regulatory_Environment.md` | FSC/FSS overview; enforcement framing; historical context |

---

## Project History and Status

| Document | Scope |
|---|---|
| `22_Phase1_Completion_Record.md` | Phase 1 sign-off — run stats, row counts, test results, known issues (permanent record) |
| `23_GitHub_Release_Checklist.md` | Pre-release audit results; .gitignore gaps fixed; checklist status |
| `26_Beneish_MScore_Components.md` | Plain-language reference for all 8 M-Score components — formulas, economic intuition, false positive sectors, real-world cases |
| `27_Phase1_Analytical_Research_Questions.md` | Four open investigative questions from Phase 1 visual inspection — pursuable with existing beneish_scores.parquet, no pipeline work needed |
| `28_Phase2_Development_Status.md` | Phase 2 CB/BW scaffold state; 5 remaining gaps; SEIBRO scraping notes; corp_ticker_map gap analysis; AC-P2-1 through AC-P2-3 status |
| `ROADMAP.md` | Active milestones; technical backlog by ID; completed work table |

---

## Background Research

| Document | Scope |
|---|---|
| `01_Policy_Context.md` | 신고포상금 제도개편 (Feb 25, 2026) — the regulatory event that initiated this project |
| `03_Project_Rationale.md` | CB/BW 3자배정 manipulation scheme explanation; why public data is sufficient |
| `06_Existing_Infrastructure.md` | Survey of existing Korean financial data tools; gap analysis |
| `19_Data_Refresh_Cadence.md` | Recommended update frequencies by data type; staleness tolerances |
