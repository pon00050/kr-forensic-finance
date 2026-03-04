"""
test_acceptance_criteria.py — Phase 1 acceptance criteria (AC1–AC7).

Checks pipeline outputs against the acceptance thresholds defined in
00_Reference/17_MVP_Requirements.md. Each AC maps to one pytest test
function so failures are reported individually.

Also writes tests/top50_spot_check.csv for manual spot-checking.

Run after the full pipeline and beneish_screen.py:
    python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023
    python 03_Analysis/beneish_screen.py
    pytest tests/test_acceptance_criteria.py -v
"""

import hashlib
import os
import pathlib
import sys

import pandas as pd
import pytest

# Windows Unicode fix — Korean company names in output
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)

ROOT      = pathlib.Path(__file__).resolve().parents[1]   # project root
PROCESSED = ROOT / "01_Data" / "processed"
TESTS     = ROOT / "tests"


# ─── fixtures ────────────────────────────────────────────────────────────────

def _load(name: str) -> pd.DataFrame:
    """Load a processed parquet from R2 (DuckDB) if configured, else local disk."""
    endpoint = os.getenv("R2_ENDPOINT_URL", "")
    key      = os.getenv("R2_ACCESS_KEY_ID", "")
    secret   = os.getenv("R2_SECRET_ACCESS_KEY", "")
    bucket   = os.getenv("R2_BUCKET", "kr-forensic-finance")

    if all([endpoint, key, secret]):
        try:
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
                f"SELECT * FROM 's3://{bucket}/processed/{name}'"
            ).df()
        except Exception as e:
            print(f"  [WARNING] R2 read failed ({e}); falling back to local")

    p = PROCESSED / name
    if not p.exists():
        pytest.skip(f"{p} not found — run the pipeline first or configure R2 credentials")
    return pd.read_parquet(p)


@pytest.fixture(scope="module")
def scores():
    return _load("beneish_scores.parquet")


@pytest.fixture(scope="module")
def financials():
    return _load("company_financials.parquet")


# ─── AC1: Coverage ───────────────────────────────────────────────────────────

def test_ac1_coverage(scores):
    """≥80% of companies have ≥3 of 5 years with a non-null m_score."""
    per_company    = scores.dropna(subset=["m_score"]).groupby("corp_code")["year"].nunique()
    total          = scores["corp_code"].nunique()
    qualifying     = (per_company >= 3).sum()
    pct            = qualifying / total * 100 if total > 0 else 0
    assert pct >= 80, (
        f"AC1 FAIL: only {qualifying}/{total} companies ({pct:.1f}%) have ≥3 scoreable years"
    )


# ─── AC2: Sector enrichment ──────────────────────────────────────────────────

def test_ac2_sector_enrichment(scores):
    """≥80% of beneish_scores rows have a non-null wics_sector_code.

    Threshold is 80%, not 95%, because WICS does not classify ~15% of KOSDAQ
    companies. This is a source data ceiling, not a pipeline defect.
    See KNOWN_ISSUES.md KI-001 for full rationale.
    """
    assert "wics_sector_code" in scores.columns, (
        "AC2 FAIL: wics_sector_code column missing from beneish_scores"
    )
    non_null = scores["wics_sector_code"].notna().sum()
    total    = len(scores)
    pct      = non_null / total * 100 if total > 0 else 0
    assert pct >= 80, (
        f"AC2 FAIL: only {non_null}/{total} rows ({pct:.1f}%) have wics_sector_code"
    )


# ─── AC3: Score computability ────────────────────────────────────────────────

def test_ac3_score_computability(scores):
    """≥70% of company-year pairs have a non-null m_score."""
    non_null = scores["m_score"].notna().sum()
    total    = len(scores)
    pct      = non_null / total * 100 if total > 0 else 0
    assert pct >= 70, (
        f"AC3 FAIL: only {non_null}/{total} company-years ({pct:.1f}%) have m_score"
    )


# ─── AC4: Financial exclusion ────────────────────────────────────────────────

def test_ac4_financial_exclusion(financials):
    """Zero rows with KSIC 640–669 or 68200 in company_financials.

    Checked against company_financials (which carries ksic_code) rather than
    beneish_scores (which does not). See KNOWN_ISSUES.md KI-003.
    """
    assert "ksic_code" in financials.columns, (
        "AC4 SKIP: ksic_code column not found in company_financials"
    )

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
        f"AC4 FAIL: {flagged} financial-sector rows (KSIC 640–669 / 68200) "
        f"found in company_financials"
    )


