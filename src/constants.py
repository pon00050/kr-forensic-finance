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
EXERCISE_PEAK_WINDOW_CALENDAR_DAYS = 5  # calendar days, intentionally NOT trading days — see Fix 1B
VOLUME_SURGE_RATIO = 3.0
HOLDINGS_DECREASE_RATIO = 0.95
PRICE_WINDOW_TRADING_DAYS = 60  # trading days (not calendar days) — see KI-043
VALID_OHLCV_BACKENDS = ("pykrx", "fdr", "yfinance")

# Timing anomaly thresholds
TIMING_PRICE_CHANGE_PCT = 5.0
TIMING_VOLUME_RATIO = 2.0
TIMING_BORDERLINE_PRICE_PCT = 3.0
# DART listing API returns YYYYMMDD only (no time); 18:00 KST assumed for gap_hours.
# Market close: 15:30 KST (15.5 decimal hours). Gap = 18.0 - 15.5 = 2.5 hours.
TIMING_GAP_HOURS_ASSUMED = 2.5
# Prior-day filings: ~18:00 KST filing → 09:00 open next day → 15.0 h gap.
TIMING_GAP_HOURS_PRIOR_DAY = 15.0

# Officer network
OFFICER_FLAG_THRESHOLD = 2

