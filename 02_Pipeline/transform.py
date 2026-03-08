"""
transform.py — Raw → processed: build company_financials.parquet from
01_Data/raw/financials/ and 01_Data/raw/sector/.

Phase 1 only: one output table (company_financials.parquet).
All other tables (CB/BW, price/volume, officers, disclosures, KFTC) are Phase 2/3.

Input:
  01_Data/raw/company_list.parquet          — KOSDAQ universe (from extract_dart.py)
  01_Data/raw/financials/{corp_code}_{year}.parquet  — per-company-year finstate_all
  01_Data/raw/sector/wics.parquet           — WICS industry group membership
  01_Data/raw/sector/ksic.parquet           — DART induty_code per company

Output:
  01_Data/processed/company_financials.parquet

Schema: see 00_Reference/7_Projects/17_MVP_Requirements.md §4.5

Usage:
    python 02_Pipeline/transform.py
    python 02_Pipeline/transform.py --start 2019 --end 2023
"""

from __future__ import annotations

import argparse
from datetime import date
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from _pipeline_helpers import parse_amount as _parse_amount

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(stream=open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False))],
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
RAW = ROOT / "01_Data" / "raw"
RAW_FINANCIALS = RAW / "financials"
RAW_SECTOR = RAW / "sector"
PROCESSED = ROOT / "01_Data" / "processed"

# ---------------------------------------------------------------------------
# Financial sector exclusions
# KSIC Rev.10 640–669 = Section K (financial and insurance activities)
# KSIC 68200 = REITs (Section L — real estate; NOT captured by 640–669)
# ---------------------------------------------------------------------------
FINANCIAL_KSIC_RANGE = range(640, 670)   # 640 inclusive, 669 inclusive
REIT_KSIC_CODE = "68200"

# ---------------------------------------------------------------------------
# DART XBRL unit-scale corrections
#
# DART XBRL filings declare monetary fields with a `scale` attribute
# (-6 = 백만원, -3 = 천원, 0 = 원). The DART API returns raw values WITHOUT
# applying the scale correction. Some filers enter values in 원 but declare
# the wrong scale, producing values inflated by 10^3 or 10^6 in a single year.
#
# Format: {(corp_code, year): divisor}
#
# To add a new correction: verify the error factor against the NEXT year's
# DART filing frmtrm_amount (prior-year restatement column) or an external
# source (FNGuide, KIND, news). Document in BUGS.md before adding here.
# ---------------------------------------------------------------------------
_XBRL_UNIT_CORRECTIONS: dict[tuple[str, int], int] = {
    ("00204226", 2022): 1_000_000,  # 소프트센: filed with scale -6 (백만원 declared, 원 entered)
    ("00390860", 2023): 1_000,       # 대산F&B: scale -3 (천원 declared, 원 entered); 1.288e+14 ÷ 1e3 = 1.288e+11
    ("01219155", 2022): 1_000,      # 네오크레마: scale -3 (천원 declared, 원 entered)
}

# Monetary columns affected by unit-scale errors (all 10 financial metrics)
_MONETARY_COLS: list[str] = [
    "receivables", "revenue", "cogs", "sga", "ppe",
    "depreciation", "total_assets", "lt_debt", "net_income", "cfo",
]

# ---------------------------------------------------------------------------
# Account extraction: account_id (primary) and account_nm Korean (fallback)
#
# Each entry: (list_of_account_ids, list_of_korean_nm_substrings)
# Both lists are checked in order; first match wins.
# ---------------------------------------------------------------------------
ACCOUNT_SPECS: dict[str, tuple[list[str], list[str]]] = {
    "receivables": (
        [
            "dart_ShortTermTradeReceivable",              # DART-specific, labeled 매출채권 (empirical)
            "dart_ShortTermTradeReceivables",
            "ifrs-full_TradeAndOtherCurrentReceivables",  # Sometimes mapped to 기타수취채권 — lower priority
            "ifrs-full_TradeAndOtherReceivables",
        ],
        ["매출채권및기타채권", "매출채권"],
    ),
    "revenue": (
        ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"],
        ["매출액", "수익(매출액)"],
    ),
    "cogs": (
        ["ifrs-full_CostOfSales"],
        ["매출원가"],
    ),
    "sga": (
        [
            "dart_TotalSellingGeneralAdministrativeExpenses",   # confirmed primary (empirical Feb 2026)
            "dart_SellingGeneralAdministrativeExpenses",
            "ifrs-full_SellingGeneralAndAdministrativeExpense",
        ],
        ["판매비와관리비", "판관비", "판매비", "관리비"],
    ),
    "ppe": (
        ["ifrs-full_PropertyPlantAndEquipment"],
        ["유형자산"],
    ),
    "total_assets": (
        ["ifrs-full_Assets"],
        ["자산총계"],
    ),
    # lt_debt has its own fallback chain — handled separately in _extract_lt_debt
    "net_income": (
        ["ifrs-full_ProfitLoss"],
        ["당기순이익"],
    ),
    "depreciation": (
        [
            "ifrs-full_AdjustmentsForDepreciationExpense",
            "ifrs-full_DepreciationAndAmortisationExpense",
        ],
        ["감가상각비", "유형자산상각비"],   # 유형자산상각비 used by non-standard filers (empirical Feb 2026)
    ),
    "cfo": (
        [
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "ifrs-full_CashFlowsFromOperatingActivities",
        ],
        ["영업활동현금흐름", "영업활동으로인한현금흐름"],
    ),
}


