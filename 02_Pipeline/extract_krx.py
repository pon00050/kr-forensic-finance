"""
extract_krx.py — KRX data extraction layer.

Fetches daily OHLCV and short selling data from KRX via PyKRX.
All output is written to 01_Data/raw/krx/ as CSV.
Scripts are idempotent — re-running overwrites with identical data.

PyKRX does not require an API key. It scrapes the KRX data portal,
so network failures are expected — each fetch retries up to 3 times.

Usage:
    python 02_Pipeline/extract_krx.py --market KOSDAQ --start 2020-01-01 --end 2025-12-31
    python 02_Pipeline/extract_krx.py --ticker 005930  # single ticker
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd
from pykrx import stock as krx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
RAW_KRX = ROOT / "01_Data" / "raw" / "krx"


def _retry(fn, *args, retries: int = 3, delay: float = 2.0, **kwargs) -> pd.DataFrame:
    """Call fn(*args, **kwargs) with up to `retries` retries on exception."""
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == retries:
                raise
            log.warning("Attempt %d/%d failed: %s — retrying in %.1fs", attempt, retries, exc, delay)
            time.sleep(delay)
    raise RuntimeError("Unreachable")


def fetch_listed_companies(market: str = "KOSDAQ") -> pd.DataFrame:
    """
    Return DataFrame of all companies listed on market with columns:
    ticker, corp_name, sector, listing_date.

    market: 'KOSDAQ' | 'KOSPI' | 'KONEX'
    """
    log.info("Fetching listed companies for market: %s", market)
    tickers = krx.get_market_ticker_list(market=market)
    rows = []
    for ticker in tickers:
        try:
            name = krx.get_market_ticker_name(ticker)
            rows.append({"ticker": ticker, "corp_name": name, "market": market})
            time.sleep(0.05)
        except Exception as exc:
            log.warning("Ticker name fetch failed %s: %s", ticker, exc)

    df = pd.DataFrame(rows)
    out = RAW_KRX / f"listed_{market.lower()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    log.info("Saved %d tickers to %s", len(df), out)
    return df


def fetch_ohlcv(
    ticker: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV for ticker from start_date to end_date.

    start_date / end_date: 'YYYYMMDD'

    Output: 01_Data/raw/krx/ohlcv/{ticker}.csv
    """
    log.debug("Fetching OHLCV %s %s→%s", ticker, start_date, end_date)
    df = _retry(
        krx.get_market_ohlcv_by_date,
        start_date,
        end_date,
        ticker,
    )
    if df is None or df.empty:
        log.warning("No OHLCV data for %s", ticker)
        return pd.DataFrame()

    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    df.insert(0, "ticker", ticker)

    out = RAW_KRX / "ohlcv" / f"{ticker}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return df


def fetch_short_balance(
    ticker: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    Fetch daily short selling balance for ticker.

    Output: 01_Data/raw/krx/short/{ticker}.csv
    """
    log.debug("Fetching short balance %s %s→%s", ticker, start_date, end_date)
    try:
        df = _retry(
            krx.get_shorting_balance_by_date,
            start_date,
            end_date,
            ticker,
        )
    except Exception as exc:
        log.warning("Short balance fetch failed %s: %s", ticker, exc)
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    df.insert(0, "ticker", ticker)

    out = RAW_KRX / "short" / f"{ticker}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return df


def run(
    market: str,
    start_date: str,
    end_date: str,
    ticker: str | None = None,
) -> None:
    """
    Main KRX extraction run.

    If ticker is given, fetches only that ticker.
    Otherwise fetches all tickers in market.

    Note: corp_ticker_map is built by extract_corp_ticker_map.py (standalone).
    """
    # Normalise dates to YYYYMMDD (accept YYYY-MM-DD too)
    start_date = start_date.replace("-", "")
    end_date = end_date.replace("-", "")

    if ticker:
        tickers = [ticker]
    else:
        krx_df = fetch_listed_companies(market)
        tickers = krx_df["ticker"].tolist()

    total = len(tickers)
    for i, tkr in enumerate(tickers, 1):
        log.info("[%d/%d] %s OHLCV + short", i, total, tkr)
        fetch_ohlcv(tkr, start_date, end_date)
        time.sleep(0.3)
        fetch_short_balance(tkr, start_date, end_date)
        time.sleep(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract KRX OHLCV + short selling data")
    parser.add_argument("--market", default="KOSDAQ", choices=["KOSDAQ", "KOSPI", "KONEX"])
    parser.add_argument("--start", default="20200101", help="Start date YYYYMMDD")
    parser.add_argument("--end", default="20251231", help="End date YYYYMMDD")
    parser.add_argument("--ticker", help="Single KRX ticker (6-digit)")
    args = parser.parse_args()
    run(args.market, args.start, args.end, args.ticker)
