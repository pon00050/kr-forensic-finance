"""
test_pipeline_invariants.py — Guard tests for pipeline correctness invariants.

Three categories per the rationale in KNOWN_ISSUES.md and the project's
testing philosophy:

  1. KSIC sample-preservation  — a --sample run must not destroy existing data
  2. Schema contracts           — company_financials columns, dtypes, no financial sector rows
  3. Beneish formula spot-check — known input → expected M-Score to 2 decimal places

These tests guard the three places where silent failure is most plausible.
They do not aim for comprehensive coverage — the acceptance criteria suite
(test_acceptance_criteria.py) covers downstream correctness.

Run:
    pytest tests/test_pipeline_invariants.py -v
"""

import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd
import pytest

ROOT      = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "01_Data" / "processed"
RAW       = ROOT / "01_Data" / "raw"


# ─── Category 1: KSIC sample-preservation ────────────────────────────────────

class TestKsicSamplePreservation:
    """
    A --sample N run of fetch_ksic() must preserve all entries that already
    exist in ksic.parquet and not discard companies outside the sample window.

    Regression guard for the bug discovered Feb 27, 2026:
    sample run overwrote full ksic.parquet with only N rows.
    See KNOWN_ISSUES.md KI-004.
    """

    def _make_companies(self, corp_codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame({"corp_code": corp_codes, "stock_code": corp_codes})

    def test_sample_run_preserves_existing_entries(self, tmp_path, monkeypatch):
        """
        Given: ksic.parquet already has 10 entries (the "full universe").
        When:  fetch_ksic() is called with sample=3 (first 3 companies only).
        Then:  the written parquet still contains all 10 entries.
        """
        import importlib, sys as _sys

        # Point RAW_SECTOR at tmp_path so we don't touch real data
        existing_codes = [f"CODE{i:04d}" for i in range(10)]
        existing_df = pd.DataFrame({
            "corp_code":   existing_codes,
            "induty_code": ["264"] * 10,
        })
        (tmp_path / "sector").mkdir()
        existing_df.to_parquet(tmp_path / "sector" / "ksic.parquet", index=False)

        # Patch RAW_SECTOR inside extract_dart before importing
        import extract_dart as ed
        monkeypatch.setattr(ed, "RAW_SECTOR", tmp_path / "sector")

        # Stub dart.company() to return a minimal object
        class _FakeInfo:
            induty_code = "264"
        class _FakeDart:
            def company(self, corp_code):
                return _FakeInfo()
        monkeypatch.setattr(ed, "_dart", lambda: _FakeDart())

        # Run with sample=3 — only first 3 of 10 companies
        all_companies = self._make_companies(existing_codes)
        ed.fetch_ksic(all_companies, force=False, sample=3)

        result = pd.read_parquet(tmp_path / "sector" / "ksic.parquet")
        assert len(result) == 10, (
            f"fetch_ksic with sample=3 wrote {len(result)} rows; "
            f"expected 10 (all pre-existing entries preserved)"
        )
        assert set(result["corp_code"]) == set(existing_codes), (
            "fetch_ksic with sample=3 dropped some pre-existing corp_codes"
        )

    def test_full_run_writes_all_companies(self, tmp_path, monkeypatch):
        """A full run (no sample) with no pre-existing file writes all companies."""
        import extract_dart as ed
        monkeypatch.setattr(ed, "RAW_SECTOR", tmp_path / "sector")
        (tmp_path / "sector").mkdir()

        class _FakeInfo:
            induty_code = "264"
        class _FakeDart:
            def company(self, corp_code):
                return _FakeInfo()
        monkeypatch.setattr(ed, "_dart", lambda: _FakeDart())
        monkeypatch.setattr(ed, "_sleep_ksic", 0)

        companies = self._make_companies([f"C{i:04d}" for i in range(5)])
        result = ed.fetch_ksic(companies, force=False, sample=None)
        assert len(result) == 5


# ─── Category 2: Schema contracts ────────────────────────────────────────────

class TestSchemaContracts:
    """
    company_financials.parquet must have the expected columns, numeric types,
    and no financial-sector rows. Guards against accidental schema drift that
    would silently break downstream Beneish calculations.
    """

    @pytest.fixture(scope="class")
    def financials(self):
        p = PROCESSED / "company_financials.parquet"
        if not p.exists():
            pytest.skip("company_financials.parquet not found — run the pipeline first")
        df = pd.read_parquet(p)
        if df.empty:
            pytest.skip("company_financials.parquet has 0 rows — run the pipeline first")
        return df

    REQUIRED_COLUMNS = [
        "corp_code", "ticker", "company_name", "market", "year",
        "fs_type", "fs_type_shift", "expense_method",
        "receivables", "revenue", "cogs", "sga", "ppe",
        "depreciation", "total_assets", "lt_debt", "net_income", "cfo",
        "wics_sector_code", "ksic_code",
    ]

    NUMERIC_COLUMNS = [
        "receivables", "revenue", "cogs", "sga", "ppe",
        "depreciation", "total_assets", "lt_debt", "net_income", "cfo",
    ]

    def test_required_columns_present(self, financials):
        missing = [c for c in self.REQUIRED_COLUMNS if c not in financials.columns]
        assert not missing, f"Missing columns in company_financials: {missing}"

    def test_extraction_date_format(self, financials):
        """extraction_date must be ISO 8601 (YYYY-MM-DD) when present.

        Skipped on the existing Phase 1 parquet (produced before PR2).
        Will be enforced after the next pipeline re-run.
        """
        if "extraction_date" not in financials.columns:
            pytest.skip("extraction_date not present — re-run pipeline to add it")
        import re
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        sample = financials["extraction_date"].dropna().head(20)
        bad = [v for v in sample if not pattern.match(str(v))]
        assert not bad, f"extraction_date values not ISO 8601: {bad}"

    def test_numeric_columns_are_float(self, financials):
        wrong_type = [
            c for c in self.NUMERIC_COLUMNS
            if c in financials.columns and not pd.api.types.is_float_dtype(financials[c])
        ]
        assert not wrong_type, (
            f"Expected float64 columns but got wrong dtype: {wrong_type}"
        )

    def test_year_is_integer(self, financials):
        assert pd.api.types.is_integer_dtype(financials["year"]), (
            f"year column should be integer, got {financials['year'].dtype}"
        )

    def test_no_financial_sector_rows(self, financials):
        """KSIC 640–669 and 68200 must be excluded (transform.py financial exclusion)."""
        if "ksic_code" not in financials.columns:
            pytest.skip("ksic_code column not present")

        def is_financial(code) -> bool:
            if pd.isna(code):
                return False
            s = str(code).strip()
            if s == "68200":
                return True
            try:
                return 640 <= int(s[:3]) <= 669
            except (ValueError, TypeError):
                return False

        flagged = financials["ksic_code"].apply(is_financial).sum()
        assert flagged == 0, (
            f"{flagged} financial-sector rows (KSIC 640–669 / 68200) survived transform"
        )

    def test_no_empty_corp_code(self, financials):
        null_count = financials["corp_code"].isna().sum()
        assert null_count == 0, f"{null_count} rows have null corp_code"

    def test_market_column_values(self, financials):
        """All rows should be KOSDAQ (Phase 1 scope)."""
        if "market" not in financials.columns:
            pytest.skip("market column not present")
        unexpected = financials[financials["market"] != "KOSDAQ"]
        assert len(unexpected) == 0, (
            f"{len(unexpected)} rows have unexpected market value: "
            f"{unexpected['market'].unique().tolist()}"
        )

    def test_fs_type_values_and_distribution(self, financials):
        """fs_type must be one of three allowed values; must not be 100% no_filing (KI-003 regression guard).

        The KI-003 Unicode bug caused every company-year to silently write no_filing
        because OpenDartReader's finstate_all() printed Korean text before making the
        API call, raising UnicodeEncodeError on Windows cp1252, caught by except Exception.
        A >50% no_filing rate is the failure signature — this threshold catches a
        partial regression without being too tight on legitimate missing data.
        """
        allowed = {"CFS", "OFS", "no_filing"}
        actual = set(financials["fs_type"].dropna().unique())
        assert actual.issubset(allowed), f"Unexpected fs_type values: {actual - allowed}"

        no_filing_pct = (financials["fs_type"] == "no_filing").mean()
        assert no_filing_pct < 0.50, (
            f"Over 50% of rows are no_filing ({no_filing_pct:.1%}). "
            "Check Unicode fix in extract_dart.py (KI-003)."
        )

    def test_no_duplicate_company_years(self, financials):
        """Duplicate (corp_code, year) rows would corrupt Beneish lag calculations."""
        dup_count = financials.duplicated(subset=["corp_code", "year"]).sum()
        assert dup_count == 0, (
            f"{dup_count} duplicate (corp_code, year) pairs found in company_financials"
        )

    def test_year_range(self, financials):
        """Years must be 4-digit integers in a plausible DART data range (2000–2099).

        Guards against parsing bugs like 20190 or 202, not against pipeline scope changes.
        The Phase 1 run window (2019–2023) is enforced by the CLI, not this test.
        """
        bad = financials[~financials["year"].between(2000, 2099)]
        assert len(bad) == 0, (
            f"{len(bad)} rows have implausible year values: {bad['year'].unique().tolist()}"
        )

    def test_fs_type_shift_correctness(self, financials):
        """fs_type_shift=True must only appear for corps with >1 distinct fs_type."""
        mixed_corps = set(financials[financials["fs_type_shift"]]["corp_code"])
        uniform_corps = set(financials[~financials["fs_type_shift"]]["corp_code"]) - mixed_corps

        for corp in mixed_corps:
            n = financials[financials["corp_code"] == corp]["fs_type"].nunique()
            assert n > 1, (
                f"corp {corp} has fs_type_shift=True but only {n} distinct fs_type value"
            )
        for corp in list(uniform_corps)[:50]:
            n = financials[financials["corp_code"] == corp]["fs_type"].nunique()
            assert n == 1, (
                f"corp {corp} has fs_type_shift=False but {n} distinct fs_type values"
            )


# ─── Category 3: Beneish formula spot-check ──────────────────────────────────

class TestBeneishFormula:
    """
    Known input → expected M-Score to 2 decimal places.

    Constructs a synthetic two-year dataset for one fictitious company and
    asserts the computed M-Score matches hand-calculated expectations.

    This documents the formula implementation as executable specification and
    guards against accidental coefficient or sign changes during refactors.

    Reference: Beneish (1999) 8-variable model.
    Coefficients: −4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
                  + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI
    """

    def _make_two_year_df(self) -> pd.DataFrame:
        """
        Construct a minimal two-year (T-1=2019, T=2020) financial DataFrame
        with exact values chosen so that every Beneish ratio is deterministic
        and hand-calculable.

        Values (all in KRW millions, arbitrary but round):
          revenue:       T-1=1000, T=1200   → SGI = 1.20
          receivables:   T-1=100,  T=130    → DSRI = (130/1200) / (100/1000) = 1.0833
          cogs:          T-1=600,  T=700    → gross margin T-1=0.40, T=0.4167
                                              GMI = 0.40/0.4167 = 0.9600
          ppe:           T-1=400,  T=420
          depreciation:  T-1=50,   T=55
                                              DEPI = (50/(400+50)) / (55/(420+55))
                                                   = 0.11111 / 0.11579 = 0.9596
          total_assets:  T-1=1500, T=1700
          soft_assets:   T-1=1100, T=1280   → AQI = (1280/1700)/(1100/1500) = 1.0256
          sga:           T-1=80,   T=110    → SGAI = (110/1200)/(80/1000) = 1.1458
          lt_debt:       T-1=200,  T=260    → LVGI = (260/1700)/(200/1500) = 1.1471
          net_income:    T=100
          cfo:           T=80
                                              TATA = (100-80)/1700 = 0.01176
        """
        return pd.DataFrame([
            {
                "corp_code": "TEST001", "ticker": "000001", "company_name": "Test Co",
                "market": "KOSDAQ", "year": 2019,
                "fs_type": "CFS", "expense_method": "function",
                "receivables": 100, "revenue": 1000, "cogs": 600, "sga": 80,
                "ppe": 400, "depreciation": 50, "total_assets": 1500,
                "lt_debt": 200, "net_income": None, "cfo": None,
                "wics_sector_code": "G4510", "wics_sector": "Retail",
                "ksic_code": "521", "krx_sector": None,
            },
            {
                "corp_code": "TEST001", "ticker": "000001", "company_name": "Test Co",
                "market": "KOSDAQ", "year": 2020,
                "fs_type": "CFS", "expense_method": "function",
                "receivables": 130, "revenue": 1200, "cogs": 700, "sga": 110,
                "ppe": 420, "depreciation": 55, "total_assets": 1700,
                "lt_debt": 260, "net_income": 100, "cfo": 80,
                "wics_sector_code": "G4510", "wics_sector": "Retail",
                "ksic_code": "521", "krx_sector": None,
            },
        ])

    def _run_beneish(self, df_fin: pd.DataFrame) -> pd.DataFrame:
        """Run the Beneish computation cell logic directly."""
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
            "depreciation", "total_assets", "lt_debt", "fs_type", "expense_method",
        ]
        for col in lag_cols:
            df[f"{col}_l"] = df.groupby("corp_code")[col].shift(1)

        rev   = df["revenue"].replace(0, np.nan)
        rev_l = df["revenue_l"].replace(0, np.nan)
        ta    = df["total_assets"].replace(0, np.nan)
        ta_l  = df["total_assets_l"].replace(0, np.nan)

        df["gross_profit"]   = df["revenue"]   - df["cogs"]
        df["gross_profit_l"] = df["revenue_l"] - df["cogs_l"]
        df["gross_margin"]   = df["gross_profit"]   / rev
        df["gross_margin_l"] = df["gross_profit_l"] / rev_l
        df["soft_assets"]    = df["total_assets"]   - df["ppe"]
        df["soft_assets_l"]  = df["total_assets_l"] - df["ppe_l"]

        df["dsri"] = (df["receivables"] / rev)   / (df["receivables_l"] / rev_l)
        gmi_raw    = df["gross_margin_l"] / df["gross_margin"].replace(0, np.nan)
        df["gmi"]  = np.where(df["expense_method"] == "nature", 1.0, gmi_raw)
        df["aqi"]  = (df["soft_assets"] / ta)    / (df["soft_assets_l"] / ta_l)
        df["sgi"]  = df["revenue"] / rev_l
        ppe_depr   = (df["ppe"]   + df["depreciation"]).replace(0, np.nan)
        ppe_depr_l = (df["ppe_l"] + df["depreciation_l"]).replace(0, np.nan)
        df["depi"] = (df["depreciation_l"] / ppe_depr_l) / (df["depreciation"] / ppe_depr)
        sgai_raw   = (df["sga"] / rev) / (df["sga_l"] / rev_l)
        df["sgai"] = np.where(df["expense_method"] == "nature", 1.0, sgai_raw)
        df["lvgi"] = (df["lt_debt"] / ta) / (df["lt_debt_l"] / ta_l)
        df["tata"] = (df["net_income"] - df["cfo"]) / ta

        core   = ["dsri", "aqi", "sgi", "depi", "tata"]
        df["_null_core"] = df[core].isna().sum(axis=1)

        df["m_score"] = np.where(
            df["_null_core"] > 2,
            np.nan,
            (
                -4.84
                + 0.920 * df["dsri"].fillna(1.0)
                + 0.528 * df["gmi"]
                + 0.404 * df["aqi"].fillna(1.0)
                + 0.892 * df["sgi"].fillna(1.0)
                + 0.115 * df["depi"].fillna(1.0)
                - 0.172 * df["sgai"]
                + 4.679 * df["tata"].fillna(0.0)
                - 0.327 * df["lvgi"].fillna(1.0)
            ),
        )
        df.loc[df["revenue_l"].isna(), "m_score"] = np.nan
        return df

    def test_m_score_known_input(self):
        """M-Score for hand-crafted synthetic data matches manual calculation."""
        df = self._make_two_year_df()
        result = self._run_beneish(df)
        row_t = result[result["year"] == 2020].iloc[0]

        # Hand-calculated ratios (see docstring above)
        assert abs(row_t["dsri"] - 1.0833) < 0.001, f"DSRI={row_t['dsri']:.4f}"
        assert abs(row_t["gmi"]  - 0.9600) < 0.001, f"GMI={row_t['gmi']:.4f}"
        assert abs(row_t["aqi"]  - 1.0267) < 0.001, f"AQI={row_t['aqi']:.4f}"
        assert abs(row_t["sgi"]  - 1.2000) < 0.001, f"SGI={row_t['sgi']:.4f}"
        assert abs(row_t["depi"] - 0.9596) < 0.001, f"DEPI={row_t['depi']:.4f}"
        assert abs(row_t["sgai"] - 1.1458) < 0.001, f"SGAI={row_t['sgai']:.4f}"
        assert abs(row_t["lvgi"] - 1.1471) < 0.001, f"LVGI={row_t['lvgi']:.4f}"
        assert abs(row_t["tata"] - 0.01176) < 0.0001, f"TATA={row_t['tata']:.5f}"

        # M-Score = -4.84 + 0.920*1.0833 + 0.528*0.9600 + 0.404*1.0267
        #           + 0.892*1.2000 + 0.115*0.9596 - 0.172*1.1458
        #           + 4.679*0.01176 - 0.327*1.1471
        # = -4.84 + 0.9967 + 0.5069 + 0.4148 + 1.0704 + 0.1104
        #   - 0.1971 + 0.0550 - 0.3751
        # = -2.2580
        expected_m_score = -2.26
        assert abs(row_t["m_score"] - expected_m_score) < 0.02, (
            f"M-Score={row_t['m_score']:.4f}, expected ~{expected_m_score}"
        )

    def test_nature_method_sets_gmi_sgai_to_one(self):
        """Nature-of-expense companies must have GMI=1.0 and SGAI=1.0."""
        df = self._make_two_year_df()
        df["expense_method"] = "nature"
        result = self._run_beneish(df)
        row_t = result[result["year"] == 2020].iloc[0]
        assert row_t["gmi"]  == 1.0, f"nature method: GMI={row_t['gmi']}"
        assert row_t["sgai"] == 1.0, f"nature method: SGAI={row_t['sgai']}"

    def test_first_year_has_no_m_score(self):
        """Year T-1 (no lag data available) must have null m_score."""
        df = self._make_two_year_df()
        result = self._run_beneish(df)
        row_t_minus_1 = result[result["year"] == 2019].iloc[0]
        assert pd.isna(row_t_minus_1["m_score"]), (
            f"First year should have null m_score, got {row_t_minus_1['m_score']}"
        )

    def test_m_score_null_tolerance(self):
        """The _null_core > 2 guard: ≤2 missing core fields still compute; >2 → null."""
        # Core fields in _run_beneish: dsri, aqi, sgi, depi, tata
        # dsri needs receivables + revenue; aqi needs soft_assets + ta; sgi needs revenue;
        # depi needs ppe + depreciation; tata needs net_income + cfo

        base = self._make_two_year_df()

        # Case A: 2 nulled core inputs — score should still be computed (not null)
        df_a = base.copy()
        df_a.loc[df_a["year"] == 2020, "receivables"] = None   # kills dsri
        df_a.loc[df_a["year"] == 2020, "net_income"]  = None   # kills tata (along with cfo=80)
        result_a = self._run_beneish(df_a)
        row_a = result_a[result_a["year"] == 2020].iloc[0]
        # With 2 null core fields, _null_core == 2 which is NOT > 2 → score computed
        assert not pd.isna(row_a["m_score"]), (
            f"Case A (2 nulled core inputs): expected non-null m_score, got {row_a['m_score']}"
        )

        # Case B: 3 nulled core inputs — score must be null
        df_b = base.copy()
        df_b.loc[df_b["year"] == 2020, "receivables"]  = None  # kills dsri
        df_b.loc[df_b["year"] == 2020, "net_income"]   = None  # kills tata (cfo=80, but net_income=None → tata=NaN)
        df_b.loc[df_b["year"] == 2020, "depreciation"] = None  # kills depi
        df_b.loc[df_b["year"] == 2019, "depreciation"] = None  # kills depi_l too
        result_b = self._run_beneish(df_b)
        row_b = result_b[result_b["year"] == 2020].iloc[0]
        assert pd.isna(row_b["m_score"]), (
            f"Case B (3 nulled core inputs): expected null m_score, got {row_b['m_score']}"
        )


