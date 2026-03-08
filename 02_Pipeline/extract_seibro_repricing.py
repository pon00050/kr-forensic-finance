"""
extract_seibro_repricing.py — Fetch CB/BW repricing + exercise history from SEIBRO.

Enriches cb_bw_events.parquet by populating repricing_history and exercise_events
columns for each event row using the SEIBRO REST API via data.go.kr.

Data source: api.seibro.or.kr (data.go.kr dataset 15001145)
API format: XML (parsed with xml.etree.ElementTree)
Rate limit: 100 calls/day (development), no-limit in production
Join key: bond ISIN (12-char, e.g. KR62797117B7) — requires bond_isin_map.parquet

Prerequisites:
  - Register on data.go.kr (free, instant)
  - Apply for API key on dataset 15001145 (auto-approved for dev)
  - Add SEIBRO_API_KEY to .env
  - Run build_isin_map.py first to produce 01_Data/processed/bond_isin_map.parquet

Endpoints used (both from dataset 15001145 — StockSvc):
  - getXrcStkOptionXrcInfoN1: exercise conditions + repricing (리픽싱) per bond ISIN
  - getXrcStkStatInfoN1:      CB/BW bond details (exercise price, terms) per bond ISIN

Note: BondSvc/getRgtXrcInfo and SecDepoStat/getCvreqStat are aggregate-only (market-wide)
and cannot be used for per-company analysis. See 00_Reference/2_Data/38_SEIBRO_API_Credentials.md.

Outputs:
  01_Data/processed/cb_bw_events.parquet        (updated in-place with repricing data)
  01_Data/raw/seibro/repricings/<bond_isin>.json (raw cache per bond)
  01_Data/raw/seibro/exercises/<bond_isin>.json  (raw cache per bond)

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
from collections.abc import Callable
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

SEIBRO_BASE = "https://api.seibro.or.kr/openapi/service"

# dataset 15001145 — StockSvc (per-bond; requires bondIsin parameter)
# getXrcStkOptionXrcInfoN1: exercise conditions and repricing (리픽싱)
REPRICING_SVC = f"{SEIBRO_BASE}/StockSvc/getXrcStkOptionXrcInfoN1"
# getXrcStkStatInfoN1: CB/BW bond details (exercise price, exercise ratio, terms)
EXERCISE_SVC = f"{SEIBRO_BASE}/StockSvc/getXrcStkStatInfoN1"

SLEEP_DEFAULT = 1.0  # SEIBRO is more restrictive than DART
MAX_RETRIES = 3


def _seibro_api_key() -> str:
    key = os.getenv("SEIBRO_API_KEY", "")
    if not key or key == "your_seibro_api_key_here":
        raise EnvironmentError(
            "SEIBRO_API_KEY not set. Register on data.go.kr, apply for dataset "
            "15001145, add key to .env as SEIBRO_API_KEY=<your_key>"
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
            # Check SEIBRO result code
            result_code = _xml_text(root.find("header"), "resultCode")
            if result_code and result_code != "00":
                result_msg = _xml_text(root.find("header"), "resultMsg")
                log.warning("SEIBRO API error code=%s msg=%r (url=%s)", result_code, result_msg, url)
                return None
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


def _load_isin_map() -> dict[str, list[str]]:
    """
    Load bond_isin_map.parquet → {corp_code (8-char): [isin1, isin2, ...]}

    Requires build_isin_map.py to have been run first.
    Returns empty dict if the file does not exist.
    """
    map_path = PROCESSED / "bond_isin_map.parquet"
    if not map_path.exists():
        log.warning(
            "bond_isin_map.parquet not found. Run build_isin_map.py first. "
            "Extractor will skip all companies."
        )
        return {}
    df = pd.read_parquet(map_path)
    result: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        cc = str(row["corp_code"]).zfill(8)
        isin = str(row["bond_isin"]).strip()
        if isin:
            result.setdefault(cc, []).append(isin)
    log.info("Loaded ISIN map: %d corp_codes, %d total bonds", len(result), sum(len(v) for v in result.values()))
    return result


def fetch_repricing_for_bond(
    bond_isin: str, corp_code: str, api_key: str
) -> list[dict]:
    """
    Fetch repricing (리픽싱) history for one bond ISIN from SEIBRO dataset 15001145.

    Uses getXrcStkOptionXrcInfoN1 (exercise conditions + repricing per bond).

    Returns list of dicts with keys matching the bilingual schema used by
    run_cb_bw_timelines.py score_events():
      {"date": "YYYYMMDD", "new_price": float, "bond_name": str, "reason": str}

    Also keeps Korean key aliases for forward-compatibility:
      {"조정일자": "YYYYMMDD", "조정가액": float}
    """
    params = {
        "serviceKey": api_key,
        "bondIsin": bond_isin,
        "numOfRows": "100",
        "pageNo": "1",
    }
    root = _fetch_xml(REPRICING_SVC, params)
    if root is None:
        return []

    items = root.findall(".//item")
    if not items:
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

    log.debug("bond_isin=%s corp_code=%s: %d repricing records", bond_isin, corp_code, len(repricings))
    return repricings


def fetch_exercises_for_bond(
    bond_isin: str, corp_code: str, api_key: str
) -> list[dict]:
    """
    Fetch exercise/conversion details for one bond ISIN from SEIBRO dataset 15001145.

    Uses getXrcStkStatInfoN1 (CB/BW bond details including exercise terms).

    Returns list of dicts with keys matching the bilingual schema used by
    run_cb_bw_timelines.py score_events():
      {"exercise_date": "YYYYMMDD", "shares": int, "exercise_price": float}

    Also keeps Korean key aliases:
      {"권리행사일": "YYYYMMDD"}
    """
    params = {
        "serviceKey": api_key,
        "bondIsin": bond_isin,
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

    log.debug("bond_isin=%s corp_code=%s: %d exercise records", bond_isin, corp_code, len(exercises))
    return exercises


def _match_to_event(
    records: list[dict],
    event_issue_date: str,
    date_key: str,
    window_days: int = 90,
) -> list[dict]:
    """
    Filter records to those on or after the event issue_date.

    Repricing and exercise events occur after issuance. We keep all records
    from issue_date onward; the scoring function applies its own temporal logic.

    date_key: the key in each record dict that holds the event date (YYYYMMDD).
    window_days: unused (kept for API compatibility); filter is issue_date-forward.
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
            if rec_dt >= issue_dt:
                matched.append(rec)
        except Exception:
            matched.append(rec)

    return matched


