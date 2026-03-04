# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo",
#     "pandas",
#     "pyarrow",
#     "numpy",
#     "networkx",
#     "pyvis",
#     "plotly",
# ]
# ///
"""
officer_network.py — Milestone 4: Officer/shareholder network graph.

Builds a networkx graph of individuals appearing across flagged KOSDAQ companies,
revealing hidden connection networks that cross-company registration data obscures.

Graph structure:
    Nodes: individuals (person_id/name) + companies (corp_code)
    Edges:
        officer_at      — person → company (officer or major shareholder)
        holds_pct       — person → company, weighted by % held
        cross_holds     — company → company (from KFTC data, where available)

Key signals:
    - High betweenness centrality: individuals connecting otherwise unrelated companies
    - CB subscriber ↔ issuing company officer overlap (primary manipulation signal)
    - Individuals appearing in ≥2 Milestone 1–3 flagged companies

Output:
    03_Analysis/officer_network/graph.html   — interactive pyvis visualization
    03_Analysis/officer_network/centrality_report.csv

Run interactively:
    uv run marimo edit 03_Analysis/officer_network.py

Run as web app:
    uv run marimo run 03_Analysis/officer_network.py
"""

import marimo

__generated_with = "0.9.0"
app = marimo.App(width="wide", app_title="Officer Network Graph")


@app.cell
def _imports():
    import marimo as mo
    import networkx as nx
    import numpy as np
    import pandas as pd
    import plotly.express as px
    from pathlib import Path

    try:
        from pyvis.network import Network as PyvisNetwork
        PYVIS_AVAILABLE = True
    except ImportError:
        PYVIS_AVAILABLE = False

    return mo, nx, np, pd, px, Path, PyvisNetwork, PYVIS_AVAILABLE


@app.cell
def _load_data(mo, pd, Path):
    """Load processed data and Milestone 1–3 flag outputs."""
    processed = Path("01_Data/processed")
    analysis = Path("03_Analysis")

    required = ["officer_holdings.parquet"]
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

    df_oh = pd.read_parquet(processed / "officer_holdings.parquet")
    df_kftc = (
        pd.read_parquet(processed / "kftc_network.parquet")
        if (processed / "kftc_network.parquet").exists()
        else pd.DataFrame()
    )
    df_map = (
        pd.read_parquet(processed / "corp_ticker_map.parquet")
        if (processed / "corp_ticker_map.parquet").exists()
        else pd.DataFrame()
    )

    # Load Milestone 1–3 flags if available
    flagged_companies: set = set()

    beneish_path = analysis / "beneish_scores.csv"
    if beneish_path.exists():
        df_b = pd.read_csv(beneish_path)
        if "flag" in df_b.columns and "corp_code" in df_b.columns:
            flagged_companies.update(df_b[df_b["flag"] == True]["corp_code"].tolist())

    cb_path = analysis / "cb_bw_summary.csv"
    if cb_path.exists():
        df_cb = pd.read_csv(cb_path)
        if "anomaly_score" in df_cb.columns and "corp_code" in df_cb.columns:
            flagged_companies.update(df_cb[df_cb["anomaly_score"] >= 2]["corp_code"].tolist())

    timing_path = analysis / "timing_anomalies.csv"
    if timing_path.exists():
        df_ta = pd.read_csv(timing_path)
        if "flag" in df_ta.columns and "corp_code" in df_ta.columns:
            flagged_companies.update(df_ta[df_ta["flag"] == True]["corp_code"].tolist())

    mo.callout(
        mo.md(
            f"Loaded **{df_oh['corp_code'].nunique():,}** companies, "
            f"**{df_oh['officer_name'].nunique():,}** individuals.  \n"
            f"Flagged from prior milestones: **{len(flagged_companies):,}** companies"
        ),
        kind="success",
    )
    return df_oh, df_kftc, df_map, flagged_companies


