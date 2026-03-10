"""
extract_bondholder_register.py — Phase 2: 사채권자명부 from DART CB filings.

Three-step DART chain per company:
  1. dart.list(corp_code, kind="B") → filter to 전환사채 filings → rcept_nos
  2. dart.sub_docs(rcept_no, match="사채권자명부") → sub-document URL
  3. requests.get(url) + pd.read_html(html) → bondholder table

Standalone only — NOT wired into the main pipeline.

Output:
  01_Data/processed/bondholder_register.parquet
  Columns: corp_code, rcept_no, rcept_dt, holder_name, address,
           face_value_krw, note, parse_status

Usage:
  python 02_Pipeline/extract_bondholder_register.py --corp-codes 01049167
  python 02_Pipeline/extract_bondholder_register.py --sample 10 --sleep 0.5
"""

from __future__ import annotations

import argparse
import datetime
import logging
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
RAW_DIR = ROOT / "01_Data" / "raw" / "dart" / "bondholder_register"

SLEEP_DEFAULT = 0.5

# Column name aliases for bondholder tables — add variants as discovered (see KI-014)
_BONDHOLDER_COL_ALIASES: dict[str, list[str]] = {
    "holder_name":    [
        "성명/법인명", "사채권자명", "발행 대상자명", "발행대상자명",
        "채권자명", "법인명", "성명", "권리자명", "인수인",
    ],
    "address":        ["주소", "소재지", "사업자번호"],
    "face_value_krw": [
        "사채권면액", "발행권면", "채권금액", "납입금액", "인수금액", "권면금액",
    ],
    "note":           ["비고", "참고", "관계", "만기"],
}

REQUIRED_COLS = [
    "corp_code", "rcept_no", "rcept_dt", "holder_name", "address",
    "face_value_krw", "note", "parse_status",
]


# ── DART chain ────────────────────────────────────────────────────────────────

def _fetch_cb_filings(
    corp_code: str,
    dart,
    bgn_de: str,
    end_de: str,
) -> list[tuple[str, str]]:
    """
    Fetch list of 전환사채 filings for one company.

    Returns [(rcept_no, rcept_dt), ...].
    TODO: only fetches first page — add pagination if a company has >100 CB filings.
    """
    try:
        df = dart.list(corp_code, start=bgn_de, end=end_de, kind="B")
    except Exception as exc:
        log.debug("dart.list failed for corp_code=%s: %s", corp_code, exc)
        return []

    if df is None or len(df) == 0:
        return []

    mask = df["report_nm"].str.contains("전환사채", na=False)
    cb_filings = df[mask]
    if len(cb_filings) == 0:
        return []

    result = []
    for _, row in cb_filings.iterrows():
        rcept_no = str(row.get("rcept_no", "")).strip()
        rcept_dt = str(row.get("rcept_dt", "")).strip()
        if rcept_no:
            result.append((rcept_no, rcept_dt))
    return result


def _fetch_bondholder_html(
    rcept_no: str,
    dart,
    raw_dir: Path,
    force: bool = False,
) -> tuple[str | None, str]:
    """
    Fetch 사채권자명부 HTML for one CB filing.

    Returns (html, status) where status ∈ {cached, fetched, no_subdoc, fetch_error}.
    Caches HTML to raw_dir/<rcept_no>.html.
    """
    cache_path = raw_dir / f"{rcept_no}.html"

    if cache_path.exists() and not force:
        with open(cache_path, encoding="utf-8") as f:
            return f.read(), "cached"

    try:
        sub_df = dart.sub_docs(rcept_no, match="사채권자명부")
    except Exception as exc:
        log.debug("dart.sub_docs failed for rcept_no=%s: %s", rcept_no, exc)
        return None, "fetch_error"

    if sub_df is None or len(sub_df) == 0:
        return None, "no_subdoc"

    # Resolve URL column — try "url" first, fall back to last column
    if "url" in sub_df.columns:
        url = sub_df.iloc[0]["url"]
    else:
        url = sub_df.iloc[0, -1]

    if not url or not str(url).startswith("http"):
        return None, "no_subdoc"

    try:
        resp = requests.get(str(url), headers=DART_HTML_HEADERS, timeout=30)
        if resp.status_code != 200:
            log.warning("DART viewer returned %d for rcept_no=%s", resp.status_code, rcept_no)
            return None, "fetch_error"
        html = resp.text
    except Exception as exc:
        log.warning("HTTP error fetching bondholder HTML rcept_no=%s: %s", rcept_no, exc)
        return None, "fetch_error"

    raw_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(html)

    return html, "fetched"


