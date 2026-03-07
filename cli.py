"""kr-forensic-finance CLI — entry point for the `krff` command.

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

_VERSION = _pkg_version("kr-forensic-finance")
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

    from src.pipeline import run_pipeline

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
        )
    except Exception as exc:
        typer.echo(f"Pipeline failed: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def analyze(
    parquet: Optional[Path] = typer.Option(None, help="Path to beneish_scores.parquet"),
) -> None:
    """Load beneish_scores.parquet and print a summary."""
    from src.analysis import run_beneish_screen

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
    from src.analysis import run_beneish_screen
    from src.charts import generate_charts

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
) -> None:
    """Generate a self-contained HTML forensic report for one company."""
    corp_code = corp_code.strip()
    if not corp_code.isdigit() or not (1 <= len(corp_code) <= 8):
        raise typer.BadParameter(f"corp_code must be 1–8 digits, got {corp_code!r}")
    corp_code = corp_code.zfill(8)

    from src.report import generate_report

    try:
        out_path = (output_dir or (_ANALYSIS_DIR / "reports")) / f"{corp_code}_report.html"
        typer.echo(f"Generating report for corp_code={corp_code}...")
        result = generate_report(corp_code=corp_code, output_path=out_path, skip_claude=skip_claude)
        typer.echo(f"Wrote {result} ({result.stat().st_size / 1024:.0f} KB)")
    except Exception as exc:
        typer.echo(f"Report generation failed: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Include DART run summary details"),
) -> None:
    """Show pipeline data status: which artifacts exist, row counts, and sizes."""
    from src.status import get_status, format_status

    typer.echo(format_status(get_status(), verbose=verbose))


@app.command()
def quality(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show per-column null breakdown"),
) -> None:
    """Show data quality metrics: null rates, coverage gaps, and stat test output status."""
    from src.quality import get_quality, format_quality

    try:
        typer.echo(format_quality(get_quality(), verbose=verbose))
    except Exception as exc:
        typer.echo(f"Quality check failed: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def refresh(
    sample: Optional[int] = typer.Option(None, help="Limit to N companies for each stage (smoke test: --sample 1)"),
    skip_analysis: bool = typer.Option(False, "--skip-analysis", help="Skip Phase 2 runner scripts (cb_bw, timing, network)"),
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

    import subprocess

    root = Path(__file__).parent
    analysis = root / "03_Analysis"

    def _run_script(label: str, script: Path) -> None:
        typer.echo(f"\n--- {label} ---")
        result = subprocess.run([sys.executable, str(script)], cwd=str(root))
        if result.returncode != 0:
            typer.echo(f"ERROR: {label} exited with code {result.returncode}", err=True)
            raise typer.Exit(code=result.returncode)

    from src.pipeline import run_pipeline

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
    uvicorn.run("app:app", host=host, port=port, reload=reload)


@app.command()
def version() -> None:
    """Print kr-forensic-finance version."""
    typer.echo(f"kr-forensic-finance v{_VERSION}")


if __name__ == "__main__":
    app()
