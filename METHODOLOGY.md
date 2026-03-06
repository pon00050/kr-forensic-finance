# Methodology

## What This Pipeline Investigates — and How

This document describes the investigative logic of the pipeline: what data it collects,
why each piece matters, and how the layers combine into a ranked list of companies
warranting human review. It is intended for researchers, journalists, analysts, and
regulators who want to understand the approach before engaging with the data or code.

**Important framing:** Every output of this pipeline is a hypothesis, not a conclusion.
The pipeline identifies statistical anomalies and structural patterns. Whether any
specific company engaged in misconduct is a judgment that requires human investigation,
legal expertise, and access to non-public information. The pipeline provides the map;
investigators supply the judgment.

---

## The Pattern Being Investigated

Korea's KOSDAQ market has a well-documented manipulation playbook, documented in
regulatory enforcement records, academic literature, and investigative journalism.
It centers on a financial instrument called a convertible bond (CB) or bond with
warrants (BW) — collectively CB/BW.

A legitimate CB is a routine financing tool. A company issues debt, and bondholders
have the right to convert it into equity at a pre-agreed price. The problem emerges
when the conversion price is not fixed — when it contains *repricing provisions* that
allow the price to be ratcheted down if the stock falls. In the extreme form of this
scheme:

1. A financially distressed company issues a CB with aggressive repricing provisions.
2. The stock price falls — or is helped to fall — triggering a lower conversion price.
3. The bondholder (often a private equity vehicle or special purpose company) converts
   at the reduced price, receiving more shares for the same money.
4. The new shares are sold into the market. Retail investors, who held existing shares,
   are diluted. The bondholder profits.

If this process is coordinated with stock price manipulation, strategic timing of
material announcements, or insider trading by company officers, it becomes a mechanism
for systematic wealth transfer from retail investors to insiders.

The data for this entire chain exists in public disclosures. The pipeline makes it
tractable to screen for it at scale.

---

## The Data Spine

### Step 1 — Who are the companies?

The pipeline begins with DART (Data Analysis, Retrieval and Transfer System), Korea's
official financial disclosure platform. Every listed company on KOSDAQ files annual
reports, interim reports, and event-based disclosures here.

We pull ten financial statement line items for every KOSDAQ company across five years:
receivables, revenue, COGS, SGA, PPE, depreciation, total assets, long-term debt, net
income, and operating cash flow. This becomes `company_financials.parquet` — the ground
layer. It is messy by design: real-world Korean small-cap financials have missing
depreciation entries, mid-stream accounting method switches, and companies reporting
under "nature of expense" classification (which omits the COGS line entirely). The
pipeline handles each of these explicitly rather than silently dropping the affected rows.

### Step 2 — Which companies look financially distorted?

The Beneish M-Score (Beneish 1999) is an eight-variable formula designed to detect
earnings manipulation in financial statements. It asks: are receivables growing faster
than revenue? Is gross margin deteriorating? Is depreciation slowing suspiciously?
Is the quality of accruals declining?

Each question compares year T to year T-1 across the eight components, weighted by
their empirical association with manipulation in the original study sample. A score
above −1.78 is the threshold Beneish identified as warranting investigation.

An important calibration note: the M-Score was developed on US GAAP companies. Korean
IFRS accounting norms differ in several ways that inflate false positive rates —
particularly for biotech and pharmaceutical companies, where R&D capitalisation
practices create structurally high scores that do not indicate manipulation. The pipeline
flags these as high-false-positive-risk and treats them separately.

The output is `beneish_scores.parquet`: roughly 5,500 company-year scores across 1,400+
companies from 2020 to 2023. This answers the question: *which companies look
financially distorted?*

### Step 3 — Where are the CB/BW events?

A separate extraction pulls every CB and BW issuance disclosure from DART. This becomes
`cb_bw_events.parquet` — 3,672 events across 919 companies. Each row records a moment
in time: a specific company issued a specific convertible instrument on a specific date,
at a specific conversion price, with specific repricing provisions.

This table is the spine of the investigation. It says: *something happened here.* The
rest of the pipeline is asking whether what happened was legitimate.

### Step 4 — What did the stock do?

For each CB/BW event, daily price and volume data is pulled from KRX (the Korea Exchange)
for the ±60-day window surrounding the event. This becomes `price_volume.parquet` —
245,000 daily observations. It captures the stock's behaviour immediately before and
after each issuance.

### Step 5 — What did officers do?

Executive and major shareholder holding changes are extracted from DART. This becomes
`officer_holdings.parquet` — nearly 7,000 records of individual insiders changing their
position. The timing of these changes relative to CB/BW events is one of the most
direct signals of whether insiders were positioning ahead of dilution they knew was
coming.

### Step 6 — When were material announcements made?

`disclosures.parquet` holds the timestamp of every material disclosure filing for
921 companies across the pipeline's date range. This table enables a specific test:
were price movements on disclosure days in flagged companies statistically more extreme
than in a control group of unflagged companies? If insiders were trading ahead of
announcements, the stock should move before the announcement — not on it.

### Step 7 — What are the bond ISINs?

To access the SEIBRO repricing register (Korea's official record of CB/BW conversion
term changes), each bond's 12-character ISIN is required. DART filings do not contain
ISINs — they are issued by the Korea Securities Depository after registration, which
happens *after* the DART filing. The ISINs are sourced instead from the FSC
(Financial Services Commission) bond issuance registry, queried by company name.
This produces `bond_isin_map.parquet` — 1,859 validated ISINs across 656 companies.

