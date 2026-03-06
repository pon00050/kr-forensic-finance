"""
extract_kftc.py — KFTC 재벌 cross-shareholding data extraction layer.

Data source: KFTC Corporate Group Portal (egroup.go.kr) — annual bulk disclosure
of 대규모기업집단 (large corporate group) designated by the Fair Trade Commission.

Coverage note: Only covers groups designated as 대규모기업집단 (≥5 trillion KRW
in assets). Smaller KOSDAQ manipulation targets — the primary focus of this project —
typically fall outside KFTC scope. For those companies, the officer network graph
(Milestone 4 / officer_network.py) serves the same analytical purpose using DART data.

Data format: The portal provides downloadable Excel/CSV files and a public REST API.
This module uses the public API first; falls back to parsing the download pages if needed.

Output: 01_Data/raw/kftc/ — JSON per download type.

Usage:
    python 02_Pipeline/extract_kftc.py
    python 02_Pipeline/extract_kftc.py --group "삼성"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from _pipeline_helpers import write_json as _write_json

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
RAW_KFTC = ROOT / "01_Data" / "raw" / "kftc"

KFTC_PORTAL = "https://egroup.go.kr"
KFTC_API = "https://egroup.go.kr/api"  # OpenAPI base — verify endpoint at portal

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; kr-forensic-research/0.1; "
        "educational/research use)"
    ),
    "Accept": "application/json, text/html",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _get(url: str, params: dict | None = None, timeout: int = 20) -> requests.Response:
    for attempt in range(1, 4):
        try:
            resp = SESSION.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            time.sleep(1.0)
            return resp
        except requests.RequestException as exc:
            if attempt == 3:
                raise
            log.warning("GET attempt %d failed: %s", attempt, exc)
            time.sleep(3.0)
    raise RuntimeError("Unreachable")


def fetch_group_list() -> list[dict]:
    """
    Fetch the current 대규모기업집단 designated group names and affiliate counts.

    Returns list of dicts with keys: group_name, designation_year, affiliate_count,
    total_assets_trillion_krw.

    Output: 01_Data/raw/kftc/group_list.json
    """
    log.info("Fetching KFTC group list")
    results: list[dict] = []

    # Try public API
    api_key = os.getenv("KFTC_API_KEY", "")
    if api_key and api_key != "your_kftc_openapi_key_here":
        try:
            resp = _get(
                f"{KFTC_API}/groupList",
                params={"serviceKey": api_key, "type": "json", "numOfRows": "200"},
            )
            data = resp.json()
            items = data.get("response", {}).get("body", {}).get("items", [])
            if isinstance(items, list):
                results = items
            elif isinstance(items, dict):
                results = [items]
            log.info("API returned %d groups", len(results))
        except Exception as exc:
            log.warning("KFTC API group list failed: %s — trying HTML scrape", exc)

    # HTML scrape fallback
    if not results:
        try:
            resp = _get(f"{KFTC_PORTAL}/groupList.do")
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if table:
                headers = [th.get_text(strip=True) for th in table.find_all("th")]
                for row in table.find_all("tr")[1:]:
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if cells:
                        results.append(dict(zip(headers, cells)))
            log.info("HTML scrape found %d groups", len(results))
        except Exception as exc2:
            log.warning("KFTC HTML group list scrape failed: %s", exc2)
            log.warning(
                "Manual download available at: %s/groupList.do — "
                "download CSV and place in 01_Data/raw/kftc/group_list_manual.csv",
                KFTC_PORTAL,
            )

    out = RAW_KFTC / "group_list.json"
    _write_json(out, results)
    return results


def fetch_cross_holdings(group_name: str) -> list[dict]:
    """
    Fetch 주식소유 현황 (shareholding structure) for a named corporate group.

    Returns list of holding relationships with keys:
    holder_corp, target_corp, shares_held, pct_held, fiscal_year.

    Output: 01_Data/raw/kftc/{group_name}/cross_holdings.json
    """
    log.info("Fetching cross holdings: %s", group_name)
    results: list[dict] = []

    api_key = os.getenv("KFTC_API_KEY", "")
    if api_key and api_key != "your_kftc_openapi_key_here":
        try:
            resp = _get(
                f"{KFTC_API}/stockOwnership",
                params={
                    "serviceKey": api_key,
                    "groupName": group_name,
                    "type": "json",
                    "numOfRows": "500",
                },
            )
            data = resp.json()
            items = data.get("response", {}).get("body", {}).get("items", [])
            if isinstance(items, list):
                results = items
            elif isinstance(items, dict):
                results = [items]
        except Exception as exc:
            log.warning("KFTC API cross holdings failed for %s: %s — trying HTML scrape", group_name, exc)

    if not results:
        try:
            resp = _get(
                f"{KFTC_PORTAL}/stockList.do",
                params={"groupName": group_name},
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if table:
                headers = [th.get_text(strip=True) for th in table.find_all("th")]
                for row in table.find_all("tr")[1:]:
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if cells:
                        results.append(dict(zip(headers, cells)))
        except Exception as exc2:
            log.warning("KFTC HTML cross holdings scrape failed for %s: %s", group_name, exc2)

    safe_name = group_name.replace("/", "_").replace(" ", "_")
    out = RAW_KFTC / safe_name / "cross_holdings.json"
    _write_json(out, {"group_name": group_name, "holdings": results})
    return results


def run(target_group: str | None = None) -> None:
    """
    Fetch KFTC data. If target_group is given, fetches only that group.
    Otherwise fetches all designated groups.
    """
    groups = fetch_group_list()

    if target_group:
        groups = [g for g in groups if target_group in str(g.get("group_name", ""))]
        if not groups:
            log.warning("No group matching '%s' found in group list", target_group)
            # Still attempt the fetch with the provided name directly
            fetch_cross_holdings(target_group)
            return

    for i, group in enumerate(groups, 1):
        name = group.get("group_name") or group.get("그룹명") or str(group)
        log.info("[%d/%d] Cross holdings: %s", i, len(groups), name)
        fetch_cross_holdings(str(name))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract KFTC cross-shareholding data")
    parser.add_argument("--group", help="Single group name (e.g. '삼성')")
    args = parser.parse_args()
    run(args.group)
