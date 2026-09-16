from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pandas as pd
import pytest

from financial_analyst.agent.tier1 import quote_fetcher as quote_module
from financial_analyst.data.pit import PitUnavailableError, guard_frame_asof


def _frame(day: str, value: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": [value], "vol": [100.0]}, index=pd.to_datetime([day]),
    )


def test_historical_rows_without_acquisition_version_fail_closed():
    asof = (date.today() - timedelta(days=2)).isoformat()
    with pytest.raises(PitUnavailableError, match="do not prove a historical version"):
        guard_frame_asof(
            _frame(asof), source="quote", code="SH600000",
            asof=asof, params={"freq": "day"},
        )


def test_explicit_pulled_at_and_immutable_version_are_accepted():
    asof = (date.today() - timedelta(days=2)).isoformat()
    pulled = _frame(asof)
    pulled.attrs["pulled_at"] = f"{asof}T15:00:00"
    _, receipt = guard_frame_asof(
        pulled, source="quote", code="SH600000", asof=asof, params={},
    )
    assert receipt["point_in_time"] is True
    assert receipt["proof"] == "provider_pulled_at"

    versioned = _frame(asof)
    versioned.attrs.update({
        "snapshot_version": "fixture-v1",
        "snapshot_asof": asof,
        "snapshot_immutable": True,
    })
    _, receipt = guard_frame_asof(
        versioned, source="quote", code="SH600000", asof=asof, params={},
    )
    assert receipt["historical_version_proven"] is True
    assert receipt["snapshot_version"] == "fixture-v1"
    assert receipt["snapshot_asof"].startswith(asof)
    assert receipt["snapshot_immutable"] is True

    numeric_trade_date = pd.DataFrame({"trade_date": [int(asof.replace("-", ""))],
                                       "close": [1.0]})
    numeric_trade_date.attrs["pulled_at"] = f"{asof}T15:00:00"
    _, receipt = guard_frame_asof(
        numeric_trade_date, source="quote", code="SH600000", asof=asof, params={},
    )
    assert receipt["data_asof"] == asof


def test_pulled_after_asof_is_rejected():
    asof = (date.today() - timedelta(days=2)).isoformat()
    frame = _frame(asof)
    frame.attrs["pulled_at"] = f"{date.today().isoformat()}T09:00:00"
    with pytest.raises(PitUnavailableError, match="pulled_at"):
        guard_frame_asof(
            frame, source="quote", code="SH600000", asof=asof, params={},
        )


def test_historical_mixed_trade_dates_drop_unparseable_rows():
    asof = (date.today() - timedelta(days=2)).isoformat()
    frame = pd.DataFrame({
        "trade_date": [asof.replace("-", ""), "NOT-A-DATE"],
        "close": [1.0, 999.0],
    })
    frame.attrs["pulled_at"] = f"{asof}T15:00:00+08:00"

    guarded, receipt = guard_frame_asof(
        frame, source="quote", code="SH600000", asof=asof, params={},
    )

    assert guarded["trade_date"].tolist() == [asof.replace("-", "")]
    assert guarded["close"].tolist() == [1.0]
    assert "NOT-A-DATE" not in guarded.to_string()
    assert receipt["content_sha256"]


def test_asof_cutoff_uses_hong_kong_day_boundary():
    asof = "2026-08-29"
    after_hong_kong_midnight = _frame(asof)
    after_hong_kong_midnight.attrs["pulled_at"] = "2026-08-29T20:00:00Z"
    with pytest.raises(PitUnavailableError, match="pulled_at"):
        guard_frame_asof(
            after_hong_kong_midnight,
            source="quote", code="SH600000", asof=asof, params={},
        )

    before_hong_kong_midnight = _frame(asof)
    before_hong_kong_midnight.attrs["pulled_at"] = "2026-08-29T15:59:59Z"
    guarded, receipt = guard_frame_asof(
        before_hong_kong_midnight,
        source="quote", code="SH600000", asof=asof, params={},
    )
    assert len(guarded) == 1
    assert receipt["historical_version_proven"] is True


def test_current_unversioned_read_is_explicitly_not_pit_and_content_bound():
    today = date.today().isoformat()
    _, first = guard_frame_asof(
        _frame(today, 1.0), source="quote", code="SH600000",
        asof=today, params={"freq": "day"},
    )
    _, second = guard_frame_asof(
        _frame(today, 2.0), source="quote", code="SH600000",
        asof=today, params={"freq": "day"},
    )
    assert first["point_in_time"] is False
    assert first["proof"] == "current_unversioned_read"
    assert first["content_sha256"] != second["content_sha256"]
    assert first["cache_key"] != second["cache_key"]


def test_quote_daily_basic_degrades_per_source_and_skips_current_metadata(
    monkeypatch, tmp_path,
):
    asof = (date.today() - timedelta(days=2)).isoformat()

    class Loader:
        def fetch_quote(self, code, start, end):
            idx = pd.date_range(end=pd.Timestamp(end), periods=30, freq="D")
            frame = pd.DataFrame({"close": range(1, 31), "vol": [100.0] * 30}, index=idx)
            frame.attrs["pulled_at"] = f"{end}T15:00:00"
            return frame

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame({"pe_ttm": [10.0], "total_mv": [10000.0]},
                                index=pd.to_datetime([end]))

    class Facts:
        valuation = None
        snapshot_receipts = []
        unavailable_sources = []

    monkeypatch.setattr(quote_module.f10_corpus, "load_facts", lambda *a, **k: Facts())
    monkeypatch.setattr(
        quote_module.stock_basic, "get_basic",
        lambda code: (_ for _ in ()).throw(AssertionError("historical metadata read")),
    )
    output = asyncio.run(quote_module.QuoteFetcher(tmp_path, loader=Loader())._execute({
        "code": "SH600000", "asof_date": asof,
    }))
    assert output["price"] == 30.0
    assert output.get("pe") is None
    assert any(item["source"] == "daily_basic" for item in output["unavailable_sources"])
    assert any(item["source"] == "current_stock_basic_metadata"
               for item in output["unavailable_sources"])


def test_quote_current_path_keeps_valuation_and_identity_while_marking_unversioned(
    monkeypatch, tmp_path,
):
    today = date.today().isoformat()

    class Loader:
        def fetch_quote(self, code, start, end):
            idx = pd.date_range(end=pd.Timestamp(end), periods=30, freq="D")
            return pd.DataFrame({"close": [10.0] * 30, "vol": [100.0] * 30}, index=idx)

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame({
                "pe_ttm": [12.0], "pb": [2.0], "total_mv": [20000.0],
                "circ_mv": [15000.0], "turnover_rate": [1.5],
            }, index=pd.to_datetime([end]))

    monkeypatch.setattr(quote_module.stock_basic, "get_basic", lambda code: {
        "name": "fixture", "industry": "chips", "area": "test",
        "market": "main", "list_date": "20000101",
    })
    output = asyncio.run(quote_module.QuoteFetcher(tmp_path, loader=Loader())._execute({
        "code": "SH600000", "asof_date": today,
    }))
    assert output["pe"] == 12.0 and output["mv_yi"] == 2.0
    assert output["name"] == "fixture" and output["industry"] == "chips"
    assert all(item["point_in_time"] is False for item in output["snapshot_receipts"])
