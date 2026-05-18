"""Tests for stdin/file input on ask + report."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from typer.testing import CliRunner
from financial_analyst.cli import app


def test_ask_with_file_input(tmp_path):
    q_file = tmp_path / "q.txt"
    q_file.write_text("hello from file", encoding="utf-8")
    fake_response = {
        "choices": [{"message": {"content": json.dumps({
            "answer": "echo from file",
            "actions_taken": [], "references": [],
            "needs_full_report": False, "suggested_code": "",
        }), "tool_calls": None}}]
    }
    with patch("financial_analyst.ask.ask_agent.LLMClient.for_agent") as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = client
        runner = CliRunner()
        result = runner.invoke(app, ["ask", "--file", str(q_file)])
    assert result.exit_code == 0
    assert "echo from file" in result.stdout


def test_ask_no_query_no_file_fails():
    runner = CliRunner()
    # No stdin provided in CliRunner default -> should fail
    result = runner.invoke(app, ["ask"])
    assert result.exit_code != 0
    assert "Error" in result.stdout or "provide" in result.stdout.lower()


def test_report_with_file_batch(tmp_path):
    codes_file = tmp_path / "codes.txt"
    codes_file.write_text("SH600519\n# comment line\nSZ000858\n", encoding="utf-8")
    runner = CliRunner()
    # Test --help to verify flags are present without running reports
    result = runner.invoke(app, ["report", "--help"])
    assert result.exit_code == 0
    assert "--file" in result.stdout or "-f" in result.stdout
    assert "--trace" in result.stdout


def test_report_no_code_no_file_fails():
    runner = CliRunner()
    result = runner.invoke(app, ["report"])
    assert result.exit_code != 0


def test_report_with_file_parses_codes(tmp_path, monkeypatch):
    """Verify -f reads codes correctly. Mock run_report_oneshot to avoid real run."""
    codes_file = tmp_path / "codes.txt"
    codes_file.write_text("SH600519\n# this is a comment\nSZ000858\n", encoding="utf-8")
    calls = []

    async def fake_run(code, asof, out_dir, trace=False):
        calls.append(code)

    monkeypatch.setattr("financial_analyst.tui.run_report_oneshot", fake_run)
    runner = CliRunner()
    result = runner.invoke(app, ["report", "--file", str(codes_file)])
    assert result.exit_code == 0
    assert calls == ["SH600519", "SZ000858"]   # comment skipped
