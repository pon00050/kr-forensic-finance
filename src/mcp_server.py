"""src/mcp_server.py — FastMCP server for Korean financial statements data.

Exposes 10 tools backed by the parquet files and CSV outputs in 01_Data/processed/
and 03_Analysis/. Mounts at /mcp/ inside the existing FastAPI app.

Tools:
    lookup_corp_code          — name/ticker → corp_code (always use this first)
    get_company_summary       — all signals for one company in one call
    get_beneish_scores        — Beneish M-Score history with 8 components
    get_cb_bw_events          — convertible bond / bond warrant issuance events
    get_price_volume          — OHLCV price and volume data
    get_officer_holdings      — officer shareholding changes from DART
    get_timing_anomalies      — disclosure timing vs. price/volume impact
    get_major_holders         — 5%+ block-holding filings (DART 대량보유)
    get_officer_network       — cross-company officer network centrality
    search_flagged_companies  — ranked list of companies by Beneish M-Score

Usage:
    From app.py:
        from src.mcp_server import mcp_server
        mcp_app = mcp_server.http_app(path="/")
        app.mount("/mcp", mcp_app)
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import anyio
import pandas as pd
from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from src.data_access import (
    CB_BW_CSV,
    NETWORK_CSV,
    TIMING_CSV,
    load_csv,
    load_officer_network,
    load_parquet,
)
from src.db import parquet_path, query, to_duckdb_path
from src.mcp_utils import df_to_json_str, df_to_records, paginate, sanitize_for_json
from src.report import get_company_summary as _get_company_summary

log = logging.getLogger(__name__)

# ── FastMCP instance ──────────────────────────────────────────────────────────
mcp_server = FastMCP(
    name="kr-financial-statements",
    instructions=(
        "Provides access to Korean listed company financial statements, Beneish M-Score "
        "anomaly screening, convertible bond events, disclosure timing analysis, and "
        "officer network data sourced from DART (Korean FSS), KRX, and SEIBRO. "
        "All companies are KOSPI/KOSDAQ listed. "
        "Always call lookup_corp_code first if you have a company name or ticker — "
        "all other tools require the 8-digit DART corp_code as input."
    ),
    on_duplicate="warn",
    mask_error_details=False,
)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — lookup_corp_code  (ALWAYS CALL THIS FIRST)
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def lookup_corp_code(
    query_str: Annotated[str, Field(
        description=(
            "Company name (Korean or English) or stock ticker symbol. "
            "Partial names are supported (e.g. '피씨엘', 'PCL', 'Samsung', '005930'). "
            "Returns up to `limit` ranked matches."
        )
    )],
    limit: Annotated[int, Field(description="Maximum results to return (1–20).", ge=1, le=20)] = 10,
) -> str:
    """
    Look up DART corp_code by company name or stock ticker.

    This is the entry point for all other tools — they require corp_code,
    not company names or tickers. Call this first, then use the corp_code
    from results as input to get_beneish_scores, get_cb_bw_events, etc.

    Returns a ranked list of matches. Fields per result:
        corp_code    — 8-digit DART identifier (zero-padded string)
        corp_name    — Korean company name
        ticker       — stock ticker code (e.g. "241820"), may be null for unlisted
        market       — "KOSPI" | "KOSDAQ" | "KONEX" | null
        match_type   — "exact_name" | "partial_name" | "ticker"

    Example: query_str='피씨엘' → [{"corp_code": "01051092", "corp_name": "피씨엘", ...}]
    Example: query_str='241820' → [{"corp_code": "01051092", "corp_name": "피씨엘", ...}]
    """
    path = parquet_path("corp_ticker_map")
    if not path.exists():
        raise ToolError("corp_ticker_map.parquet not found. Run the pipeline first.")

    path_str = to_duckdb_path(path)
    q = query_str.strip()

    def _search() -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()

        # 1. Exact ticker match
        sql_ticker = (
            "SELECT LPAD(CAST(corp_code AS VARCHAR), 8, '0') AS corp_code, "
            "corp_name, ticker, market "
            "FROM read_parquet(?) "
            "WHERE CAST(ticker AS VARCHAR) = ? "
            f"LIMIT {limit}"
        )
        df = query(sql_ticker, [path_str, q])
        for r in df_to_records(df):
            key = r["corp_code"]
            if key not in seen:
                r["match_type"] = "ticker"
                results.append(r)
                seen.add(key)

        # 2. Exact name match
        sql_exact = (
            "SELECT LPAD(CAST(corp_code AS VARCHAR), 8, '0') AS corp_code, "
            "corp_name, ticker, market "
            "FROM read_parquet(?) "
            "WHERE corp_name = ? "
            f"LIMIT {limit}"
        )
        df = query(sql_exact, [path_str, q])
        for r in df_to_records(df):
            key = r["corp_code"]
            if key not in seen:
                r["match_type"] = "exact_name"
                results.append(r)
                seen.add(key)

        # 3. Partial name match (if not enough results yet)
        if len(results) < limit:
            sql_partial = (
                "SELECT LPAD(CAST(corp_code AS VARCHAR), 8, '0') AS corp_code, "
                "corp_name, ticker, market "
                "FROM read_parquet(?) "
                "WHERE corp_name LIKE ? "
                f"LIMIT {limit}"
            )
            df = query(sql_partial, [path_str, f"%{q}%"])
            for r in df_to_records(df):
                key = r["corp_code"]
                if key not in seen:
                    r["match_type"] = "partial_name"
                    results.append(r)
                    seen.add(key)

        return results[:limit]

    results = await anyio.to_thread.run_sync(_search)
    return json.dumps(results, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2 — get_company_summary
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def get_company_summary(
    corp_code: Annotated[str, Field(
        description=(
            "8-digit DART corp_code (zero-padded). "
            "Use lookup_corp_code first if you only have a name or ticker."
        )
    )],
) -> str:
    """
    Return a pre-computed anomaly summary for one Korean listed company.

    This is the highest-value single tool — call it first for any company
    investigation. It aggregates all signal types into one structured response.

    Returns a dict with:
        corp_code                        — 8-digit DART identifier
        company_name                     — Korean company name
        ticker                           — stock ticker
        beneish_years                    — list of {year, m_score, risk_tier, flag}
        cb_bw_count                      — total CB/BW issuance events
        cb_bw_flagged_count              — events with flag_count > 0
        cb_bw_max_flags                  — highest flag_count across all events
        cb_bw_flag_types                 — list of flag type strings seen
        timing_anomaly_count             — total disclosure timing events
        timing_flagged_count             — flagged (material + anomalous) events
        officer_network_centrality       — betweenness centrality (null if not in network)
        officer_network_appears_in_multiple — True if officers appear in ≥2 flagged companies

    Beneish M-Score threshold: -1.78 (US academic). Korean bootstrap: -2.45.
    Scores above -1.78 are flagged as potential earnings manipulators.
    """
    corp_code = corp_code.zfill(8)
    summary = await anyio.to_thread.run_sync(_get_company_summary, corp_code)
    return json.dumps(sanitize_for_json(summary), ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3 — get_beneish_scores
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def get_beneish_scores(
    corp_code: Annotated[str, Field(
        description="8-digit DART corp_code. Use lookup_corp_code first if needed."
    )],
    years: Annotated[list[int] | None, Field(
        description=(
            "Optional list of fiscal years to return, e.g. [2021, 2022, 2023]. "
            "Returns all available years (2018–2023) if omitted."
        )
    )] = None,
) -> str:
    """
    Return Beneish M-Score history with all 8 component values for one company.

    Each record contains:
        year             — fiscal year (int)
        m_score          — composite Beneish M-Score (float; null if insufficient data)
        risk_tier        — "Critical" | "High" | "Medium" | "Low"
        flag             — True if m_score > -1.78 (US threshold)
        dsri             — Days Sales in Receivables Index (>1 = receivables grew faster than revenue)
        gmi              — Gross Margin Index (>1 = margins declined)
        aqi              — Asset Quality Index (>1 = non-current non-PP&E assets grew)
        sgi              — Sales Growth Index (>1 = revenue grew)
        depi             — Depreciation Index (>1 = lower depreciation rate)
        sgai             — SG&A Index (>1 = SG&A grew faster than revenue)
        lvgi             — Leverage Growth Index (>1 = more leverage)
        tata             — Total Accruals to Total Assets (positive = accrual-heavy earnings)

    Components may be null for years with insufficient prior-year data or XBRL gaps.
    Data covers 2018–2023. KOSDAQ companies only (7,447 company-years total).
    """
    corp_code = corp_code.zfill(8)

    def _load() -> pd.DataFrame:
        df = load_parquet("beneish_scores.parquet", corp_code=corp_code, sort_by="year")
        if years and not df.empty:
            df = df[df["year"].isin(years)]
        return df

    df = await anyio.to_thread.run_sync(_load)
    if df.empty:
        return json.dumps([], ensure_ascii=False)

    cols = ["year", "m_score", "risk_tier", "flag", "dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata"]
    available = [c for c in cols if c in df.columns]
    return df_to_json_str(df[available])


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4 — get_cb_bw_events
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def get_cb_bw_events(
    corp_code: Annotated[str, Field(
        description="8-digit DART corp_code. Use lookup_corp_code first if needed."
    )],
) -> str:
    """
    Return convertible bond (CB) and bond warrant (BW) issuance events for one company.

    CB/BW instruments are frequently abused in KOSDAQ manipulation schemes:
    repricing clauses allow the conversion price to fall after issuance,
    diluting existing shareholders. flag_count indicates how many of 4
    manipulation indicators triggered for each event.

    Each record contains:
        issue_date       — bond issuance date (YYYY-MM-DD)
        bond_type        — "CB" (전환사채) or "BW" (신주인수권부사채)
        exercise_price   — initial conversion/exercise price in KRW
        flag_count       — number of manipulation flags triggered (0–4)
        flags            — pipe-separated flag names, e.g. "volume_surge|holdings_decrease"
        peak_date        — date of price peak before issuance (null if none detected)
        peak_before_issue — True if stock price peaked in 60 days before issuance
        volume_ratio     — trading volume ratio at time of event vs. 60-day average
        dart_link        — DART filing URL for this event

    Returns empty list if no CB/BW events exist for this company.
    Source: 03_Analysis/cb_bw_summary.csv (3,667 events; 756 flagged across all companies).
    """
    if not CB_BW_CSV.exists():
        raise ToolError("cb_bw_summary.csv not found. Run: python 03_Analysis/run_cb_bw_timelines.py")

    corp_code = corp_code.zfill(8)
    df = await anyio.to_thread.run_sync(load_csv, CB_BW_CSV, corp_code)

    cols = ["issue_date", "bond_type", "exercise_price", "flag_count", "flags",
            "peak_date", "peak_before_issue", "volume_ratio", "dart_link"]
    available = [c for c in cols if c in df.columns]
    return df_to_json_str(df[available] if available else df)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5 — get_price_volume
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def get_price_volume(
    corp_code: Annotated[str, Field(
        description="8-digit DART corp_code. Use lookup_corp_code first if needed."
    )],
    start_date: Annotated[str, Field(
        description="Start date in YYYY-MM-DD format, e.g. '2021-01-01'."
    )],
    end_date: Annotated[str, Field(
        description="End date in YYYY-MM-DD format, e.g. '2021-12-31'."
    )],
    limit: Annotated[int, Field(description="Max rows to return (1–500).", ge=1, le=500)] = 250,
    offset: Annotated[int, Field(description="Row offset for pagination.", ge=0)] = 0,
) -> str:
    """
    Return OHLCV (open/high/low/close/volume) price data for a Korean listed company.

    Data sourced from KRX via pykrx. Covers ±60 day windows around corporate
    events for companies in the pipeline universe. Not a full continuous price history
    for all companies — coverage depends on which companies had CB/BW events screened.

    Each record contains:
        date     — trading date (YYYY-MM-DD)
        open     — opening price in KRW
        high     — intraday high in KRW
        low      — intraday low in KRW
        close    — closing price in KRW
        volume   — shares traded

    Returns at most `limit` rows (default 250, max 500). Use offset for pagination.
    Use `has_more` and `total_count` in the response envelope to check for more data.
    """
    path = parquet_path("price_volume")
    if not path.exists():
        raise ToolError("price_volume.parquet not found. Run the pipeline first.")

    corp_code = corp_code.zfill(8)
    path_str = to_duckdb_path(path)

    def _load() -> pd.DataFrame:
        sql = (
            "SELECT date, open, high, low, close, volume "
            "FROM read_parquet(?) "
            "WHERE LPAD(CAST(corp_code AS VARCHAR), 8, '0') = ? "
            "  AND CAST(date AS VARCHAR) >= ? "
            "  AND CAST(date AS VARCHAR) <= ? "
            "ORDER BY date"
        )
        return query(sql, [path_str, corp_code, start_date, end_date])

    df = await anyio.to_thread.run_sync(_load)
    records = df_to_records(df)
    envelope = paginate(records, limit=limit, offset=offset)
    return json.dumps(envelope, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6 — get_officer_holdings
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def get_officer_holdings(
    corp_code: Annotated[str, Field(
        description="8-digit DART corp_code. Use lookup_corp_code first if needed."
    )],
) -> str:
    """
    Return officer and major shareholder holding change records from DART.

    Sourced from DART elestock API (임원·주요주주 소유보고). Korean law requires
    disclosure within 5 business days of any transaction by officers or shareholders
    holding ≥10% of outstanding shares.

    Each record contains:
        corp_code    — 8-digit DART identifier
        person_name  — officer/shareholder name (Korean)
        position     — title/role at the company
        shares_held  — number of shares held after the reported transaction
        change_date  — date of the reported transaction or filing

    Returns empty list if no officer holding data exists for this company.
    """
    corp_code = corp_code.zfill(8)
    df = await anyio.to_thread.run_sync(load_parquet, "officer_holdings.parquet", corp_code)

    cols = ["corp_code", "person_name", "position", "shares_held", "change_date"]
    available = [c for c in cols if c in df.columns]
    return df_to_json_str(df[available] if available else df)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 7 — get_timing_anomalies
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def get_timing_anomalies(
    corp_code: Annotated[str, Field(
        description="8-digit DART corp_code. Use lookup_corp_code first if needed."
    )],
    flagged_only: Annotated[bool, Field(
        description=(
            "If True (default), return only material disclosures (is_material=True) "
            "that are also flagged (flag=True). "
            "If False, return all disclosure timing records for this company."
        )
    )] = True,
) -> str:
    """
    Return disclosure timing anomaly records — DART filings correlated with
    same-day or prior-day abnormal price and volume movements.

    A timing anomaly occurs when a material DART disclosure (e.g. CB issuance,
    capital increase, merger) coincides with ≥5% price move and ≥2x volume spike
    on the same or prior trading day — suggesting information may have leaked
    before official disclosure.

    Each record contains:
        filing_date       — date of the DART disclosure (YYYY-MM-DD)
        timing            — "same_day" | "prior_day"
        title             — disclosure title (Korean)
        price_change_pct  — stock price % change on the event day
        volume_ratio      — volume on event day / 60-day average volume
        anomaly_score     — composite score (higher = more anomalous)
        flag              — True if this record meets the anomaly threshold
        is_material       — True if disclosure type is classified as material
        disclosure_type   — DART disclosure category code
        dart_link         — DART filing URL

    Source: 03_Analysis/timing_anomalies.csv (32,741 total; 3,373 flagged material).
    """
    if not TIMING_CSV.exists():
        raise ToolError("timing_anomalies.csv not found. Run: python 03_Analysis/run_timing_anomalies.py")

    corp_code = corp_code.zfill(8)

    def _load() -> pd.DataFrame:
        df = load_csv(TIMING_CSV, corp_code=corp_code)
        if flagged_only and not df.empty:
            has_flag = "flag" in df.columns
            has_material = "is_material" in df.columns
            if has_flag and has_material:
                df = df[df["flag"] & df["is_material"]]
            elif has_flag:
                df = df[df["flag"]]
        return df

    df = await anyio.to_thread.run_sync(_load)
    cols = ["filing_date", "timing", "title", "price_change_pct", "volume_ratio",
            "anomaly_score", "flag", "is_material", "disclosure_type", "dart_link"]
    available = [c for c in cols if c in df.columns]
    return df_to_json_str(df[available] if available else df)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 8 — get_major_holders
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def get_major_holders(
    corp_code: Annotated[str, Field(
        description="8-digit DART corp_code. Use lookup_corp_code first if needed."
    )],
) -> str:
    """
    Return 5%+ block-holding filings (대량보유 보고) from DART for one company.

    Korean law (자본시장법 §147) requires any person or group acquiring ≥5% of
    outstanding shares to file a disclosure within 5 business days. This is
    equivalent to SEC Schedule 13D/G filings in the US.

    Each record contains the DART-reported fields including:
        rcept_dt     — disclosure filing date
        report_tp    — report type (initial filing, amendment, etc.)
        repror       — reporter name (the block holder)
        stkqy        — shares held at filing
        stkrt        — ownership percentage at filing
        stkqy_irds   — change in shares held
        stkrt_irds   — change in ownership percentage
        report_resn  — reason for the holding change

    Returns empty list if no 5%+ filings exist for this company.
    Source: major_holders.parquet (8,514 rows across all companies).
    """
    corp_code = corp_code.zfill(8)
    df = await anyio.to_thread.run_sync(load_parquet, "major_holders.parquet", corp_code)
    return df_to_json_str(df)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 9 — get_officer_network
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def get_officer_network(
    corp_code: Annotated[str, Field(
        description="8-digit DART corp_code. Use lookup_corp_code first if needed."
    )],
) -> str:
    """
    Return officer network centrality records for officers at one company who
    also appear at other flagged companies.

    The officer network is a graph of ~3,043 individuals identified across
    KOSDAQ companies. Only individuals appearing at ≥2 flagged companies are
    tracked (114 such individuals as of the last pipeline run).

    Betweenness centrality measures how often a person sits on the shortest
    path between companies in the network — high centrality officers are
    potential connective nodes across manipulation networks.

    Each record contains:
        person_name             — officer name (Korean)
        company_count           — total companies this person appears at
        flagged_company_count   — companies with flag_count ≥ 1 (threshold: 2)
        companies               — comma-separated corp_codes
        betweenness_centrality  — network centrality score (higher = more central)

    Returns empty list if no officers from this company appear in the cross-company network.
    Source: 03_Analysis/officer_network/centrality_report.csv.
    """
    if not NETWORK_CSV.exists():
        raise ToolError("centrality_report.csv not found. Run: python 03_Analysis/run_officer_network.py")

    corp_code = corp_code.zfill(8)
    df = await anyio.to_thread.run_sync(load_officer_network, corp_code)
    return df_to_json_str(df)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 10 — search_flagged_companies
# ─────────────────────────────────────────────────────────────────────────────

@mcp_server.tool
async def search_flagged_companies(
    min_m_score: Annotated[float, Field(
        description=(
            "Minimum Beneish M-Score threshold. "
            "Companies with m_score ABOVE this value are returned (higher = more anomalous). "
            "Use -1.78 for the US academic threshold. "
            "Use -2.45 for the Korean bootstrap threshold (more conservative). "
            "Default: -1.78."
        )
    )] = -1.78,
    year: Annotated[int | None, Field(
        description="Filter to a specific fiscal year (2018–2023). Returns all years if omitted."
    )] = None,
    limit: Annotated[int, Field(description="Max results (1–100).", ge=1, le=100)] = 20,
    offset: Annotated[int, Field(description="Row offset for pagination.", ge=0)] = 0,
) -> str:
    """
    Return a ranked list of Korean listed companies by Beneish M-Score anomaly severity.

    Results are sorted by m_score descending (highest / most anomalous first).
    Use get_company_summary or get_beneish_scores on individual corp_codes from
    this list to investigate further.

    Each record contains:
        corp_code    — 8-digit DART identifier
        company_name — Korean company name
        ticker       — stock ticker
        m_score      — Beneish M-Score for this company-year
        year         — fiscal year

    Universe: 7,447 KOSDAQ company-years (2018–2023). 1,250 flag above -1.78.
    """
    path = parquet_path("beneish_scores")
    if not path.exists():
        raise ToolError("beneish_scores.parquet not found. Run the pipeline first.")

    path_str = to_duckdb_path(path)

    def _search() -> list[dict]:
        where_parts = ["m_score IS NOT NULL", f"m_score > {min_m_score}"]
        params: list = [path_str]
        if year is not None:
            where_parts.append("year = ?")
            params.append(year)
        where_sql = " AND ".join(where_parts)
        sql = (
            "SELECT LPAD(CAST(corp_code AS VARCHAR), 8, '0') AS corp_code, "
            "company_name, ticker, m_score, year "
            f"FROM read_parquet(?) WHERE {where_sql} "
            "ORDER BY m_score DESC"
        )
        df = query(sql, params)
        return df_to_records(df)

    all_records = await anyio.to_thread.run_sync(_search)
    envelope = paginate(all_records, limit=limit, offset=offset)
    return json.dumps(envelope, ensure_ascii=False)


__all__ = ["mcp_server"]
