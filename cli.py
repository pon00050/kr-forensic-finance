"""krff-shell CLI — entry point for the `krff` command.

Usage:
  krff run [OPTIONS]     Run the ETL pipeline
  krff analyze           Load and print beneish_scores.parquet
  krff charts            Generate beneish_viz.html from beneish_scores.parquet
  krff status            Show pipeline artifact inventory
  krff version           Print version
"""

from __future__ import annotations

import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Optional

import typer

# Windows: force UTF-8 stdout/stderr so Korean company names don't crash cp1252
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass  # Python < 3.7 fallback

app = typer.Typer(
    name="krff",
    help="Korean forensic-finance pipeline CLI",
    add_completion=False,
)

try:
    _VERSION = _pkg_version("krff-shell")
except Exception:
    try:
        _VERSION = _pkg_version("kr-forensic-finance")
    except Exception:
        _VERSION = "unknown"
_DEFAULT_PARQUET = Path(__file__).parent / "01_Data" / "processed" / "beneish_scores.parquet"
_ANALYSIS_DIR = Path(__file__).parent / "03_Analysis"


def _require_positive_sample(sample: Optional[int]) -> None:
    if sample is not None and sample < 1:
        raise typer.BadParameter(f"sample must be >= 1, got {sample}", param_hint="'--sample'")


