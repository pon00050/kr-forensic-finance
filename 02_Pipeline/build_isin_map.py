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
  4. Deduplicate and save to bond_isin_map.parquet

Data source: apis.data.go.kr dataset 15043421 (금융위원회_채권발행정보)
API key: SEIBRO_API_KEY in .env (same portal key covers both SEIBRO and FSC datasets)
Rate limit: 10,000 calls/day (development tier)
IMPORTANT: Use lowercase 'serviceKey', NOT capital 'ServiceKey' (capital returns 401)

ISIN format: Korean bond ISINs are 12 chars — "KR" + 10 alphanumeric (e.g. KR6241821B33).
CB/BW ISINs typically start with "KR6".

Output:
  01_Data/processed/bond_isin_map.parquet
    Columns: corp_code (str, 8-char), bond_isin (str, 12-char),
             corp_name, isin_name, bond_issue_date, bond_expiry_date

Usage:
  python 02_Pipeline/build_isin_map.py
  python 02_Pipeline/build_isin_map.py --sample 20 --sleep 0.1
  python 02_Pipeline/build_isin_map.py --corp-codes 01051092,01207761
"""

from __future__ import annotations

import argparse
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

FSC_BOND_URL = (
    "https://apis.data.go.kr/1160100/service/GetBondTradInfoService"
    "/getIssuIssuItemStat"
)

# Keywords in isinCdNm that indicate CB/BW bonds (vs regular corporate bonds)
CB_BW_KEYWORDS = ["CB", "전환", "BW", "신주인수권", "교환", "EB"]


def _api_key() -> str:
    key = os.getenv("SEIBRO_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "SEIBRO_API_KEY not set. This key (from data.go.kr) also covers "
            "the FSC bond issuance API. Add to .env."
        )
    return key


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
            r = requests.get(FSC_BOND_URL, params=params, timeout=30)
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


def build_isin_map(
    corp_codes: list[str],
    corp_name_map: dict[str, str],
    sleep: float = 0.1,
    append: bool = False,
) -> pd.DataFrame:
    """Build bond_isin_map.parquet for the given corp_codes."""
    api_key = _api_key()

    rows: list[dict] = []
    no_name = 0
    no_bonds = 0
    api_calls = 0

    for i, corp_code in enumerate(corp_codes, 1):
        cc = str(corp_code).zfill(8)
        corp_name = corp_name_map.get(cc)

        if not corp_name:
            log.info("[%d/%d] %s — no corp_name in map, skipping", i, len(corp_codes), cc)
            no_name += 1
            continue

        log.info("[%d/%d] %s (%s)", i, len(corp_codes), cc, corp_name)
        items = _fetch_bonds_for_company(corp_name, api_key, sleep)
        api_calls += 1
        time.sleep(sleep)

        if not items:
            log.info("  No bonds found in FSC data")
            no_bonds += 1
            continue

        cb_items = _filter_cb_bw(items)
        if not cb_items:
            log.info("  %d bonds found but none are CB/BW (filtered out)", len(items))
            no_bonds += 1
            continue

        log.info("  %d CB/BW bonds (of %d total)", len(cb_items), len(items))
        for item in cb_items:
            isin = item.get("isinCd", "")
            if not isin:
                continue
            rows.append({
                "corp_code": cc,
                "bond_isin": isin,
                "corp_name": corp_name,
                "isin_name": item.get("isinCdNm", ""),
                "bond_issue_date": item.get("bondIssuDt", ""),
                "bond_expiry_date": item.get("bondExprDt", ""),
            })

    log.info(
        "Done. %d API calls, %d ISINs found, %d no-name skipped, %d no-bonds.",
        api_calls, len(rows), no_name, no_bonds,
    )

    new_df = pd.DataFrame(
        rows,
        columns=[
            "corp_code", "bond_isin", "corp_name",
            "isin_name", "bond_issue_date", "bond_expiry_date",
        ],
    )

    out_path = PROCESSED / "bond_isin_map.parquet"
    if append and out_path.exists():
        existing = pd.read_parquet(out_path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["corp_code", "bond_isin"])
        combined.to_parquet(out_path, index=False)
        log.info(
            "Updated bond_isin_map.parquet: %d rows (was %d, added %d new)",
            len(combined), len(existing), len(combined) - len(existing),
        )
        return combined
    else:
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
        "--append", action="store_true",
        help="Append to existing bond_isin_map.parquet instead of overwriting",
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

    build_isin_map(corp_codes, corp_name_map, sleep=args.sleep, append=args.append)


if __name__ == "__main__":
    main()
