# A Day in the Life: Traditional Workflow vs. MCP

> **Scenario:** It's Monday morning. Your manager asks:
> *"Screen for the most suspicious KOSDAQ companies right now. I need the top 3
> with full profiles by the meeting in 2 hours."*

---

## The Traditional Way

### Step 1 — Get the company list (20 min)

Open the KRX website. Download the full KOSDAQ listing as an Excel file (~1,800 rows).
Open DART (dart.fss.or.kr). You need financial statements to calculate Beneish scores —
but DART doesn't give you pre-calculated scores. You'll need to pull raw financials.

### Step 2 — Download financials (40 min)

For each of the ~800 KOSDAQ companies you care about, you either:
- Use OpenDartReader in Python to pull each company's XBRL data, or
- Download individual 사업보고서 PDFs from DART and manually extract the numbers

Either way this takes time. And DART's XBRL coverage is inconsistent — some companies
file with missing fields. You discover this only after you've already downloaded everything.

### Step 3 — Calculate Beneish scores in Excel (30 min)

Paste the financial data into a spreadsheet. Build formulas for each of the 8 components:
DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI, TATA. The M-Score formula itself requires careful
prior-year references. Any missing field produces a #DIV/0! or #VALUE! error that silently
corrupts your score. You build a column to flag errors and manually inspect.

Result: a ranked list of company-years by M-Score. You still don't know which ones also
had suspicious CB activity or insider selling.

### Step 4 — Cross-reference CB filings (25 min)

Go back to DART. Search for 전환사채 (convertible bond) filings. There's no bulk download.
You click through filings one by one for your top 10 suspects. You check the issue date,
exercise price, and whether there was a volume spike — which requires opening KRX market
data separately, downloading OHLCV CSVs, and doing a VLOOKUP to match by ticker and date.

### Step 5 — Check insider holdings (15 min)

Go back to DART again. Search for 임원주요주주소유보고서 for each company. Download.
Cross-reference with your CB event dates to see if officers sold before issuance. Another
round of VLOOKUPs.

### Step 6 — Write up the summary (20 min)

Manually compile your findings into a summary table. Format it. Double-check the numbers
because you've been copying between so many sources.

---

**Total time: ~2.5 hours — and you're already late for the meeting.**

You also have no audit trail for which data source you used for which number. And if the
manager asks a follow-up question about a different company, you start over.

---

## The MCP Way

### Everything above — one conversation, ~4 minutes

---

**You:** *"Screen for the most suspicious KOSDAQ companies in 2023 with Beneish scores above 2.
Give me the top 3 with full profiles."*

**Step 1 — Screen the universe**

```
search_flagged_companies(year=2023, min_m_score=2, limit=5)
```

16 companies flagged. Top 5:

| Rank | Company | Ticker | M-Score 2023 |
|---|---|---|---|
| 1 | 한울비앤씨 | 214870 | **+4.14** |
| 2 | 샌즈랩 | 411080 | **+3.97** |
| 3 | 마이크로디지탈 | 305090 | **+3.41** |
| 4 | 에이에프더블류 | 312610 | +3.05 |
| 5 | 나노씨엠에스 | 247660 | +2.81 |

**Step 2 — Full profiles on the top 3, in parallel**

```
get_company_summary("00820389")   # 한울비앤씨
get_company_summary("01242241")   # 샌즈랩
get_company_summary("01267967")   # 마이크로디지탈
```

All three return simultaneously. Beneish history, CB counts, timing anomaly counts,
officer network status — in one call each.

**Step 3 — Drill into Beneish components and CB events for the #1 suspect**

```
get_beneish_scores("00820389", years=[2022, 2023])
get_cb_bw_events("00820389")
get_beneish_scores("01267967", years=[2021, 2022, 2023])
```

---

## Side-by-Side Summary: Top 3 Suspects

### #1 — 한울비앤씨 (214870)

