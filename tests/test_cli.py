from typer.testing import CliRunner
from financial_analyst.cli import app


def test_version_command():
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_help_lists_commands():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert "report" in result.stdout.lower()
    assert "chat" in result.stdout.lower()
