# MCP Natural Language Query Guide

This document explains what the KRFF MCP server enables and shows real worked examples.

---

## What MCP Enables

The MCP (Model Context Protocol) server exposes all pipeline data — Beneish scores,
CB/BW events, price/volume history, officer networks, timing anomalies — as tools that
an AI assistant (Claude Code, Claude Desktop) can call in response to plain-language
questions.

**You ask in plain Korean or English. Claude calls the right tools, joins the results
across datasets, and returns a synthesized answer.**

This replaces workflows that previously required:
- Opening multiple parquet files or CSV exports
- Writing DuckDB/pandas queries to join tables
- Building VLOOKUP chains across Excel sheets
- Manually cross-referencing DART filing pages

With MCP you skip all of that. The query is your question.

---

## Setup

```bash
uv run krff serve        # starts FastAPI + MCP at http://127.0.0.1:8000
```

Then in Claude Code, open `/mcp` and connect (or it auto-connects via `.mcp.json`).

---

## Worked Examples

All examples below were run live against the pipeline data on Mar 11, 2026.

---

### Example 1 — Company Lookup by Name or Ticker

**Query:** *"Find 롤링스톤"*

Behind the scenes: `lookup_corp_code("롤링스톤")` → `get_company_summary("01049167")`

**Result:**

| Signal | Value |
|---|---|
| Ticker | 214610 (KOSDAQ) |
| Beneish 2023 | **+0.58** — Critical (threshold: −1.78) |
| CB issuances | 8 total, 1 flagged |
| Timing anomalies | 16 flagged out of 140 disclosure events |

One CB event (2021-11-30) showed a **512× normal volume** spike in the 60 days before
issuance, with the stock price peaking 29 days earlier. The exercise price was 9,762 KRW
at issuance; by the next CB in 2024 the price had collapsed to 1,513 KRW (−85%).

---

### Example 2 — Price History Around a Suspicious Event

**Query:** *"Show me 롤링스톤's stock price around the flagged CB issuance in late 2021"*

Behind the scenes: `get_price_volume("01049167", "2021-10-01", "2022-01-31")`

**Result (first 5 rows of 83):**

| Date | Open | High | Low | Close | Volume |
|---|---|---|---|---|---|
| 2021-10-01 | 80,919 | 83,157 | 76,089 | 77,963 | 12,883 |
| 2021-10-05 | 77,577 | 77,952 | 73,871 | 74,028 | 20,935 |
| 2021-10-06 | 73,862 | 76,089 | 69,115 | 69,126 | 20,941 |
| 2021-10-07 | 69,052 | 74,610 | 68,896 | 74,257 | 15,091 |
| 2021-10-08 | 73,508 | 74,611 | 72,384 | 72,842 | 7,148 |

The stock dropped ~14% in the month before the CB was issued on Nov 30 — consistent with
informed selling ahead of dilutive financing.

---

### Example 3 — Multi-Signal Cross-Table Screen

**Query:** *"Which KOSDAQ companies had the most suspicious CB activity in 2022 — big
volume spikes before issuance AND high Beneish scores in the same year?"*

Behind the scenes:
1. `search_flagged_companies(year=2022, min_m_score=0, limit=30)` — 53 companies above 0
2. `get_cb_bw_events(corp_code)` for each of the top 5 Beneish companies in parallel

**Answer:** **캔버스엔 (210120)** is the only company in the top 2022 Beneish cohort that
also had multi-flag CB events:

| Signal | Detail |
|---|---|
| Beneish M-Score 2022 | **+3.80** (extremely high; threshold is −1.78) |
| CB events | 6 total |
| Flagged CBs | 2 events with flag_count = 2 (`volume_surge + holdings_decrease`) |
| Peak volume ratio | **13.55×** normal before one issuance |
| Pattern | Stock peaked → officers reduced holdings → CB issued |

이오플로우 (294090) had the highest Beneish at +9.52 but its CB volume spike was from
2021, not 2022 — the anomaly years don't overlap. 서남, 지니너스, 블리츠웨이 had high
Beneish scores but no CB manipulation flags — accounting anomaly without the financing
pattern.

This three-dataset cross-join (Beneish scores + CB events + timing alignment) would
require multiple VLOOKUP steps or a SQL query in a traditional workflow. Here it took
one question.

---

## More Queries to Try

```
# Full risk profile for any company
"Show me everything on 피씨엘"

# Anomaly screening
"What are the top 10 most anomalous companies in 2023?"
"Which companies had both a high Beneish score and officer selling?"

# CB/BW patterns
"Which companies issued CBs with volume surges before issuance?"
"Show me all two-flag CB events across the whole dataset"

# Network / insider
"Are any officers connected across multiple flagged companies?"
"Who are the major holders of 에코앤드림?"

# Price investigation
"What did 캔버스엔's stock do in the 60 days before that 13x volume CB?"

# Timing / disclosure
"Did 롤링스톤 have any disclosure anomalies?"
```

---

## Tool Reference

| Tool | What it does | When to call it |
|---|---|---|
| `lookup_corp_code` | Name/ticker → 8-digit corp_code | Always first — required by all other tools |
| `get_company_summary` | All signals aggregated | First call for any company investigation |
| `get_beneish_scores` | M-Score + 8 components per year | Deep-dive on accounting anomalies |
| `get_cb_bw_events` | CB/BW issuances with flag counts | Investigating financing patterns |
| `get_price_volume` | OHLCV for a date window | Confirming price/volume around events |
| `get_officer_holdings` | Insider ownership changes | Checking for insider selling |
| `get_major_holders` | 5%+ block holders | Understanding ownership structure |
| `get_timing_anomalies` | Disclosures preceding price moves | Information leakage analysis |
| `get_officer_network` | Cross-company officer centrality | Finding connected individuals |
| `search_flagged_companies` | Ranked anomaly screen | Starting point for any new investigation |

---

## Data Coverage

- **Universe:** KOSDAQ listed companies (KOSPI companies return no match — by design)
- **Beneish scores:** 7,447 company-years, 2018–2023; 1,250 flagged above −1.78
- **CB/BW events:** 3,667 events; 756 flagged
- **Price/volume:** ±60-day windows around CB/BW events (not full continuous history)
- **Timing anomalies:** 32,741 events; 3,373 flagged (material disclosures + ≥5% price move + ≥2× volume)
- **Last updated:** March 8, 2026

---

## Important Notes

- **These are hypotheses, not conclusions.** All signals require human judgment before
  any action. This tool surfaces patterns for review — not findings of fact.
- The pipeline uses only publicly available data (DART, KRX, SEIBRO).
- Scores above −1.78 (Beneish threshold) indicate elevated manipulation *risk*, not
  confirmed manipulation.
