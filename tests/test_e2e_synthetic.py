"""
test_e2e_synthetic.py — Synthetic end-to-end tests for the computation pipeline.

Exercises the full chain: company_financials → Beneish scoring → chart generation
using a synthetic dataset. No real data, no API calls, no DART key required.
Runnable in CI alongside test_pipeline_invariants.py.

Coverage targets:
  - Beneish 8-variable formula with hand-verifiable M-Scores
  - Nature-method GMI/SGAI substitution (1.0)
  - Missing depreciation → DEPI imputation (1.0)
  - Missing lt_debt → LVGI imputation (1.0)
  - High FP risk sector → risk_tier "Medium"
  - CFS→OFS fs_type shift detection
  - All 5 chart functions in src/charts.py
  - HTML export via generate_charts()
  - CLI wrappers in src/analysis.py and src/charts.py

Run:
    pytest tests/test_e2e_synthetic.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from krff.constants import BENEISH_THRESHOLD

ROOT = Path(__file__).resolve().parents[1]
# conftest.py already adds 02_Pipeline to sys.path


# ---------------------------------------------------------------------------
# Synthetic dataset builder
# ---------------------------------------------------------------------------

def _base_row(corp_code, ticker, name, year, **overrides):
    """Build one row of the 34-column company_financials schema."""
    row = {
        "corp_code": corp_code,
        "ticker": ticker,
        "company_name": name,
        "market": "KOSDAQ",
        "year": year,
        "extraction_date": "2024-01-15",
        "fs_type": "CFS",
        "fs_type_shift": False,
        "dart_api_source": "finstate_all_CFS",
        "expense_method": "function",
        # Financials — defaults (overridden per company)
        "receivables": 100e6,
        "revenue": 1000e6,
        "cogs": 600e6,
        "sga": 80e6,
        "ppe": 400e6,
        "depreciation": 50e6,
        "total_assets": 1500e6,
        "lt_debt": 200e6,
        "net_income": 100e6,
        "cfo": 80e6,
        # Sector
        "wics_sector_code": "G4510",
        "wics_sector": "소프트웨어",
        "ksic_code": "621",
        "krx_sector": None,
        # Match methods
        "match_method_receivables": "exact_id",
        "match_method_revenue": "exact_id",
        "match_method_cogs": "exact_id",
        "match_method_sga": "exact_id",
        "match_method_ppe": "exact_id",
        "match_method_depreciation": "exact_id",
        "match_method_total_assets": "exact_id",
        "match_method_lt_debt": "exact_id",
        "match_method_net_income": "exact_id",
        "match_method_cfo": "exact_id",
    }
    row.update(overrides)
    return row


def build_synthetic_financials() -> pd.DataFrame:
    """
    Build a 24-row synthetic dataset (12 companies × 2 years each).

    Companies SYN01–SYN06 test specific edge cases.
    Companies SYN07–SYN12 are filler to meet the ≥10-per-sector threshold
    required by chart_risk_sector() and chart_heatmap().
    """
    rows = []

    # --- SYN01: Normal company (function method, all fields populated) ---
    # Hand-calculable M-Score ≈ -2.26
    rows.append(_base_row("00000001", "000001", "TestCo Normal", 2019,
        receivables=100e6, revenue=1000e6, cogs=600e6, sga=80e6,
        ppe=400e6, depreciation=50e6, total_assets=1500e6, lt_debt=200e6,
        net_income=100e6, cfo=80e6))
    rows.append(_base_row("00000001", "000001", "TestCo Normal", 2020,
        receivables=130e6, revenue=1200e6, cogs=700e6, sga=110e6,
        ppe=420e6, depreciation=55e6, total_assets=1700e6, lt_debt=260e6,
        net_income=100e6, cfo=80e6))

    # --- SYN02: Nature-method company (cogs & sga null → GMI=1.0, SGAI=1.0) ---
    rows.append(_base_row("00000002", "000002", "TestCo Nature", 2019,
        expense_method="nature", revenue=500e6, receivables=50e6,
        cogs=None, sga=None, ppe=200e6, depreciation=20e6,
        total_assets=800e6, lt_debt=100e6, net_income=40e6, cfo=35e6,
        match_method_cogs=None, match_method_sga=None))
    rows.append(_base_row("00000002", "000002", "TestCo Nature", 2020,
        expense_method="nature", revenue=600e6, receivables=70e6,
        cogs=None, sga=None, ppe=220e6, depreciation=22e6,
        total_assets=950e6, lt_debt=120e6, net_income=50e6, cfo=45e6,
        match_method_cogs=None, match_method_sga=None))

    # --- SYN03: Missing depreciation in both years → DEPI=1.0 ---
    rows.append(_base_row("00000003", "000003", "TestCo NoDEPI", 2019,
        revenue=800e6, receivables=80e6, cogs=400e6, sga=100e6,
        ppe=300e6, depreciation=None, total_assets=1200e6, lt_debt=150e6,
        net_income=80e6, cfo=70e6, match_method_depreciation=None))
    rows.append(_base_row("00000003", "000003", "TestCo NoDEPI", 2020,
        revenue=900e6, receivables=95e6, cogs=450e6, sga=110e6,
        ppe=320e6, depreciation=None, total_assets=1350e6, lt_debt=160e6,
        net_income=80e6, cfo=70e6, match_method_depreciation=None))

    # --- SYN04: Missing lt_debt in both years → LVGI=1.0 ---
    rows.append(_base_row("00000004", "000004", "TestCo NoLTDebt", 2019,
        fs_type="OFS", dart_api_source="finstate_all_OFS",
        revenue=600e6, receivables=60e6, cogs=350e6, sga=70e6,
        ppe=250e6, depreciation=25e6, total_assets=1000e6, lt_debt=None,
        net_income=60e6, cfo=55e6, match_method_lt_debt=None))
    rows.append(_base_row("00000004", "000004", "TestCo NoLTDebt", 2020,
        fs_type="OFS", dart_api_source="finstate_all_OFS",
        revenue=700e6, receivables=75e6, cogs=400e6, sga=80e6,
        ppe=270e6, depreciation=27e6, total_assets=1150e6, lt_debt=None,
        net_income=90e6, cfo=85e6, match_method_lt_debt=None))

    # --- SYN05: High FP risk sector (G3510 biotech) ---
    rows.append(_base_row("00000005", "000005", "TestCo Biotech", 2019,
        revenue=50e6, receivables=10e6, cogs=20e6, sga=25e6,
        ppe=200e6, depreciation=40e6, total_assets=500e6, lt_debt=50e6,
        net_income=-30e6, cfo=-10e6,
        wics_sector_code="G3510", wics_sector="제약/바이오/생명"))
    rows.append(_base_row("00000005", "000005", "TestCo Biotech", 2020,
        revenue=150e6, receivables=40e6, cogs=60e6, sga=80e6,
        ppe=250e6, depreciation=50e6, total_assets=700e6, lt_debt=75e6,
        net_income=20e6, cfo=15e6,
        wics_sector_code="G3510", wics_sector="제약/바이오/생명"))

    # --- SYN06: CFS→OFS shift ---
    rows.append(_base_row("00000006", "000006", "TestCo Shift", 2019,
        fs_type="CFS", receivables=100e6, revenue=1000e6, cogs=600e6,
        sga=80e6, ppe=400e6, depreciation=50e6, total_assets=1500e6,
        lt_debt=200e6, net_income=90e6, cfo=80e6))
    rows.append(_base_row("00000006", "000006", "TestCo Shift", 2020,
        fs_type="OFS", dart_api_source="finstate_all_OFS",
        fs_type_shift=True,
        receivables=110e6, revenue=1100e6, cogs=650e6, sga=85e6,
        ppe=420e6, depreciation=55e6, total_assets=1600e6, lt_debt=210e6,
        net_income=90e6, cfo=80e6))

    # --- SYN07–SYN12: Filler companies (same sector as SYN01 to hit ≥10 obs) ---
    for i in range(7, 13):
        code = f"0000{i:04d}"
        ticker = f"00{i:04d}"
        # Vary revenue/assets slightly so scores aren't identical
        scale = 0.8 + (i - 7) * 0.1
        rows.append(_base_row(code, ticker, f"TestCo Filler{i}", 2019,
            revenue=1000e6 * scale, receivables=100e6 * scale,
            cogs=600e6 * scale, sga=80e6 * scale, ppe=400e6 * scale,
            depreciation=50e6 * scale, total_assets=1500e6 * scale,
            lt_debt=200e6 * scale, net_income=80e6 * scale, cfo=70e6 * scale))
        rows.append(_base_row(code, ticker, f"TestCo Filler{i}", 2020,
            revenue=1050e6 * scale, receivables=105e6 * scale,
            cogs=630e6 * scale, sga=82e6 * scale, ppe=410e6 * scale,
            depreciation=52e6 * scale, total_assets=1550e6 * scale,
            lt_debt=205e6 * scale, net_income=85e6 * scale, cfo=75e6 * scale))

    df = pd.DataFrame(rows)
    # Ensure correct dtypes
    for col in ["receivables", "revenue", "cogs", "sga", "ppe", "depreciation",
                "total_assets", "lt_debt", "net_income", "cfo"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["year"] = df["year"].astype("int64")
    df["fs_type_shift"] = df["fs_type_shift"].astype(bool)
    return df


# ---------------------------------------------------------------------------
# Extract Beneish computation from beneish_screen.py cell
# ---------------------------------------------------------------------------

def _compute_beneish(df_fin: pd.DataFrame) -> pd.DataFrame:
    """
    Replicate the _compute_beneish cell from beneish_screen.py.

    This is a direct extraction of the Marimo cell logic so we can call it
    without the Marimo runtime. Any divergence from the source cell is a bug.
    """
    from datetime import date

    df = df_fin.copy()

    numeric_cols = [
        "receivables", "revenue", "cogs", "sga", "ppe", "depreciation",
        "total_assets", "lt_debt", "net_income", "cfo",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values(["corp_code", "year"]).reset_index(drop=True)

    lag_cols = [
        "receivables", "revenue", "cogs", "sga", "ppe",
        "depreciation", "total_assets", "lt_debt",
        "fs_type", "expense_method",
    ]
    for col in lag_cols:
        df[f"{col}_l"] = df.groupby("corp_code")[col].shift(1)

    df["fs_type_switched"] = (
        df["fs_type_l"].notna() & (df["fs_type"] != df["fs_type_l"])
    )

    rev = df["revenue"].replace(0, np.nan)
    rev_l = df["revenue_l"].replace(0, np.nan)
    ta = df["total_assets"].replace(0, np.nan)
    ta_l = df["total_assets_l"].replace(0, np.nan)

    df["gross_profit"] = df["revenue"] - df["cogs"]
    df["gross_profit_l"] = df["revenue_l"] - df["cogs_l"]
    df["gross_margin"] = df["gross_profit"] / rev
    df["gross_margin_l"] = df["gross_profit_l"] / rev_l
    df["soft_assets"] = df["total_assets"] - df["ppe"]
    df["soft_assets_l"] = df["total_assets_l"] - df["ppe_l"]

    # 8 Beneish ratios
    df["dsri"] = (df["receivables"] / rev) / (df["receivables_l"] / rev_l)
    gmi_raw = df["gross_margin_l"] / df["gross_margin"].replace(0, np.nan)
    df["gmi"] = np.where(df["expense_method"] == "nature", 1.0, gmi_raw)
    df["aqi"] = (df["soft_assets"] / ta) / (df["soft_assets_l"] / ta_l)
    df["sgi"] = df["revenue"] / rev_l
    ppe_depr = (df["ppe"] + df["depreciation"]).replace(0, np.nan)
    ppe_depr_l = (df["ppe_l"] + df["depreciation_l"]).replace(0, np.nan)
    df["depi"] = (df["depreciation_l"] / ppe_depr_l) / (df["depreciation"] / ppe_depr)
    sgai_raw = (df["sga"] / rev) / (df["sga_l"] / rev_l)
    df["sgai"] = np.where(df["expense_method"] == "nature", 1.0, sgai_raw)
    df["lvgi"] = (df["lt_debt"] / ta) / (df["lt_debt_l"] / ta_l)
    df["tata"] = (df["net_income"] - df["cfo"]) / ta

    # M-Score
    core_components = ["dsri", "aqi", "sgi", "depi", "tata"]
    df["_null_core"] = df[core_components].isna().sum(axis=1)
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
    df.loc[df["revenue_l"].isna(), "m_score"] = np.nan
    df["flag"] = df["m_score"].notna() & (df["m_score"] > BENEISH_THRESHOLD)

    fp_sectors = {"G3510", "G3520"}
    if "wics_sector_code" in df.columns:
        df["high_fp_risk"] = df["wics_sector_code"].isin(fp_sectors)
    else:
        df["high_fp_risk"] = False

    conditions = [
        ~df["flag"],
        df["flag"] & df["high_fp_risk"],
        df["flag"] & ~df["high_fp_risk"] & (df["m_score"] > -1.0),
    ]
    choices = ["Low", "Medium", "Critical"]
    df["risk_tier"] = np.select(conditions, choices, default="High")

    df["extraction_date"] = date.today().isoformat()

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

    df_scored = df[df["revenue_l"].notna()].copy()
    df_scored["dart_link"] = df_scored["corp_code"].apply(
        lambda c: f"https://dart.fss.or.kr/corp/searchAjax.do?textCrpNm=&textCrpCik={c}"
    )
    lag_drop = [c for c in df_scored.columns if c.endswith("_l")]
    df_scored = df_scored.drop(columns=lag_drop, errors="ignore")
    return df_scored.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic_financials():
    return build_synthetic_financials()


@pytest.fixture(scope="module")
def scored(synthetic_financials):
    return _compute_beneish(synthetic_financials)


# ---------------------------------------------------------------------------
# Tests: Beneish computation
# ---------------------------------------------------------------------------

class TestBeneishE2E:
    """Full Beneish computation on synthetic data with hand-verified results."""

    def test_output_has_expected_rows(self, scored):
        """Only year-2020 rows survive (year-2019 has no T-1 lag)."""
        assert len(scored) == 12  # 12 companies × 1 scoreable year each

    def test_output_has_required_columns(self, scored):
        """All beneish_scores.parquet output columns present."""
        required = [
            "corp_code", "ticker", "company_name", "market", "year",
            "fs_type", "fs_type_switched", "expense_method",
            "dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata",
            "m_score", "flag", "high_fp_risk", "risk_tier",
            "wics_sector_code", "wics_sector", "sector_percentile",
            "dart_link", "extraction_date",
        ]
        for col in required:
            assert col in scored.columns, f"Missing column: {col}"

    def test_syn01_normal_m_score(self, scored):
        """SYN01 (normal company): M-Score is finite and computable."""
        row = scored[scored["corp_code"] == "00000001"]
        assert len(row) == 1
        m = row["m_score"].iloc[0]
        assert np.isfinite(m), f"M-Score should be finite, got {m}"
        # Hand verification: all 8 components populated, score should be < -1.78 (Low risk)
        assert row["flag"].iloc[0] == False
        assert row["risk_tier"].iloc[0] == "Low"

    def test_syn01_components_all_finite(self, scored):
        """SYN01: All 8 components should be non-null and finite."""
        row = scored[scored["corp_code"] == "00000001"].iloc[0]
        for comp in ["dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata"]:
            val = row[comp]
            assert pd.notna(val) and np.isfinite(val), f"{comp} = {val}"

    def test_syn02_nature_method_substitution(self, scored):
        """SYN02 (nature-method): GMI and SGAI forced to 1.0."""
        row = scored[scored["corp_code"] == "00000002"].iloc[0]
        assert row["gmi"] == 1.0, f"GMI should be 1.0 for nature-method, got {row['gmi']}"
        assert row["sgai"] == 1.0, f"SGAI should be 1.0 for nature-method, got {row['sgai']}"
        assert row["expense_method"] == "nature"
        # M-Score should still be computable
        assert pd.notna(row["m_score"])

    def test_syn03_missing_depreciation_depi_imputed(self, scored):
        """SYN03 (no depreciation): DEPI is NaN but M-Score still computed (filled to 1.0)."""
        row = scored[scored["corp_code"] == "00000003"].iloc[0]
        # DEPI raw is NaN when depreciation is null in both years
        assert pd.isna(row["depi"]), "DEPI should be NaN when depreciation missing"
        # But M-Score is still computed because DEPI is filled with 1.0 internally
        assert pd.notna(row["m_score"]), "M-Score should be computed despite missing depreciation"

    def test_syn04_missing_lt_debt_lvgi_imputed(self, scored):
        """SYN04 (no lt_debt): LVGI is NaN but M-Score still computed (filled to 1.0)."""
        row = scored[scored["corp_code"] == "00000004"].iloc[0]
        assert pd.isna(row["lvgi"]), "LVGI should be NaN when lt_debt missing"
        assert pd.notna(row["m_score"]), "M-Score should be computed despite missing lt_debt"

    def test_syn05_high_fp_risk_sector(self, scored):
        """SYN05 (G3510 biotech): high_fp_risk=True, risk_tier='Medium' if flagged."""
        row = scored[scored["corp_code"] == "00000005"].iloc[0]
        assert row["high_fp_risk"] == True
        assert row["wics_sector_code"] == "G3510"
        if row["flag"]:
            assert row["risk_tier"] == "Medium", \
                f"Flagged high-FP-risk company should be 'Medium', got '{row['risk_tier']}'"

    def test_syn06_fs_type_shift_detected(self, scored):
        """SYN06 (CFS→OFS): fs_type_switched=True."""
        row = scored[scored["corp_code"] == "00000006"].iloc[0]
        assert row["fs_type_switched"] == True
        assert row["fs_type"] == "OFS"

    def test_risk_tier_values(self, scored):
        """All risk_tier values are one of the four valid tiers."""
        valid_tiers = {"Low", "Medium", "High", "Critical"}
        actual = set(scored["risk_tier"].unique())
        assert actual.issubset(valid_tiers), f"Unexpected tiers: {actual - valid_tiers}"

    def test_flag_matches_threshold(self, scored):
        """flag=True iff m_score > -1.78 and m_score is not null."""
        for _, row in scored.iterrows():
            if pd.isna(row["m_score"]):
                assert row["flag"] == False, f"{row['corp_code']}: null m_score should have flag=False"
            elif row["m_score"] > BENEISH_THRESHOLD:
                assert row["flag"] == True, f"{row['corp_code']}: m_score={row['m_score']} > {BENEISH_THRESHOLD} should be flagged"
            else:
                assert row["flag"] == False, f"{row['corp_code']}: m_score={row['m_score']} <= {BENEISH_THRESHOLD} should not be flagged"

    def test_dart_link_format(self, scored):
        """Every row has a DART link containing its corp_code."""
        for _, row in scored.iterrows():
            assert row["corp_code"] in row["dart_link"]
            assert row["dart_link"].startswith("https://dart.fss.or.kr/")

    def test_sector_percentile_within_range(self, scored):
        """Non-null sector_percentile values are in (0, 100]."""
        valid = scored["sector_percentile"].dropna()
        if len(valid) > 0:
            assert (valid > 0).all() and (valid <= 100).all()

    def test_no_year_2019_rows(self, scored):
        """Year 2019 rows are dropped (no T-1 lag data for 2019)."""
        assert (scored["year"] == 2019).sum() == 0


# ---------------------------------------------------------------------------
# Tests: Chart generation
# ---------------------------------------------------------------------------

class TestChartGeneration:
    """Verify all chart functions execute without error on synthetic data."""

    def test_chart_distribution(self, scored):
        from krff.charts import chart_distribution
        fig = chart_distribution(scored)
        assert fig is not None
        assert hasattr(fig, "to_html")

    def test_chart_risk_sector(self, scored):
        from krff.charts import chart_risk_sector
        fig = chart_risk_sector(scored)
        assert fig is not None

    def test_chart_year_trend(self, scored):
        from krff.charts import chart_year_trend
        fig = chart_year_trend(scored)
        assert fig is not None

    def test_chart_components(self, scored):
        from krff.charts import chart_components
        fig = chart_components(scored)
        assert fig is not None

    def test_chart_heatmap(self, scored):
        from krff.charts import chart_heatmap
        fig = chart_heatmap(scored)
        assert fig is not None

    def test_generate_charts_writes_html(self, scored, tmp_path):
        from krff.charts import generate_charts
        html_path = generate_charts(scored, tmp_path)
        assert html_path.exists()
        content = html_path.read_text(encoding="utf-8")
        assert "<h2>" in content
        assert "Chart 1" in content
        assert len(content) > 10_000, f"HTML too small ({len(content)} bytes)"

    def test_export_html_contains_all_charts(self, scored, tmp_path):
        from krff.charts import generate_charts
        html_path = generate_charts(scored, tmp_path)
        content = html_path.read_text(encoding="utf-8")
        for i in range(1, 6):
            assert f"Chart {i}" in content, f"Missing Chart {i} in HTML output"


# ---------------------------------------------------------------------------
# Tests: Parquet round-trip (write + read back)
# ---------------------------------------------------------------------------

class TestParquetRoundTrip:
    """Verify synthetic data survives parquet serialization."""

    def test_financials_parquet_roundtrip(self, synthetic_financials, tmp_path):
        path = tmp_path / "company_financials.parquet"
        synthetic_financials.to_parquet(path, index=False, engine="pyarrow")
        reloaded = pd.read_parquet(path)
        assert len(reloaded) == len(synthetic_financials)
        assert list(reloaded.columns) == list(synthetic_financials.columns)

    def test_scores_parquet_roundtrip(self, scored, tmp_path):
        output_cols = [
            "corp_code", "ticker", "company_name", "market", "year",
            "extraction_date", "fs_type", "fs_type_switched", "expense_method",
            "dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata",
            "m_score", "flag", "high_fp_risk", "risk_tier",
            "wics_sector_code", "wics_sector", "sector_percentile", "dart_link",
        ]
        available = [c for c in output_cols if c in scored.columns]
        df_out = scored[available]
        path = tmp_path / "beneish_scores.parquet"
        df_out.to_parquet(path, index=False, engine="pyarrow")
        reloaded = pd.read_parquet(path)
        assert len(reloaded) == len(df_out)
        # Verify M-Scores survived
        assert reloaded["m_score"].notna().sum() == df_out["m_score"].notna().sum()


# ---------------------------------------------------------------------------
# Tests: src/ wrapper functions
# ---------------------------------------------------------------------------

class TestSrcWrappers:
    """Verify src/analysis.py and src/charts.py are importable and callable."""

    def test_src_charts_importable(self):
        from krff.charts import generate_charts, chart_distribution
        assert callable(generate_charts)
        assert callable(chart_distribution)

    def test_src_analysis_importable(self):
        from krff.analysis import run_beneish_screen
        assert callable(run_beneish_screen)