def _fetch_or_load_cache(
    cache_path: Path,
    fetch_fn: Callable[[], list[dict]],
    force: bool,
    dry_run: bool,
    sleep: float,
) -> list[dict]:
    """Return cached data if available, otherwise fetch, cache, and return."""
    if not force and cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    if dry_run:
        return []
    data = fetch_fn()
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    time.sleep(sleep)
    return data


def enrich_cb_bw_parquet(
    force: bool = False,
    rebuild: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    dry_run: bool = False,
) -> pd.DataFrame:
    """
    Read cb_bw_events.parquet, enrich repricing_history and exercise_events columns,
    write back to the same file.

    Loops over (corp_code, bond_isin) pairs from bond_isin_map.parquet.
    Cache files are named by bond ISIN (not corp_code).

    When force=False: only enriches corp_codes where repricing_history == "[]".
    When force=True: re-fetches for all corp_codes.

    dry_run=True: fetches from API (or uses cache) but does NOT write back to parquet.
    """
    cb_path = PROCESSED / "cb_bw_events.parquet"
    if not cb_path.exists():
        log.error("cb_bw_events.parquet not found. Run extract_cb_bw.py first.")
        return pd.DataFrame()

    df = pd.read_parquet(cb_path)
    log.info("Loaded %d CB/BW events from %s", len(df), cb_path)

    # Load ISIN map (corp_code → [isin, ...])
    isin_map = _load_isin_map()
    if not isin_map:
        log.error("No ISIN map available. Run build_isin_map.py first.")
        return df

    if not dry_run:
        api_key = _seibro_api_key()
    else:
        api_key = "DRY_RUN_NO_KEY"
        log.info("--dry-run: no API calls will be made")

    # Identify corp_codes to enrich
    if force or rebuild:
        corps_to_enrich = df["corp_code"].astype(str).str.zfill(8).unique().tolist()
    else:
        mask = df["repricing_history"].apply(
            lambda x: x == "[]" if isinstance(x, str) else True
        )
        corps_to_enrich = df.loc[mask, "corp_code"].astype(str).str.zfill(8).unique().tolist()

    # Build (corp_code, isin) pairs, restricted to companies in ISIN map
    pairs: list[tuple[str, str]] = []
    for cc in corps_to_enrich:
        isins = isin_map.get(cc, [])
        for isin in isins:
            pairs.append((cc, isin))

    if not pairs:
        log.warning("No (corp_code, ISIN) pairs to enrich. Check bond_isin_map.parquet.")
        return df

    if sample is not None:
        # Sample by unique corp_codes, not raw pairs
        sample_corps = corps_to_enrich[:sample]
        pairs = [(cc, isin) for cc, isin in pairs if cc in set(sample_corps)]
        log.info("--sample %d: limiting to %d corp_codes (%d bond pairs)", sample, len(sample_corps), len(pairs))

    log.info("Enriching %d (corp_code, ISIN) pairs with SEIBRO repricing + exercise data", len(pairs))

    # Cache directories (named by ISIN)
    repricing_cache_dir = RAW / "seibro" / "repricings"
    exercise_cache_dir = RAW / "seibro" / "exercises"
    repricing_cache_dir.mkdir(parents=True, exist_ok=True)
    exercise_cache_dir.mkdir(parents=True, exist_ok=True)

    # Fetch and cache per (corp_code, isin)
    repricing_by_corp: dict[str, list[dict]] = {}
    exercise_by_corp: dict[str, list[dict]] = {}

    for i, (corp_code, bond_isin) in enumerate(pairs, 1):
        log.info(
            "SEIBRO fetch %d/%d (corp_code=%s isin=%s)",
            i, len(pairs), corp_code, bond_isin,
        )

        repr_cache = repricing_cache_dir / f"{bond_isin}.json"
        exer_cache = exercise_cache_dir / f"{bond_isin}.json"

        repricings = _fetch_or_load_cache(
            repr_cache,
            lambda isin=bond_isin, cc=corp_code: fetch_repricing_for_bond(isin, cc, api_key),
            force, dry_run, sleep,
        )
        exercises = _fetch_or_load_cache(
            exer_cache,
            lambda isin=bond_isin, cc=corp_code: fetch_exercises_for_bond(isin, cc, api_key),
            force, dry_run, sleep,
        )

        # Aggregate across multiple ISINs for the same corp_code
        repricing_by_corp.setdefault(corp_code, []).extend(repricings)
        exercise_by_corp.setdefault(corp_code, []).extend(exercises)

    if dry_run:
        log.info("--dry-run complete. No parquet updated.")
        return df

    # Write enriched data back to parquet
    enriched_count = 0
    repricing_updates: dict[int, str] = {}
    exercise_updates: dict[int, str] = {}

    for idx, row in df.iterrows():
        corp_code = str(row["corp_code"]).zfill(8)
        if corp_code not in repricing_by_corp:
            continue

        issue_date = str(row.get("issue_date", ""))
        repricings = _match_to_event(
            repricing_by_corp[corp_code], issue_date, date_key="date"
        )
        exercises = _match_to_event(
            exercise_by_corp[corp_code], issue_date, date_key="exercise_date"
        )
        repricing_updates[idx] = json.dumps(repricings, ensure_ascii=False)
        exercise_updates[idx] = json.dumps(exercises, ensure_ascii=False)
        if repricings or exercises:
            enriched_count += 1

    if repricing_updates:
        df.loc[list(repricing_updates.keys()), "repricing_history"] = pd.Series(repricing_updates)
        df.loc[list(exercise_updates.keys()), "exercise_events"] = pd.Series(exercise_updates)

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
        "--rebuild", action="store_true",
        help="Re-enrich all corp_codes from cached raw files without re-fetching from API",
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
        rebuild=args.rebuild,
        sample=args.sample,
        sleep=args.sleep,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
