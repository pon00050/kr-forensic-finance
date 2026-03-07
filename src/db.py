"""src/db.py — DuckDB connection factory over existing Parquet files.

Tier 1 analytics layer: fresh in-memory DuckDB connection per query.
No persistent .duckdb file — reads parquet in-place.

Usage:
    from src.db import query, read_table
    df = read_table("beneish_scores", corp_code="01051092")
    df = query("SELECT * FROM read_parquet(?) WHERE year >= ?", [path, 2021])
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import duckdb
import pandas as pd

from src._paths import PROCESSED_DIR

log = logging.getLogger(__name__)

# Logical table names → parquet filenames
PARQUET_TABLES: dict[str, str] = {
    "company_financials": "company_financials.parquet",
    "beneish_scores": "beneish_scores.parquet",
    "cb_bw_events": "cb_bw_events.parquet",
    "price_volume": "price_volume.parquet",
    "corp_ticker_map": "corp_ticker_map.parquet",
    "officer_holdings": "officer_holdings.parquet",
    "disclosures": "disclosures.parquet",
    "major_holders": "major_holders.parquet",
    "bondholder_register": "bondholder_register.parquet",
    "revenue_schedule": "revenue_schedule.parquet",
    "bond_isin_map": "bond_isin_map.parquet",
}

# Whitelist of column names allowed in ORDER BY (identifiers can't be parameterized)
_SORT_WHITELIST = {
    "year", "corp_code", "corp_name", "company_name", "ticker",
    "m_score", "flag_count", "event_date", "rcept_dt", "report_nm",
    "date", "close", "volume", "centrality", "severity",
}


def parquet_path(name: str, processed_dir: Path | None = None) -> Path:
    """Resolve a logical table name or filename to an absolute path."""
    proc = processed_dir or PROCESSED_DIR
    # Strip .parquet suffix if provided
    base = name.removesuffix(".parquet")
    filename = PARQUET_TABLES.get(base, f"{base}.parquet")
    return proc / filename


def get_connection() -> duckdb.DuckDBPyConnection:
    """Create a fresh in-memory DuckDB connection. NOT thread-safe — one per request."""
    return duckdb.connect()


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Execute SQL and return a DataFrame. Creates + closes connection per call.

    Uses ? positional params for all values including file paths in read_parquet().
    Returns empty DataFrame on error or missing file.
    """
    con = get_connection()
    try:
        if params:
            result = con.execute(sql, params)
        else:
            result = con.execute(sql)
        return result.fetchdf()
    except duckdb.IOException:
        # File not found — expected for missing parquets
        return pd.DataFrame()
    except Exception as exc:
        log.warning("DuckDB query error: %s", exc)
        return pd.DataFrame()
    finally:
        con.close()


async def async_query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Async wrapper for query() — runs in executor for FastAPI compatibility."""
    return await asyncio.get_event_loop().run_in_executor(None, query, sql, params)


def read_table(
    name: str,
    corp_code: str | None = None,
    sort_by: str | None = None,
    columns: list[str] | None = None,
    processed_dir: Path | None = None,
) -> pd.DataFrame:
    """High-level table read with optional filtering, sorting, and column selection.

    Args:
        name: Logical table name ("beneish_scores") or filename ("beneish_scores.parquet")
        corp_code: Filter to this DART 8-digit corp_code
        sort_by: Column to ORDER BY (validated against whitelist)
        columns: List of columns to SELECT (default: all)
        processed_dir: Override processed directory
    """
    path = parquet_path(name, processed_dir)
    if not path.exists():
        return pd.DataFrame()

    path_str = str(path).replace("\\", "/")

    # Build SELECT clause
    select_clause = ", ".join(columns) if columns else "*"

    # Build WHERE clause
    where_parts: list[str] = []
    params: list = [path_str]

    if corp_code is not None:
        # Check if table has corp_code column before filtering
        schema_sql = "SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet(?))"
        schema_df = query(schema_sql, [path_str])
        if schema_df.empty or "corp_code" not in schema_df["column_name"].values:
            return pd.DataFrame()
        where_parts.append("LPAD(CAST(corp_code AS VARCHAR), 8, '0') = ?")
        params.append(corp_code)

    # Build ORDER BY clause (validated against whitelist)
    order_clause = ""
    if sort_by and sort_by in _SORT_WHITELIST:
        order_clause = f" ORDER BY {sort_by}"

    # Assemble SQL
    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    sql = f"SELECT {select_clause} FROM read_parquet(?){where_sql}{order_clause}"

    return query(sql, params)


__all__ = [
    "PARQUET_TABLES",
    "parquet_path",
    "get_connection",
    "query",
    "async_query",
    "read_table",
]
