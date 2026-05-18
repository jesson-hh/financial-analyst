import json
from pathlib import Path
from unittest.mock import patch, AsyncMock
import pandas as pd
from typer.testing import CliRunner
from financial_analyst.cli import app


def test_cli_dream_no_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "--since", "30"])
    assert result.exit_code == 0
    assert "no reports" in result.stdout.lower() or "found 0" in result.stdout.lower()


def test_cli_dream_dry_run_no_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rpt = {"code": "SH600519", "rating_overall": -1, "action": "sell",
           "target_price": 1500, "stop_loss": 1700, "position_pct": 0}
    (out_dir / "SH600519_2026-05-01.json").write_text(json.dumps(rpt), encoding="utf-8")

    fake_proposals = {
        "proposals": [{
            "target_agent": "bull-advocate", "topic_slug": "test", "title": "Test",
            "lesson_md": "body", "confidence": "low",
            "supporting_cases": [], "reasoning": "",
        }],
        "summary": "test",
    }
    fake_llm = {"choices": [{"message": {"content": json.dumps(fake_proposals)}}]}

    class FakeLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            return pd.DataFrame({
                "trade_date": pd.date_range("2026-05-02", periods=20, freq="B"),
                "open": [1700]*20, "high": [1750]*20, "low": [1650]*20,
                "close": [1690 - i*5 for i in range(20)],
                "vol": [1e6]*20, "amount": [1e8]*20,
            })

    with patch("financial_analyst.data.loader_factory.get_default_loader", return_value=FakeLoader()):
        with patch("financial_analyst.dream.introspector.LLMClient.for_agent") as mock_llm:
            client = AsyncMock(); client.chat = AsyncMock(return_value=fake_llm)
            mock_llm.return_value = client
            runner = CliRunner()
            result = runner.invoke(app, ["dream", "--since", "365", "--dry-run"])
    assert result.exit_code == 0
    assert "dry-run" in result.stdout.lower()
    assert not (tmp_path / "memories" / "_proposed").exists()


def test_cli_dream_writes_proposals(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    rpt = {"code": "SH600519", "rating_overall": -1, "action": "sell",
           "target_price": 1500, "stop_loss": 1700, "position_pct": 0}
    (out_dir / "SH600519_2026-05-01.json").write_text(json.dumps(rpt), encoding="utf-8")

    fake_proposals = {
        "proposals": [{
            "target_agent": "bear-advocate", "topic_slug": "test-rule", "title": "T",
            "lesson_md": "body", "confidence": "low",
            "supporting_cases": [], "reasoning": "",
        }],
        "summary": "test",
    }
    fake_llm = {"choices": [{"message": {"content": json.dumps(fake_proposals)}}]}

    class FakeLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            return pd.DataFrame({
                "trade_date": pd.date_range("2026-05-02", periods=20, freq="B"),
                "open": [1700]*20, "high": [1750]*20, "low": [1650]*20,
                "close": [1690 - i*5 for i in range(20)],
                "vol": [1e6]*20, "amount": [1e8]*20,
            })

    with patch("financial_analyst.data.loader_factory.get_default_loader", return_value=FakeLoader()):
        with patch("financial_analyst.dream.introspector.LLMClient.for_agent") as mock_llm:
            client = AsyncMock(); client.chat = AsyncMock(return_value=fake_llm)
            mock_llm.return_value = client
            runner = CliRunner()
            result = runner.invoke(app, ["dream", "--since", "365"])
    assert result.exit_code == 0
    proposed = tmp_path / "memories" / "_proposed" / "bear-advocate"
    assert proposed.exists()
    md_files = list(proposed.glob("*.md"))
    assert len(md_files) == 1


def test_cli_dream_help():
    runner = CliRunner()
    result = runner.invoke(app, ["dream", "--help"])
    assert result.exit_code == 0
    assert "since" in result.stdout.lower()
    assert "dry-run" in result.stdout.lower()
