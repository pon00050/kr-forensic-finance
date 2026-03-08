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

