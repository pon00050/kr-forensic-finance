"""
extract_depreciation_schedule.py — Phase 2: 감가상각 (depreciation schedule) from DART 사업보고서.

Three-step DART chain per company-year:
  1. dart.list(corp_code, kind="A") → filter to annual 사업보고서 → rcept_no per year
  2. dart.sub_docs(rcept_no, match="감가상각") → select depreciation attachment;
     fallback to match="유형자산" if no 감가상각 attachment found
  3. requests.get(url) + pd.read_html(html) → parse depreciation method/rate or amount table

Two table formats are handled:
  Format A — Method/rate table: rows = asset categories, columns = 방법/내용연수/상각률
  Format B — Amount table: rows = asset categories, columns = year amounts (당기상각비 etc.)

Input corpus: explicit --corp-codes (default: 5 Tier 1 leads) or beneish_scores.parquet.

Standalone only — NOT wired into the main pipeline.

Output:
  01_Data/processed/depreciation_schedule.parquet
  Columns: corp_code, rcept_no, report_year, asset_category, depr_method,
           useful_life_yr, depr_rate_pct, depr_amount_krw, parse_status

Usage:
  python 02_Pipeline/extract_depreciation_schedule.py
  python 02_Pipeline/extract_depreciation_schedule.py --corp-codes 01051092,01207761
  python 02_Pipeline/extract_depreciation_schedule.py --years 2021,2022,2023
  python 02_Pipeline/extract_depreciation_schedule.py --sample 10 --sleep 0.5
"""

from __future__ import annotations

