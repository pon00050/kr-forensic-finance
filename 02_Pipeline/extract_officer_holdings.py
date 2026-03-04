"""
extract_officer_holdings.py — Phase 2: officer stock holdings from DART elestock.

For each corp_code in cb_bw_events.parquet, fetches the executive stock change
report (임원ᆞ주요주주특정증권등소유상황보고서) via the DART elestock endpoint.

Output:
  01_Data/processed/officer_holdings.parquet
  Columns: corp_code, date, officer_name, change_shares

Usage:
  python 02_Pipeline/extract_officer_holdings.py
  python 02_Pipeline/extract_officer_holdings.py --sample 10 --sleep 0.5
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(stream=sys.stdout)],
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
PROCESSED = ROOT / "01_Data" / "processed"

DART_ELESTOCK_URL = "https://opendart.fss.or.kr/api/elestock.json"
SLEEP_DEFAULT = 0.5


def _dart_api_key() -> str:
    key = os.getenv("DART_API_KEY", "")
    if not key or key == "your_opendart_api_key_here":
        raise EnvironmentError("DART_API_KEY not set.")
    return key


def _fetch_elestock(corp_code: str, api_key: str) -> list[dict]:
    """Fetch officer holdings for one company. Returns list of row dicts."""
    try:
        resp = requests.get(
            DART_ELESTOCK_URL,
            params={"crtfc_key": api_key, "corp_code": corp_code},
            timeout=30,
        )
        data = resp.json()
    except Exception as exc:
        log.warning("elestock request failed for corp_code=%s: %s", corp_code, exc)
        return []

    status = str(data.get("status", ""))
    if status in ("013", "020"):
        if status == "020":
            log.warning("DART Error 020 (rate limit) for corp_code=%s", corp_code)
        return []
    if status != "000":
        log.debug("elestock status=%s for corp_code=%s — skipping", status, corp_code)
        return []

    items = data.get("list", [])
    rows = []
    for item in items:
        report_date = item.get("rcept_dt", "")
        if len(report_date) == 8 and report_date.isdigit():
            report_date = f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:]}"

        officer_name = (
            item.get("nm")
            or item.get("reprt_nm")
            or ""
        )
        raw_chg = item.get("chng_rm_stck_cnt") or item.get("chg_stk_qnt") or "0"
        try:
            change_shares = float(str(raw_chg).replace(",", ""))
        except (ValueError, TypeError):
            change_shares = None

        rows.append({
            "corp_code": corp_code,
            "date": report_date,
            "officer_name": officer_name,
            "change_shares": change_shares,
        })
    return rows


def fetch_officer_holdings(
    force: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    max_minutes: float | None = None,
) -> pd.DataFrame:
    """
    Fetch officer holdings for all corp_codes in cb_bw_events.parquet.
    Writes 01_Data/processed/officer_holdings.parquet.
    """
    out = PROCESSED / "officer_holdings.parquet"
    if out.exists() and not force:
        log.info("officer_holdings.parquet exists, loading cached (use --force to refresh)")
        return pd.read_parquet(out)

    cb_path = PROCESSED / "cb_bw_events.parquet"
    if not cb_path.exists():
        raise FileNotFoundError(
            "cb_bw_events.parquet not found. Run extract_cb_bw.py first."
        )

    events = pd.read_parquet(cb_path)
    corp_codes = events["corp_code"].dropna().unique().tolist()
    if sample is not None:
        corp_codes = corp_codes[:sample]
        log.info("--sample %d applied", sample)

    api_key = _dart_api_key()
    deadline = (
        datetime.datetime.now() + datetime.timedelta(minutes=max_minutes)
        if max_minutes else None
    )

    all_rows: list[dict] = []
    total = len(corp_codes)

    for i, corp_code in enumerate(corp_codes, 1):
        if deadline and datetime.datetime.now() >= deadline:
            log.info("--max-minutes reached at company %d/%d", i, total)
            break

        if i % 100 == 0 or i == 1:
            log.info("Officer holdings %d/%d (corp_code=%s)", i, total, corp_code)

        rows = _fetch_elestock(str(corp_code).zfill(8), api_key)
        all_rows.extend(rows)
        time.sleep(sleep)

    if not all_rows:
        df_out = pd.DataFrame(columns=["corp_code", "date", "officer_name", "change_shares"])
    else:
        df_out = pd.DataFrame(all_rows)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(out, index=False)
    log.info("Written %d officer holding rows to %s", len(df_out), out)
    return df_out


def main():
    parser = argparse.ArgumentParser(description="Fetch officer holdings from DART elestock")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=SLEEP_DEFAULT)
    parser.add_argument("--max-minutes", type=float, default=None)
    args = parser.parse_args()

    fetch_officer_holdings(
        force=args.force,
        sample=args.sample,
        sleep=args.sleep,
        max_minutes=args.max_minutes,
    )


if __name__ == "__main__":
    main()
