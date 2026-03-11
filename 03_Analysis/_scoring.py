"""Shared CB/BW event scoring logic.

Used by both run_cb_bw_timelines.py (standalone runner) and
cb_bw_timelines.py (Marimo app) to avoid duplicating ~150 lines of
scoring code.
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

import numpy as np
import pandas as pd

from src.constants import (
    FLAG_REPRICING_BELOW_MARKET,
    FLAG_EXERCISE_AT_PEAK,
    FLAG_VOLUME_SURGE,
    FLAG_HOLDINGS_DECREASE,
    REPRICING_DISCOUNT_RATIO,
    EXERCISE_PEAK_WINDOW_CALENDAR_DAYS,
    VOLUME_SURGE_RATIO,
    HOLDINGS_DECREASE_RATIO,
    PRICE_WINDOW_TRADING_DAYS,
    TIMING_PRICE_CHANGE_PCT,
    TIMING_VOLUME_RATIO,
    TIMING_BORDERLINE_PRICE_PCT,
    TIMING_GAP_HOURS_ASSUMED,
    TIMING_GAP_HOURS_PRIOR_DAY,
)

log = logging.getLogger(__name__)


class FlagDetails(TypedDict, total=False):
    """Sparse dict accumulating per-event flag evidence. All keys optional."""
    repricing_flag:        bool
    exercise_cluster_flag: bool
    peak_date:             str | None
    volume_ratio:          float


class CbBwResult(TypedDict):
    """One row in the cb_bw_summary.csv output."""
    corp_code:              str
    ticker:                 str
    issue_date:             str
    bond_type:              str
    exercise_price:         float | None
    anomaly_score:          int
    flag_count:             int
    flags:                  str
    repricing_flag:         bool
    exercise_cluster_flag:  bool
    volume_flag:            bool
    holdings_flag:          bool
    volume_ratio:           float | None
    peak_date:              str | None
    peak_before_issue:      bool
    dart_link:              str
    issue_amount:           float | None
    refixing_floor:         float | None


def score_events(
    df_cb: pd.DataFrame,
    df_pv_clean: pd.DataFrame,
    df_oh: pd.DataFrame,
    df_map: pd.DataFrame,
) -> pd.DataFrame:
    """Score CB/BW events against 4 manipulation flags.

    Returns DataFrame with flag columns, flag_count, and peak_date.
    """
    if not df_map.empty and "corp_code" in df_map.columns and "ticker" in df_map.columns:
        map_lookup = df_map.set_index("corp_code")["ticker"].to_dict()
    else:
        map_lookup = {}

    # Pre-group price data by ticker to avoid repeated full-DataFrame filtering
    pv_by_ticker = {
        t: g.sort_values("date").reset_index(drop=True)
        for t, g in df_pv_clean.groupby("ticker")
    }

    # Pre-group officer holdings by corp_code to avoid O(n×m) scan in event loop
    oh_by_corp: dict[str, pd.DataFrame] = {
        cc: g.copy()
        for cc, g in df_oh.groupby("corp_code")
    } if not df_oh.empty and "corp_code" in df_oh.columns else {}

    results: list[CbBwResult] = []
    for event in df_cb.itertuples(index=False):
        # itertuples preserves dtypes (no int→float upcast); access fields as attributes
        corp_code = event.corp_code
        issue_date_raw = getattr(event, "issue_date", None)
        bond_type = getattr(event, "bond_type", "CB")
        exercise_price = getattr(event, "exercise_price", None)
        issue_amount = getattr(event, "issue_amount", None)
        refixing_floor = getattr(event, "refixing_floor", None)

        if not issue_date_raw:
            continue

        issue_date = pd.to_datetime(issue_date_raw, errors="coerce")
        if pd.isna(issue_date):
            continue

        ticker = map_lookup.get(corp_code)
        if not ticker:
            continue

        df_ticker = pv_by_ticker.get(ticker)
        if df_ticker is None or df_ticker.empty or "close" not in df_ticker.columns:
            continue
        issue_idx = df_ticker["date"].searchsorted(issue_date)

        window_start = max(0, issue_idx - PRICE_WINDOW_TRADING_DAYS)
        window_end = min(len(df_ticker), issue_idx + PRICE_WINDOW_TRADING_DAYS + 1)
        df_window = df_ticker.iloc[window_start:window_end].copy()
        df_pre = df_ticker.iloc[max(0, window_start - 30):window_start].copy()

        if df_window.empty:
            continue

        flags: list[str] = []
        flag_details: FlagDetails = {}

        # Flag 1: Repricing below market price
        repricing_flag = False
        repricing_raw = getattr(event, "repricing_history", "[]")
        try:
            repricings = json.loads(repricing_raw) if isinstance(repricing_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            repricings = []
        for rp in repricings:
            rp_price = rp.get("new_price") or rp.get("조정가액")
            rp_date_raw = rp.get("date") or rp.get("조정일자")
            if not rp_price or not rp_date_raw:
                continue
            rp_date = pd.to_datetime(str(rp_date_raw)[:8], errors="coerce")
            if not pd.isna(rp_date):
                candidates = df_ticker[df_ticker["date"] <= rp_date]["close"]
                market_price_at_rp = candidates.iloc[-1] if not candidates.empty else None
                if market_price_at_rp and float(rp_price) < market_price_at_rp * REPRICING_DISCOUNT_RATIO:
                    repricing_flag = True
        if repricing_flag:
            flags.append(FLAG_REPRICING_BELOW_MARKET)
            flag_details["repricing_flag"] = True

        # Flag 2: Exercise clustering within 5 days of price peak
        exercise_cluster_flag = False
        peak_date = None
        exercise_raw = getattr(event, "exercise_events", "[]")
        try:
            exercises = json.loads(exercise_raw) if isinstance(exercise_raw, str) else []
        except (json.JSONDecodeError, TypeError):
            exercises = []
        if not df_window.empty and "close" in df_window.columns:
            close_valid = df_window["close"].dropna()
            if not close_valid.empty:
                peak_idx = close_valid.idxmax()
                peak_date = df_window.loc[peak_idx, "date"] if peak_idx in df_window.index else None
            for ex in exercises:
                ex_date_raw = ex.get("exercise_date") or ex.get("권리행사일")
                if not ex_date_raw:
                    continue
                if peak_date is not None:
                    ex_date = pd.to_datetime(str(ex_date_raw)[:8], errors="coerce")
                    # EXERCISE_PEAK_WINDOW_CALENDAR_DAYS uses calendar days intentionally
                    # (not trading days) for simplicity — see Fix 1B in session 84
                    if not pd.isna(ex_date) and abs((ex_date - peak_date).days) <= EXERCISE_PEAK_WINDOW_CALENDAR_DAYS:
                        exercise_cluster_flag = True
        # Always store peak_date — computed from price data regardless of exercise events
        if peak_date is not None:
            flag_details["peak_date"] = str(peak_date)
        if exercise_cluster_flag:
            flags.append(FLAG_EXERCISE_AT_PEAK)
            flag_details["exercise_cluster_flag"] = True

        # Flag 3: Volume ratio > 3x pre-event baseline
        volume_flag = False
        volume_ratio = None
        vol_col = next((c for c in df_window.columns if "volume" in c.lower() or "거래량" in c), None)
        if vol_col and not df_pre.empty and vol_col in df_pre.columns:
            baseline_vol = df_pre[vol_col].mean()
            event_vol = df_window[vol_col].mean()
            if baseline_vol and baseline_vol > 0:
                volume_ratio = event_vol / baseline_vol
                flag_details["volume_ratio"] = round(float(volume_ratio), 2)
                if volume_ratio > VOLUME_SURGE_RATIO:
                    volume_flag = True
        if volume_flag:
            flags.append(FLAG_VOLUME_SURGE)

        # Flag 4: Officer holdings decrease post-exercise
        holdings_flag = False
        df_corp_oh = oh_by_corp.get(corp_code, pd.DataFrame())
        if not df_corp_oh.empty and "date" in df_corp_oh.columns:
            df_corp_oh["date"] = pd.to_datetime(df_corp_oh["date"].astype(str).str[:10], errors="coerce")
            post_ex = df_corp_oh[df_corp_oh["date"] > issue_date]
            pre_ex = df_corp_oh[df_corp_oh["date"] <= issue_date]
            if not post_ex.empty and not pre_ex.empty:
                try:
                    pre_shares = pd.to_numeric(pre_ex["change_shares"], errors="coerce").sum()
                    post_shares = pd.to_numeric(post_ex["change_shares"], errors="coerce").sum()
                    if pre_shares > 0 and post_shares < pre_shares * HOLDINGS_DECREASE_RATIO:
                        holdings_flag = True
                except (ValueError, TypeError) as exc:
                    log.debug("Holdings comparison failed for %s: %s", corp_code, exc)
        if holdings_flag:
            flags.append(FLAG_HOLDINGS_DECREASE)

        anomaly_score = len(flags)
        peak_date_val = flag_details.get("peak_date")
        peak_before_issue = (
            peak_date_val is not None
            and pd.Timestamp(peak_date_val) < issue_date
        )
        row: CbBwResult = {
            "corp_code": corp_code,
            "ticker": ticker,
            "issue_date": str(issue_date.date()),
            "bond_type": bond_type,
            "exercise_price": exercise_price,
            "anomaly_score": anomaly_score,
            "flag_count": anomaly_score,
            "flags": ", ".join(flags),
            "repricing_flag": FLAG_REPRICING_BELOW_MARKET in flags,
            "exercise_cluster_flag": FLAG_EXERCISE_AT_PEAK in flags,
            "volume_flag": FLAG_VOLUME_SURGE in flags,
            "holdings_flag": FLAG_HOLDINGS_DECREASE in flags,
            "volume_ratio": flag_details.get("volume_ratio"),
            "peak_date": peak_date_val,
            "peak_before_issue": peak_before_issue,
            "dart_link": f"https://dart.fss.or.kr/corp/searchAjax.do?textCrpCik={corp_code}",
            "issue_amount": issue_amount,
            "refixing_floor": refixing_floor,
        }
        results.append(row)

    df_results = pd.DataFrame(results)
    if not df_results.empty:
        df_results = df_results.sort_values("anomaly_score", ascending=False)
    return df_results


def score_disclosures(
    df_disc_clean: pd.DataFrame,
    df_pv_clean: pd.DataFrame,
    df_map: pd.DataFrame,
) -> pd.DataFrame:
    """Score disclosure timing anomalies against price/volume movement.

    Returns DataFrame with flag columns, anomaly_score, and timing label.
    Vectorized via merge() instead of per-row MultiIndex lookup (~5-10x faster
    than the original iterrows loop at 271K disclosures × 2 offsets).
    """
    if not df_map.empty and "corp_code" in df_map.columns:
        map_lookup = df_map.drop_duplicates("corp_code").set_index("corp_code")["ticker"].to_dict()
    else:
        map_lookup = {}

    # Add ticker column vectorized — avoids per-row dict lookup
    if df_disc_clean.empty or "corp_code" not in df_disc_clean.columns:
        return pd.DataFrame()
    df_disc = df_disc_clean.copy()
    df_disc["_ticker"] = df_disc["corp_code"].map(map_lookup)
    df_disc = df_disc.dropna(subset=["_ticker"])
    if df_disc.empty:
        return pd.DataFrame()

    # Prepare price/volume lookup — drop NaN values upfront (equivalent to the
    # original loop's `if np.isnan(price_chg) or np.isnan(vol_ratio): continue`)
    pv = df_pv_clean[["ticker", "date", "price_change_pct", "volume_ratio"]].copy()
    pv["price_change_pct"] = pd.to_numeric(pv["price_change_pct"], errors="coerce")
    pv["volume_ratio"] = pd.to_numeric(pv["volume_ratio"], errors="coerce")
    pv = pv.dropna(subset=["price_change_pct", "volume_ratio"])

    _gap_map = {
        "same_day": TIMING_GAP_HOURS_ASSUMED,    # 2.5 h: filing ~18:00, close 15:30
        "prior_day": TIMING_GAP_HOURS_PRIOR_DAY,  # 15.0 h: filing ~18:00, open 09:00 next day
    }

    result_frames: list[pd.DataFrame] = []
    for offset_days, label in [(0, "same_day"), (-1, "prior_day")]:
        gap_hours = _gap_map[label]

        disc_offset = df_disc.copy()
        disc_offset["_check_date"] = disc_offset["trading_date"] + pd.Timedelta(days=offset_days)

        # Inner join: equivalent to the original loop's `if key not in pv_idx.index: continue`
        merged = disc_offset.merge(
            pv,
            left_on=["_ticker", "_check_date"],
            right_on=["ticker", "date"],
            how="inner",
        )
        if merged.empty:
            continue

        price_abs = merged["price_change_pct"].abs()
        merged["_anomaly_score"] = price_abs * merged["volume_ratio"] * gap_hours
        is_mat = (
            merged["is_material"].fillna(False).astype(bool)
            if "is_material" in merged.columns
            else pd.Series(False, index=merged.index)
        )
        merged["_flag"] = (
            is_mat
            & (price_abs >= TIMING_PRICE_CHANGE_PCT)
            & (merged["volume_ratio"] >= TIMING_VOLUME_RATIO)
        )

        keep = merged[merged["_flag"] | (price_abs >= TIMING_BORDERLINE_PRICE_PCT)]
        if keep.empty:
            continue

        out = pd.DataFrame({
            "corp_code": keep["corp_code"].values,
            "ticker": keep["_ticker"].values,
            "filing_date": keep["trading_date"].dt.date.astype(str).values,
            "check_date": keep["_check_date"].dt.date.astype(str).values,
            "timing": label,
            "disclosure_type": keep["disclosure_type"].values if "disclosure_type" in keep.columns else None,
            "title": keep["title"].values if "title" in keep.columns else None,
            "price_change_pct": keep["price_change_pct"].round(2).values,
            "volume_ratio": keep["volume_ratio"].round(2).values,
            "gap_hours": round(gap_hours, 1),
            "anomaly_score": keep["_anomaly_score"].round(2).values,
            "flag": keep["_flag"].values,
            "is_material": keep["is_material"].values if "is_material" in keep.columns else False,
            "dart_link": keep["dart_link"].values if "dart_link" in keep.columns else None,
        })
        result_frames.append(out)

    if not result_frames:
        return pd.DataFrame()

    df_results = pd.concat(result_frames, ignore_index=True)
    df_results = df_results.sort_values("anomaly_score", ascending=False)
    return df_results