import argparse
import datetime
import logging
import re
import sys
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from _pipeline_helpers import (
    DART_HTML_HEADERS,
    _dart_api_key,
    _detect_unit_multiplier,
    _norm_corp_code,
    _parse_krw,
    fetch_annual_report_rcept_no as _fetch_annual_report_rcept_no,
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
RAW_DIR = ROOT / "01_Data" / "raw" / "dart" / "depreciation"

SLEEP_DEFAULT = 0.5
DEFAULT_M_SCORE_THRESHOLD = -1.78

# Default Tier 1 leads — run without --corp-codes to target these companies
TIER1_CORP_CODES = [
    "01051092",  # 피씨엘
    "01207761",  # 프로브잇
    "00530413",  # 더코디
    "01049167",  # 롤링스톤
    "00619640",  # 에코앤드림
]

# Column keyword patterns for depreciation method/rate tables
_METHOD_KEYWORDS = {"방법", "상각방법", "감가상각방법"}
_LIFE_KEYWORDS = {"내용연수", "기간", "사용연수"}
_RATE_KEYWORDS = {"상각률", "상각비율", "율"}
_AMOUNT_KEYWORDS = {"감가상각비", "상각비", "당기상각", "감가상각액"}

# Asset labels to skip (summaries, headers)
_SKIP_LABELS = {"", "nan", "구분", "합계", "합 계", "계", "소계", "자산"}

REQUIRED_COLS = [
    "corp_code", "rcept_no", "report_year", "asset_category",
    "depr_method", "useful_life_yr", "depr_rate_pct", "depr_amount_krw", "parse_status",
]


# ── DART chain ────────────────────────────────────────────────────────────────


def _fetch_depreciation_html(
    rcept_no: str,
    dart,
    raw_dir: Path,
    force: bool = False,
) -> tuple[str | None, str]:
    """
    Fetch depreciation footnote HTML for one 사업보고서 filing.

    Tries match="감가상각" first; if no attachment found, falls back to match="유형자산".
    Returns (html, status) where status in {cached, fetched, no_subdoc, fetch_error}.
    """
    cache_path = raw_dir / f"{rcept_no}.html"

    if cache_path.exists() and not force:
        with open(cache_path, encoding="utf-8") as f:
            return f.read(), "cached"

    url = None
    for match_term in ("감가상각", "유형자산"):
        try:
            sub_df = dart.sub_docs(rcept_no, match=match_term)
        except Exception as exc:
            log.debug("dart.sub_docs failed rcept_no=%s match=%s: %s", rcept_no, match_term, exc)
            continue

        if sub_df is None or len(sub_df) == 0:
            continue

        title_col = next((c for c in ("title", "menu_nm") if c in sub_df.columns), None)

        # Priority: most specific match
        selected_row = None
        if title_col is not None:
            priority = sub_df[sub_df[title_col].str.contains(
                "감가상각|유형자산|상각", na=False
            )]
            if len(priority) > 0:
                selected_row = priority.iloc[0]

        if selected_row is None:
            selected_row = sub_df.iloc[0]

        url = selected_row["url"] if "url" in sub_df.columns else selected_row.iloc[-1]
        if url and str(url).startswith("http"):
            break
        url = None

    if not url:
        return None, "no_subdoc"

    try:
        resp = requests.get(str(url), headers=DART_HTML_HEADERS, timeout=30)
        if resp.status_code != 200:
            log.warning("DART viewer returned %d for rcept_no=%s", resp.status_code, rcept_no)
            return None, "fetch_error"
        html = resp.text
    except Exception as exc:
        log.warning("HTTP error fetching depreciation HTML rcept_no=%s: %s", rcept_no, exc)
        return None, "fetch_error"

    raw_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(html)

    return html, "fetched"


# ── Parsers ───────────────────────────────────────────────────────────────────

def _col_matches(col_str: str, keywords: set[str]) -> bool:
    return any(kw in col_str for kw in keywords)


def _parse_useful_life(raw: str) -> float | None:
    """Extract a numeric useful life from strings like '40년', '5-10년', '5~10년'."""
    if not raw or str(raw).lower() in ("nan", "none", "-", ""):
        return None
    raw_str = str(raw).replace(",", "")
    # Range: take midpoint
    range_match = re.search(r"(\d+)[-~](\d+)", raw_str)
    if range_match:
        lo, hi = float(range_match.group(1)), float(range_match.group(2))
        return (lo + hi) / 2
    # Single number
    num_match = re.search(r"(\d+(?:\.\d+)?)", raw_str)
    if num_match:
        return float(num_match.group(1))
    return None


def _parse_rate(raw: str) -> float | None:
    """Extract depreciation rate from strings like '25%', '0.25', '25'."""
    if not raw or str(raw).lower() in ("nan", "none", "-", ""):
        return None
    raw_str = str(raw).replace(",", "").replace("%", "")
    num_match = re.search(r"(\d+(?:\.\d+)?)", raw_str)
    if num_match:
        val = float(num_match.group(1))
        # If > 1 and no % sign originally, treat as percentage already
        return val if val <= 100 else val / 100
    return None


def _parse_method_table(table: pd.DataFrame) -> list[dict] | None:
    """
    Parse Format A: method/rate/useful-life table.
    Expected columns include asset category, depreciation method, useful life, rate.
    Returns list of row dicts or None if format not recognized.
    """
    cols = [str(c) for c in table.columns]

    method_col = next((c for c in table.columns if _col_matches(str(c), _METHOD_KEYWORDS)), None)
    life_col = next((c for c in table.columns if _col_matches(str(c), _LIFE_KEYWORDS)), None)
    rate_col = next((c for c in table.columns if _col_matches(str(c), _RATE_KEYWORDS)), None)

    if method_col is None and life_col is None:
        return None

    rows = []
    for _, row in table.iterrows():
        label = str(row.iloc[0]).strip()
        label_norm = " ".join(label.split())
        if not label or label.lower() == "nan" or label_norm in _SKIP_LABELS:
            continue

        method = str(row[method_col]).strip() if method_col is not None else None
        if method and method.lower() in ("nan", "none", "-"):
            method = None

        life = _parse_useful_life(str(row[life_col])) if life_col is not None else None
        rate = _parse_rate(str(row[rate_col])) if rate_col is not None else None

        rows.append({
            "asset_category": label,
            "depr_method": method,
            "useful_life_yr": life,
            "depr_rate_pct": rate,
            "depr_amount_krw": None,
        })

    return rows if rows else None


def _parse_amount_table(table: pd.DataFrame, report_year: int) -> list[dict] | None:
    """
    Parse Format B: amount table with year columns.
    Extract depreciation amounts (당기상각비 rows or rows matching amount keywords).
    """
    unit_multiplier = 1  # will be detected from HTML before calling

    cols_str = [str(c) for c in table.columns]

    # Find year columns
    year_cols: dict[int, object] = {}
    for col in table.columns:
        col_s = str(col)
        matches = re.findall(r"(20\d{2}|19\d{2})", col_s)
        for m in matches:
            yr = int(m)
            if 2010 <= yr <= 2030:
                year_cols[yr] = col
        if "당기" in col_s and report_year not in year_cols:
            year_cols[report_year] = col
        elif "전기" in col_s and (report_year - 1) not in year_cols:
            year_cols[report_year - 1] = col

    if not year_cols:
        return None

    rows = []
    for _, row in table.iterrows():
        label = str(row.iloc[0]).strip()
        label_norm = " ".join(label.split())
        if not label or label.lower() == "nan" or label_norm in _SKIP_LABELS:
            continue

        # Only capture rows that look like depreciation amounts
        if not any(kw in label for kw in _AMOUNT_KEYWORDS):
            continue

        for yr, col in year_cols.items():
            if 2010 <= yr <= 2030:
                amount = _parse_krw(row[col], unit_multiplier)
                rows.append({
                    "asset_category": label,
                    "depr_method": None,
                    "useful_life_yr": None,
                    "depr_rate_pct": None,
                    "depr_amount_krw": amount,
                })

    return rows if rows else None


def _parse_depreciation_table(html: str, report_year: int) -> list[dict] | None:
    """
    Parse depreciation schedule HTML. Tries Format A (method table) first, then Format B.
    """
    unit_multiplier = _detect_unit_multiplier(html)

    tables = None
    for flavor in ("lxml", "html5lib"):
        try:
            tables = pd.read_html(StringIO(html), flavor=flavor)
            break
        except Exception:
            continue

    if not tables:
        return None

    for table in tables:
        if table.shape[1] < 2 or len(table) == 0:
            continue

        if isinstance(table.columns, pd.MultiIndex):
            table.columns = [" ".join(str(c) for c in col).strip() for col in table.columns]

        # Try Format A first
        result = _parse_method_table(table)
        if result:
            return result

        # Try Format B
        result = _parse_amount_table(table, report_year)
        if result:
            # Apply unit multiplier to amounts
            for r in result:
                if r["depr_amount_krw"] is not None:
                    r["depr_amount_krw"] = r["depr_amount_krw"] * unit_multiplier
            return result

    return None


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_depreciation_schedule(
    force: bool = False,
    rebuild: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    max_minutes: float | None = None,
    corp_codes_filter: list[str] | None = None,
    years: list[int] | None = None,
    m_score_threshold: float = DEFAULT_M_SCORE_THRESHOLD,
) -> pd.DataFrame:
    """
    Fetch 감가상각 depreciation schedules from DART 사업보고서.

    Defaults to 5 Tier 1 leads when no --corp-codes specified.
    Writes 01_Data/processed/depreciation_schedule.parquet.
    """
    try:
        import OpenDartReader
    except ImportError:
        raise ImportError("opendartreader is required: uv add opendartreader")

    out = PROCESSED / "depreciation_schedule.parquet"
    if out.exists() and not force and not rebuild and corp_codes_filter is None:
        log.info("depreciation_schedule.parquet exists, loading cached (use --force to refresh)")
        return pd.read_parquet(out)

    if years is None:
        years = [2021, 2022, 2023]

    if corp_codes_filter:
        corp_codes = [_norm_corp_code(c) for c in corp_codes_filter]
        log.info("--corp-codes filter: %d companies", len(corp_codes))
    else:
        # Default: Tier 1 leads only (targeted extraction, not full corpus)
        corp_codes = TIER1_CORP_CODES
        log.info("No --corp-codes specified: targeting %d Tier 1 leads", len(corp_codes))

    if sample is not None:
        corp_codes = corp_codes[:sample]
        log.info("--sample %d applied", sample)

    api_key = _dart_api_key()
    dart = OpenDartReader(api_key)

    deadline = (
        datetime.datetime.now() + datetime.timedelta(minutes=max_minutes)
        if max_minutes else None
    )

    all_rows: list[dict] = []
    total = len(corp_codes)
    parse_status_counts: dict[str, int] = {}

    for i, corp_code in enumerate(corp_codes, 1):
        if deadline and datetime.datetime.now() >= deadline:
            log.info("--max-minutes reached at company %d/%d", i, total)
            break

        log.info("Depreciation schedule %d/%d (corp_code=%s)", i, total, corp_code)

        for year in years:
            rcept_no = _fetch_annual_report_rcept_no(corp_code, dart, year)
            if not rcept_no:
                all_rows.append({
                    "corp_code": corp_code, "rcept_no": "", "report_year": year,
                    "asset_category": "", "depr_method": None,
                    "useful_life_yr": None, "depr_rate_pct": None,
                    "depr_amount_krw": None, "parse_status": "no_filing",
                })
                parse_status_counts["no_filing"] = parse_status_counts.get("no_filing", 0) + 1
                continue

            html, html_status = _fetch_depreciation_html(rcept_no, dart, RAW_DIR, force=force)

            if html_status in ("no_subdoc", "fetch_error"):
                all_rows.append({
                    "corp_code": corp_code, "rcept_no": rcept_no, "report_year": year,
                    "asset_category": "", "depr_method": None,
                    "useful_life_yr": None, "depr_rate_pct": None,
                    "depr_amount_krw": None, "parse_status": html_status,
                })
                parse_status_counts[html_status] = parse_status_counts.get(html_status, 0) + 1
                time.sleep(sleep)
                continue

            parsed = _parse_depreciation_table(html, year)
            if parsed is None:
                all_rows.append({
                    "corp_code": corp_code, "rcept_no": rcept_no, "report_year": year,
                    "asset_category": "", "depr_method": None,
                    "useful_life_yr": None, "depr_rate_pct": None,
                    "depr_amount_krw": None, "parse_status": "parse_error",
                })
                parse_status_counts["parse_error"] = parse_status_counts.get("parse_error", 0) + 1
            else:
                for dep_row in parsed:
                    all_rows.append({
                        "corp_code": corp_code,
                        "rcept_no": rcept_no,
                        "report_year": year,
                        **dep_row,
                        "parse_status": "success",
                    })
                parse_status_counts["success"] = parse_status_counts.get("success", 0) + len(parsed)

            time.sleep(sleep)

    log.info("parse_status distribution: %s", parse_status_counts)

    if not all_rows:
        df_out = pd.DataFrame(columns=REQUIRED_COLS)
        df_out["report_year"] = df_out["report_year"].astype("Int64")
    else:
        df_out = pd.DataFrame(all_rows)[REQUIRED_COLS]
        df_out["report_year"] = pd.to_numeric(df_out["report_year"], errors="coerce").astype("Int64")
        df_out["useful_life_yr"] = pd.to_numeric(df_out["useful_life_yr"], errors="coerce")
        df_out["depr_rate_pct"] = pd.to_numeric(df_out["depr_rate_pct"], errors="coerce")
        df_out["depr_amount_krw"] = pd.to_numeric(df_out["depr_amount_krw"], errors="coerce")
        df_out = df_out.drop_duplicates(
            subset=["corp_code", "rcept_no", "asset_category", "report_year"], keep="last"
        )

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(out, index=False)
    log.info("Written %d depreciation schedule rows to %s", len(df_out), out)
    return df_out


def main():
    parser = argparse.ArgumentParser(
        description="Fetch 감가상각 (depreciation schedule) from DART 사업보고서. "
                    "Defaults to 5 Tier 1 leads if no --corp-codes provided."
    )
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch all data from API (overwrites cache + parquet)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild parquet from cached raw files without re-fetching")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=SLEEP_DEFAULT)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument(
        "--corp-codes", type=str, default=None,
        help="Comma-separated corp_codes (default: 5 Tier 1 leads)",
    )
    parser.add_argument(
        "--years", type=str, default="2021,2022,2023",
        help="Comma-separated fiscal years (default: 2021,2022,2023)",
    )
    parser.add_argument(
        "--m-score-threshold", type=float, default=DEFAULT_M_SCORE_THRESHOLD,
        help="M-score threshold when not using --corp-codes (default: -1.78)",
    )
    args = parser.parse_args()

    corp_codes_filter = (
        [c.strip().zfill(8) for c in args.corp_codes.split(",")]
        if args.corp_codes
        else None
    )
    years = [int(y.strip()) for y in args.years.split(",")]

    fetch_depreciation_schedule(
        force=args.force,
        rebuild=args.rebuild,
        sample=args.sample,
        sleep=args.sleep,
        max_minutes=args.max_minutes,
        corp_codes_filter=corp_codes_filter,
        years=years,
        m_score_threshold=args.m_score_threshold,
    )


if __name__ == "__main__":
    main()
