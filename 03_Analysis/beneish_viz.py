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
def _chart_distribution(df):
    from src.charts import chart_distribution
    return (chart_distribution(df),)


@app.cell
def _chart_risk_sector(df):
    from src.charts import chart_risk_sector
    return (chart_risk_sector(df),)


@app.cell
def _chart_year_trend(df):
    from src.charts import chart_year_trend
    return (chart_year_trend(df),)


@app.cell
def _chart_components(df):
    from src.charts import chart_components
    return (chart_components(df),)


@app.cell
def _chart_heatmap(df):
    from src.charts import chart_heatmap
    return (chart_heatmap(df),)


@app.cell
def _export_html(df, Path):
    from src.charts import generate_charts
    _out = generate_charts(df, Path(__file__).parent)
    print(f"Wrote {_out} ({_out.stat().st_size / 1024:.0f} KB)")
    return


if __name__ == "__main__":
    app.run()
