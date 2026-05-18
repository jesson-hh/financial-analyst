from typer.testing import CliRunner
from financial_analyst.cli import app


def test_brief_help():
    runner = CliRunner()
    result = runner.invoke(app, ["brief", "--help"])
    assert result.exit_code == 0
    assert "asof" in result.stdout.lower()
    assert "universe" in result.stdout.lower()
