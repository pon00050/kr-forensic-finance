"""src/quality.py -- data quality report for all pipeline artifacts.

Usage:
    from src.quality import get_quality, format_quality
    print(format_quality(get_quality()))
    print(format_quality(get_quality(), verbose=True))
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src._paths import PROJECT_ROOT as _PROJECT_ROOT, PROCESSED_DIR as _PROCESSED

_STAT_OUTPUTS = _PROJECT_ROOT / "03_Analysis" / "statistical_tests" / "outputs"

# Per-table known structural issues (column name → issue description).
# These are checked dynamically against actual null rates at runtime.
_NULL_ISSUE_COLS: dict[str, list[tuple[str, str]]] = {
    "company_financials.parquet": [
        ("krx_sector", "krx_sector 100% null"),
        ("depreciation", "depreciation null"),
    ],
    "beneish_scores.parquet": [
        ("depi", "depi null"),
        ("lvgi", "lvgi null"),
    ],
    "corp_ticker_map.parquet": [
        ("effective_from", "effective_from 100% null (design)"),
    ],
}

# Stat test output files: (filename, "MISSING: reason") or (filename, None)
_STAT_OUTPUT_FILES: list[tuple[str, str | None]] = [
    ("peer_clusters.csv", None),
    ("cluster_silhouette.csv", None),
    ("pca_pc3_scores.csv", None),
    ("pca_beneish_loadings.csv", None),
    ("pca_beneish_variance.csv", None),
    ("bootstrap_centrality.csv", None),
    ("bootstrap_threshold.csv", None),
    ("lasso_coefficients.csv", None),
    ("rf_importance.csv", None),
    ("fdr_timing_anomalies.csv", None),
    ("fdr_disclosure_leakage.csv", None),
    ("double_flagged_companies.csv", None),
    ("permutation_repricing.csv", "blocked: SEIBRO pending"),
    ("survival_cox_summary.csv", "blocked: SEIBRO pending"),
]


def get_quality(
    processed_dir: Path | None = None,
    stat_outputs_dir: Path | None = None,
) -> dict:
    """Inspect all pipeline parquets and return structured quality dict."""
    import numpy as np
    import pandas as pd

    proc = processed_dir or _PROCESSED
    stat_out = stat_outputs_dir or _STAT_OUTPUTS

    # --- Tables ---
    tables = []
    for path in sorted(proc.glob("*.parquet")):
        name = path.name
        df = pd.read_parquet(path)
        rows, cols_ = df.shape
        null_count = int(df.isnull().sum().sum())
        null_pct = null_count / max(rows * cols_, 1) * 100
        # inf count (float columns only)
        float_cols = df.select_dtypes(include="float")
        inf_count = int(np.isinf(float_cols.values).sum()) if not float_cols.empty else 0

        # Build issues string from known patterns
        issues_parts: list[str] = []
        for col, label in _NULL_ISSUE_COLS.get(name, []):
            if col in df.columns:
                col_null_pct = df[col].isnull().mean() * 100
                if col_null_pct > 5:
                    issues_parts.append(f"{label} ({col_null_pct:.0f}%)")
        if inf_count > 0:
            issues_parts.append(f"{inf_count} infs")
        issues = "; ".join(issues_parts)

        # Per-column null breakdown (for verbose mode)
        col_nulls = {}
        for c in df.columns:
            n = int(df[c].isnull().sum())
            if n > 0:
                col_nulls[c] = (n, n / rows * 100)

        tables.append({
            "name": name,
            "rows": rows,
            "cols": cols_,
            "null_count": null_count,
            "null_pct": null_pct,
            "inf_count": inf_count,
            "issues": issues,
            "col_nulls": col_nulls,
            "modified": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d"),
        })

    # --- Coverage (reuse DFs from table scan where possible) ---
    table_cache: dict[str, pd.DataFrame] = {}
    for t in tables:
        table_cache[t["name"]] = pd.read_parquet(proc / t["name"])

    coverage: dict[str, str] = {}
    cbe_n: int | None = None
    try:
        bim = table_cache.get("bond_isin_map.parquet")
        cbe = table_cache.get("cb_bw_events.parquet")
        if bim is not None and cbe is not None:
            bim_n = bim["corp_code"].nunique()
            cbe_n = cbe["corp_code"].nunique()
            coverage["isin"] = f"{bim_n:,} / {cbe_n:,} CB/BW corps ({bim_n / max(cbe_n, 1) * 100:.1f}%)"
        else:
            coverage["isin"] = "unavailable"
    except Exception:
        coverage["isin"] = "unavailable"

    try:
        disc = table_cache.get("disclosures.parquet")
        if disc is not None:
            disc_n = disc["corp_code"].nunique()
            if cbe_n is None:
                cbe2 = table_cache.get("cb_bw_events.parquet")
                cbe_n = cbe2["corp_code"].nunique() if cbe2 is not None else 0
            coverage["disclosures"] = f"{disc_n:,} / {cbe_n:,} CB/BW corps ({disc_n / max(cbe_n, 1) * 100:.1f}%)"
        else:
            coverage["disclosures"] = "unavailable"
    except Exception:
        coverage["disclosures"] = "unavailable"

    try:
        pv = table_cache.get("price_volume.parquet")
        ctm = table_cache.get("corp_ticker_map.parquet")
        if pv is not None and ctm is not None:
            pv_tickers = set(pv["ticker"].astype(str).unique())
            ctm_tickers = set(ctm["ticker"].dropna().astype(str).unique())
            pv_n = len(pv_tickers & ctm_tickers)
            ctm_n = len(ctm_tickers)
            coverage["price"] = f"{pv_n:,} / {ctm_n:,} mapped tickers ({pv_n / max(ctm_n, 1) * 100:.1f}%)"
        else:
            coverage["price"] = "unavailable"
    except Exception:
        coverage["price"] = "unavailable"

    # --- Stat test outputs ---
    stat_outputs = []
    missing_count = 0
    blocked_count = 0
    for filename, blocked_reason in _STAT_OUTPUT_FILES:
        path = stat_out / filename
        if path.exists():
            try:
                if filename.endswith(".parquet"):
                    import pyarrow.parquet as pq
                    meta = pq.read_metadata(path)
                    rows_out = meta.num_rows
                else:
                    import csv
                    with open(path, encoding="utf-8") as f:
                        rows_out = sum(1 for _ in f) - 1  # subtract header
            except Exception:
                rows_out = None
            stat_outputs.append({
                "name": filename,
                "exists": True,
                "rows": rows_out,
                "modified": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                "blocked_reason": None,
            })
        else:
            if blocked_reason:
                blocked_count += 1
            else:
                missing_count += 1
            stat_outputs.append({
                "name": filename,
                "exists": False,
                "rows": None,
                "modified": None,
                "blocked_reason": blocked_reason,
            })

    tables_with_issues = sum(1 for t in tables if t["issues"])

    return {
        "tables": tables,
        "coverage": coverage,
        "stat_outputs": stat_outputs,
        "summary": {
            "tables_with_issues": tables_with_issues,
            "missing_outputs": missing_count,
            "blocked_outputs": blocked_count,
        },
    }


def format_quality(quality: dict, verbose: bool = False) -> str:
    """Format quality dict into a human-readable report."""
    lines: list[str] = []
    lines.append("Data Quality Report")
    lines.append("")

    # --- Tables section ---
    hdr = f"{'Table':<42} {'Rows':>8}  {'Null%':>6}  {'Inf':>5}  Issues"
    lines.append(hdr)
    lines.append("-" * 90)
    for t in quality["tables"]:
        null_str = f"{t['null_pct']:5.1f}%"
        inf_str = f"{t['inf_count']:5d}"
        row_str = f"{t['rows']:>8,}"
        issues = t["issues"] or "OK"
        lines.append(f"{t['name']:<42} {row_str}  {null_str}  {inf_str}  {issues}")
        if verbose and t["col_nulls"]:
            for col, (n, pct) in t["col_nulls"].items():
                lines.append(f"    {col}: {n:,} null ({pct:.1f}%)")
    lines.append("")

    # --- Coverage section ---
    lines.append("Coverage")
    cov = quality["coverage"]
    lines.append(f"  ISIN:         {cov.get('isin', 'unavailable')}")
    lines.append(f"  Disclosures:  {cov.get('disclosures', 'unavailable')}")
    lines.append(f"  Price:        {cov.get('price', 'unavailable')}")
    lines.append("")

    # --- Stat test outputs section ---
    lines.append("Stat Test Outputs")
    for s in quality["stat_outputs"]:
        if s["exists"]:
            rows_str = f"{s['rows']:,} rows" if s["rows"] is not None else "? rows"
            lines.append(f"  {s['name']:<38} {rows_str:<12} {s['modified']}")
        elif s["blocked_reason"]:
            lines.append(f"  {s['name']:<38} MISSING ({s['blocked_reason']})")
        else:
            lines.append(f"  {s['name']:<38} MISSING")
    lines.append("")

    # --- Summary ---
    sm = quality["summary"]
    parts = []
    if sm["tables_with_issues"] > 0:
        parts.append(f"{sm['tables_with_issues']} tables with issues")
    if sm["missing_outputs"] > 0:
        parts.append(f"{sm['missing_outputs']} outputs missing")
    if sm["blocked_outputs"] > 0:
        parts.append(f"{sm['blocked_outputs']} outputs blocked (SEIBRO)")
    if not parts:
        lines.append("All checks OK")
    else:
        lines.append("  |  ".join(parts))

    return "\n".join(lines)


__all__ = ["get_quality", "format_quality"]
