"""Tests for the `fa data update-etf` CLI subcommand."""
from typer.testing import CliRunner
from financial_analyst import data_cli


def test_update_etf_calls_pipelines(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("financial_analyst.data.updaters.etf_price.update_etf_daily_batch",
                        lambda *a, **k: calls.append("price") or {"ok": 1, "total": 1, "empty": 0, "failed": 0})
    monkeypatch.setattr("financial_analyst.data.updaters.etf_fund.update_all_fund",
                        lambda *a, **k: calls.append("fund"))
    monkeypatch.setattr("financial_analyst.data.updaters.etf_spot.update_etf_spot",
                        lambda *a, **k: calls.append("spot"))
    # avoid real calendar copy / data-path side effects:
    monkeypatch.setattr(data_cli, "_ensure_etf_calendar", lambda etf_uri: None, raising=False)
    r = CliRunner().invoke(data_cli.data_app, ["update-etf", "--codes", "SH510300"])
    assert r.exit_code == 0, r.output
    assert {"price", "fund", "spot"} <= set(calls)
