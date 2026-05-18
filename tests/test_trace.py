"""Tests for --trace flag on report command."""
from typer.testing import CliRunner
from financial_analyst.cli import app


def test_report_has_trace_flag():
    runner = CliRunner()
    result = runner.invoke(app, ["report", "--help"])
    assert result.exit_code == 0
    assert "--trace" in result.stdout
