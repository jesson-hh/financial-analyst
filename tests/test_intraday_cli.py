from typer.testing import CliRunner
from financial_analyst.cli import app


def test_intraday_help():
    runner = CliRunner()
    result = runner.invoke(app, ["intraday", "--help"])
    assert result.exit_code == 0
    assert "codes" in result.stdout.lower()
    assert "asof" in result.stdout.lower()
