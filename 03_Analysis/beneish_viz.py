import marimo

__generated_with = "0.10.0"
app = marimo.App(width="medium")


@app.cell
def _imports():
    import marimo as mo
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    from pathlib import Path
    return mo, pd, px, go, Path


@app.cell
def _load_data(pd, Path):
    _parquet = Path(__file__).parent.parent / "01_Data" / "processed" / "beneish_scores.parquet"
    if not _parquet.exists():
        raise FileNotFoundError(
            f"beneish_scores.parquet not found at {_parquet}\n"
            "Run the pipeline then beneish_screen.py first:\n"
            "  python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023\n"
            "  python 03_Analysis/beneish_screen.py\n"
            "(Output goes to 01_Data/processed/beneish_scores.parquet)"
        )
    df = pd.read_parquet(_parquet)
    print(f"Loaded {len(df):,} rows, {df['corp_code'].nunique():,} companies, years {sorted(df['year'].unique())}")
    return df


@app.cell
def _chart_distribution(df, px, go):
    _color_map = {
        "Low": "#2ecc71",
        "Medium": "#f1c40f",
        "High": "#e67e22",
        "Critical": "#e74c3c",
    }
    _tier_order = ["Low", "Medium", "High", "Critical"]

    import numpy as _np
    # Filter to finite values; dataset has inf/-inf outliers from division by near-zero assets.
    # Clip display range to [-10, 5] — covers >99.9% of observations; extreme outliers excluded.
    _plot_df = df[df["m_score"].notna() & _np.isfinite(df["m_score"])].copy()
    _n_plotted = len(_plot_df)
    _n_clipped = (_plot_df["m_score"] < -10).sum() + (_plot_df["m_score"] > 5).sum()

    fig_distribution = px.histogram(
        _plot_df,
        x="m_score",
        color="risk_tier",
        nbins=80,
        color_discrete_map=_color_map,
        category_orders={"risk_tier": _tier_order},
        title=f"M-Score Distribution — KOSDAQ 2020–2023 (n={_n_plotted:,}; {_n_clipped} extreme outliers excluded)",
        labels={"m_score": "Beneish M-Score", "risk_tier": "Risk Tier"},
        range_x=[-10, 5],
    )
    fig_distribution.add_vline(
        x=-1.78,
        line_dash="dash",
        line_color="black",
        annotation_text="−1.78 threshold",
        annotation_position="top right",
    )
    fig_distribution.update_layout(barmode="stack")
    return fig_distribution


@app.cell
def _chart_risk_sector(df, pd, px):
    _tier_order = ["Low", "Medium", "High", "Critical"]
    _color_map = {
        "Low": "#2ecc71",
        "Medium": "#f1c40f",
        "High": "#e67e22",
        "Critical": "#e74c3c",
    }

    # Only sectors with ≥10 observations
    _sector_counts = df.groupby("wics_sector").size()
    _valid_sectors = _sector_counts[_sector_counts >= 10].index

    _df_sector = df[df["wics_sector"].isin(_valid_sectors)].copy()
    _df_sector["wics_sector"] = _df_sector["wics_sector"].fillna("Unknown")

    _counts = (
        _df_sector.groupby(["wics_sector", "risk_tier"])
        .size()
        .reset_index(name="count")
    )
    _totals = _counts.groupby("wics_sector")["count"].transform("sum")
    _counts["pct"] = _counts["count"] / _totals * 100

    # Order sectors by total flag rate descending
    _flag_rate = (
        _df_sector[_df_sector["flag"]].groupby("wics_sector").size()
        / _df_sector.groupby("wics_sector").size()
    ).sort_values(ascending=False)
    _sector_order = _flag_rate.index.tolist()

    fig_risk_sector = px.bar(
        _counts,
        x="wics_sector",
        y="pct",
        color="risk_tier",
        color_discrete_map=_color_map,
        category_orders={"risk_tier": _tier_order, "wics_sector": _sector_order},
        title="Risk Tier Distribution by WICS Sector (sectors with ≥10 observations)",
        labels={"pct": "Share (%)", "wics_sector": "WICS Sector", "risk_tier": "Risk Tier"},
    )
    fig_risk_sector.update_layout(yaxis_title="Share (%)")
    return fig_risk_sector


@app.cell
def _chart_year_trend(df, pd, px):
    _years = sorted(df["year"].dropna().unique())

    _rows = []
    for _y in _years:
        _sub = df[df["year"] == _y]
        _total = len(_sub)
        _flagged = _sub["flag"].sum()
        _sub_nofp = _sub[~_sub["high_fp_risk"]]
        _flagged_nofp = _sub_nofp["flag"].sum()
        _total_nofp = len(_sub_nofp)
        _rows.append({
            "year": int(_y),
            "All companies": round(_flagged / _total * 100, 1) if _total else 0,
            "Excl. high-FP-risk": round(_flagged_nofp / _total_nofp * 100, 1) if _total_nofp else 0,
        })

    _trend = pd.DataFrame(_rows).melt(id_vars="year", var_name="Series", value_name="Flag rate (%)")

    fig_year_trend = px.line(
        _trend,
        x="year",
        y="Flag rate (%)",
        color="Series",
        markers=True,
        title="Flagged Company Rate by Year (threshold −1.78)",
        labels={"year": "Fiscal Year"},
    )
    fig_year_trend.update_xaxes(tickvals=_years, tickformat="d")
    return fig_year_trend


