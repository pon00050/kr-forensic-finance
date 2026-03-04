"""
extract_dart.py — Phase 1: DART data extraction (financials + sector).

Fetches raw data from external sources and writes to 01_Data/raw/.
Nothing is computed or filtered here — that is transform.py's job.

Stages:
  company-list  Build KOSDAQ universe: PyKRX join to dart.corp_codes
  financials    Annual finstate_all per company-year (CFS then OFS fallback)
  sector        WICS industry group snapshot + KSIC code per company

All stages are resumable: existing files are skipped unless --force is passed.
A run_summary.json is written on completion of the financials stage.

Rate budget (conservative 10,000 calls/day):
  Financials : ~1,700 companies x 5 years x 1 call = ~8,500 calls @ 0.5s sleep
  KSIC       : ~1,700 companies x 1 call           = ~1,700 calls @ 0.3s sleep
  WICS       : 25 calls (one per industry group)
  Total      : ~10,225 calls -- split financials across two days if needed,
               or use --start/--end to process fewer years per run.

Usage:
  python 02_Pipeline/extract_dart.py --market KOSDAQ --start 2019 --end 2023
  python 02_Pipeline/extract_dart.py --market KOSDAQ --start 2019 --end 2023 --force
  python 02_Pipeline/extract_dart.py --stage company-list
  python 02_Pipeline/extract_dart.py --stage sector
  python 02_Pipeline/extract_dart.py --corp-code 00126380 --start 2019 --end 2023
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

import OpenDartReader
import pandas as pd
import requests
from dotenv import load_dotenv
load_dotenv()

try:
    from tqdm import tqdm as _tqdm
    _TQDM_AVAILABLE = True
except ImportError:
    _TQDM_AVAILABLE = False

# Windows Unicode fix: OpenDartReader's finstate_all() has a print() that emits Korean
# text (e.g. "연결제무제표"). On Windows the default sys.stdout is cp1252, so that
# print() raises UnicodeEncodeError, which the pipeline's exception handler silently
# catches and records as no_filing — meaning zero API calls are ever made.
# Redirecting sys.stdout to utf-8 before the first finstate_all call fixes this.
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(stream=sys.stdout)],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
RAW = ROOT / "01_Data" / "raw"
RAW_FINANCIALS = RAW / "financials"
RAW_SECTOR = RAW / "sector"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Reference date for PyKRX KOSDAQ universe (company list).
# Last confirmed KOSDAQ trading day of 2023. PyKRX supports historical dates.
KOSDAQ_REF_DATE = "20231229"

# WICS industry group codes and Korean names (25 groups).
# Confirmed live Feb 2026. Source: WISEindex WICS taxonomy.
WICS_INDUSTRY_GROUPS: dict[str, str] = {
    "G1010": "에너지",
    "G1510": "소재",
    "G2010": "자본재",
    "G2020": "상업서비스",
    "G2030": "운송",
    "G2510": "자동차",
    "G2520": "내구소비재",
    "G2530": "의류/의복",
    "G2550": "소매유통",
    "G2560": "여가/서비스",
    "G3010": "식품/음료",
    "G3020": "가정용품",
    "G3030": "식품유통/약국",
    "G3510": "제약/바이오/생명",
    "G3520": "의료기기/서비스",
    "G4010": "은행",
    "G4020": "다각화금융",
    "G4030": "보험",
    "G4510": "소프트웨어",
    "G4520": "하드웨어",
    "G4530": "반도체",
    "G5010": "통신서비스",
    "G5020": "미디어/엔터",
    "G5510": "전기",
    "G5520": "가스",
}

# Sector (2-digit) derived from first 3 chars of group code, e.g. G4510 -> G45.
WICS_SECTOR_NAMES: dict[str, str] = {
    "G10": "에너지",
    "G15": "소재",
    "G20": "산업재",
    "G25": "경기소비재",
    "G30": "필수소비재",
    "G35": "건강관리",
    "G40": "금융",
    "G45": "IT",
    "G50": "통신서비스",
    "G55": "유틸리티",
}

WICS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.wiseindex.com/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

SLEEP_FINANCIALS = 0.5   # seconds between finstate_all calls
SLEEP_KSIC = 0.3         # seconds between dart.company() calls
SLEEP_WICS = 1.0         # seconds between WICS API calls

# Module-level overrides — set via _apply_sleep_override() when --sleep is passed
_sleep_financials = SLEEP_FINANCIALS
_sleep_ksic = SLEEP_KSIC
_sleep_wics = SLEEP_WICS


def _apply_sleep_override(seconds: float) -> None:
    """Override all sleep constants. Call once at startup when --sleep is used."""
    global _sleep_financials, _sleep_ksic, _sleep_wics
    _sleep_financials = seconds
    _sleep_ksic = seconds
    _sleep_wics = seconds
    log.info("--sleep %.2f: overriding all sleep constants", seconds)


# M2: WICS serves recent dates only — no historical snapshots.
# --wics-date YYYYMMDD overrides for within-session reproducibility.
#
# WICS API only serves recent dates — confirmed empirically Feb 2026:
#   20231229, 20241231 return empty (invalid TRD_DT epoch).
#   20260226, 20250131 return full data.
# _find_wics_snapshot_date() probes backwards from today (up to 10 days)
# to find the most recent valid trading date. Falls back to today's date
# if all probes fail (e.g. offline). Called once at module load.
def _find_wics_snapshot_date() -> str:
    from datetime import datetime, timedelta
    today = datetime.today()
    for days_back in range(0, 10):
        dt = (today - timedelta(days=days_back)).strftime("%Y%m%d")
        try:
            resp = requests.get(
                "https://www.wiseindex.com/Index/GetIndexComponets"
                f"?ceil_yn=0&dt={dt}&sec_cd=G4510",
                headers=WICS_HEADERS,
                timeout=10,
            )
            if resp.status_code == 200 and resp.json().get("info", {}).get("CNT", 0) > 0:
                log.debug("WICS snapshot date resolved: %s", dt)
                return dt
        except Exception:
            pass
    return today.strftime("%Y%m%d")


def _last_trading_day_of_year(year: int) -> str:
    """Return the last trading day of `year` as YYYYMMDD by probing WICS.

    Probes Dec 31, 30, 29, 28, 27 in order; returns the first date that
    returns CNT > 0. Falls back to YYYYMMDD 1231 if all probes fail.
    """
    candidates = [(12, 31), (12, 30), (12, 29), (12, 28), (12, 27)]
    for month, day in candidates:
        dt = f"{year}{month:02d}{day:02d}"
        try:
            resp = requests.get(
                "https://www.wiseindex.com/Index/GetIndexComponets"
                f"?ceil_yn=0&dt={dt}&sec_cd=G4510",
                headers=WICS_HEADERS,
                timeout=10,
            )
            if resp.status_code == 200 and resp.json().get("info", {}).get("CNT", 0) > 0:
                result = dt
                log.debug("_last_trading_day_of_year(%d) → %s", year, result)
                return result
        except Exception:
            pass
    result = f"{year}1231"
    log.debug("_last_trading_day_of_year(%d) → %s", year, result)
    return result


WICS_SNAPSHOT_DATE = _find_wics_snapshot_date()


# ---------------------------------------------------------------------------
# DART client
# ---------------------------------------------------------------------------

def _dart() -> OpenDartReader:
    """Initialise and return an OpenDartReader instance."""
    api_key = os.getenv("DART_API_KEY", "")
    if not api_key or api_key == "your_opendart_api_key_here":
        raise EnvironmentError(
            "DART_API_KEY not set. Copy .env.example to .env and add your key."
        )
    return OpenDartReader(api_key)


# ---------------------------------------------------------------------------
# Stage 1: Company list
# ---------------------------------------------------------------------------

def fetch_company_list(market: str = "KOSDAQ", force: bool = False, ref_date: str | None = None) -> pd.DataFrame:
    """
    Build the active company universe for `market` and write to
    01_Data/raw/company_list.parquet.

    Method:
      1. PyKRX get_market_ticker_list -> set of tickers listed on the exchange.
      2. dart.corp_codes -> all DART-registered companies with stock_code.
      3. Inner join on stock_code -- only companies on both lists are kept.

    The stock_market column does NOT exist in dart.corp_codes (confirmed empirically).
    The PyKRX join is the only correct market-filter method.

    ref_date: YYYYMMDD reference date for PyKRX ticker list. Defaults to KOSDAQ_REF_DATE.
    """
    out = RAW / "company_list.parquet"
    if out.exists() and not force:
        log.info("company_list.parquet exists, loading cached (use --force to refresh)")
        return pd.read_parquet(out)

    effective_ref_date = ref_date or KOSDAQ_REF_DATE
    log.info("Fetching %s ticker list from PyKRX (ref date: %s)...", market, effective_ref_date)
    from pykrx import stock  # lazy import — avoids blocking network call at module load time
    tickers = stock.get_market_ticker_list(effective_ref_date, market=market)
    ticker_set = {str(t).zfill(6) for t in tickers}
    log.info("PyKRX returned %d %s tickers", len(ticker_set), market)

    log.info("Fetching DART corp_codes...")
    dart = _dart()
    corp_df = dart.corp_codes.copy()
    corp_df["stock_code"] = (
        corp_df["stock_code"].fillna("").str.strip().str.zfill(6)
    )

    # Inner join: keep only companies whose stock_code appears in the market ticker set
    result = corp_df[corp_df["stock_code"].isin(ticker_set)].copy()
    result["market"] = market
    result = result.reset_index(drop=True)

    log.info("Company list: %d companies matched to %s", len(result), market)
    RAW.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out, index=False)
    log.info("Written: %s", out)
    return result


# ---------------------------------------------------------------------------
# Stage 2: Financial statements
# ---------------------------------------------------------------------------

def _parse_amount(raw) -> float | None:
    """Clean a DART thstrm_amount string to float. Returns None on failure."""
    if raw is None:
        return None
    s = str(raw).replace(",", "").replace(" ", "").strip()
    if not s or s in ("nan", "None", "-", ""):
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _finstate_with_backoff(dart, corp_code: str, year: int, fs_div: str):
    """Call dart.finstate_all() with exponential backoff on Error 020 (rate limit).
    Raises the final exception if all retries are exhausted."""
    delays = [2, 4, 8, 16]
    last_exc = None
    for attempt, delay in enumerate([0] + delays):
        if delay:
            log.warning("DART Error 020 (rate limit) — retrying in %ds (attempt %d/4)", delay, attempt)
            time.sleep(delay)
        try:
            return dart.finstate_all(corp_code, year, fs_div=fs_div)
        except Exception as exc:
            last_exc = exc
            if "020" not in str(exc):
                raise  # not a rate limit error — fail immediately
    raise last_exc


def fetch_financials_for_company(
    corp_code: str,
    corp_name: str,
    years: list[int],
    dart: OpenDartReader,
    force: bool = False,
) -> dict[int, str]:
    """
    Fetch annual financial statements for one company across all requested years.

    Returns a dict: {year: "CFS" | "OFS" | "no_filing"}

    Files written: 01_Data/raw/financials/{corp_code}_{year}.parquet
    Each parquet contains the full finstate_all DataFrame (all sj_div, all accounts)
    plus three metadata columns: _fs_type, _corp_code, _year.

    Two-pass strategy (OpenDartReader v0.2.3 does NOT auto-fallback):
      1. Attempt CFS (연결재무제표)
      2. If empty, attempt OFS (별도재무제표)
      3. If still empty, write a marker parquet with _fs_type="no_filing"
    """
    RAW_FINANCIALS.mkdir(parents=True, exist_ok=True)
    results: dict[int, str] = {}

    for year in years:
        out = RAW_FINANCIALS / f"{corp_code}_{year}.parquet"

        if out.exists() and not force:
            try:
                meta = pd.read_parquet(out, columns=["_fs_type"])
                results[year] = str(meta["_fs_type"].iloc[0]) if len(meta) else "CFS"
            except Exception:
                results[year] = "CFS"  # assume if column missing
            continue

        fs_type = "no_filing"
        df: pd.DataFrame | None = None

        # Pass 1: CFS
        try:
            df = _finstate_with_backoff(dart, corp_code, year, "CFS")
            if df is not None and not df.empty:
                fs_type = "CFS"
            else:
                df = None
        except Exception as exc:
            log.debug("CFS failed %s %d: %s", corp_code, year, exc)
            df = None
        time.sleep(_sleep_financials)

        # Pass 2: OFS fallback
        if df is None:
            try:
                df = _finstate_with_backoff(dart, corp_code, year, "OFS")
                if df is not None and not df.empty:
                    fs_type = "OFS"
                else:
                    fs_type = "no_filing"
                    df = None
            except Exception as exc:
                log.debug("OFS failed %s %d: %s", corp_code, year, exc)
                fs_type = "no_filing"
                df = None
            time.sleep(_sleep_financials)

        if df is not None and not df.empty:
            df = df.copy()
            df["_fs_type"] = fs_type
            df["_corp_code"] = corp_code
            df["_year"] = year
            # Normalise thstrm_amount to string for consistent parquet schema
            if "thstrm_amount" in df.columns:
                df["thstrm_amount"] = df["thstrm_amount"].astype(str)
            df.to_parquet(out, index=False)
        else:
            # Write marker so we know this year was attempted
            pd.DataFrame([{
                "_corp_code": corp_code,
                "_year": year,
                "_fs_type": "no_filing",
            }]).to_parquet(out, index=False)

        results[year] = fs_type

    return results


def fetch_all_financials(
    companies: pd.DataFrame,
    start_year: int,
    end_year: int,
    force: bool = False,
    sample: int | None = None,
    max_minutes: float | None = None,
) -> dict:
    """
    Fetch financial statements for all companies across all years.
    Returns a run summary dict written to 01_Data/raw/run_summary.json.

    sample: if set, limit to the first N companies (for development/testing).
    max_minutes: if set, stop cleanly when wall-clock time exceeds this limit.
    """
    dart = _dart()
    years = list(range(start_year, end_year + 1))
    if sample is not None:
        companies = companies.head(sample)
        log.info("--sample %d: limiting to first %d companies", sample, len(companies))
    total = len(companies)

    deadline = time.monotonic() + max_minutes * 60 if max_minutes is not None else None
    if deadline is not None:
        log.info("--max-minutes %.1f: deadline set (%.0f s from now)", max_minutes, max_minutes * 60)

    start_time = time.monotonic()

    summary: dict = {
        "total_companies": total,
        "years": years,
        "full_data": [],
        "partial_data": [],
        "no_data": [],
        "errors": [],
    }

    company_list = list(companies.itertuples())
    company_iter = _tqdm(company_list, desc="Financials", unit="co") \
        if _TQDM_AVAILABLE else company_list
    for i, row in enumerate(company_iter, 1):
        if deadline is not None and time.monotonic() > deadline:
            log.warning(
                "--max-minutes %.1f exceeded at company %d/%d. Stopping cleanly.",
                max_minutes, i - 1, total,
            )
            break

        corp_code = row.corp_code
        corp_name = getattr(row, "corp_name", corp_code)

        elapsed = time.monotonic() - start_time
        if i > 1 and elapsed > 0:
            rate = (i - 1) / elapsed  # companies/sec (completed so far)
            eta_sec = (total - (i - 1)) / rate
            log.info(
                "[%d/%d] %s (%s) | %.2f c/s | ETA ~%dm%ds",
                i, total, corp_name, corp_code,
                rate, int(eta_sec // 60), int(eta_sec % 60),
            )
        else:
            log.info("[%d/%d] %s (%s)", i, total, corp_name, corp_code)

        try:
            year_results = fetch_financials_for_company(
                corp_code, corp_name, years, dart, force=force
            )
        except Exception as exc:
            log.error("Unexpected error for %s: %s", corp_code, exc)
            summary["errors"].append({"corp_code": corp_code, "error": str(exc)})
            continue

        filed = [y for y, s in year_results.items() if s != "no_filing"]
        if len(filed) == len(years):
            summary["full_data"].append(corp_code)
        elif filed:
            summary["partial_data"].append({
                "corp_code": corp_code,
                "filed_years": filed,
                "missing_years": [y for y in years if y not in filed],
            })
        else:
            summary["no_data"].append(corp_code)

    elapsed_sec = time.monotonic() - start_time
    summary["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    summary["elapsed_minutes"] = round(elapsed_sec / 60, 2)
    return summary


# ---------------------------------------------------------------------------
# Stage 3: Sector -- WICS
# ---------------------------------------------------------------------------

def fetch_wics(snapshot_date: str = WICS_SNAPSHOT_DATE, force: bool = False, year: int | None = None) -> pd.DataFrame:
    """
    Fetch WICS industry group memberships for all 25 groups.
    Writes 01_Data/raw/sector/wics.parquet.

    No market field is returned by WICS -- both KOSPI and KOSDAQ companies appear.
    transform.py filters to KOSDAQ using the company_list ticker set.

    Single-date snapshot (end of 2023). Limitation: WICS assignments change over time,
    but a single snapshot is sufficient for Phase 1 MVP.
    """
    if year is not None:
        snapshot_date = _last_trading_day_of_year(year)
        log.info("fetch_wics: year=%d → snapshot_date=%s", year, snapshot_date)
    out = RAW_SECTOR / "wics.parquet"
    if out.exists() and not force:
        log.info("wics.parquet exists (use --force to refresh)")
        return pd.read_parquet(out)

    RAW_SECTOR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    failed: list[str] = []

    for group_code, group_name in WICS_INDUSTRY_GROUPS.items():
        sector_code = group_code[:3]
        sector_name = WICS_SECTOR_NAMES.get(sector_code, "")
        url = (
            "https://www.wiseindex.com/Index/GetIndexComponets"
            f"?ceil_yn=0&dt={snapshot_date}&sec_cd={group_code}"
        )
        try:
            resp = requests.get(url, headers=WICS_HEADERS, timeout=15)
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")
            companies_in_group = resp.json().get("list", [])
            for c in companies_in_group:
                rows.append({
                    "ticker": str(c.get("CMP_CD", "")).zfill(6),
                    "company_name_wics": c.get("CMP_KOR", ""),
                    "wics_group_code": group_code,
                    "wics_group_name": group_name,
                    "wics_sector_code": sector_code,
                    "wics_sector_name": sector_name,
                    "snapshot_date": snapshot_date,
                })
            log.info("  WICS %s (%s): %d companies", group_code, group_name, len(companies_in_group))
        except Exception as exc:
            log.warning("WICS %s failed: %s", group_code, exc)
            failed.append(group_code)
        time.sleep(_sleep_wics)

    if not rows:
        log.error("No WICS data retrieved. Check HTTPS and browser headers.")
        return pd.DataFrame()

    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker", "wics_group_code"])
    df.to_parquet(out, index=False)
    log.info(
        "WICS written: %s (%d rows, %d unique tickers, %d groups failed)",
        out, len(df), df["ticker"].nunique(), len(failed),
    )
    return df


# ---------------------------------------------------------------------------
# Stage 3: Sector -- KSIC
# ---------------------------------------------------------------------------

def fetch_ksic(
    companies: pd.DataFrame,
    force: bool = False,
    sample: int | None = None,
) -> pd.DataFrame:
    """
    Fetch KSIC industry code (induty_code) for each company via dart.company().
    Writes 01_Data/raw/sector/ksic.parquet.

    DART uses KSIC Rev.10 as of Feb 2026 (Samsung induty_code=264 confirmed).
    One call per company; ~1,700 calls at 0.3s sleep ~= 9 minutes.

    sample: if set, limit to the first N companies (for development/testing).
    """
    if sample is not None:
        companies = companies.head(sample)
        log.info("--sample %d: limiting KSIC fetch to %d companies", sample, len(companies))
    out = RAW_SECTOR / "ksic.parquet"
    RAW_SECTOR.mkdir(parents=True, exist_ok=True)

    # Load existing to resume without re-fetching
    existing: dict[str, str] = {}
    if out.exists() and not force:
        try:
            df_ex = pd.read_parquet(out)
            existing = dict(zip(
                df_ex["corp_code"].astype(str),
                df_ex["induty_code"].fillna("").astype(str),
            ))
            log.info("Loaded %d existing KSIC entries (resuming)", len(existing))
        except Exception:
            pass

    dart = _dart()
    rows: list[dict] = []
    total = len(companies)

    ksic_list = list(companies.itertuples())
    ksic_iter = _tqdm(ksic_list, desc="KSIC", unit="co") \
        if _TQDM_AVAILABLE else ksic_list
    for i, row in enumerate(ksic_iter, 1):
        corp_code = str(row.corp_code)

        if corp_code in existing and not force:
            rows.append({"corp_code": corp_code, "induty_code": existing[corp_code]})
            continue

        induty_code = ""
        try:
            info = dart.company(corp_code)
            if info is not None:
                if hasattr(info, "get"):
                    induty_code = str(info.get("induty_code") or "").strip()
                elif hasattr(info, "induty_code"):
                    induty_code = str(info.induty_code or "").strip()
        except Exception as exc:
            log.debug("KSIC fetch failed %s: %s", corp_code, exc)

        rows.append({"corp_code": corp_code, "induty_code": induty_code})

        if i % 100 == 0:
            log.info("KSIC: %d/%d", i, total)
            # Write incrementally every 100 companies so we don't lose progress
            pd.DataFrame(rows).to_parquet(out, index=False)

        time.sleep(_sleep_ksic)

    # Merge newly fetched rows back over the full existing set so that a
    # --sample run never discards entries for companies outside the sample.
    new_df = pd.DataFrame(rows)
    if existing and sample is not None:
        existing_df = pd.DataFrame(
            [{"corp_code": k, "induty_code": v} for k, v in existing.items()]
        )
        # new_df takes precedence for any corp_code present in both
        merged = (
            pd.concat([existing_df, new_df], ignore_index=True)
            .drop_duplicates(subset="corp_code", keep="last")
            .reset_index(drop=True)
        )
    else:
        merged = new_df

    merged.to_parquet(out, index=False)
    log.info("KSIC written: %s (%d rows)", out, len(merged))
    return merged


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def run(
    market: str = "KOSDAQ",
    start_year: int = 2019,
    end_year: int = 2023,
    stage: str | None = None,
    corp_code: str | None = None,
    force: bool = False,
    sample: int | None = None,
    max_minutes: float | None = None,
    wics_date: str | None = None,
) -> None:
    """Run all stages or a single named stage."""
    if wics_date is not None:
        global WICS_SNAPSHOT_DATE
        WICS_SNAPSHOT_DATE = wics_date
        log.info("WICS snapshot date pinned to %s (--wics-date override)", wics_date)

    ref_date = _last_trading_day_of_year(end_year)

    if stage == "company-list":
        fetch_company_list(market, force=force, ref_date=ref_date)

    elif stage == "financials":
        companies = fetch_company_list(market, force=False)
        if companies.empty:
            log.error("Company list empty. Run --stage company-list first.")
            return
        if corp_code:
            dart = _dart()
            years = list(range(start_year, end_year + 1))
            corp_name_series = companies.loc[
                companies["corp_code"] == corp_code, "corp_name"
            ]
            corp_name = corp_name_series.iloc[0] if not corp_name_series.empty else corp_code
            fetch_financials_for_company(corp_code, corp_name, years, dart, force=force)
        else:
            summary = fetch_all_financials(
                companies, start_year, end_year,
                force=force, sample=sample, max_minutes=max_minutes,
            )
            RAW.mkdir(parents=True, exist_ok=True)
            summary["counts"] = {
                "total": summary["total_companies"],
                "full_data": len(summary["full_data"]),
                "partial_data": len(summary["partial_data"]),
                "no_data": len(summary["no_data"]),
                "errors": len(summary["errors"]),
            }
            summary_path = RAW / "run_summary.json"
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
            log.info("Run summary: %s", summary["counts"])

    elif stage == "sector":
        companies = fetch_company_list(market, force=False, ref_date=ref_date)
        if companies.empty:
            log.error("Company list empty. Run --stage company-list first.")
            return
        fetch_wics(force=force, year=end_year)
        fetch_ksic(companies, force=force, sample=sample)

    else:
        # Full run: all stages
        companies = fetch_company_list(market, force=force, ref_date=ref_date)
        summary = fetch_all_financials(
            companies, start_year, end_year,
            force=force, sample=sample, max_minutes=max_minutes,
        )
        RAW.mkdir(parents=True, exist_ok=True)
        summary["counts"] = {
            "total": summary["total_companies"],
            "full_data": len(summary["full_data"]),
            "partial_data": len(summary["partial_data"]),
            "no_data": len(summary["no_data"]),
            "errors": len(summary["errors"]),
        }
        summary_path = RAW / "run_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        log.info("Financials summary: %s", summary["counts"])
        fetch_wics(force=force, year=end_year)
        fetch_ksic(companies, force=force, sample=sample)
        log.info("=== extract_dart complete ===")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Phase 1: Extract DART financials and sector data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Full run (all stages):
    python 02_Pipeline/extract_dart.py --market KOSDAQ --start 2019 --end 2023

  Company list only:
    python 02_Pipeline/extract_dart.py --stage company-list

  Sector data only (after company list exists):
    python 02_Pipeline/extract_dart.py --stage sector

  Single company (for testing/debugging):
    python 02_Pipeline/extract_dart.py --corp-code 00126380 --start 2022 --end 2023

  Force re-fetch (overwrite existing files):
    python 02_Pipeline/extract_dart.py --stage sector --force
        """,
    )
    parser.add_argument("--market", default="KOSDAQ", choices=["KOSDAQ", "KOSPI"])
    parser.add_argument("--start", type=int, default=2019)
    parser.add_argument("--end", type=int, default=2023)
    parser.add_argument(
        "--stage",
        choices=["company-list", "financials", "sector"],
        help="Run a single stage only (default: all stages in order)",
    )
    parser.add_argument(
        "--corp-code",
        help="Single company corp_code (for --stage financials only)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing raw files (default: skip existing)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help="Limit universe to first N companies (for development/testing)",
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=None,
        metavar="M",
        dest="max_minutes",
        help="Hard wall-clock deadline for the financials fetch loop (default: no limit)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Override all sleep constants (default: 0.5/0.3/1.0). Use 0.1 for test runs.",
    )
    parser.add_argument(
        "--wics-date",
        default=None,
        metavar="YYYYMMDD",
        dest="wics_date",
        help="Pin WICS snapshot date. Note: WICS only serves recent dates.",
    )
    args = parser.parse_args()
    if args.sleep is not None:
        _apply_sleep_override(args.sleep)
    run(
        market=args.market,
        start_year=args.start,
        end_year=args.end,
        stage=args.stage,
        corp_code=args.corp_code,
        force=args.force,
        sample=args.sample,
        max_minutes=args.max_minutes,
        wics_date=args.wics_date,
    )
