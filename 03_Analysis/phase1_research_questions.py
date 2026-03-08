"""
phase1_research_questions.py — Phase 1 analytical deep-dive.

Answers the four open research questions from:
  00_Reference/7_Projects/27_Phase1_Analytical_Research_Questions.md

Input:
  01_Data/processed/beneish_scores.parquet

Outputs (written to 03_Analysis/):
  phase1_q1_critical_drivers.csv    — Critical company-years by dominant component cluster
  phase1_q2_sector_breakdown.csv    — Sector flag rates + component medians
  phase1_q3_repeat_flaggers.csv     — Companies with 3–4 consecutive Critical flags
  phase1_q4_fs_type_distribution.csv — Flag rates by fs_type × switched group

Run:
  python 03_Analysis/phase1_research_questions.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows (Korean sector names)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
SCORES_PATH = ROOT / "01_Data" / "processed" / "beneish_scores.parquet"
OUT_DIR = Path(__file__).parent

COMPONENTS = ["dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata"]


def _load() -> pd.DataFrame:
    if not SCORES_PATH.exists():
        sys.exit(
            f"ERROR: {SCORES_PATH} not found.\n"
            "Run: python 03_Analysis/beneish_screen.py"
        )
    df = pd.read_parquet(SCORES_PATH)
    # Filter to rows with a valid finite m_score
    df = df[np.isfinite(df["m_score"].fillna(np.nan))].copy()
    return df


# ---------------------------------------------------------------------------
# Q1 — Is SGI the primary driver of Critical flags?
# ---------------------------------------------------------------------------

def q1_critical_drivers(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each Critical company-year, flag which components are above the
    population median. Cluster into:
      - 'SGI_only'            — only SGI elevated
      - 'SGI_DSRI_TATA'       — all three elevated (classic revenue inflation)
      - 'SGI_DSRI'            — SGI + DSRI elevated, TATA not
      - 'SGI_TATA'            — SGI + TATA elevated, DSRI not
      - 'multi_no_SGI'        — multiple elevated, SGI not among them
      - 'other'               — everything else
    """
    pop_medians = df[COMPONENTS].median()

    critical = df[df["risk_tier"] == "Critical"].copy()

    # Boolean: component is above population median
    for comp in COMPONENTS:
        critical[f"{comp}_hi"] = critical[comp] > pop_medians[comp]

    def _cluster(row) -> str:
        sgi = row["sgi_hi"]
        dsri = row["dsri_hi"]
        tata = row["tata_hi"]
        n_hi = sum(row[f"{c}_hi"] for c in COMPONENTS)

        if sgi and dsri and tata:
            return "SGI_DSRI_TATA"
        if sgi and dsri:
            return "SGI_DSRI"
        if sgi and tata:
            return "SGI_TATA"
        if sgi and n_hi == 1:
            return "SGI_only"
        if not sgi and n_hi >= 2:
            return "multi_no_SGI"
        return "other"

    critical["driver_cluster"] = critical.apply(_cluster, axis=1)

    out_cols = [
        "corp_code", "ticker", "company_name", "year", "m_score",
        "high_fp_risk", "wics_sector",
        "dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi", "tata",
        "dsri_hi", "sgi_hi", "tata_hi", "driver_cluster",
    ]
    out = critical[[c for c in out_cols if c in critical.columns]].copy()
    out = out.sort_values("m_score", ascending=False).reset_index(drop=True)

    summary = critical["driver_cluster"].value_counts()
    fp_in_sgi_only = critical[
        (critical["driver_cluster"] == "SGI_only") & critical["high_fp_risk"]
    ].shape[0]

    _print_section("Q1 — Critical flag drivers")
    print(f"  Total Critical company-years: {len(critical)}")
    print(f"  Component medians (full population):")
    for comp in COMPONENTS:
        print(f"    {comp:6s}: {pop_medians[comp]:.4f}")
    print()
    print("  Driver cluster breakdown:")
    for cluster, count in summary.items():
        pct = count / len(critical) * 100
        print(f"    {cluster:<18s}: {count:>4d}  ({pct:.1f}%)")
    print()
    print(f"  SGI_only overlapping high_fp_risk (biotech/pharma): {fp_in_sgi_only}")

    return out


# ---------------------------------------------------------------------------
# Q2 — Which sectors are genuinely vs. structurally elevated?
# ---------------------------------------------------------------------------

