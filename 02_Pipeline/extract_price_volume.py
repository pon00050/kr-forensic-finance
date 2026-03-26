"""
extract_price_volume.py — Phase 2: OHLCV price/volume data.

Loads cb_bw_events.parquet, maps corp_code → ticker, then fetches ±60 trading
days of OHLCV for each CB/BW event window.

Backends (choose with --backend):
  pykrx      Default. Works from laptop only — KRX blocks datacenter IPs.
  fdr        FinanceDataReader. Uses KRX data feed; geo-block status unknown.
             uv add FinanceDataReader (not yet in pyproject.toml — add before use)
  yfinance   Yahoo Finance global CDN. No geo-block documented.
             uv add yfinance (not yet in pyproject.toml — add before use)

Spike procedure (Day 1 — test from Railway):
  1. Deploy a one-off script that calls this with --backend fdr --sample 3
  2. If FDR returns data → replace pykrx in pyproject.toml, mark I1 resolved
  3. If FDR fails → retry with --backend yfinance --sample 3
  4. If both fail → adopt Colab+cache pattern (see 29_Railway_Infrastructure_Analysis.md)

Output:
  01_Data/processed/price_volume.parquet
  Columns: ticker, date, open, high, low, close, volume

Usage:
  python 02_Pipeline/extract_price_volume.py
  python 02_Pipeline/extract_price_volume.py --sample 5 --sleep 0.3
  python 02_Pipeline/extract_price_volume.py --backend fdr --sample 3
  python 02_Pipeline/extract_price_volume.py --backend yfinance --sample 3
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(stream=sys.stdout)],
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
RAW = ROOT / "01_Data" / "raw"
PROCESSED = ROOT / "01_Data" / "processed"

WINDOW_TRADING_DAYS = 60  # trading days each side — see KI-043
SLEEP_DEFAULT = 0.3


def _fetch_ohlcv_pykrx(ticker: str, start_dt: str, end_dt: str) -> pd.DataFrame:
    """Fetch one ticker window via pykrx. start/end are YYYYMMDD strings."""
    from pykrx import stock as krx_stock
    df = krx_stock.get_market_ohlcv_by_date(start_dt, end_dt, ticker, adjusted=True)
    if df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"날짜": "date", "시가": "open", "고가": "high",
                              "저가": "low", "종가": "close", "거래량": "volume"})
    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].copy()


def _fetch_ohlcv_fdr(ticker: str, start_dt: str, end_dt: str) -> pd.DataFrame:
    """Fetch one ticker window via FinanceDataReader (KRX feed, no geo-block)."""
    try:
        import FinanceDataReader as fdr
    except ImportError:
        raise ImportError("FinanceDataReader not installed. Run: uv add FinanceDataReader")
    # FDR accepts YYYYMMDD or YYYY-MM-DD; returns DatetimeIndex
    df = fdr.DataReader(ticker, "KRX", start=start_dt, end=end_dt)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    # FDR columns: Date, Open, High, Low, Close, Volume (sometimes Change)
    df.columns = [c.lower() for c in df.columns]
    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].copy()


def _fetch_ohlcv_yfinance(ticker: str, start_dt: str, end_dt: str) -> pd.DataFrame:
    """Fetch one ticker window via yfinance (Yahoo global CDN, no geo-block)."""
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed. Run: uv add yfinance")
    # Korean tickers: try .KS (KOSPI/KOSDAQ both supported by Yahoo) then .KQ
    start_iso = f"{start_dt[:4]}-{start_dt[4:6]}-{start_dt[6:]}"
    end_iso   = f"{end_dt[:4]}-{end_dt[4:6]}-{end_dt[6:]}"
    for suffix in (".KS", ".KQ"):
        raw = yf.download(f"{ticker}{suffix}", start=start_iso, end=end_iso,
                          progress=False, auto_adjust=True)
        if raw is not None and not raw.empty:
            df = raw.reset_index()
            # yfinance columns may be MultiIndex after auto_adjust — flatten
            if hasattr(df.columns, "get_level_values"):
                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                               for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={"date": "date"})
            keep = [c for c in ["date", "open", "high", "low", "close", "volume"]
                    if c in df.columns]
            return df[keep].copy()
    return pd.DataFrame()


_BACKENDS = {
    "pykrx": _fetch_ohlcv_pykrx,
    "fdr": _fetch_ohlcv_fdr,
    "yfinance": _fetch_ohlcv_yfinance,
}


def fetch_price_volume(
    force: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    max_minutes: float | None = None,
    backend: str = "pykrx",
) -> pd.DataFrame:
    """
    Fetch OHLCV windows around each CB/BW event.
    Writes 01_Data/processed/price_volume.parquet.

    Requires cb_bw_events.parquet and company_list.parquet to exist.

    Args:
        backend: Data source. "pykrx" (default, laptop-only), "fdr"
                 (FinanceDataReader), or "yfinance". See module docstring.
    """
    import time

    if backend not in _BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}. Choose from: {list(_BACKENDS)}")
    fetch_fn = _BACKENDS[backend]
    log.info("Using backend: %s", backend)

    out = PROCESSED / "price_volume.parquet"
    if out.exists() and not force:
        log.info("price_volume.parquet exists, loading cached (use --force to refresh)")
        return pd.read_parquet(out)

    cb_path = PROCESSED / "cb_bw_events.parquet"
    if not cb_path.exists():
        raise FileNotFoundError(
            "cb_bw_events.parquet not found. Run extract_cb_bw.py first."
        )
    company_list_path = RAW / "company_list.parquet"
    if not company_list_path.exists():
        raise FileNotFoundError(
            "company_list.parquet not found. Run extract_dart.py --stage company-list."
        )

    events = pd.read_parquet(cb_path)
    companies = pd.read_parquet(company_list_path)

    # Build corp_code → ticker mapping
    ticker_map: dict[str, str] = {}
    for row in companies.itertuples():
        cc = str(row.corp_code).zfill(8)
        ticker = str(getattr(row, "stock_code", "")).zfill(6)
        if ticker and ticker != "000000":
            ticker_map[cc] = ticker

    # Get unique (corp_code, issue_date) pairs
    pairs = events[["corp_code", "issue_date"]].drop_duplicates()
    if sample is not None:
        pairs = pairs.head(sample)

    deadline = (
        datetime.datetime.now() + datetime.timedelta(minutes=max_minutes)
        if max_minutes else None
    )

    frames: list[pd.DataFrame] = []
    total = len(pairs)

    for i, ev in enumerate(pairs.itertuples(), 1):
        if deadline and datetime.datetime.now() >= deadline:
            log.info("--max-minutes reached at event %d/%d", i, total)
            break

        corp_code = str(ev.corp_code).zfill(8)

        try:
            issue_dt = pd.to_datetime(ev.issue_date)
        except Exception:
            log.warning("Unparseable issue_date=%r for corp_code=%s", ev.issue_date, corp_code)
            continue

        if pd.isna(issue_dt):
            log.warning("Null issue_date for corp_code=%s, skipping", corp_code)
            continue

        ticker = ticker_map.get(corp_code)
        if not ticker:
            log.debug("No ticker for corp_code=%s, skipping", corp_code)
            continue

        from krff.trading_calendar import trading_day_offset
        start_dt = trading_day_offset(issue_dt, -WINDOW_TRADING_DAYS).strftime("%Y%m%d")
        end_dt   = trading_day_offset(issue_dt, +WINDOW_TRADING_DAYS).strftime("%Y%m%d")

        try:
            df_ohlcv = fetch_fn(ticker, start_dt, end_dt)
            if df_ohlcv.empty:
                continue
            df_ohlcv["ticker"] = ticker
            frames.append(df_ohlcv)
            log.debug("Fetched %d rows for ticker=%s (backend=%s)", len(df_ohlcv), ticker, backend)
        except Exception as exc:
            log.warning("%s error for ticker=%s: %s", backend, ticker, exc)

        time.sleep(sleep)

    if not frames:
        df_out = pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume"])
    else:
        df_out = pd.concat(frames, ignore_index=True)
        # Deduplicate
        if "ticker" in df_out.columns and "date" in df_out.columns:
            df_out = df_out.drop_duplicates(subset=["ticker", "date"])

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(out, index=False)
    log.info("Written %d price/volume rows to %s", len(df_out), out)
    return df_out


def main():
    parser = argparse.ArgumentParser(description="Fetch price/volume windows for CB/BW events")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=SLEEP_DEFAULT)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument(
        "--backend",
        choices=list(_BACKENDS),
        default="pykrx",
        help="OHLCV data source. 'pykrx' (default, laptop only), 'fdr', or 'yfinance'.",
    )
    args = parser.parse_args()

    fetch_price_volume(
        force=args.force,
        sample=args.sample,
        sleep=args.sleep,
        max_minutes=args.max_minutes,
        backend=args.backend,
    )


def _configure_stdout() -> None:
    """Windows UTF-8 fix — call only when running as main script, not when imported.

    Avoids breaking pytest's capsys capture when this module is imported in tests.
    """
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


if __name__ == "__main__":
    _configure_stdout()
    main()
