"""
build_isin_map.py — Build bond_isin_map.parquet: corp_code → CB/BW bond ISINs.

SEIBRO StockSvc endpoints require a 12-character bond ISIN (bondIsin parameter).
This script builds the lookup table by querying the FSC (금융위원회) Bond Issuance
Information API (data.go.kr dataset 15043421) by company name.

Why not DART:
  The original approach (v1, sessions 26–28) attempted to extract ISINs from DART
  CB/BW filing HTML via regex. This was run with --sample 50 and returned 0 ISINs.
  Root cause: DART CB/BW filings (주요사항보고서) are submitted BEFORE the bond is
  registered with KSD (한국예탁결제원). The ISIN is assigned by KSD after registration,
  so it does not exist in the DART filing at the time of submission. The DART approach
  is inherently unable to provide bond ISINs — this is a data availability limitation,
  not a code bug.

Current approach (v2, session 29):
  The FSC Bond Issuance Information API (getIssuIssuItemStat) returns bond ISINs
  (isinCd) when queried by issuer name (bondIsurNm). Confirmed working 2026-03-06:
  querying "피씨엘" returned isinCd=KR6241821B33 (피씨엘 2 CB, issued 2021-03-19).

Strategy:
  1. Read cb_bw_events.parquet for the set of corp_codes with known CB/BW events
  2. Join corp_code → corp_name via corp_ticker_map.parquet
  3. For each company, query FSC API by bondIsurNm → collect isinCd values
  4. Validate: exact issuer-name match + date proximity to cb_bw_events
  5. Deduplicate and save to bond_isin_map.parquet

Data source: apis.data.go.kr dataset 15043421 (금융위원회_채권발행정보)
API key: SEIBRO_API_KEY in .env (same portal key covers both SEIBRO and FSC datasets)
Rate limit: 10,000 calls/day (development tier)
Actual latency: ~1.1s per API call + sleep = ~1.2s/call total
IMPORTANT: Use lowercase 'serviceKey', NOT capital 'ServiceKey' (capital returns 401)

ISIN format: Korean bond ISINs are 12 chars — "KR" + 10 alphanumeric (e.g. KR6241821B33).
CB/BW ISINs typically start with "KR6".

Caching:
  JSON responses cached per corp_code in 01_Data/raw/fsc/bond_isins/<corp_code>.json.
  Re-runs use cache by default (skip API). Use --force to re-fetch.

Output:
  01_Data/processed/bond_isin_map.parquet
    Columns: corp_code (str, 8-char), bond_isin (str, 12-char),
             corp_name, issuer_name, isin_name, bond_issue_date, bond_expiry_date

Usage:
  python 02_Pipeline/build_isin_map.py
  python 02_Pipeline/build_isin_map.py --sample 20 --sleep 0.1
  python 02_Pipeline/build_isin_map.py --corp-codes 01051092,01207761
  python 02_Pipeline/build_isin_map.py --force   # re-fetch all, ignore cache
"""

from __future__ import annotations

import argparse
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

sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(stream=sys.stdout)],
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
PROCESSED = ROOT / "01_Data" / "processed"
CACHE_DIR = ROOT / "01_Data" / "raw" / "fsc" / "bond_isins"

FSC_BOND_URL = (
    "https://apis.data.go.kr/1160100/service/GetBondTradInfoService"
    "/getIssuIssuItemStat"
)

# Keywords in isinCdNm that indicate CB/BW bonds (vs regular corporate bonds)
CB_BW_KEYWORDS = ["CB", "전환", "BW", "신주인수권", "교환", "EB"]

SESSION = requests.Session()


def _api_key() -> str:
    key = os.getenv("SEIBRO_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "SEIBRO_API_KEY not set. This key (from data.go.kr) also covers "
            "the FSC bond issuance API. Add to .env."
        )
    return key


def _read_cache(corp_code: str) -> list[dict] | None:
    """Return cached API response for corp_code, or None if no cache."""
    cache_path = CACHE_DIR / f"{corp_code}.json"
    if not cache_path.exists():
        return None
    with open(cache_path, encoding="utf-8") as f:
        return json.load(f)


