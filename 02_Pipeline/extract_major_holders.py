"""
extract_major_holders.py — Phase 2: 대량보유상황보고서 (5%+ ownership filings) from DART.

For each corp_code in cb_bw_events.parquet, fetches the full history of major
shareholder threshold-crossing filings (대량보유상황보고서) via the DART majorstock.json
endpoint.

Output:
  01_Data/processed/major_holders.parquet
  Columns: corp_code, rcept_no, rcept_dt, corp_name, report_tp, repror,
           stkqy, stkqy_irds, stkrt, stkrt_irds, ctr_stkqy, ctr_stkrt, report_resn

Usage:
  python 02_Pipeline/extract_major_holders.py
  python 02_Pipeline/extract_major_holders.py --sample 10 --sleep 0.5
  python 02_Pipeline/extract_major_holders.py --force
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from _pipeline_helpers import (
    _dart_api_key, _norm_corp_code,
    DART_STATUS_OK, DART_STATUS_NOT_FOUND, DART_STATUS_RATE_LIMIT,
)

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
RAW_DIR = ROOT / "01_Data" / "raw" / "dart" / "major_holders"

DART_MAJORSTOCK_URL = "https://opendart.fss.or.kr/api/majorstock.json"
SLEEP_DEFAULT = 0.5
REQUIRED_COLS = [
    "corp_code", "rcept_no", "rcept_dt", "corp_name", "report_tp", "repror",
    "stkqy", "stkqy_irds", "stkrt", "stkrt_irds", "ctr_stkqy", "ctr_stkrt",
    "report_resn",
]


def _safe_float(val) -> float | None:
    """Parse a float from DART response values; strip commas and %."""
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _fetch_majorstock(
    corp_code: str,
    api_key: str,
    raw_dir: Path,
    force: bool = False,
) -> list[dict]:
    """
    Fetch 대량보유상황보고서 filings for one company.

    Returns list of row dicts. Caches per-company JSON to raw_dir.
    Status 013 = no filings (cache empty list, return []).
    Status 020 = rate limit (log warning, return [], do NOT cache).
    """
    cache_path = raw_dir / f"{corp_code}.json"

    if cache_path.exists() and not force:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    try:
        resp = requests.get(
            DART_MAJORSTOCK_URL,
            params={"crtfc_key": api_key, "corp_code": corp_code},
            timeout=30,
        )
        data = resp.json()
    except Exception as exc:
        log.warning("majorstock request failed for corp_code=%s: %s", corp_code, exc)
        return []

    status = str(data.get("status", ""))

    if status == DART_STATUS_RATE_LIMIT:
        log.warning("DART Error 020 (rate limit) for corp_code=%s — not caching", corp_code)
        return []

    if status == DART_STATUS_NOT_FOUND:
        # No filings — cache empty list
        raw_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []

    if status != DART_STATUS_OK:
        log.debug("majorstock status=%s for corp_code=%s — skipping", status, corp_code)
        return []

    items = data.get("list", [])
    rows = []
    for item in items:
        rcept_dt = item.get("rcept_dt", "")
        if len(rcept_dt) == 8 and rcept_dt.isdigit():
            rcept_dt = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}"

        rows.append({
            "corp_code": corp_code,
            "rcept_no": item.get("rcept_no", ""),
            "rcept_dt": rcept_dt,
            "corp_name": item.get("corp_name", ""),
            "report_tp": item.get("report_tp", ""),
            "repror": item.get("repror", ""),
            "stkqy": _safe_float(item.get("stkqy")),
            "stkqy_irds": _safe_float(item.get("stkqy_irds")),
            "stkrt": _safe_float(item.get("stkrt")),
            "stkrt_irds": _safe_float(item.get("stkrt_irds")),
            "ctr_stkqy": _safe_float(item.get("ctr_stkqy")),
            "ctr_stkrt": _safe_float(item.get("ctr_stkrt")),
            "report_resn": item.get("report_resn", ""),
        })

    raw_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)

    return rows


def fetch_major_holders(
    force: bool = False,
    rebuild: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    max_minutes: float | None = None,
) -> pd.DataFrame:
    """
    Fetch 대량보유상황보고서 for all corp_codes in cb_bw_events.parquet.
    Writes 01_Data/processed/major_holders.parquet.
    """
    out = PROCESSED / "major_holders.parquet"
    if out.exists() and not force and not rebuild:
        log.info("major_holders.parquet exists, loading cached (use --force or --rebuild to refresh)")
        return pd.read_parquet(out)

    cb_path = PROCESSED / "cb_bw_events.parquet"
    if not cb_path.exists():
        raise FileNotFoundError(
            "cb_bw_events.parquet not found. Run extract_cb_bw.py first."
        )

    events = pd.read_parquet(cb_path)
    corp_codes = [_norm_corp_code(c) for c in events["corp_code"].dropna().unique()]
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
            log.info("Major holders %d/%d (corp_code=%s)", i, total, corp_code)

        rows = _fetch_majorstock(corp_code, api_key, RAW_DIR, force=force)
        all_rows.extend(rows)
        time.sleep(sleep)

    if not all_rows:
        df_out = pd.DataFrame(columns=REQUIRED_COLS)
    else:
        df_out = pd.DataFrame(all_rows)
        # Dedup on (corp_code, rcept_no)
        df_out = df_out.drop_duplicates(subset=["corp_code", "rcept_no"])

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(out, index=False)
    log.info("Written %d major holder rows to %s", len(df_out), out)
    return df_out


def main():
    parser = argparse.ArgumentParser(
        description="Fetch 대량보유상황보고서 (5%+ ownership filings) from DART majorstock"
    )
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch all data from API (overwrites cache + parquet)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild parquet from cached raw files without re-fetching from API")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=SLEEP_DEFAULT)
    parser.add_argument("--max-minutes", type=float, default=None)
    args = parser.parse_args()

    fetch_major_holders(
        force=args.force,
        rebuild=args.rebuild,
        sample=args.sample,
        sleep=args.sleep,
        max_minutes=args.max_minutes,
    )


if __name__ == "__main__":
    main()
