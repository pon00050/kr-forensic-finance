"""Smoke tests for the krff Typer CLI."""

from typer.testing import CliRunner

from cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "1.5.0" in result.output
