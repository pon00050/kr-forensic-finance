"""
run_timing_anomalies.py — Standalone runner for disclosure timing anomaly detection.

Extracts the scoring logic from timing_anomalies.py (Marimo app) and runs it
as a plain Python script. Produces the same output files without the Marimo UI.

Output:
    03_Analysis/timing_anomalies.csv

Run:
    python 03_Analysis/run_timing_anomalies.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "01_Data" / "processed"
ANALYSIS = ROOT / "03_Analysis"

# Add project root and analysis dir to sys.path for src.constants and _scoring
for _p in (str(ROOT), str(ROOT / "03_Analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _scoring import score_disclosures  # noqa: E402
from krff.constants import TIMING_GAP_HOURS_ASSUMED  # noqa: E402

MATERIAL_KEYWORDS = [
    "주요사항", "전환사채", "신주인수권", "유상증자", "합병", "분할", "양수도",
    "최대주주", "특수관계인", "풍문", "조회공시", "공급계약", "매출액",
]

_TYPE_KEYWORDS: dict[str, str] = {
    "주요사항": "주요사항보고",
    "전환사채": "주요사항보고",
    "신주인수권": "주요사항보고",
    "유상증자": "주요사항보고",
    "합병": "주요사항보고",
    "분할": "주요사항보고",
    "최대주주": "지분공시",
    "특수관계인": "지분공시",
    "사업보고서": "정기공시",
    "분기보고서": "정기공시",
    "반기보고서": "정기공시",
    "감사보고서": "외부감사",
    "공급계약": "기타",
    "매출액": "기타",
    "풍문": "조회공시",
    "조회공시": "조회공시",
}


def _classify_disclosure_type(title: str) -> str:
    """Derive a DART category label from the disclosure title string."""
    if not title:
        return "기타"
    for kw, label in _TYPE_KEYWORDS.items():
        if kw in title:
            return label
    return "기타"


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    required = ["disclosures.parquet", "price_volume.parquet"]
    missing = [f for f in required if not (PROCESSED / f).exists()]
    if missing:
        print(f"ERROR: Missing data files: {', '.join(missing)}", file=sys.stderr)
        print("Run: python 02_Pipeline/pipeline.py and python 02_Pipeline/extract_disclosures.py", file=sys.stderr)
        sys.exit(1)

    df_disc = pd.read_parquet(PROCESSED / "disclosures.parquet")
    df_pv = pd.read_parquet(PROCESSED / "price_volume.parquet")
    df_map = (
        pd.read_parquet(PROCESSED / "corp_ticker_map.parquet")
        if (PROCESSED / "corp_ticker_map.parquet").exists()
        else pd.DataFrame()
    )
    print(f"Loaded {len(df_disc):,} disclosures, {df_pv['ticker'].nunique():,} tickers")
    return df_disc, df_pv, df_map


def prepare_disclosures(df_disc: pd.DataFrame) -> pd.DataFrame:
    df = df_disc.copy()
    df["filed_at"] = pd.to_datetime(df["filed_at"].astype(str).str[:8], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["filed_at"])
    df["filed_datetime"] = df["filed_at"] + pd.Timedelta(hours=18)
    df["trading_date"] = df["filed_at"]
    df["gap_hours"] = TIMING_GAP_HOURS_ASSUMED
    pattern = "|".join(MATERIAL_KEYWORDS)
    df["is_material"] = df["title"].str.contains(pattern, na=False)
    df["disclosure_type"] = df["title"].apply(_classify_disclosure_type)
    return df


def prepare_price(df_pv: pd.DataFrame) -> pd.DataFrame:
    df = df_pv.copy()
    date_col = next((c for c in df.columns if "date" in c.lower()), None)
    if date_col and date_col != "date":
        df = df.rename(columns={date_col: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values(["ticker", "date"])
    df["price_change_pct"] = df.groupby("ticker")["close"].pct_change() * 100

    vol_col = next((c for c in df.columns if "volume" in c.lower()), None)
    if vol_col:
        df["vol_30d_avg"] = (
            df.groupby("ticker")[vol_col]
            .transform(lambda x: x.rolling(30, min_periods=5).mean().shift(1))
        )
        df["volume_ratio"] = df[vol_col] / df["vol_30d_avg"].replace(0, float("nan"))
    else:
        df["volume_ratio"] = float("nan")
    return df


def main() -> None:
    df_disc, df_pv, df_map = load_data()
    df_disc_clean = prepare_disclosures(df_disc)
    df_pv_clean = prepare_price(df_pv)
    print("Scoring disclosure timing anomalies...")
    df_results = score_disclosures(df_disc_clean, df_pv_clean, df_map)
    print(f"Scored {len(df_results):,} disclosure-day pairs")

    out_path = ANALYSIS / "timing_anomalies.csv"
    df_results.to_csv(out_path, index=False, encoding="utf-8-sig")
    flagged = df_results["flag"].sum() if not df_results.empty else 0
    print(f"Exported {len(df_results):,} rows to {out_path}")
    print(f"  Flagged (>=5% price change AND >=2x volume): {flagged:,}")


if __name__ == "__main__":
    main()