@app.cell
def _build_graph(df_oh, df_kftc, flagged_companies, nx, pd):
    """Build the networkx graph."""
    G = nx.DiGraph()

    # Normalise holdings
    df = df_oh.copy()
    df["officer_name"] = df["officer_name"].fillna("").str.strip()
    df["corp_code"] = df["corp_code"].fillna("").str.strip()
    df = df[df["officer_name"] != ""]
    df = df[df["corp_code"] != ""]

    # Add person nodes
    for person in df["officer_name"].unique():
        G.add_node(f"person:{person}", label=person, node_type="person", color="#4a90d9")

    # Add company nodes
    for corp in df["corp_code"].unique():
        is_flagged = corp in flagged_companies
        G.add_node(
            f"corp:{corp}",
            label=corp,
            node_type="company",
            flagged=is_flagged,
            color="#e05c5c" if is_flagged else "#aaaaaa",
        )

    # Add officer_at and holds_pct edges
    for _, row in df.iterrows():
        person_id = f"person:{row['officer_name']}"
        corp_id = f"corp:{row['corp_code']}"
        pct = None
        try:
            pct = float(str(row.get("pct", "") or "").replace(",", "").replace("%", ""))
        except (ValueError, TypeError):
            pass

        edge_attrs = {
            "edge_type": "officer_at",
            "title": row.get("title", ""),
            "weight": pct or 1.0,
        }
        G.add_edge(person_id, corp_id, **edge_attrs)

    # Add KFTC cross-shareholding edges (company → company)
    if not df_kftc.empty:
        for _, row in df_kftc.iterrows():
            holder = str(row.get("holder_corp", "")).strip()
            target = str(row.get("target_corp", "")).strip()
            if holder and target:
                h_id = f"kftc_corp:{holder}"
                t_id = f"kftc_corp:{target}"
                G.add_node(h_id, label=holder, node_type="kftc_company", color="#f5a623")
                G.add_node(t_id, label=target, node_type="kftc_company", color="#f5a623")
                pct_held = None
                try:
                    pct_held = float(str(row.get("pct_held", "") or "").replace(",", "").replace("%", ""))
                except (ValueError, TypeError):
                    pass
                G.add_edge(h_id, t_id, edge_type="cross_holds", weight=pct_held or 1.0)

    return G


@app.cell
def _compute_centrality(G, nx, pd, flagged_companies):
    """Compute betweenness centrality and identify key individuals."""
    if len(G.nodes) == 0:
        return pd.DataFrame()

    # Betweenness centrality on undirected projection for person nodes
    G_undirected = G.to_undirected()
    try:
        centrality = nx.betweenness_centrality(G_undirected, normalized=True)
    except Exception:
        centrality = {n: 0.0 for n in G.nodes}

    # Degree: number of companies each person is connected to
    rows = []
    for node, data in G.nodes(data=True):
        if data.get("node_type") != "person":
            continue
        person_name = data.get("label", node)
        # Companies this person is an officer at
        companies = [
            nbr.replace("corp:", "")
            for nbr in G.successors(node)
            if G.nodes[nbr].get("node_type") == "company"
        ]
        flagged_count = sum(1 for c in companies if c in flagged_companies)
        rows.append({
            "person_name": person_name,
            "company_count": len(companies),
            "flagged_company_count": flagged_count,
            "companies": ", ".join(companies[:10]),  # truncate for display
            "betweenness_centrality": round(centrality.get(node, 0.0), 6),
            "appears_in_multiple_flagged": flagged_count >= 2,
        })

    df_centrality = pd.DataFrame(rows)
    if not df_centrality.empty:
        df_centrality = df_centrality.sort_values(
            ["flagged_company_count", "betweenness_centrality"],
            ascending=[False, False],
        )

    return df_centrality


@app.cell
def _ui_controls(mo):
    min_companies = mo.ui.slider(
        start=1,
        stop=10,
        step=1,
        value=2,
        label="Min companies per person",
        show_value=True,
    )
    flagged_only = mo.ui.checkbox(label="Show only persons in flagged companies", value=True)
    return min_companies, flagged_only