def q2_sector_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per WICS sector × year: flag rate, median M-Score, median per component,
    and dominant_driver (component with highest median relative to its
    population median).
    """
    pop_medians = df[COMPONENTS].median()

    # Work only on rows with a sector
    has_sector = df[df["wics_sector"].notna()].copy()

    grp = has_sector.groupby(["wics_sector", "wics_sector_code"])

    agg = grp.agg(
        n=("m_score", "count"),
        flag_rate=("flag", "mean"),
        critical_rate=("risk_tier", lambda s: (s == "Critical").mean()),
        high_fp_risk_rate=("high_fp_risk", "mean"),
        median_m_score=("m_score", "median"),
        **{f"med_{c}": (c, "median") for c in COMPONENTS},
    ).reset_index()

    # Dominant driver: component whose sector median is furthest above its population median
    def _dominant(row) -> str:
        excess = {c: row[f"med_{c}"] - pop_medians[c] for c in COMPONENTS}
        return max(excess, key=excess.get)

    agg["dominant_driver"] = agg.apply(_dominant, axis=1)
    agg = agg.sort_values("critical_rate", ascending=False).reset_index(drop=True)

    _print_section("Q2 — Sector flag rates")
    print(f"  Sectors with data: {len(agg)}")
    print()
    top10 = agg.nlargest(10, "critical_rate")
    print(f"  Top 10 by Critical rate:")
    print(f"  {'Sector':<35} {'n':>5} {'flag%':>6} {'crit%':>6} {'fp_risk%':>8} {'dominant'}")
    print(f"  {'-'*75}")
    for _, row in top10.iterrows():
        print(
            f"  {str(row['wics_sector']):<35} {row['n']:>5} "
            f"{row['flag_rate']*100:>5.1f}% {row['critical_rate']*100:>5.1f}% "
            f"{row['high_fp_risk_rate']*100:>7.1f}% {row['dominant_driver']}"
        )

    return agg


# ---------------------------------------------------------------------------
# Q3 — Repeat Critical flaggers
# ---------------------------------------------------------------------------

def q3_repeat_flaggers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Count Critical flags per corp_code across years.
    Flag companies with 3 or 4 Critical years as highest-priority targets.
    """
    critical = df[df["risk_tier"] == "Critical"].copy()

    per_company = (
        critical.groupby("corp_code")
        .agg(
            company_name=("company_name", "first"),
            ticker=("ticker", "first"),
            wics_sector=("wics_sector", "first"),
            high_fp_risk=("high_fp_risk", "first"),
            critical_years=("year", "nunique"),
            years_list=("year", lambda s: sorted(s.tolist())),
            avg_m_score=("m_score", "mean"),
            fs_type_switched=("fs_type_switched", "any"),
        )
        .reset_index()
    )

    per_company["years_str"] = per_company["years_list"].apply(
        lambda y: ", ".join(str(x) for x in y)
    )
    per_company = per_company.sort_values(
        ["critical_years", "avg_m_score"], ascending=[False, False]
    ).reset_index(drop=True)

    high_priority = per_company[per_company["critical_years"] >= 3]

    _print_section("Q3 — Repeat Critical flaggers")
    dist = per_company["critical_years"].value_counts().sort_index()
    print("  Critical flag counts per company:")
    for n_years, count in dist.items():
        print(f"    {n_years} Critical year(s): {count:>4d} companies")
    print()
    print(f"  High-priority (≥3 Critical years): {len(high_priority)} companies")
    if len(high_priority) > 0:
        switched = high_priority["fs_type_switched"].sum()
        print(f"    Of which fs_type_switched=True: {switched} ({switched/len(high_priority)*100:.1f}%)")
        print()
        print(f"  Top 20 repeat flaggers:")
        print(f"  {'Company':<30} {'Ticker':>6} {'Crit yrs':>8} {'Avg M':>7} {'Switched':>8} {'Sector'}")
        print(f"  {'-'*85}")
        for _, row in high_priority.head(20).iterrows():
            print(
                f"  {str(row['company_name']):<30} {str(row['ticker']):>6} "
                f"{row['critical_years']:>8} {row['avg_m_score']:>7.3f} "
                f"{'Y' if row['fs_type_switched'] else 'N':>8} "
                f"{str(row.get('wics_sector', 'N/A'))}"
            )

    return per_company.drop(columns=["years_list"])


# ---------------------------------------------------------------------------
# Q4 — CFS vs OFS flag distribution
# ---------------------------------------------------------------------------

