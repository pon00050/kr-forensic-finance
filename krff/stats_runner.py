"""src/stats_runner.py — Statistical test freshness checker.

Encodes the DAG of 14 statistical test scripts, checks staleness,
and returns an ordered audit result with skip gates (labels, SEIBRO, upstream).

Usage:
    from krff.stats_runner import get_stats_audit, format_stats_audit
    result = get_stats_audit()
    print(format_stats_audit(result))
    print(format_stats_audit(result, verbose=True))
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from krff._paths import PROJECT_ROOT as _PROJECT_ROOT
from krff.audit import is_stale


MIN_LABELS = 5
LABELS_PATH = "03_Analysis/statistical_tests/labels.csv"

_STAT_OUTPUTS = "03_Analysis/statistical_tests/outputs"
_PROCESSED = "01_Data/processed"
_ANALYSIS = "03_Analysis"
_OFFICER_NETWORK = "03_Analysis/officer_network"


@dataclass
class StatNode:
    name: str                            # short key, e.g. "fdr_timing"
    script: str                          # relative path from project root
    inputs: list[str]                    # relative paths that make this node stale
    primary_output: str                  # relative path to check for staleness
    needs_labels: bool = False           # requires labels.csv >= MIN_LABELS rows
    needs_seibro: bool = False           # requires SEIBRO repricing data
    upstream_stat: Optional[str] = None  # name of another StatNode whose primary_output is an input


STATS_DAG: list[StatNode] = [
    StatNode(
        name="fdr_timing",
        script="03_Analysis/statistical_tests/fdr_timing_anomalies.py",
        inputs=[f"{_ANALYSIS}/timing_anomalies.csv"],
        primary_output=f"{_STAT_OUTPUTS}/fdr_timing_anomalies.csv",
    ),
    StatNode(
        name="fdr_leakage",
        script="03_Analysis/statistical_tests/fdr_disclosure_leakage.py",
        inputs=[f"{_OFFICER_NETWORK}/centrality_report.csv"],
        primary_output=f"{_STAT_OUTPUTS}/fdr_disclosure_leakage.csv",
    ),
    StatNode(
        name="pca",
        script="03_Analysis/statistical_tests/pca_beneish.py",
        inputs=[f"{_PROCESSED}/beneish_scores.parquet"],
        primary_output=f"{_STAT_OUTPUTS}/pca_pc3_scores.csv",
    ),
    StatNode(
        name="cluster",
        script="03_Analysis/statistical_tests/cluster_peers.py",
        inputs=[f"{_PROCESSED}/beneish_scores.parquet"],
        primary_output=f"{_STAT_OUTPUTS}/peer_clusters.csv",
    ),
    StatNode(
        name="impute",
        script="03_Analysis/statistical_tests/impute_financials.py",
        inputs=[f"{_PROCESSED}/beneish_scores.parquet"],
        primary_output=f"{_STAT_OUTPUTS}/company_financials_imputed.parquet",
    ),
    StatNode(
        name="bootstrap_centrality",
        script="03_Analysis/statistical_tests/bootstrap_centrality.py",
        inputs=[f"{_OFFICER_NETWORK}/centrality_report.csv"],
        primary_output=f"{_STAT_OUTPUTS}/bootstrap_centrality.csv",
    ),
    StatNode(
        name="bootstrap_threshold",
        script="03_Analysis/statistical_tests/bootstrap_threshold.py",
        inputs=[f"{_PROCESSED}/beneish_scores.parquet"],
        primary_output=f"{_STAT_OUTPUTS}/bootstrap_threshold.csv",
        needs_labels=True,
    ),
    StatNode(
        name="lasso",
        script="03_Analysis/statistical_tests/lasso_beneish.py",
        inputs=[f"{_PROCESSED}/beneish_scores.parquet"],
        primary_output=f"{_STAT_OUTPUTS}/lasso_coefficients.csv",
        needs_labels=True,
    ),
    StatNode(
        name="classify_outliers",
        script="03_Analysis/statistical_tests/classify_extreme_outliers.py",
        inputs=[f"{_PROCESSED}/beneish_scores.parquet"],
        primary_output=f"{_STAT_OUTPUTS}/extreme_outlier_classification.csv",
    ),
    StatNode(
        name="cross_screen",
        script="03_Analysis/statistical_tests/cross_screen_analysis.py",
        inputs=[
            f"{_STAT_OUTPUTS}/pca_pc3_scores.csv",
            f"{_ANALYSIS}/cb_bw_summary.csv",
        ],
        primary_output=f"{_STAT_OUTPUTS}/double_flagged_companies.csv",
        upstream_stat="pca",
    ),
    StatNode(
        name="rf",
        script="03_Analysis/statistical_tests/rf_feature_importance.py",
        inputs=[f"{_PROCESSED}/beneish_scores.parquet"],
        primary_output=f"{_STAT_OUTPUTS}/rf_importance.csv",
        needs_labels=True,
    ),
    StatNode(
        name="label_coverage",
        script="03_Analysis/statistical_tests/label_coverage_analysis.py",
        inputs=[f"{_STAT_OUTPUTS}/double_flagged_companies.csv"],
        primary_output=f"{_STAT_OUTPUTS}/label_coverage.csv",
        needs_labels=True,
        upstream_stat="cross_screen",
    ),
    StatNode(
        name="survival",
        script="03_Analysis/statistical_tests/survival_repricing.py",
        inputs=[
            f"{_PROCESSED}/cb_bw_events.parquet",
            f"{_PROCESSED}/beneish_scores.parquet",
        ],
        primary_output=f"{_STAT_OUTPUTS}/survival_cox_summary.csv",
        needs_seibro=True,
    ),
    StatNode(
        name="permutation",
        script="03_Analysis/statistical_tests/permutation_repricing_peak.py",
        inputs=[f"{_PROCESSED}/beneish_scores.parquet"],
        primary_output=f"{_STAT_OUTPUTS}/permutation_repricing.csv",
        needs_seibro=True,
    ),
]

# Fast lookup by name
_DAG_BY_NAME: dict[str, StatNode] = {n.name: n for n in STATS_DAG}


def _check_labels(project_root: Path) -> tuple[bool, int]:
    """Return (has_enough_labels, row_count). Reads labels.csv line count only."""
    labels = project_root / LABELS_PATH
    if not labels.exists():
        return False, 0
    with open(labels, encoding="utf-8") as f:
        rows = sum(1 for _ in f) - 1  # subtract header
    count = max(rows, 0)
    return count >= MIN_LABELS, count


def _check_seibro(project_root: Path) -> bool:
    """Return True if cb_bw_events.parquet has any non-null repricing_history values."""
    p = project_root / "01_Data/processed/cb_bw_events.parquet"
    if not p.exists():
        return False
    import pandas as pd
    try:
        df = pd.read_parquet(p, columns=["repricing_history"])
        return bool(df["repricing_history"].notna().any())
    except Exception:
        return False


def get_stats_audit(project_root: Optional[Path] = None) -> dict:
    """Compute per-test freshness and return structured audit result.

    Returns:
        {
            "tests": [
                {
                    "name": str,
                    "status": "ok"|"stale"|"missing"|"skip_seibro"|"skip_labels"|"skip_upstream",
                    "output": str,
                    "script": str,
                    "reason": str,
                },
                ...
            ],
            "any_runnable": bool,       # any test is stale/missing and not skipped
            "run_order": [name, ...],   # stale/missing tests in topo order
            "labels_ok": bool,
            "labels_count": int,
            "seibro_ok": bool,
        }
    """
    root = project_root or _PROJECT_ROOT
    labels_ok, labels_count = _check_labels(root)
    seibro_ok = _check_seibro(root)

    # Nodes whose status prevents downstream from running
    blocked: set[str] = set()
    tests_result = []

    for node in STATS_DAG:
        # Gate 1: SEIBRO
        if node.needs_seibro and not seibro_ok:
            status = "skip_seibro"
            reason = "SEIBRO repricing data not yet available (cb_bw_events.parquet has no repricing_history)"
            blocked.add(node.name)

        # Gate 2: labels
        elif node.needs_labels and not labels_ok:
            status = "skip_labels"
            reason = f"labels.csv has fewer than {MIN_LABELS} rows (found {labels_count})"
            blocked.add(node.name)

        # Gate 3: upstream stat
        elif node.upstream_stat and node.upstream_stat in blocked:
            status = "skip_upstream"
            reason = f"upstream test '{node.upstream_stat}' is blocked or missing"
            blocked.add(node.name)

        # Staleness check
        else:
            out_path = root / node.primary_output
            inp_paths = [root / inp for inp in node.inputs]

            if not out_path.exists():
                status = "missing"
                reason = "output file does not exist"
                blocked.add(node.name)
            else:
                stale, newest = is_stale(out_path, inp_paths)
                if stale:
                    status = "stale"
                    if newest:
                        try:
                            rel = newest.relative_to(root).as_posix()
                        except ValueError:
                            rel = str(newest)
                        reason = f"newer input: {rel}"
                    else:
                        reason = "output predates inputs"
                else:
                    status = "ok"
                    reason = ""

        tests_result.append({
            "name": node.name,
            "status": status,
            "output": node.primary_output,
            "script": node.script,
            "reason": reason,
        })

    run_order = [
        t["name"] for t in tests_result
        if t["status"] in ("stale", "missing")
    ]
    any_runnable = bool(run_order)

    return {
        "tests": tests_result,
        "any_runnable": any_runnable,
        "run_order": run_order,
        "labels_ok": labels_ok,
        "labels_count": labels_count,
        "seibro_ok": seibro_ok,
    }


def format_stats_audit(result: dict, verbose: bool = False) -> str:
    """Render stats audit result as a human-readable string."""
    lines: list[str] = []

    STATUS_ICON = {
        "ok": "✓ OK",
        "stale": "⚠ STALE",
        "missing": "✗ MISSING",
        "skip_seibro": "⊘ SKIP",
        "skip_labels": "⊘ SKIP",
        "skip_upstream": "⊘ SKIP",
    }

    labels_count = result.get("labels_count", 0)
    seibro_ok = result.get("seibro_ok", False)

    lines.append("Statistical Tests Audit")
    lines.append("=" * 60)
    labels_status = "OK" if result["labels_ok"] else f"INSUFFICIENT — need ≥ {MIN_LABELS}"
    lines.append(f"  labels.csv  : {labels_count} rows ({labels_status})")
    lines.append(f"  SEIBRO data : {'available' if seibro_ok else 'not yet available'}")
    lines.append("")

    for entry in result["tests"]:
        icon = STATUS_ICON.get(entry["status"], entry["status"])
        skip_detail = ""
        if entry["status"] == "skip_seibro":
            skip_detail = " (no SEIBRO data)"
        elif entry["status"] == "skip_labels":
            skip_detail = " (labels insufficient)"
        elif entry["status"] == "skip_upstream":
            skip_detail = f" (upstream '{_DAG_BY_NAME[entry['name']].upstream_stat}' blocked)"

        lines.append(f"[{icon}{skip_detail}]  {entry['name']}")

        if entry["reason"] and entry["status"] != "ok":
            lines.append(f"  Reason : {entry['reason']}")

        if verbose and entry["status"] in ("stale", "ok", "missing"):
            lines.append(f"  Output : {entry['output']}")
            node = _DAG_BY_NAME.get(entry["name"])
            if node:
                lines.append("  Inputs :")
                for inp in node.inputs:
                    lines.append(f"    {inp}")

    lines.append("\n" + "=" * 60)

    n_stale = sum(1 for t in result["tests"] if t["status"] in ("stale", "missing"))
    n_skip = sum(1 for t in result["tests"] if t["status"].startswith("skip"))
    lines.append(f"{n_stale} tests stale/missing, {n_skip} skipped")

    if result["run_order"]:
        lines.append("Run in order:")
        for name in result["run_order"]:
            node = _DAG_BY_NAME.get(name)
            if node:
                lines.append(f"  python {node.script}")

    return "\n".join(lines)