@app.command()
def run(
    market: str = typer.Option("KOSDAQ", help="Exchange market (KOSDAQ or KOSPI)"),
    start: int = typer.Option(2019, help="Start year"),
    end: int = typer.Option(2023, help="End year"),
    stage: Optional[str] = typer.Option(None, help="Pipeline stage: dart | transform | cb_bw (default: dart + transform)"),
    corp_code: Optional[str] = typer.Option(None, help="Single corp_code to process"),
    force: bool = typer.Option(False, "--force", help="Re-download even if cached"),
    sample: Optional[int] = typer.Option(None, help="Limit to N companies (smoke test)"),
    max_minutes: Optional[float] = typer.Option(None, help="Hard time limit in minutes"),
    sleep: Optional[float] = typer.Option(None, help="Sleep seconds between API calls"),
    wics_date: Optional[str] = typer.Option(None, help="WICS snapshot date (YYYYMMDD)"),
    scoped: bool = typer.Option(False, "--scoped", help="Limit cb_bw stage to top-N flagged"),
    top_n: int = typer.Option(100, help="Top-N for scoped cb_bw stage"),
    backend: str = typer.Option("pykrx", help="OHLCV backend: pykrx (default), fdr, or yfinance"),
) -> None:
    """Run the ETL pipeline (DART extraction + transform)."""
    if market.upper() not in ("KOSDAQ", "KOSPI"):
        raise typer.BadParameter(f"market must be KOSDAQ or KOSPI, got {market!r}", param_hint="'--market'")
    if not (2010 <= start <= 2030):
        raise typer.BadParameter(f"start must be between 2010 and 2030, got {start}", param_hint="'--start'")
    if not (2010 <= end <= 2030):
        raise typer.BadParameter(f"end must be between 2010 and 2030, got {end}", param_hint="'--end'")
    if start >= end:
        raise typer.BadParameter(f"start ({start}) must be less than end ({end})")
    _require_positive_sample(sample)
    if max_minutes is not None and max_minutes <= 0:
        raise typer.BadParameter(f"max_minutes must be > 0, got {max_minutes}", param_hint="'--max-minutes'")
    if sleep is not None and sleep < 0:
        raise typer.BadParameter(f"sleep must be >= 0, got {sleep}", param_hint="'--sleep'")
    if top_n < 1:
        raise typer.BadParameter(f"top_n must be >= 1, got {top_n}", param_hint="'--top-n'")
    if wics_date is not None and (len(wics_date) != 8 or not wics_date.isdigit()):
        raise typer.BadParameter(f"wics_date must be 8 digits (YYYYMMDD), got {wics_date!r}", param_hint="'--wics-date'")
    from krff.constants import VALID_OHLCV_BACKENDS as _valid_backends
    if backend not in _valid_backends:
        raise typer.BadParameter(f"backend must be one of {_valid_backends}, got {backend!r}", param_hint="'--backend'")

    from krff.pipeline import run_pipeline

    try:
        run_pipeline(
            market=market,
            start=start,
            end=end,
            stage=stage,
            corp_code=corp_code,
            force=force,
            sample=sample,
            max_minutes=max_minutes,
            sleep=sleep,
            wics_date=wics_date,
            scoped=scoped,
            top_n=top_n,
            backend=backend,
        )
    except Exception as exc:
        typer.echo(f"Pipeline failed: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def analyze(
    parquet: Optional[Path] = typer.Option(None, help="Path to beneish_scores.parquet"),
) -> None:
    """Load beneish_scores.parquet and print a summary."""
    from krff.analysis import run_beneish_screen

    path = parquet or _DEFAULT_PARQUET
    if not path.exists():
        typer.echo(f"Error: {path} not found. Run 'krff run' then 'python 03_Analysis/beneish_screen.py' first.", err=True)
        raise typer.Exit(code=1)

    try:
        df = run_beneish_screen(path)
        typer.echo(df.to_string())
        typer.echo(f"\n{len(df):,} rows · {df['corp_code'].nunique():,} companies · {int(df['flag'].sum()):,} flagged")
    except Exception as exc:
        typer.echo(f"Analyze failed: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def charts(
    parquet: Optional[Path] = typer.Option(None, help="Path to beneish_scores.parquet"),
    output_dir: Optional[Path] = typer.Option(None, help="Directory for beneish_viz.html (default: 03_Analysis/)"),
) -> None:
    """Generate beneish_viz.html from beneish_scores.parquet."""
    from krff.analysis import run_beneish_screen
    from krff.charts import generate_charts

    path = parquet or _DEFAULT_PARQUET
    if not path.exists():
        typer.echo(f"Error: {path} not found. Run 'krff run' then 'krff analyze' first.", err=True)
        raise typer.Exit(code=1)

    try:
        df = run_beneish_screen(path)
        out_dir = output_dir or _ANALYSIS_DIR
        out_path = generate_charts(df, out_dir)
        typer.echo(f"Wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB)")
    except Exception as exc:
        typer.echo(f"Charts failed: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def report(
    corp_code: str = typer.Argument(..., help="DART 8-digit corp code, e.g. 01051092"),
    output_dir: Optional[Path] = typer.Option(None, help="Output dir (default: 03_Analysis/reports/)"),
    skip_claude: bool = typer.Option(False, "--skip-claude", help="Skip Claude API synthesis"),
    force: bool = typer.Option(False, "--force", help="Re-queue even if previously rejected"),
) -> None:
    """Generate a self-contained HTML forensic report for one company."""
    corp_code = corp_code.strip()
    if not corp_code.isdigit() or not (1 <= len(corp_code) <= 8):
        raise typer.BadParameter(f"corp_code must be 1–8 digits, got {corp_code!r}")
    corp_code = corp_code.zfill(8)

    from krff.report import generate_report

    try:
        out_path = (output_dir or (_ANALYSIS_DIR / "reports")) / f"{corp_code}_report.html"
        typer.echo(f"Generating report for corp_code={corp_code}...")
        result = generate_report(corp_code=corp_code, output_path=out_path, skip_claude=skip_claude)
        typer.echo(f"Wrote {result} ({result.stat().st_size / 1024:.0f} KB)")
    except Exception as exc:
        typer.echo(f"Report generation failed: {exc}", err=True)
        raise typer.Exit(code=1)

    # Auto-queue for human review after successful generation
    try:
        from krff.review import queue_add
        # Look up corp_name from corp_ticker_map if available
        corp_name = ""
        cmap = _ANALYSIS_DIR.parent / "01_Data" / "processed" / "corp_ticker_map.parquet"
        if cmap.exists():
            import pandas as pd
            df = pd.read_parquet(cmap, columns=["corp_code", "corp_name"])
            row = df[df["corp_code"].astype(str).str.zfill(8) == corp_code]
            if not row.empty:
                corp_name = str(row.iloc[0]["corp_name"])
        queue_add(corp_code, corp_name, force=force)
        typer.echo(f"Queued {corp_code} ({corp_name or '—'}) for review → run 'krff queue' to see pending")
    except Exception as exc:
        typer.echo(f"Warning: could not queue for review: {exc}", err=True)


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Include DART run summary details"),
) -> None:
    """Show pipeline data status: which artifacts exist, row counts, and sizes."""
    from krff.status import get_status, format_status

    typer.echo(format_status(get_status(), verbose=verbose))