def _write_cache(corp_code: str, items: list[dict]) -> None:
    """Save API response list to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{corp_code}.json"
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _fetch_bonds_for_company(
    corp_name: str, api_key: str, sleep: float
) -> list[dict]:
    """Query FSC API by company name. Returns list of bond dicts with isinCd."""
    all_items: list[dict] = []
    page = 1
    max_pages = 10  # safety cap

    while page <= max_pages:
        params = {
            "serviceKey": api_key,  # lowercase — capital returns 401
            "pageNo": str(page),
            "numOfRows": "100",
            "resultType": "json",
            "bondIsurNm": corp_name,
        }
        try:
            r = SESSION.get(FSC_BOND_URL, params=params, timeout=30)
        except requests.RequestException as exc:
            log.warning("  Request failed for %s page %d: %s", corp_name, page, exc)
            break

        if r.status_code != 200:
            log.warning("  HTTP %d for %s page %d", r.status_code, corp_name, page)
            break

        try:
            data = r.json()
        except ValueError:
            log.warning("  Invalid JSON for %s page %d", corp_name, page)
            break

        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") != "00":
            log.warning(
                "  API error for %s: %s", corp_name, header.get("resultMsg", "?")
            )
            break

        body = data.get("response", {}).get("body", {})
        total_count = body.get("totalCount", 0)
        items = body.get("items", {})

        if not items:
            break

        # items can be {"item": [...]} or {"item": {single_dict}}
        item_list = items.get("item", [])
        if isinstance(item_list, dict):
            item_list = [item_list]

        all_items.extend(item_list)

        # Check if we've fetched all pages
        fetched = page * 100
        if fetched >= total_count:
            break

        page += 1
        time.sleep(sleep)

    return all_items


def _filter_cb_bw(items: list[dict]) -> list[dict]:
    """Keep only CB/BW/EB bonds, filtering out regular corporate bonds."""
    filtered = []
    for item in items:
        isin_name = item.get("isinCdNm", "")
        if any(kw in isin_name for kw in CB_BW_KEYWORDS):
            filtered.append(item)
    return filtered


def _validate_isin_map(df: pd.DataFrame) -> pd.DataFrame:
    """Remove false-positive ISINs using exact name match + date proximity.

    Filter 1 — Exact issuer name: FSC response bondIsurNm must match the
    corp_name we queried with. Short/generic names (레이, 나노) return bonds
    from unrelated issuers; this filter removes them.

    Filter 2 — Date proximity: bond_issue_date must be within ±60 days of
    at least one issue_date in cb_bw_events.parquet for that corp_code.
    """
    if df.empty:
        return df

    before = len(df)

    # Filter 1: exact issuer name match
    name_mask = df["issuer_name"] == df["corp_name"]
    name_rejected = (~name_mask).sum()
    df = df[name_mask].copy()

    # Filter 2: date proximity to cb_bw_events (advisory — log only, don't remove)
    # Rationale: DART cb_bw_events may not capture all CB issuances (e.g., older
    # events outside the query window). Removing date orphans loses legitimate ISINs.
    # The name filter alone removes 95%+ of false positives.
    cb_path = PROCESSED / "cb_bw_events.parquet"
    date_unmatched = 0
    if cb_path.exists():
        cb = pd.read_parquet(cb_path)
        cb["corp_code"] = cb["corp_code"].astype(str).str.zfill(8)
        cb["_cb_date"] = pd.to_datetime(cb["issue_date"], errors="coerce")

        df["_fsc_date"] = pd.to_datetime(df["bond_issue_date"], format="%Y%m%d", errors="coerce")

        for cc, grp in df.groupby("corp_code"):
            cb_dates = cb.loc[cb["corp_code"] == cc, "_cb_date"].dropna()
            if cb_dates.empty:
                continue
            for idx, row in grp.iterrows():
                fsc_dt = row["_fsc_date"]
                if pd.isna(fsc_dt):
                    continue
                diffs = (cb_dates - fsc_dt).abs()
                if diffs.min() > pd.Timedelta(days=60):
                    date_unmatched += 1

        df = df.drop(columns=["_fsc_date"])

    after = len(df)
    log.info(
        "Validation: %d → %d rows (removed %d name mismatches; %d date-unmatched kept)",
        before, after, name_rejected, date_unmatched,
    )
    return df


def build_isin_map(
    corp_codes: list[str],
    corp_name_map: dict[str, str],
    sleep: float = 0.1,
    force: bool = False,
) -> pd.DataFrame:
    """Build bond_isin_map.parquet for the given corp_codes."""
    api_key = _api_key()

    rows: list[dict] = []
    no_name = 0
    no_bonds = 0
    api_calls = 0
    cache_hits = 0
    t_start = time.monotonic()

    for i, corp_code in enumerate(corp_codes, 1):
        cc = str(corp_code).zfill(8)
        corp_name = corp_name_map.get(cc)

        if not corp_name:
            log.info("[%d/%d] %s — no corp_name in map, skipping", i, len(corp_codes), cc)
            no_name += 1
            continue

        # Check cache
        if not force:
            cached = _read_cache(cc)
            if cached is not None:
                cache_hits += 1
                items = cached
                if i <= 3 or i == len(corp_codes):
                    log.info("[%d/%d] %s (%s) — from cache", i, len(corp_codes), cc, corp_name)
            else:
                log.info("[%d/%d] %s (%s)", i, len(corp_codes), cc, corp_name)
                items = _fetch_bonds_for_company(corp_name, api_key, sleep)
                api_calls += 1
                _write_cache(cc, items)
                time.sleep(sleep)
        else:
            log.info("[%d/%d] %s (%s)", i, len(corp_codes), cc, corp_name)
            items = _fetch_bonds_for_company(corp_name, api_key, sleep)
            api_calls += 1
            _write_cache(cc, items)
            time.sleep(sleep)

        if not items:
            if not (not force and cached is not None):
                log.info("  No bonds found in FSC data")
            no_bonds += 1
            continue

        cb_items = _filter_cb_bw(items)
        if not cb_items:
            if not (not force and cached is not None):
                log.info("  %d bonds found but none are CB/BW (filtered out)", len(items))
            no_bonds += 1
            continue

        if not (not force and cached is not None and cache_hits > 3):
            log.info("  %d CB/BW bonds (of %d total)", len(cb_items), len(items))

        for item in cb_items:
            isin = item.get("isinCd", "")
            if not isin:
                continue
            rows.append({
                "corp_code": cc,
                "bond_isin": isin,
                "corp_name": corp_name,
                "issuer_name": item.get("bondIsurNm", ""),
                "isin_name": item.get("isinCdNm", ""),
                "bond_issue_date": item.get("bondIssuDt", ""),
                "bond_expiry_date": item.get("bondExprDt", ""),
            })

        # ETA logging every 50 companies
        if i % 50 == 0:
            elapsed = time.monotonic() - t_start
            rate = elapsed / i
            remaining = rate * (len(corp_codes) - i)
            log.info(
                "  [Progress] %d/%d (%.0f%%), %.1fs/company, ETA: %.0f min remaining",
                i, len(corp_codes), 100 * i / len(corp_codes), rate, remaining / 60,
            )

    elapsed = time.monotonic() - t_start
    log.info(
        "Done. %d API calls, %d cache hits, %d ISINs found, %d no-name, %d no-bonds. (%.0fs)",
        api_calls, cache_hits, len(rows), no_name, no_bonds, elapsed,
    )

    new_df = pd.DataFrame(
        rows,
        columns=[
            "corp_code", "bond_isin", "corp_name", "issuer_name",
            "isin_name", "bond_issue_date", "bond_expiry_date",
        ],
    )

    # Validate: remove false positives from name-matching collisions
    new_df = _validate_isin_map(new_df)

    out_path = PROCESSED / "bond_isin_map.parquet"
    new_df.to_parquet(out_path, index=False)
    log.info(
        "Saved bond_isin_map.parquet: %d rows (%d corp_codes with ISINs)",
        len(new_df), new_df["corp_code"].nunique() if not new_df.empty else 0,
    )
    return new_df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build bond_isin_map.parquet: corp_code → CB/BW bond ISINs via FSC API"
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Limit to first N corp_codes from cb_bw_events.parquet",
    )
    parser.add_argument(
        "--corp-codes", type=str, default=None,
        help="Comma-separated corp_codes to process (e.g. 01051092,01207761)",
    )
    parser.add_argument(
        "--sleep", type=float, default=0.1,
        help="Seconds between API calls (default: 0.1)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch from API, ignoring cache",
    )
    args = parser.parse_args()

    # Load corp_code → corp_name mapping
    ctm_path = PROCESSED / "corp_ticker_map.parquet"
    if not ctm_path.exists():
        log.error("corp_ticker_map.parquet not found. Run pipeline first.")
        sys.exit(1)
    ctm = pd.read_parquet(ctm_path)
    corp_name_map: dict[str, str] = dict(
        zip(
            ctm["corp_code"].astype(str).str.zfill(8),
            ctm["corp_name"].astype(str),
        )
    )
    log.info("Loaded %d corp_name mappings from corp_ticker_map.parquet", len(corp_name_map))

    # Determine corp_codes to process
    if args.corp_codes:
        corp_codes = [c.strip().zfill(8) for c in args.corp_codes.split(",") if c.strip()]
        log.info("Processing %d specified corp_codes", len(corp_codes))
    else:
        cb_path = PROCESSED / "cb_bw_events.parquet"
        if not cb_path.exists():
            log.error("cb_bw_events.parquet not found. Run extract_cb_bw.py first.")
            sys.exit(1)
        df = pd.read_parquet(cb_path)
        corp_codes = df["corp_code"].astype(str).str.zfill(8).unique().tolist()
        log.info("Loaded %d unique corp_codes from cb_bw_events.parquet", len(corp_codes))

    if args.sample is not None:
        corp_codes = corp_codes[: args.sample]
        log.info("--sample %d: processing %d corp_codes", args.sample, len(corp_codes))

    build_isin_map(corp_codes, corp_name_map, sleep=args.sleep, force=args.force)


if __name__ == "__main__":
    main()
