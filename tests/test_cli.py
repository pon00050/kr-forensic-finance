"""Smoke tests for the krff Typer CLI."""

import pathlib

import pytest
from typer.testing import CliRunner

from cli import app

runner = CliRunner()

ROOT      = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "01_Data" / "processed"


def test_cli_help():
    """--help exits 0 and lists all subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "analyze", "charts", "status", "version", "monitor", "alerts", "audit"):
        assert cmd in result.output, f"Expected '{cmd}' in --help output"


def test_cli_version():
    """version prints the package version string."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "kr-forensic-finance" in result.output
    assert "1.5.0" in result.output


def test_cli_analyze_with_data():
    """analyze exits 0 and prints row/company summary when parquet exists."""
    p = PROCESSED / "beneish_scores.parquet"
    if not p.exists():
        pytest.skip("beneish_scores.parquet not found — run the pipeline first")

    result = runner.invoke(app, ["analyze"])
    assert result.exit_code == 0, f"krff analyze failed:\n{result.output}"
    assert "rows" in result.output
    assert "companies" in result.output


def test_cli_report_in_help():
    """'report' appears in krff --help output."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "report" in result.output


def test_cli_report_missing_corp_code():
    """krff report with no argument exits non-zero."""
    result = runner.invoke(app, ["report"])
    assert result.exit_code != 0


# ─── Status command tests ─────────────────────────────────────────────────────


def test_cli_status_no_data(tmp_path, monkeypatch):
    """status exits 0 and shows 0/11 when processed dir is empty."""
    import krff.status as st

    processed = tmp_path / "processed"
    processed.mkdir()
    monkeypatch.setattr(st, "_PROCESSED", processed)
    monkeypatch.setattr(st, "_RUN_SUMMARY", tmp_path / "run_summary.json")

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
    assert "0/11 artifacts present" in result.output


def test_cli_status_with_some_data(tmp_path, monkeypatch):
    """status shows correct counts when some parquets exist."""
    import pandas as pd
    import krff.status as st

    processed = tmp_path / "processed"
    processed.mkdir()
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    df.to_parquet(processed / "company_financials.parquet", index=False)
    df.to_parquet(processed / "beneish_scores.parquet", index=False)
    monkeypatch.setattr(st, "_PROCESSED", processed)
    monkeypatch.setattr(st, "_RUN_SUMMARY", tmp_path / "run_summary.json")

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
    assert "2/11 artifacts present" in result.output
    assert "3" in result.output  # row count


def test_cli_status_verbose_with_run_summary(tmp_path, monkeypatch):
    """--verbose shows DART run summary when run_summary.json exists."""
    import json
    import krff.status as st

    processed = tmp_path / "processed"
    processed.mkdir()
    monkeypatch.setattr(st, "_PROCESSED", processed)

    summary_path = tmp_path / "run_summary.json"
    summary_path.write_text(json.dumps({
        "total_companies": 100,
        "years": [2021, 2022, 2023],
        "completed_at": "2026-03-06T10:00:00",
        "elapsed_minutes": 12.5,
        "full_data": ["00111111"] * 80,
        "partial_data": [{"corp_code": "00222222"}] * 10,
        "no_data": ["00333333"] * 5,
        "errors": [{"corp_code": "00444444"}] * 5,
    }), encoding="utf-8")
    monkeypatch.setattr(st, "_RUN_SUMMARY", summary_path)

    result = runner.invoke(app, ["status", "--verbose"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
    assert "DART Run Summary" in result.output
    assert "100" in result.output  # total_companies


def test_cli_status_verbose_no_run_summary(tmp_path, monkeypatch):
    """--verbose exits 0 even when run_summary.json does not exist."""
    import krff.status as st

    processed = tmp_path / "processed"
    processed.mkdir()
    monkeypatch.setattr(st, "_PROCESSED", processed)
    monkeypatch.setattr(st, "_RUN_SUMMARY", tmp_path / "nonexistent.json")

    result = runner.invoke(app, ["status", "--verbose"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"


# ─── Phase A3: Input validation tests ────────────────────────────────────────


def test_run_invalid_market():
    """krff run --market NASDAQ exits 2 with a message about valid values."""
    result = runner.invoke(app, ["run", "--market", "NASDAQ"])
    assert result.exit_code == 2, f"Expected exit 2:\n{result.output}"
    assert "KOSDAQ or KOSPI" in result.output


def test_run_start_after_end():
    """krff run --start 2023 --end 2020 exits 2 (start >= end)."""
    result = runner.invoke(app, ["run", "--start", "2023", "--end", "2020"])
    assert result.exit_code == 2, f"Expected exit 2:\n{result.output}"


def test_run_invalid_sample():
    """krff run --sample 0 exits 2."""
    result = runner.invoke(app, ["run", "--sample", "0"])
    assert result.exit_code == 2, f"Expected exit 2:\n{result.output}"


def test_report_invalid_corp_code():
    """krff report with non-digit corp_code exits 2 mentioning 'digits'."""
    result = runner.invoke(app, ["report", "abc!xyz"])
    assert result.exit_code == 2, f"Expected exit 2:\n{result.output}"
    assert "digits" in result.output


def test_refresh_invalid_sample():
    """krff refresh --sample -1 exits 2."""
    result = runner.invoke(app, ["refresh", "--sample", "-1"])
    assert result.exit_code == 2, f"Expected exit 2:\n{result.output}"


def test_quality_runs(tmp_path, monkeypatch):
    """krff quality exits 0 when processed dir is empty (no parquets)."""
    import krff.quality as sq

    processed = tmp_path / "processed"
    processed.mkdir()
    monkeypatch.setattr(sq, "_PROCESSED", processed)
    monkeypatch.setattr(sq, "_STAT_OUTPUTS", tmp_path / "stat_outputs")

    result = runner.invoke(app, ["quality"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
    assert "Data Quality Report" in result.output


def test_charts_missing_parquet(tmp_path):
    """krff charts with a missing parquet path exits 1 with a 'not found' message."""
    missing = tmp_path / "nonexistent.parquet"
    result = runner.invoke(app, ["charts", "--parquet", str(missing)])
    assert result.exit_code == 1, f"Expected exit 1:\n{result.output}"
    assert "not found" in result.output


def test_refresh_skips_beneish_when_sample_active(monkeypatch):
    """KI-026: krff refresh --sample N must not invoke beneish_screen.py (Stage 3)."""
    import krff.pipeline as sp

    monkeypatch.setattr(sp, "run_pipeline", lambda **kw: None)

    result = runner.invoke(app, ["refresh", "--sample", "1", "--skip-analysis"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
    assert "skipped" in result.output.lower()
    assert "beneish" in result.output.lower()


def test_cli_report_smoke(tmp_path, monkeypatch):
    """With monkeypatched generate_report and --skip-claude, prints 'Wrote' and exits 0."""
    import krff.report as rpt

    def fake_generate_report(corp_code, output_path=None, skip_claude=False):
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("<html><body>test report</body></html>", encoding="utf-8")
            return output_path
        fallback = tmp_path / f"{corp_code}_report.html"
        fallback.write_text("<html><body>test report</body></html>", encoding="utf-8")
        return fallback

    monkeypatch.setattr(rpt, "generate_report", fake_generate_report)
    result = runner.invoke(
        app, ["report", "01051092", "--skip-claude", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}\n{result.exception}"
    assert "Wrote" in result.output


# ─── Phase 3 stub tests ─────────────────────────────────────────────────────


def test_monitor_stub_exits_zero():
    """krff monitor --once exits 0 with stub message."""
    result = runner.invoke(app, ["monitor", "--once"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
    assert "stub" in result.output.lower() or "not yet implemented" in result.output.lower()


def test_alerts_stub_exits_zero():
    """krff alerts exits 0 with stub message."""
    result = runner.invoke(app, ["alerts"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}"
    assert "stub" in result.output.lower() or "not yet implemented" in result.output.lower()


# ─── I1: --backend option tests ──────────────────────────────────────────────


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_run_backend_help():
    """krff run --help shows --backend option."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--backend" in _strip_ansi(result.output)


