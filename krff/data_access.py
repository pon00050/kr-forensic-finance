"""src/data_access.py — Reusable data loading functions.

Extracted from src/report.py so that future FastAPI endpoints, CLI commands,
and analysis scripts can load data without depending on the report module.

Usage:
    from krff.data_access import load_parquet, load_csv, load_company_name
    df = load_parquet("beneish_scores.parquet", corp_code="01051092")
    df_all = load_parquet("beneish_scores.parquet")  # all rows
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from krff._paths import PROJECT_ROOT, PROCESSED_DIR
from krff.db import read_table, query, parquet_path, to_duckdb_path

log = logging.getLogger(__name__)

# ─── Path constants ────────────────────────────────────────────────────────────
ANALYSIS_DIR = PROJECT_ROOT / "03_Analysis"
CB_BW_CSV = ANALYSIS_DIR / "cb_bw_summary.csv"
TIMING_CSV = ANALYSIS_DIR / "timing_anomalies.csv"
NETWORK_CSV = ANALYSIS_DIR / "officer_network" / "centrality_report.csv"

# JFIA catalog — sourced from sibling jfia-forensic project (gitignored raw data)
# Override JFIA_CATALOG_PATH env var to point to the file in non-standard layouts
_DEFAULT_JFIA_PATH = PROJECT_ROOT.parent / "jfia-forensic" / "data" / "raw" / "jfia_catalog.json"
_JFIA_CATALOG_PATH: Path = Path(os.environ.get("JFIA_CATALOG_PATH", str(_DEFAULT_JFIA_PATH)))


def load_parquet(
    name: str,
    corp_code: str | None = None,
    sort_by: str | None = None,
    processed_dir: Path | None = None,
) -> pd.DataFrame:
    """Load a parquet table from processed dir, optionally filtered to one corp_code."""
    return read_table(
        name, corp_code=corp_code, sort_by=sort_by, processed_dir=processed_dir,
    )


def load_csv(path: Path, corp_code: str | None = None) -> pd.DataFrame:
    """Load a CSV analysis output, optionally filtered to one corp_code."""
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
        if corp_code is not None:
            if "corp_code" not in df.columns:
                return pd.DataFrame()
            mask = df["corp_code"].astype(str).str.zfill(8) == corp_code
            df = df[mask]
        return df.reset_index(drop=True)
    except FileNotFoundError:
        return pd.DataFrame()
    except Exception as exc:
        log.warning("Error loading %s for %s: %s", path.name, corp_code, exc)
        return pd.DataFrame()


def load_company_name(
    corp_code: str,
    beneish_df: pd.DataFrame | None = None,
    processed_dir: Path | None = None,
) -> str:
    """Extract company name from pre-loaded beneish data or corp_ticker_map."""
    if beneish_df is not None and not beneish_df.empty and "company_name" in beneish_df.columns:
        val = beneish_df["company_name"].iloc[0]
        if pd.notna(val) and str(val).strip():
            return str(val).strip()
    proc = processed_dir or PROCESSED_DIR
    path = parquet_path("corp_ticker_map", proc)
    if path.exists():
        path_str = to_duckdb_path(path)
        for col in ("company_name", "corp_name", "name"):
            sql = (
                f"SELECT {col} FROM read_parquet(?) "
                "WHERE LPAD(CAST(corp_code AS VARCHAR), 8, '0') = ? "
                "LIMIT 1"
            )
            df2 = query(sql, [path_str, corp_code])
            if not df2.empty:
                val = df2.iloc[0, 0]
                if pd.notna(val) and str(val).strip():
                    return str(val).strip()
    return corp_code


def load_jfia_catalog(catalog_path: Path | None = None) -> "JFIACatalog | None":
    """
    Load the JFIA article catalog JSON.

    Returns a JFIACatalog instance, or None if the file is absent.
    Uses _JFIA_CATALOG_PATH by default (sibling jfia-forensic project).
    """
    path = catalog_path or _JFIA_CATALOG_PATH
    if not path.exists():
        log.debug("JFIA catalog not found at %s", path)
        return None
    try:
        # Lazy import — jfia-forensic is an optional sibling project
        from jfia_forensic.catalog import JFIACatalog
        return JFIACatalog.load(path)
    except ImportError:
        log.warning("jfia-forensic not installed — install it with: pip install jfia-forensic")
        return None
    except Exception as exc:
        log.warning("Error loading JFIA catalog: %s", exc)
        return None


# Lazy singleton — loaded once per process
_JFIA_CATALOG_SINGLETON: "JFIACatalog | None | _SENTINEL" = None


class _SENTINEL:
    """Sentinel to distinguish 'not loaded yet' from 'loaded but None'."""


_jfia_loaded = False


def get_jfia_catalog() -> "JFIACatalog | None":
    """Return a cached JFIACatalog singleton (lazy-loaded on first call)."""
    global _jfia_loaded, _JFIA_CATALOG_SINGLETON
    if not _jfia_loaded:
        _JFIA_CATALOG_SINGLETON = load_jfia_catalog()
        _jfia_loaded = True
    return _JFIA_CATALOG_SINGLETON


def load_officer_network(
    corp_code: str,
    network_csv: Path | None = None,
) -> pd.DataFrame:
    """Load officer network CSV with token-match filtering on 'companies' column."""
    csv_path = network_csv or NETWORK_CSV
    if not csv_path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
        if "companies" not in df.columns:
            return pd.DataFrame()
        mask = df["companies"].apply(
            lambda val: corp_code in [c.strip() for c in str(val).split(",")]
            if pd.notna(val) else False
        )
        return df[mask].reset_index(drop=True)
    except FileNotFoundError:
        return pd.DataFrame()
    except Exception as exc:
        log.warning("Error loading officer_network for %s: %s", corp_code, exc)
        return pd.DataFrame()


__all__ = [
    "load_parquet",
    "load_csv",
    "load_company_name",
    "load_officer_network",
    "load_jfia_catalog",
    "get_jfia_catalog",
    "ANALYSIS_DIR",
    "CB_BW_CSV",
    "TIMING_CSV",
    "NETWORK_CSV",
    "_JFIA_CATALOG_PATH",
]
