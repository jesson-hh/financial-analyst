from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from financial_analyst import tui


def test_report_asof_is_strict_and_non_future():
    today = date.today().isoformat()
    assert tui._normalize_report_asof(None) == today
    assert tui._normalize_report_asof(today) == today
    assert tui._normalize_report_asof("2020-02-29") == "2020-02-29"
    for bad in ("2026-1-01", "2026-02-30", "2026-01-01T00:00:00", " 2026-01-01 "):
        with pytest.raises(ValueError):
            tui._normalize_report_asof(bad)
    with pytest.raises(ValueError):
        tui._normalize_report_asof((date.today() + timedelta(days=1)).isoformat())


def test_invalid_asof_stops_before_engine_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tui, "_ensure_registered",
        lambda: (_ for _ in ()).throw(AssertionError("engine setup reached")),
    )
    assert asyncio.run(tui.run_report_oneshot(
        "SZ300750", "2026-02-30", tmp_path,
    )) is False


def test_progress_and_timeline_are_isolated_by_asof(monkeypatch, tmp_path):
    today = date.today().isoformat()
    assert tui._report_progress_path(tmp_path, "SZ300750", today).name == "SZ300750_progress.json"
    assert tui._report_progress_path(tmp_path, "SZ300750", "2020-01-02").name == (
        "SZ300750_2020-01-02_progress.json"
    )

    import financial_analyst.data.loaders.stock_timeline as timeline_module
    entries = []

    class Timeline:
        def append_entry(self, code, line):
            entries.append((code, line))

    monkeypatch.setattr(timeline_module, "StockTimelineLoader", Timeline)
    output = SimpleNamespace(
        rating_overall=4, action="hold", target_price=12.3,
        stop_loss=9.8, position_pct=0.02,
    )
    assert tui._append_report_timeline_if_current(
        "SZ300750", "2020-01-02", output, tmp_path / "history.md",
    ) is False
    assert entries == []
    assert tui._append_report_timeline_if_current(
        "SZ300750", today, output, tmp_path / "current.md",
    ) is True
    assert len(entries) == 1 and today in entries[0][1]


def test_current_company_research_report_does_not_append_stock_timeline(
    monkeypatch, tmp_path,
):
    import financial_analyst.data.loaders.stock_timeline as timeline_module

    entries = []

    class Timeline:
        def append_entry(self, code, line):
            entries.append((code, line))

    monkeypatch.setattr(timeline_module, "StockTimelineLoader", Timeline)
    sidecar = tmp_path / "company-report.json"
    sidecar.write_text('{"company_risk_input": null}', encoding="utf-8")
    output = SimpleNamespace(
        output_json_path=str(sidecar),
        rating_overall=4, action="hold", target_price=12.3,
        stop_loss=9.8, position_pct=0.02,
    )

    assert tui._append_report_timeline_if_current(
        "SH600667", date.today().isoformat(), output,
        tmp_path / "company-report.md",
    ) is False
    assert entries == []

    typed_output = SimpleNamespace(
        report_kind="company_research",
        output_json_path=str(tmp_path / "missing-sidecar.json"),
        rating_overall=None, action=None, target_price=None,
        stop_loss=None, position_pct=None,
    )
    assert tui._append_report_timeline_if_current(
        "SH600667", date.today().isoformat(), typed_output,
        tmp_path / "typed-company-report.md",
    ) is False
    assert entries == []