def _extract_field(
    df: pd.DataFrame,
    account_ids: list[str],
    account_nms: list[str],
    sj_filter: list[str] | None = None,
) -> tuple[float | None, str | None]:
    """
    Extract a single Beneish field value from a finstate_all DataFrame.

    Search order:
      1. account_id exact match (more reliable — XBRL standard names)
      2. account_nm substring match (fallback for non-standard filers)

    sj_filter: if provided, only look in rows with sj_div in this list.

    Returns:
        (value, method) where method is "exact_id", "korean_substring", or None.
    """
    if df is None or df.empty:
        return (None, None)

    subset = df
    if sj_filter and "sj_div" in df.columns:
        subset = df[df["sj_div"].isin(sj_filter)]
    if subset.empty:
        return (None, None)

    # Primary: exact account_id match
    if "account_id" in subset.columns:
        for aid in account_ids:
            match = subset[subset["account_id"] == aid]
            if not match.empty:
                return (_parse_amount(match.iloc[0].get("thstrm_amount")), "exact_id")

    # Fallback: account_nm substring match
    if "account_nm" in subset.columns:
        for nm in account_nms:
            match = subset[subset["account_nm"].str.contains(nm, na=False, regex=False)]
            if not match.empty:
                return (_parse_amount(match.iloc[0].get("thstrm_amount")), "korean_substring")

    return (None, None)


def _extract_lt_debt(df: pd.DataFrame) -> tuple[float | None, str | None]:
    """
    Extract long-term debt using the confirmed fallback chain:
      1. dart_LongTermBorrowingsGross  (confirmed present in DART XBRL)
      2. dart_BondsIssued              (secondary — corporate bonds)
      3. null — never fall back to 비유동부채 (non-current liabilities total)

    dart_NoncurrentBorrowings does NOT exist. ifrs-full:NoncurrentPortionOfLongtermBorrowings
    does NOT exist. Both confirmed empirically (OQ-E, Feb 2026).

    Returns:
        (value, method) where method is "exact_id", "korean_substring", or None.
    """
    if df is None or df.empty:
        return (None, None)

    # BS rows only
    subset = df
    if "sj_div" in df.columns:
        subset = df[df["sj_div"] == "BS"]

    if "account_id" in subset.columns:
        for aid in ["dart_LongTermBorrowingsGross", "dart_BondsIssued"]:
            match = subset[subset["account_id"] == aid]
            if not match.empty:
                return (_parse_amount(match.iloc[0].get("thstrm_amount")), "exact_id")

    # Korean fallback — only 장기차입금 (long-term borrowings), NOT 비유동부채
    if "account_nm" in subset.columns:
        match = subset[subset["account_nm"].str.contains("장기차입금", na=False, regex=False)]
        if not match.empty:
            return (_parse_amount(match.iloc[0].get("thstrm_amount")), "korean_substring")

    return (None, None)


def _detect_expense_method(df: pd.DataFrame) -> str:
    """
    Determine income statement expense method.
    Returns 'function' if 매출원가 is present, 'nature' otherwise.

    Must check sj_div IN ('IS', 'CIS') — companies reporting CIS without a
    separate IS block are common on KOSDAQ and would be misclassified if only
    'IS' is checked.
    """
    if df is None or df.empty:
        return "nature"

    if "sj_div" not in df.columns or "account_nm" not in df.columns:
        return "nature"

    is_rows = df[df["sj_div"].isin(["IS", "CIS"])]
    if is_rows.empty:
        return "nature"

    has_cogs = is_rows["account_nm"].str.contains("매출원가", na=False, regex=False).any()
    return "function" if has_cogs else "nature"


