"""
extract_revenue_schedule.py — Phase 2: 매출명세서 (revenue schedule) from DART 사업보고서.

Three-step DART chain per company-year:
  1. dart.list(corp_code, kind="A") → filter to annual 사업보고서 → rcept_no per year
  2. dart.sub_docs(rcept_no, match="매출") → select 매출명세서 attachment by title
  3. requests.get(url) + pd.read_html(html) → revenue-by-customer/segment table

Input corpus: companies from beneish_scores.parquet where m_score > threshold,
OR explicit --corp-codes.

Standalone only — NOT wired into the main pipeline.

Output:
  01_Data/processed/revenue_schedule.parquet
  Columns: corp_code, rcept_no, report_year, row_label, revenue_krw,
           revenue_year, parse_status

Usage:
  python 02_Pipeline/extract_revenue_schedule.py --corp-codes 01049167,01051092
  python 02_Pipeline/extract_revenue_schedule.py --years 2021,2022,2023
  python 02_Pipeline/extract_revenue_schedule.py --sample 10 --sleep 0.5
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
RAW_DIR = ROOT / "01_Data" / "raw" / "dart" / "revenue_schedule"

SLEEP_DEFAULT = 0.5
DEFAULT_M_SCORE_THRESHOLD = -1.78

# Labels to skip when parsing revenue tables
_SKIP_LABELS = {"", "nan", "품목", "고객", "합계", "합 계", "합  계", "계", "소계", "구분"}

REQUIRED_COLS = [
    "corp_code", "rcept_no", "report_year", "row_label",
    "revenue_krw", "revenue_year", "parse_status",
]


# ── DART chain ────────────────────────────────────────────────────────────────


def _fetch_revenue_html(
    rcept_no: str,
    dart,
    raw_dir: Path,
    force: bool = False,
) -> tuple[str | None, str]:
    """
    Fetch 매출명세서 HTML for one 사업보고서 filing.

    Returns (html, status) where status ∈ {cached, fetched, no_subdoc, fetch_error}.
    Caches HTML to raw_dir/<rcept_no>.html.

    Note: "no_subdoc" often means the revenue schedule is embedded in the main body
    rather than as a named attachment — manual DART viewer review required.
    """
    cache_path = raw_dir / f"{rcept_no}.html"

    if cache_path.exists() and not force:
        with open(cache_path, encoding="utf-8") as f:
            return f.read(), "cached"

    try:
        sub_df = dart.sub_docs(rcept_no, match="매출")
    except Exception as exc:
        log.debug("dart.sub_docs failed for rcept_no=%s: %s", rcept_no, exc)
        return None, "fetch_error"

    if sub_df is None or len(sub_df) == 0:
        return None, "no_subdoc"

    # Title column may be "title" or "menu_nm" depending on OpenDartReader version
    title_col = None
    for col in ("title", "menu_nm"):
        if col in sub_df.columns:
            title_col = col
            break

    # Priority: row whose title contains 매출명세 or 매출액명세; fallback to iloc[0]
    selected_row = None
    if title_col is not None:
        priority = sub_df[sub_df[title_col].str.contains("매출명세|매출액명세", na=False)]
        if len(priority) > 0:
            selected_row = priority.iloc[0]

    if selected_row is None:
        selected_row = sub_df.iloc[0]

    # Resolve URL column
    if "url" in sub_df.columns:
        url = selected_row["url"]
    else:
        url = selected_row.iloc[-1]

    if not url or not str(url).startswith("http"):
        return None, "no_subdoc"

    try:
        resp = requests.get(str(url), headers=DART_HTML_HEADERS, timeout=30)
        if resp.status_code != 200:
            log.warning("DART viewer returned %d for rcept_no=%s", resp.status_code, rcept_no)
            return None, "fetch_error"
        html = resp.text
    except Exception as exc:
        log.warning("HTTP error fetching revenue HTML rcept_no=%s: %s", rcept_no, exc)
        return None, "fetch_error"

    raw_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(html)

    return html, "fetched"


def _extract_year_columns(df: pd.DataFrame, report_year: int) -> dict[int, str]:
    """
    Map year integers to column names in the revenue DataFrame.

    Explicit years: regex r"(20\\d{2}|19\\d{2})" on each column name.
    Implicit fallback: 당기 → report_year, 전기 → report_year-1, 전전기 → report_year-2.
    Only accepts years 2010–2030.
    """
    year_map: dict[int, str] = {}

    for col in df.columns:
        col_str = str(col)
        # Explicit year in column name
        matches = re.findall(r"(20\d{2}|19\d{2})", col_str)
        for m in matches:
            yr = int(m)
            if 2010 <= yr <= 2030:
                year_map[yr] = col

        # Implicit period names (only if no explicit year found yet for that period)
        if "당기" in col_str and report_year not in year_map:
            year_map[report_year] = col
        elif "전전기" in col_str and (report_year - 2) not in year_map:
            year_map[report_year - 2] = col
        elif "전기" in col_str and (report_year - 1) not in year_map:
            year_map[report_year - 1] = col

    return year_map


def _parse_revenue_table(html: str, report_year: int) -> list[dict] | None:
    """
    Parse 매출명세서 HTML table into a list of row dicts.

    Returns None if no valid table found.
    Each dict has: row_label, revenue_krw, revenue_year.
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
        # Skip empty or single-column tables
        if table.shape[1] < 2 or len(table) == 0:
            continue

        # Flatten MultiIndex headers
        if isinstance(table.columns, pd.MultiIndex):
            table.columns = [" ".join(str(c) for c in col).strip() for col in table.columns]

        year_map = _extract_year_columns(table, report_year)
        if not year_map:
            continue

        rows = []
        for _, row in table.iterrows():
            label = str(row.iloc[0]).strip()
            label_normalized = " ".join(label.split())  # collapse internal whitespace
            if not label or label.lower() == "nan" or label_normalized in _SKIP_LABELS:
                continue

            for yr, col in year_map.items():
                if 2010 <= yr <= 2030:
                    rev = _parse_krw(row[col], unit_multiplier)
                    rows.append({
                        "row_label": label,
                        "revenue_krw": rev,
                        "revenue_year": yr,
                    })

        # Valid table: at least 1 non-None revenue value
        has_value = any(r["revenue_krw"] is not None for r in rows)
        if rows and has_value:
            return rows

    return None


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_revenue_schedule(
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
    Fetch 매출명세서 revenue schedules from 사업보고서.

    Input corpus: companies from beneish_scores.parquet where m_score > threshold,
    or explicit corp_codes_filter.
    Writes 01_Data/processed/revenue_schedule.parquet.
    """
    try:
        import OpenDartReader
    except ImportError:
        raise ImportError("opendartreader is required: uv add opendartreader")

    out = PROCESSED / ("revenue_schedule_preview.parquet" if sample is not None else "revenue_schedule.parquet")
    if sample is not None:
        log.info("SAMPLE MODE: output → %s (production parquet untouched)", out.name)
    elif out.exists() and not force and not rebuild and corp_codes_filter is None:
        log.info("revenue_schedule.parquet exists, loading cached (use --force or --rebuild to refresh)")
        return pd.read_parquet(out)

    if years is None:
        years = [2021, 2022, 2023]

    # Determine corpus
    if corp_codes_filter:
        corp_codes = [_norm_corp_code(c) for c in corp_codes_filter]
        log.info("--corp-codes filter: %d companies", len(corp_codes))
    else:
        scores_path = PROCESSED / "beneish_scores.parquet"
        if not scores_path.exists():
            raise FileNotFoundError(
                "beneish_scores.parquet not found. Run beneish_screen.py first."
            )
        scores = pd.read_parquet(scores_path)
        flagged = scores[scores["m_score"] > m_score_threshold]
        corp_codes = [_norm_corp_code(c) for c in flagged["corp_code"].dropna().unique()]
        log.info(
            "m_score > %.2f → %d companies to screen", m_score_threshold, len(corp_codes)
        )

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

        if i % 50 == 0 or i == 1:
            log.info("Revenue schedule %d/%d (corp_code=%s)", i, total, corp_code)

        for year in years:
            rcept_no = _fetch_annual_report_rcept_no(corp_code, dart, year)
            if not rcept_no:
                all_rows.append({
                    "corp_code": corp_code,
                    "rcept_no": "",
                    "report_year": year,
                    "row_label": "",
                    "revenue_krw": None,
                    "revenue_year": year,
                    "parse_status": "no_filing",
                })
                parse_status_counts["no_filing"] = parse_status_counts.get("no_filing", 0) + 1
                continue

            html, html_status = _fetch_revenue_html(rcept_no, dart, RAW_DIR, force=force)

            if html_status in ("no_subdoc", "fetch_error"):
                all_rows.append({
                    "corp_code": corp_code,
                    "rcept_no": rcept_no,
                    "report_year": year,
                    "row_label": "",
                    "revenue_krw": None,
                    "revenue_year": year,
                    "parse_status": html_status,
                })
                parse_status_counts[html_status] = parse_status_counts.get(html_status, 0) + 1
                time.sleep(sleep)
                continue

            parsed = _parse_revenue_table(html, year)
            if parsed is None:
                all_rows.append({
                    "corp_code": corp_code,
                    "rcept_no": rcept_no,
                    "report_year": year,
                    "row_label": "",
                    "revenue_krw": None,
                    "revenue_year": year,
                    "parse_status": "parse_error",
                })
                parse_status_counts["parse_error"] = parse_status_counts.get("parse_error", 0) + 1
            else:
                for rev_row in parsed:
                    all_rows.append({
                        "corp_code": corp_code,
                        "rcept_no": rcept_no,
                        "report_year": year,
                        **rev_row,
                        "parse_status": "success",
                    })
                parse_status_counts["success"] = parse_status_counts.get("success", 0) + len(parsed)

            time.sleep(sleep)

    log.info("parse_status distribution: %s", parse_status_counts)

    if not all_rows:
        df_out = pd.DataFrame(columns=REQUIRED_COLS)
        # Enforce correct dtypes on empty DataFrame
        df_out["report_year"] = df_out["report_year"].astype("Int64")
        df_out["revenue_year"] = df_out["revenue_year"].astype("Int64")
    else:
        df_out = pd.DataFrame(all_rows)[REQUIRED_COLS]
        df_out["report_year"] = pd.to_numeric(df_out["report_year"], errors="coerce").astype("Int64")
        df_out["revenue_year"] = pd.to_numeric(df_out["revenue_year"], errors="coerce").astype("Int64")
        # Dedup on (corp_code, rcept_no, row_label, revenue_year)
        df_out = df_out.drop_duplicates(
            subset=["corp_code", "rcept_no", "row_label", "revenue_year"], keep="last"
        )

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(out, index=False)
    log.info("Written %d revenue schedule rows to %s", len(df_out), out)
    return df_out


def main():
    parser = argparse.ArgumentParser(
        description="Fetch 매출명세서 (revenue schedule) from DART 사업보고서"
    )
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch all data from API (overwrites cache + parquet)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild parquet from cached raw files without re-fetching from API")
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=SLEEP_DEFAULT)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument(
        "--corp-codes", type=str, default=None,
        help="Comma-separated corp_codes (e.g. 01051092,01207761)",
    )
    parser.add_argument(
        "--years", type=str, default="2021,2022,2023",
        help="Comma-separated fiscal years to fetch (default: 2021,2022,2023)",
    )
    parser.add_argument(
        "--m-score-threshold", type=float, default=DEFAULT_M_SCORE_THRESHOLD,
        help="M-score threshold for selecting companies (default: -1.78)",
    )
    args = parser.parse_args()

    corp_codes_filter = (
        [c.strip().zfill(8) for c in args.corp_codes.split(",")]
        if args.corp_codes
        else None
    )
    years = [int(y.strip()) for y in args.years.split(",")]

    fetch_revenue_schedule(
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