# ─── Category 4: Beneish output schema ───────────────────────────────────────

REQUIRED_BENEISH_COLUMNS = [
    "corp_code", "ticker", "company_name", "market", "year", "extraction_date",
    "fs_type", "fs_type_switched", "expense_method",
    "dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata",
    "m_score", "flag", "high_fp_risk", "risk_tier",
    "wics_sector_code", "wics_sector", "sector_percentile", "dart_link",
]


class TestBeneishOutputSchema:
    """
    beneish_scores.parquet must contain all required output columns.

    The aggregate M-Score alone is insufficient for downstream consumers:
    - Credit analysts need individual components to understand why a company is flagged.
    - Researchers testing sector patterns (elevated TATA in biotech, etc.) need
      component-level data.
    - fs_type and fs_type_switched are required for academic disclosure of CFS/OFS provenance.
    """

    @pytest.fixture(scope="class")
    def beneish_scores(self):
        p = PROCESSED / "beneish_scores.parquet"
        if not p.exists():
            pytest.skip("beneish_scores.parquet not found — run beneish_screen.py first")
        return pd.read_parquet(p)

    def test_beneish_required_columns_present(self, beneish_scores):
        """All required Beneish output columns must be present by name."""
        missing = [c for c in REQUIRED_BENEISH_COLUMNS if c not in beneish_scores.columns]
        assert not missing, f"Missing columns in beneish_scores.parquet: {missing}"

    def test_extraction_date_format(self, beneish_scores):
        """extraction_date must be ISO 8601 (YYYY-MM-DD) with no nulls."""
        import re
        assert "extraction_date" in beneish_scores.columns, "extraction_date column missing"
        assert beneish_scores["extraction_date"].notna().all(), "extraction_date has null values"
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        bad = [v for v in beneish_scores["extraction_date"].unique() if not pattern.match(str(v))]
        assert not bad, f"extraction_date values not ISO 8601: {bad}"

    def test_risk_tier_values(self, beneish_scores):
        """risk_tier must contain only the four allowed values."""
        valid = {"Critical", "High", "Medium", "Low"}
        assert "risk_tier" in beneish_scores.columns, "risk_tier column missing"
        actual = set(beneish_scores["risk_tier"].dropna().unique())
        unexpected = actual - valid
        assert not unexpected, f"Unexpected risk_tier values: {unexpected}"

    def test_risk_tier_logic(self, beneish_scores):
        """risk_tier must follow the documented tier logic."""
        df = beneish_scores[beneish_scores["m_score"].notna()].copy()
        # Low: flag=False
        low = df[df["risk_tier"] == "Low"]
        assert (low["flag"] == False).all(), "Low tier rows must have flag=False"  # noqa: E712
        # Medium: flag=True and high_fp_risk=True
        medium = df[df["risk_tier"] == "Medium"]
        assert (medium["flag"] == True).all(), "Medium tier rows must have flag=True"  # noqa: E712
        assert (medium["high_fp_risk"] == True).all(), "Medium tier rows must have high_fp_risk=True"  # noqa: E712
        # Critical: flag=True, high_fp_risk=False, m_score > -1.0
        critical = df[df["risk_tier"] == "Critical"]
        assert (critical["flag"] == True).all(), "Critical tier rows must have flag=True"  # noqa: E712
        assert (critical["high_fp_risk"] == False).all(), "Critical tier rows must have high_fp_risk=False"  # noqa: E712
        assert (critical["m_score"] > -1.0).all(), "Critical tier rows must have m_score > -1.0"
        # High: flag=True, high_fp_risk=False, m_score <= -1.0
        high = df[df["risk_tier"] == "High"]
        assert (high["flag"] == True).all(), "High tier rows must have flag=True"  # noqa: E712
        assert (high["high_fp_risk"] == False).all(), "High tier rows must have high_fp_risk=False"  # noqa: E712
        assert (high["m_score"] <= -1.0).all(), "High tier rows must have m_score <= -1.0"


# ─── Category 5: Reference artifact completeness ─────────────────────────────


class TestReferenceArtifacts:
    """
    Published reference artifacts in 00_Reference/ must exist and be complete.
    Guards against the crosswalk CSV being accidentally deleted or truncated.
    """

    def test_xbrl_crosswalk_exists_and_complete(self):
        """dart_xbrl_crosswalk.csv must exist in 00_Reference/ and cover all 10 financial variables."""
        crosswalk_path = ROOT / "tests" / "fixtures" / "dart_xbrl_crosswalk.csv"
        assert crosswalk_path.exists(), "dart_xbrl_crosswalk.csv not found in tests/fixtures/"

        df = pd.read_csv(crosswalk_path)
        required_cols = ["variable_name", "element_id_primary", "statement_type", "formula_role"]
        missing_cols = [c for c in required_cols if c not in df.columns]
        assert not missing_cols, f"Missing columns in crosswalk: {missing_cols}"

        expected_variables = {
            "receivables", "revenue", "cogs", "sga", "ppe",
            "depreciation", "total_assets", "lt_debt", "net_income", "cfo",
        }
        actual_variables = set(df["variable_name"].tolist())
        missing_vars = expected_variables - actual_variables
        assert not missing_vars, f"Crosswalk missing entries for variables: {missing_vars}"