def _extract_company_year(
    corp_code: str,
    year: int,
) -> dict | None:
    """
    Load and process a single company-year parquet file.
    Returns a dict of extracted field values, or None if no_filing.
    """
    path = RAW_FINANCIALS / f"{corp_code}_{year}.parquet"
    if not path.exists():
        return None

    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        log.warning("Failed to read %s: %s", path, exc)
        return None

    # Marker parquet (no_filing) contains only _corp_code, _year, _fs_type
    fs_type = "CFS"
    if "_fs_type" in df.columns and len(df) > 0:
        fs_type = str(df["_fs_type"].iloc[0])
    if fs_type == "no_filing" or (len(df.columns) <= 3 and "_fs_type" in df.columns):
        return None  # skip no_filing markers

    expense_method = _detect_expense_method(df)

    row: dict = {
        "corp_code": corp_code,
        "year": year,
        "fs_type": fs_type,
        "dart_api_source": f"finstate_all_{fs_type}",
        "expense_method": expense_method,
    }

    # Extract all standard fields (non-lt_debt)
    for field, (ids, nms) in ACCOUNT_SPECS.items():
        if field == "cogs":
            # COGS only from IS/CIS rows
            row[field], row[f"match_method_{field}"] = _extract_field(df, ids, nms, sj_filter=["IS", "CIS"])
        elif field in ("depreciation", "cfo"):
            # Cash flow statement rows
            row[field], row[f"match_method_{field}"] = _extract_field(df, ids, nms, sj_filter=["CF"])
        elif field in ("ppe", "total_assets"):
            # Balance sheet rows
            row[field], row[f"match_method_{field}"] = _extract_field(df, ids, nms, sj_filter=["BS"])
        else:
            row[field], row[f"match_method_{field}"] = _extract_field(df, ids, nms)

    # lt_debt has its own logic
    row["lt_debt"], row["match_method_lt_debt"] = _extract_lt_debt(df)

    # For nature-method companies, cogs and sga are structurally absent — set explicit null
    if expense_method == "nature":
        row["cogs"] = None
        row["sga"] = None
        row["match_method_cogs"] = None
        row["match_method_sga"] = None

    return row


def _detect_xbrl_anomalies(df: pd.DataFrame) -> None:
    """
    Scan for potential DART XBRL unit-scale errors not yet in _XBRL_UNIT_CORRECTIONS.

    Flags company-years where revenue changes >100× YoY (either direction).
    Logs warnings for human review — does NOT auto-correct, because the
    correct divisor (10^3 or 10^6) requires cross-checking against the next
    year's DART frmtrm_amount or an external source.

    See BUGS.md 'DART XBRL unit-scale error' for root cause and confirmed cases.
    """
    df_s = df.sort_values(["corp_code", "year"]).copy()
    df_s["_rev_prev"] = df_s.groupby("corp_code")["revenue"].shift(1)
    df_s["_rev_yoy"] = df_s["revenue"] / df_s["_rev_prev"]

    suspicious = df_s[
        df_s["_rev_prev"].notna() & (df_s["_rev_prev"] > 0) &
        df_s["revenue"].notna() & (df_s["revenue"] > 0) &
        ((df_s["_rev_yoy"] > 100) | (df_s["_rev_yoy"] < 0.01))
    ]

    if suspicious.empty:
        return

    for _, row in suspicious.iterrows():
        log.warning(
            "XBRL anomaly candidate: corp_code=%s (%s) year=%d "
            "revenue=%.3e prev_year=%.3e ratio=%.1f× — "
            "cross-check DART frmtrm_amount; add to _XBRL_UNIT_CORRECTIONS if confirmed",
            row["corp_code"],
            row.get("company_name", "?"),
            int(row["year"]),
            row["revenue"],
            row["_rev_prev"],
            row["_rev_yoy"],
        )
    log.warning(
        "%d XBRL anomaly candidate(s) detected. Investigate each warning above.",
        len(suspicious),
    )


