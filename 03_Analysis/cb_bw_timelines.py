# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo",
#     "pandas",
#     "pyarrow",
#     "numpy",
#     "plotly",
# ]
# ///
"""
cb_bw_timelines.py — Milestone 2: CB/BW anomaly timeline analysis.

For each CB/BW issuance event, builds a ±60 trading day price/volume window
and scores the event against four manipulation signal flags:
    1. Repricing below market price (리픽싱)
    2. Exercise clustering within 5 days of price peak
    3. Volume ratio > 3× pre-event baseline
    4. Officer holdings decrease post-exercise

Outputs per flagged event and an aggregate summary CSV.

Methodology note:
    CB/BW 3자배정 (third-party allotment) manipulation is a documented KOSDAQ pattern.
    Signals here are statistical anomalies, not evidence of fraud. False positive rate
    expected ~40% — all outputs require human review before any regulatory action.

Run interactively:
    uv run marimo edit 03_Analysis/cb_bw_timelines.py

Run as web app:
    uv run marimo run 03_Analysis/cb_bw_timelines.py
"""

import marimo

__generated_with = "0.9.0"
app = marimo.App(width="wide", app_title="CB/BW Anomaly Timelines")


@app.cell
def _imports():
    import json
    import marimo as mo
    import numpy as np
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    from pathlib import Path
    return json, mo, np, pd, px, go, Path


@app.cell
def _load_data(mo, pd, Path):
    """Load processed Parquet tables."""
    processed = Path("01_Data/processed")

    required = ["cb_bw_events.parquet", "price_volume.parquet", "officer_holdings.parquet"]
    missing = [f for f in required if not (processed / f).exists()]
    if missing:
        mo.stop(
            mo.callout(
                mo.md(
                    f"**Missing data files:** {', '.join(missing)}  \n"
                    "Run the pipeline first: `python 02_Pipeline/pipeline.py --market KOSDAQ --start 2020 --end 2025`"
                ),
                kind="danger",
            )
        )

    df_cb = pd.read_parquet(processed / "cb_bw_events.parquet")
    df_pv = pd.read_parquet(processed / "price_volume.parquet")
    df_oh = pd.read_parquet(processed / "officer_holdings.parquet")
    df_map = pd.read_parquet(processed / "corp_ticker_map.parquet") if (processed / "corp_ticker_map.parquet").exists() else pd.DataFrame()

    mo.callout(
        mo.md(
            f"Loaded **{len(df_cb):,}** CB/BW events across "
            f"**{df_cb['corp_code'].nunique():,}** companies"
        ),
        kind="success",
    )
    return df_cb, df_pv, df_oh, df_map


@app.cell
def _prepare_price(df_pv, pd):
    """Normalise price/volume dataframe for window lookups."""
    df_pv_clean = df_pv.copy()
    # Identify date column
    date_col = next(
        (c for c in df_pv_clean.columns if "date" in c.lower()),
        df_pv_clean.columns[1] if len(df_pv_clean.columns) > 1 else None,
    )
    if date_col and date_col != "date":
        df_pv_clean = df_pv_clean.rename(columns={date_col: "date"})
    df_pv_clean["date"] = pd.to_datetime(df_pv_clean["date"], errors="coerce")
    df_pv_clean = df_pv_clean.dropna(subset=["date"]).sort_values(["ticker", "date"])
    return df_pv_clean


@app.cell
def _score_events(df_cb, df_pv_clean, df_oh, df_map):
    """Score each CB/BW event against 4 manipulation signal flags."""
    from _scoring import score_events
    df_results = score_events(df_cb, df_pv_clean, df_oh, df_map)
    return df_results


@app.cell
def _display(mo, df_results, px):
    """Display results."""
    if df_results.empty:
        return mo.callout(
            mo.md("No scoreable CB/BW events found. Check that price/volume data covers the event dates."),
            kind="warn",
        )

    flagged = df_results[df_results["anomaly_score"] > 0]
    high_risk = df_results[df_results["anomaly_score"] >= 3]

    summary = mo.hstack([
        mo.stat(value=str(len(df_results)), label="Total CB/BW events"),
        mo.stat(value=str(len(flagged)), label="Events with ≥1 flag"),
        mo.stat(value=str(len(high_risk)), label="High risk (≥3 flags)"),
    ])

    fig = px.histogram(
        df_results,
        x="anomaly_score",
        title="CB/BW anomaly score distribution",
        labels={"anomaly_score": "Anomaly score (0–4)"},
        color_discrete_sequence=["#e05c5c"],
    )
    fig.update_layout(height=300)

    display_cols = [
        "corp_code", "ticker", "issue_date", "bond_type", "anomaly_score",
        "flags", "volume_ratio", "exercise_price",
        "issue_amount", "refixing_floor",
        "dart_link",
    ]
    available = [c for c in display_cols if c in df_results.columns]

    return mo.vstack([
        summary,
        mo.ui.plotly(fig),
        mo.md("### Flagged events (sorted by anomaly score)"),
        mo.ui.table(flagged[available], selection=None),
    ])


@app.cell
def _export_results(df_results, mo, Path, json):
    """Export per-event JSON files and aggregate CSV."""
    out_dir = Path("03_Analysis/cb_bw_timelines")
    out_dir.mkdir(parents=True, exist_ok=True)

    for _, row in df_results[df_results["anomaly_score"] > 0].iterrows():
        fname = f"{row['corp_code']}_{row['issue_date'].replace('-', '')}.json"
        with open(out_dir / fname, "w", encoding="utf-8") as f:
            json.dump(row.to_dict(), f, ensure_ascii=False, indent=2, default=str)

    summary_path = Path("03_Analysis/cb_bw_summary.csv")
    df_results.to_csv(summary_path, index=False, encoding="utf-8-sig")

    return mo.callout(
        mo.md(
            f"Exported **{len(df_results):,}** events to `{summary_path}`  \n"
            f"Per-event JSON files in `{out_dir}/`"
        ),
        kind="success",
    )


if __name__ == "__main__":
    app.run()
