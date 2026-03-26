"""src/report.py — per-company forensic HTML report generator.

Usage:
    from krff.report import generate_report
    path = generate_report("01051092", skip_claude=True)
    path = generate_report("01051092")  # with Claude synthesis if ANTHROPIC_API_KEY set
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import logging
import pandas as pd

log = logging.getLogger(__name__)

# ─── Path constants ────────────────────────────────────────────────────────────
from krff._paths import PROJECT_ROOT as _PROJECT_ROOT
from krff import data_access as _da

_REPORTS_DIR = _PROJECT_ROOT / "03_Analysis" / "reports"

_FORBIDDEN_MODEL  = "claude-opus-4-6"
_SYNTHESIS_MODEL  = "claude-sonnet-4-6"
_BENEISH_COMPONENTS = ["dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata"]
_RISK_TIER_ORDER  = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

__all__ = [
    "generate_report",
    "get_company_summary",
    "get_report_html",
    "build_company_summary",
    "build_report_html",
    "synthesize_with_claude",
    "chart_mscore_trend",
    "chart_component_bar",
    "chart_cb_bw_timeline",
    "chart_timing_anomalies",
]


# ─── Chart functions ───────────────────────────────────────────────────────────

def _empty_figure(msg: str = "No data available."):
    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_annotation(
        text=msg,
        xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(size=14, color="#888"),
    )
    fig.update_layout(height=300)
    return fig


def chart_mscore_trend(df: pd.DataFrame):
    """Line + markers: X=year, Y=m_score, dashed hline at -1.78."""
    if df.empty or "m_score" not in df.columns or df["m_score"].isna().all():
        return _empty_figure()
    plot_df = df[df["m_score"].notna()].copy()
    if plot_df.empty:
        return _empty_figure()
    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=plot_df["year"],
        y=plot_df["m_score"],
        mode="lines+markers",
        name="M-Score",
        line=dict(color="#e74c3c"),
        marker=dict(size=8),
    ))
    fig.add_hline(
        y=-1.78,
        line_dash="dash",
        line_color="black",
        annotation_text="−1.78 threshold",
        annotation_position="top right",
    )
    fig.update_layout(
        title="Beneish M-Score Trend",
        xaxis_title="Fiscal Year",
        yaxis_title="M-Score",
        height=350,
    )
    return fig


def chart_component_bar(df: pd.DataFrame):
    """Horizontal bar: most recent year with ≥1 non-NaN component; red if >1.0."""
    if df.empty:
        return _empty_figure()
    components = [c for c in _BENEISH_COMPONENTS if c in df.columns]
    if not components:
        return _empty_figure()
    valid_rows = df[df[components].notna().any(axis=1)]
    if valid_rows.empty:
        return _empty_figure()
    row = valid_rows.sort_values("year").iloc[-1]
    labels = [c.upper() for c in components if pd.notna(row.get(c))]
    values = [float(row[c]) for c in components if pd.notna(row.get(c))]
    if not values:
        return _empty_figure()
    colors = ["#e74c3c" if v > 1.0 else "#4682b4" for v in values]
    import plotly.graph_objects as go
    fig = go.Figure(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker_color=colors,
    ))
    fig.add_vline(
        x=1.0, line_dash="dash", line_color="black",
        annotation_text="1.0", annotation_position="top right",
    )
    year = int(row["year"]) if pd.notna(row.get("year")) else "N/A"
    fig.update_layout(
        title=f"Beneish Components — {year}",
        xaxis_title="Component Value",
        height=350,
    )
    return fig


def chart_cb_bw_timeline(df: pd.DataFrame):
    """Scatter: X=issue_date, Y=volume_ratio, marker size=flag_count, color=bond_type."""
    if df.empty or "issue_date" not in df.columns:
        return _empty_figure()
    import plotly.graph_objects as go
    plot_df = df.copy()
    fig = go.Figure()
    if "bond_type" in plot_df.columns:
        for bond_type in plot_df["bond_type"].unique():
            subset = plot_df[plot_df["bond_type"] == bond_type]
            sizes = (
                (subset["flag_count"].fillna(1) * 8 + 8).tolist()
                if "flag_count" in subset.columns
                else [12] * len(subset)
            )
            y_vals = (
                subset["volume_ratio"].fillna(1.0).tolist()
                if "volume_ratio" in subset.columns
                else [1.0] * len(subset)
            )
            fig.add_trace(go.Scatter(
                x=subset["issue_date"],
                y=y_vals,
                mode="markers",
                name=str(bond_type),
                marker=dict(size=sizes, opacity=0.7),
            ))
    else:
        sizes = (
            (plot_df["flag_count"].fillna(1) * 8 + 8).tolist()
            if "flag_count" in plot_df.columns
            else [12] * len(plot_df)
        )
        y_vals = (
            plot_df["volume_ratio"].fillna(1.0).tolist()
            if "volume_ratio" in plot_df.columns
            else [1.0] * len(plot_df)
        )
        fig.add_trace(go.Scatter(
            x=plot_df["issue_date"],
            y=y_vals,
            mode="markers",
            marker=dict(size=sizes, opacity=0.7),
            name="Events",
        ))
    fig.update_layout(
        title="CB/BW Issuance Events",
        xaxis_title="Issue Date",
        yaxis_title="Volume Ratio",
        height=350,
    )
    return fig


def chart_timing_anomalies(df: pd.DataFrame):
    """Scatter: X=filing_date, Y=price_change_pct, color=flag, size=volume_ratio (capped 10x)."""
    if df.empty or "filing_date" not in df.columns:
        return _empty_figure()
    import plotly.graph_objects as go
    plot_df = df.copy()
    price_col = "price_change_pct" if "price_change_pct" in plot_df.columns else None
    if price_col is None:
        return _empty_figure()
    vol_col = "volume_ratio" if "volume_ratio" in plot_df.columns else None
    sizes = (
        (plot_df[vol_col].fillna(1.0).clip(upper=10.0) * 4 + 6).tolist()
        if vol_col else [10] * len(plot_df)
    )
    flag_col = "flag" if "flag" in plot_df.columns else None
    colors = (
        ["#e74c3c" if bool(f) else "#4682b4" for f in plot_df[flag_col]]
        if flag_col else ["#4682b4"] * len(plot_df)
    )
    fig = go.Figure(go.Scatter(
        x=plot_df["filing_date"],
        y=plot_df[price_col],
        mode="markers",
        marker=dict(size=sizes, color=colors, opacity=0.7),
        name="Disclosures",
    ))
    fig.update_layout(
        title="Timing Anomalies",
        xaxis_title="Filing Date",
        yaxis_title="Price Change (%)",
        height=350,
    )
    return fig


# ─── HTML helpers ──────────────────────────────────────────────────────────────

def _df_to_html_table(
    df: pd.DataFrame,
    columns: list[str],
    formatters: dict | None = None,
) -> str:
    if df.empty or not columns:
        return ""
    available = [c for c in columns if c in df.columns]
    if not available:
        return ""
    subset = df[available].copy()
    if formatters:
        for col, fmt in formatters.items():
            if col in subset.columns:
                subset[col] = subset[col].apply(fmt)
    return subset.to_html(index=False, escape=False, classes="data-table", border=0)


def _risk_badge(risk_tier: str) -> str:
    colors = {"Critical": "#e74c3c", "High": "#e67e22", "Medium": "#f1c40f", "Low": "#2ecc71"}
    text_c = {"Critical": "white", "High": "white", "Medium": "#333", "Low": "#333"}
    c = colors.get(risk_tier, "#ccc")
    t = text_c.get(risk_tier, "#333")
    return (
        f'<span style="background:{c};color:{t};padding:3px 10px;'
        f'border-radius:4px;font-weight:bold;">{risk_tier}</span>'
    )


def _severity_badge(severity: str) -> str:
    colors = {"low": "#f1c40f", "medium": "#e67e22", "high": "#e74c3c"}
    text_c = {"low": "#333", "medium": "white", "high": "white"}
    c = colors.get(severity, "#ccc")
    t = text_c.get(severity, "#333")
    return (
        f'<span class="badge-{severity}" style="background:{c};color:{t};'
        f'padding:2px 8px;border-radius:4px;font-size:0.85rem;">{severity}</span>'
    )


def _highest_risk_tier(beneish_df: pd.DataFrame) -> str:
    if beneish_df.empty or "risk_tier" not in beneish_df.columns:
        return "Unknown"
    tiers = beneish_df["risk_tier"].dropna().tolist()
    if not tiers:
        return "Unknown"
    return max(tiers, key=lambda t: _RISK_TIER_ORDER.get(t, 0))


def build_company_summary(
    corp_code: str,
    company_name: str,
    ticker: str,
    beneish_df: pd.DataFrame,
    cb_bw_df: pd.DataFrame,
    timing_df: pd.DataFrame,
    network_df: pd.DataFrame,
) -> dict:
    beneish_years = []
    if not beneish_df.empty:
        for _, row in beneish_df.iterrows():
            if pd.notna(row.get("m_score")):
                beneish_years.append({
                    "year": int(row["year"]),
                    "m_score": float(row["m_score"]),
                    "risk_tier": str(row.get("risk_tier", "")),
                    "flag": bool(row.get("flag", False)),
                })

    cb_bw_count = len(cb_bw_df)
    cb_bw_flagged = (
        int(cb_bw_df["flag_count"].gt(0).sum())
        if not cb_bw_df.empty and "flag_count" in cb_bw_df.columns
        else 0
    )
    cb_bw_max_flags = (
        int(cb_bw_df["flag_count"].max())
        if not cb_bw_df.empty and "flag_count" in cb_bw_df.columns
        else 0
    )
    cb_bw_flag_types: list[str] = []
    if not cb_bw_df.empty and "flags" in cb_bw_df.columns:
        for flags_str in cb_bw_df["flags"].dropna():
            cb_bw_flag_types.extend(f.strip() for f in str(flags_str).split("|") if f.strip())
        cb_bw_flag_types = list(set(cb_bw_flag_types))

    timing_count = len(timing_df)
    timing_flagged = (
        int(timing_df["flag"].sum())
        if not timing_df.empty and "flag" in timing_df.columns
        else 0
    )

    centrality = None
    in_multiple = False
    if not network_df.empty:
        if "betweenness_centrality" in network_df.columns:
            centrality = float(network_df["betweenness_centrality"].max())
        if "company_count" in network_df.columns:
            in_multiple = bool((network_df["company_count"] > 1).any())

    return {
        "corp_code": corp_code,
        "company_name": company_name,
        "ticker": ticker,
        "beneish_years": beneish_years,
        "cb_bw_count": cb_bw_count,
        "cb_bw_flagged_count": cb_bw_flagged,
        "cb_bw_max_flags": cb_bw_max_flags,
        "cb_bw_flag_types": cb_bw_flag_types,
        "timing_anomaly_count": timing_count,
        "timing_flagged_count": timing_flagged,
        "officer_network_centrality": centrality,
        "officer_network_appears_in_multiple": in_multiple,
    }


def build_report_html(
    *,
    corp_code: str,
    company_name: str,
    ticker: str,
    beneish_df: pd.DataFrame,
    cb_bw_df: pd.DataFrame,
    timing_df: pd.DataFrame,
    network_df: pd.DataFrame,
    holdings_df: pd.DataFrame,
    fig_mscore: go.Figure,
    fig_components: go.Figure,
    fig_cb_bw: go.Figure,
    fig_timing: go.Figure,
    flags: list[dict],
    skip_claude: bool,
    cb_bw_csv_exists: bool,
    timing_csv_exists: bool,
    network_csv_exists: bool,
) -> str:
    generated_date = date.today().isoformat()
    dart_link = f"https://dart.fss.or.kr/dsearch/main.do?option=corp&textCrpNm={company_name}"

    highest_tier = _highest_risk_tier(beneish_df)
    risk_badge_html = _risk_badge(highest_tier) if highest_tier != "Unknown" else ""

    # Chart HTML fragments — CDN on first, False on rest (same pattern as src/charts.py)
    fig_mscore_html     = fig_mscore.to_html(full_html=False, include_plotlyjs="cdn")
    fig_components_html = fig_components.to_html(full_html=False, include_plotlyjs=False)
    fig_cb_bw_html      = fig_cb_bw.to_html(full_html=False, include_plotlyjs=False)
    fig_timing_html     = fig_timing.to_html(full_html=False, include_plotlyjs=False)

    # ── Section 2: Beneish M-Score History ──────────────────────────────────────
    beneish_cols = ["year", "m_score", "risk_tier", "flag", "sector_percentile"]
    beneish_table = _df_to_html_table(beneish_df, beneish_cols, {
        "m_score": lambda v: f"{v:.4f}" if pd.notna(v) else "N/A",
        "flag": lambda v: "&#10003;" if v else "–",
        "sector_percentile": lambda v: f"{v:.1f}" if pd.notna(v) else "N/A",
    })
    if beneish_table:
        sec2_content = f"{beneish_table}\n<div class='chart'>{fig_mscore_html}</div>"
    else:
        sec2_content = (
            "<p class='note'>No Beneish M-Score data found for this company. "
            "Run <code>python 03_Analysis/beneish_screen.py</code> first.</p>"
            f"\n<div class='chart'>{fig_mscore_html}</div>"
        )

    # ── Section 3: Beneish Components ───────────────────────────────────────────
    _comp_interps = {
        "dsri": "Days Sales in Receivables Index (>1 = receivables grew faster than revenue)",
        "gmi":  "Gross Margin Index (>1 = gross margins declined)",
        "aqi":  "Asset Quality Index (>1 = non-current non-PP&E assets grew)",
        "sgi":  "Sales Growth Index (>1 = revenue grew)",
        "depi": "Depreciation Index (>1 = lower depreciation rate)",
        "sgai": "SG&A Index (>1 = SG&A grew faster than revenue)",
        "lvgi": "Leverage Growth Index (>1 = more leverage)",
        "tata": "Total Accruals to Total Assets (positive = accrual-heavy earnings)",
    }
    components_in_df = [c for c in _BENEISH_COMPONENTS if c in beneish_df.columns] if not beneish_df.empty else []
    if components_in_df:
        valid_rows = beneish_df[beneish_df[components_in_df].notna().any(axis=1)]
        if not valid_rows.empty:
            row = valid_rows.sort_values("year").iloc[-1]
            comp_data = [
                {
                    "component": c.upper(),
                    "value": f"{float(row[c]):.4f}",
                    "interpretation": _comp_interps.get(c, ""),
                }
                for c in _BENEISH_COMPONENTS
                if c in row.index and pd.notna(row[c])
            ]
            comp_df = pd.DataFrame(comp_data)
            comp_table = _df_to_html_table(comp_df, ["component", "value", "interpretation"])
            sec3_content = (
                f"<div class='chart'>{fig_components_html}</div>\n{comp_table}"
            )
        else:
            sec3_content = (
                f"<p class='note'>No component data available.</p>"
                f"\n<div class='chart'>{fig_components_html}</div>"
            )
    else:
        sec3_content = (
            f"<p class='note'>No component data available.</p>"
            f"\n<div class='chart'>{fig_components_html}</div>"
        )

    # ── Section 4: CB/BW Events ──────────────────────────────────────────────────
    if not cb_bw_csv_exists:
        sec4_note    = (
            "<p class='note'>Run "
            "<code>python 03_Analysis/run_cb_bw_timelines.py</code> first to generate CB/BW summary.</p>"
        )
        cb_bw_summary_html = ""
        cb_bw_table        = ""
    elif cb_bw_df.empty:
        sec4_note    = "<p class='note'>No CB/BW events found for this company.</p>"
        cb_bw_summary_html = ""
        cb_bw_table        = ""
    else:
        n_events  = len(cb_bw_df)
        n_flagged = (
            int(cb_bw_df["flag_count"].gt(0).sum())
            if "flag_count" in cb_bw_df.columns else 0
        )
        cb_bw_summary_html = f"<p class='summary'>{n_events} events, {n_flagged} flagged</p>"
        sec4_note  = ""
        cb_bw_cols = [
            "issue_date", "bond_type", "exercise_price", "flag_count",
            "flags", "peak_date", "volume_ratio", "dart_link",
        ]
        cb_bw_table = _df_to_html_table(cb_bw_df, cb_bw_cols, {
            "volume_ratio": lambda v: f"{v:.2f}" if pd.notna(v) else "N/A",
            "exercise_price": lambda v: f"{int(v):,}" if pd.notna(v) else "N/A",
            "dart_link": lambda v: (
                f'<a href="{v}" target="_blank">DART</a>'
                if pd.notna(v) and str(v).startswith("http") else ""
            ),
        })

    sec4_content = (
        f"{cb_bw_summary_html}\n{cb_bw_table}\n{sec4_note}"
        f"\n<div class='chart'>{fig_cb_bw_html}</div>"
    )

    # ── Section 5: Timing Anomalies ──────────────────────────────────────────────
    if not timing_csv_exists:
        sec5_note        = (
            "<p class='note'>Run "
            "<code>python 03_Analysis/run_timing_anomalies.py</code> first.</p>"
        )
        timing_summary_html = ""
        timing_table        = ""
    elif timing_df.empty:
        sec5_note        = "<p class='note'>No timing anomalies found for this company.</p>"
        timing_summary_html = ""
        timing_table        = ""
    else:
        n_total    = len(timing_df)
        n_flag_t   = int(timing_df["flag"].sum()) if "flag" in timing_df.columns else 0
        timing_summary_html = f"<p class='summary'>{n_total} disclosures, {n_flag_t} anomalous</p>"
        sec5_note  = ""
        timing_cols = [
            "filing_date", "timing", "title",
            "price_change_pct", "volume_ratio", "flag", "dart_link",
        ]
        timing_table = _df_to_html_table(timing_df, timing_cols, {
            "price_change_pct": lambda v: f"{v:.2f}%" if pd.notna(v) else "N/A",
            "volume_ratio": lambda v: f"{v:.2f}" if pd.notna(v) else "N/A",
            "flag": lambda v: "&#10003;" if v else "–",
            "dart_link": lambda v: (
                f'<a href="{v}" target="_blank">DART</a>'
                if pd.notna(v) and str(v).startswith("http") else ""
            ),
        })

    sec5_content = (
        f"{timing_summary_html}\n{timing_table}\n{sec5_note}"
        f"\n<div class='chart'>{fig_timing_html}</div>"
    )

    # ── Section 6: Officer Network ────────────────────────────────────────────────
    if not network_csv_exists:
        sec6a_content = (
            "<p class='note'>Run "
            "<code>python 03_Analysis/run_officer_network.py</code> first.</p>"
        )
    elif network_df.empty:
        sec6a_content = (
            "<p class='note'>No officers from this company appear in the cross-company network.</p>"
        )
    else:
        net_cols = [
            "person_name", "company_count", "flagged_company_count", "betweenness_centrality",
        ]
        network_table = _df_to_html_table(network_df, net_cols, {
            "betweenness_centrality": lambda v: f"{v:.6f}" if pd.notna(v) else "N/A",
        })
        sec6a_content = (
            network_table
            or "<p class='note'>No officer network data for this company.</p>"
        )

    if holdings_df.empty:
        sec6b_content = "<p class='note'>No officer holding change data found for this company.</p>"
    else:
        _expected_holding_cols = ["corp_code", "person_name", "position", "shares_held", "change_date"]
        avail_holding = [c for c in _expected_holding_cols if c in holdings_df.columns]
        if not avail_holding:
            avail_holding = list(holdings_df.columns)
        holdings_table = _df_to_html_table(holdings_df, avail_holding)
        sec6b_content = holdings_table or "<p class='note'>No officer holding data available.</p>"

    # ── Section 7: Claude Synthesis ────────────────────────────────────────────
    if skip_claude:
        sec7_content = "<p class='note'>AI synthesis skipped (--skip-claude).</p>"
    elif not os.getenv("ANTHROPIC_API_KEY"):
        sec7_content = (
            "<p class='note'>Set <code>ANTHROPIC_API_KEY</code> in <code>.env</code> "
            "to enable Claude synthesis.</p>"
        )
    elif not flags:
        sec7_content = "<p class='note'>No anomalies identified by model.</p>"
    else:
        rows_html = ""
        for f in flags:
            severity  = f.get("severity", "low")
            badge     = _severity_badge(severity)
            flag_type = f.get("flag_type", "")
            quote     = f.get("source_quote", "").replace("<", "&lt;").replace(">", "&gt;")
            rows_html += f"<tr><td>{quote}</td><td>{flag_type}</td><td>{badge}</td></tr>\n"
        sec7_content = (
            "<table class='data-table'>"
            "<thead><tr>"
            "<th>Source / Observation</th><th>Flag Type</th><th>Severity</th>"
            "</tr></thead>"
            f"<tbody>\n{rows_html}</tbody></table>"
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>{company_name} ({corp_code}) — Forensic Report</title>
  <style>
    body {{ font-family: sans-serif; max-width: 1200px; margin: auto; padding: 2rem; }}
    h1 {{ color: #333; }}
    h2 {{ color: #555; margin-top: 2.5rem; border-top: 1px solid #eee; padding-top: 1rem; }}
    h3 {{ color: #666; margin-top: 1.5rem; }}
    .meta {{ color: #666; font-size: 0.95rem; margin-bottom: 1rem; }}
    .summary {{ color: #333; font-weight: bold; margin: 0.5rem 0; }}
    .note {{ color: #888; font-style: italic; }}
    .chart {{ margin: 1rem 0; }}
    .ai-disclaimer {{
      background: #fff3cd; border: 1px solid #ffc107;
      border-radius: 4px; padding: 0.75rem 1rem; margin: 1rem 0; font-size: 0.9rem;
    }}
    .data-table {{
      border-collapse: collapse; width: 100%; font-size: 0.9rem; margin: 1rem 0;
    }}
    .data-table th {{
      background: #f5f5f5; text-align: left;
      padding: 6px 10px; border-bottom: 2px solid #ddd;
    }}
    .data-table td {{ padding: 5px 10px; border-bottom: 1px solid #eee; }}
    .data-table tr:hover {{ background: #fafafa; }}
    .disclaimer {{
      color: #888; font-size: 0.8rem;
      margin-top: 3rem; border-top: 1px solid #eee; padding-top: 1rem;
    }}
  </style>
</head>
<body>

  <!-- Section 1: Header -->
  <h1>{company_name} <small style="font-size:0.6em;color:#888;">({ticker})</small> {risk_badge_html}</h1>
  <p class="meta">
    corp_code: {corp_code} &nbsp;&middot;&nbsp;
    <a href="{dart_link}" target="_blank">DART 검색</a> &nbsp;&middot;&nbsp;
    Generated: {generated_date} &nbsp;&middot;&nbsp;
    Data range: 2019&ndash;2023
  </p>

  <!-- Section 2: Beneish M-Score History -->
  <h2>Beneish M-Score History</h2>
  {sec2_content}

  <!-- Section 3: Beneish Components -->
  <h2>Beneish Components (Most Recent Year)</h2>
  {sec3_content}

  <!-- Section 4: CB/BW Events -->
  <h2>CB/BW Events</h2>
  {sec4_content}

  <!-- Section 5: Timing Anomalies -->
  <h2>Timing Anomalies</h2>
  {sec5_content}

  <!-- Section 6: Officer Network -->
  <h2>Officer Network</h2>
  <h3>Officers in Multiple Flagged Companies</h3>
  {sec6a_content}
  <h3>Officer Holding Changes</h3>
  {sec6b_content}

  <!-- Section 7: Claude Synthesis -->
  <h2>Claude Synthesis</h2>
  <div class="ai-disclaimer">
    &#9888; <strong>Hypothesis candidates only.</strong>
    These are anomaly flags for human review &mdash;
    <em>not fraud conclusions</em>, not investment advice.
  </div>
  {sec7_content}

  <p class="disclaimer">
    Generated by
    <a href="https://github.com/pon00050/kr-forensic-finance">kr-forensic-finance</a>.
    Outputs are anomaly hypotheses for human review &mdash;
    <strong>not fraud conclusions</strong>.
    False positive rate ~40%. Not investment advice or legal opinion.
  </p>
</body>
</html>"""


