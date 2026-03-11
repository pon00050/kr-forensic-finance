# The Long Way Around
### How a forensic accountant would research 에코앤드림 the traditional way

---

It's 8:47 on a Tuesday morning. Jihoon has a meeting at 2pm.

His manager dropped a name on his desk yesterday afternoon: **에코앤드림 (101360)**. A
tip came in from a contacts network. Nothing specific — just *"take a look, something
feels off."* Jihoon has been in forensic accounting for six years. He knows what "take a
look" means. It means build a full risk profile by the meeting.

He makes coffee and opens his laptop.

---

## 9:00 AM — DART, Round One

He starts where every Korean forensic accountant starts: **dart.fss.or.kr**.

He types 에코앤드림 into the search bar. The portal returns a list of filings — hundreds
of them, spanning years. There's no summary view. No risk score. No flag. Just a
chronological list of document titles in Korean, some with 기재정정 (amendment) notices
attached.

He needs the 사업보고서 — the annual business report. He clicks the most recent one.
A PDF opens. It's 284 pages.

He starts with the financial statements on page 187. The numbers he needs for Beneish —
receivables, revenue, gross profit, assets, depreciation, SG&A, debt — are scattered
across four separate tables: the balance sheet, the income statement, the cash flow
statement, and the notes. The notes reference sub-tables. Some sub-tables are on pages
he has to scroll back to find.

He opens Excel. He starts a new workbook. He types the numbers in by hand.

Then he realizes he needs the *prior year* figures too, because Beneish is an index — it
measures change, not level. That means he needs the 2022 사업보고서 as well. He goes back
to DART, finds it, opens another 260-page PDF, and starts copying again.

It's 9:51. He has two years of raw numbers and a half-built spreadsheet.

---

## 10:15 AM — The Beneish Formula Problem

He pulls up the Beneish formula from a paper he saved years ago. Eight components. He
starts building the Excel formulas.

DSRI is straightforward — receivables divided by revenue, indexed to prior year. He gets
1.42. Fine.

GMI gives him trouble. Gross margin calculation requires revenue and COGS, but 에코앤드림's
income statement uses a functional format that buries COGS inside "매출원가" which is
reported net of some adjustments described in Note 18. He flips to Note 18. It references
Note 22. He finds Note 22. He adjusts his formula.

LVGI is worse. He needs total debt — but the balance sheet shows short-term borrowings,
current portion of long-term debt, bonds payable, and lease liabilities in four separate
lines. Are lease liabilities debt for this purpose? He checks the original Beneish (1999)
paper. IFRS didn't exist in 1999. He makes a judgment call and documents it in a comment.

DEPI requires depreciation. It's not on the face of the income statement. It's in the
cash flow statement, line 37, buried in "비현금 항목 조정." He finds it.

By 10:15 he has a working M-Score: **−2.41 for 2022**. Below the −1.78 threshold.
Not flagged.

He stares at it. His gut says something is wrong but the score says it isn't. He knows
from experience that the Beneish threshold was calibrated on US companies in the 1990s.
Korean KOSDAQ small-caps are different. But he has no Korean-calibrated threshold to
reference. He notes the LVGI of 2.23 and the rising SGAI and moves on.

It's 10:31.

---

## 10:35 AM — Convertible Bonds

He goes back to DART. This time he filters by filing type: **전환사채관련사채권발행**
(convertible bond issuance). Three results come up, in 2016, 2019, and 2021.

He clicks the 2021 filing. It's a 주요사항보고서. He reads through it — the issue date,
the face value, the conversion price: 39,869 KRW. He notes it.

Now he needs to know if the stock price peaked before issuance. That requires price data.

He opens a new tab: **data.krx.co.kr**. He navigates to the OHLCV download page, selects
에코앤드림 (ticker 101360), sets the date range to November 2020 through March 2021, and
clicks download. A CSV arrives. He opens it.

He scans the Close column manually. The peak appears to be around early January. He adds
a MAX formula. **January 8, 2021: 39,300 KRW.** The CB was issued 38 days later at 39,869
KRW — nearly exactly at the peak.

He notes the coincidence. But "coincidence" is all he can call it right now.

Now he needs volume. Was there abnormal trading at that peak? He looks at the Volume
column for Jan 8. He sees a big number. But big compared to what? He needs a baseline.
He calculates the 60-day average volume manually with an AVERAGE formula over the prior
60 trading days. The ratio comes out to roughly **300×**.

Three hundred times normal volume on the day the stock peaked, 38 days before a
convertible bond was issued at that price.

He writes **"329× — verify"** in his notes. He doesn't fully trust his own calculation
because he's not certain he counted the trading days correctly around the Chuseok holiday.

It's 11:22. He hasn't eaten breakfast.

---

## 11:30 AM — Disclosure Timing

He remembers there were two filings around that same period — the asset acquisition
disclosures from late December 2020 and early January 2021. He goes back to DART and
pulls them up.

**2020-12-28: 주요사항보고서 (유형자산양수결정)** — an asset acquisition.
**2021-01-08: [기재정정] 주요사항보고서** — an amendment to the same filing.

