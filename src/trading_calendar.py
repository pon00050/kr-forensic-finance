"""src/trading_calendar.py — KRX trading calendar helper (KI-043).

Wraps exchange_calendars XKRX to provide explicit Korean trading-day logic.
Replaces the previous implicit approach of relying on pykrx returning empty
data on non-trading days.

Coverage:
    - All statutory Korean public holidays (설날, 추석, 공휴일)
    - KRX-specific market closures (year-end half-days, etc.)
    - Historical data 2018–2023: complete

Gap:
    - 임시공휴일 (ad-hoc government holidays) require a community PR to
      exchange_calendars and lag by days to weeks. For live/current-year use,
      cross-check against data.go.kr 특일정보 API (dataset 15012690).

Usage:
    from src.trading_calendar import is_trading_day, trading_day_offset
    from src.trading_calendar import trading_days_in_range
"""

from __future__ import annotations

import pandas as pd

_xkrx = None


def _calendar():
    """Lazy-load and cache the XKRX calendar instance."""
    global _xkrx
    if _xkrx is None:
        from exchange_calendars import get_calendar
        _xkrx = get_calendar("XKRX")
    return _xkrx


def is_trading_day(date: str | pd.Timestamp) -> bool:
    """Return True if date is a KRX trading session."""
    return bool(_calendar().is_session(pd.Timestamp(date).normalize()))


def trading_days_in_range(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DatetimeIndex:
    """Return all KRX trading sessions between start and end (inclusive)."""
    return _calendar().sessions_in_range(
        pd.Timestamp(start).normalize(),
        pd.Timestamp(end).normalize(),
    )


def trading_day_offset(date: str | pd.Timestamp, n: int) -> pd.Timestamp:
    """Return the KRX session n trading days from date.

    n > 0 = forward (later dates), n < 0 = backward (earlier dates).
    If date is not itself a session, snaps to the nearest session in
    the offset direction before counting.

    Examples:
        trading_day_offset("2021-02-15", -60)  # 2020-11-16
        trading_day_offset("2021-02-15",  60)  # 2021-05-12
    """
    sessions = _calendar().sessions
    ts = pd.Timestamp(date).normalize()
    idx = int(sessions.searchsorted(ts))
    target = max(0, min(len(sessions) - 1, idx + n))
    return sessions[target]