@app.cell
def _display(mo, df_centrality, G, min_companies, flagged_only, px):
    """Display centrality table and graph stats."""
    if df_centrality.empty:
        return mo.callout(
            mo.md("No network data to display. Ensure officer_holdings.parquet has data."),
            kind="warn",
        )

    filtered = df_centrality[df_centrality["company_count"] >= min_companies.value]
    if flagged_only.value:
        filtered = filtered[filtered["flagged_company_count"] >= 1]

    summary = mo.hstack([
        mo.stat(value=str(G.number_of_nodes()), label="Total nodes"),
        mo.stat(value=str(G.number_of_edges()), label="Total edges"),
        mo.stat(value=str(len(filtered)), label="Key individuals"),
        mo.stat(
            value=str(filtered["appears_in_multiple_flagged"].sum()),
            label="In ≥2 flagged companies",
        ),
    ])

    fig = px.scatter(
        filtered.head(100),
        x="company_count",
        y="betweenness_centrality",
        size="flagged_company_count",
        hover_data=["person_name", "companies"],
        color="flagged_company_count",
        title="Individual betweenness centrality vs. company count",
        labels={
            "company_count": "Number of companies",
            "betweenness_centrality": "Betweenness centrality",
            "flagged_company_count": "Flagged company count",
        },
        color_continuous_scale="Reds",
    )
    fig.update_layout(height=400)

    display_cols = [
        "person_name", "company_count", "flagged_company_count",
        "betweenness_centrality", "appears_in_multiple_flagged", "companies",
    ]
    available = [c for c in display_cols if c in filtered.columns]

    return mo.vstack([
        mo.hstack([min_companies, flagged_only]),
        summary,
        mo.ui.plotly(fig),
        mo.md("### Key individuals (sorted by flagged company count, then centrality)"),
        mo.ui.table(filtered[available].head(200), selection=None),
    ])


@app.cell
def _export(G, df_centrality, mo, Path, PYVIS_AVAILABLE, PyvisNetwork):
    """Export interactive graph HTML and centrality CSV."""
    out_dir = Path("03_Analysis/officer_network")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Centrality CSV
    csv_path = out_dir / "centrality_report.csv"
    df_centrality.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # Interactive HTML via pyvis
    html_path = out_dir / "graph.html"
    if PYVIS_AVAILABLE and len(G.nodes) > 0:
        # Limit graph size for browser rendering
        MAX_NODES = 500
        if len(G.nodes) > MAX_NODES:
            # Keep nodes with highest degree
            top_nodes = sorted(G.nodes, key=lambda n: G.degree(n), reverse=True)[:MAX_NODES]
            G_display = G.subgraph(top_nodes).copy()
        else:
            G_display = G

        net = PyvisNetwork(
            height="700px",
            width="100%",
            directed=True,
            notebook=False,
        )
        net.set_options("""
        {
            "physics": {"stabilization": {"iterations": 100}},
            "nodes": {"font": {"size": 12}},
            "edges": {"arrows": {"to": {"enabled": true, "scaleFactor": 0.5}}}
        }
        """)

        for node, data in G_display.nodes(data=True):
            net.add_node(
                node,
                label=data.get("label", node)[:30],
                color=data.get("color", "#aaaaaa"),
                title=f"Type: {data.get('node_type', 'unknown')}",
            )
        for u, v, data in G_display.edges(data=True):
            net.add_edge(
                u, v,
                title=data.get("edge_type", ""),
                value=min(data.get("weight", 1.0), 10.0),
            )

        net.save_graph(str(html_path))
        graph_msg = f"Interactive graph saved to `{html_path}`"
    else:
        graph_msg = "pyvis not available — install with `uv add pyvis` for interactive graph export"

    return mo.callout(
        mo.md(
            f"Exported centrality report to `{csv_path}`  \n"
            f"{graph_msg}"
        ),
        kind="success",
    )


if __name__ == "__main__":
    app.run()