He cross-references with his KRX price data. On December 28 the stock rose 7.42%. On
January 8 — the same day as the volume peak he just calculated — it rose another 9.02%.

He writes this in his notes. The amendment filing and the volume spike and the CB peak
are all the same date. He draws an arrow connecting them in his notebook. Three
events converging on one day, 38 days before a major financing.

He wants to check whether this pattern exists in any other filings. But to do that
systematically — to check all 21 disclosure events against same-day price and volume
movements — he would need to download the full DART disclosure list, match every filing
date to KRX data, and calculate price/volume changes for each one. That's a full day of
work on its own. He flags it as "incomplete" and moves on.

It's 11:58. The meeting is at 2pm.

---

## 12:10 PM — Ownership Research

He goes back to DART, this time looking for 대량보유상황보고서 — the 5%+ block holder
filings. He finds eight of them. He opens each one.

**김민용** appears repeatedly. The filings show he's the controlling shareholder at around
20%. But the reason codes catch Jihoon's eye: **주식담보대출계약 연장** — stock-pledged
loan rollovers. The man has pledged his own shares as collateral for loans, and he's been
rolling those loans over repeatedly.

Jihoon knows what this means in practice: if the stock falls far enough, the lender calls
the margin, 김민용 is forced to sell, and the price falls further. A controlling
shareholder with pledged shares is a hidden pressure valve.

He also notices that 국민연금 — the National Pension Service, normally a passive long-term
holder — filed a report in April 2024 showing they had sold **170,000 shares**, dropping
their stake below the 5% reporting threshold. Institutional exit. He notes it.

He tries to build a timeline of 김민용's ownership changes in Excel. But each DART filing
is formatted slightly differently. Some list shares held, some list changes from prior
filing. He has to manually reconcile eight PDFs to reconstruct the ownership history. It
takes 35 minutes and he's still not confident his numbers are right because two of the
filings reference "특별관계자" (related parties) whose stakes are bundled in inconsistently.

It's 12:58.

---

## 1:05 PM — The Part He Can't Do at All

He wants to know if any of 에코앤드림's officers or directors appear at other flagged
companies. This is the network question — the one that would tell him whether the same
individuals are running patterns across multiple companies.

There is no tool for this.

DART has 임원·주요주주 소유보고서 filings, but they're organized by company, not by
person. To find whether **김민용** or **김승일** or **김종진** appear at other listed
companies, he would need to:

1. Download the officer holding reports for every KOSDAQ-listed company
2. Extract all individual names
3. Build a cross-reference table
4. Flag the ones that appear at multiple companies that are also flagged on other metrics

That is months of work. He has 55 minutes.

He skips it.

---

## 1:15 PM — Assembly

He has 45 minutes. He opens a PowerPoint and starts building a summary slide.

He has:
- A Beneish score for 2022 (below threshold, with concerning components)
- Notes on the 2021 CB issuance (329× volume, price peak timing — unverified)
- Two disclosure timing observations (Dec 28, Jan 8)
- A partial ownership timeline (김민용 pledged loans, NPS exit)
- A gap where the network analysis should be

He types his conclusions carefully. *"Patterns warrant further review."* *"Volume anomaly
requires confirmation."* *"Officer network analysis not completed due to data constraints."*

He presents at 2pm. His manager asks: *"What about the other companies this management
team has been involved in?"*

Jihoon looks at his notes. *"I wasn't able to get to that today."*

---

## What the Clock Looked Like

| Task | Time spent | Complete? |
|---|---|---|
| Financial statement extraction (2 years) | 51 min | Partial — judgment calls on LVGI |
| Beneish calculation + formula debugging | 44 min | Yes — one year |
| CB event identification + price/volume check | 47 min | Partial — 1 of 3 events fully verified |
| Disclosure timing (2 events only) | 28 min | Partial — 21 events not checked |
| Ownership history reconstruction | 35 min | Partial — related parties unreconciled |
| Officer network analysis | 0 min | **Not done — no tool exists** |
| Summary writeup | 45 min | Yes |
| **Total** | **~4.5 hours** | **Incomplete** |

---

## The Same Profile, With MCP

Seven parallel tool calls. Three seconds of query time.

Every signal Jihoon found — and the one he couldn't — fully populated:

- All three CB events with volume ratios (313×, 329×)
- All 21 disclosure events screened, 2 flagged
- Full ownership timeline, automatically reconciled
- Officer network: three cross-company connections identified, including **김민용** at 디지캡
  and **김승일** at 유니트론텍 — the answer to the manager's question

Jihoon's 4.5 hours of incomplete work, done completely, in the time it takes to pour a
second cup of coffee.

---

*The point is not that Jihoon is slow. The point is that the data was always there —
in DART, in KRX, in the ownership filings — and the bottleneck was never insight.
It was retrieval. MCP eliminates the retrieval bottleneck so the analyst can spend
4.5 hours on what actually requires a human: judgment, context, and the question
that isn't in any database.*