@app.command()
def quality(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-column null breakdown"),
) -> None:
    """Show data quality metrics: null rates, coverage gaps, and stat test output status."""
    from krff.quality import get_quality, format_quality

    try:
        typer.echo(format_quality(get_quality(), verbose=verbose))
    except Exception as exc:
        typer.echo(f"Quality check failed: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def audit(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show all input file mtimes"),
) -> None:
    """Check pipeline data freshness. Flags stale outputs and recommends reruns."""
    from krff.audit import get_audit, format_audit

    result = get_audit()
    typer.echo(format_audit(result, verbose=verbose))
    if result["any_stale"]:
        raise typer.Exit(code=1)


def _run_script(label: str, script: Path) -> None:
    """Run a Python script as a subprocess; raise typer.Exit on failure."""
    import subprocess

    typer.echo(f"\n--- {label} ---")
    result = subprocess.run([sys.executable, str(script)], cwd=str(Path(__file__).parent))
    if result.returncode != 0:
        typer.echo(f"ERROR: {label} exited with code {result.returncode}", err=True)
        raise typer.Exit(code=result.returncode)


@app.command()
def stats(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without executing"),
    run_all: bool = typer.Option(False, "--all", help="Run all eligible tests regardless of staleness"),
    only: Optional[list[str]] = typer.Option(None, "--only", help="Run only these named tests"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show input paths for stale tests"),
) -> None:
    """Run stale statistical tests in topological order."""
    from krff.stats_runner import get_stats_audit, format_stats_audit, STATS_DAG, _DAG_BY_NAME

    root = Path(__file__).parent
    result = get_stats_audit(root)

    typer.echo(format_stats_audit(result, verbose=verbose))

    if dry_run:
        raise typer.Exit(code=0)

    # Build run list
    if run_all:
        to_run = [
            t["name"] for t in result["tests"]
            if not t["status"].startswith("skip")
        ]
    else:
        to_run = list(result["run_order"])

    if only:
        only_set = set(only)
        to_run = [n for n in to_run if n in only_set]

    if not to_run:
        raise typer.Exit(code=0)

    failed: list[str] = []
    for name in to_run:
        node = _DAG_BY_NAME.get(name)
        if node is None:
            typer.echo(f"Unknown test: {name}", err=True)
            failed.append(name)
            continue
        try:
            _run_script(name, root / node.script)
        except typer.Exit:
            failed.append(name)

    if failed:
        typer.echo(f"\n{len(failed)} test(s) failed: {', '.join(failed)}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"\n{len(to_run)} test(s) completed.")


@app.command()
def refresh(
    sample: Optional[int] = typer.Option(None, help="Limit to N companies for each stage (smoke test: --sample 1)"),
    skip_analysis: bool = typer.Option(False, "--skip-analysis", help="Skip Phase 2 runner scripts (cb_bw, timing, network)"),
    backend: str = typer.Option("pykrx", help="OHLCV backend: pykrx (default), fdr, or yfinance"),
) -> None:
    """Re-run the full data pipeline and analysis in sequence.

    Stages (in order):
      1. DART extraction (financials, CB/BW, officer holdings, disclosures)
      2. Transform (beneish_scores.parquet, imputation)
      3. beneish_screen.py
      4. run_cb_bw_timelines.py
      5. run_timing_anomalies.py
      6. run_officer_network.py

    Use --sample 1 to smoke-test all stages with minimal API calls.

    Note: beneish_screen.py and Phase 2 runners are both skipped when --sample is active.
    --sample is for API quota smoke-testing only; production scoring requires full output.
    """
    _require_positive_sample(sample)
    from krff.constants import VALID_OHLCV_BACKENDS as _valid_backends
    if backend not in _valid_backends:
        raise typer.BadParameter(f"backend must be one of {_valid_backends}, got {backend!r}", param_hint="'--backend'")

    root = Path(__file__).parent
    analysis = root / "03_Analysis"

    from krff.pipeline import run_pipeline

    # Stage 1 — DART extraction
    typer.echo("\n--- Stage 1: DART extraction ---")
    try:
        run_pipeline(stage="dart", sample=sample)
    except Exception as exc:
        typer.echo(f"Stage 1 (DART extraction) failed: {exc}", err=True)
        raise typer.Exit(code=1)

    # Stage 2 — Transform
    typer.echo("\n--- Stage 2: Transform ---")
    try:
        run_pipeline(stage="transform", sample=sample)
    except Exception as exc:
        typer.echo(f"Stage 2 (Transform) failed: {exc}", err=True)
        raise typer.Exit(code=1)

    # Stage 3 — Beneish screen (skipped when --sample active: sample runs test API quota only)
    if sample is not None:
        typer.echo(
            "\n--- Stage 3: beneish_screen.py (skipped — --sample active; "
            "scoring requires full transform output) ---"
        )
    else:
        _run_script("Stage 3: beneish_screen.py", analysis / "beneish_screen.py")

    if not skip_analysis:
        # Stage 4 — CB/BW timelines
        _run_script("Stage 4: run_cb_bw_timelines.py", analysis / "run_cb_bw_timelines.py")

        # Stage 5 — Timing anomalies
        _run_script("Stage 5: run_timing_anomalies.py", analysis / "run_timing_anomalies.py")

        # Stage 6 — Officer network
        _run_script("Stage 6: run_officer_network.py", analysis / "run_officer_network.py")

    typer.echo("\nRefresh complete.")


@app.command()
def monitor(
    source: Optional[str] = typer.Option(None, help="Data source to poll (e.g. dart_rss, krx_warning)"),
    interval: int = typer.Option(300, help="Poll interval in seconds"),
    once: bool = typer.Option(False, "--once", help="Poll once and exit"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Poll but do not trigger re-scoring"),
) -> None:
    """Poll external data sources for new events and trigger re-scoring."""
    typer.echo("monitor command is a Phase 3 stub — not yet implemented")
    raise typer.Exit(code=0)


@app.command()
def alerts(
    limit: int = typer.Option(20, help="Maximum alerts to show"),
    corp_code: Optional[str] = typer.Option(None, help="Filter by corp_code"),
    severity: Optional[str] = typer.Option(None, help="Filter by severity (info/low/medium/high/critical)"),
    unresolved: bool = typer.Option(False, "--unresolved", help="Show only unresolved alerts"),
) -> None:
    """Show recent alerts from the monitoring system."""
    typer.echo("alerts command is a Phase 3 stub — not yet implemented")
    raise typer.Exit(code=0)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev mode)"),
) -> None:
    """Start the FastAPI HTTP server (uvicorn)."""
    try:
        import uvicorn
    except ImportError:
        typer.echo("uvicorn not installed. Run: uv sync", err=True)
        raise typer.Exit(code=1)
    uvicorn.run("app:app", host=host, port=port, reload=reload, lifespan="on")


@app.command()
def queue(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter: pending | reviewed"),
) -> None:
    """Show the report review queue."""
    from krff.review import list_queue, get_counts

    counts = get_counts()
    typer.echo(
        f"\nREVIEW QUEUE  "
        f"pending={counts['pending']}  "
        f"visible_free={counts['visible_free']}  "
        f"visible_paid={counts['visible_paid']}  "
        f"hidden={counts['hidden']}"
    )

    rows = list_queue(status)
    if not rows:
        typer.echo("  (empty)")
        return

    typer.echo(
        f"\n{'CODE':<10} {'NAME':<20} {'STATUS':<10} {'VIS':<4} {'TIER':<6} "
        f"{'ASSESSMENT':<17} {'QUEUED':<12} {'NOTES'}"
    )
    typer.echo("─" * 96)
    for r in rows:
        typer.echo(
            f"{r['corp_code']:<10} "
            f"{(r['corp_name'] or '—')[:20]:<20} "
            f"{r['status']:<10} "
            f"{'Y' if r['visible'] else 'N':<4} "
            f"{(r['tier'] or '—'):<6} "
            f"{(r['flag_assessment'] or '—'):<17} "
            f"{r['queued_at'][:10]:<12} "
            f"{r['notes'] or ''}"
        )


@app.command()
def surface(
    corp_code: str = typer.Argument(..., help="DART 8-digit corp code"),
    tier: str = typer.Option(..., "--tier", "-t", help="Tier: free | paid"),
    assessment: Optional[str] = typer.Option(
        None, "--assessment", "-a",
        help="Flag assessment: true_positive | false_positive | false_negative | clean_confirmed",
    ),
    notes: str = typer.Option("", "--notes", "-n", help="Optional reviewer notes"),
) -> None:
    """Make a report visible at the given tier (mark as reviewed + surfaced)."""
    corp_code = corp_code.strip().zfill(8)
    if tier not in ("free", "paid"):
        raise typer.BadParameter(f"tier must be 'free' or 'paid', got {tier!r}", param_hint="'--tier'")

    from krff.review import surface as _surface
    try:
        updated = _surface(corp_code, tier, assessment=assessment, notes=notes)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if not updated:
        typer.echo(
            f"corp_code {corp_code} not found in queue. "
            "Run 'krff report <corp_code>' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    parts = [f"Surfaced {corp_code} → tier={tier}"]
    if assessment:
        parts.append(f"assessment={assessment}")
    typer.echo("  ".join(parts))


@app.command()
def hide(
    corp_code: str = typer.Argument(..., help="DART 8-digit corp code"),
    assessment: Optional[str] = typer.Option(
        None, "--assessment", "-a",
        help="Flag assessment: true_positive | false_positive | false_negative | clean_confirmed",
    ),
    notes: str = typer.Option("", "--notes", "-n", help="Optional reason"),
) -> None:
    """Mark a report as reviewed but hidden (will not be served on any tier)."""
    corp_code = corp_code.strip().zfill(8)

    from krff.review import hide as _hide
    try:
        updated = _hide(corp_code, assessment=assessment, notes=notes)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if not updated:
        typer.echo(
            f"corp_code {corp_code} not found in queue. "
            "Run 'krff report <corp_code>' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    parts = [f"Hidden {corp_code}"]
    if assessment:
        parts.append(f"assessment={assessment}")
    typer.echo("  ".join(parts))


@app.command(name="assess")
def assess_cmd(
    corp_code: str = typer.Argument(..., help="DART 8-digit corp code"),
    assessment: str = typer.Option(
        ..., "--assessment", "-a",
        help="true_positive | false_positive | false_negative | clean_confirmed",
    ),
    notes: str = typer.Option("", "--notes", "-n", help="Optional notes"),
) -> None:
    """Record a methodology verdict without changing visibility."""
    corp_code = corp_code.strip().zfill(8)

    from krff.review import assess as _assess
    try:
        updated = _assess(corp_code, assessment, notes=notes)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if not updated:
        typer.echo(
            f"corp_code {corp_code} not found in queue. "
            "Run 'krff report <corp_code>' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Assessed {corp_code} → {assessment}")


@app.command()
def review(
    corp_code: str = typer.Argument(..., help="DART 8-digit corp code"),
) -> None:
    """Open a generated report in the default browser for review."""
    import webbrowser

    corp_code = corp_code.strip().zfill(8)
    report_path = _ANALYSIS_DIR / "reports" / f"{corp_code}_report.html"
    if not report_path.exists():
        typer.echo(
            f"Report not found: {report_path}\nRun 'krff report {corp_code}' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    url = report_path.as_uri()
    typer.echo(f"Opening {report_path.name} in browser...")
    webbrowser.open(url)


@app.command()
def requeue(
    corp_code: str = typer.Argument(..., help="DART 8-digit corp code"),
) -> None:
    """Reset a reviewed-hidden report back to pending for re-review."""
    corp_code = corp_code.strip().zfill(8)
    from krff.review import queue_add

    queue_add(corp_code, force=True)
    typer.echo(f"Reset {corp_code} to pending. Run 'krff queue' to confirm.")


@app.command(name="seed-queue")
def seed_queue_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be inserted without writing"),
) -> None:
    """Pre-populate the review queue with all companies from corp_ticker_map.parquet."""
    import pandas as pd
    from krff.review import seed_queue

    cmap_path = _ANALYSIS_DIR.parent / "01_Data" / "processed" / "corp_ticker_map.parquet"
    if not cmap_path.exists():
        typer.echo(f"Error: {cmap_path} not found. Run 'krff run' first.", err=True)
        raise typer.Exit(code=1)

    df = pd.read_parquet(cmap_path, columns=["corp_code", "corp_name"])
    df["corp_code"] = df["corp_code"].astype(str).str.zfill(8)
    corps = list(df.drop_duplicates("corp_code").itertuples(index=False, name=None))

    if dry_run:
        typer.echo(f"Would seed {len(corps)} companies into review queue.")
        return

    inserted, skipped = seed_queue(corps)
    typer.echo(f"Seeded queue: {inserted} inserted, {skipped} already present.")


@app.command()
def batch_report(
    top: Optional[int] = typer.Option(None, "--top", help="Process only the top N companies by M-score"),
    workers: int = typer.Option(4, "--workers", help="Parallel worker threads"),
    skip_claude: bool = typer.Option(False, "--skip-claude", help="Skip Claude API synthesis"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would run without generating"),
    force: bool = typer.Option(False, "--force", help="Re-queue even if already queued"),
) -> None:
    """Generate reports for all flagged companies in parallel and auto-queue each."""
    import concurrent.futures

    import pandas as pd

    from krff.constants import BENEISH_THRESHOLD
    from krff.report import generate_report
    from krff.review import list_queue, queue_add

    if not _DEFAULT_PARQUET.exists():
        typer.echo(f"Error: {_DEFAULT_PARQUET} not found. Run 'krff run' first.", err=True)
        raise typer.Exit(code=1)

    # Build flagged corps sorted by m_score desc
    df = pd.read_parquet(_DEFAULT_PARQUET, columns=["corp_code", "m_score"])
    flagged = (
        df[df["m_score"] > BENEISH_THRESHOLD]
        .copy()
        .assign(corp_code=lambda x: x["corp_code"].astype(str).str.zfill(8))
        .sort_values("m_score", ascending=False)
        .drop_duplicates("corp_code")
    )
    if top is not None:
        flagged = flagged.head(top)

    # Build corp_code → corp_name lookup
    corp_name_map: dict[str, str] = {}
    cmap_path = _ANALYSIS_DIR.parent / "01_Data" / "processed" / "corp_ticker_map.parquet"
    if cmap_path.exists():
        cmap = pd.read_parquet(cmap_path, columns=["corp_code", "corp_name"])
        cmap["corp_code"] = cmap["corp_code"].astype(str).str.zfill(8)
        corp_name_map = dict(zip(cmap["corp_code"], cmap["corp_name"]))

    # Skip already-queued if not --force
    targets = list(flagged["corp_code"])
    if not force:
        queued_codes = {r["corp_code"] for r in list_queue()}
        targets = [c for c in targets if c not in queued_codes]

    if dry_run:
        typer.echo(f"Would generate {len(targets)} report(s):")
        for code in targets:
            typer.echo(f"  {code}  {corp_name_map.get(code, '—')}")
        return

    if not targets:
        typer.echo("Nothing to generate (all already queued; use --force to regenerate).")
        return

    total = len(targets)
    typer.echo(f"Generating {total} report(s) with {workers} workers...")

    reports_dir = _ANALYSIS_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    results: list[str] = []
    import threading
    lock = threading.Lock()

    def _generate_one(i_code: tuple[int, str]) -> None:
        i, corp_code = i_code
        corp_name = corp_name_map.get(corp_code, "")
        out_path = reports_dir / f"{corp_code}_report.html"
        try:
            generate_report(corp_code=corp_code, output_path=out_path, skip_claude=skip_claude)
            queue_add(corp_code, corp_name, force=force)
            msg = f"[{i}/{total}] {corp_code} {corp_name} — done"
        except Exception as exc:
            msg = f"[{i}/{total}] {corp_code} {corp_name} — ERROR: {exc}"
        with lock:
            results.append(msg)
            typer.echo(msg)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        executor.map(_generate_one, enumerate(targets, start=1))

    errors = sum(1 for r in results if "ERROR" in r)
    typer.echo(f"\nDone. {total - errors}/{total} succeeded.")
    if errors:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print krff-shell version."""
    typer.echo(f"krff-shell v{_VERSION}")


if __name__ == "__main__":
    app()
