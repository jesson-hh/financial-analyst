from typer.testing import CliRunner
from financial_analyst.cli import app


def test_version_command():
    from financial_analyst import __version__
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_commands():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert "report" in result.stdout.lower()
    assert "chat" in result.stdout.lower()
