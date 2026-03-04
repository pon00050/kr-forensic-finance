"""Standalone chart functions extracted from 03_Analysis/beneish_viz.py.

beneish_viz.py Marimo cells call these functions so the same logic is importable
from cli.py, FastAPI, or any future caller without going through the Marimo runtime.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def chart_distribution(df: pd.DataFrame) -> go.Figure:
    """M-Score histogram coloured by risk tier."""
    color_map = {
        "Low": "#2ecc71",
        "Medium": "#f1c40f",
        "High": "#e67e22",
        "Critical": "#e74c3c",
    }
    tier_order = ["Low", "Medium", "High", "Critical"]

    plot_df = df[df["m_score"].notna() & np.isfinite(df["m_score"])].copy()
    n_plotted = len(plot_df)
    n_clipped = (plot_df["m_score"] < -10).sum() + (plot_df["m_score"] > 5).sum()

    fig = px.histogram(
        plot_df,
        x="m_score",
        color="risk_tier",
        nbins=80,
        color_discrete_map=color_map,
        category_orders={"risk_tier": tier_order},
        title=(
            f"M-Score Distribution — KOSDAQ 2020–2023 "
            f"(n={n_plotted:,}; {n_clipped} extreme outliers excluded)"
        ),
        labels={"m_score": "Beneish M-Score", "risk_tier": "Risk Tier"},
        range_x=[-10, 5],
    )
    fig.add_vline(
        x=-1.78,
        line_dash="dash",
        line_color="black",
        annotation_text="−1.78 threshold",
        annotation_position="top right",
    )
    fig.update_layout(barmode="stack")
    return fig


def chart_risk_sector(df: pd.DataFrame) -> go.Figure:
    """Stacked bar: risk tier share by WICS sector (sectors ≥10 observations)."""
    tier_order = ["Low", "Medium", "High", "Critical"]
    color_map = {
        "Low": "#2ecc71",
        "Medium": "#f1c40f",
        "High": "#e67e22",
        "Critical": "#e74c3c",
    }

    sector_counts = df.groupby("wics_sector").size()
    valid_sectors = sector_counts[sector_counts >= 10].index

    df_sector = df[df["wics_sector"].isin(valid_sectors)].copy()
    df_sector["wics_sector"] = df_sector["wics_sector"].fillna("Unknown")

    counts = (
        df_sector.groupby(["wics_sector", "risk_tier"])
        .size()
        .reset_index(name="count")
    )
    totals = counts.groupby("wics_sector")["count"].transform("sum")
    counts["pct"] = counts["count"] / totals * 100

    flag_rate = (
        df_sector[df_sector["flag"]].groupby("wics_sector").size()
        / df_sector.groupby("wics_sector").size()
    ).sort_values(ascending=False)
    sector_order = flag_rate.index.tolist()

    fig = px.bar(
        counts,
        x="wics_sector",
        y="pct",
        color="risk_tier",
        color_discrete_map=color_map,
        category_orders={"risk_tier": tier_order, "wics_sector": sector_order},
        title="Risk Tier Distribution by WICS Sector (sectors with ≥10 observations)",
        labels={"pct": "Share (%)", "wics_sector": "WICS Sector", "risk_tier": "Risk Tier"},
    )
    fig.update_layout(yaxis_title="Share (%)")
    return fig


def chart_year_trend(df: pd.DataFrame) -> go.Figure:
    """Line chart: flagged-company rate by year (all companies vs. excl. high-FP-risk)."""
    years = sorted(df["year"].dropna().unique())

    rows = []
    for y in years:
        sub = df[df["year"] == y]
        total = len(sub)
        flagged = sub["flag"].sum()
        sub_nofp = sub[~sub["high_fp_risk"]]
        flagged_nofp = sub_nofp["flag"].sum()
        total_nofp = len(sub_nofp)
        rows.append({
            "year": int(y),
            "All companies": round(flagged / total * 100, 1) if total else 0,
            "Excl. high-FP-risk": round(flagged_nofp / total_nofp * 100, 1) if total_nofp else 0,
        })

    trend = pd.DataFrame(rows).melt(id_vars="year", var_name="Series", value_name="Flag rate (%)")

    fig = px.line(
        trend,
        x="year",
        y="Flag rate (%)",
        color="Series",
        markers=True,
        title="Flagged Company Rate by Year (threshold −1.78)",
        labels={"year": "Fiscal Year"},
    )
    fig.update_xaxes(tickvals=years, tickformat="d")
    return fig


def chart_components(df: pd.DataFrame) -> go.Figure:
    """Grouped bar: median Beneish component values, flagged vs. non-flagged."""
    components = ["dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata"]
    labels = {
        "dsri": "DSRI<br>Receivables growth",
        "gmi": "GMI<br>Gross margin trend",
        "aqi": "AQI<br>Asset quality",
        "sgi": "SGI<br>Sales growth",
        "depi": "DEPI<br>Depreciation rate",
        "sgai": "SGAI<br>SG&A growth",
        "lvgi": "LVGI<br>Leverage change",
        "tata": "TATA<br>Accruals vs. cash",
    }

    rows = []
    for flag_val, label in [(True, "Flagged"), (False, "Not Flagged")]:
        sub = df[df["flag"] == flag_val]
        for col in components:
            if col in df.columns:
                median = sub[col].median()
                rows.append({
                    "Component": labels[col],
                    "Flagged": label,
                    "Median Value": round(median, 3) if pd.notna(median) else None,
                })

    comp_df = pd.DataFrame(rows).dropna(subset=["Median Value"])

    fig = px.bar(
        comp_df,
        x="Component",
        y="Median Value",
        color="Flagged",
        barmode="group",
        color_discrete_map={"Flagged": "#e74c3c", "Not Flagged": "#3498db"},
        title=(
            "Median Beneish Component Values — Flagged vs. Non-Flagged Companies"
            "<br><sup>Median used — component distributions (especially SGI) have extreme outliers "
            "that distort means. 1.0 = neutral (no change year-over-year)</sup>"
        ),
        labels={"Median Value": "Median Value (1.0 = neutral)"},
    )
    fig.add_hline(
        y=1.0,
        line_dash="dot",
        line_color="gray",
        annotation_text="1.0 neutral",
        annotation_position="top left",
    )
    return fig


def chart_heatmap(df: pd.DataFrame) -> go.Figure:
    """Heatmap: flag rate (%) by WICS sector × fiscal year."""
    sector_counts = df.groupby("wics_sector").size()
    valid_sectors = sector_counts[sector_counts >= 10].index

    df_h = df[df["wics_sector"].isin(valid_sectors) & df["year"].notna()].copy()
    df_h["year"] = df_h["year"].astype(int)

    pivot = (
        df_h.groupby(["wics_sector", "year"])["flag"]
        .agg(lambda x: round(x.sum() / len(x) * 100, 1))
        .unstack("year")
        .fillna(0)
    )

    pivot = pivot.loc[pivot.mean(axis=1).sort_values(ascending=False).index]
    # Convert year columns to strings so px.imshow treats them as categorical,
    # preventing spurious half-year tick values (2020.5, 2021.5, …).
    pivot.columns = [str(c) for c in pivot.columns]

    fig = px.imshow(
        pivot,
        color_continuous_scale=["white", "#ffe5e5", "#ff9999", "#e74c3c"],
        zmin=0,
        zmax=100,
        text_auto=True,
        title="Flag Rate Heatmap by Sector and Year (%)",
        labels={"x": "Fiscal Year", "y": "WICS Sector", "color": "Flag rate (%)"},
        aspect="auto",
    )
    fig.update_coloraxes(colorbar_title="Flag rate (%)")
    return fig


def export_html(figures: list[tuple[go.Figure, str]], df: pd.DataFrame, out_path: Path) -> Path:
    """Assemble figures into a self-contained HTML file and write to out_path."""
    html_parts = []
    for i, (fig, title) in enumerate(figures):
        # First chart: let Plotly inject its own version-specific CDN <script> tag,
        # ensuring the JS version matches the binary data format Python generated.
        # Subsequent charts: False (script already loaded).
        js = "cdn" if i == 0 else False
        html_parts.append(f"<h2>{title}</h2>")
        html_parts.append(fig.to_html(full_html=False, include_plotlyjs=js))

    n_companies = df["corp_code"].nunique()
    n_rows = len(df)
    n_flagged = int(df["flag"].sum())

    full_html = f"""<!DOCTYPE html>
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
    {n_companies:,} companies &nbsp;·&nbsp;
    {n_rows:,} company-year observations &nbsp;·&nbsp;
    {n_flagged:,} flagged ({n_flagged / n_rows * 100:.1f}%) &nbsp;·&nbsp;
    Threshold: −1.78 (8-variable model)
  </p>
  {''.join(html_parts)}
  <p class="disclaimer">
    Generated by <a href="https://github.com/pon00050/kr-forensic-finance">kr-forensic-finance</a>.
    Outputs are anomaly hypotheses for human review — <strong>not fraud conclusions</strong>.
    False positive rate ~40%. Not investment advice or legal opinion.
  </p>
</body>
</html>"""

    out_path = Path(out_path)
    out_path.write_text(full_html, encoding="utf-8")
    return out_path


def generate_charts(df: pd.DataFrame, output_dir: str | Path) -> Path:
    """Generate all Beneish viz charts and write beneish_viz.html. Returns path."""
    figures = [
        (chart_distribution(df),  "Chart 1 — M-Score Distribution"),
        (chart_risk_sector(df),   "Chart 2 — Risk Tier by Sector"),
        (chart_year_trend(df),    "Chart 3 — Flag Rate by Year"),
        (chart_components(df),    "Chart 4 — Component Medians: Flagged vs. Non-Flagged"),
        (chart_heatmap(df),       "Chart 5 — Sector × Year Flag Rate Heatmap"),
    ]
    out_path = Path(output_dir) / "beneish_viz.html"
    return export_html(figures, df, out_path)


__all__ = [
    "chart_distribution",
    "chart_risk_sector",
    "chart_year_trend",
    "chart_components",
    "chart_heatmap",
    "export_html",
    "generate_charts",
]
