"""src/constants.py — Shared constants used across analysis and reporting."""

from __future__ import annotations

BENEISH_THRESHOLD = -1.78

# CB/BW manipulation signal flag names
FLAG_REPRICING_BELOW_MARKET = "repricing_below_market"
FLAG_EXERCISE_AT_PEAK = "exercise_at_peak"
FLAG_VOLUME_SURGE = "volume_surge"
FLAG_HOLDINGS_DECREASE = "holdings_decrease"

# CB/BW scoring thresholds
REPRICING_DISCOUNT_RATIO = 0.95
EXERCISE_PEAK_WINDOW_DAYS = 5
VOLUME_SURGE_RATIO = 3.0
HOLDINGS_DECREASE_RATIO = 0.95
PRICE_WINDOW_DAYS = 60

# Timing anomaly thresholds
TIMING_PRICE_CHANGE_PCT = 5.0
TIMING_VOLUME_RATIO = 2.0
TIMING_BORDERLINE_PRICE_PCT = 3.0

# Officer network
OFFICER_FLAG_THRESHOLD = 2

# Beneish extreme outliers — all m_score > 10 company-years, batch-classified
# via DART frmtrm_amount cross-check (Session 48). See 00_Reference/37_Extreme_
# Outlier_Classification.md for full methodology and per-company analysis.
#
# Classification: 23 DART_CONFIRMED, 4 DART_RESTATED (minor accounting adjustments),
# 6 UNVERIFIABLE (2023 data, no 2024 extract). Zero XBRL unit errors found.
# Primary drivers: GMI (36%), DSRI (33%), SGI (30%). Zero TATA-driven.
#
# These are genuine business events (COVID disruptions, M&A, margin collapses)
# that produce mathematically correct but uninformative M-scores. Exclude from
# Beneish threshold calibration and ML training runs.
BENEISH_EXTREME_OUTLIERS: frozenset[tuple[str, int]] = frozenset({
    # --- SGI-driven (revenue jumps 10x+) ---
    ("01051092", 2020),   # PCL: COVID diagnostics boom (SGI=1499, m=1335)
    ("01258428", 2023),   # Prestige Bio: recovery from near-zero 2022 (SGI=111, m=95)
    ("00219440", 2022),   # Humax Holdings: M&A integration (SGI=90, m=76)
    ("00318662", 2021),   # CNT85: post-COVID rebound (SGI=43, m=35)
    ("01274310", 2021),   # EOFlow: medical device ramp (SGI=32, m=29)
    ("01276026", 2022),   # Genome & Company: clinical revenue ramp (SGI=31, m=24)
    ("00366137", 2021),   # KG Eco: M&A, minor restatement 3.5% (SGI=23, m=17)
    ("00919966", 2022),   # Sillagen: biotech revenue restart (SGI=20, m=13)
    ("01153293", 2020),   # JLK: AI diagnostics COVID ramp (SGI=20, m=13)
    ("00363592", 2023),   # Hancom With: revenue restructuring (SGI=14, m=17) [unverifiable]
    # --- GMI-driven (gross margin compression) ---
    ("01207761", 2022),   # Probe It: consolidation scope change, restated 4x (GMI=710, m=372)
    ("00990819", 2022),   # Artist Studio: margin collapse (GMI=135, m=69)
    ("00418379", 2020),   # Nature & Environment: COVID margin pressure (GMI=141, m=67)
    ("00530413", 2021),   # The Codi: COVID margin impact (GMI=107, m=54)
    ("00171265", 2021),   # Paradise: casino/hotel COVID impact (GMI=106, m=54)
    ("01274310", 2022),   # EOFlow: margin compression post-ramp (GMI=43, m=38)
    ("00867973", 2022),   # Seonam: margin compression (GMI=50, m=27)
    ("00305570", 2021),   # Seoul Rieger: COVID margin collapse (GMI=57, m=25)
    ("00863038", 2022),   # Carry: margin compression (GMI=44, m=20)
    ("01494118", 2022),   # Noul: margin compression (GMI=26, m=11)
    ("00623184", 2020),   # Oneul E&M: COVID margin impact (GMI=28, m=11)
    ("00108001", 2023),   # Namhwa Construction: margin (GMI=29, m=13) [unverifiable]
    # --- DSRI-driven (receivables spike) ---
    ("00318662", 2020),   # CNT85: receivables spike pre-rebound (DSRI=50, m=41)
    ("00624518", 2020),   # Dasan Solue: consolidation restatement 33% (DSRI=43, m=35)
    ("01508855", 2022),   # Blitzway: receivables spike (DSRI=37, m=31)
    ("00653194", 2020),   # Aptochrom: receivables spike (DSRI=36, m=30)
    ("00205687", 2020),   # The Rami: receivables spike (DSRI=58, m=24)
    ("00370255", 2020),   # Teckel: minor COGS restatement 2% (DSRI=15, m=16)
    ("00232007", 2020),   # Sangji Construction: receivables spike (DSRI=18, m=12)
    ("00632845", 2021),   # Yellow Balloon: travel/COVID receivables (DSRI=15, m=10)
    ("00660459", 2023),   # Nano CMS: receivables spike (DSRI=25, m=19) [unverifiable]
    ("01267967", 2023),   # Micro Digital: receivables spike (DSRI=18, m=14) [unverifiable]
    ("00390408", 2023),   # Incredible Buzz: receivables spike (DSRI=19, m=13) [unverifiable]
    # --- Also from DQ1 (not m_score > 10 but identified as extreme) ---
    ("01258428", 2022),   # Prestige Bio: genuine low-revenue year (SGI=0.005, m=3.4)
})