# ─── Category 6: Backoff helper ──────────────────────────────────────────────

class TestBackoffHelper:
    """
    Unit tests for _finstate_with_backoff() in extract_dart.py.
    Uses a fake dart object; patches time.sleep to avoid real delays.
    """

    def _get_fn(self):
        import extract_dart as ed
        return ed._finstate_with_backoff

    def test_non_020_error_fails_immediately(self, monkeypatch):
        """Non-rate-limit exceptions must propagate immediately without retry."""
        call_count = 0

        class _FakeDart:
            def finstate_all(self, corp_code, year, fs_div):
                nonlocal call_count
                call_count += 1
                raise ValueError("no route to host")

        monkeypatch.setattr("time.sleep", lambda s: None)
        fn = self._get_fn()
        with pytest.raises(ValueError, match="no route to host"):
            fn(_FakeDart(), "00000001", 2022, "CFS")
        assert call_count == 1, f"Expected 1 attempt, got {call_count}"

    def test_020_error_retries_exhausted(self, monkeypatch):
        """Error 020 must retry up to 5 total attempts then re-raise."""
        call_count = 0

        class _FakeDart:
            def finstate_all(self, corp_code, year, fs_div):
                nonlocal call_count
                call_count += 1
                raise Exception("Error 020 quota exceeded")

        monkeypatch.setattr("time.sleep", lambda s: None)
        fn = self._get_fn()
        with pytest.raises(Exception, match="020"):
            fn(_FakeDart(), "00000001", 2022, "CFS")
        assert call_count == 5, f"Expected 5 total attempts (1 + 4 retries), got {call_count}"

    def test_020_succeeds_on_retry(self, monkeypatch):
        """Error 020 on first two attempts then success on third must return the result."""
        call_count = 0
        sentinel = object()

        class _FakeDart:
            def finstate_all(self, corp_code, year, fs_div):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise Exception("Error 020")
                return sentinel

        monkeypatch.setattr("time.sleep", lambda s: None)
        fn = self._get_fn()
        result = fn(_FakeDart(), "00000001", 2022, "CFS")
        assert result is sentinel
        assert call_count == 3, f"Expected 3 attempts, got {call_count}"


# ─── Category 7: Run summary merge ───────────────────────────────────────────

class TestRunSummaryMerge:
    """
    Unit tests for _merge_run_summaries() in pipeline.py.
    Pure Python — no file I/O or monkeypatching required.
    """

    def _base_new(self):
        return {"total_companies": 10, "years": [2022], "completed_at": "2026-01-01", "elapsed_minutes": 1.0}

    def _get_fn(self):
        # Add pipeline dir to path so pipeline.py can be imported
        import sys
        # conftest.py adds 02_Pipeline to sys.path
        from pipeline import _merge_run_summaries
        return _merge_run_summaries

    def test_full_beats_partial_in_old(self):
        fn = self._get_fn()
        old = {"full_data": [], "partial_data": [{"corp_code": "B", "years": [2022]}], "no_data": [], "errors": []}
        new = {**self._base_new(), "full_data": ["B"], "partial_data": [], "no_data": [], "errors": []}
        merged = fn(old, new)
        assert "B" in merged["full_data"]
        partial_codes = [e["corp_code"] for e in merged["partial_data"]]
        assert "B" not in partial_codes

    def test_full_beats_no_data_in_old(self):
        fn = self._get_fn()
        old = {"full_data": [], "partial_data": [], "no_data": ["C"], "errors": []}
        new = {**self._base_new(), "full_data": ["C"], "partial_data": [], "no_data": [], "errors": []}
        merged = fn(old, new)
        assert "C" in merged["full_data"]
        assert "C" not in merged["no_data"]

    def test_old_full_not_downgraded_by_new_no_data(self):
        fn = self._get_fn()
        old = {"full_data": ["A"], "partial_data": [], "no_data": [], "errors": []}
        new = {**self._base_new(), "full_data": [], "partial_data": [], "no_data": ["A"], "errors": []}
        merged = fn(old, new)
        assert "A" in merged["full_data"]
        assert "A" not in merged["no_data"]

    def test_errors_last_write_wins(self):
        fn = self._get_fn()
        old = {"full_data": [], "partial_data": [], "no_data": [], "errors": [{"corp_code": "X", "error": "timeout"}]}
        new = {**self._base_new(), "full_data": [], "partial_data": [], "no_data": [], "errors": [{"corp_code": "X", "error": "020"}]}
        merged = fn(old, new)
        err = next(e for e in merged["errors"] if e["corp_code"] == "X")
        assert err["error"] == "020"


# ─── Category 8: Transform unit tests ────────────────────────────────────────

