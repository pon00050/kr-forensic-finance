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
timing_anomalies.py — Milestone 3: Disclosure timing anomaly detection.

For each material disclosure, checks whether the same-day or prior-day price/volume
movement is inconsistent with the timing of filing (e.g., large price moves before
after-hours disclosure → possible information leakage).

Anomaly score = abs(price_change) × volume_ratio × gap_hours
Flag condition: price change ≥ 5% AND volume ratio ≥ 2× on same or prior trading day.

Korean market hours: 09:00–15:30 KST.
DART filings timestamped in KST.

Calibration note: After-hours filings for routine annual reports will appear anomalous
by this metric. The disclosure type filter reduces but does not eliminate false positives.
All flags require human review.

Run interactively:
    uv run marimo edit 03_Analysis/timing_anomalies.py

Run as web app:
    uv run marimo run 03_Analysis/timing_anomalies.py
"""

import marimo

__generated_with = "0.9.0"
app = marimo.App(width="wide", app_title="Disclosure Timing Anomalies")


@app.cell
def _imports():
    import marimo as mo
    import numpy as np
    import pandas as pd
    import plotly.express as px
    from pathlib import Path
    return mo, np, pd, px, Path


@app.cell
def _load_data(mo, pd, Path):
    """Load processed Parquet tables."""
    processed = Path("01_Data/processed")

    required = ["disclosures.parquet", "price_volume.parquet"]
    missing = [f for f in required if not (processed / f).exists()]
    if missing:
        mo.stop(
            mo.callout(
                mo.md(
                    f"**Missing data files:** {', '.join(missing)}  \n"
                    "Run the pipeline first: `python 02_Pipeline/pipeline.py`"
                ),
                kind="danger",
            )
        )

    df_disc = pd.read_parquet(processed / "disclosures.parquet")
    df_pv = pd.read_parquet(processed / "price_volume.parquet")
    df_map = (
        pd.read_parquet(processed / "corp_ticker_map.parquet")
        if (processed / "corp_ticker_map.parquet").exists()
        else pd.DataFrame()
    )

    mo.callout(
        mo.md(f"Loaded **{len(df_disc):,}** disclosures, **{df_pv['ticker'].nunique():,}** tickers"),
        kind="success",
    )
    return df_disc, df_pv, df_map


@app.cell
def _prepare_disclosures(df_disc, pd):
    """
    Normalise disclosure timestamps and filter to material disclosure types.

    Material disclosure types (by DART report_nm pattern):
    - 주요사항보고서 (major event reports — CB/BW issuance, M&A decisions)
    - 공시번호 containing specific keywords
    - Anything with large same-day moves regardless of type

    DART filing timestamps: 'filed_at' column is date string YYYYMMDD from listing API.
    The DART listing API does not return hour-minute timestamps; for high-precision
    gap analysis, use the DART full-text API (rcept_no → document details).
    Here we use a conservative approximation: after-hours = filed_at date with
    assumed time 18:00 KST (DART's typical batch upload window).
    """
    df_disc_clean = df_disc.copy()
    df_disc_clean["filed_at"] = pd.to_datetime(df_disc_clean["filed_at"].astype(str).str[:8], format="%Y%m%d", errors="coerce")
    df_disc_clean = df_disc_clean.dropna(subset=["filed_at"])

    # Approximate filing time: 18:00 KST (conservative; actual varies 15:30–23:59)
    df_disc_clean["filed_datetime"] = df_disc_clean["filed_at"] + pd.Timedelta(hours=18)
    df_disc_clean["trading_date"] = df_disc_clean["filed_at"]  # same calendar date

    # Market close: 15:30 KST → gap_hours from close to filing
    market_close_hour = 15.5  # 15:30 in decimal hours
    df_disc_clean["gap_hours"] = 18.0 - market_close_hour  # approximation (2.5 hours)

    # Filter to material types — exclude routine semi-annual reports, proxy statements
    MATERIAL_KEYWORDS = [
        "주요사항", "전환사채", "신주인수권", "유상증자", "합병", "분할", "양수도",
        "최대주주", "특수관계인", "풍문", "조회공시", "공급계약", "매출액",
    ]
    pattern = "|".join(MATERIAL_KEYWORDS)
    df_disc_clean["is_material"] = df_disc_clean["title"].str.contains(pattern, na=False)

    return df_disc_clean


@app.cell
def _prepare_price(df_pv, pd):
    """Prepare price/volume data for lookup."""
    df_pv_clean = df_pv.copy()
    date_col = next((c for c in df_pv_clean.columns if "date" in c.lower()), None)
    if date_col and date_col != "date":
        df_pv_clean = df_pv_clean.rename(columns={date_col: "date"})
    df_pv_clean["date"] = pd.to_datetime(df_pv_clean["date"], errors="coerce")

    # Calculate daily price change
    df_pv_clean = df_pv_clean.sort_values(["ticker", "date"])
    df_pv_clean["price_change_pct"] = df_pv_clean.groupby("ticker")["close"].pct_change() * 100

    # 30-day rolling average volume
    vol_col = next((c for c in df_pv_clean.columns if "volume" in c.lower()), None)
    if vol_col:
        df_pv_clean["vol_30d_avg"] = (
            df_pv_clean.groupby("ticker")[vol_col]
            .transform(lambda x: x.rolling(30, min_periods=5).mean().shift(1))
        )
        df_pv_clean["volume_ratio"] = df_pv_clean[vol_col] / df_pv_clean["vol_30d_avg"].replace(0, float("nan"))
    else:
        df_pv_clean["volume_ratio"] = float("nan")

    return df_pv_clean


@app.cell
def _score_disclosures(df_disc_clean, df_pv_clean, df_map, pd, np):
    """Score each disclosure against timing anomaly criteria."""

    # corp_code → ticker map
    if not df_map.empty and "corp_code" in df_map.columns:
        map_lookup = df_map.drop_duplicates("corp_code").set_index("corp_code")["ticker"].to_dict()
    else:
        map_lookup = {}

    # Index price data by (ticker, date) for fast lookup
    pv_idx = df_pv_clean.set_index(["ticker", "date"])

    results = []
    for _, disc in df_disc_clean.iterrows():
        corp_code = disc["corp_code"]
        ticker = map_lookup.get(corp_code)
        if not ticker:
            continue

        t_date = disc["trading_date"]
        gap_hours = disc["gap_hours"]

        # Look up same-day and prior-day price action
        for offset_days, label in [(0, "same_day"), (-1, "prior_day")]:
            check_date = t_date + pd.Timedelta(days=offset_days)
            key = (ticker, check_date)
            if key not in pv_idx.index:
                continue

            row_pv = pv_idx.loc[key]
            price_chg = float(row_pv.get("price_change_pct", np.nan))
            vol_ratio = float(row_pv.get("volume_ratio", np.nan))

            if np.isnan(price_chg) or np.isnan(vol_ratio):
                continue

            anomaly_score = abs(price_chg) * vol_ratio * gap_hours
            flag = abs(price_chg) >= 5.0 and vol_ratio >= 2.0

            if flag or abs(price_chg) >= 3.0:  # capture borderline cases too
                results.append({
                    "corp_code": corp_code,
                    "ticker": ticker,
                    "filing_date": str(t_date.date()),
                    "check_date": str(check_date.date()),
                    "timing": label,
                    "disclosure_type": disc.get("type"),
                    "title": disc.get("title"),
                    "price_change_pct": round(price_chg, 2),
                    "volume_ratio": round(vol_ratio, 2),
                    "gap_hours": round(gap_hours, 1),
                    "anomaly_score": round(anomaly_score, 2),
                    "flag": flag,
                    "is_material": disc.get("is_material", False),
                    "dart_link": disc.get("dart_link"),
                })

    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_results = df_results.sort_values("anomaly_score", ascending=False)

    return df_results


@app.cell
def _ui_controls(mo, df_results):
    """Interactive controls."""
    min_price_chg = mo.ui.slider(
        start=0.0,
        stop=20.0,
        step=0.5,
        value=5.0,
        label="Min |price change| %",
        show_value=True,
    )
    min_vol_ratio = mo.ui.slider(
        start=1.0,
        stop=10.0,
        step=0.5,
        value=2.0,
        label="Min volume ratio",
        show_value=True,
    )
    material_only = mo.ui.checkbox(label="Material disclosures only", value=False)
    return min_price_chg, min_vol_ratio, material_only


@app.cell
def _display(mo, df_results, min_price_chg, min_vol_ratio, material_only, px):
    """Apply filters and display results."""
    if df_results.empty:
        return mo.callout(
            mo.md("No timing anomalies could be scored. Ensure disclosures and price data overlap in date range."),
            kind="warn",
        )

    filtered = df_results[
        (df_results["price_change_pct"].abs() >= min_price_chg.value)
        & (df_results["volume_ratio"] >= min_vol_ratio.value)
    ]
    if material_only.value:
        filtered = filtered[filtered["is_material"] == True]

    summary = mo.hstack([
        mo.stat(value=str(len(df_results)), label="Total scored disclosures"),
        mo.stat(value=str(len(filtered)), label="Above thresholds"),
        mo.stat(value=str(filtered["corp_code"].nunique()), label="Unique companies"),
    ])

    fig = px.scatter(
        filtered.head(500),
        x="price_change_pct",
        y="volume_ratio",
        color="anomaly_score",
        hover_data=["corp_code", "ticker", "title", "filing_date"],
        title="Disclosure timing anomalies — price change vs. volume ratio",
        labels={
            "price_change_pct": "Price change %",
            "volume_ratio": "Volume ratio vs. 30d avg",
        },
        color_continuous_scale="Reds",
    )
    fig.update_layout(height=400)

    display_cols = [
        "corp_code", "ticker", "filing_date", "timing", "title",
        "price_change_pct", "volume_ratio", "anomaly_score", "dart_link",
    ]
    available = [c for c in display_cols if c in filtered.columns]

    return mo.vstack([
        mo.hstack([min_price_chg, min_vol_ratio, material_only]),
        summary,
        mo.ui.plotly(fig),
        mo.ui.table(filtered[available].head(200), selection=None),
    ])


@app.cell
def _export(df_results, mo, Path):
    """Export timing anomalies CSV."""
    out_path = Path("03_Analysis/timing_anomalies.csv")
    df_results.to_csv(out_path, index=False, encoding="utf-8-sig")
    return mo.callout(
        mo.md(f"Exported **{len(df_results):,}** rows to `{out_path}`"),
        kind="success",
    )


if __name__ == "__main__":
    app.run()
