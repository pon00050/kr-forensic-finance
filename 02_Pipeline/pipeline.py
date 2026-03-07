"""
pipeline.py — Phase 1 ETL orchestrator.

Calls extract_dart → transform in dependency order.
All steps are idempotent: re-running is safe and skips existing files.

Phase 1 stages:
    dart       Build KOSDAQ universe + fetch annual financial statements (2019–2023)
    transform  Raw Parquet → company_financials.parquet

Phase 2 stage (cb_bw) is implemented. KRX, SEIBRO, KFTC standalone stages are not yet wired.
Run `--stage dart` and `--stage transform` independently to control quota usage.

Usage:
    # Full Phase 1 run
    python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023

    # DART extraction only (financials + sector)
    python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023 --stage dart

    # Transform only (after extraction is complete)
    python 02_Pipeline/pipeline.py --start 2019 --end 2023 --stage transform

    # Single company test
    python 02_Pipeline/pipeline.py --stage dart --corp-code 00126380 --start 2019 --end 2023

    # Sample mode (first 50 companies — ~4 min, good for development)
    python 02_Pipeline/pipeline.py --market KOSDAQ --start 2022 --end 2023 --sample 50
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TypedDict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(stream=open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False))],
)
log = logging.getLogger(__name__)

# Ensure 02_Pipeline/ is importable regardless of working directory
_PIPELINE_DIR = Path(__file__).parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))


class RunSummaryEntry(TypedDict):
    """Shape of each entry in partial_data and errors lists."""
    corp_code: str          # 8-digit DART identifier (only key accessed here)
    # Additional keys from extract_dart are preserved as-is


class RunSummary(TypedDict):
    """Shape of run_summary.json — produced and consumed by _merge_run_summaries."""
    total_companies:  int
    years:            list[int]
    completed_at:     str | None
    elapsed_minutes:  float | None
    full_data:        list[str]
    partial_data:     list[RunSummaryEntry]
    no_data:          list[str]
    errors:           list[RunSummaryEntry]


def _merge_run_summaries(old: RunSummary | dict, new: RunSummary) -> RunSummary:
    """Merge two run summary dicts. new wins on conflicts; full_data > partial_data > no_data."""
    merged_full    = set(old.get("full_data", []))
    merged_partial = {
        e["corp_code"]: e
        for e in old.get("partial_data", [])
        if "corp_code" in e
    }
    merged_no_data = set(old.get("no_data", []))
    merged_errors  = {
        e["corp_code"]: e
        for e in old.get("errors", [])
        if "corp_code" in e
    }

    for corp_code in new.get("full_data", []):
        merged_full.add(corp_code)
        merged_partial.pop(corp_code, None)
        merged_no_data.discard(corp_code)
        merged_errors.pop(corp_code, None)
    for entry in new.get("partial_data", []):
        cc = entry.get("corp_code")
        if cc is None:
            continue
        if cc not in merged_full:
            merged_partial[cc] = entry
            merged_no_data.discard(cc)
            merged_errors.pop(cc, None)
    for corp_code in new.get("no_data", []):
        if corp_code not in merged_full and corp_code not in merged_partial:
            merged_no_data.add(corp_code)
    for entry in new.get("errors", []):
        if "corp_code" in entry:
            merged_errors[entry["corp_code"]] = entry

    return {
        "total_companies": new["total_companies"],
        "years": new["years"],
        "completed_at": new.get("completed_at"),
        "elapsed_minutes": new.get("elapsed_minutes"),
        "full_data":    sorted(merged_full),
        "partial_data": list(merged_partial.values()),
        "no_data":      sorted(merged_no_data),
        "errors":       list(merged_errors.values()),
    }


def run_stage_dart(
    market: str,
    start: int,
    end: int,
    stage: str | None,
    corp_code: str | None,
    force: bool,
    rebuild: bool = False,
    sample: int | None = None,
    max_minutes: float | None = None,
    sleep: float | None = None,
    wics_date: str | None = None,
) -> None:
    """
    Run DART extraction stages.

    `stage` here refers to sub-stages within extract_dart:
      'company-list' | 'financials' | 'sector' | None (all three)

    sample: if set, limit the company universe to the first N companies.
    max_minutes: hard wall-clock deadline for the financials fetch loop.
    sleep: override all sleep constants (test mode).
    """
    import extract_dart as ed

    if sleep is not None:
        ed._apply_sleep_override(sleep)

    if wics_date is not None:
        ed._wics_cache = wics_date
        log.info("WICS snapshot date pinned to %s (--wics-date override)", wics_date)

    if corp_code:
        # Single-company test mode
        log.info("Single-company mode: %s", corp_code)
        dart = ed._dart()
        companies = ed.fetch_company_list(market=market, force=force)
        comp_row = companies[companies["corp_code"] == corp_code]
        if comp_row.empty:
            log.error("corp_code %s not in %s universe", corp_code, market)
            return
        corp_name = comp_row.iloc[0]["corp_name"]
        years = list(range(start, end + 1))
        results = ed.fetch_financials_for_company(
            corp_code, corp_name, years, dart, force=force, rebuild=rebuild
        )
        log.info("Results for %s: %s", corp_code, results)
        return

    if stage in (None, "company-list"):
        ed.fetch_company_list(market=market, force=force, rebuild=rebuild)

    if stage in (None, "financials"):
        companies = ed.fetch_company_list(market=market, force=False)
        summary = ed.fetch_all_financials(
            companies, start_year=start, end_year=end,
            force=force, rebuild=rebuild, sample=sample, max_minutes=max_minutes,
        )
        # Write run summary — merge with existing if this is a resumed run
        import json
        out = Path("01_Data/raw/run_summary.json")
        out.parent.mkdir(parents=True, exist_ok=True)

        if out.exists():
            with open(out, encoding="utf-8") as f:
                old = json.load(f)
        else:
            old = {}
        merged_summary = _merge_run_summaries(old, summary)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(merged_summary, f, ensure_ascii=False, indent=2)
        log.info(
            "Run summary: %d full, %d partial, %d no-data, %d errors",
            len(merged_summary["full_data"]),
            len(merged_summary["partial_data"]),
            len(merged_summary["no_data"]),
            len(merged_summary["errors"]),
        )

    if stage in (None, "sector"):
        companies = ed.fetch_company_list(market=market, force=False)
        ed.fetch_wics(force=force, rebuild=rebuild, year=end)
        ed.fetch_ksic(companies, force=force, rebuild=rebuild, sample=sample)


def run_stage_cb_bw(
    force: bool = False,
    rebuild: bool = False,
    sample: int | None = None,
    sleep: float | None = None,
    max_minutes: float | None = None,
    scoped: bool = False,
    top_n: int = 100,
    backend: str = "pykrx",
) -> None:
    """Phase 2: fetch CB/BW events → price/volume → officer holdings."""
    import extract_cb_bw as ecb
    import extract_price_volume as epv
    import extract_officer_holdings as eoh

    _sleep = sleep if sleep is not None else 0.5

    import extract_corp_ticker_map as ectm

    log.info("=== Stage: cb_bw (corp_ticker_map) ===")
    ectm.build_corp_ticker_map(force=force)

    log.info("=== Stage: cb_bw (CB/BW events) ===")
    ecb.fetch_cb_bw_events(force=force, sample=sample, sleep=_sleep, max_minutes=max_minutes, scoped=scoped, top_n=top_n)

    log.info("=== Stage: cb_bw (price/volume) ===")
    epv.fetch_price_volume(force=force, sample=sample, sleep=_sleep, max_minutes=max_minutes, backend=backend)

    log.info("=== Stage: cb_bw (officer holdings) ===")
    eoh.fetch_officer_holdings(force=force, sample=sample, sleep=_sleep, max_minutes=max_minutes)

    import extract_major_holders as emh

    log.info("=== Stage: cb_bw (major holders / 대량보유보고) ===")
    emh.fetch_major_holders(force=force, rebuild=rebuild, sample=sample, sleep=_sleep, max_minutes=max_minutes)

    import extract_disclosures as edisc

    log.info("=== Stage: cb_bw (disclosures) ===")
    edisc.fetch_disclosures(force=force, sample=sample, sleep=_sleep, max_minutes=max_minutes)

    import extract_seibro_repricing as eseibro

    log.info("=== Stage: cb_bw (SEIBRO repricing + exercise enrichment) ===")
    try:
        eseibro.enrich_cb_bw_parquet(force=force, rebuild=rebuild, sample=sample, sleep=_sleep)
    except EnvironmentError as exc:
        log.warning("SEIBRO enrichment skipped — %s", exc)


def run_stage_transform(start: int, end: int, sample: int | None = None, force: bool = False, rebuild: bool = False) -> None:
    """Run transform.py to build company_financials.parquet."""
    import transform as tr
    log.info("=== Stage: transform (%d–%d) ===", start, end)
    tr.run(start_year=start, end_year=end, sample=sample, force=force)


def run(
    market: str = "KOSDAQ",
    start: int = 2019,
    end: int = 2023,
    stage: str | None = None,
    corp_code: str | None = None,
    force: bool = False,
    rebuild: bool = False,
    sample: int | None = None,
    max_minutes: float | None = None,
    sleep: float | None = None,
    wics_date: str | None = None,
    scoped: bool = False,
    top_n: int = 100,
    backend: str = "pykrx",
) -> None:
    """
    Run the Phase 1 pipeline.

    stage: 'dart' | 'transform' | 'cb_bw' | None (dart+transform in sequence)
    """
    log.info(
        "=== Pipeline: market=%s, years=%d–%d, stage=%s ===",
        market, start, end, stage or "all",
    )

    if stage == "dart":
        log.info("=== Stage: dart ===")
        run_stage_dart(
            market, start, end, stage=None, corp_code=corp_code,
            force=force, rebuild=rebuild, sample=sample, max_minutes=max_minutes,
            sleep=sleep, wics_date=wics_date,
        )
        return

    if stage == "transform":
        run_stage_transform(start, end, sample=sample, force=force, rebuild=rebuild)
        return

    if stage == "cb_bw":
        log.info("=== Stage: cb_bw ===")
        run_stage_cb_bw(force=force, rebuild=rebuild, sample=sample, sleep=sleep, max_minutes=max_minutes, scoped=scoped, top_n=top_n, backend=backend)
        return

    # Full Phase 1 pipeline: dart → transform
    log.info("=== Stage: dart ===")
    run_stage_dart(
        market, start, end, stage=None, corp_code=corp_code,
        force=force, rebuild=rebuild, sample=sample, max_minutes=max_minutes,
        sleep=sleep, wics_date=wics_date,
    )

    log.info("=== Stage: transform ===")
    run_stage_transform(start, end, sample=sample, force=force, rebuild=rebuild)

    log.info("=== Phase 1 pipeline complete ===")
    log.info(
        "Next: python 03_Analysis/beneish_screen.py"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the kr-forensic-finance Phase 1 ETL pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Full Phase 1 run:
    python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023

  DART extraction only (leaves transform for later):
    python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023 --stage dart

  Transform only (after extraction is complete):
    python 02_Pipeline/pipeline.py --start 2019 --end 2023 --stage transform

  Re-fetch everything from scratch:
    python 02_Pipeline/pipeline.py --market KOSDAQ --start 2019 --end 2023 --force

  Single company test (NAVER):
    python 02_Pipeline/pipeline.py --stage dart --corp-code 00126380 --start 2022 --end 2023

  Sample mode — first 50 companies, 2 years (~4 min):
    python 02_Pipeline/pipeline.py --market KOSDAQ --start 2022 --end 2023 --sample 50
        """,
    )
    parser.add_argument(
        "--market",
        default="KOSDAQ",
        choices=["KOSDAQ", "KOSPI", "KONEX"],
        help="Market to process (default: KOSDAQ)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=2019,
        help="First fiscal year, inclusive (default: 2019)",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=2023,
        help="Last fiscal year, inclusive (default: 2023)",
    )
    parser.add_argument(
        "--stage",
        choices=["dart", "transform", "cb_bw"],
        help="Run a single stage only (default: run both)",
    )
    parser.add_argument(
        "--corp-code",
        dest="corp_code",
        help="Run DART extraction for a single company (8-digit DART code)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch all data even if output files already exist",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild parquets from cached raw files without re-fetching from API",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help="Limit universe to first N companies (for development/testing). "
             "~4 min for N=50, 5 years. Use before running the full universe.",
    )
    parser.add_argument(
        "--max-minutes",
        type=float,
        default=None,
        metavar="M",
        dest="max_minutes",
        help="Hard wall-clock deadline for the financials fetch loop (default: no limit). "
             "Use --max-minutes 3 for a guaranteed-short test run.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Override inter-request sleep (default: 0.5s/0.3s/1.0s per stage). "
             "Use --sleep 0.1 for test runs with --sample.",
    )
    parser.add_argument(
        "--wics-date",
        default=None,
        metavar="YYYYMMDD",
        dest="wics_date",
        help="Pin WICS snapshot date. Note: WICS only serves recent dates.",
    )
    parser.add_argument(
        "--scoped",
        action="store_true",
        help="(cb_bw stage) Apply Phase 2 scoping filter: top-N by M-Score union companies with CB/BW events.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=100,
        metavar="N",
        dest="top_n",
        help="(cb_bw stage) Number of top M-Score companies to include in scoped universe (default: 100).",
    )

    args = parser.parse_args()
    run(
        market=args.market,
        start=args.start,
        end=args.end,
        stage=args.stage,
        corp_code=args.corp_code,
        force=args.force,
        rebuild=args.rebuild,
        sample=args.sample,
        max_minutes=args.max_minutes,
        sleep=args.sleep,
        wics_date=args.wics_date,
        scoped=args.scoped,
        top_n=args.top_n,
    )
