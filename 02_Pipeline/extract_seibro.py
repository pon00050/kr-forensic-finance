"""
extract_seibro.py — SEIBRO CB/BW data extraction layer.

SEIBRO (seibro.or.kr) provides convertible bond (CB) and bond with warrant (BW)
issuance terms and exercise/conversion history.

--- Investigation sequence (complete in order, cheapest-first) ---

Step 1 — Call SEIBRO before writing any scraper:
  Phone: 1577-6600  |  Open API portal: openplatform.seibro.or.kr
  Ask: "Is granular CB/BW conversion term and exercise history data available
       via API or bulk download?"
  If YES → register for API key (2–3 day approval), implement in 1–2 days.
  If NO  → proceed to Step 2.

Step 2 — XHR endpoint probe (this module, run --probe):
  The websquare endpoints below may return data with the right headers.
  Use --probe to see raw response status, headers, and body without full scraping.
  Also use browser devtools (F12 → Network) on seibro.or.kr to capture the exact
  XHR payload + headers the WebSquare UI sends, then replay with requests.
  Confidence: 50% (WebSquare sometimes exposes endpoints; sometimes needs session state)

Step 3 — Playwright XHR interception (only if Steps 1–2 fail, Days 3–5):
  Uncomment playwright in pyproject.toml, install, then write a spike script:
    from playwright.async_api import async_playwright
  Launch headless browser, navigate to CB 사채현황 → 권리조정내역, intercept XHR.
  Spend max 3 days. If blocked by CAPTCHA/session/auth → defer to Phase 3.
  Alternative: Claude Agent SDK + Playwright MCP (see 30_Multi_Agent_Implementation_Guide.md)

Key insight: DART DS005 already covers CB/BW issuance terms and repricing notices.
SEIBRO adds granular exercise history by date. Phase 2 delivers value without SEIBRO —
it only loses exercise timing precision. SEIBRO is an enhancement, not a dependency.

--- API vs. scraping strategy ---
  - SEIBRO has a partial OpenAPI at https://seibro.or.kr/websquare/service/
  - Endpoint availability changes without notice — this module tries the API first
    and falls back to HTML scraping for endpoints that return errors or empty responses.
  - All HTML scraping uses BeautifulSoup on publicly accessible pages.
  - Rate limiting: 1 request/sec to avoid triggering SEIBRO's IP throttle.

Output: 01_Data/raw/seibro/{corp_code}/ — JSON per fetch type.

Usage:
    python 02_Pipeline/extract_seibro.py --corp-code 00126380
    python 02_Pipeline/extract_seibro.py --corp-codes-file 01_Data/raw/dart/cb_bw_corp_codes.txt
    python 02_Pipeline/extract_seibro.py --probe --corp-code 00126380
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from _pipeline_helpers import write_json as _write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
RAW_SEIBRO = ROOT / "01_Data" / "raw" / "seibro"

# SEIBRO base URLs — subject to change; update if scraping breaks.
SEIBRO_BASE = "https://seibro.or.kr"
SEIBRO_API = f"{SEIBRO_BASE}/websquare/service/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; kr-forensic-research/0.1; "
        "educational/research use; contact: see project README)"
    ),
    "Accept": "application/json, text/html",
    "Referer": SEIBRO_BASE,
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _get(url: str, params: dict | None = None, timeout: int = 15) -> requests.Response:
    """GET with retry and rate limit."""
    for attempt in range(1, 4):
        try:
            resp = SESSION.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            time.sleep(1.0)  # SEIBRO rate limit
            return resp
        except requests.RequestException as exc:
            if attempt == 3:
                raise
            log.warning("GET attempt %d failed: %s — retrying", attempt, exc)
            time.sleep(2.0)
    raise RuntimeError("Unreachable")


def _post(url: str, data: dict, timeout: int = 15) -> requests.Response:
    """POST with retry and rate limit."""
    for attempt in range(1, 4):
        try:
            resp = SESSION.post(url, data=data, timeout=timeout)
            resp.raise_for_status()
            time.sleep(1.0)
            return resp
        except requests.RequestException as exc:
            if attempt == 3:
                raise
            log.warning("POST attempt %d failed: %s — retrying", attempt, exc)
            time.sleep(2.0)
    raise RuntimeError("Unreachable")


def fetch_cb_issuance_terms(corp_code: str) -> list[dict]:
    """
    Fetch CB (전환사채) issuance conditions and repricing history for corp_code.

    Strategy: POST to SEIBRO OpenAPI bond search endpoint. Falls back to
    HTML scrape of the public CB search page if API returns no data.

    Output: 01_Data/raw/seibro/{corp_code}/cb_issuance_terms.json
    """
    log.info("Fetching CB issuance terms: %s", corp_code)
    results: list[dict] = []

    # Attempt API call
    try:
        api_url = f"{SEIBRO_API}BondService"
        payload = {
            "W2XPOP_CMD": "getCBList",
            "isuCd": corp_code,
            "pageSize": "100",
            "pageNo": "1",
        }
        resp = _post(api_url, payload)
        data = resp.json()
        if isinstance(data, list):
            results = data
        elif isinstance(data, dict) and "list" in data:
            results = data["list"]
    except Exception as exc:
        log.warning("SEIBRO API CB terms failed for %s: %s — trying HTML scrape", corp_code, exc)

    # HTML scrape fallback
    if not results:
        try:
            url = f"{SEIBRO_BASE}/websquare/main.html#CB_issuance"
            params = {"isuCd": corp_code}
            resp = _get(url, params=params)
            soup = BeautifulSoup(resp.text, "html.parser")
            # Parse table rows — SEIBRO renders data in standard HTML tables
            table = soup.find("table", {"id": "grid1"}) or soup.find("table")
            if table:
                headers = [th.get_text(strip=True) for th in table.find_all("th")]
                for row in table.find_all("tr")[1:]:
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if cells and len(cells) == len(headers):
                        results.append(dict(zip(headers, cells)))
            log.info("HTML scrape found %d CB records for %s", len(results), corp_code)
        except Exception as exc2:
            log.warning("SEIBRO HTML CB scrape failed for %s: %s", corp_code, exc2)

    out = RAW_SEIBRO / corp_code / "cb_issuance_terms.json"
    _write_json(out, {"corp_code": corp_code, "type": "CB", "records": results})
    return results


def fetch_bw_issuance_terms(corp_code: str) -> list[dict]:
    """
    Fetch BW (신주인수권부사채) warrant terms for corp_code.

    Same API/scrape strategy as CB.

    Output: 01_Data/raw/seibro/{corp_code}/bw_issuance_terms.json
    """
    log.info("Fetching BW issuance terms: %s", corp_code)
    results: list[dict] = []

    try:
        api_url = f"{SEIBRO_API}BondService"
        payload = {
            "W2XPOP_CMD": "getBWList",
            "isuCd": corp_code,
            "pageSize": "100",
            "pageNo": "1",
        }
        resp = _post(api_url, payload)
        data = resp.json()
        if isinstance(data, list):
            results = data
        elif isinstance(data, dict) and "list" in data:
            results = data["list"]
    except Exception as exc:
        log.warning("SEIBRO API BW terms failed for %s: %s — trying HTML scrape", corp_code, exc)

    if not results:
        try:
            url = f"{SEIBRO_BASE}/websquare/main.html#BW_issuance"
            params = {"isuCd": corp_code}
            resp = _get(url, params=params)
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if table:
                headers = [th.get_text(strip=True) for th in table.find_all("th")]
                for row in table.find_all("tr")[1:]:
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if cells and len(cells) == len(headers):
                        results.append(dict(zip(headers, cells)))
        except Exception as exc2:
            log.warning("SEIBRO HTML BW scrape failed for %s: %s", corp_code, exc2)

    out = RAW_SEIBRO / corp_code / "bw_issuance_terms.json"
    _write_json(out, {"corp_code": corp_code, "type": "BW", "records": results})
    return results


def fetch_exercise_history(corp_code: str) -> list[dict]:
    """
    Fetch 권리행사내역 (actual CB conversion / BW exercise events) for corp_code.

    These are the timestamps showing when bondholders actually converted or exercised,
    which can be cross-referenced with price peaks to detect coordinated pump-and-dump.

    Output: 01_Data/raw/seibro/{corp_code}/exercise_history.json
    """
    log.info("Fetching exercise history: %s", corp_code)
    results: list[dict] = []

    try:
        api_url = f"{SEIBRO_API}BondExerciseService"
        payload = {
            "W2XPOP_CMD": "getExerciseList",
            "isuCd": corp_code,
            "pageSize": "200",
            "pageNo": "1",
        }
        resp = _post(api_url, payload)
        data = resp.json()
        if isinstance(data, list):
            results = data
        elif isinstance(data, dict) and "list" in data:
            results = data["list"]
    except Exception as exc:
        log.warning("SEIBRO API exercise history failed for %s: %s — trying HTML scrape", corp_code, exc)

    if not results:
        try:
            url = f"{SEIBRO_BASE}/websquare/main.html#CB_exercise"
            params = {"isuCd": corp_code}
            resp = _get(url, params=params)
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if table:
                headers = [th.get_text(strip=True) for th in table.find_all("th")]
                for row in table.find_all("tr")[1:]:
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if cells and len(cells) == len(headers):
                        results.append(dict(zip(headers, cells)))
        except Exception as exc2:
            log.warning("SEIBRO HTML exercise history scrape failed for %s: %s", corp_code, exc2)

    out = RAW_SEIBRO / corp_code / "exercise_history.json"
    _write_json(out, {"corp_code": corp_code, "records": results})
    return results


def run(corp_codes: list[str]) -> None:
    """Fetch all SEIBRO data for the given corp_codes."""
    for i, code in enumerate(corp_codes, 1):
        log.info("[%d/%d] SEIBRO fetch: %s", i, len(corp_codes), code)
        fetch_cb_issuance_terms(code)
        fetch_bw_issuance_terms(code)
        fetch_exercise_history(code)


def probe(corp_code: str) -> None:
    """
    Send raw probe requests to SEIBRO endpoints and dump status, headers, and body.

    Use this during the XHR endpoint investigation (Step 2) to determine whether
    the websquare endpoints are accessible without a browser session.

    Results are printed to stdout and saved to:
        01_Data/raw/seibro/{corp_code}/probe_results.json
    """
    endpoints = [
        {
            "name": "CB list (BondService/getCBList)",
            "method": "POST",
            "url": f"{SEIBRO_API}BondService",
            "data": {"W2XPOP_CMD": "getCBList", "isuCd": corp_code,
                     "pageSize": "10", "pageNo": "1"},
        },
        {
            "name": "BW list (BondService/getBWList)",
            "method": "POST",
            "url": f"{SEIBRO_API}BondService",
            "data": {"W2XPOP_CMD": "getBWList", "isuCd": corp_code,
                     "pageSize": "10", "pageNo": "1"},
        },
        {
            "name": "Exercise history (BondExerciseService/getExerciseList)",
            "method": "POST",
            "url": f"{SEIBRO_API}BondExerciseService",
            "data": {"W2XPOP_CMD": "getExerciseList", "isuCd": corp_code,
                     "pageSize": "10", "pageNo": "1"},
        },
        {
            "name": "Main page GET (session/cookie test)",
            "method": "GET",
            "url": SEIBRO_BASE,
            "data": {},
        },
    ]

    results = []
    for ep in endpoints:
        log.info("Probing: %s", ep["name"])
        try:
            if ep["method"] == "POST":
                resp = SESSION.post(ep["url"], data=ep["data"], timeout=15)
            else:
                resp = SESSION.get(ep["url"], timeout=15)

            body_preview = resp.text[:2000]
            result = {
                "name": ep["name"],
                "url": ep["url"],
                "method": ep["method"],
                "payload": ep["data"],
                "status_code": resp.status_code,
                "content_type": resp.headers.get("Content-Type", ""),
                "response_headers": dict(resp.headers),
                "body_length": len(resp.text),
                "body_preview": body_preview,
                "is_json": False,
                "json_keys": [],
            }
            # Try to parse as JSON
            try:
                parsed = resp.json()
                result["is_json"] = True
                if isinstance(parsed, dict):
                    result["json_keys"] = list(parsed.keys())
                elif isinstance(parsed, list):
                    result["json_keys"] = [f"list[{len(parsed)}]"]
            except Exception:
                pass

            results.append(result)
            log.info("  status=%d  content-type=%s  body=%d chars  json=%s",
                     resp.status_code, result["content_type"],
                     result["body_length"], result["is_json"])
            if result["is_json"] and result["json_keys"]:
                log.info("  json keys: %s", result["json_keys"])
            else:
                log.info("  body preview: %s", body_preview[:200])

        except Exception as exc:
            result = {"name": ep["name"], "error": str(exc)}
            results.append(result)
            log.warning("  FAILED: %s", exc)

        time.sleep(1.0)

    out = RAW_SEIBRO / corp_code / "probe_results.json"
    _write_json(out, {"corp_code": corp_code, "probe_results": results})
    log.info("Probe results saved to %s", out)
    print("\n=== PROBE SUMMARY ===")
    for r in results:
        if "error" in r:
            print(f"  FAIL  {r['name']}: {r['error']}")
        else:
            verdict = "JSON" if r["is_json"] else "non-JSON"
            print(f"  {r['status_code']}  {verdict:8s}  {r['name']}")
    print(f"\nFull results: {out}")
    print("\nNext steps:")
    print("  If status=200 + JSON: endpoint works. Implement directly in fetch_*() functions.")
    print("  If status=200 + non-JSON (HTML shell): WebSquare requires JS session. Try Playwright.")
    print("  If status=4xx/5xx: endpoint blocked or path changed. Check devtools for correct URL.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract SEIBRO CB/BW data")
    parser.add_argument("--probe", action="store_true",
                        help="Probe mode: send raw requests and dump responses (Step 2 investigation)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--corp-code", help="Single DART corp_code")
    group.add_argument(
        "--corp-codes-file",
        help="Path to text file with one corp_code per line",
    )
    args = parser.parse_args()

    if args.corp_code:
        codes = [args.corp_code]
    else:
        codes = Path(args.corp_codes_file).read_text(encoding="utf-8").splitlines()
        codes = [c.strip() for c in codes if c.strip()]

    if args.probe:
        probe(codes[0])
    else:
        run(codes)
