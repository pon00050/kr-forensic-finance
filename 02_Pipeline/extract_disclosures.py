"""
extract_disclosures.py — Phase 2: DART disclosure listing extraction.

Fetches the filing index for each company via the DART list.json endpoint.
Returns all disclosure filings (주요사항보고서, 정기보고서, etc.) with their
filing dates, titles, and receipt numbers.

The DART listing API returns YYYYMMDD dates only (no hour-minute precision).
For timing anomaly analysis, 03_Analysis/timing_anomalies.py applies a
conservative 18:00 KST assumption for after-hours filing detection.

Consumed by: 03_Analysis/timing_anomalies.py (Milestone 3)

Output:
  01_Data/processed/disclosures.parquet
  Columns: corp_code, rcept_no, filed_at, title, type, dart_link

Usage:
  python 02_Pipeline/extract_disclosures.py
  python 02_Pipeline/extract_disclosures.py --sample 10 --sleep 0.5
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from _pipeline_helpers import (
    _dart_api_key,
    fetch_with_backoff as _fetch_with_backoff,
    DART_STATUS_OK, DART_STATUS_NOT_FOUND,
)

load_dotenv()

sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(stream=sys.stdout)],
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
RAW = ROOT / "01_Data" / "raw"
PROCESSED = ROOT / "01_Data" / "processed"

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
SLEEP_DEFAULT = 0.5
PAGE_COUNT = 100
DISCLOSURE_COLS = ["corp_code", "rcept_no", "filed_at", "title", "type", "dart_link"]


def _fetch_disclosures_for_company(
    corp_code: str, api_key: str, bgn_de: str, end_de: str, sleep: float,
) -> list[dict]:
    """Fetch all disclosure filings for one company, handling pagination."""
    rows: list[dict] = []
    page_no = 1

    while True:
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": str(page_no),
            "page_count": str(PAGE_COUNT),
        }

        try:
            data = _fetch_with_backoff(DART_LIST_URL, params)
        except Exception as exc:
            log.warning(
                "list.json failed for corp_code=%s page=%d: %s",
                corp_code, page_no, exc,
            )
            break

        status = str(data.get("status", ""))
        if status == DART_STATUS_NOT_FOUND:
            break  # no filings — normal
        if status != DART_STATUS_OK:
            log.debug("list.json status=%s for corp_code=%s — skipping", status, corp_code)
            break

        items = data.get("list", [])
        if not items:
            break

        for item in items:
            rcept_no = item.get("rcept_no", "")
            rcept_dt = item.get("rcept_dt", "")
            report_nm = item.get("report_nm", "")
            pblntf_ty = item.get("pblntf_ty", "")

            filed_at = rcept_dt.replace(".", "").replace("-", "")[:8]

            dart_link = (
                f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                if rcept_no
                else ""
            )

            rows.append({
                "corp_code": corp_code,
                "rcept_no": rcept_no,
                "filed_at": filed_at,
                "title": report_nm,
                "type": pblntf_ty,
                "dart_link": dart_link,
            })

        total_page = int(data.get("total_page", 1))
        if page_no >= total_page:
            break
        page_no += 1
        time.sleep(sleep)

    return rows


def _fetch_priority_disclosures(
    priority_corp_codes: list[str],
    out: Path,
    bgn_de: str,
    end_de: str | None,
    sleep: float,
) -> pd.DataFrame:
    """
    Re-fetch disclosures for a specific list of corp_codes and merge into the
    existing parquet, overwriting any existing rows for those corp_codes.

    Used when force=False but we need fresh data for specific companies (e.g.
    Tier 1 companies that were absent from the original extraction run).
    """
    api_key = _dart_api_key()
    _end_de = end_de or datetime.date.today().strftime("%Y%m%d")

    existing = pd.read_parquet(out)
    # Remove existing rows for priority corps so we can replace them
    existing = existing[~existing["corp_code"].isin(priority_corp_codes)].copy()

    new_rows: list[dict] = []
    for i, corp_code in enumerate(priority_corp_codes, 1):
        corp_code = str(corp_code).zfill(8)
        log.info(
            "Priority disclosure fetch %d/%d (corp_code=%s)",
            i, len(priority_corp_codes), corp_code,
        )
        rows = _fetch_disclosures_for_company(corp_code, api_key, bgn_de, _end_de, sleep)
        new_rows.extend(rows)
        time.sleep(sleep)

    if new_rows:
        df_new = pd.DataFrame(new_rows, columns=DISCLOSURE_COLS)
        df_merged = pd.concat([existing, df_new], ignore_index=True)
        df_merged = df_merged.drop_duplicates(subset=["corp_code", "rcept_no"])
        df_merged.to_parquet(out, index=False)
        log.info(
            "Priority re-fetch: added %d disclosure rows for %d priority corp_codes",
            len(df_new), len(priority_corp_codes),
        )
        return df_merged

    return existing


def fetch_disclosures(
    force: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    max_minutes: float | None = None,
    bgn_de: str = "20190101",
    end_de: str | None = None,
    priority_corp_codes: list[str] | None = None,
) -> pd.DataFrame:
    """
    Fetch disclosure listings for all companies in company_list.parquet.
    Writes 01_Data/processed/disclosures.parquet.

    priority_corp_codes: if provided, always re-fetch these corp_codes even when
    force=False and the parquet already exists (useful for Tier 1 companies that
    were absent from the original extraction run).
    """
    out = PROCESSED / "disclosures.parquet"

    if out.exists() and not force and not priority_corp_codes:
        log.info("disclosures.parquet exists, loading cached (use --force to refresh)")
        return pd.read_parquet(out)

    # When priority_corp_codes are given and force=False, do a targeted re-fetch
    # for those specific corp_codes, then merge into the existing parquet.
    if out.exists() and not force and priority_corp_codes:
        return _fetch_priority_disclosures(
            priority_corp_codes=priority_corp_codes,
            out=out,
            bgn_de=bgn_de,
            end_de=end_de,
            sleep=sleep,
        )

    company_list_path = RAW / "company_list.parquet"
    if not company_list_path.exists():
        raise FileNotFoundError(
            "01_Data/raw/company_list.parquet not found. "
            "Run: python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023"
        )
    companies = pd.read_parquet(company_list_path)
    if sample is not None:
        companies = companies.head(sample)
        log.info("--sample %d applied", sample)

    api_key = _dart_api_key()
    _end_de = end_de or datetime.date.today().strftime("%Y%m%d")
    deadline = (
        datetime.datetime.now() + datetime.timedelta(minutes=max_minutes)
        if max_minutes else None
    )

    all_rows: list[dict] = []
    total = len(companies)

    for i, row in enumerate(companies.itertuples(), 1):
        if deadline and datetime.datetime.now() >= deadline:
            log.info("--max-minutes reached; stopping early at company %d/%d", i, total)
            break

        corp_code = str(row.corp_code).zfill(8)
        if i % 100 == 0 or i == 1:
            log.info("Disclosures %d/%d (corp_code=%s)", i, total, corp_code)

        rows = _fetch_disclosures_for_company(
            corp_code, api_key, bgn_de, _end_de, sleep,
        )
        all_rows.extend(rows)
        time.sleep(sleep)

    df = (
        pd.DataFrame(all_rows, columns=DISCLOSURE_COLS)
        if all_rows
        else pd.DataFrame(columns=DISCLOSURE_COLS)
    )

    before = len(df)
    df = df.drop_duplicates(subset=["corp_code", "rcept_no"])
    if len(df) < before:
        log.info("Dropped %d duplicate disclosure rows", before - len(df))

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    log.info("Written %d disclosure rows to %s", len(df), out)
    return df


def main():
    parser = argparse.ArgumentParser(
        description="Fetch disclosure listings from DART list.json"
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=SLEEP_DEFAULT)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument(
        "--bgn-de", type=str, default="20190101",
        help="Start date YYYYMMDD (default: 20190101)",
    )
    parser.add_argument(
        "--end-de", type=str, default=None,
        help="End date YYYYMMDD (default: today)",
    )
    parser.add_argument(
        "--priority-corp-codes", type=str, default=None,
        help="Comma-separated corp_codes to force-fetch even when parquet exists "
             "(e.g. 01051092,01207761 for Tier 1 companies)",
    )
    args = parser.parse_args()

    priority = (
        [c.strip().zfill(8) for c in args.priority_corp_codes.split(",")]
        if args.priority_corp_codes
        else None
    )

    fetch_disclosures(
        force=args.force,
        sample=args.sample,
        sleep=args.sleep,
        max_minutes=args.max_minutes,
        bgn_de=args.bgn_de,
        end_de=args.end_de,
        priority_corp_codes=priority,
    )


if __name__ == "__main__":
    main()
