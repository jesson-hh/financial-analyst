import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from financial_analyst.cli import app


def test_news_collect_help():
    runner = CliRunner()
    result = runner.invoke(app, ["news-collect", "--help"])
    assert result.exit_code == 0
    assert "sources" in result.stdout.lower() or "source" in result.stdout.lower()


def test_news_query_help():
    runner = CliRunner()
    result = runner.invoke(app, ["news-query", "--help"])
    assert result.exit_code == 0


def test_news_stats_help():
    runner = CliRunner()
    result = runner.invoke(app, ["news-stats", "--help"])
    assert result.exit_code == 0


def test_news_collect_no_opencli(monkeypatch):
    """When opencli is missing, collect should exit with helpful message."""
    monkeypatch.setattr(
        "financial_analyst.data.collectors.opencli.is_opencli_available",
        lambda: False,
    )
    runner = CliRunner()
    result = runner.invoke(app, ["news-collect", "--sources", "kuaixun"])
    assert result.exit_code != 0
    assert "opencli" in result.stdout.lower()


def test_news_stats_empty_db(tmp_path, monkeypatch):
    """Newly-created DB should have all zeros."""
    monkeypatch.setattr(
        "financial_analyst.data.news_db.DEFAULT_DB_PATH",
        tmp_path / "test.sqlite",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["news-stats"])
    assert result.exit_code == 0
    assert "news" in result.stdout.lower()
    assert "0" in result.stdout