@app.cell
def _chart_components(df, pd, px):
    _components = ["dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata"]
    _labels = {
        "dsri": "DSRI<br>Receivables growth",
        "gmi": "GMI<br>Gross margin trend",
        "aqi": "AQI<br>Asset quality",
        "sgi": "SGI<br>Sales growth",
        "depi": "DEPI<br>Depreciation rate",
        "sgai": "SGAI<br>SG&A growth",
        "lvgi": "LVGI<br>Leverage change",
        "tata": "TATA<br>Accruals vs. cash",
    }

    _rows = []
    for _flag_val, _label in [(True, "Flagged"), (False, "Not Flagged")]:
        _sub = df[df["flag"] == _flag_val]
        for _col in _components:
            if _col in df.columns:
                _mean = _sub[_col].mean()
                _rows.append({
                    "Component": _labels[_col],
                    "Flagged": _label,
                    "Mean Value": round(_mean, 3) if pd.notna(_mean) else None,
                })

    _comp_df = pd.DataFrame(_rows).dropna(subset=["Mean Value"])

    fig_components = px.bar(
        _comp_df,
        x="Component",
        y="Mean Value",
        color="Flagged",
        barmode="group",
        color_discrete_map={"Flagged": "#e74c3c", "Not Flagged": "#3498db"},
        title="Average Beneish Component Values — Flagged vs. Non-Flagged Companies",
        labels={"Mean Value": "Mean Value (1.0 = neutral)"},
    )
    fig_components.add_hline(
        y=1.0,
        line_dash="dot",
        line_color="gray",
        annotation_text="1.0 neutral",
        annotation_position="top left",
    )
    return fig_components


@app.cell
def _chart_heatmap(df, pd, px):
    # Sectors with ≥10 observations across all years
    _sector_counts = df.groupby("wics_sector").size()
    _valid_sectors = _sector_counts[_sector_counts >= 10].index

    _df_h = df[df["wics_sector"].isin(_valid_sectors) & df["year"].notna()].copy()
    _df_h["year"] = _df_h["year"].astype(int)

    _pivot = (
        _df_h.groupby(["wics_sector", "year"])["flag"]
        .agg(lambda x: round(x.sum() / len(x) * 100, 1))
        .unstack("year")
        .fillna(0)
    )

    # Order rows by average flag rate descending
    _pivot = _pivot.loc[_pivot.mean(axis=1).sort_values(ascending=False).index]
    # Convert year columns to strings so px.imshow treats them as categorical,
    # preventing spurious half-year tick values (2020.5, 2021.5, …).
    _pivot.columns = [str(c) for c in _pivot.columns]

    fig_heatmap = px.imshow(
        _pivot,
        color_continuous_scale=["white", "#ffe5e5", "#ff9999", "#e74c3c"],
        zmin=0,
        zmax=100,
        text_auto=True,
        title="Flag Rate Heatmap by Sector and Year (%)",
        labels={"x": "Fiscal Year", "y": "WICS Sector", "color": "Flag rate (%)"},
        aspect="auto",
    )
    fig_heatmap.update_coloraxes(colorbar_title="Flag rate (%)")
    return fig_heatmap


@app.cell
def _export_html(df, fig_distribution, fig_risk_sector, fig_year_trend, fig_components, fig_heatmap, Path):
    _charts = [
        (fig_distribution, "Chart 1 — M-Score Distribution"),
        (fig_risk_sector,  "Chart 2 — Risk Tier by Sector"),
        (fig_year_trend,   "Chart 3 — Flag Rate by Year"),
        (fig_components,   "Chart 4 — Component Averages: Flagged vs. Non-Flagged"),
        (fig_heatmap,      "Chart 5 — Sector × Year Flag Rate Heatmap"),
    ]

    _html_parts = []
    for _i, (_fig, _title) in enumerate(_charts):
        # First chart: let Plotly inject its own version-specific CDN <script> tag,
        # ensuring the JS version matches the binary data format Python generated.
        # Subsequent charts: False (script already loaded).
        _js = "cdn" if _i == 0 else False
        _html_parts.append(f"<h2>{_title}</h2>")
        _html_parts.append(_fig.to_html(full_html=False, include_plotlyjs=_js))

    _n_companies = df["corp_code"].nunique()
    _n_rows = len(df)
    _n_flagged = df["flag"].sum()

    _full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>KOSDAQ Beneish M-Score Screen — Phase 1 Results</title>
  <style>
    body {{ font-family: sans-serif; max-width: 1200px; margin: auto; padding: 2rem; }}
    h1 {{ color: #333; }}
    h2 {{ color: #555; margin-top: 2.5rem; border-top: 1px solid #eee; padding-top: 1rem; }}
    .meta {{ color: #666; font-size: 0.95rem; margin-bottom: 2rem; }}
    .disclaimer {{ color: #888; font-size: 0.8rem; margin-top: 3rem; border-top: 1px solid #eee; padding-top: 1rem; }}
  </style>
</head>
<body>
  <h1>KOSDAQ Beneish M-Score Screen — Phase 1 Results (2019–2023)</h1>
  <p class="meta">
    {_n_companies:,} companies &nbsp;·&nbsp;
    {_n_rows:,} company-year observations &nbsp;·&nbsp;
    {_n_flagged:,} flagged ({_n_flagged/_n_rows*100:.1f}%) &nbsp;·&nbsp;
    Threshold: −1.78 (8-variable model)
  </p>
  {''.join(_html_parts)}
  <p class="disclaimer">
    Generated by <a href="https://github.com/pon00050/kr-forensic-finance">kr-forensic-finance</a>.
    Outputs are anomaly hypotheses for human review — <strong>not fraud conclusions</strong>.
    False positive rate ~40%. Not investment advice or legal opinion.
  </p>
</body>
</html>"""

    _out = Path(__file__).parent / "beneish_viz.html"
    _out.write_text(_full_html, encoding="utf-8")
    print(f"Wrote {_out} ({_out.stat().st_size / 1024:.0f} KB)")
    return


if __name__ == "__main__":
    app.run()