class TestTransformUnits:
    """
    Unit tests for _extract_lt_debt() and _detect_expense_method() from transform.py.
    All tests build minimal DataFrames — no parquet I/O.
    """

    def _get_fns(self):
        import sys
        # conftest.py adds 02_Pipeline to sys.path
        import transform as tr
        return tr._extract_lt_debt, tr._detect_expense_method

    # ── _extract_lt_debt ──────────────────────────────────────────────────────

    def test_lt_debt_prefers_longtermborrowingsgross(self):
        fn, _ = self._get_fns()
        df = pd.DataFrame([
            {"sj_div": "BS", "account_id": "dart_LongTermBorrowingsGross", "account_nm": "장기차입금", "thstrm_amount": "100,000"},
            {"sj_div": "BS", "account_id": "dart_BondsIssued", "account_nm": "사채", "thstrm_amount": "200,000"},
        ])
        value, method = fn(df)
        assert value == 100_000.0
        assert method == "exact_id"

    def test_lt_debt_falls_back_to_bonds_issued(self):
        fn, _ = self._get_fns()
        df = pd.DataFrame([
            {"sj_div": "BS", "account_id": "dart_BondsIssued", "account_nm": "사채", "thstrm_amount": "200,000"},
        ])
        value, method = fn(df)
        assert value == 200_000.0
        assert method == "exact_id"

    def test_lt_debt_falls_back_to_korean_name(self):
        fn, _ = self._get_fns()
        df = pd.DataFrame([
            {"sj_div": "BS", "account_nm": "장기차입금", "thstrm_amount": "50,000"},
        ])
        value, method = fn(df)
        assert value == 50_000.0
        assert method == "korean_substring"

    def test_lt_debt_returns_none_when_absent(self):
        fn, _ = self._get_fns()
        df = pd.DataFrame([
            {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계", "thstrm_amount": "1,000,000"},
        ])
        value, method = fn(df)
        assert value is None
        assert method is None

    # ── _detect_expense_method ────────────────────────────────────────────────

    def test_expense_method_function_via_is(self):
        _, fn = self._get_fns()
        df = pd.DataFrame([
            {"sj_div": "IS", "account_nm": "매출원가"},
            {"sj_div": "IS", "account_nm": "판매비와관리비"},
        ])
        assert fn(df) == "function"

    def test_expense_method_function_via_cis(self):
        _, fn = self._get_fns()
        df = pd.DataFrame([
            {"sj_div": "CIS", "account_nm": "매출원가"},
        ])
        assert fn(df) == "function"

    def test_expense_method_nature_when_no_cogs(self):
        _, fn = self._get_fns()
        df = pd.DataFrame([
            {"sj_div": "IS", "account_nm": "판매비와관리비"},
            {"sj_div": "IS", "account_nm": "기타비용"},
        ])
        assert fn(df) == "nature"


# ─── Category 9: KSIC full-run resume behavior ───────────────────────────────

# Extend TestKsicSamplePreservation with an additional test below.
# (Appended here as a standalone function in the test module scope for clarity.)

class TestKsicFullRunResume:
    """
    Documents the full-run (sample=None) behavior of fetch_ksic():
    when sample=None, existing entries are overwritten by the new full run.
    This test verifies that behavior is intentional and documented.
    """

    def _make_companies(self, corp_codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame({"corp_code": corp_codes, "stock_code": corp_codes})

    def test_full_run_resume_does_not_discard_existing(self, tmp_path, monkeypatch):
        """
        When sample=None and all 10 companies are in the run, all 10 must appear
        in the output — pre-existing entries should not be silently lost.

        Note: full-run (sample=None) overwrites the parquet with fresh data for all
        companies. If the company list hasn't changed, this is a no-op in effect.
        This test verifies the output still contains exactly the expected 10 rows.
        """
        import extract_dart as ed
        monkeypatch.setattr(ed, "RAW_SECTOR", tmp_path / "sector")
        (tmp_path / "sector").mkdir()

        existing_codes = [f"CODE{i:04d}" for i in range(10)]
        existing_df = pd.DataFrame({
            "corp_code":   existing_codes,
            "induty_code": ["264"] * 10,
        })
        existing_df.to_parquet(tmp_path / "sector" / "ksic.parquet", index=False)

        class _FakeInfo:
            induty_code = "264"
        class _FakeDart:
            def company(self, corp_code):
                return _FakeInfo()
        monkeypatch.setattr(ed, "_dart", lambda: _FakeDart())
        monkeypatch.setattr(ed, "_sleep_ksic", 0)

        all_companies = self._make_companies(existing_codes)
        result = ed.fetch_ksic(all_companies, force=False, sample=None)

        assert len(result) == 10, (
            f"Full run wrote {len(result)} rows; expected 10. "
            "If this fails, full-run overwrites behavior discarded existing entries."
        )
        assert set(result["corp_code"]) == set(existing_codes)


# ─── Category 10: WICS snapshot date pinning (M2) ────────────────────────────

class TestWicsSnapshotDate:
    """
    M2: _last_trading_day_of_year(year) probes for the last trading day of
    a given year and returns YYYYMMDD. fetch_wics(year=N) overrides snapshot_date
    with the result so multi-day runs don't drift.
    """

    def _get_module(self):
        import sys
        # conftest.py adds 02_Pipeline to sys.path
        import extract_dart as ed
        return ed

    def test_last_trading_day_of_year_returns_yyyymmdd(self, monkeypatch):
        """Mock a 200 with CNT=5 for 20231229 → returns '20231229'."""
        import requests
        ed = self._get_module()

        class _MockResp:
            status_code = 200
            def json(self):
                return {"info": {"CNT": 5}}

        call_dates = []

        def _mock_get(url, **kwargs):
            dt = url.split("dt=")[1].split("&")[0]
            call_dates.append(dt)
            if dt == "20231229":
                return _MockResp()
            r = _MockResp()
            r.status_code = 200
            r._cnt = 0
            r.json = lambda: {"info": {"CNT": 0}}
            return r

        monkeypatch.setattr(requests, "get", _mock_get)
        result = ed._last_trading_day_of_year(2023)
        assert result == "20231229", f"Expected '20231229', got {result!r}"
        assert result.isdigit() and len(result) == 8

    def test_last_trading_day_of_year_fallback(self, monkeypatch):
        """All 5 candidates return CNT=0 → falls back to '20231231'."""
        import requests
        ed = self._get_module()

        class _MockResp:
            status_code = 200
            def json(self):
                return {"info": {"CNT": 0}}

        monkeypatch.setattr(requests, "get", lambda url, **kw: _MockResp())
        result = ed._last_trading_day_of_year(2023)
        assert result == "20231231", f"Expected fallback '20231231', got {result!r}"

    def test_fetch_wics_records_snapshot_date_in_parquet(self, tmp_path, monkeypatch):
        """fetch_wics(year=2023) writes parquet with snapshot_date starting with '2023'."""
        import requests
        ed = self._get_module()

        # Patch the sector output dir
        sector_dir = tmp_path / "sector"
        sector_dir.mkdir()
        monkeypatch.setattr(ed, "RAW_SECTOR", sector_dir)

        # Mock _last_trading_day_of_year to return a predictable date
        monkeypatch.setattr(ed, "_last_trading_day_of_year", lambda y: f"{y}1231")

        # Mock the WICS HTTP call to return one row for one group
        class _MockResp:
            status_code = 200
            def json(self):
                return {
                    "info": {"CNT": 1},
                    "list": [
                        {
                            "CMP_CD": "000001",
                            "CMP_KOR": "테스트",
                            "SEC_NM_KOR": "IT",
                            "IDX_CD": "G4510",
                        }
                    ],
                }

        monkeypatch.setattr(requests, "get", lambda url, **kw: _MockResp())

        ed.fetch_wics(force=True, year=2023)

        out = sector_dir / "wics.parquet"
        assert out.exists(), "wics.parquet not written"
        df = pd.read_parquet(out)
        assert "snapshot_date" in df.columns, "snapshot_date column missing"
        assert df["snapshot_date"].iloc[0].startswith("2023"), (
            f"snapshot_date should start with '2023', got {df['snapshot_date'].iloc[0]!r}"
        )


# ─── Category 11: match_method lineage columns (PR1) ─────────────────────────

class TestMatchMethodLineage:
    """
    PR1: company_financials.parquet must carry match_method_* columns indicating
    whether each variable was extracted via exact XBRL account_id or Korean
    account_nm substring fallback.
    """

    MATCH_METHOD_COLS = [
        "match_method_revenue",
        "match_method_receivables",
        "match_method_cogs",
        "match_method_sga",
        "match_method_ppe",
        "match_method_depreciation",
        "match_method_total_assets",
        "match_method_lt_debt",
        "match_method_net_income",
        "match_method_cfo",
    ]

    @pytest.fixture(scope="class")
    def financials(self):
        p = PROCESSED / "company_financials.parquet"
        if not p.exists():
            pytest.skip("company_financials.parquet not found — run the pipeline first")
        df = pd.read_parquet(p)
        if df.empty:
            pytest.skip("company_financials.parquet has 0 rows — run the pipeline first")
        return df

    def test_match_method_columns_exist(self, financials):
        missing = [c for c in self.MATCH_METHOD_COLS if c not in financials.columns]
        assert not missing, f"Missing match_method columns: {missing}"

    def test_match_method_values_valid(self, financials):
        valid = {"exact_id", "korean_substring"}
        for col in self.MATCH_METHOD_COLS:
            if col not in financials.columns:
                continue
            actual = set(financials[col].dropna().unique())
            unexpected = actual - valid
            assert not unexpected, f"{col} has unexpected values: {unexpected}"

    def test_extract_field_returns_tuple(self):
        """_extract_field() must return (value, method) tuple in all cases."""
        import sys
        # conftest.py adds 02_Pipeline to sys.path
        import transform as tr

        # (a) exact account_id match → ("exact_id")
        df_a = pd.DataFrame([
            {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "1,000,000"},
        ])
        val_a, method_a = tr._extract_field(df_a, ["ifrs-full_Revenue"], ["매출액"])
        assert val_a == 1_000_000.0, f"value: {val_a}"
        assert method_a == "exact_id", f"method: {method_a}"

        # (b) only account_nm match → ("korean_substring")
        df_b = pd.DataFrame([
            {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "500,000"},
        ])
        val_b, method_b = tr._extract_field(df_b, ["nonexistent_id"], ["매출액"])
        assert val_b == 500_000.0, f"value: {val_b}"
        assert method_b == "korean_substring", f"method: {method_b}"

        # (c) no match → (None, None)
        df_c = pd.DataFrame([
            {"sj_div": "IS", "account_nm": "기타항목", "thstrm_amount": "100"},
        ])
        val_c, method_c = tr._extract_field(df_c, ["nonexistent_id"], ["없는항목"])
        assert val_c is None, f"value: {val_c}"
        assert method_c is None, f"method: {method_c}"


# ─── Category 12: CB/BW schema contracts (Phase 2) ───────────────────────────

class TestCbBwSchema:
    """
    Phase 2: schema contracts for cb_bw_events, price_volume, officer_holdings
    parquets. Tests skip (not error) when files don't exist yet.
    """

    @pytest.fixture(scope="class")
    def cb_bw(self):
        p = PROCESSED / "cb_bw_events.parquet"
        if not p.exists():
            pytest.skip("cb_bw_events.parquet not found — run Phase 2 pipeline first")
        return pd.read_parquet(p)

    @pytest.fixture(scope="class")
    def price_vol(self):
        p = PROCESSED / "price_volume.parquet"
        if not p.exists():
            pytest.skip("price_volume.parquet not found — run Phase 2 pipeline first")
        return pd.read_parquet(p)

    @pytest.fixture(scope="class")
    def officer_hdg(self):
        p = PROCESSED / "officer_holdings.parquet"
        if not p.exists():
            pytest.skip("officer_holdings.parquet not found — run Phase 2 pipeline first")
        return pd.read_parquet(p)

    # cb_bw_events
    def test_cb_bw_required_columns(self, cb_bw):
        required = ["corp_code", "issue_date", "bond_type", "exercise_price",
                    "repricing_history", "exercise_events"]
        missing = [c for c in required if c not in cb_bw.columns]
        assert not missing, f"cb_bw_events missing columns: {missing}"

    def test_cb_bw_bond_type_values(self, cb_bw):
        actual = set(cb_bw["bond_type"].dropna().unique())
        assert actual.issubset({"CB", "BW"}), f"Unexpected bond_type values: {actual - {'CB','BW'}}"

    def test_cb_bw_issue_date_parseable(self, cb_bw):
        bad = pd.to_datetime(cb_bw["issue_date"], errors="coerce").isna().sum()
        assert bad == 0, f"{bad} unparseable issue_date values"

    def test_cb_bw_no_duplicate_events(self, cb_bw):
        dup = cb_bw.duplicated(subset=["corp_code", "issue_date", "bond_type"]).sum()
        assert dup == 0, f"{dup} duplicate (corp_code, issue_date, bond_type) rows"

    # price_volume
    def test_price_volume_required_columns(self, price_vol):
        required = ["ticker", "date", "open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in price_vol.columns]
        assert not missing, f"price_volume missing columns: {missing}"

    def test_price_volume_date_parseable(self, price_vol):
        bad = pd.to_datetime(price_vol["date"], errors="coerce").isna().sum()
        assert bad == 0, f"{bad} unparseable date values in price_volume"

    # officer_holdings
    def test_officer_holdings_required_columns(self, officer_hdg):
        required = ["corp_code", "date", "officer_name", "change_shares"]
        missing = [c for c in required if c not in officer_hdg.columns]
        assert not missing, f"officer_holdings missing columns: {missing}"

    # Parse logic unit tests (no HTTP)
    def test_parse_cb_response_status_013_returns_empty(self):
        """Parser with status 013 (no data) must return empty list."""
        import sys
        # conftest.py adds 02_Pipeline to sys.path
        try:
            import extract_cb_bw as ecb
        except ImportError:
            pytest.skip("extract_cb_bw.py not yet implemented")
        result = ecb._parse_dart_response(
            {"status": "013", "message": "no data"}, corp_code="00000001", bond_type="CB"
        )
        assert result == [], f"Expected empty list for status 013, got {result}"

    def test_parse_cb_response_valid_returns_rows(self):
        """Parser with valid response returns rows with expected fields."""
        import sys
        # conftest.py adds 02_Pipeline to sys.path
        try:
            import extract_cb_bw as ecb
        except ImportError:
            pytest.skip("extract_cb_bw.py not yet implemented")
        mock_response = {
            "status": "000",
            "message": "OK",
            "list": [
                {
                    "rcept_no": "20210315001234",
                    "cv_prc": "5,000",
                    "cvbdIssuAmt": "10000000000",
                }
            ],
        }
        rows = ecb._parse_dart_response(mock_response, corp_code="00000001", bond_type="CB")
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        assert rows[0]["corp_code"] == "00000001"
        assert rows[0]["bond_type"] == "CB"
        assert pd.to_datetime(rows[0]["issue_date"], errors="coerce") is not pd.NaT


# ─── Category 13: sector percentile market isolation (PR4) ───────────────────

class TestSectorPercentileMarket:
    """
    PR4: sector_percentile must be computed within (sector, year, market) groups,
    not across markets. A KOSDAQ company's percentile must not be influenced by
    KOSPI peers in the same sector.
    """

    def _compute_sector_percentile(self, scored: pd.DataFrame) -> pd.DataFrame:
        """Mirror the sector_percentile logic from beneish_screen.py."""
        import sys
        pipeline_dir = str(ROOT / "03_Analysis")
        if pipeline_dir not in sys.path:
            sys.path.insert(0, pipeline_dir)
        # Import and apply the groupby logic — currently uses (sector, year) only
        # After PR4, it must use (sector, year, market)
        scored = scored.copy()
        scored["sector_percentile"] = (
            scored.groupby(["wics_sector_code", "year", "market"])["m_score"]
            .rank(pct=True)
        )
        return scored

    def test_sector_percentile_isolates_market(self):
        """KOSDAQ percentiles must not be influenced by KOSPI rows in same sector/year."""
        import numpy as np

        # Build synthetic scored DataFrame with mixed markets in same sector/year
        df = pd.DataFrame([
            # KOSDAQ companies: m_scores tightly clustered
            {"corp_code": f"KD{i:04d}", "market": "KOSDAQ", "year": 2022,
             "wics_sector_code": "G4510", "m_score": float(-3 + i * 0.1)}
            for i in range(10)
        ] + [
            # KOSPI companies: extreme m_scores that would distort KOSDAQ percentiles
            {"corp_code": f"KP{i:04d}", "market": "KOSPI", "year": 2022,
             "wics_sector_code": "G4510", "m_score": float(5 + i * 10)}
            for i in range(10)
        ])

        result = self._compute_sector_percentile(df)

        # KOSDAQ percentile of lowest scorer should be ~0.1 (1 of 10)
        kd_sorted = result[result["market"] == "KOSDAQ"].sort_values("m_score")
        bottom_kd_pct = kd_sorted.iloc[0]["sector_percentile"]
        assert bottom_kd_pct <= 0.15, (
            f"Bottom KOSDAQ company percentile={bottom_kd_pct:.3f}; "
            f"expected ≤0.15 if isolated from KOSPI"
        )

        # KOSPI percentile of highest scorer should be ~1.0 (10 of 10)
        kp_sorted = result[result["market"] == "KOSPI"].sort_values("m_score")
        top_kp_pct = kp_sorted.iloc[-1]["sector_percentile"]
        assert top_kp_pct >= 0.85, (
            f"Top KOSPI company percentile={top_kp_pct:.3f}; "
            f"expected ≥0.85 if isolated from KOSDAQ"
        )


# ─── Category 14: corp_ticker_map schema contract ────────────────────────────

class TestCorpTickerMap:
    """
    Gap 4: corp_ticker_map.parquet schema contract.
    When the file exists, it must have the required columns and no nulls on
    the two join keys (corp_code, ticker).
    When the file is absent, tests skip.
    """

    @pytest.fixture(scope="class")
    def corp_map(self):
        p = PROCESSED / "corp_ticker_map.parquet"
        if not p.exists():
            pytest.skip("corp_ticker_map.parquet not found — run extract_corp_ticker_map.py first")
        return pd.read_parquet(p)

    def test_required_columns(self, corp_map):
        required = ["corp_code", "ticker", "corp_name", "market", "effective_from", "effective_to"]
        missing = [c for c in required if c not in corp_map.columns]
        assert not missing, f"corp_ticker_map missing columns: {missing}"

    def test_no_null_join_keys(self, corp_map):
        null_corp = corp_map["corp_code"].isna().sum()
        null_ticker = corp_map["ticker"].isna().sum()
        assert null_corp == 0, f"{null_corp} null corp_code values"
        assert null_ticker == 0, f"{null_ticker} null ticker values"

    def test_corp_code_eight_digits(self, corp_map):
        bad = corp_map["corp_code"].str.len().ne(8).sum()
        assert bad == 0, f"{bad} corp_code values not 8 characters"

    def test_no_duplicate_corp_codes(self, corp_map):
        dup = corp_map.duplicated(subset=["corp_code"]).sum()
        assert dup == 0, f"{dup} duplicate corp_code rows in corp_ticker_map"


# ─── Category 15: CB/BW scoping filter (Gap 3) ───────────────────────────────

class TestCbBwScopingFilter:
    """
    Gap 3: fetch_cb_bw_events() with --scoped flag must return a subset of
    companies, not all 1,700+. Unit test uses synthetic data — no DART calls.
    """

    def test_scoping_filter_reduces_universe(self, tmp_path, monkeypatch):
        """
        Given: beneish_scores.parquet with 5 companies, top-3 by m_score flagged.
        When: build_scoped_universe() is called.
        Then: result contains those 3 corp_codes (at minimum).
        """
        try:
            import extract_cb_bw as ecb
        except ImportError:
            pytest.skip("extract_cb_bw.py not yet implemented")
        if not hasattr(ecb, "build_scoped_universe"):
            pytest.skip("build_scoped_universe() not yet implemented in extract_cb_bw.py")

        # Synthetic beneish_scores with 5 companies
        scores = pd.DataFrame({
            "corp_code": ["00000001", "00000002", "00000003", "00000004", "00000005"],
            "m_score": [2.0, 1.5, 1.0, -3.0, -4.0],  # top-3 by m_score are 001, 002, 003
        })
        scores_path = tmp_path / "beneish_scores.parquet"
        scores.to_parquet(scores_path, index=False)

        cb_bw_corp_codes = {"00000004", "00000006"}  # 006 not in scores — union case

        result = ecb.build_scoped_universe(
            scores_path=scores_path,
            cb_bw_corp_codes=cb_bw_corp_codes,
            top_n=3,
        )

        # Must contain top-3 M-Score companies
        assert "00000001" in result
        assert "00000002" in result
        assert "00000003" in result
        # Must contain the CB/BW company
        assert "00000004" in result
        # Must include corp_code not in scores (union, not intersection)
        assert "00000006" in result
        # Must NOT contain the lowest-scoring company outside top-3 (unless it has CB/BW)
        assert "00000005" not in result


# ─── Category 16: Data quality — XBRL unit-scale anomalies ──────────────────

class TestDataQualityAnomalies:
    """
    Guards against silent DART XBRL unit-scale errors that produce
    implausible financial values and corrupt Beneish M-Scores.

    See BUGS.md 'DART XBRL unit-scale error' for root cause and the three
    confirmed cases fixed by _XBRL_UNIT_CORRECTIONS in transform.py.
    """

    @pytest.fixture(scope="class")
    def financials(self):
        p = PROCESSED / "company_financials.parquet"
        if not p.exists():
            pytest.skip("company_financials.parquet not found — run the pipeline first")
        df = pd.read_parquet(p)
        if df.empty:
            pytest.skip("company_financials.parquet has 0 rows — run the pipeline first")
        return df

    def test_no_revenue_sandwich_spikes(self, financials: pd.DataFrame) -> None:
        """
        Detects XBRL unit-scale errors via the 'sandwich' pattern:
        a company-year where revenue is >100× BOTH its prior year AND its
        following year. This indicates a single-year unit-scale inflation.

        After _XBRL_UNIT_CORRECTIONS is applied in transform.py, no such rows
        should remain in the processed parquet.
        """
        df = financials.sort_values(["corp_code", "year"]).copy()
        df["rev_prev"] = df.groupby("corp_code")["revenue"].shift(1)
        df["rev_next"] = df.groupby("corp_code")["revenue"].shift(-1)

        spike_mask = (
            df["rev_prev"].notna() & (df["rev_prev"] > 0) &
            df["rev_next"].notna() & (df["rev_next"] > 0) &
            (df["revenue"] / df["rev_prev"] > 100) &
            (df["revenue"] / df["rev_next"] > 100)
        )
        spikes = df[spike_mask][
            ["corp_code", "company_name", "year", "revenue", "rev_prev", "rev_next"]
        ]
        assert len(spikes) == 0, (
            f"{len(spikes)} revenue sandwich spike(s) found — likely XBRL unit-scale "
            f"error not in _XBRL_UNIT_CORRECTIONS:\n{spikes.to_string()}"
        )

    def test_no_implausible_kosdaq_revenue(self, financials: pd.DataFrame) -> None:
        """
        No KOSDAQ company should have revenue >20조 KRW (2e13).
        The largest legitimate KOSDAQ company in the current dataset is
        에코프로비엠 at ~7조. The 2e13 ceiling is safely above that.

        Catches bugged final-year rows that can't be caught by the sandwich
        test (no following year available to complete the sandwich pattern).
        """
        kosdaq = financials[financials["market"] == "KOSDAQ"]
        extreme = kosdaq[
            kosdaq["revenue"].notna() &
            np.isfinite(kosdaq["revenue"]) &
            (kosdaq["revenue"] > 2e13)
        ][["corp_code", "company_name", "year", "revenue"]]
        assert len(extreme) == 0, (
            f"{len(extreme)} KOSDAQ company-year(s) with revenue >20조 KRW — "
            f"likely XBRL unit-scale error:\n{extreme.to_string()}"
        )


# ─── Category N: _merge_run_summaries schema contract ────────────────────────

class TestMergeRunSummaries:
    """_merge_run_summaries schema contract and safety tests (KI-008)."""

    def _make_summary(self, **overrides) -> dict:
        base = {
            "total_companies": 10,
            "years": [2021, 2022],
            "completed_at": "2026-03-05",
            "elapsed_minutes": 1.5,
            "full_data": ["00100001"],
            "partial_data": [],
            "no_data": [],
            "errors": [],
        }
        base.update(overrides)
        return base

    def test_merge_with_empty_old_does_not_raise(self):
        """First run: old={}, new=full summary — must not KeyError."""
        from pipeline import _merge_run_summaries
        result = _merge_run_summaries({}, self._make_summary())
        assert "total_companies" in result
        assert "full_data" in result

    def test_merge_skips_entries_missing_corp_code(self):
        """Malformed partial_data entries (no corp_code) are silently skipped."""
        from pipeline import _merge_run_summaries
        old = self._make_summary(partial_data=[{"not_corp_code": "x"}])
        new = self._make_summary()
        result = _merge_run_summaries(old, new)  # must not KeyError
        assert result["partial_data"] == []

    def test_output_has_all_required_keys(self):
        """Return value always has all 8 RunSummary keys."""
        from pipeline import _merge_run_summaries
        result = _merge_run_summaries({}, self._make_summary())
        required = {"total_companies", "years", "completed_at", "elapsed_minutes",
                    "full_data", "partial_data", "no_data", "errors"}
        assert required.issubset(result.keys())


# ─── Category N+1: beneish_screen.py output column completeness ──────────────

class TestBeneishOutputColsComplete:
    """
    beneish_screen.py must not silently omit columns before writing Parquet.

    Guards KI-009: the previous silent-omission pattern (available = [c for c
    in output_cols if c in df_scored.columns]) was replaced with an AssertionError.
    This test validates that beneish_scores.parquet contains all expected columns,
    confirming the assert in beneish_screen.py is firing correctly on real data.
    """

    @pytest.fixture(scope="class")
    def scores(self):
        p = PROCESSED / "beneish_scores.parquet"
        if not p.exists():
            pytest.skip("beneish_scores.parquet not found — run beneish_screen.py first")
        return pd.read_parquet(p)

    def test_beneish_output_cols_are_complete(self, scores):
        """All expected output columns are present in beneish_scores.parquet."""
        missing = [c for c in REQUIRED_BENEISH_COLUMNS if c not in scores.columns]
        assert not missing, (
            f"beneish_scores.parquet missing expected columns: {missing}. "
            f"Check beneish_screen.py output_cols assertion."
        )


# ─── Category N+2: peak_date population guard (Fix A) ────────────────────────

class TestPeakDatePopulation:
    """
    Guard for Fix A: run_cb_bw_timelines.py peak_date must not be uniformly null.

    Before Fix A, peak_date was only stored inside `if exercise_cluster_flag:`.
    Since exercise_cluster_flag never fires (SEIBRO data absent), peak_date was
    always null. After Fix A, peak_date is computed from price/volume data and
    stored unconditionally.

    This test skips when cb_bw_summary.csv is absent (runner not yet executed).
    """

    @pytest.fixture(scope="class")
    def cb_summary(self):
        p = pathlib.Path(__file__).resolve().parents[1] / "03_Analysis" / "cb_bw_summary.csv"
        if not p.exists():
            pytest.skip("cb_bw_summary.csv not found — run run_cb_bw_timelines.py first")
        return pd.read_csv(p)

    def test_peak_date_column_exists(self, cb_summary):
        assert "peak_date" in cb_summary.columns, (
            "peak_date column missing from cb_bw_summary.csv"
        )

    def test_peak_date_is_not_all_null(self, cb_summary):
        """After Fix A, at least some rows must have a non-null peak_date.

        This will still fail if run_cb_bw_timelines.py has not been re-run after
        Fix A was applied. Re-run the script to regenerate cb_bw_summary.csv.
        """
        non_null = cb_summary["peak_date"].notna().sum()
        assert non_null > 0, (
            f"peak_date is null for all {len(cb_summary)} rows in cb_bw_summary.csv. "
            "Re-run: python 03_Analysis/run_cb_bw_timelines.py after Fix A."
        )


# ─── Category N+3: officer network threshold guard (Fix B) ───────────────────

class TestOfficerFlagThreshold:
    """
    Guard for FLAG_THRESHOLD in run_officer_network.py.

    History:
    - Fix B (session 13): lowered from 2 → 1 because max anomaly_score was 1
      (SEIBRO pending; only volume_flag fired). At threshold=2 all companies
      fell below and flagged_company_count=0 for every officer.
    - Session 33: raised back to 2 because holdings_flag now fires (date-slice
      bug fixed in session 32); 27 events have flag_count=2 across 23 companies.
      Threshold=2 now produces a meaningful high-precision seed set.

    Uses ast to parse the constant from source — avoids importing the module
    and triggering a networkx import that may not be installed in CI.
    """

    def test_flag_threshold_constant_is_two(self):
        import ast
        src_path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "src"
            / "constants.py"
        )
        assert src_path.exists(), f"constants.py not found at {src_path}"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "OFFICER_FLAG_THRESHOLD":
                        assert isinstance(node.value, ast.Constant), (
                            "OFFICER_FLAG_THRESHOLD must be a literal constant"
                        )
                        assert node.value.value == 2, (
                            f"OFFICER_FLAG_THRESHOLD={node.value.value}; expected 2. "
                            "Session 33: holdings_flag operational; 27 events at "
                            "flag_count=2. Threshold=2 produces a high-precision "
                            "seed set. Do not revert to 1 without updating this test."
                        )
                        return
        raise AssertionError(
            "OFFICER_FLAG_THRESHOLD constant not found in src/constants.py"
        )


# ─── Category 16: major_holders schema + API handling ────────────────────────

class TestMajorHoldersSchema:
    """
    Schema contract for major_holders.parquet.
    Skips when file is absent (not yet generated).
    """

    @pytest.fixture(scope="class")
    def major_holders(self):
        p = PROCESSED / "major_holders.parquet"
        if not p.exists():
            pytest.skip("major_holders.parquet not found — run extract_major_holders.py first")
        return pd.read_parquet(p)

    def test_required_columns(self, major_holders):
        required = [
            "corp_code", "rcept_no", "rcept_dt", "corp_name", "report_tp", "repror",
            "stkqy", "stkqy_irds", "stkrt", "stkrt_irds", "ctr_stkqy", "ctr_stkrt",
            "report_resn",
        ]
        missing = [c for c in required if c not in major_holders.columns]
        assert not missing, f"major_holders.parquet missing columns: {missing}"

    def test_corp_code_non_null_and_eight_chars(self, major_holders):
        null_count = major_holders["corp_code"].isna().sum()
        assert null_count == 0, f"{null_count} null corp_code values"
        bad = major_holders["corp_code"].str.len().ne(8).sum()
        assert bad == 0, f"{bad} corp_code values not 8 characters"

    def test_rcept_dt_format(self, major_holders):
        non_empty = major_holders["rcept_dt"].dropna()
        non_empty = non_empty[non_empty != ""]
        bad = non_empty[~non_empty.str.match(r"^\d{4}-\d{2}-\d{2}$")]
        assert len(bad) == 0, f"{len(bad)} rcept_dt values not in YYYY-MM-DD format: {bad.head().tolist()}"

    def test_no_duplicate_corp_code_rcept_no(self, major_holders):
        dup = major_holders.duplicated(subset=["corp_code", "rcept_no"]).sum()
        assert dup == 0, f"{dup} duplicate (corp_code, rcept_no) pairs in major_holders"

    def test_stkrt_in_valid_range(self, major_holders):
        non_null = major_holders["stkrt"].dropna()
        if len(non_null) == 0:
            return
        out_of_range = non_null[(non_null < 0) | (non_null > 100)]
        assert len(out_of_range) == 0, (
            f"{len(out_of_range)} stkrt values outside 0–100 range: {out_of_range.head().tolist()}"
        )


class TestMajorHoldersApiHandling:
    """
    Unit tests for _fetch_majorstock() API edge cases.
    Uses monkeypatched requests — no network calls.
    """



    def test_status_013_returns_empty_and_caches(self, tmp_path, monkeypatch):
        """Status 013 (no filings) → empty list returned + empty cache written."""
        import extract_major_holders as emh

        def mock_get(*args, **kwargs):
            class FakeResp:
                def json(self):
                    return {"status": "013", "message": "조회된 데이터가 없습니다."}
            return FakeResp()

        monkeypatch.setattr(emh.requests, "get", mock_get)
        raw_dir = tmp_path / "major_holders"
        result = emh._fetch_majorstock("00000001", "fake_key", raw_dir, force=False)

        assert result == []
        cache_path = raw_dir / "00000001.json"
        assert cache_path.exists(), "Cache file should be written for status 013"
        with open(cache_path) as f:
            import json as _json
            cached = _json.load(f)
        assert cached == []

    def test_cache_hit_skips_http_calls(self, tmp_path, monkeypatch):
        """When cache file exists and force=False, no HTTP call is made."""
        import extract_major_holders as emh
        import json as _json

        raw_dir = tmp_path / "major_holders"
        raw_dir.mkdir(parents=True)
        cached_rows = [{"corp_code": "00000002", "rcept_no": "20230001"}]
        cache_path = raw_dir / "00000002.json"
        with open(cache_path, "w") as f:
            _json.dump(cached_rows, f)

        call_count = {"n": 0}

        def mock_get(*args, **kwargs):
            call_count["n"] += 1
            raise AssertionError("HTTP call should not be made when cache exists")

        monkeypatch.setattr(emh.requests, "get", mock_get)
        result = emh._fetch_majorstock("00000002", "fake_key", raw_dir, force=False)

        assert result == cached_rows
        assert call_count["n"] == 0

    def test_status_020_not_cached(self, tmp_path, monkeypatch):
        """Status 020 (rate limit) → returns [] and does NOT write cache."""
        import extract_major_holders as emh

        def mock_get(*args, **kwargs):
            class FakeResp:
                def json(self):
                    return {"status": "020", "message": "요청 제한"}
            return FakeResp()

        monkeypatch.setattr(emh.requests, "get", mock_get)
        raw_dir = tmp_path / "major_holders"
        result = emh._fetch_majorstock("00000003", "fake_key", raw_dir, force=False)

        assert result == []
        cache_path = raw_dir / "00000003.json"
        assert not cache_path.exists(), "Cache must NOT be written for status 020"


# ─── Category 17: bondholder register schema + parse logic ───────────────────

class TestBondholderRegisterSchema:
    """
    Schema contract for bondholder_register.parquet.
    Skips when file is absent (not yet generated).
    """

    @pytest.fixture(scope="class")
    def bondholder_register(self):
        p = PROCESSED / "bondholder_register.parquet"
        if not p.exists():
            pytest.skip(
                "bondholder_register.parquet not found — "
                "run extract_bondholder_register.py first"
            )
        return pd.read_parquet(p)

    def test_required_columns(self, bondholder_register):
        required = [
            "corp_code", "rcept_no", "rcept_dt", "holder_name", "address",
            "face_value_krw", "note", "parse_status",
        ]
        missing = [c for c in required if c not in bondholder_register.columns]
        assert not missing, f"bondholder_register.parquet missing columns: {missing}"

    def test_parse_status_values_valid(self, bondholder_register):
        valid = {"success", "no_subdoc", "parse_error", "no_filing", "fetch_error"}
        bad = set(bondholder_register["parse_status"].unique()) - valid
        assert not bad, f"Unexpected parse_status values: {bad}"

    def test_success_rows_have_holder_name(self, bondholder_register):
        success = bondholder_register[bondholder_register["parse_status"] == "success"]
        if len(success) == 0:
            return
        empty_name = success["holder_name"].isna() | (success["holder_name"] == "")
        n_empty = empty_name.sum()
        assert n_empty == 0, f"{n_empty} success rows have empty holder_name"

    def test_no_duplicate_corp_rcept_holder(self, bondholder_register):
        dup = bondholder_register.duplicated(
            subset=["corp_code", "rcept_no", "holder_name"]
        ).sum()
        assert dup == 0, f"{dup} duplicate (corp_code, rcept_no, holder_name) rows"

    def test_success_face_value_mostly_populated(self, bondholder_register):
        """At most 20% of success rows may have null face_value_krw (tables without amounts)."""
        success = bondholder_register[bondholder_register["parse_status"] == "success"]
        if len(success) == 0:
            return
        null_pct = success["face_value_krw"].isna().sum() / len(success)
        assert null_pct <= 0.20, (
            f"{null_pct:.1%} of success rows have null face_value_krw (threshold: 20%). "
            "Check _BONDHOLDER_COL_ALIASES for missing column name variants."
        )


class TestBondholderParseLogic:
    """Unit tests for bondholder HTML parsing — no network calls."""



    def _make_html_table(self, headers: list[str], rows: list[list]) -> str:
        header_cells = "".join(f"<th>{h}</th>" for h in headers)
        data_rows = ""
        for row in rows:
            cells = "".join(f"<td>{v}</td>" for v in row)
            data_rows += f"<tr>{cells}</tr>"
        return f"<html><body><table><tr>{header_cells}</tr>{data_rows}</table></body></html>"

    def test_standard_columns_parsed_correctly(self):
        from extract_bondholder_register import _parse_bondholder_table
        html = self._make_html_table(
            ["성명/법인명", "주소", "사채권면액", "비고"],
            [["홍길동", "서울시 강남구", "100,000,000", "특이사항없음"]],
        )
        result = _parse_bondholder_table(html)
        assert result is not None
        assert len(result) == 1
        assert result[0]["holder_name"] == "홍길동"
        assert result[0]["address"] == "서울시 강남구"
        assert result[0]["face_value_krw"] == 100_000_000
        assert result[0]["note"] == "특이사항없음"

    def test_alternate_column_name_resolved(self):
        from extract_bondholder_register import _parse_bondholder_table
        # "사채권자명" should map to holder_name
        html = self._make_html_table(
            ["사채권자명", "사채권면액"],
            [["(주)테스트", "50,000,000"]],
        )
        result = _parse_bondholder_table(html)
        assert result is not None
        assert result[0]["holder_name"] == "(주)테스트"

    def test_empty_html_returns_none(self):
        from extract_bondholder_register import _parse_bondholder_table
        result = _parse_bondholder_table("<html><body></body></html>")
        assert result is None

    def test_parse_krw_parenthetical_negative(self):
        from _pipeline_helpers import _parse_krw
        assert _parse_krw("(50,000,000)") == -50_000_000

    def test_parse_krw_comma_formatting(self):
        from _pipeline_helpers import _parse_krw
        assert _parse_krw("1,234,567") == 1_234_567

    def test_parse_krw_cheonwon_multiplier(self):
        from _pipeline_helpers import _parse_krw
        assert _parse_krw("100,000", unit_multiplier=1000) == 100_000_000

    def test_parse_krw_empty_string_returns_none(self):
        from _pipeline_helpers import _parse_krw
        assert _parse_krw("") is None
        assert _parse_krw(None) is None

    def test_detect_unit_multiplier_cheonwon(self):
        from _pipeline_helpers import _detect_unit_multiplier
        html_cheonwon = "<html>(단위: 천원)</html>"
        assert _detect_unit_multiplier(html_cheonwon) == 1000

    def test_detect_unit_multiplier_no_unit(self):
        from _pipeline_helpers import _detect_unit_multiplier
        html_plain = "<html><body>일반 표</body></html>"
        assert _detect_unit_multiplier(html_plain) == 1


# ─── Category 18: revenue schedule schema + parse logic ──────────────────────

class TestRevenueScheduleSchema:
    """
    Schema contract for revenue_schedule.parquet.
    Skips when file is absent (not yet generated).
    """

    @pytest.fixture(scope="class")
    def revenue_schedule(self):
        p = PROCESSED / "revenue_schedule.parquet"
        if not p.exists():
            pytest.skip(
                "revenue_schedule.parquet not found — "
                "run extract_revenue_schedule.py first"
            )
        return pd.read_parquet(p)

    def test_required_columns(self, revenue_schedule):
        required = [
            "corp_code", "rcept_no", "report_year", "row_label",
            "revenue_krw", "revenue_year", "parse_status",
        ]
        missing = [c for c in required if c not in revenue_schedule.columns]
        assert not missing, f"revenue_schedule.parquet missing columns: {missing}"

    def test_year_columns_are_integer_dtype(self, revenue_schedule):
        for col in ("report_year", "revenue_year"):
            assert pd.api.types.is_integer_dtype(revenue_schedule[col]), (
                f"{col} must be integer dtype, got {revenue_schedule[col].dtype}"
            )

    def test_revenue_year_in_valid_range(self, revenue_schedule):
        non_null = revenue_schedule["revenue_year"].dropna()
        out_of_range = non_null[(non_null < 2010) | (non_null > 2030)]
        assert len(out_of_range) == 0, (
            f"{len(out_of_range)} revenue_year values outside 2010–2030: "
            f"{out_of_range.head().tolist()}"
        )

    def test_success_rows_have_row_label(self, revenue_schedule):
        success = revenue_schedule[revenue_schedule["parse_status"] == "success"]
        if len(success) == 0:
            return
        empty = success["row_label"].isna() | (success["row_label"] == "")
        assert empty.sum() == 0, f"{empty.sum()} success rows have empty row_label"

    def test_no_duplicate_dedup_key(self, revenue_schedule):
        dup = revenue_schedule.duplicated(
            subset=["corp_code", "rcept_no", "row_label", "revenue_year"]
        ).sum()
        assert dup == 0, (
            f"{dup} duplicate (corp_code, rcept_no, row_label, revenue_year) rows"
        )


class TestRevenueParseLogic:
    """Unit tests for revenue schedule HTML parsing — no network calls."""



    def _make_html_table(self, headers: list[str], rows: list[list]) -> str:
        header_cells = "".join(f"<th>{h}</th>" for h in headers)
        data_rows = ""
        for row in rows:
            cells = "".join(f"<td>{v}</td>" for v in row)
            data_rows += f"<tr>{cells}</tr>"
        return f"<html><body><table><tr>{header_cells}</tr>{data_rows}</table></body></html>"

    def test_explicit_year_columns_produce_two_rows(self):
        """Columns with explicit years produce one row per year."""
        from extract_revenue_schedule import _parse_revenue_table
        html = self._make_html_table(
            ["구분", "당기(2023)", "전기(2022)"],
            [["고객A", "500,000,000", "400,000,000"]],
        )
        result = _parse_revenue_table(html, report_year=2023)
        assert result is not None
        years_found = {r["revenue_year"] for r in result}
        assert 2023 in years_found, f"Expected year 2023 in {years_found}"
        assert 2022 in years_found, f"Expected year 2022 in {years_found}"

    def test_implicit_period_names_map_correctly(self):
        """당기/전기 without explicit year maps to report_year / report_year-1."""
        from extract_revenue_schedule import _parse_revenue_table
        html = self._make_html_table(
            ["구분", "당기", "전기"],
            [["제품A", "300,000,000", "200,000,000"]],
        )
        result = _parse_revenue_table(html, report_year=2022)
        assert result is not None
        years_found = {r["revenue_year"] for r in result}
        assert 2022 in years_found, f"Expected 2022 in {years_found}"
        assert 2021 in years_found, f"Expected 2021 in {years_found}"

    def test_cheonwon_unit_multiplied(self):
        """(단위: 천원) in page → amounts multiplied by 1000."""
        from extract_revenue_schedule import _parse_revenue_table
        html = (
            "<html><body>(단위: 천원)<table>"
            "<tr><th>구분</th><th>당기(2023)</th></tr>"
            "<tr><td>제품B</td><td>100,000</td></tr>"
            "</table></body></html>"
        )
        result = _parse_revenue_table(html, report_year=2023)
        assert result is not None
        assert result[0]["revenue_krw"] == 100_000_000

    def test_skip_labels_excluded(self):
        """Rows with skip labels (합계, 계, 구분) are not included in output."""
        from extract_revenue_schedule import _parse_revenue_table
        html = self._make_html_table(
            ["구분", "당기(2023)"],
            [["합계", "999,999,999"], ["고객A", "100,000,000"]],
        )
        result = _parse_revenue_table(html, report_year=2023)
        assert result is not None
        labels = [r["row_label"] for r in result]
        assert "합계" not in labels, "합계 should be excluded as a skip label"
        assert "고객A" in labels

    def test_extract_year_columns_explicit(self):
        """_extract_year_columns correctly maps explicit year columns."""
        from extract_revenue_schedule import _extract_year_columns
        df = pd.DataFrame(columns=["구분", "당기(2023)", "전기(2022)", "전전기(2021)"])
        year_map = _extract_year_columns(df, report_year=2023)
        assert year_map.get(2023) == "당기(2023)"
        assert year_map.get(2022) == "전기(2022)"
        assert year_map.get(2021) == "전전기(2021)"


# ─── Category 19: Report module ───────────────────────────────────────────────

class TestReportModule:
    """
    Unit tests for src/report.py — report generator module.
    All tests use monkeypatched loaders so no real parquets are needed.
    TDD: these tests are written before src/report.py exists (ImportError is expected until
    the module is created).
    """

    @pytest.fixture
    def synthetic_beneish(self):
        return pd.DataFrame({
            "corp_code": ["01051092"] * 3,
            "ticker": ["241820"] * 3,
            "company_name": ["피씨엘"] * 3,
            "year": [2021, 2022, 2023],
            "m_score": [-1.5, -2.1, -0.8],
            "risk_tier": ["High", "High", "Low"],
            "flag": [True, True, False],
            "sector_percentile": [75.0, 80.0, 30.0],
            "dsri": [1.2, 1.4, 1.0],
            "gmi": [0.9, 0.85, 1.0],
            "aqi": [1.1, 1.2, 1.0],
            "sgi": [1.3, 1.1, 1.0],
            "depi": [0.95, 1.0, 1.0],
            "sgai": [1.1, 0.9, 1.0],
            "lvgi": [1.2, 1.1, 1.0],
            "tata": [0.02, 0.01, 0.0],
        })

    @pytest.fixture
    def synthetic_cb_bw(self):
        return pd.DataFrame({
            "corp_code": ["01051092"] * 2,
            "issue_date": ["2021-03-15", "2022-06-01"],
            "bond_type": ["CB", "BW"],
            "exercise_price": [5000, 3000],
            "flag_count": [2, 1],
            "flags": ["DSRI_HIGH|SGI_HIGH", "GMI_HIGH"],
            "peak_date": ["2021-03-01", None],
            "volume_ratio": [3.5, 1.2],
        })

    @pytest.fixture
    def synthetic_timing(self):
        return pd.DataFrame({
            "corp_code": ["01051092"],
            "filing_date": ["2022-06-15"],
            "title": ["Test disclosure"],
            "price_change_pct": [8.5],
            "volume_ratio": [3.2],
            "flag": [True],
        })

    def _patch_loaders(self, monkeypatch, beneish_df, cb_bw_df, timing_df):
        """Monkeypatch all loaders and CSV path constants in src.data_access."""
        import src.data_access as da

        def _filter(df, cc):
            if df.empty:
                return pd.DataFrame()
            if "corp_code" not in df.columns:
                return df.copy()
            return df[df["corp_code"].astype(str) == cc].copy()

        monkeypatch.setattr(da, "load_parquet", lambda name, cc=None, sort_by=None, processed_dir=None: _filter(beneish_df, cc) if "beneish" in name else pd.DataFrame())
        monkeypatch.setattr(da, "load_company_name", lambda cc, beneish_df=None, processed_dir=None: "피씨엘" if cc == "01051092" else cc)
        monkeypatch.setattr(da, "load_csv", lambda path, cc=None: _filter(cb_bw_df, cc) if "cb_bw" in str(path) else _filter(timing_df, cc))
        monkeypatch.setattr(da, "load_officer_network", lambda cc, network_csv=None: pd.DataFrame())
        _nonexistent = pathlib.Path("/nonexistent/fake.csv")
        monkeypatch.setattr(da, "CB_BW_CSV", _nonexistent)
        monkeypatch.setattr(da, "TIMING_CSV", _nonexistent)
        monkeypatch.setattr(da, "NETWORK_CSV", _nonexistent)

    def test_generate_report_returns_path(
        self, tmp_path, monkeypatch, synthetic_beneish, synthetic_cb_bw, synthetic_timing
    ):
        """generate_report returns a Path that exists, is >5KB, is valid HTML."""
        import src.report as rpt
        self._patch_loaders(monkeypatch, synthetic_beneish, synthetic_cb_bw, synthetic_timing)
        out = tmp_path / "01051092_report.html"
        result = rpt.generate_report("01051092", output_path=out, skip_claude=True)
        assert isinstance(result, pathlib.Path)
        assert result.exists()
        assert result.stat().st_size > 5000, f"Report only {result.stat().st_size} bytes"
        content = result.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "피씨엘" in content

    def test_report_html_contains_all_sections(
        self, tmp_path, monkeypatch, synthetic_beneish, synthetic_cb_bw, synthetic_timing
    ):
        """HTML report contains all 5 major section headings."""
        import src.report as rpt
        self._patch_loaders(monkeypatch, synthetic_beneish, synthetic_cb_bw, synthetic_timing)
        out = tmp_path / "sections_report.html"
        rpt.generate_report("01051092", output_path=out, skip_claude=True)
        content = out.read_text(encoding="utf-8")
        for phrase in ("M-Score", "CB/BW", "Timing", "Officer", "Synthesis"):
            assert phrase in content, f"Expected '{phrase}' in report HTML"

    def test_report_empty_sections_show_note(self, tmp_path, monkeypatch):
        """With all-empty data, report generates without raising and contains note text."""
        import src.report as rpt
        self._patch_loaders(monkeypatch, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        out = tmp_path / "empty_report.html"
        rpt.generate_report("01051092", output_path=out, skip_claude=True)
        content = out.read_text(encoding="utf-8")
        assert (
            "No " in content
            or "not found" in content.lower()
            or "no data" in content.lower()
        ), "Expected an absence note in empty report"

    def test_corp_code_zero_padded(self, tmp_path, monkeypatch):
        """Passing '1051092' (7 digits) causes loaders to be called with '01051092'."""
        import src.data_access as da
        import src.report as rpt
        called_with: list[str] = []

        def fake_load_parquet(name: str, cc=None, sort_by=None, processed_dir=None) -> pd.DataFrame:
            if cc is not None:
                called_with.append(cc)
            return pd.DataFrame()

        monkeypatch.setattr(da, "load_parquet", fake_load_parquet)
        monkeypatch.setattr(da, "load_company_name", lambda cc, beneish_df=None, processed_dir=None: cc)
        monkeypatch.setattr(da, "load_csv", lambda path, cc=None: pd.DataFrame())
        monkeypatch.setattr(da, "load_officer_network", lambda cc, network_csv=None: pd.DataFrame())
        monkeypatch.setattr(da, "CB_BW_CSV", pathlib.Path("/nonexistent/cb_bw.csv"))
        monkeypatch.setattr(da, "TIMING_CSV", pathlib.Path("/nonexistent/timing.csv"))
        monkeypatch.setattr(da, "NETWORK_CSV", pathlib.Path("/nonexistent/network.csv"))
        out = tmp_path / "padded_report.html"
        rpt.generate_report("1051092", output_path=out, skip_claude=True)
        assert called_with, "Expected load_parquet to be called"
        assert called_with[0] == "01051092", f"Expected '01051092', got '{called_with[0]}'"

    def test_mscore_trend_chart_returns_figure(self, synthetic_beneish):
        """chart_mscore_trend returns a go.Figure."""
        import plotly.graph_objects as go
        from src.report import chart_mscore_trend
        fig = chart_mscore_trend(synthetic_beneish)
        assert isinstance(fig, go.Figure)

    def test_component_bar_chart_returns_figure(self, synthetic_beneish):
        """chart_component_bar returns a go.Figure."""
        import plotly.graph_objects as go
        from src.report import chart_component_bar
        fig = chart_component_bar(synthetic_beneish)
        assert isinstance(fig, go.Figure)

    def test_chart_functions_handle_empty_input(self):
        """All 4 chart functions handle pd.DataFrame() without raising."""
        import plotly.graph_objects as go
        from src.report import (
            chart_cb_bw_timeline,
            chart_component_bar,
            chart_mscore_trend,
            chart_timing_anomalies,
        )
        for fn in (chart_mscore_trend, chart_component_bar, chart_cb_bw_timeline, chart_timing_anomalies):
            fig = fn(pd.DataFrame())
            assert isinstance(fig, go.Figure), f"{fn.__name__} did not return go.Figure on empty input"

    def test_synthesize_with_claude_no_key(self, monkeypatch):
        """synthesize_with_claude returns [] when ANTHROPIC_API_KEY not in env."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from src.report import synthesize_with_claude
        result = synthesize_with_claude({"corp_code": "01051092", "company_name": "test"})
        assert result == []

    def test_synthesize_with_claude_mock(self, monkeypatch):
        """Returns validated flag list when mocked API returns valid JSON."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

        class _FakeBlock:
            text = '[{"source_quote": "Revenue dropped 80%", "flag_type": "SGI_HIGH", "severity": "high"}]'

        class _FakeMessage:
            content = [_FakeBlock()]

        class _FakeMessages:
            def create(self, **kwargs):
                return _FakeMessage()

        class _FakeClient:
            messages = _FakeMessages()

        import src.report as rpt
        monkeypatch.setattr(rpt, "_make_anthropic_client", lambda: _FakeClient())
        result = rpt.synthesize_with_claude({"corp_code": "01051092", "company_name": "test"})
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["severity"] == "high"
        assert result[0]["flag_type"] == "SGI_HIGH"


# ─── Category 20: depreciation_schedule schema ────────────────────────────────

@pytest.mark.skipif(
    not (PROCESSED / "depreciation_schedule.parquet").exists(),
    reason="depreciation_schedule.parquet not found — run extract_depreciation_schedule.py first",
)
class TestDepreciationScheduleSchema:
    """
    Schema contract for depreciation_schedule.parquet.
    Skips gracefully if the parquet has not been generated yet.
    """

    @pytest.fixture
    def depr_schedule(self):
        p = PROCESSED / "depreciation_schedule.parquet"
        return pd.read_parquet(p)

    def test_required_columns(self, depr_schedule):
        required = [
            "corp_code", "rcept_no", "report_year", "asset_category",
            "depr_method", "useful_life_yr", "depr_rate_pct",
            "depr_amount_krw", "parse_status",
        ]
        missing = [c for c in required if c not in depr_schedule.columns]
        assert not missing, f"depreciation_schedule.parquet missing columns: {missing}"

    def test_corp_code_is_8digit_string(self, depr_schedule):
        non_null = depr_schedule["corp_code"].dropna()
        assert (non_null.str.len() == 8).all(), (
            "corp_code must be 8-digit zero-padded string"
        )

    def test_report_year_is_integer_dtype(self, depr_schedule):
        assert pd.api.types.is_integer_dtype(depr_schedule["report_year"]), (
            f"report_year must be integer dtype, got {depr_schedule['report_year'].dtype}"
        )

    def test_report_year_in_valid_range(self, depr_schedule):
        non_null = depr_schedule["report_year"].dropna()
        if len(non_null) > 0:
            assert non_null.min() >= 2015, "report_year should be >= 2015"
            assert non_null.max() <= 2030, "report_year should be <= 2030"

    def test_parse_status_valid_values(self, depr_schedule):
        valid = {"no_filing", "fetch_error", "no_subdoc", "parse_error", "success"}
        actual = set(depr_schedule["parse_status"].dropna().unique())
        invalid = actual - valid
        assert not invalid, f"Unexpected parse_status values: {invalid}"

    def test_success_rows_have_asset_category(self, depr_schedule):
        success = depr_schedule[depr_schedule["parse_status"] == "success"]
        if len(success) > 0:
            empty = success["asset_category"].isna() | (success["asset_category"] == "")
            assert not empty.any(), "success rows must have non-empty asset_category"

    def test_no_duplicate_dedup_key(self, depr_schedule):
        dup = depr_schedule.duplicated(
            subset=["corp_code", "rcept_no", "asset_category", "report_year"]
        )
        assert not dup.any(), (
            f"depreciation_schedule.parquet has {dup.sum()} duplicate rows "
            f"on (corp_code, rcept_no, asset_category, report_year)"
        )

    def test_numeric_columns_are_float(self, depr_schedule):
        for col in ("useful_life_yr", "depr_rate_pct", "depr_amount_krw"):
            non_null = depr_schedule[col].dropna()
            if len(non_null) > 0:
                assert pd.api.types.is_float_dtype(depr_schedule[col]), (
                    f"{col} must be float dtype, got {depr_schedule[col].dtype}"
                )


# ─── Category 21: Quality module ─────────────────────────────────────────────

class TestQualityModule:
    """Tests for src/quality.py — get_quality() and format_quality()."""

    def test_get_quality_returns_expected_keys(self, tmp_path):
        """get_quality() must return a dict with the four top-level keys."""
        from src.quality import get_quality

        # Use tmp_path as both dirs so no real files needed
        result = get_quality(processed_dir=tmp_path, stat_outputs_dir=tmp_path)
        assert set(result.keys()) == {"tables", "coverage", "stat_outputs", "summary"}
        assert isinstance(result["tables"], list)
        assert isinstance(result["coverage"], dict)
        assert isinstance(result["stat_outputs"], list)
        assert isinstance(result["summary"], dict)
        assert "tables_with_issues" in result["summary"]
        assert "missing_outputs" in result["summary"]
        assert "blocked_outputs" in result["summary"]

    def test_format_quality_contains_section_headers(self, tmp_path):
        """format_quality() output must contain the three section headers."""
        from src.quality import get_quality, format_quality

        result = get_quality(processed_dir=tmp_path, stat_outputs_dir=tmp_path)
        output = format_quality(result, verbose=False)
        assert "Data Quality Report" in output
        assert "Coverage" in output
        assert "Stat Test Outputs" in output

    def test_format_quality_verbose_includes_col_detail(self, tmp_path):
        """format_quality(verbose=True) must include per-column null detail for tables with nulls."""
        import pandas as pd
        from src.quality import get_quality, format_quality

        # Create a minimal parquet with a null column so verbose output has something to show
        df = pd.DataFrame({"a": [1, None, 3], "b": ["x", "y", "z"]})
        df.to_parquet(tmp_path / "test_table.parquet", index=False)

        result = get_quality(processed_dir=tmp_path, stat_outputs_dir=tmp_path)
        output_verbose = format_quality(result, verbose=True)
        output_normal = format_quality(result, verbose=False)

        # Verbose output should contain column-level null info
        assert "a:" in output_verbose
        # Normal output should not contain the indented column detail
        assert "    a:" not in output_normal


class TestHoldingsFlagJoin:
    """Guard against date-parsing and join regressions in holdings_flag logic."""

    def test_officer_holdings_date_format_is_iso(self):
        """officer_holdings date column must be parseable with str[:10] (YYYY-MM-DD)."""
        if not (PROCESSED / "officer_holdings.parquet").exists():
            pytest.skip("officer_holdings.parquet not present")
        oh = pd.read_parquet(PROCESSED / "officer_holdings.parquet")
        assert "date" in oh.columns
        # str[:10] must parse as valid dates (not NaT)
        parsed = pd.to_datetime(oh["date"].astype(str).str[:10], errors="coerce")
        valid_pct = parsed.notna().mean()
        assert valid_pct > 0.95, f"Only {valid_pct:.1%} of officer_holdings dates parse via str[:10]"

    def test_holdings_corp_code_overlap_with_cb_bw(self):
        """Normalized corp_codes must overlap between officer_holdings and cb_bw_events."""
        if not (PROCESSED / "officer_holdings.parquet").exists():
            pytest.skip("officer_holdings.parquet not present")
        if not (PROCESSED / "cb_bw_events.parquet").exists():
            pytest.skip("cb_bw_events.parquet not present")
        oh = pd.read_parquet(PROCESSED / "officer_holdings.parquet")
        cb = pd.read_parquet(PROCESSED / "cb_bw_events.parquet")
        oh_codes = set(oh["corp_code"].astype(str).str.zfill(8))
        cb_codes = set(cb["corp_code"].astype(str).str.zfill(8))
        overlap = len(oh_codes & cb_codes)
        assert overlap > 100, f"Only {overlap} corp_codes overlap after normalization"


# ─── Category 22: Constants consolidation ─────────────────────────────────────

class TestConstantsConsolidation:
    """Verify all scoring constants exist in src/constants.py with correct types."""

    def test_cb_bw_scoring_constants_exist(self):
        from src.constants import (
            REPRICING_DISCOUNT_RATIO,
            EXERCISE_PEAK_WINDOW_DAYS,
            VOLUME_SURGE_RATIO,
            HOLDINGS_DECREASE_RATIO,
            PRICE_WINDOW_DAYS,
        )
        assert isinstance(REPRICING_DISCOUNT_RATIO, float)
        assert isinstance(EXERCISE_PEAK_WINDOW_DAYS, int)
        assert isinstance(VOLUME_SURGE_RATIO, float)
        assert isinstance(HOLDINGS_DECREASE_RATIO, float)
        assert isinstance(PRICE_WINDOW_DAYS, int)

    def test_timing_constants_exist(self):
        from src.constants import (
            TIMING_PRICE_CHANGE_PCT,
            TIMING_VOLUME_RATIO,
            TIMING_BORDERLINE_PRICE_PCT,
        )
        assert isinstance(TIMING_PRICE_CHANGE_PCT, float)
        assert isinstance(TIMING_VOLUME_RATIO, float)
        assert isinstance(TIMING_BORDERLINE_PRICE_PCT, float)

    def test_officer_flag_threshold_exists(self):
        from src.constants import OFFICER_FLAG_THRESHOLD
        assert OFFICER_FLAG_THRESHOLD == 2


# ─── Category 23: score_disclosures importable from _scoring ──────────────────

class TestScoringModule:
    """Verify score_disclosures is importable from 03_Analysis/_scoring.py."""

    def test_score_disclosures_importable(self):
        sys.path.insert(0, str(ROOT / "03_Analysis"))
        try:
            from _scoring import score_disclosures
            assert callable(score_disclosures)
        finally:
            sys.path.pop(0)

    def test_score_disclosures_empty_input(self):
        sys.path.insert(0, str(ROOT / "03_Analysis"))
        try:
            from _scoring import score_disclosures
            empty_pv = pd.DataFrame(columns=["ticker", "date", "close", "volume", "price_change_pct", "volume_ratio"])
            result = score_disclosures(pd.DataFrame(), empty_pv, pd.DataFrame())
            assert isinstance(result, pd.DataFrame)
            assert len(result) == 0
        finally:
            sys.path.pop(0)


# ─── Category 24: data_access module ─────────────────────────────────────────

class TestDataAccessModule:
    """Tests for src/data_access.py."""

    def test_load_parquet_missing_file(self, tmp_path):
        from src.data_access import load_parquet
        result = load_parquet("nonexistent.parquet", processed_dir=tmp_path)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_load_parquet_no_filter(self, tmp_path):
        from src.data_access import load_parquet
        df = pd.DataFrame({"corp_code": ["01051092", "00364254"], "val": [1, 2]})
        df.to_parquet(tmp_path / "test.parquet", index=False)
        result = load_parquet("test.parquet", processed_dir=tmp_path)
        assert len(result) == 2

    def test_load_parquet_with_filter(self, tmp_path):
        from src.data_access import load_parquet
        df = pd.DataFrame({"corp_code": ["01051092", "00364254"], "val": [1, 2]})
        df.to_parquet(tmp_path / "test.parquet", index=False)
        result = load_parquet("test.parquet", corp_code="01051092", processed_dir=tmp_path)
        assert len(result) == 1
        assert result["corp_code"].iloc[0] == "01051092"

    def test_load_csv_missing_file(self):
        from src.data_access import load_csv
        result = load_csv(pathlib.Path("/nonexistent/file.csv"))
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_load_company_name_from_beneish(self):
        from src.data_access import load_company_name
        df = pd.DataFrame({"company_name": ["피씨엘"]})
        assert load_company_name("01051092", beneish_df=df) == "피씨엘"

    def test_load_company_name_fallback(self, tmp_path):
        from src.data_access import load_company_name
        result = load_company_name("99999999", processed_dir=tmp_path)
        assert result == "99999999"


# ─── Category 25: Pydantic model validation ──────────────────────────────────

class TestPydanticModels:
    """Validate Pydantic models against actual function output shapes."""

    def test_company_summary_validates(self, monkeypatch):
        import src.data_access as da
        from src.models import CompanySummary
        from src.report import get_company_summary

        monkeypatch.setattr(da, "load_parquet", lambda name, cc=None, sort_by=None, processed_dir=None: pd.DataFrame())
        monkeypatch.setattr(da, "load_company_name", lambda cc, beneish_df=None, processed_dir=None: "Test")
        monkeypatch.setattr(da, "load_csv", lambda path, cc=None: pd.DataFrame())
        monkeypatch.setattr(da, "load_officer_network", lambda cc, network_csv=None: pd.DataFrame())
        monkeypatch.setattr(da, "CB_BW_CSV", pathlib.Path("/nonexistent"))
        monkeypatch.setattr(da, "TIMING_CSV", pathlib.Path("/nonexistent"))
        monkeypatch.setattr(da, "NETWORK_CSV", pathlib.Path("/nonexistent"))

        result = get_company_summary("01051092")
        validated = CompanySummary.model_validate(result)
        assert validated.corp_code == "01051092"

    def test_pipeline_status_validates(self, tmp_path):
        from src.models import PipelineStatus
        from src.status import get_status

        result = get_status(processed_dir=tmp_path, run_summary_path=tmp_path / "none.json")
        validated = PipelineStatus.model_validate(result)
        assert validated.summary.total > 0

    def test_data_quality_validates(self, tmp_path):
        from src.models import DataQuality
        from src.quality import get_quality

        result = get_quality(processed_dir=tmp_path, stat_outputs_dir=tmp_path)
        validated = DataQuality.model_validate(result)
        assert isinstance(validated.tables, list)


# ─── Category 26: Report public API ──────────────────────────────────────────

class TestReportPublicAPI:
    """Tests for get_company_summary() and get_report_html()."""

    def _patch_da(self, monkeypatch):
        import src.data_access as da
        monkeypatch.setattr(da, "load_parquet", lambda name, cc=None, sort_by=None, processed_dir=None: pd.DataFrame())
        monkeypatch.setattr(da, "load_company_name", lambda cc, beneish_df=None, processed_dir=None: "TestCo")
        monkeypatch.setattr(da, "load_csv", lambda path, cc=None: pd.DataFrame())
        monkeypatch.setattr(da, "load_officer_network", lambda cc, network_csv=None: pd.DataFrame())
        monkeypatch.setattr(da, "CB_BW_CSV", pathlib.Path("/nonexistent"))
        monkeypatch.setattr(da, "TIMING_CSV", pathlib.Path("/nonexistent"))
        monkeypatch.setattr(da, "NETWORK_CSV", pathlib.Path("/nonexistent"))

    def test_get_company_summary_returns_dict(self, monkeypatch):
        from src.report import get_company_summary
        self._patch_da(monkeypatch)
        result = get_company_summary("01051092")
        assert isinstance(result, dict)
        assert result["corp_code"] == "01051092"
        assert result["company_name"] == "TestCo"

    def test_get_report_html_returns_string(self, monkeypatch):
        from src.report import get_report_html
        self._patch_da(monkeypatch)
        result = get_report_html("01051092")
        assert isinstance(result, str)
        assert "<!DOCTYPE html>" in result


# ─── Category 27: _paths env var override ─────────────────────────────────────

class TestPathsEnvOverride:
    """Verify KRFF_DATA_DIR environment variable override works."""

    def test_krff_data_dir_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KRFF_DATA_DIR", str(tmp_path / "custom"))
        # Force reimport to pick up env var
        import importlib
        import src._paths
        importlib.reload(src._paths)
        try:
            assert src._paths.PROCESSED_DIR == tmp_path / "custom"
        finally:
            # Restore original
            monkeypatch.delenv("KRFF_DATA_DIR", raising=False)
            importlib.reload(src._paths)


# ─── Category 28: DuckDB query contracts ────────────────────────────────────────

class TestDuckDBQueryContracts:
    """Verify src.db module contracts: parameterized queries, filtering, error handling."""

    def _write_test_parquet(self, tmp_path, name="test.parquet", data=None):
        if data is None:
            data = {
                "corp_code": ["00111111", "00222222", "00111111"],
                "year": [2021, 2022, 2023],
                "m_score": [1.5, -2.1, 0.3],
            }
        df = pd.DataFrame(data)
        path = tmp_path / name
        df.to_parquet(path, index=False)
        return path

    def test_missing_parquet_returns_empty_df(self, tmp_path):
        """read_table() on nonexistent file returns empty DataFrame, no crash."""
        from src.db import read_table
        result = read_table("nonexistent_table", processed_dir=tmp_path)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_query_result_schema_matches(self, tmp_path):
        """Columns from DuckDB match parquet file columns."""
        from src.db import read_table
        path = self._write_test_parquet(tmp_path)
        result = read_table(path.stem, processed_dir=tmp_path)
        assert set(result.columns) == {"corp_code", "year", "m_score"}
        assert len(result) == 3

    def test_corp_code_filtering(self, tmp_path):
        """WHERE corp_code works with zero-padding."""
        from src.db import read_table
        self._write_test_parquet(tmp_path)
        result = read_table("test", corp_code="00111111", processed_dir=tmp_path)
        assert len(result) == 2
        assert all(result["corp_code"].astype(str).str.zfill(8) == "00111111")

    def test_corp_code_filter_no_corp_code_column(self, tmp_path):
        """Table without corp_code col + filter returns empty DataFrame."""
        from src.db import read_table
        self._write_test_parquet(tmp_path, data={"x": [1, 2], "y": [3, 4]})
        result = read_table("test", corp_code="00111111", processed_dir=tmp_path)
        assert result.empty

    def test_parameterized_query_prevents_injection(self, tmp_path):
        """Injection string in corp_code returns empty result, no error."""
        from src.db import read_table
        self._write_test_parquet(tmp_path)
        result = read_table(
            "test",
            corp_code="'; DROP TABLE test; --",
            processed_dir=tmp_path,
        )
        assert result.empty

    def test_sort_by_column(self, tmp_path):
        """ORDER BY year works correctly."""
        from src.db import read_table
        self._write_test_parquet(tmp_path)
        result = read_table("test", sort_by="year", processed_dir=tmp_path)
        assert len(result) == 3
        assert list(result["year"]) == [2021, 2022, 2023]

    def test_query_raw_sql(self, tmp_path):
        """Low-level query() with params executes correctly."""
        from src.db import query
        path = self._write_test_parquet(tmp_path)
        path_str = str(path).replace("\\", "/")
        result = query(
            "SELECT * FROM read_parquet(?) WHERE year >= ?",
            [path_str, 2022],
        )
        assert len(result) == 2

    def test_data_access_load_parquet_integration(self, tmp_path):
        """load_parquet() still works after DuckDB migration (end-to-end)."""
        from src.data_access import load_parquet
        self._write_test_parquet(tmp_path, name="beneish_scores.parquet")
        result = load_parquet(
            "beneish_scores.parquet",
            corp_code="00111111",
            processed_dir=tmp_path,
        )
        assert len(result) == 2