# ─── AC5: Market purity ──────────────────────────────────────────────────────

def test_ac5_market_purity(scores):
    """Zero rows from the *other* market in beneish_scores output.

    Reads PIPELINE_MARKET env var (default: KOSDAQ) to determine which market
    was run, then asserts that no rows from the opposite market are present.
    This allows the same test to validate both KOSDAQ-only and KOSPI-only runs.
    """
    pipeline_market = os.getenv("PIPELINE_MARKET", "KOSDAQ").upper()
    assert "market" in scores.columns, "AC5 SKIP: market column not found"
    if pipeline_market == "KOSDAQ":
        other = scores[scores["market"].str.upper().str.contains("KOSPI", na=False)]
        assert len(other) == 0, (
            f"AC5 FAIL: {len(other)} KOSPI rows found in beneish_scores (PIPELINE_MARKET=KOSDAQ)"
        )
    else:
        other = scores[scores["market"].str.upper().str.contains("KOSDAQ", na=False)]
        assert len(other) == 0, (
            f"AC5 FAIL: {len(other)} KOSDAQ rows found in beneish_scores (PIPELINE_MARKET={pipeline_market})"
        )


# ─── AC6: Expense method ─────────────────────────────────────────────────────

def test_ac6_expense_method(scores):
    """expense_method is 100% populated; nature-method rows have gmi=1.0 and sgai=1.0."""
    assert "expense_method" in scores.columns, (
        "AC6 FAIL: expense_method column missing"
    )
    null_count = scores["expense_method"].isna().sum()
    assert null_count == 0, (
        f"AC6 FAIL: {null_count} rows have null expense_method"
    )
    nature = scores[scores["expense_method"].str.lower().str.contains("nature", na=False)]
    if len(nature) > 0:
        bad_gmi  = (nature["gmi"]  != 1.0).sum() if "gmi"  in nature.columns else 0
        bad_sgai = (nature["sgai"] != 1.0).sum() if "sgai" in nature.columns else 0
        assert bad_gmi == 0, (
            f"AC6 FAIL: {bad_gmi} nature-method rows have gmi ≠ 1.0"
        )
        assert bad_sgai == 0, (
            f"AC6 FAIL: {bad_sgai} nature-method rows have sgai ≠ 1.0"
        )


# ─── AC7: Reproducibility ────────────────────────────────────────────────────

def test_ac7_reproducibility():
    """beneish_scores.parquet exists on disk (proxy for reproducibility).

    Full reproducibility requires a complete re-run from scratch. This test
    verifies the file exists and records its md5 in the failure message for
    manual comparison across runs.
    """
    p = PROCESSED / "beneish_scores.parquet"
    assert p.exists(), "AC7 FAIL: beneish_scores.parquet not found"
    md5 = hashlib.md5(p.read_bytes()).hexdigest()  # diagnostic only — log for cross-run comparison
    scores = pd.read_parquet(p)
    assert len(scores) >= 1_000, f"AC7 FAIL: beneish_scores has only {len(scores)} rows (md5={md5})"
    assert scores["m_score"].notna().any(), "AC7 FAIL: No non-null m_score values found"
    assert scores["flag"].notna().all(), "AC7 FAIL: flag column has nulls"


# ─── Top-50 spot check (session-scoped side effect) ──────────────────────────

@pytest.fixture(scope="module", autouse=True)
def write_top50_spot_check(scores):
    """Write top50_spot_check.csv to tests/ after all AC tests run."""
    try:
        ratio_cols = [c for c in ["dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata"]
                      if c in scores.columns]
        link_col   = next((c for c in scores.columns
                           if "dart" in c.lower() and "link" in c.lower()), None)
        id_cols    = ["corp_code", "company_name", "ticker", "year", "m_score", "flag"]
        avail_id   = [c for c in id_cols if c in scores.columns]
        extra      = ([link_col] if link_col else [])

        top50 = (
            scores.dropna(subset=["m_score"])
            .sort_values("m_score")
            .head(50)[avail_id + ratio_cols + extra]
        )
        out = TESTS / "top50_spot_check.csv"
        top50.to_csv(out, index=False, encoding="utf-8-sig")
    except Exception:
        pass  # spot check is best-effort; never fail a test run over it
    yield
