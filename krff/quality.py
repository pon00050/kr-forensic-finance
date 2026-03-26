"""src/quality.py -- data quality report for all pipeline artifacts.

Usage:
    from krff.quality import get_quality, format_quality
    print(format_quality(get_quality()))
    print(format_quality(get_quality(), verbose=True))
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

from krff._paths import PROJECT_ROOT as _PROJECT_ROOT, PROCESSED_DIR as _PROCESSED

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
    proc = processed_dir or _PROCESSED
    stat_out = stat_outputs_dir or _STAT_OUTPUTS

    # --- Tables ---
    # Single DuckDB connection for the entire scan — one connection open/close
    # instead of one per table, reducing overhead from O(N tables) to O(1).
    import duckdb
    from krff.db import to_duckdb_path as _dpath, query as _db_query
    tables = []
    con = duckdb.connect()
    try:
        for path in sorted(proc.glob("*.parquet")):
            name = path.name
            path_str = _dpath(path)

            # Per-table try/except so one bad file doesn't abort the whole scan.
            try:
                schema_df = con.execute(
                    "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
                    [path_str],
                ).fetchdf()
                all_cols = list(schema_df["column_name"])
                col_types = dict(zip(schema_df["column_name"], schema_df["column_type"]))
                cols_ = len(all_cols)

                # Row count + null counts via aggregate query
                null_exprs = ", ".join(
                    f"COUNT(*) - COUNT(\"{c}\") AS \"{c}_nulls\"" for c in all_cols
                )
                agg_sql = f"SELECT COUNT(*) AS total_rows, {null_exprs} FROM read_parquet(?)"
                agg_row = con.execute(agg_sql, [path_str]).fetchdf().iloc[0]
                rows = int(agg_row["total_rows"])

                col_nulls = {}
                null_count = 0
                for c in all_cols:
                    n = int(agg_row[f"{c}_nulls"])
                    if n > 0:
                        col_nulls[c] = (n, n / rows * 100)
                    null_count += n
                null_pct = null_count / max(rows * cols_, 1) * 100

                # Inf detection for float/double columns
                float_cols = [
                    c for c, t in col_types.items()
                    if t.upper() in ("FLOAT", "DOUBLE", "REAL")
                ]
                inf_count = 0
                if float_cols:
                    inf_exprs = " + ".join(
                        f"COUNT(*) FILTER (WHERE isinf(\"{c}\"))" for c in float_cols
                    )
                    inf_sql = f"SELECT {inf_exprs} AS inf_total FROM read_parquet(?)"
                    inf_row = con.execute(inf_sql, [path_str]).fetchdf()
                    inf_count = int(inf_row.iloc[0, 0])
            except Exception as exc:
                log.warning("DuckDB quality scan failed for %s: %s", name, exc)
                rows, cols_, null_count, null_pct, inf_count, col_nulls = 0, 0, 0, 0.0, 0, {}

            # Build issues string from known patterns
            issues_parts: list[str] = []
            for col, label in _NULL_ISSUE_COLS.get(name, []):
                if col in col_nulls:
                    col_null_pct = col_nulls[col][1]
                    if col_null_pct > 5:
                        issues_parts.append(f"{label} ({col_null_pct:.0f}%)")
            if inf_count > 0:
                issues_parts.append(f"{inf_count} infs")
            issues = "; ".join(issues_parts)

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
    finally:
        con.close()

    # --- Coverage (DuckDB aggregate queries — no full DataFrame loads) ---
    coverage: dict[str, str] = {}
    cbe_n: int | None = None

    cbe_path = proc / "cb_bw_events.parquet"
    bim_path = proc / "bond_isin_map.parquet"
    try:
        if bim_path.exists() and cbe_path.exists():
            bim_str = _dpath(bim_path)
            cbe_str = _dpath(cbe_path)
            bim_df = _db_query("SELECT COUNT(DISTINCT corp_code) AS n FROM read_parquet(?)", [bim_str])
            cbe_df = _db_query("SELECT COUNT(DISTINCT corp_code) AS n FROM read_parquet(?)", [cbe_str])
            bim_n = int(bim_df.iloc[0, 0])
            cbe_n = int(cbe_df.iloc[0, 0])
            coverage["isin"] = f"{bim_n:,} / {cbe_n:,} CB/BW corps ({bim_n / max(cbe_n, 1) * 100:.1f}%)"
        else:
            coverage["isin"] = "unavailable"
    except Exception:
        coverage["isin"] = "unavailable"

    disc_path = proc / "disclosures.parquet"
    try:
        if disc_path.exists():
            disc_str = _dpath(disc_path)
            disc_df = _db_query("SELECT COUNT(DISTINCT corp_code) AS n FROM read_parquet(?)", [disc_str])
            disc_n = int(disc_df.iloc[0, 0])
            if cbe_n is None and cbe_path.exists():
                cbe_str = _dpath(cbe_path)
                cbe_df = _db_query("SELECT COUNT(DISTINCT corp_code) AS n FROM read_parquet(?)", [cbe_str])
                cbe_n = int(cbe_df.iloc[0, 0])
            cbe_n = cbe_n or 0
            coverage["disclosures"] = f"{disc_n:,} / {cbe_n:,} CB/BW corps ({disc_n / max(cbe_n, 1) * 100:.1f}%)"
        else:
            coverage["disclosures"] = "unavailable"
    except Exception:
        coverage["disclosures"] = "unavailable"

    pv_path = proc / "price_volume.parquet"
    ctm_path = proc / "corp_ticker_map.parquet"
    try:
        if pv_path.exists() and ctm_path.exists():
            pv_str = _dpath(pv_path)
            ctm_str = _dpath(ctm_path)
            # Use DuckDB to compute ticker intersection count
            intersect_sql = (
                "SELECT COUNT(*) AS n FROM ("
                "  SELECT DISTINCT CAST(ticker AS VARCHAR) AS t FROM read_parquet(?) "
                "  INTERSECT "
                "  SELECT DISTINCT CAST(ticker AS VARCHAR) AS t FROM read_parquet(?) WHERE ticker IS NOT NULL"
                ")"
            )
            pv_n_df = _db_query(intersect_sql, [pv_str, ctm_str])
            ctm_n_df = _db_query(
                "SELECT COUNT(DISTINCT CAST(ticker AS VARCHAR)) AS n FROM read_parquet(?) WHERE ticker IS NOT NULL",
                [ctm_str],
            )
            pv_n = int(pv_n_df.iloc[0, 0])
            ctm_n = int(ctm_n_df.iloc[0, 0])
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