def build_company_financials(
    start_year: int = 2019,
    end_year: int = 2023,
    sample: int | None = None,
) -> pd.DataFrame:
    """
    Build company_financials.parquet from raw per-company-year parquet files.

    Steps:
      1. Load company_list.parquet (KOSDAQ universe from extract_dart.py)
      2. For each company × year: extract financial fields
      3. Join WICS sector (stock_code → industry group)
      4. Join KSIC code (corp_code → induty_code)
      5. Exclude financial sector (KSIC 640–669) and REITs (KSIC 68200)
      6. Apply DART XBRL unit-scale corrections (_XBRL_UNIT_CORRECTIONS)
      7. Detect potential new XBRL unit-scale anomalies (logs warnings)
      8. Detect CFS→OFS year-over-year shifts
      9. Write to 01_Data/processed/company_financials.parquet
    """
    # -----------------------------------------------------------------------
    # 1. Load company list
    # -----------------------------------------------------------------------
    company_list_path = RAW / "company_list.parquet"
    if not company_list_path.exists():
        raise FileNotFoundError(
            "01_Data/raw/company_list.parquet not found. "
            "Run: python 02_Pipeline/extract_dart.py --stage company-list"
        )
    companies = pd.read_parquet(company_list_path)
    log.info("Company list: %d companies", len(companies))
    if sample is not None:
        companies = companies.head(sample)
        log.info("--sample %d: limiting transform to first %d companies", sample, sample)

    # -----------------------------------------------------------------------
    # 2. Extract financial fields for each company-year
    # -----------------------------------------------------------------------
    years = list(range(start_year, end_year + 1))
    rows: list[dict] = []
    total = len(companies)

    for i, comp_row in enumerate(companies.itertuples(), 1):
        corp_code = comp_row.corp_code
        if i % 100 == 0:
            log.info("Processing company %d/%d (corp_code=%s)...", i, total, corp_code)

        for year in years:
            record = _extract_company_year(corp_code, year)
            if record is None:
                continue

            # Attach company identity
            record["ticker"] = getattr(comp_row, "stock_code", None)
            record["company_name"] = getattr(comp_row, "corp_name", None)
            record["market"] = getattr(comp_row, "market", "KOSDAQ")
            rows.append(record)

    if not rows:
        log.warning("No financial data rows extracted. Check 01_Data/raw/financials/.")
        df = _empty_company_financials()
        out = _write_parquet(df, "company_financials")
        _upload_to_r2(out, "processed/company_financials.parquet")
        return df

    df = pd.DataFrame(rows)
    log.info("Extracted %d company-year rows before sector join", len(df))

    # -----------------------------------------------------------------------
    # 3. Join WICS sector
    # -----------------------------------------------------------------------
    wics_path = RAW_SECTOR / "wics.parquet"
    if wics_path.exists():
        wics = pd.read_parquet(wics_path)
        # wics columns from extract_dart.py:
        #   ticker, wics_group_code (e.g. G3510), wics_group_name, wics_sector_code (e.g. G35),
        #   wics_sector_name, snapshot_date
        # We store industry group as wics_sector_code (G3510) because beneish_screen
        # uses group-level codes for high_fp_risk and sector_percentile.
        if "ticker" in wics.columns and "wics_group_code" in wics.columns and "ticker" in df.columns:
            wics_slim = (
                wics[["ticker", "wics_group_code", "wics_group_name"]]
                .rename(columns={"wics_group_code": "wics_sector_code", "wics_group_name": "wics_sector"})
                .drop_duplicates(subset="ticker")
            )
            df = df.merge(wics_slim, on="ticker", how="left")
            log.info(
                "WICS join: %d/%d rows have sector code",
                int(df["wics_sector_code"].notna().sum()),
                len(df),
            )
        else:
            log.warning("WICS parquet missing expected columns; skipping sector join")
            df["wics_sector_code"] = None
            df["wics_sector"] = None
    else:
        log.warning("wics.parquet not found at %s; sector columns will be null", wics_path)
        df["wics_sector_code"] = None
        df["wics_sector"] = None

    # krx_sector is Phase 2 — set to null in Phase 1
    df["krx_sector"] = None

    # -----------------------------------------------------------------------
    # 4. Join KSIC codes
    # -----------------------------------------------------------------------
    ksic_path = RAW_SECTOR / "ksic.parquet"
    if ksic_path.exists():
        ksic = pd.read_parquet(ksic_path)
        if "corp_code" in ksic.columns and "induty_code" in ksic.columns:
            ksic_slim = ksic[["corp_code", "induty_code"]].drop_duplicates(subset="corp_code")
            ksic_slim = ksic_slim.rename(columns={"induty_code": "ksic_code"})
            df = df.merge(ksic_slim, on="corp_code", how="left")
            log.info(
                "KSIC join: %d/%d rows have KSIC code",
                df["ksic_code"].notna().sum(),
                len(df),
            )
        else:
            log.warning("ksic.parquet missing expected columns; skipping KSIC join")
            df["ksic_code"] = None
    else:
        log.warning("ksic.parquet not found at %s; ksic_code column will be null", ksic_path)
        df["ksic_code"] = None

    # -----------------------------------------------------------------------
    # 5. Financial sector exclusions
    #    KSIC 640–669 = Section K (financial and insurance activities)
    #    KSIC 68200   = REITs (Section L — outside Section K range)
    #
    #    DART induty_code is a string like "264", "640", "68200".
    #    Convert to int for range check where possible.
    # -----------------------------------------------------------------------
    before = len(df)
    if "ksic_code" in df.columns and df["ksic_code"].notna().any():
        def _is_financial(code) -> bool:
            if pd.isna(code):
                return False
            s = str(code).strip()
            if s == REIT_KSIC_CODE:
                return True
            try:
                # Use first 3 digits only — DART induty_code is variable-length
                # (3, 4, or 5 digits). int(s) would fail to match 5-digit codes
                # like 66199 (insurance) or 64992 (holding companies) against the
                # 640-669 range. int(s[:3]) correctly maps all of them to their
                # 3-digit KSIC section code.
                return int(s[:3]) in FINANCIAL_KSIC_RANGE
            except (ValueError, TypeError):
                return False

        exclude_mask = df["ksic_code"].apply(_is_financial)
        n_excluded = exclude_mask.sum()
        if n_excluded > 0:
            log.info(
                "Excluding %d rows in financial sector (KSIC 640–669, 68200)",
                n_excluded,
            )
            df = df[~exclude_mask].copy()

    log.info("After exclusions: %d rows (removed %d)", len(df), before - len(df))

    # -----------------------------------------------------------------------
    # 6. Apply DART XBRL unit-scale corrections
    #    Divides all monetary fields by the confirmed error factor for known
    #    mis-scaled filings. Runs on every --stage transform so the fix
    #    persists across pipeline re-runs without manual intervention.
    #    See _XBRL_UNIT_CORRECTIONS for the correction registry and BUGS.md
    #    for root cause analysis and verification methodology.
    # -----------------------------------------------------------------------
    for (corp_code, year), divisor in _XBRL_UNIT_CORRECTIONS.items():
        mask = (df["corp_code"] == corp_code) & (df["year"] == year)
        if mask.any():
            df.loc[mask, _MONETARY_COLS] = df.loc[mask, _MONETARY_COLS] / divisor
            log.info(
                "XBRL unit correction applied: corp_code=%s year=%d ÷%d",
                corp_code, year, divisor,
            )

    # -----------------------------------------------------------------------
    # 7. Detect potential new XBRL unit-scale anomalies (logs warnings only)
    # -----------------------------------------------------------------------
    _detect_xbrl_anomalies(df)

    # -----------------------------------------------------------------------
    # 8. Detect CFS→OFS year-over-year shifts
    # -----------------------------------------------------------------------
    if len(df) > 0:
        shift_corps = (
            df.groupby("corp_code")["fs_type"]
            .transform(lambda s: s.nunique() > 1)
            .astype(bool)
        )
        df["fs_type_shift"] = shift_corps
        n_shifted_corps = df[df["fs_type_shift"]]["corp_code"].nunique()
        if n_shifted_corps > 0:
            log.info(
                "fs_type_shift: %d rows across %d companies have mixed CFS/OFS across years",
                int(df["fs_type_shift"].sum()), n_shifted_corps,
            )
    else:
        df["fs_type_shift"] = pd.Series(dtype=bool)

    # -----------------------------------------------------------------------
    # 9. Column order and types
    # -----------------------------------------------------------------------
    df["extraction_date"] = date.today().isoformat()

    column_order = [
        "corp_code", "ticker", "company_name", "market", "year", "extraction_date",
        "fs_type", "fs_type_shift", "dart_api_source", "expense_method",
        "receivables", "revenue", "cogs", "sga", "ppe",
        "depreciation", "total_assets", "lt_debt", "net_income", "cfo",
        "wics_sector_code", "wics_sector", "ksic_code", "krx_sector",
    ]
    # Add any extra columns at the end (don't drop them)
    extra = [c for c in df.columns if c not in column_order]
    df = df[column_order + extra]

    # Ensure numeric columns are float64
    float_cols = [
        "receivables", "revenue", "cogs", "sga", "ppe",
        "depreciation", "total_assets", "lt_debt", "net_income", "cfo",
    ]
    for col in float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    df["year"] = df["year"].astype(int)
    df = df.reset_index(drop=True)

    # When --sample is active, merge new rows into existing output to prevent
    # data loss (KI-026). New rows take precedence on (corp_code, year) conflicts.
    # Full runs (sample=None) always rebuild from scratch — correct by design.
    if sample is not None:
        existing_path = PROCESSED / "company_financials.parquet"
        if existing_path.exists():
            existing = pd.read_parquet(existing_path)
            df = (
                pd.concat([existing, df], ignore_index=True)
                .drop_duplicates(subset=["corp_code", "year"], keep="last")
                .reset_index(drop=True)
            )
            log.info("--sample merge: preserved %d existing rows", len(df))

    out = _write_parquet(df, "company_financials")
    _upload_to_r2(out, "processed/company_financials.parquet")
    return df