| Dimension | Finding |
|---|---|
| Beneish 2023 | **+4.14** — Critical |
| Beneish trend | Clean (−4.98) in 2018–2022, then sudden spike in 2023 |
| Key driver | **DSRI = 6.35** — receivables grew 6× faster than revenue in one year |
| Secondary driver | **GMI = 2.16** — gross margins deteriorating |
| CB issuances | 8 total, none flagged — financing pattern looks clean |
| Timing anomalies | 8 flagged out of 65 disclosure events |
| Officer network | Not connected to other flagged companies |

**Interpretation:** The 2023 Beneish spike is almost entirely driven by a receivables explosion
(DSRI 6.35) against falling revenue (SGI 0.58). Revenue down 42%, receivables ballooning —
classic channel stuffing or fictitious sales hypothesis. Clean prior history makes this
more notable, not less: this isn't a structurally weak company, something changed in 2023.

---

### #2 — 샌즈랩 (411080)

| Dimension | Finding |
|---|---|
| Beneish 2023 | **+3.97** — Critical |
| History | Only one year of data (recent listing) |
| CB issuances | 0 — no convertible bond activity |
| Timing anomalies | 0 flagged |
| Officer network | No data |

**Interpretation:** High Beneish score but minimal corroborating signals. Single data point
with no financing or disclosure anomalies. Needs more years of data before drawing
conclusions. Lower investigative priority than #1 or #3.

---

### #3 — 마이크로디지탈 (305090)

| Dimension | Finding |
|---|---|
| Beneish 2021 | −1.49 (borderline) |
| Beneish 2022 | **+0.01** (flagged) |
| Beneish 2023 | **+3.41** — Critical |
| Trend | Three consecutive years of deterioration |
| Key 2023 driver | **DSRI = 6.78** — same receivables pattern as 한울비앤씨 |
| Secondary 2022 driver | **GMI = 7.14** — severe margin collapse in 2022 |
| CB issuances | 3 total, none flagged |
| Timing anomalies | 5 flagged out of 41 |

**Interpretation:** Three consecutive deteriorating years is structurally different from
a one-year spike. The 2022 GMI of 7.14 (margins collapsed by 86%) followed by 2023 DSRI
of 6.78 (receivables explosion) suggests a two-stage deterioration: first profitability
eroded, then revenue quality eroded. This is the pattern most consistent with a company
trying to paper over declining fundamentals with aggressive revenue recognition.

---

## Time Comparison

| Task | Traditional | MCP |
|---|---|---|
| Screen 800+ companies by M-Score | 40 min (download + calculate) | 3 seconds |
| Pull full profile for 3 companies | 30 min (DART × 3) | 6 seconds (parallel calls) |
| Beneish component breakdown | 20 min (formula audit) | 2 seconds |
| CB event cross-reference | 25 min (DART manual search) | 2 seconds |
| Timing anomaly check | 15 min (KRX download + match) | 2 seconds |
| Write summary | 20 min | Already done — it's this output |
| **Total** | **~2.5 hours** | **~4 minutes** |

---

## What MCP Does Not Replace

MCP accelerates the **data gathering and pattern detection** layer. It does not replace:

- **Human judgment** on whether a pattern is material
- **Domain knowledge** about why a specific company's receivables might legitimately spike
  (e.g., a large government contract)
- **On-the-ground research** — calling the IR department, reading the footnotes
- **Legal conclusions** — these are hypotheses for further investigation, not findings of fact

Think of MCP as giving you the analyst's morning brief in 4 minutes instead of 2.5 hours,
so you spend the remaining time on the work that actually requires a human.

---

## Follow-Up Questions You Can Ask Immediately

With the traditional workflow, a follow-up question means starting over. With MCP:

```
"What did 한울비앤씨's stock do in 2023 while those receivables were ballooning?"
"Who are the major holders of 마이크로디지탈?"
"Are any officers of 한울비앤씨 connected to other flagged companies?"
"Show me all KOSDAQ companies with DSRI above 5 in any year"
```

Each takes seconds. The audit trail is the conversation itself.