def test_run_invalid_backend():
    """krff run --backend invalid exits 2."""
    result = runner.invoke(app, ["run", "--backend", "invalid"])
    assert result.exit_code == 2, f"Expected exit 2:\n{result.output}"


def test_run_valid_backends_accepted(monkeypatch):
    """krff run --backend fdr/yfinance are accepted (pipeline call is mocked)."""
    import krff.pipeline as sp
    monkeypatch.setattr(sp, "run_pipeline", lambda **kw: None)

    for backend in ("fdr", "yfinance", "pykrx"):
        result = runner.invoke(app, ["run", "--stage", "cb_bw", "--backend", backend])
        assert result.exit_code == 0, f"--backend {backend} failed:\n{result.output}"


def test_refresh_backend_help():
    """krff refresh --help shows --backend option."""
    result = runner.invoke(app, ["refresh", "--help"])
    assert result.exit_code == 0
    assert "--backend" in _strip_ansi(result.output)


def test_refresh_invalid_backend():
    """krff refresh --backend bogus exits 2."""
    result = runner.invoke(app, ["refresh", "--backend", "bogus"])
    assert result.exit_code == 2, f"Expected exit 2:\n{result.output}"


def test_audit_help():
    """krff audit --help exits 0 and mentions freshness."""
    result = runner.invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
    assert "freshness" in result.output.lower(), (
        f"Expected 'freshness' in audit --help output:\n{result.output}"
    )


def test_audit_verbose_flag_exists():
    """krff audit --help shows --verbose flag."""
    result = runner.invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
    assert "--verbose" in _strip_ansi(result.output)


# ─── Stats command tests ──────────────────────────────────────────────────────


def test_stats_dry_run():
    """krff stats --dry-run exits 0 and shows status icons without executing scripts."""
    result = runner.invoke(app, ["stats", "--dry-run"])
    assert result.exit_code == 0, f"Expected exit 0:\n{result.output}\n{result.exception}"
    assert any(icon in result.output for icon in ("⚠", "✓", "✗", "⊘")), (
        f"Expected at least one status icon in output:\n{result.output}"
    )


def test_stats_in_help():
    """krff --help output contains 'stats'."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "stats" in result.output, f"Expected 'stats' in --help output:\n{result.output}"