def _empty_company_financials() -> pd.DataFrame:
    """Return an empty DataFrame with the correct 34-column schema."""
    import numpy as np
    df = pd.DataFrame(columns=[
        "corp_code", "ticker", "company_name", "market", "year", "extraction_date",
        "fs_type", "fs_type_shift", "dart_api_source", "expense_method",
        "receivables", "revenue", "cogs", "sga", "ppe",
        "depreciation", "total_assets", "lt_debt", "net_income", "cfo",
        "wics_sector_code", "wics_sector", "ksic_code", "krx_sector",
        "match_method_receivables", "match_method_revenue",
        "match_method_cogs", "match_method_sga", "match_method_ppe",
        "match_method_depreciation", "match_method_total_assets",
        "match_method_lt_debt", "match_method_net_income", "match_method_cfo",
    ])
    for col in ["receivables", "revenue", "cogs", "sga", "ppe",
                "depreciation", "total_assets", "lt_debt", "net_income", "cfo"]:
        df[col] = df[col].astype(np.float64)
    return df


def _write_parquet(df: pd.DataFrame, name: str) -> Path:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out = PROCESSED / f"{name}.parquet"
    df.to_parquet(out, index=False, engine="pyarrow")
    log.info("Wrote %s (%d rows, %d cols)", out, len(df), len(df.columns))
    return out


