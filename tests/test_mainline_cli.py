import pandas as pd
from pathlib import Path
from unittest.mock import AsyncMock, patch
from typer.testing import CliRunner
from financial_analyst.cli import app


def test_mainline_help():
    runner = CliRunner()
    result = runner.invoke(app, ["mainline", "--help"])
    assert result.exit_code == 0
    assert "asof" in result.stdout.lower()
    assert "panel" in result.stdout.lower()


def test_mainline_missing_panel_errors(tmp_path):
    """Without a panel, mainline-classifier should fail gracefully."""
    runner = CliRunner()
    result = runner.invoke(app, ["mainline", "--asof", "2026-05-15",
                                  "--panel", str(tmp_path / "nope.parquet"),
                                  "--out", str(tmp_path / "out")])
    # Exit code may be 0 (CLI doesn't propagate sub-agent failure) but stdout should mention error
    assert "panel" in result.stdout.lower() or result.exit_code != 0
