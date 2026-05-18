import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from typer.testing import CliRunner
from financial_analyst.cli import app


def test_ask_help():
    runner = CliRunner()
    result = runner.invoke(app, ["ask", "--help"])
    assert result.exit_code == 0
    assert "ask" in result.stdout.lower()


def test_ask_basic_query():
    fake_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "answer": "Test reply",
                    "actions_taken": [],
                    "references": [],
                    "needs_full_report": False,
                    "suggested_code": "",
                }),
                "tool_calls": None,
            }
        }]
    }
    with patch("financial_analyst.ask.ask_agent.LLMClient.for_agent") as mock_for:
        fake_client = AsyncMock()
        fake_client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = fake_client
        runner = CliRunner()
        result = runner.invoke(app, ["ask", "hi there"])
    assert result.exit_code == 0
    assert "Test reply" in result.stdout
