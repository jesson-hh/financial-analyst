"""Tests for the `financial-analyst ingest` CLI subcommand."""
import pandas as pd
import yaml
from pathlib import Path
from typer.testing import CliRunner

from financial_analyst.cli import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_csv(path: Path, codes=("SH600519",), n_days=3):
    rows = []
    for code in codes:
        for d in pd.date_range("2026-05-01", periods=n_days, freq="B"):
            rows.append(
                {
                    "ts_code": code,
                    "trade_date": d.strftime("%Y-%m-%d"),
                    "open": 100,
                    "high": 105,
                    "low": 95,
                    "close": 102,
                    "volume": 1e6,
                    "amount": 1e8,
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)


def _make_cfg(cfg_path: Path, csv_path: Path, target: Path, source_name="test_src"):
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": source_name,
                        "type": "csv",
                        "path": str(csv_path),
                        "code_col": "ts_code",
                        "date_col": "trade_date",
                        "target": str(target),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cli_ingest_dry_run(tmp_path):
    csv_path = tmp_path / "data.csv"
    _make_csv(csv_path)
    cfg = tmp_path / "data_sources.yaml"
    target = tmp_path / "out"
    _make_cfg(cfg, csv_path, target)

    runner = CliRunner()
    result = runner.invoke(
        app, ["ingest", "--source", "test_src", "--config", str(cfg), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "n_codes" in result.stdout
    assert "dry-run" in result.stdout.lower()
    # No writes should have happened
    assert not target.exists()


def test_cli_ingest_writes_target(tmp_path):
    csv_path = tmp_path / "data.csv"
    _make_csv(csv_path)
    cfg = tmp_path / "data_sources.yaml"
    target = tmp_path / "out"
    _make_cfg(cfg, csv_path, target)

    runner = CliRunner()
    result = runner.invoke(
        app, ["ingest", "--source", "test_src", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert (target / "calendars" / "day.txt").exists()
    assert (target / "features" / "sh600519" / "close.day.bin").exists()
    assert "Done" in result.stdout


def test_cli_ingest_unknown_source(tmp_path):
    cfg = tmp_path / "data_sources.yaml"
    cfg.write_text(yaml.safe_dump({"sources": []}), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app, ["ingest", "--source", "missing", "--config", str(cfg)]
    )
    assert result.exit_code != 0
    assert "Unknown source" in result.stdout


def test_cli_ingest_help():
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    assert "--source" in result.stdout
    assert "--dry-run" in result.stdout


def test_cli_ingest_missing_config(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ingest", "--source", "x", "--config", str(tmp_path / "missing.yaml")],
    )
    assert result.exit_code != 0