# ─── Claude synthesis ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a Korean financial anomaly detection assistant. Your role is inconsistency "
    "flagging only — not fraud conclusions, not investment advice. Given structured data "
    "about a Korean listed company, identify factual inconsistencies and anomalous patterns. "
    'Output only a JSON array. Each element: {"source_quote": str, "flag_type": str, '
    '"severity": "low"|"medium"|"high"}. '
    "severity must be one of: low, medium, high. Return [] if no anomalies found."
)


def _make_anthropic_client():
    """Return an anthropic.Anthropic client instance. Extracted for testability."""
    import anthropic
    return anthropic.Anthropic()


def synthesize_with_claude(company_summary: dict) -> list[dict]:
    """Call Claude API to flag anomalies in the company summary.

    Returns list of {"source_quote": str, "flag_type": str, "severity": str} dicts.
    Returns [] on any error, missing API key, or missing package.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return []

    try:
        import anthropic  # noqa: F401 — lazy import; top-level import breaks CI
    except ImportError:
        print("anthropic package not installed; skipping synthesis", file=sys.stderr)
        return []

    _model = _SYNTHESIS_MODEL
    if _model == _FORBIDDEN_MODEL:
        raise ValueError(f"Model {_model!r} is forbidden per CLAUDE.md routing rules")

    try:
        client = _make_anthropic_client()
        response = client.messages.create(
            model=_model,
            max_tokens=1024,
            system=[{
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": json.dumps(company_summary, ensure_ascii=False, default=str),
            }],
        )
        text = response.content[0].text
        # Strip markdown code fences before parsing
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        text = text.strip()
        data = json.loads(text)
        result = []
        for item in data:
            if (
                isinstance(item, dict)
                and "source_quote" in item
                and "flag_type" in item
                and "severity" in item
                and item["severity"] in ("low", "medium", "high")
            ):
                result.append(item)
        return result
    except Exception as e:
        print(f"Claude synthesis failed: {e}", file=sys.stderr)
        return []


# ─── Public API for FastAPI ────────────────────────────────────────────────────

def get_company_summary(corp_code: str) -> dict:
    """Load all data for one company and return structured summary dict.

    Returns dict validating against models.CompanySummary.
    """
    corp_code = corp_code.zfill(8)
    beneish_df = _da.load_parquet("beneish_scores.parquet", corp_code, sort_by="year")
    company_name = _da.load_company_name(corp_code, beneish_df)
    cb_bw_df = _da.load_csv(_da.CB_BW_CSV, corp_code)
    timing_df = _da.load_csv(_da.TIMING_CSV, corp_code)
    network_df = _da.load_officer_network(corp_code)
    ticker = ""
    if not beneish_df.empty and "ticker" in beneish_df.columns:
        t = beneish_df["ticker"].iloc[0]
        if pd.notna(t):
            ticker = str(t)
    return build_company_summary(
        corp_code, company_name, ticker,
        beneish_df, cb_bw_df, timing_df, network_df,
    )


def get_report_html(corp_code: str, skip_claude: bool = True) -> str:
    """Generate HTML report string without writing to disk.

    Returns the full HTML string. Shares chart + summary building
    with generate_report().
    """
    corp_code = corp_code.zfill(8)

    beneish_df = _da.load_parquet("beneish_scores.parquet", corp_code, sort_by="year")
    company_name = _da.load_company_name(corp_code, beneish_df)

    cb_bw_df = _da.load_csv(_da.CB_BW_CSV, corp_code)
    timing_df = _da.load_csv(_da.TIMING_CSV, corp_code)
    network_df = _da.load_officer_network(corp_code)
    holdings_df = _da.load_parquet("officer_holdings.parquet", corp_code)

    ticker = ""
    if not beneish_df.empty and "ticker" in beneish_df.columns:
        t = beneish_df["ticker"].iloc[0]
        if pd.notna(t):
            ticker = str(t)

    fig_mscore = chart_mscore_trend(beneish_df)
    fig_components = chart_component_bar(beneish_df)
    fig_cb_bw = chart_cb_bw_timeline(cb_bw_df)
    fig_timing = chart_timing_anomalies(timing_df)

    company_summary = build_company_summary(
        corp_code, company_name, ticker,
        beneish_df, cb_bw_df, timing_df, network_df,
    )
    flags: list[dict] = [] if skip_claude else synthesize_with_claude(company_summary)

    return build_report_html(
        corp_code=corp_code,
        company_name=company_name,
        ticker=ticker,
        beneish_df=beneish_df,
        cb_bw_df=cb_bw_df,
        timing_df=timing_df,
        network_df=network_df,
        holdings_df=holdings_df,
        fig_mscore=fig_mscore,
        fig_components=fig_components,
        fig_cb_bw=fig_cb_bw,
        fig_timing=fig_timing,
        flags=flags,
        skip_claude=skip_claude,
        cb_bw_csv_exists=_da.CB_BW_CSV.exists(),
        timing_csv_exists=_da.TIMING_CSV.exists(),
        network_csv_exists=_da.NETWORK_CSV.exists(),
    )


# ─── Main entrypoint ───────────────────────────────────────────────────────────

def generate_report(
    corp_code: str,
    output_path: Optional[Path] = None,
    skip_claude: bool = False,
) -> Path:
    """Generate a self-contained HTML forensic report for one company.

    Args:
        corp_code:   DART 8-digit corp code (zero-padded automatically).
        output_path: Path to write HTML. Defaults to 03_Analysis/reports/<corp_code>_report.html.
        skip_claude: If True, skip Claude API synthesis.

    Returns:
        Path to the written HTML file.
    """
    corp_code = corp_code.zfill(8)

    if output_path is None:
        output_path = _REPORTS_DIR / f"{corp_code}_report.html"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load data (file existence tracked before calling loaders)
    beneish_df   = _da.load_parquet("beneish_scores.parquet", corp_code, sort_by="year")
    company_name = _da.load_company_name(corp_code, beneish_df)

    cb_bw_csv_exists = _da.CB_BW_CSV.exists()
    cb_bw_df         = _da.load_csv(_da.CB_BW_CSV, corp_code)

    timing_csv_exists = _da.TIMING_CSV.exists()
    timing_df         = _da.load_csv(_da.TIMING_CSV, corp_code)

    network_csv_exists = _da.NETWORK_CSV.exists()
    network_df         = _da.load_officer_network(corp_code)
    holdings_df        = _da.load_parquet("officer_holdings.parquet", corp_code)

    # Get ticker from beneish data
    ticker = ""
    if not beneish_df.empty and "ticker" in beneish_df.columns:
        t = beneish_df["ticker"].iloc[0]
        if pd.notna(t):
            ticker = str(t)

    # Generate 4 charts
    fig_mscore     = chart_mscore_trend(beneish_df)
    fig_components = chart_component_bar(beneish_df)
    fig_cb_bw      = chart_cb_bw_timeline(cb_bw_df)
    fig_timing     = chart_timing_anomalies(timing_df)

    # Build company summary for Claude
    company_summary = build_company_summary(
        corp_code, company_name, ticker,
        beneish_df, cb_bw_df, timing_df, network_df,
    )

    # Claude synthesis
    flags: list[dict] = [] if skip_claude else synthesize_with_claude(company_summary)

    # Build and write HTML
    html = build_report_html(
        corp_code=corp_code,
        company_name=company_name,
        ticker=ticker,
        beneish_df=beneish_df,
        cb_bw_df=cb_bw_df,
        timing_df=timing_df,
        network_df=network_df,
        holdings_df=holdings_df,
        fig_mscore=fig_mscore,
        fig_components=fig_components,
        fig_cb_bw=fig_cb_bw,
        fig_timing=fig_timing,
        flags=flags,
        skip_claude=skip_claude,
        cb_bw_csv_exists=cb_bw_csv_exists,
        timing_csv_exists=timing_csv_exists,
        network_csv_exists=network_csv_exists,
    )

    output_path.write_text(html, encoding="utf-8")
    return output_path
