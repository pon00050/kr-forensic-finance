# ROADMAP.md

**Status:** Phase 1 complete (Feb 27, 2026) — KOSDAQ Beneish M-Score screen 2019–2023, 44 tests passing (37 invariant + 7 AC).

**Legend:**
- Status: ✅ Complete · ⬜ Open · 🔄 In progress
- Effort: Low (<1 day) · Medium (1–3 days) · High (>3 days)
- Phase 1.5 = before next milestone; Phase 2–4 = alongside/after named milestone

---

## Section 1 — Active Milestones (Phases 2–4)

| # | Milestone | Status | Scripts | Revenue models unlocked |
|---|---|---|---|---|
| 1 | Beneish M-Score screen | ✅ Complete (Feb 2026) | `beneish_screen.py` | All |
| 2 | CB/BW timelines | ⬜ Planned | `cb_bw_timelines.py` | #1, #3, #4, #8, #9 |
| 3 | Timing anomalies | ⬜ Planned | `timing_anomalies.py` | #3, #4, #5, #9 |
| 4 | Officer network | ⬜ Planned | `officer_network.py` | #2, #3, #4, #9 |

Revenue model numbers reference `00_Reference/00_Feature_Analysis.md` Section 4 priority matrix.

---

## Section 2 — Technical Backlog

### Open items

| ID | Description | Phase | Effort | Status | Detail |
|---|---|---|---|---|---|
| PR2 | Extraction timestamp in both parquets | 1.5 | Low | ✅ Complete | `19_Pipeline_Improvement_Areas.md` §DQ2 |
| PR3 | Composite risk tier column (`risk_tier`) | 1.5 | Low | ✅ Complete | `17_MVP_Requirements.md` §9 |
| H3 | DART Error 020 exponential backoff | 2 | Medium | ✅ Complete | `19_Pipeline_Improvement_Areas.md` §H3 |
| M1 | `run_summary.json` merge on resume | 2 | Low | ✅ Complete | `19_Pipeline_Improvement_Areas.md` §M1 |
| M3 | Silent CFS→OFS shift detection | 2 | Low | ✅ Complete | `19_Pipeline_Improvement_Areas.md` §M3 |
| M2 | Pin WICS snapshot date to `end_year` | 3 | Low | ⬜ Open | `19_Pipeline_Improvement_Areas.md` §M2 |
| PR1 | Data lineage `match_method_*` columns | 3 | Medium | ⬜ Open | `17_MVP_Requirements.md` §9, `19_Pipeline_Improvement_Areas.md` §DQ1 |
| PR4 | KOSPI coverage | 4 | Medium | ⬜ Open | `17_MVP_Requirements.md` §9 |
| PR5 | Historical backfill 2014–2018 | 4 | Medium | ⬜ Open | `17_MVP_Requirements.md` §9 |
| L2 | `--force` flag in `transform.py` | Later | Low | ✅ Complete | `19_Pipeline_Improvement_Areas.md` §L2 |
| L3 | Optional tqdm progress bar | Later | Low | ⬜ Open | `19_Pipeline_Improvement_Areas.md` §L3 |

### Fixed items (not actionable)

| ID | Description | Status |
|---|---|---|
| C1 | `--sample` propagation to transform | ✅ Fixed |
| C2 | `--sample` propagation via pipeline.py | ✅ Fixed |
| H1 | `--max-minutes` hard timeout | ✅ Fixed |
| H2 | ETA in progress logs | ✅ Fixed |
| L1 | `--sleep` flag | ✅ Fixed |

---

## Section 3 — Commercial / Legal Blockers

Non-engineering prerequisites. Run in parallel with pipeline work; owned outside engineering.

| ID | Description | Priority | Prerequisite for | Owner |
|---|---|---|---|---|
| CB4 | GitHub public release | **Do now** | All revenue channels | Project owner |
| CB1 | FSCMA compliance analysis (alt data distribution) | Before monetizing #3 | Alt data (#3), SaaS (#15) | Legal counsel |
| CB2 | PIPA-compliant officer data packaging | Before distributing Milestone 4 output | KYC (#2), Litigation (#4) | Legal counsel |
| CB3 | Law firm referral structure (whistleblower) | Do later | Whistleblower (#5) | Business development |
| CB5 | Nonprofit legal entity (사단법인) | Do later (3–6 mo lead time) | Grant funding (#14), media (#11) | Legal/organizational |

---

## Section 4 — Completed Work (Phase 1)

| Item | Completed | Notes |
|---|---|---|
| Full KOSDAQ pipeline (2019–2023) | Feb 27, 2026 | 7,042 rows, 1,702 companies |
| Beneish M-Score screen | Feb 27, 2026 | 5,357 rows, 4 score periods |
| CFS/OFS regression test (KI-003 guard) | Feb 27, 2026 | `test_fs_type_values_and_distribution` |
| Beneish output schema contract | Feb 27, 2026 | `REQUIRED_BENEISH_COLUMNS`, `TestBeneishOutputSchema` |
| XBRL crosswalk CSV | Feb 27, 2026 | `00_Reference/dart_xbrl_crosswalk.csv` |
| Full test suite (44 tests) | Feb 27, 2026 | AC1–AC7 (7) + invariant tests (37) |
| Cloud infrastructure (R2 + Oracle Cloud Free Tier VPS) | Feb 27, 2026 | R2 upload confirmed; VPS running at 168.107.21.26; PyKRX geo-block on VPS (KRX returns 0 tickers from data center IP) |
| GitHub public release | Feb 27, 2026 | https://github.com/pon00050/kr-forensic-finance — 57 files, initial commit |

| Documentation housekeeping | Feb 27, 2026 | .gitignore additions; scope headers on all 00_Reference docs; accuracy fixes (7-table claim, extract_krx.py orphan, milestone stubs); new reference-index.md and pipeline-details.md |

Full run stats and sign-off: `00_Reference/22_Phase1_Completion_Record.md`.
