"""
run_cb_bw_timelines.py — Standalone runner for CB/BW timeline analysis.

Extracts the scoring logic from cb_bw_timelines.py (Marimo app) and runs it
as a plain Python script. Produces the same output files without the Marimo UI.

Outputs:
    03_Analysis/cb_bw_summary.csv
    03_Analysis/cb_bw_timelines/<corp_code>_<date>.json  (flagged events only)

Run:
    python 03_Analysis/run_cb_bw_timelines.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd


class FlagDetails(TypedDict, total=False):
    """Sparse dict accumulating per-event flag evidence. All keys are optional."""
    repricing_flag:        bool
    exercise_cluster_flag: bool
    peak_date:             str | None    # str(pd.Timestamp) or None
    volume_ratio:          float         # rounded to 2dp


class CbBwResult(TypedDict):
    """One row in the cb_bw_summary.csv output."""
    corp_code:              str
    ticker:                 str
    issue_date:             str          # "YYYY-MM-DD"
    bond_type:              str
    exercise_price:         float | None
    anomaly_score:          int
    flag_count:             int
    flags:                  str          # comma-separated flag names
    repricing_flag:         bool
    exercise_cluster_flag:  bool
    volume_flag:            bool
    holdings_flag:          bool
    volume_ratio:           float | None
    peak_date:              str | None
    dart_link:              str

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "01_Data" / "processed"
ANALYSIS = ROOT / "03_Analysis"


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    required = ["cb_bw_events.parquet", "price_volume.parquet", "officer_holdings.parquet"]
    missing = [f for f in required if not (PROCESSED / f).exists()]
    if missing:
        print(f"ERROR: Missing data files: {', '.join(missing)}", file=sys.stderr)
        print("Run: python 02_Pipeline/pipeline.py --market KOSDAQ --start 2020 --end 2025", file=sys.stderr)
        sys.exit(1)

    df_cb = pd.read_parquet(PROCESSED / "cb_bw_events.parquet")
    df_pv = pd.read_parquet(PROCESSED / "price_volume.parquet")
    df_oh = pd.read_parquet(PROCESSED / "officer_holdings.parquet")
    df_map = (
        pd.read_parquet(PROCESSED / "corp_ticker_map.parquet")
        if (PROCESSED / "corp_ticker_map.parquet").exists()
        else pd.DataFrame()
    )
    print(f"Loaded {len(df_cb):,} CB/BW events across {df_cb['corp_code'].nunique():,} companies")
    return df_cb, df_pv, df_oh, df_map


def prepare_price(df_pv: pd.DataFrame) -> pd.DataFrame:
    df = df_pv.copy()
    date_col = next(
        (c for c in df.columns if "date" in c.lower()),
        df.columns[1] if len(df.columns) > 1 else None,
    )
    if date_col and date_col != "date":
        df = df.rename(columns={date_col: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["ticker", "date"])
    return df


def score_events(
    df_cb: pd.DataFrame,
    df_pv_clean: pd.DataFrame,
    df_oh: pd.DataFrame,
    df_map: pd.DataFrame,
) -> pd.DataFrame:
    if not df_map.empty and "corp_code" in df_map.columns and "ticker" in df_map.columns:
        map_lookup = df_map.set_index("corp_code")["ticker"].to_dict()
    else:
        map_lookup = {}

    results = []
    for _, event in df_cb.iterrows():
        corp_code = event["corp_code"]
        issue_date_raw = event.get("issue_date")
        bond_type = event.get("bond_type", "CB")
        exercise_price = event.get("exercise_price")

        if not issue_date_raw:
            continue

        issue_date = pd.to_datetime(issue_date_raw, errors="coerce")
        if pd.isna(issue_date):
            continue

        ticker = map_lookup.get(corp_code)
        if not ticker:
            continue

        df_ticker = df_pv_clean[df_pv_clean["ticker"] == ticker].copy()
        if df_ticker.empty or "close" not in df_ticker.columns:
            continue

        df_ticker = df_ticker.sort_values("date").reset_index(drop=True)
        issue_idx = df_ticker["date"].searchsorted(issue_date)

        window_start = max(0, issue_idx - 60)
        window_end = min(len(df_ticker), issue_idx + 61)
        df_window = df_ticker.iloc[window_start:window_end].copy()
        df_pre = df_ticker.iloc[max(0, window_start - 30):window_start].copy()

        if df_window.empty:
            continue

        flags = []
        flag_details: FlagDetails = {}

        # Flag 1: Repricing below market price
        repricing_flag = False
        repricing_raw = event.get("repricing_history", "[]")
        try:
            repricings = json.loads(repricing_raw) if isinstance(repricing_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            repricings = []
        for rp in repricings:
            rp_price = rp.get("new_price") or rp.get("조정가액")
            rp_date_raw = rp.get("date") or rp.get("조정일자")
            if not rp_price or not rp_date_raw:
                continue   # skip malformed repricing record; can't score without price+date
            rp_date = pd.to_datetime(str(rp_date_raw)[:8], errors="coerce")
            if not pd.isna(rp_date):
                candidates = df_ticker[df_ticker["date"] <= rp_date]["close"]
                market_price_at_rp = candidates.iloc[-1] if not candidates.empty else None
                if market_price_at_rp and float(rp_price) < market_price_at_rp * 0.95:
                    repricing_flag = True
        if repricing_flag:
            flags.append("repricing_below_market")
            flag_details["repricing_flag"] = True

        # Flag 2: Exercise clustering within 5 days of price peak
        exercise_cluster_flag = False
        peak_date = None
        exercise_raw = event.get("exercise_events", "[]")
        try:
            exercises = json.loads(exercise_raw) if isinstance(exercise_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            exercises = []
        if not df_window.empty and "close" in df_window.columns:
            peak_idx = df_window["close"].idxmax()
            peak_date = df_window.loc[peak_idx, "date"] if peak_idx in df_window.index else None
            for ex in exercises:
                ex_date_raw = ex.get("exercise_date") or ex.get("권리행사일")
                if not ex_date_raw:
                    continue   # skip malformed exercise record
                if peak_date is not None:
                    ex_date = pd.to_datetime(str(ex_date_raw)[:8], errors="coerce")
                    if not pd.isna(ex_date) and abs((ex_date - peak_date).days) <= 5:
                        exercise_cluster_flag = True
        if exercise_cluster_flag:
            flags.append("exercise_at_peak")
            flag_details["exercise_cluster_flag"] = True
            flag_details["peak_date"] = str(peak_date) if peak_date is not None else None

        # Flag 3: Volume ratio > 3× pre-event baseline
        volume_flag = False
        volume_ratio = None
        vol_col = next((c for c in df_window.columns if "volume" in c.lower() or "거래량" in c), None)
        if vol_col and not df_pre.empty and vol_col in df_pre.columns:
            baseline_vol = df_pre[vol_col].mean()
            event_vol = df_window[vol_col].mean()
            if baseline_vol and baseline_vol > 0:
                volume_ratio = event_vol / baseline_vol
                if volume_ratio > 3.0:
                    volume_flag = True
        if volume_flag:
            flags.append("volume_surge")
            flag_details["volume_ratio"] = round(float(volume_ratio), 2)

        # Flag 4: Officer holdings decrease post-exercise
        holdings_flag = False
        df_corp_oh = df_oh[df_oh["corp_code"] == corp_code].copy()
        if not df_corp_oh.empty and "date" in df_corp_oh.columns:
            df_corp_oh["date"] = pd.to_datetime(df_corp_oh["date"].astype(str).str[:8], errors="coerce")
            post_ex = df_corp_oh[df_corp_oh["date"] > issue_date]
            pre_ex = df_corp_oh[df_corp_oh["date"] <= issue_date]
            if not post_ex.empty and not pre_ex.empty:
                try:
                    pre_shares = pd.to_numeric(pre_ex["change_shares"], errors="coerce").sum()
                    post_shares = pd.to_numeric(post_ex["change_shares"], errors="coerce").sum()
                    if pre_shares > 0 and post_shares < pre_shares * 0.95:
                        holdings_flag = True
                except Exception:
                    pass
        if holdings_flag:
            flags.append("holdings_decrease")

        anomaly_score = len(flags)
        row: CbBwResult = {
            "corp_code": corp_code,
            "ticker": ticker,
            "issue_date": str(issue_date.date()),
            "bond_type": bond_type,
            "exercise_price": exercise_price,
            "anomaly_score": anomaly_score,
            "flag_count": anomaly_score,
            "flags": ", ".join(flags),
            "repricing_flag": "repricing_below_market" in flags,
            "exercise_cluster_flag": "exercise_at_peak" in flags,
            "volume_flag": "volume_surge" in flags,
            "holdings_flag": "holdings_decrease" in flags,
            "volume_ratio": flag_details.get("volume_ratio"),
            "peak_date": flag_details.get("peak_date"),
            "dart_link": f"https://dart.fss.or.kr/corp/searchAjax.do?textCrpCik={corp_code}",
        }
        results.append(row)

    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_results = df_results.sort_values("anomaly_score", ascending=False)
    return df_results


def export_results(df_results: pd.DataFrame) -> None:
    out_dir = ANALYSIS / "cb_bw_timelines"
    out_dir.mkdir(parents=True, exist_ok=True)

    flagged = df_results[df_results["anomaly_score"] > 0]
    for _, row in flagged.iterrows():
        fname = f"{row['corp_code']}_{row['issue_date'].replace('-', '')}.json"
        with open(out_dir / fname, "w", encoding="utf-8") as f:
            json.dump(row.to_dict(), f, ensure_ascii=False, indent=2, default=str)

    summary_path = ANALYSIS / "cb_bw_summary.csv"
    df_results.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(f"Exported {len(df_results):,} events to {summary_path}")
    print(f"  Flagged (>=1 flag): {len(flagged):,}")
    print(f"  High risk (>=3 flags): {(df_results['anomaly_score'] >= 3).sum():,}")
    print(f"Per-event JSON in {out_dir}/")


def main() -> None:
    df_cb, df_pv, df_oh, df_map = load_data()
    df_pv_clean = prepare_price(df_pv)
    print("Scoring CB/BW events...")
    df_results = score_events(df_cb, df_pv_clean, df_oh, df_map)
    print(f"Scored {len(df_results):,} events")
    export_results(df_results)


if __name__ == "__main__":
    main()
