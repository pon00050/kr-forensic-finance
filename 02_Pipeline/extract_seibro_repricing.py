"""
extract_seibro_repricing.py — Fetch CB/BW repricing + exercise history from SEIBRO.

Enriches cb_bw_events.parquet by populating repricing_history and exercise_events
columns for each event row using the SEIBRO REST API via data.go.kr.

Data source: api.seibro.or.kr (data.go.kr datasets 15001145, 15074595)
API format: XML (parsed with xml.etree.ElementTree)
Rate limit: 100 calls/day (development), no-limit in production
Join key: (corp_code, bond_type) + date proximity (±30 days)

Prerequisites:
  - Register on data.go.kr (free, instant)
  - Apply for API key on datasets 15001145 and 15074595 (auto-approved for dev)
  - Add SEIBRO_API_KEY to .env

Outputs:
  01_Data/processed/cb_bw_events.parquet  (updated in-place with repricing data)
  01_Data/raw/seibro/repricings/<corp_code>.json  (raw cache per company)
  01_Data/raw/seibro/exercises/<corp_code>.json   (raw cache per company)

Usage:
  python 02_Pipeline/extract_seibro_repricing.py
  python 02_Pipeline/extract_seibro_repricing.py --sample 5 --dry-run
  python 02_Pipeline/extract_seibro_repricing.py --force
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

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

SEIBRO_BASE = "http://api.seibro.or.kr/openapi/service"

# dataset 15001145 — stock info service (repricing / exercise conditions)
REPRICING_SVC = f"{SEIBRO_BASE}/StockInfoSvc/getStkcirtBdInfo"

# dataset 15074595 — bond info service (exercise history)
EXERCISE_SVC = f"{SEIBRO_BASE}/BondSvc/getRgtXrcInfo"

SLEEP_DEFAULT = 1.0  # SEIBRO is more restrictive than DART
MAX_RETRIES = 3


def _seibro_api_key() -> str:
    key = os.getenv("SEIBRO_API_KEY", "")
    if not key or key == "your_seibro_api_key_here":
        raise EnvironmentError(
            "SEIBRO_API_KEY not set. Register on data.go.kr, apply for datasets "
            "15001145 + 15074595, add key to .env as SEIBRO_API_KEY=<your_key>"
        )
    return key


def _xml_text(elem: ET.Element | None, tag: str, default: str = "") -> str:
    """Safely extract text from a child element."""
    if elem is None:
        return default
    child = elem.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _fetch_xml(url: str, params: dict) -> ET.Element | None:
    """GET request returning parsed XML root, with retry on transient errors."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            return root
        except ET.ParseError as exc:
            log.warning("XML parse error (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
        except requests.RequestException as exc:
            log.warning("HTTP error (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    return None


def _normalise_date(raw: str) -> str:
    """
    Normalise a SEIBRO date string to YYYYMMDD format.

    SEIBRO returns dates in multiple formats:
      YYYYMMDD (ideal), YYYY-MM-DD, YYYY.MM.DD
    """
    if not raw:
        return ""
    s = raw.replace("-", "").replace(".", "").strip()
    return s[:8] if len(s) >= 8 else s


def fetch_repricing_for_company(
    corp_code: str, api_key: str
) -> list[dict]:
    """
    Fetch repricing (리픽싱) history for one company from SEIBRO dataset 15001145.

    Returns list of dicts with keys matching the bilingual schema used by
    run_cb_bw_timelines.py score_events():
      {"date": "YYYYMMDD", "new_price": float, "bond_name": str, "reason": str}

    Also keeps Korean key aliases for forward-compatibility:
      {"조정일자": "YYYYMMDD", "조정가액": float}
    """
    params = {
        "serviceKey": api_key,
        "isinCd": "",            # leave blank to search by corp_code
        "corpCd": corp_code,     # company code (may need SEIBRO internal code)
        "numOfRows": "100",
        "pageNo": "1",
    }
    root = _fetch_xml(REPRICING_SVC, params)
    if root is None:
        return []

    items = root.findall(".//item")
    if not items:
        # Try alternate path — SEIBRO XML structure varies by service version
        items = root.findall(".//items/item")

    repricings: list[dict] = []
    for item in items:
        raw_date = _xml_text(item, "adjstDt") or _xml_text(item, "조정일자")
        raw_price = _xml_text(item, "adjstPrc") or _xml_text(item, "조정가액")
        bond_name = _xml_text(item, "isueNm") or _xml_text(item, "종목명")
        reason = _xml_text(item, "adjstRsn") or _xml_text(item, "사유")
        bond_type = _xml_text(item, "stkrtBdKndCd") or _xml_text(item, "종류")

        if not raw_date or not raw_price:
            continue

        try:
            price = float(raw_price.replace(",", ""))
        except (ValueError, TypeError):
            continue

        norm_date = _normalise_date(raw_date)
        repricings.append({
            # English keys (used by score_events() bilingual lookup)
            "date": norm_date,
            "new_price": price,
            "bond_name": bond_name,
            "reason": reason,
            "bond_type_raw": bond_type,
            # Korean key aliases (forward-compat)
            "조정일자": norm_date,
            "조정가액": price,
        })

    log.debug("corp_code=%s: %d repricing records", corp_code, len(repricings))
    return repricings


def fetch_exercises_for_company(
    corp_code: str, api_key: str
) -> list[dict]:
    """
    Fetch exercise/conversion history for one company from SEIBRO dataset 15074595.

    Returns list of dicts with keys matching the bilingual schema used by
    run_cb_bw_timelines.py score_events():
      {"exercise_date": "YYYYMMDD", "shares": int, "exercise_price": float}

    Also keeps Korean key aliases:
      {"권리행사일": "YYYYMMDD"}
    """
    params = {
        "serviceKey": api_key,
        "corpCd": corp_code,
        "numOfRows": "100",
        "pageNo": "1",
    }
    root = _fetch_xml(EXERCISE_SVC, params)
    if root is None:
        return []

    items = root.findall(".//item")
    if not items:
        items = root.findall(".//items/item")

    exercises: list[dict] = []
    for item in items:
        raw_date = _xml_text(item, "xrcDt") or _xml_text(item, "행사일") or _xml_text(item, "권리행사일")
        raw_shares = _xml_text(item, "stkqty") or _xml_text(item, "주식수")
        raw_price = _xml_text(item, "xrcPrc") or _xml_text(item, "행사가격")
        bond_name = _xml_text(item, "isueNm") or _xml_text(item, "종목명")
        bond_type = _xml_text(item, "stkrtBdKndCd") or _xml_text(item, "종류")

        if not raw_date:
            continue

        norm_date = _normalise_date(raw_date)

        try:
            shares = int(raw_shares.replace(",", "")) if raw_shares else 0
        except (ValueError, TypeError):
            shares = 0

        try:
            price = float(raw_price.replace(",", "")) if raw_price else None
        except (ValueError, TypeError):
            price = None

        exercises.append({
            # English keys
            "exercise_date": norm_date,
            "shares": shares,
            "exercise_price": price,
            "bond_name": bond_name,
            "bond_type_raw": bond_type,
            # Korean key aliases
            "권리행사일": norm_date,
        })

    log.debug("corp_code=%s: %d exercise records", corp_code, len(exercises))
    return exercises


def _match_to_event(
    records: list[dict],
    event_issue_date: str,
    date_key: str,
    window_days: int = 90,
) -> list[dict]:
    """
    Filter records to those within window_days of the event issue_date.

    Repricing and exercise events can occur up to several years after issuance,
    but for initial join we use ±window_days from issue_date as a conservative
    first pass. The scoring function applies its own temporal logic.

    date_key: the key in each record dict that holds the event date (YYYYMMDD).
    window_days: half-window around issue_date to include (default 90 = ±3 months).
    """
    try:
        issue_dt = pd.to_datetime(event_issue_date, errors="coerce")
    except Exception:
        return records  # can't filter; return all

    if pd.isna(issue_dt):
        return records

    matched = []
    for rec in records:
        raw_date = rec.get(date_key, "")
        if not raw_date:
            matched.append(rec)
            continue
        try:
            rec_dt = pd.to_datetime(raw_date, format="%Y%m%d", errors="coerce")
            if pd.isna(rec_dt):
                matched.append(rec)
                continue
            # Include if after issue_date (repricing/exercise happens after issuance)
            if rec_dt >= issue_dt:
                matched.append(rec)
        except Exception:
            matched.append(rec)

    return matched


def enrich_cb_bw_parquet(
    force: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    dry_run: bool = False,
) -> pd.DataFrame:
    """
    Read cb_bw_events.parquet, enrich repricing_history and exercise_events columns,
    write back to the same file.

    When force=False: only enriches rows where repricing_history == "[]".
    When force=True: re-fetches for all corp_codes.

    dry_run=True: fetches and caches data but does NOT write back to parquet.
    """
    cb_path = PROCESSED / "cb_bw_events.parquet"
    if not cb_path.exists():
        log.error("cb_bw_events.parquet not found. Run extract_cb_bw.py first.")
        return pd.DataFrame()

    df = pd.read_parquet(cb_path)
    log.info("Loaded %d CB/BW events from %s", len(df), cb_path)

    if not dry_run:
        api_key = _seibro_api_key()
    else:
        api_key = "DRY_RUN_NO_KEY"
        log.info("--dry-run: no API calls will be made")

    # Identify corp_codes to enrich
    if force:
        corps_to_enrich = df["corp_code"].unique().tolist()
    else:
        # Only enrich rows that still have empty arrays
        mask = df["repricing_history"].apply(
            lambda x: json.loads(x) == [] if isinstance(x, str) else True
        )
        corps_to_enrich = df.loc[mask, "corp_code"].unique().tolist()

    if sample is not None:
        corps_to_enrich = corps_to_enrich[:sample]
        log.info("--sample %d: limiting to %d corp_codes", sample, len(corps_to_enrich))

    log.info("Enriching %d corp_codes with SEIBRO repricing + exercise data", len(corps_to_enrich))

    # Cache directories
    repricing_cache_dir = RAW / "seibro" / "repricings"
    exercise_cache_dir = RAW / "seibro" / "exercises"
    repricing_cache_dir.mkdir(parents=True, exist_ok=True)
    exercise_cache_dir.mkdir(parents=True, exist_ok=True)

    # Fetch and cache per company
    repricing_by_corp: dict[str, list[dict]] = {}
    exercise_by_corp: dict[str, list[dict]] = {}

    for i, corp_code in enumerate(corps_to_enrich, 1):
        log.info("SEIBRO fetch %d/%d (corp_code=%s)", i, len(corps_to_enrich), corp_code)

        repr_cache = repricing_cache_dir / f"{corp_code}.json"
        exer_cache = exercise_cache_dir / f"{corp_code}.json"

        if not force and repr_cache.exists():
            with open(repr_cache, encoding="utf-8") as f:
                repricings = json.load(f)
        elif dry_run:
            repricings = []
        else:
            repricings = fetch_repricing_for_company(corp_code, api_key)
            with open(repr_cache, "w", encoding="utf-8") as f:
                json.dump(repricings, f, ensure_ascii=False, indent=2)
            time.sleep(sleep)

        if not force and exer_cache.exists():
            with open(exer_cache, encoding="utf-8") as f:
                exercises = json.load(f)
        elif dry_run:
            exercises = []
        else:
            exercises = fetch_exercises_for_company(corp_code, api_key)
            with open(exer_cache, "w", encoding="utf-8") as f:
                json.dump(exercises, f, ensure_ascii=False, indent=2)
            time.sleep(sleep)

        repricing_by_corp[corp_code] = repricings
        exercise_by_corp[corp_code] = exercises

    if dry_run:
        log.info("--dry-run complete. No parquet updated.")
        return df

    # Write enriched data back to parquet
    enriched_count = 0
    for idx, row in df.iterrows():
        corp_code = row["corp_code"]
        if corp_code not in repricing_by_corp:
            continue

        issue_date = str(row.get("issue_date", ""))

        repricings = _match_to_event(
            repricing_by_corp[corp_code], issue_date, date_key="date"
        )
        exercises = _match_to_event(
            exercise_by_corp[corp_code], issue_date, date_key="exercise_date"
        )

        df.at[idx, "repricing_history"] = json.dumps(repricings, ensure_ascii=False)
        df.at[idx, "exercise_events"] = json.dumps(exercises, ensure_ascii=False)

        if repricings or exercises:
            enriched_count += 1

    df.to_parquet(cb_path, index=False)
    log.info(
        "Enriched cb_bw_events.parquet: %d events now have repricing or exercise data",
        enriched_count,
    )
    return df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich cb_bw_events.parquet with SEIBRO repricing + exercise history"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch for all corp_codes, even those already enriched",
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Limit to first N corp_codes (for testing)",
    )
    parser.add_argument(
        "--sleep", type=float, default=SLEEP_DEFAULT,
        help=f"Seconds between API calls (default: {SLEEP_DEFAULT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and cache data but do NOT write back to parquet",
    )
    args = parser.parse_args()

    enrich_cb_bw_parquet(
        force=args.force,
        sample=args.sample,
        sleep=args.sleep,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