def _parse_bondholder_table(html: str) -> list[dict] | None:
    """
    Parse 사채권자명부 HTML table into a list of row dicts.

    Returns None if no valid table found.
    """
    unit_multiplier = _detect_unit_multiplier(html)

    # Try lxml first, fall back to html5lib
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
        # Flatten MultiIndex headers
        if isinstance(table.columns, pd.MultiIndex):
            table.columns = [" ".join(str(c) for c in col).strip() for col in table.columns]

        # Promote row 0 to header when pd.read_html assigned integer indices
        # (happens when the HTML table has no <th> row — all cells are <td>)
        if (
            len(table) > 1
            and all(isinstance(c, int) for c in table.columns)
            and table.iloc[0].notna().all()
        ):
            table.columns = [str(v).strip() for v in table.iloc[0]]
            table = table.iloc[1:].reset_index(drop=True)

        col_map: dict[str, str] = {}
        for canonical, aliases in _BONDHOLDER_COL_ALIASES.items():
            for alias in aliases:
                for col in table.columns:
                    if alias in str(col):
                        col_map[canonical] = col
                        break
                if canonical in col_map:
                    break

        # Valid table requires holder_name at minimum
        if "holder_name" not in col_map:
            continue

        known_headers: set[str] = set()
        for aliases in _BONDHOLDER_COL_ALIASES.values():
            known_headers.update(a.strip() for a in aliases)

        rows = []
        for _, row in table.iterrows():
            holder_name = str(row[col_map["holder_name"]]).strip()
            # Skip empty, header-repeat, or summary rows
            if not holder_name or holder_name.lower() == "nan":
                continue
            if holder_name in {"합계", "계", "소계", "합  계"}:
                continue
            if holder_name in known_headers:
                continue

            address = ""
            if "address" in col_map:
                address = str(row[col_map["address"]]).strip()
                if address.lower() == "nan":
                    address = ""

            face_value_krw = None
            if "face_value_krw" in col_map:
                face_value_krw = _parse_krw(row[col_map["face_value_krw"]], unit_multiplier)

            note = ""
            if "note" in col_map:
                note = str(row[col_map["note"]]).strip()
                if note.lower() == "nan":
                    note = ""

            rows.append({
                "holder_name": holder_name,
                "address": address,
                "face_value_krw": face_value_krw,
                "note": note,
            })

        if rows:
            return rows

    return None


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_bondholder_register(
    force: bool = False,
    rebuild: bool = False,
    sample: int | None = None,
    sleep: float = SLEEP_DEFAULT,
    max_minutes: float | None = None,
    corp_codes_filter: list[str] | None = None,
    bgn_de: str = "20160101",
    end_de: str | None = None,
) -> pd.DataFrame:
    """
    Fetch 사채권자명부 for CB filings.

    Input corpus: corp_codes from cb_bw_events.parquet, filtered by corp_codes_filter.
    Writes 01_Data/processed/bondholder_register.parquet.
    """
    try:
        import OpenDartReader
    except ImportError:
        raise ImportError("opendartreader is required: uv add opendartreader")

    out = PROCESSED / ("bondholder_register_preview.parquet" if sample is not None else "bondholder_register.parquet")
    if sample is not None:
        log.info("SAMPLE MODE: output → %s (production parquet untouched)", out.name)
    elif out.exists() and not force and not rebuild and corp_codes_filter is None:
        log.info("bondholder_register.parquet exists, loading cached (use --force or --rebuild to refresh)")
        return pd.read_parquet(out)

    cb_path = PROCESSED / "cb_bw_events.parquet"
    if not cb_path.exists():
        raise FileNotFoundError("cb_bw_events.parquet not found. Run extract_cb_bw.py first.")

    events = pd.read_parquet(cb_path)
    all_corp_codes = [_norm_corp_code(c) for c in events["corp_code"].dropna().unique()]

    if corp_codes_filter:
        all_corp_codes = [c for c in all_corp_codes if c in set(corp_codes_filter)]
        log.info("--corp-codes filter: %d companies", len(all_corp_codes))

    if sample is not None:
        all_corp_codes = all_corp_codes[:sample]
        log.info("--sample %d applied", sample)

    if end_de is None:
        end_de = datetime.date.today().strftime("%Y%m%d")

    api_key = _dart_api_key()
    dart = OpenDartReader(api_key)

    deadline = (
        datetime.datetime.now() + datetime.timedelta(minutes=max_minutes)
        if max_minutes else None
    )

    all_rows: list[dict] = []
    total = len(all_corp_codes)
    parse_status_counts: dict[str, int] = {}

    for i, corp_code in enumerate(all_corp_codes, 1):
        if deadline and datetime.datetime.now() >= deadline:
            log.info("--max-minutes reached at company %d/%d", i, total)
            break

        if i % 50 == 0 or i == 1:
            log.info("Bondholder register %d/%d (corp_code=%s)", i, total, corp_code)

        cb_filings = _fetch_cb_filings(corp_code, dart, bgn_de, end_de)
        if not cb_filings:
            all_rows.append({
                "corp_code": corp_code,
                "rcept_no": "",
                "rcept_dt": "",
                "holder_name": "",
                "address": "",
                "face_value_krw": None,
                "note": "",
                "parse_status": "no_filing",
            })
            parse_status_counts["no_filing"] = parse_status_counts.get("no_filing", 0) + 1
            continue

        for rcept_no, rcept_dt in cb_filings:
            html, html_status = _fetch_bondholder_html(rcept_no, dart, RAW_DIR, force=force)

            if html_status in ("no_subdoc", "fetch_error"):
                all_rows.append({
                    "corp_code": corp_code,
                    "rcept_no": rcept_no,
                    "rcept_dt": rcept_dt,
                    "holder_name": "",
                    "address": "",
                    "face_value_krw": None,
                    "note": "",
                    "parse_status": html_status,
                })
                parse_status_counts[html_status] = parse_status_counts.get(html_status, 0) + 1
                time.sleep(sleep)
                continue

            parsed = _parse_bondholder_table(html)
            if parsed is None:
                all_rows.append({
                    "corp_code": corp_code,
                    "rcept_no": rcept_no,
                    "rcept_dt": rcept_dt,
                    "holder_name": "",
                    "address": "",
                    "face_value_krw": None,
                    "note": "",
                    "parse_status": "parse_error",
                })
                parse_status_counts["parse_error"] = parse_status_counts.get("parse_error", 0) + 1
            else:
                for holder_row in parsed:
                    all_rows.append({
                        "corp_code": corp_code,
                        "rcept_no": rcept_no,
                        "rcept_dt": rcept_dt,
                        **holder_row,
                        "parse_status": "success",
                    })
                parse_status_counts["success"] = parse_status_counts.get("success", 0) + len(parsed)

            time.sleep(sleep)

    log.info("parse_status distribution: %s", parse_status_counts)

    if not all_rows:
        df_out = pd.DataFrame(columns=REQUIRED_COLS)
    else:
        df_out = pd.DataFrame(all_rows)[REQUIRED_COLS]
        # Dedup on (corp_code, rcept_no, holder_name) — keep last
        df_out = df_out.drop_duplicates(subset=["corp_code", "rcept_no", "holder_name"], keep="last")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(out, index=False)
    log.info("Written %d bondholder register rows to %s", len(df_out), out)
    return df_out


def main():
    parser = argparse.ArgumentParser(
        description="Fetch 사채권자명부 (bondholder register) from DART CB filings"
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
        help="Comma-separated corp_codes to fetch (e.g. 01051092,01207761)",
    )
    parser.add_argument(
        "--bgn-de", type=str, default="20160101",
        help="Start date YYYYMMDD (default: 20160101)",
    )
    parser.add_argument(
        "--end-de", type=str, default=None,
        help="End date YYYYMMDD (default: today)",
    )
    args = parser.parse_args()

    corp_codes_filter = (
        [c.strip().zfill(8) for c in args.corp_codes.split(",")]
        if args.corp_codes
        else None
    )

    fetch_bondholder_register(
        force=args.force,
        rebuild=args.rebuild,
        sample=args.sample,
        sleep=args.sleep,
        max_minutes=args.max_minutes,
        corp_codes_filter=corp_codes_filter,
        bgn_de=args.bgn_de,
        end_de=args.end_de,
    )


if __name__ == "__main__":
    main()