---

## The Three Confirmation Layers

With the data spine in place, the pipeline applies three overlapping tests to each
CB/BW event. A company that appears in all three is the highest-priority candidate
for investigation.

### Layer 1 — Price and volume behaviour

`cb_bw_timelines.py` scores each event against four flags:

- **Peak before issuance:** Did the stock price peak and begin declining in the weeks
  before the CB was issued? This is consistent with insiders knowing dilution was coming.
- **Volume surge:** Did trading volume spike anomalously around the event window?
- **Rapid repricing:** Was the conversion price repriced downward within months of
  issuance? Frequent repricing suggests either a deliberately structured downward spiral
  or a stock being managed downward.
- **Exercise clustering:** Did multiple conversions happen in tight time windows, creating
  concentrated dilution pressure?

A company scoring three or four flags across multiple events is a Tier 1 candidate.

### Layer 2 — Disclosure timing

`fdr_disclosure_leakage.py` applies a Benjamini-Hochberg false discovery rate correction
to test whether disclosure-day price moves in flagged companies are systematically more
extreme than in a control group of 811 unflagged companies. The test uses all disclosure
events — not just the extreme ones — to avoid the statistical trap of a pre-filtered
input inflating the apparent signal.

The current result across 822 test events: mild p-value enrichment near zero (72 events
versus an expected 41 in the [0, 0.05) bin), but no events survive BH correction at
q = 0.05. This is an honest, informative result: the mild enrichment is consistent with
a weak pre-disclosure signal that does not rise to statistical significance after
multiple testing correction. It does not exonerate the flagged companies; it means the
disclosure channel alone is not strong enough evidence to act on.

### Layer 3 — Officer networks

`officer_network.py` builds a graph where nodes are individuals (officers and directors)
and edges connect individuals who appear on the boards of multiple flagged companies.
Network centrality identifies individuals who are structurally positioned across the
highest-anomaly companies — a pattern consistent with coordinated activity across
multiple vehicles.

95 individuals appear in two or more flagged companies. A handful appear in three
or more, with bootstrap-stable centrality scores. These are not fraud findings; they
are investigative leads.

---

## The Missing Layer — SEIBRO Repricing Data

The pipeline has a fourth confirmation layer that is not yet operational: direct access
to SEIBRO's repricing and exercise history.

SEIBRO records every instance where a CB/BW conversion price was changed: the original
price, the new price, the date, and the regulatory rationale. If the repricing pattern
of a specific bond shows prices falling in step with coordinated stock price movements,
that is the clearest mechanical signature of a structured scheme.

The SEIBRO REST API requires a registered key. The key has been applied for and is
pending activation. Once active, `extract_seibro_repricing.py` will run across all
1,859 ISINs in the map, and the repricing and exercise data will be incorporated into
the timeline scoring. The five Tier 1 leads identified so far all have ISINs and will
be the first queried.

---

## What the Output Is — and What It Is Not

The pipeline produces ranked lists: companies where multiple independent signals
converge. The five highest-priority candidates currently identified — referred to
internally as Tier 1 leads — all exhibit three or more of the following:

- Beneish M-Score above threshold in multiple years
- CB/BW events with price peak-before-issuance and volume surge flags
- Receivables-to-revenue ratios that substantially exceed peers
- Structural unprofitability despite repeated fundraising
- Investment entity majority control with documented changes in bondholder composition

**These are hypotheses for human review.** The pipeline cannot determine intent. It
cannot access non-public communications, trading account records, or the identities
behind nominee structures. It cannot subpoena. It cannot rule out legitimate
explanations for any individual signal.

What it can do — and does — is compress weeks of manual DART trawling into a
prioritised screen, ensure reproducibility, and make the methodology transparent
enough that anyone can verify, challenge, or extend it.

That is the point of making it public.

---

## Data Sources

| Source | What it provides |
|--------|-----------------|
| DART (`opendart.fss.or.kr`) | Financials, CB/BW events, officer holdings, disclosures, bondholder registers |
| KRX (`data.krx.co.kr`) | OHLCV price/volume data via PyKRX |
| FSC bond registry (`data.go.kr`, dataset 15043421) | Bond ISINs by company name |
| SEIBRO (`data.go.kr`, datasets 15001145, 15074595) | CB/BW repricing and exercise history *(pending key activation)* |

All sources are publicly accessible. No proprietary data is used.

---

## Limitations and Honest Caveats

**The M-Score was not calibrated for Korean markets.** It was developed on US companies
in the 1990s. False positive rates are higher for Korean KOSDAQ companies, particularly
in sectors with high R&D capitalisation. Every flag should be treated as a starting
point for investigation, not a conclusion.

**SEIBRO data is not yet integrated.** The repricing layer — the most direct evidence
for structured dilution schemes — is not yet in the scoring. Current scores reflect
what is visible without it. Companies not yet flagged may be flagged once repricing
data is available; companies currently flagged may see their scores revised.

**The disclosure leakage test has limited power.** Price/volume data is only available
for ±60-day windows around CB/BW events. Disclosures that fall outside these windows
cannot be matched to price data, limiting the test's coverage to roughly 23% of
flagged-company disclosures.

**Officer networks require careful interpretation.** Appearing in multiple flagged
companies does not make a person a suspect. Experienced small-cap board members
legitimately serve on multiple boards. The network analysis identifies structural
position, not culpability.
