"""src/audit.py — Pipeline freshness checker.

Encodes the directed dependency graph (DAG) of every pipeline stage,
compares output vs input file modification times, propagates staleness
downstream, and returns an ordered list of rerun commands.

Usage:
    from krff.audit import get_audit, format_audit
    result = get_audit()
    print(format_audit(result))
    print(format_audit(result, verbose=True))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from krff._paths import PROJECT_ROOT as _PROJECT_ROOT


@dataclass
class StageNode:
    stage: str
    output: str          # relative to project root
    inputs: list[str]    # relative to project root
    rerun_cmd: str


# Directed dependency graph — order matters (topological).
DAG: list[StageNode] = [
    StageNode(
        stage="transform",
        output="01_Data/processed/company_financials.parquet",
        inputs=["01_Data/raw/run_summary.json"],
        rerun_cmd="python 02_Pipeline/pipeline.py --stage transform",
    ),
    StageNode(
        stage="beneish",
        output="01_Data/processed/beneish_scores.parquet",
        inputs=["01_Data/processed/company_financials.parquet"],
        rerun_cmd="python 03_Analysis/beneish_screen.py",
    ),
    StageNode(
        stage="beneish-csv",
        output="03_Analysis/beneish_scores.csv",
        inputs=["01_Data/processed/company_financials.parquet"],
        rerun_cmd="python 03_Analysis/beneish_screen.py",
    ),
    StageNode(
        stage="cb_bw",
        output="03_Analysis/cb_bw_summary.csv",
        inputs=[
            "01_Data/processed/cb_bw_events.parquet",
            "01_Data/processed/price_volume.parquet",
            "01_Data/processed/officer_holdings.parquet",
        ],
        rerun_cmd="python 03_Analysis/run_cb_bw_timelines.py",
    ),
    StageNode(
        stage="timing",
        output="03_Analysis/timing_anomalies.csv",
        inputs=[
            "01_Data/processed/disclosures.parquet",
            "01_Data/processed/price_volume.parquet",
        ],
        rerun_cmd="python 03_Analysis/run_timing_anomalies.py",
    ),
    StageNode(
        stage="network",
        output="03_Analysis/officer_network/centrality_report.csv",
        inputs=[
            "01_Data/processed/officer_holdings.parquet",
            "03_Analysis/beneish_scores.csv",
            "03_Analysis/cb_bw_summary.csv",
            "03_Analysis/timing_anomalies.csv",
        ],
        rerun_cmd="python 03_Analysis/run_officer_network.py",
    ),
]


def _mtime_str(path: Path) -> Optional[str]:
    """Return 'YYYY-MM-DD HH:MM' or None if file absent."""
    if not path.exists():
        return None
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def is_stale(output: Path, inputs: list[Path]) -> tuple[bool, Optional[Path]]:
    """Return (stale, newest_input).

    Stale if output does not exist OR output mtime < any existing input mtime.
    """
    if not output.exists():
        return True, None
    out_mtime = output.stat().st_mtime
    existing_inputs = [p for p in inputs if p.exists()]
    if not existing_inputs:
        return False, None  # no inputs present — cannot determine staleness
    newest = max(existing_inputs, key=lambda p: p.stat().st_mtime)
    if newest.stat().st_mtime > out_mtime:
        return True, newest
    return False, newest


def get_audit(project_root: Optional[Path] = None) -> dict:
    """Compute per-stage freshness and return structured audit result.

    Returns:
        {
            "stages": [
                {
                    "stage": str,
                    "output": str,
                    "status": "ok"|"stale"|"missing"|"propagated_stale"|"input_missing",
                    "output_mtime": str | None,
                    "newest_input": str | None,
                    "newest_input_mtime": str | None,
                    "all_inputs": [{"path": str, "mtime": str | None}],
                    "rerun_cmd": str,
                }
            ],
            "any_stale": bool,
            "rerun_order": [str],   # deduplicated, in topological order
        }
    """
    root = project_root or _PROJECT_ROOT

    # Map output path (relative) → stage name, for propagation lookup
    output_to_stage: dict[str, str] = {node.output: node.stage for node in DAG}
    # Track which stages are stale (including propagated)
    stale_stages: set[str] = set()

    stages_result = []

    for node in DAG:
        out_path = root / node.output
        inp_paths = [root / p for p in node.inputs]

        out_mtime = _mtime_str(out_path)
        all_inputs = [
            {"path": rel, "mtime": _mtime_str(root / rel)}
            for rel in node.inputs
        ]

        # Check if any input is the output of a known stale stage
        upstream_stale = any(
            output_to_stage.get(rel) in stale_stages
            for rel in node.inputs
            if rel in output_to_stage
        )

        existing_inputs = [p for p in inp_paths if p.exists()]
        all_inputs_absent = len(existing_inputs) == 0 and len(inp_paths) > 0

        if not out_path.exists():
            status = "missing"
            newest_input = None
            newest_input_mtime = None
            stale_stages.add(node.stage)
        elif all_inputs_absent:
            status = "input_missing"
            newest_input = None
            newest_input_mtime = None
        else:
            direct_stale, newest = is_stale(out_path, inp_paths)
            newest_input = str(newest.relative_to(root)).replace("\\", "/") if newest else None
            newest_input_mtime = _mtime_str(newest) if newest else None
            if direct_stale:
                status = "stale"
                stale_stages.add(node.stage)
            elif upstream_stale:
                status = "propagated_stale"
                stale_stages.add(node.stage)
            else:
                status = "ok"

        stages_result.append({
            "stage": node.stage,
            "output": node.output,
            "status": status,
            "output_mtime": out_mtime,
            "newest_input": newest_input,
            "newest_input_mtime": newest_input_mtime,
            "all_inputs": all_inputs,
            "rerun_cmd": node.rerun_cmd,
        })

    # Build rerun_order: deduplicated commands for stale stages, in DAG order
    seen_cmds: set[str] = set()
    rerun_order = []
    for entry in stages_result:
        if entry["status"] in ("stale", "missing", "propagated_stale"):
            cmd = entry["rerun_cmd"]
            if cmd not in seen_cmds:
                seen_cmds.add(cmd)
                rerun_order.append(cmd)

    any_stale = bool(stale_stages)

    return {
        "stages": stages_result,
        "any_stale": any_stale,
        "rerun_order": rerun_order,
    }


def format_audit(result: dict, verbose: bool = False) -> str:
    """Render audit result as a human-readable string."""
    lines: list[str] = []

    STATUS_ICON = {
        "ok": "✓ OK",
        "stale": "⚠ STALE",
        "missing": "✗ MISSING",
        "propagated_stale": "↓ DOWNSTREAM",
        "input_missing": "? INPUT MISSING",
    }

    lines.append("Pipeline Freshness Audit")
    lines.append("=" * 60)

    for entry in result["stages"]:
        icon = STATUS_ICON.get(entry["status"], entry["status"])
        lines.append(f"\n[{icon}]  {entry['stage']}")
        lines.append(f"  Output : {entry['output']}")
        if entry["output_mtime"]:
            lines.append(f"  Written: {entry['output_mtime']}")
        else:
            lines.append("  Written: (not found)")

        if entry["status"] == "stale" and entry["newest_input"]:
            lines.append(f"  Stale  : newer input → {entry['newest_input']} ({entry['newest_input_mtime']})")
        elif entry["status"] == "propagated_stale":
            lines.append("  Reason : upstream stage is stale")
        elif entry["status"] == "missing":
            lines.append("  Reason : output file does not exist")
        elif entry["status"] == "input_missing":
            lines.append("  Reason : no input files found — cannot check")

        if verbose:
            lines.append("  Inputs :")
            for inp in entry["all_inputs"]:
                mtime = inp["mtime"] or "(missing)"
                lines.append(f"    {mtime}  {inp['path']}")

    lines.append("\n" + "=" * 60)

    if result["any_stale"]:
        lines.append("Stale stages detected. Run in order:")
        for cmd in result["rerun_order"]:
            lines.append(f"  {cmd}")
    else:
        lines.append("All stages up-to-date.")

    return "\n".join(lines)
