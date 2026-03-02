"""
extract_cb_bw.py — Phase 2: CB/BW issuance event extraction from DART DS005.

Endpoints (not in OpenDartReader — called directly via requests):
  CB: https://opendart.fss.or.kr/api/cvbdIsDecsn.json
  BW: https://opendart.fss.or.kr/api/bdwtIsDecsn.json

status "013" means no history for that company — skip, not an error.

Output:
  01_Data/processed/cb_bw_events.parquet
  Columns: corp_code, issue_date, bond_type, exercise_price,
           repricing_history (JSON str), exercise_events (JSON str)

Usage:
  python 02_Pipeline/extract_cb_bw.py
  python 02_Pipeline/extract_cb_bw.py --sample 10 --sleep 0.5
"""

from __future__ import annotations

import argparse
import datetime
import json
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
RAW = ROOT / "01_Data" / "raw"
PROCESSED = ROOT / "01_Data" / "processed"

DART_CB_URL = "https://opendart.fss.or.kr/api/cvbdIsDecsn.json"
DART_BW_URL = "https://opendart.fss.or.kr/api/bdwtIsDecsn.json"

SLEEP_DEFAULT = 0.5


def _norm_corp_code(code) -> str:
    return str(code).zfill(8)


def _dart_api_key() -> str:
    key = os.getenv("DART_API_KEY", "")
    if not key or key == "your_opendart_api_key_here":
        raise EnvironmentError("DART_API_KEY not set.")
    return key


def _parse_dart_response(
    data: dict, corp_code: str, bond_type: str
) -> list[dict]:
    """
    Parse a DART DS005 response dict into a list of event rows.

    Returns empty list for status '013' (no data) or empty list field.
    bond_type must be 'CB' or 'BW'.
    """
    status = str(data.get("status", ""))
    if status == "013":
        return []
    if status not in ("000", ""):
        log.warning("Unexpected DART status %s for corp_code=%s bond_type=%s", status, corp_code, bond_type)
        return []

    items = data.get("list", [])
    if not items:
        return []

    rows = []
    for item in items:
        # Resolve issue date — field names differ between CB and BW
        issue_date = (
            item.get("rcept_dt")
            or item.get("bdwt_issu_dt")
            or item.get("cvbd_issu_dt")
            or ""
        )
        # Normalize YYYYMMDD → YYYY-MM-DD
        if len(issue_date) == 8 and issue_date.isdigit():
            issue_date = f"{issue_date[:4]}-{issue_date[4:6]}-{issue_date[6:]}"

        raw_price = (
            item.get("cvExrPrc")
            or item.get("exrPrc")
            or item.get("bdwt_exr_prc")
            or None
        )
        try:
            exercise_price = float(str(raw_price).replace(",", "")) if raw_price else None
        except (ValueError, TypeError):
            exercise_price = None

        rows.append({
            "corp_code": corp_code,
            "issue_date": issue_date,
            "bond_type": bond_type,
            "exercise_price": exercise_price,
            "repricing_history": json.dumps([]),
            "exercise_events": json.dumps([]),
        })

    return rows


