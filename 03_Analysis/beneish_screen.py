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
beneish_screen.py — Phase 1: Beneish M-Score screening for KOSDAQ companies.

Calculates the Beneish (1999) 8-variable M-Score for each KOSDAQ company-year.
Flags companies above the −1.78 threshold as warranting further investigation.

Methodology:
    Beneish, M. D. (1999). The detection of earnings manipulation.
    Financial Analysts Journal, 55(5), 24–36.

Calibration note: The M-Score was calibrated on US GAAP companies. Korean IFRS
accounting norms differ (R&D capitalisation, lease accounting). False positive
rates are higher for Korean companies — all flags are hypotheses for human
review, not fraud conclusions. Biotech/pharma (G3510, G3520) are flagged
separately as high false-positive-risk.

Inputs:
    01_Data/processed/company_financials.parquet  (built by transform.py)

Outputs:
    01_Data/processed/beneish_scores.parquet
    03_Analysis/beneish_scores.csv

Run interactively:
    uv run marimo edit 03_Analysis/beneish_screen.py

Run as script (non-interactive, writes CSV):
    uv run python 03_Analysis/beneish_screen.py
"""

import marimo

__generated_with = "0.9.0"
app = marimo.App(width="wide", app_title="Beneish M-Score — KOSDAQ")


@app.cell
def _imports():
    import marimo as mo
    import numpy as np
    import os
    import pandas as pd
    import plotly.express as px
    from datetime import date
    from pathlib import Path
    return mo, np, os, pd, px, date, Path


@app.cell
def _load_data(mo, os, pd, Path):
    """Load company_financials.parquet — from R2 (DuckDB) if configured, else local."""
    def _load_financials() -> pd.DataFrame:
        endpoint = os.getenv("R2_ENDPOINT_URL", "")
        key      = os.getenv("R2_ACCESS_KEY_ID", "")
        secret   = os.getenv("R2_SECRET_ACCESS_KEY", "")
        bucket   = os.getenv("R2_BUCKET", "kr-forensic-finance")

        if all([endpoint, key, secret]):
            import duckdb
            conn = duckdb.connect()
            conn.execute("INSTALL httpfs; LOAD httpfs;")
            conn.execute(f"""
                SET s3_endpoint='{endpoint.replace("https://", "")}';
                SET s3_access_key_id='{key}';
                SET s3_secret_access_key='{secret}';
                SET s3_url_style='path';
            """)
            return conn.execute(
                f"SELECT * FROM 's3://{bucket}/processed/company_financials.parquet'"
            ).df()

        local = Path("01_Data/processed/company_financials.parquet")
        if not local.exists():
            return None
        return pd.read_parquet(local)

    df_fin = _load_financials()

    if df_fin is None:
        mo.stop(
            mo.callout(
                mo.md(
                    "**Data not found.**  \n"
                    "Either run the pipeline first, or configure R2 credentials in `.env`:  \n"
                    "```\npython 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023\n```"
                ),
                kind="danger",
            )
        )

    load_callout = mo.callout(
        mo.md(
            f"Loaded **{len(df_fin):,}** company-year rows from company_financials.parquet  \n"
            f"**{df_fin['corp_code'].nunique():,}** companies, "
            f"**{df_fin['year'].min()}–{df_fin['year'].max()}**"
        ),
        kind="success",
    )
    return df_fin, load_callout


@app.cell
def _compute_beneish(df_fin, np, pd, date):
    """
    Calculate the 8 Beneish components and M-Score for each company-year pair
    (year T and year T-1).

    Adjustments for Korean KOSDAQ data:
    - expense_method='nature' companies: GMI and SGAI set to 1.0 (neutral
      substitution — these ratios are structurally undefined without a COGS line).
    - lt_debt null in either year: LVGI is null (not assumed zero).
    - M-Score is null if more than 2 component ratios other than GMI/SGAI are null.
    """
    df = df_fin.copy()

    # Ensure numeric types
    numeric_cols = [
        "receivables", "revenue", "cogs", "sga", "ppe", "depreciation",
        "total_assets", "lt_debt", "net_income", "cfo",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Sort for lag computation
    df = df.sort_values(["corp_code", "year"]).reset_index(drop=True)

    # Lag (T-1) fields
    lag_cols = [
        "receivables", "revenue", "cogs", "sga", "ppe",
        "depreciation", "total_assets", "lt_debt",
        "fs_type", "expense_method",
    ]
    for col in lag_cols:
        df[f"{col}_l"] = df.groupby("corp_code")[col].shift(1)

    # fs_type_switched: True if fs_type changed between T-1 and T
    df["fs_type_switched"] = (
        df["fs_type_l"].notna() & (df["fs_type"] != df["fs_type_l"])
    )

    # -----------------------------------------------------------------------
    # Derived intermediate values
    # -----------------------------------------------------------------------
    rev = df["revenue"].replace(0, np.nan)
    rev_l = df["revenue_l"].replace(0, np.nan)
    ta = df["total_assets"].replace(0, np.nan)
    ta_l = df["total_assets_l"].replace(0, np.nan)

    df["gross_profit"] = df["revenue"] - df["cogs"]
    df["gross_profit_l"] = df["revenue_l"] - df["cogs_l"]
    df["gross_margin"] = df["gross_profit"] / rev
    df["gross_margin_l"] = df["gross_profit_l"] / rev_l

    # Non-current non-PPE assets proxy: total_assets - ppe
    df["soft_assets"] = df["total_assets"] - df["ppe"]
    df["soft_assets_l"] = df["total_assets_l"] - df["ppe_l"]

    # -----------------------------------------------------------------------
    # 8 Beneish ratios
    # -----------------------------------------------------------------------

    # 1. DSRI — Days Sales in Receivables Index
    df["dsri"] = (
        (df["receivables"] / rev) /
        (df["receivables_l"] / rev_l)
    )

    # 2. GMI — Gross Margin Index (1.0 for nature-method companies)
    gmi_raw = df["gross_margin_l"] / df["gross_margin"].replace(0, np.nan)
    df["gmi"] = np.where(df["expense_method"] == "nature", 1.0, gmi_raw)

    # 3. AQI — Asset Quality Index
    df["aqi"] = (
        (df["soft_assets"] / ta) /
        (df["soft_assets_l"] / ta_l)
    )

    # 4. SGI — Sales Growth Index
    df["sgi"] = df["revenue"] / rev_l

    # 5. DEPI — Depreciation Index
    ppe_depr = (df["ppe"] + df["depreciation"]).replace(0, np.nan)
    ppe_depr_l = (df["ppe_l"] + df["depreciation_l"]).replace(0, np.nan)
    df["depi"] = (
        (df["depreciation_l"] / ppe_depr_l) /
        (df["depreciation"] / ppe_depr)
    )

    # 6. SGAI — SG&A Index (1.0 for nature-method companies)
    sgai_raw = (
        (df["sga"] / rev) /
        (df["sga_l"] / rev_l)
    )
    df["sgai"] = np.where(df["expense_method"] == "nature", 1.0, sgai_raw)

    # 7. LVGI — Leverage Index (null if lt_debt unavailable in either year)
    df["lvgi"] = (
        (df["lt_debt"] / ta) /
        (df["lt_debt_l"] / ta_l)
    )

    # 8. TATA — Total Accruals to Total Assets
    df["tata"] = (df["net_income"] - df["cfo"]) / ta

    # -----------------------------------------------------------------------
    # M-Score (Beneish 1999 8-variable)
    #
    # Null handling: use neutral value 1.0 for unavailable ratios so NaN
    # does not propagate through the formula. Set M-Score to null only when
    # more than 2 of the 5 core non-substituted components (dsri, aqi, sgi,
    # depi, tata) are null. GMI and SGAI already substituted above.
    # LVGI null alone (lt_debt not disclosed) does not kill M-Score.
    #
    # Neutral value rationale:
    #   DEPI=1.0  → depreciation rate unchanged (most conservative; common for
    #               companies that report CF subtotals only without line items)
    #   LVGI=1.0  → leverage unchanged (conservative when lt_debt absent)
    # These substitutions are recorded in the component columns so downstream
    # users can see which ratios were imputed.
    # -----------------------------------------------------------------------
    core_components = ["dsri", "aqi", "sgi", "depi", "tata"]
    df["_null_core"] = df[core_components].isna().sum(axis=1)

    # Impute with neutral 1.0 before computing the score
    depi_filled = df["depi"].fillna(1.0)
    lvgi_filled = df["lvgi"].fillna(1.0)

    df["m_score"] = np.where(
        df["_null_core"] > 2,
        np.nan,
        (
            -4.84
            + 0.920 * df["dsri"].fillna(1.0)
            + 0.528 * df["gmi"]
            + 0.404 * df["aqi"].fillna(1.0)
            + 0.892 * df["sgi"].fillna(1.0)
            + 0.115 * depi_filled
            - 0.172 * df["sgai"]
            + 4.679 * df["tata"].fillna(0.0)
            - 0.327 * lvgi_filled
        ),
    )
    df = df.drop(columns=["_null_core"])

    # Also null out if T-1 year has no data (no lag available — year is first year)
    df.loc[df["revenue_l"].isna(), "m_score"] = np.nan

    df["flag"] = df["m_score"].notna() & (df["m_score"] > -1.78)

    # High false-positive risk sectors: biotech/pharma (G3510) and medical devices (G3520)
    fp_sectors = {"G3510", "G3520"}
    if "wics_sector_code" in df.columns:
        df["high_fp_risk"] = df["wics_sector_code"].isin(fp_sectors)
    else:
        df["high_fp_risk"] = False

    # risk_tier is Beneish-only in Phase 1. Will be upgraded to incorporate
    # CB/BW and timing anomaly signals in Phase 2+.
    conditions = [
        ~df["flag"],
        df["flag"] & df["high_fp_risk"],
        df["flag"] & ~df["high_fp_risk"] & (df["m_score"] > -1.0),
    ]
    choices = ["Low", "Medium", "Critical"]
    df["risk_tier"] = np.select(conditions, choices, default="High")

    df["extraction_date"] = date.today().isoformat()

    # -----------------------------------------------------------------------
    # Sector percentile
    # Computed within WICS industry group × year. Requires ≥10 peers.
    # -----------------------------------------------------------------------
    df["sector_percentile"] = np.nan
    if "wics_sector_code" in df.columns:
        scored = df[df["m_score"].notna()].copy()
        if len(scored) > 0:
            group_counts = (
                scored.groupby(["wics_sector_code", "year", "market"])["m_score"]
                .transform("count")
            )
            scored["sector_percentile"] = scored.groupby(
                ["wics_sector_code", "year", "market"]
            )["m_score"].rank(pct=True) * 100
            scored.loc[group_counts < 10, "sector_percentile"] = np.nan
            df.update(scored[["sector_percentile"]])

    # Keep only rows where year T-1 data was available (i.e. this is a calculable period)
    df_scored = df[df["revenue_l"].notna()].copy()

    # -----------------------------------------------------------------------
    # DART link — direct URL to 사업보고서 for year T (corp_code search)
    # -----------------------------------------------------------------------
    df_scored["dart_link"] = df_scored["corp_code"].apply(
        lambda c: f"https://dart.fss.or.kr/corp/searchAjax.do?textCrpNm=&textCrpCik={c}"
    )

    # Clean up lag columns (not needed in output)
    lag_drop = [c for c in df_scored.columns if c.endswith("_l")]
    df_scored = df_scored.drop(columns=lag_drop, errors="ignore")

    df_scored = df_scored.reset_index(drop=True)

    return df_scored


@app.cell
def _write_parquet(df_scored, os, pd, Path):
    """Write beneish_scores.parquet to 01_Data/processed/ and upload to R2 if configured."""
    _processed = Path("01_Data/processed")
    _processed.mkdir(parents=True, exist_ok=True)

    output_cols = [
        "corp_code", "ticker", "company_name", "market", "year", "extraction_date",
        "fs_type", "fs_type_switched", "expense_method",
        "dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata",
        "m_score", "flag", "high_fp_risk", "risk_tier",
        "wics_sector_code", "wics_sector", "sector_percentile",
        "dart_link",
    ]
    missing_cols = [c for c in output_cols if c not in df_scored.columns]
    if missing_cols:
        raise AssertionError(
            f"beneish_screen: computed DataFrame is missing expected output columns: "
            f"{missing_cols}. Check Beneish formula or WICS enrichment step."
        )
    df_out = df_scored[output_cols].copy()

    parquet_path = _processed / "beneish_scores.parquet"
    df_out.to_parquet(parquet_path, index=False, engine="pyarrow")

    # Upload to R2 if credentials are configured
    endpoint = os.getenv("R2_ENDPOINT_URL", "")
    key      = os.getenv("R2_ACCESS_KEY_ID", "")
    secret   = os.getenv("R2_SECRET_ACCESS_KEY", "")
    if all([endpoint, key, secret]):
        import s3fs
        bucket = os.getenv("R2_BUCKET", "kr-forensic-finance")
        fs = s3fs.S3FileSystem(
            key=key, secret=secret,
            client_kwargs={"endpoint_url": endpoint},
        )
        fs.put(str(parquet_path), f"{bucket}/processed/beneish_scores.parquet")

    return parquet_path, df_out


@app.cell
def _write_csv(df_out, Path):
    """Write beneish_scores.csv to 03_Analysis/."""
    out_path = Path("03_Analysis/beneish_scores.csv")
    df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


@app.cell
def _ui_controls(mo, df_scored):
    """Interactive filter controls."""
    years_available = sorted(df_scored["year"].unique().tolist()) if not df_scored.empty else []

    sectors_available = []
    if "wics_sector" in df_scored.columns:
        sectors_available = sorted(
            df_scored["wics_sector"].dropna().unique().tolist()
        )

    threshold_slider = mo.ui.slider(
        start=-4.0,
        stop=1.0,
        step=0.01,
        value=-1.78,
        label="M-Score threshold (Beneish default: −1.78)",
        show_value=True,
    )
    year_filter = mo.ui.multiselect(
        options=[str(y) for y in years_available],
        value=[],
        label="Year filter (all if empty)",
    )
    sector_filter = mo.ui.multiselect(
        options=sectors_available,
        value=[],
        label="Sector filter (all if empty)",
    )
    show_fp_risk = mo.ui.checkbox(
        value=False,
        label="Highlight high false-positive-risk companies (biotech/pharma, G3510/G3520)",
    )
    return threshold_slider, year_filter, sector_filter, show_fp_risk


@app.cell
def _display(
    mo, df_scored, threshold_slider, year_filter, sector_filter, show_fp_risk, px
):
    """Apply filters and display ranked anomaly table + distribution chart."""
    import numpy as _np

    threshold = threshold_slider.value
    selected_years = [int(y) for y in year_filter.value] if year_filter.value else []
    selected_sectors = sector_filter.value if sector_filter.value else []

    df_view = df_scored.copy()

    # Apply year filter
    if selected_years:
        df_view = df_view[df_view["year"].isin(selected_years)]

    # Apply sector filter
    if selected_sectors and "wics_sector" in df_view.columns:
        df_view = df_view[df_view["wics_sector"].isin(selected_sectors)]

    # Flagged companies
    flagged = df_view[df_view["m_score"] > threshold].copy()
    if show_fp_risk.value and "high_fp_risk" in flagged.columns:
        flagged = flagged[~flagged["high_fp_risk"]]

    flagged = flagged.sort_values("m_score", ascending=False).reset_index(drop=True)

    # Stat header
    total_companies = df_view["corp_code"].nunique()
    flagged_companies = flagged["corp_code"].nunique()

    stat_row = mo.hstack([
        mo.stat(
            value=f"{len(df_view):,}",
            label="Company-years shown",
            caption=f"{total_companies:,} unique companies",
        ),
        mo.stat(
            value=f"{flagged_companies:,}",
            label=f"Flagged (M-Score > {threshold:.2f})",
            caption=f"{len(flagged):,} company-year entries",
        ),
        mo.stat(
            value=f"{df_scored['m_score'].notna().sum():,}",
            label="Total scoreable company-years",
            caption=f"{df_scored['corp_code'].nunique():,} unique companies",
        ),
    ])

    # Distribution chart
    scored_view = df_view[df_view["m_score"].notna()]
    fig = px.histogram(
        scored_view,
        x="m_score",
        nbins=80,
        title="M-Score distribution (filtered view)",
        labels={"m_score": "M-Score"},
        color_discrete_sequence=["#4a90d9"],
        template="plotly_white",
    )
    fig.add_vline(
        x=threshold,
        line_dash="dash",
        line_color="red",
        annotation_text=f"Threshold: {threshold:.2f}",
    )
    fig.update_layout(height=320, margin=dict(t=40, b=20))

    # Ranked table columns
    table_cols = [
        "corp_code", "ticker", "company_name", "year",
        "m_score", "dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata",
        "expense_method", "fs_type", "fs_type_switched",
        "high_fp_risk", "wics_sector", "sector_percentile",
        "dart_link",
    ]
    show_cols = [c for c in table_cols if c in flagged.columns]

    return mo.vstack([
        stat_row,
        threshold_slider,
        mo.hstack([year_filter, sector_filter, show_fp_risk]),
        mo.ui.plotly(fig),
        mo.md(f"### Flagged companies ({len(flagged):,} entries)"),
        mo.ui.table(
            flagged[show_cols].round(4),
            selection=None,
            frozen_columns=3,
        ),
    ])


@app.cell
def _export_summary(mo, out_path, parquet_path, df_out):
    """Display export confirmation."""
    return mo.vstack([
        mo.callout(
            mo.md(
                f"Parquet: `{parquet_path}` ({len(df_out):,} rows)  \n"
                f"CSV: `{out_path}`"
            ),
            kind="success",
        )
    ])


if __name__ == "__main__":
    app.run()
