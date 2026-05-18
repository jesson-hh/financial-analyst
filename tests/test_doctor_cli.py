from typer.testing import CliRunner
from financial_analyst.cli import app


def test_doctor_help():
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0


def test_doctor_runs():
    """Doctor should run and produce diagnostic output even without all deps."""
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "doctor" in result.stdout.lower()
    assert "python" in result.stdout.lower()
    assert "newsdb" in result.stdout.lower()
