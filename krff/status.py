"""src/status.py -- pipeline artifact inventory.

Usage:
    from krff.status import get_status, format_status
    print(format_status(get_status()))
    print(format_status(get_status(), verbose=True))
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from krff._paths import PROJECT_ROOT as _PROJECT_ROOT, PROCESSED_DIR as _PROCESSED

_RUN_SUMMARY = _PROJECT_ROOT / "01_Data" / "raw" / "run_summary.json"

ARTIFACTS: list[tuple[str, str]] = [
    ("company_financials.parquet", "DART financials (XBRL)"),
    ("beneish_scores.parquet", "Beneish M-Scores"),
    ("cb_bw_events.parquet", "CB/BW issuance events"),
    ("price_volume.parquet", "KRX OHLCV price/volume"),
    ("corp_ticker_map.parquet", "Corp code <-> ticker mapping"),
    ("officer_holdings.parquet", "Officer shareholding changes"),
    ("disclosures.parquet", "Material disclosures"),
    ("major_holders.parquet", "5%+ ownership filings"),
    ("bondholder_register.parquet", "CB bondholder names"),
    ("revenue_schedule.parquet", "Revenue by customer/segment"),
    ("depreciation_schedule.parquet", "Depreciation schedules"),
]


def _human_size(size_bytes: int) -> str:
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    return f"{size_bytes / 1024:.0f} KB"


def get_status(
    processed_dir: Path | None = None,
    run_summary_path: Path | None = None,
) -> dict:
    """Inspect pipeline artifacts and return structured status dict."""
    import pyarrow.parquet as pq

    proc = processed_dir or _PROCESSED
    rs_path = run_summary_path or _RUN_SUMMARY

    artifacts = []
    present = 0
    for filename, description in ARTIFACTS:
        path = proc / filename
        if path.exists():
            present += 1
            meta = pq.read_metadata(path)
            stat = path.stat()
            artifacts.append({
                "name": filename,
                "description": description,
                "exists": True,
                "rows": meta.num_rows,
                "cols": meta.num_columns,
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M"),
            })
        else:
            artifacts.append({
                "name": filename,
                "description": description,
                "exists": False,
                "rows": None,
                "cols": None,
                "size_bytes": None,
                "modified": None,
            })

    run_summary = None
    if rs_path.exists():
        with open(rs_path, encoding="utf-8") as f:
            data = json.load(f)
        rs_stat = rs_path.stat()
        run_summary = {
            "total_companies": data.get("total_companies", 0),
            "full_data": len(data.get("full_data", [])),
            "partial_data": len(data.get("partial_data", [])),
            "no_data": len(data.get("no_data", [])),
            "errors": len(data.get("errors", [])),
            "completed_at": data.get("completed_at"),
            "last_modified": datetime.fromtimestamp(
                rs_stat.st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M"),
        }

    return {
        "artifacts": artifacts,
        "summary": {"present": present, "total": len(ARTIFACTS)},
        "run_summary": run_summary,
    }


def format_status(status: dict, verbose: bool = False) -> str:
    """Format status dict into a human-readable table."""
    lines: list[str] = []
    lines.append("Pipeline Data Status")
    lines.append("")

    header = f"{'Artifact':<38} {'Rows':>10} {'Cols':>6} {'Size':>9} {'Modified':<16}"
    lines.append(header)

    for a in status["artifacts"]:
        if a["exists"]:
            rows = f"{a['rows']:>10,}"
            cols = f"{a['cols']:>6}"
            size = f"{_human_size(a['size_bytes']):>9}"
            mod = a["modified"]
        else:
            rows = f"{'---':>10}"
            cols = f"{'---':>6}"
            size = f"{'---':>9}"
            mod = "---"
        lines.append(f"{a['name']:<38} {rows} {cols} {size} {mod}")

    s = status["summary"]
    lines.append("")
    lines.append(f"{s['present']}/{s['total']} artifacts present")

    if verbose and status.get("run_summary"):
        rs = status["run_summary"]
        lines.append("")
        lines.append("DART Run Summary (run_summary.json)")
        lines.append(f"  Last modified: {rs['last_modified']}")
        lines.append(
            f"  Companies: {rs['total_companies']:,} total"
            f" | {rs['full_data']:,} full"
            f" | {rs['partial_data']:,} partial"
            f" | {rs['no_data']:,} no data"
            f" | {rs['errors']:,} errors"
        )

    return "\n".join(lines)


__all__ = ["get_status", "format_status", "ARTIFACTS"]