def _fetch_with_backoff(
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
            if str(data.get("status", "")) == "020":
                raise Exception("Error 020 rate limit")
            return data
        except Exception as exc:
            last_exc = exc
            if "020" not in str(exc):
                raise
    raise last_exc  # type: ignore[misc]


def build_scoped_universe(
    scores_path: Path,
    cb_bw_corp_codes: set[str],
    top_n: int = 100,
) -> set[str]:
    """
    Return the Phase 2 scoped universe: union of
      (a) top_n companies by M-Score (highest = most suspicious) from beneish_scores, and
      (b) all companies in cb_bw_corp_codes (have ≥1 CB/BW event on DART).

    Parameters
    ----------
    scores_path      : path to beneish_scores.parquet
    cb_bw_corp_codes : corp_codes that returned ≥1 CB/BW row from DART (may be empty)
    top_n            : number of highest M-Score companies to include (default 100)

    Returns
    -------
    Set of corp_code strings.
    """
    if not scores_path.exists():
        log.warning("beneish_scores.parquet not found at %s — scoping filter disabled", scores_path)
        return set()

    scores = pd.read_parquet(scores_path)
    if "m_score" not in scores.columns or "corp_code" not in scores.columns:
        log.warning("beneish_scores.parquet missing required columns — scoping filter disabled")
        return set()

    top_codes = (
        scores.dropna(subset=["m_score"])
        .sort_values("m_score", ascending=False)
        .head(top_n)["corp_code"]
        .astype(str)
        .str.zfill(8)
        .tolist()
    )

    universe = set(top_codes) | {_norm_corp_code(c) for c in cb_bw_corp_codes}
    log.info(
        "Scoped universe: %d top-M-Score + %d CB/BW companies = %d unique",
        len(top_codes), len(cb_bw_corp_codes), len(universe),
    )
    return universe


def fetch_cb_bw_events(
    force: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    max_minutes: float | None = None,
    scoped: bool = False,
    top_n: int = 100,
) -> pd.DataFrame:
    """
    Fetch CB/BW issuance events for companies in company_list.parquet.

    When scoped=True, applies the Phase 2 universe filter:
      - Top top_n companies by M-Score (from beneish_scores.parquet), UNION
      - All companies with ≥1 CB/BW event on DART (first-pass discovery).
    This reduces ~1,700 companies to ~200–400 and saves ~2,000 DART API calls.

    Writes 01_Data/processed/cb_bw_events.parquet.
    """
    out = PROCESSED / "cb_bw_events.parquet"
    if out.exists() and not force:
        log.info("cb_bw_events.parquet exists, loading cached (use --force to refresh)")
        return pd.read_parquet(out)

    company_list_path = RAW / "company_list.parquet"
    if not company_list_path.exists():
        raise FileNotFoundError(
            "01_Data/raw/company_list.parquet not found. "
            "Run: python 02_Pipeline/extract_dart.py --stage company-list"
        )
    companies = pd.read_parquet(company_list_path)
    if sample is not None:
        companies = companies.head(sample)
        log.info("--sample %d applied", sample)

    api_key = _dart_api_key()
    deadline = (
        datetime.datetime.now() + datetime.timedelta(minutes=max_minutes)
        if max_minutes else None
    )

    # --- Phase 2 scoping filter (Gap 3) -----------------------------------
    # When --scoped is active, first do a CB/BW discovery pass over ALL companies.
    # Responses are cached so the main loop can reuse them — no company is called twice.
    # discovery_cache: (corp_code, bond_type) → parsed rows list
    discovery_cache: dict[tuple[str, str], list[dict]] = {}

    if scoped:
        log.info("--scoped: running CB/BW discovery pass to identify active issuers...")
        discovery_codes: set[str] = set()
        for row in companies.itertuples():
            corp_code = _norm_corp_code(row.corp_code)
            for bond_type, url in [("CB", DART_CB_URL), ("BW", DART_BW_URL)]:
                try:
                    data = _fetch_with_backoff(
                        url, params={"crtfc_key": api_key, "corp_code": corp_code}
                    )
                    rows_found = _parse_dart_response(data, corp_code=corp_code, bond_type=bond_type)
                    discovery_cache[(corp_code, bond_type)] = rows_found
                    if rows_found:
                        discovery_codes.add(corp_code)
                except Exception as exc:
                    log.warning("Discovery error %s for %s: %s", bond_type, corp_code, exc)
                time.sleep(sleep)

        scoped_universe = build_scoped_universe(
            scores_path=PROCESSED / "beneish_scores.parquet",
            cb_bw_corp_codes=discovery_codes,
            top_n=top_n,
        )
        if scoped_universe:
            original_count = len(companies)
            companies = companies[
                companies["corp_code"].astype(str).str.zfill(8).isin(scoped_universe)
            ].copy()
            log.info("--scoped: filtered to %d companies (was %d)", len(companies), original_count)
        else:
            log.warning("--scoped: scoped universe empty — proceeding with full list")
    # ----------------------------------------------------------------------

    all_rows: list[dict] = []
    total = len(companies)

    for i, row in enumerate(companies.itertuples(), 1):
        if deadline and datetime.datetime.now() >= deadline:
            log.info("--max-minutes reached; stopping early at company %d/%d", i, total)
            break

        corp_code = _norm_corp_code(row.corp_code)
        if i % 100 == 0 or i == 1:
            log.info("CB/BW fetch %d/%d (corp_code=%s)", i, total, corp_code)

        for bond_type, url in [("CB", DART_CB_URL), ("BW", DART_BW_URL)]:
            # Reuse cached response from discovery pass if available.
            if (corp_code, bond_type) in discovery_cache:
                all_rows.extend(discovery_cache[(corp_code, bond_type)])
                continue
            try:
                data = _fetch_with_backoff(
                    url, params={"crtfc_key": api_key, "corp_code": corp_code}
                )
                rows = _parse_dart_response(data, corp_code=corp_code, bond_type=bond_type)
                all_rows.extend(rows)
            except Exception as exc:
                log.warning("Error fetching %s for %s: %s", bond_type, corp_code, exc)

            time.sleep(sleep)

    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(columns=[
        "corp_code", "issue_date", "bond_type", "exercise_price",
        "repricing_history", "exercise_events",
    ])

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    log.info("Written %d CB/BW events to %s", len(df), out)
    return df


def main():
    parser = argparse.ArgumentParser(description="Fetch CB/BW events from DART DS005")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=SLEEP_DEFAULT)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument(
        "--scoped", action="store_true",
        help="Apply Phase 2 scoping filter: top-N M-Score union CB/BW issuers (~200-400 companies)"
    )
    parser.add_argument(
        "--top-n", type=int, default=100,
        help="Number of top M-Score companies to include in scoped universe (default: 100)"
    )
    args = parser.parse_args()

    fetch_cb_bw_events(
        force=args.force,
        sample=args.sample,
        sleep=args.sleep,
        max_minutes=args.max_minutes,
        scoped=args.scoped,
        top_n=args.top_n,
    )


if __name__ == "__main__":
    main()
