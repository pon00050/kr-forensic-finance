"""
_pipeline_helpers.py — Shared utilities for pipeline extractor scripts.

Imported by extract_cb_bw.py, extract_disclosures.py, extract_officer_holdings.py,
extract_seibro_repricing.py, extract_bondholder_register.py, and
extract_revenue_schedule.py to avoid duplicating boilerplate.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

# ─── DART status code constants ───────────────────────────────────────────────
DART_STATUS_OK = "000"
DART_STATUS_NOT_FOUND = "013"
DART_STATUS_RATE_LIMIT = "020"


def _dart_api_key() -> str:
    """Retrieve and validate DART_API_KEY from environment."""
    key = os.getenv("DART_API_KEY", "")
    if not key or key == "your_opendart_api_key_here":
        raise EnvironmentError("DART_API_KEY not set.")
    return key


def _norm_corp_code(code) -> str:
    """Normalise a corp_code to an 8-character zero-padded string."""
    return str(code).zfill(8)


# Browser-like headers for DART HTML viewer requests
DART_HTML_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://dart.fss.or.kr/",
}


def _parse_krw(raw, unit_multiplier: int = 1) -> int | None:
    """Parse a KRW integer from raw table cell value. Handles comma formatting,
    parenthetical negatives, and unit multiplier (e.g. 1000 for 천원 tables)."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip().replace(",", "").replace("%", "")
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        val = int(float(s))
        return -val * unit_multiplier if negative else val * unit_multiplier
    except (ValueError, TypeError):
        return None


def _detect_unit_multiplier(html: str) -> int:
    """Return 1000 if the first 2000 chars of html mention 천원, else 1."""
    snippet = html[:2000]
    if "천원" in snippet or "(단위: 천원)" in snippet:
        return 1000
    return 1


# ─── Shared functions (consolidated from duplicates) ─────────────────────────

def fetch_with_backoff(
    url: str, params: dict, max_retries: int = 4, base_delay: float = 2.0
) -> dict:
    """GET request with exponential backoff on DART Error 020 (rate limit)."""
    delays = [base_delay * (2 ** i) for i in range(max_retries)]
    last_exc: Exception | None = None
    for attempt, delay in enumerate([0.0] + delays):
        if delay:
            log.warning("DART rate limit — retrying in %.0fs (attempt %d/%d)", delay, attempt, max_retries)
            time.sleep(delay)
        try:
            resp = requests.get(url, params=params, timeout=30)
            data = resp.json()
            if str(data.get("status", "")) == DART_STATUS_RATE_LIMIT:
                raise Exception("Error 020 rate limit")
            return data
        except Exception as exc:
            last_exc = exc
            if DART_STATUS_RATE_LIMIT not in str(exc):
                raise
    raise last_exc  # type: ignore[misc]


def parse_amount(raw) -> float | None:
    """Parse a DART thstrm_amount string to float. Returns None on failure."""
    if raw is None:
        return None
    s = str(raw).replace(",", "").replace(" ", "").strip()
    if not s or s in ("nan", "None", "-", ""):
        return None
    # Handle negative parenthetical format: (1234) → -1234
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def fetch_annual_report_rcept_no(
    corp_code: str,
    dart,
    year: int,
) -> str | None:
    """Find 사업보고서 receipt number for a given fiscal year.

    Search window: {year}0401 to {year+1}0630 (covers late filers).
    Returns the rcept_no of the most recently filed annual report (amendment
    takes precedence over original), or None if not found.
    """
    bgn_de = f"{year}0401"
    end_de = f"{year + 1}0630"
    try:
        df = dart.list(corp_code, start=bgn_de, end=end_de, kind="A")
    except Exception as exc:
        log.debug("dart.list failed for corp_code=%s year=%d: %s", corp_code, year, exc)
        return None

    if df is None or len(df) == 0:
        return None

    mask = (
        df["report_nm"].str.contains("사업보고서", na=False)
        & ~df["report_nm"].str.contains("반기|분기|수정", na=False)
    )
    annual = df[mask]
    if len(annual) == 0:
        return None

    annual = annual.sort_values("rcept_dt", ascending=False)
    return str(annual.iloc[0]["rcept_no"]).strip()


def write_json(path: Path, data: Any) -> None:
    """Write data as JSON to path, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
