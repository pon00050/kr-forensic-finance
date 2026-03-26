"""
run_officer_network.py — Standalone runner for officer/shareholder network analysis.

Extracts the graph logic from officer_network.py (Marimo app) and runs it
as a plain Python script. Produces the same output files without the Marimo UI.

Must be run after run_cb_bw_timelines.py and run_timing_anomalies.py (reads their CSVs).

Outputs:
    03_Analysis/officer_network/centrality_report.csv
    03_Analysis/officer_network/graph.html  (requires pyvis)

Run:
    python 03_Analysis/run_officer_network.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "01_Data" / "processed"
ANALYSIS = ROOT / "03_Analysis"

# Minimum anomaly_score for a CB/BW event to mark its company as "flagged".
# Raised to 2 (session 33): holdings_flag now operational; 27 events at flag_count=2
# across 23 companies without SEIBRO. Multi-flag events exist — threshold now meaningful.
from krff.constants import OFFICER_FLAG_THRESHOLD as FLAG_THRESHOLD

try:
    from pyvis.network import Network as PyvisNetwork
    PYVIS_AVAILABLE = True
except ImportError:
    PYVIS_AVAILABLE = False


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, set, dict, dict, set]:
    required = ["officer_holdings.parquet"]
    missing = [f for f in required if not (PROCESSED / f).exists()]
    if missing:
        print(f"ERROR: Missing data files: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    df_oh = pd.read_parquet(PROCESSED / "officer_holdings.parquet")
    df_kftc = (
        pd.read_parquet(PROCESSED / "kftc_network.parquet")
        if (PROCESSED / "kftc_network.parquet").exists()
        else pd.DataFrame()
    )
    df_map = (
        pd.read_parquet(PROCESSED / "corp_ticker_map.parquet")
        if (PROCESSED / "corp_ticker_map.parquet").exists()
        else pd.DataFrame()
    )

    # Build lookup dicts from corp_ticker_map
    if not df_map.empty and "corp_code" in df_map.columns:
        code_to_name   = dict(zip(df_map["corp_code"].str.zfill(8), df_map["corp_name"]))
        code_to_ticker = dict(zip(df_map["corp_code"].str.zfill(8), df_map["ticker"]))
        corp_name_set  = set(df_map["corp_name"])
    else:
        code_to_name   = {}
        code_to_ticker = {}
        corp_name_set  = set()

    flagged_companies: set = set()

    beneish_path = ANALYSIS / "beneish_scores.csv"
    if beneish_path.exists():
        df_b = pd.read_csv(beneish_path)
        if "flag" in df_b.columns and "corp_code" in df_b.columns:
            flagged_companies.update(
                df_b[df_b["flag"] == True]["corp_code"].astype(str).str.zfill(8).tolist()
            )

    cb_path = ANALYSIS / "cb_bw_summary.csv"
    if cb_path.exists():
        df_cb = pd.read_csv(cb_path)
        if "anomaly_score" in df_cb.columns and "corp_code" in df_cb.columns:
            flagged_companies.update(
                df_cb[df_cb["anomaly_score"] >= FLAG_THRESHOLD]["corp_code"].astype(str).str.zfill(8).tolist()
            )

    timing_path = ANALYSIS / "timing_anomalies.csv"
    if timing_path.exists():
        df_ta = pd.read_csv(timing_path)
        if "flag" in df_ta.columns and "corp_code" in df_ta.columns:
            flagged_companies.update(
                df_ta[df_ta["flag"] == True]["corp_code"].astype(str).str.zfill(8).tolist()
            )

    print(
        f"Loaded {df_oh['corp_code'].nunique():,} companies, "
        f"{df_oh['officer_name'].nunique():,} individuals. "
        f"Flagged from prior milestones: {len(flagged_companies):,}"
    )
    return df_oh, df_kftc, df_map, flagged_companies, code_to_name, code_to_ticker, corp_name_set


_CORPORATE_SUFFIXES = [
    "홀딩스", "주식회사", "(주)", "연금공단", "자산운용",
    "투자조합", "파트너스", "캐피탈", "펀드", "벤처스",
]


def is_corporate_reporter(name: str, corp_name_set: set) -> bool:
    """Return True if name is a corporate entity, not a human officer.

    Pass 1: exact match against known corp names from corp_ticker_map.
    Pass 2: suffix pattern for entities not in the map.
    Conservative — returns False (human) by default.
    """
    if name in corp_name_set:
        return True
    return any(s in name for s in _CORPORATE_SUFFIXES)


def build_graph(df_oh: pd.DataFrame, df_kftc: pd.DataFrame, flagged_companies: set,
                code_to_name: dict, code_to_ticker: dict, corp_name_set: set) -> nx.DiGraph:
    G: nx.DiGraph = nx.DiGraph()

    df = df_oh.copy()
    df["officer_name"] = df["officer_name"].fillna("").str.strip()
    df["corp_code"] = df["corp_code"].fillna("").str.strip()
    df = df[df["officer_name"] != ""]
    df = df[df["corp_code"] != ""]

    for person in df["officer_name"].unique():
        is_corp = is_corporate_reporter(person, corp_name_set)
        G.add_node(
            f"person:{person}",
            label=person,
            node_type="person",
            is_corporate=is_corp,
            color="#f5a623" if is_corp else "#4a90d9",  # orange=corporate, blue=human
        )

    for corp in df["corp_code"].unique():
        is_flagged = corp in flagged_companies
        corp_padded = str(corp).zfill(8)
        name = code_to_name.get(corp_padded, corp_padded)
        ticker = code_to_ticker.get(corp_padded, "")
        label = f"{name} ({ticker})" if ticker else name
        G.add_node(
            f"corp:{corp}",
            label=label,
            node_type="company",
            flagged=is_flagged,
            color="#e05c5c" if is_flagged else "#aaaaaa",
        )

    for _, row in df.iterrows():
        person_id = f"person:{row['officer_name']}"
        corp_id = f"corp:{row['corp_code']}"
        pct = None
        try:
            pct = float(str(row.get("pct", "") or "").replace(",", "").replace("%", ""))
        except (ValueError, TypeError):
            pass
        G.add_edge(person_id, corp_id, edge_type="officer_at", title=row.get("title", ""), weight=pct or 1.0)

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


def compute_centrality(G: nx.DiGraph, flagged_companies: set,
                       code_to_name: dict, code_to_ticker: dict, corp_name_set: set) -> pd.DataFrame:
    if len(G.nodes) == 0:
        return pd.DataFrame()

    G_undirected = G.to_undirected()
    try:
        centrality = nx.betweenness_centrality(G_undirected, normalized=True)
    except Exception:
        centrality = {n: 0.0 for n in G.nodes}

    rows = []
    for node, data in G.nodes(data=True):
        if data.get("node_type") != "person":
            continue
        person_name = data.get("label", node)
        companies = [
            nbr.replace("corp:", "")
            for nbr in G.successors(node)
            if G.nodes[nbr].get("node_type") == "company"
        ]
        flagged_count = sum(1 for c in companies if c in flagged_companies)
        company_names = [code_to_name.get(str(c).zfill(8), c) for c in companies]
        tickers       = [code_to_ticker.get(str(c).zfill(8), "") for c in companies]
        rows.append({
            "person_name": person_name,
            "is_corporate_reporter": is_corporate_reporter(person_name, corp_name_set),
            "company_count": len(companies),
            "flagged_company_count": flagged_count,
            "company_names": ", ".join(company_names[:10]),
            "tickers": ", ".join(t for t in tickers[:10] if t),
            "companies": ", ".join(companies[:10]),
            "betweenness_centrality": round(centrality.get(node, 0.0), 6),
            "appears_in_multiple_flagged": (
                flagged_count >= 2
                and not is_corporate_reporter(person_name, corp_name_set)
            ),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(
            ["is_corporate_reporter", "flagged_company_count", "betweenness_centrality"],
            ascending=[True, False, False],
        )
    return df


def export(G: nx.DiGraph, df_centrality: pd.DataFrame) -> None:
    out_dir = ANALYSIS / "officer_network"
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "centrality_report.csv"
    # Ensure schema is always written even when no officer data is available
    _CENTRALITY_COLS = [
        "person_name", "is_corporate_reporter", "company_count", "flagged_company_count",
        "company_names", "tickers", "companies", "betweenness_centrality", "appears_in_multiple_flagged",
    ]
    if df_centrality.empty:
        import pandas as _pd
        df_centrality = _pd.DataFrame(columns=_CENTRALITY_COLS)
    df_centrality.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"Exported centrality report to {csv_path}")
    print(f"  {len(df_centrality):,} individuals analysed")
    multi_flagged = df_centrality["appears_in_multiple_flagged"].sum() if not df_centrality.empty else 0
    print(f"  Individuals in >=2 flagged companies: {multi_flagged:,}")

    html_path = out_dir / "graph.html"
    if PYVIS_AVAILABLE and len(G.nodes) > 0:
        MAX_NODES = 500
        if len(G.nodes) > MAX_NODES:
            top_nodes = sorted(G.nodes, key=lambda n: G.degree(n), reverse=True)[:MAX_NODES]
            G_display = G.subgraph(top_nodes).copy()
        else:
            G_display = G

        net = PyvisNetwork(height="700px", width="100%", directed=True, notebook=False)
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
            net.add_edge(u, v, title=data.get("edge_type", ""), value=min(data.get("weight", 1.0), 10.0))
        net.save_graph(str(html_path))
        print(f"Interactive graph saved to {html_path}")
    else:
        print("pyvis not available — skipping graph.html export")


def main() -> None:
    df_oh, df_kftc, df_map, flagged_companies, code_to_name, code_to_ticker, corp_name_set = load_data()
    print("Building network graph...")
    G = build_graph(df_oh, df_kftc, flagged_companies, code_to_name, code_to_ticker, corp_name_set)
    print(f"Graph: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")
    print("Computing betweenness centrality...")
    df_centrality = compute_centrality(G, flagged_companies, code_to_name, code_to_ticker, corp_name_set)
    export(G, df_centrality)


if __name__ == "__main__":
    main()