def q4_fs_type_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compare flag rates and risk tier distribution across:
      - Pure CFS (fs_type=CFS, never switched)
      - Pure OFS (fs_type=OFS, never switched)
      - Switched (fs_type_switched=True)

    Group is determined per company (not per company-year).
    """
    # Classify each company into a fs_group
    company_fs = (
        df.groupby("corp_code")
        .agg(
            has_cfs=("fs_type", lambda s: (s == "CFS").any()),
            has_ofs=("fs_type", lambda s: (s == "OFS").any()),
            ever_switched=("fs_type_switched", "any"),
        )
        .reset_index()
    )

    def _group(row) -> str:
        if row["ever_switched"]:
            return "switched"
        if row["has_cfs"] and not row["has_ofs"]:
            return "pure_CFS"
        if row["has_ofs"] and not row["has_cfs"]:
            return "pure_OFS"
        return "mixed"

    company_fs["fs_group"] = company_fs.apply(_group, axis=1)
    df2 = df.merge(company_fs[["corp_code", "fs_group"]], on="corp_code", how="left")

    agg = (
        df2.groupby("fs_group")
        .agg(
            n_company_years=("m_score", "count"),
            n_companies=("corp_code", "nunique"),
            flag_rate=("flag", "mean"),
            critical_rate=("risk_tier", lambda s: (s == "Critical").mean()),
            high_rate=("risk_tier", lambda s: (s == "High").mean()),
            medium_rate=("risk_tier", lambda s: (s == "Medium").mean()),
            low_rate=("risk_tier", lambda s: (s == "Low").mean()),
            median_m_score=("m_score", "median"),
        )
        .reset_index()
    )

    _print_section("Q4 — CFS vs OFS flag distribution")
    print(f"  {'Group':<12} {'Cos':>6} {'Co-yrs':>7} {'flag%':>6} {'crit%':>6} {'high%':>6} {'med%':>5} {'low%':>5} {'med_m':>7}")
    print(f"  {'-'*70}")
    for _, row in agg.iterrows():
        print(
            f"  {str(row['fs_group']):<12} {row['n_companies']:>6} "
            f"{row['n_company_years']:>7} "
            f"{row['flag_rate']*100:>5.1f}% {row['critical_rate']*100:>5.1f}% "
            f"{row['high_rate']*100:>5.1f}% {row['medium_rate']*100:>4.1f}% "
            f"{row['low_rate']*100:>4.1f}% {row['median_m_score']:>7.3f}"
        )

    switched_crit = agg.loc[agg["fs_group"] == "switched", "critical_rate"].values
    pure_cfs_crit = agg.loc[agg["fs_group"] == "pure_CFS", "critical_rate"].values
    if len(switched_crit) and len(pure_cfs_crit):
        ratio = switched_crit[0] / pure_cfs_crit[0] if pure_cfs_crit[0] > 0 else float("inf")
        print()
        print(f"  Switched vs pure_CFS Critical rate ratio: {ratio:.2f}x")
        if ratio > 1.5:
            print("  → Switched filers are OVER-represented in Critical tier.")
            print("    Some Critical flags may be accounting-basis artifacts.")
        else:
            print("  → No strong over-representation of switched filers in Critical tier.")

    return agg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Loading {SCORES_PATH} ...")
    df = _load()
    print(f"  {len(df):,} scored company-years, {df['corp_code'].nunique():,} companies")
    print(f"  Years: {sorted(df['year'].unique())}")
    print(f"  Risk tier counts: {df['risk_tier'].value_counts().to_dict()}")

    q1 = q1_critical_drivers(df)
    q2 = q2_sector_breakdown(df)
    q3 = q3_repeat_flaggers(df)
    q4 = q4_fs_type_distribution(df)

    # Write CSVs
    q1_path = OUT_DIR / "phase1_q1_critical_drivers.csv"
    q2_path = OUT_DIR / "phase1_q2_sector_breakdown.csv"
    q3_path = OUT_DIR / "phase1_q3_repeat_flaggers.csv"
    q4_path = OUT_DIR / "phase1_q4_fs_type_distribution.csv"

    q1.to_csv(q1_path, index=False, encoding="utf-8-sig")
    q2.to_csv(q2_path, index=False, encoding="utf-8-sig")
    q3.to_csv(q3_path, index=False, encoding="utf-8-sig")
    q4.to_csv(q4_path, index=False, encoding="utf-8-sig")

    _print_section("Outputs written")
    for p in [q1_path, q2_path, q3_path, q4_path]:
        print(f"  {p}")
    print()


if __name__ == "__main__":
    main()
