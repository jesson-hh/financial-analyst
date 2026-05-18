"""Tests for `financial-analyst models|loaders|agents|collectors list` introspection."""
from pathlib import Path
from typer.testing import CliRunner
from financial_analyst.cli import app


def test_models_list():
    runner = CliRunner()
    result = runner.invoke(app, ["models", "list"])
    assert result.exit_code == 0
    assert "lgb_momentum" in result.stdout


def test_loaders_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "loaders.yaml").write_text(
        "default: qlib_binary\n"
        "loaders:\n"
        "  qlib_binary:\n"
        "    provider_uri: /tmp/qlib\n"
        "  tushare:\n"
        "    cache_enabled: true\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["loaders", "list"])
    assert result.exit_code == 0
    assert "qlib_binary" in result.stdout
    assert "tushare" in result.stdout
    assert "default loader: qlib_binary" in result.stdout


def test_agents_list():
    # Clear the registry first so _ensure_registered() re-populates all 13 built-ins
    # regardless of what other tests (test_agent_registry.py) may have left behind.
    from financial_analyst.agent.registry import SubAgentRegistry
    SubAgentRegistry.clear()

    runner = CliRunner()
    result = runner.invoke(app, ["agents", "list"])
    assert result.exit_code == 0
    # All 13 built-in agents should show
    for name in ["quote-fetcher", "fundamental-analyst", "bull-advocate", "report-writer"]:
        assert name in result.stdout


def test_collectors_list():
    runner = CliRunner()
    result = runner.invoke(app, ["collectors", "list"])
    assert result.exit_code == 0
    assert "BaseNewsCollector" in result.stdout
    assert "BaseF10Collector" in result.stdout


def test_models_list_unknown_action_fails():
    runner = CliRunner()
    result = runner.invoke(app, ["models", "bogus_action"])
    assert result.exit_code != 0
