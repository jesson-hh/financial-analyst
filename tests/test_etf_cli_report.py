"""Tests for the `fa etf-report` CLI command + run_etf_report_oneshot driver."""
from typer.testing import CliRunner
from financial_analyst import cli


def test_etf_report_invokes_oneshot(monkeypatch, tmp_path):
    calls = []

    async def _fake(code, asof, out_dir, trace=False):
        calls.append(code)
        return {"ok": True}

    # cli.py imports run_etf_report_oneshot from tui at call time:
    #   from financial_analyst.tui import run_etf_report_oneshot
    # Patch both the tui module and the cli import namespace to be safe.
    monkeypatch.setattr("financial_analyst.tui.run_etf_report_oneshot", _fake, raising=False)
    monkeypatch.setattr(cli, "run_etf_report_oneshot", _fake, raising=False)

    r = CliRunner().invoke(cli.app, ["etf-report", "SH510300", "--out-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "SH510300" in calls


def test_etf_report_batch_file(monkeypatch, tmp_path):
    """Batch mode (-f file) runs oneshot for each code in the file."""
    calls = []

    async def _fake(code, asof, out_dir, trace=False):
        calls.append(code)
        return {"ok": True}

    monkeypatch.setattr("financial_analyst.tui.run_etf_report_oneshot", _fake, raising=False)
    monkeypatch.setattr(cli, "run_etf_report_oneshot", _fake, raising=False)

    codes_file = tmp_path / "codes.txt"
    codes_file.write_text("SH510300\nSZ159915\n# comment\n\nSH512880\n", encoding="utf-8")

    r = CliRunner().invoke(cli.app, ["etf-report", "-f", str(codes_file), "--out-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert calls == ["SH510300", "SZ159915", "SH512880"]


def test_etf_report_no_code_exits_nonzero(monkeypatch, tmp_path):
    """No code and no -f file should exit with error."""
    r = CliRunner().invoke(cli.app, ["etf-report", "--out-dir", str(tmp_path)])
    assert r.exit_code != 0
