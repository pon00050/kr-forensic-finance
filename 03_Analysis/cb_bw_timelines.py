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
    df = df_pv.copy()
    # Identify date column
    date_col = next(
        (c for c in df.columns if "date" in c.lower()),
        df.columns[1] if len(df.columns) > 1 else None,
    )
    if date_col and date_col != "date":
        df = df.rename(columns={date_col: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["ticker", "date"])
    return df


@app.cell
def _score_events(df_cb, df_pv_clean, df_oh, df_map, pd, np, json):
    """Score each CB/BW event against 4 manipulation signal flags."""

    # Join corp_code → ticker
    if not df_map.empty and "corp_code" in df_map.columns and "ticker" in df_map.columns:
        map_lookup = df_map.set_index("corp_code")["ticker"].to_dict()
    else:
        map_lookup = {}

    results = []

    for _, event in df_cb.iterrows():
        corp_code = event["corp_code"]
        issue_date_raw = event.get("issue_date")
        bond_type = event.get("bond_type", "CB")
        exercise_price = event.get("exercise_price")

        if not issue_date_raw:
            continue

        issue_date = pd.to_datetime(issue_date_raw, errors="coerce")
        if pd.isna(issue_date):
            continue

        ticker = map_lookup.get(corp_code)
        if not ticker:
            continue

        # Price window: ±60 trading days
        df_ticker = df_pv_clean[df_pv_clean["ticker"] == ticker].copy()
        if df_ticker.empty or "close" not in df_ticker.columns:
            continue

        df_ticker = df_ticker.sort_values("date").reset_index(drop=True)
        issue_idx = df_ticker["date"].searchsorted(issue_date)

        window_start = max(0, issue_idx - 60)
        window_end = min(len(df_ticker), issue_idx + 61)
        df_window = df_ticker.iloc[window_start:window_end].copy()
        df_pre = df_ticker.iloc[max(0, window_start - 30):window_start].copy()

        if df_window.empty:
            continue

        flags = []
        flag_details = {}

        # Flag 1: Repricing below market price (리픽싱)
        repricing_flag = False
        repricing_raw = event.get("repricing_history", "[]")
        try:
            repricings = json.loads(repricing_raw) if isinstance(repricing_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            repricings = []
        for rp in repricings:
            rp_price = rp.get("new_price") or rp.get("조정가액")
            rp_date_raw = rp.get("date") or rp.get("조정일자")
            if rp_price and rp_date_raw:
                rp_date = pd.to_datetime(str(rp_date_raw)[:8], errors="coerce")
                if not pd.isna(rp_date):
                    market_price_at_rp = df_ticker[df_ticker["date"] <= rp_date]["close"].iloc[-1] if not df_ticker[df_ticker["date"] <= rp_date].empty else None
                    if market_price_at_rp and float(rp_price) < market_price_at_rp * 0.95:
                        repricing_flag = True
        if repricing_flag:
            flags.append("repricing_below_market")
            flag_details["repricing_flag"] = True

        # Flag 2: Exercise clustering within 5 days of price peak
        exercise_cluster_flag = False
        exercise_raw = event.get("exercise_events", "[]")
        try:
            exercises = json.loads(exercise_raw) if isinstance(exercise_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            exercises = []
        if not df_window.empty and "close" in df_window.columns:
            peak_idx = df_window["close"].idxmax()
            peak_date = df_window.loc[peak_idx, "date"] if peak_idx in df_window.index else None
            for ex in exercises:
                ex_date_raw = ex.get("exercise_date") or ex.get("권리행사일")
                if ex_date_raw and peak_date is not None:
                    ex_date = pd.to_datetime(str(ex_date_raw)[:8], errors="coerce")
                    if not pd.isna(ex_date) and abs((ex_date - peak_date).days) <= 5:
                        exercise_cluster_flag = True
        if exercise_cluster_flag:
            flags.append("exercise_at_peak")
            flag_details["exercise_cluster_flag"] = True
            flag_details["peak_date"] = str(peak_date) if peak_date is not None else None

        # Flag 3: Volume ratio > 3× pre-event baseline
        volume_flag = False
        volume_ratio = None
        vol_col = next((c for c in df_window.columns if "volume" in c.lower() or "거래량" in c), None)
        if vol_col and not df_pre.empty and vol_col in df_pre.columns:
            baseline_vol = df_pre[vol_col].mean()
            event_vol = df_window[vol_col].mean()
            if baseline_vol and baseline_vol > 0:
                volume_ratio = event_vol / baseline_vol
                if volume_ratio > 3.0:
                    volume_flag = True
        if volume_flag:
            flags.append("volume_surge")
            flag_details["volume_ratio"] = round(float(volume_ratio), 2)

        # Flag 4: Officer holdings decrease post-exercise
        holdings_flag = False
        df_corp_oh = df_oh[df_oh["corp_code"] == corp_code].copy()
        if not df_corp_oh.empty and "date" in df_corp_oh.columns:
            df_corp_oh["date"] = pd.to_datetime(df_corp_oh["date"].astype(str).str[:8], errors="coerce")
            post_ex = df_corp_oh[df_corp_oh["date"] > issue_date]
            pre_ex = df_corp_oh[df_corp_oh["date"] <= issue_date]
            if not post_ex.empty and not pre_ex.empty:
                try:
                    pre_shares = pd.to_numeric(pre_ex["change_shares"], errors="coerce").sum()
                    post_shares = pd.to_numeric(post_ex["change_shares"], errors="coerce").sum()
                    if pre_shares > 0 and post_shares < pre_shares * 0.95:
                        holdings_flag = True
                except Exception:
                    pass
        if holdings_flag:
            flags.append("holdings_decrease")

        anomaly_score = len(flags)

        result = {
            "corp_code": corp_code,
            "ticker": ticker,
            "issue_date": str(issue_date.date()),
            "bond_type": bond_type,
            "exercise_price": exercise_price,
            "anomaly_score": anomaly_score,
            "flags": ", ".join(flags),
            "repricing_flag": "repricing_below_market" in flags,
            "exercise_cluster_flag": "exercise_at_peak" in flags,
            "volume_flag": "volume_surge" in flags,
            "holdings_flag": "holdings_decrease" in flags,
            "volume_ratio": flag_details.get("volume_ratio"),
            "peak_date": flag_details.get("peak_date"),
            "dart_link": f"https://dart.fss.or.kr/corp/searchAjax.do?textCrpCik={corp_code}",
        }
        results.append(result)

    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_results = df_results.sort_values("anomaly_score", ascending=False)

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
        "flags", "volume_ratio", "exercise_price", "dart_link",
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