def _r2_fs():
    """Return s3fs filesystem pointed at R2, or None if credentials are absent."""
    endpoint = os.getenv("R2_ENDPOINT_URL")
    key      = os.getenv("R2_ACCESS_KEY_ID")
    secret   = os.getenv("R2_SECRET_ACCESS_KEY")
    if not all([endpoint, key, secret]):
        return None
    import s3fs
    return s3fs.S3FileSystem(
        key=key,
        secret=secret,
        client_kwargs={"endpoint_url": endpoint},
    )


def _upload_to_r2(local_path: Path, r2_key: str) -> None:
    """Upload a local file to R2. No-op if R2 credentials are not configured."""
    fs = _r2_fs()
    if fs is None:
        return
    bucket = os.getenv("R2_BUCKET", "kr-forensic-finance")
    dest = f"{bucket}/{r2_key}"
    fs.put(str(local_path), dest)
    log.info("Uploaded to R2: s3://%s", dest)


def run(start_year: int = 2019, end_year: int = 2023, sample: int | None = None, force: bool = False) -> None:
    """Run Phase 1 transforms."""
    if force:
        out = PROCESSED / "company_financials.parquet"
        if out.exists():
            out.unlink()
            log.info("--force: deleted %s", out)
    log.info("Transform: building company_financials.parquet (%d–%d)", start_year, end_year)
    df = build_company_financials(start_year=start_year, end_year=end_year, sample=sample)
    log.info("Transform complete. %d rows in company_financials.parquet", len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transform raw DART data to Parquet")
    parser.add_argument("--start", type=int, default=2019, help="First year (default: 2019)")
    parser.add_argument("--end", type=int, default=2023, help="Last year (default: 2023)")
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help="Limit to first N companies (must match --sample used in extract stage)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and rebuild output even if it exists",
    )
    args = parser.parse_args()
    run(start_year=args.start, end_year=args.end, sample=args.sample, force=args.force)
