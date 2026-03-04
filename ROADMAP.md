# Roadmap

## Milestones

| # | Milestone | Status | Script |
|---|---|---|---|
| 1 | Beneish M-Score screen | Complete | `beneish_screen.py` |
| 2 | CB/BW timelines | In progress | `cb_bw_timelines.py` |
| 3 | Timing anomalies | Planned | `timing_anomalies.py` |
| 4 | Officer network graph | Planned | `officer_network.py` |

## Open Backlog

| ID | Description | Phase | Effort |
|---|---|---|---|
| PR5 | Historical backfill 2014–2018 | 4 | Medium |
| A1 | Automate recurring data refresh | 2 | Low |
| I1 | Verify PyKRX from hosted IPs | 5 | Low |

## Phase 2 Prerequisites

CB/BW timelines require:
1. Running from a Korean residential IP or confirmed FDR/yfinance backend
   (PyKRX is geo-blocked on datacenter IPs)
2. SEIBRO access for repricing history (Playwright or official API)
