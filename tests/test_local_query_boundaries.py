"""Query-only historical readers must not follow a network-loader default."""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from financial_analyst.data import loader_factory, paths
from guanlan_v2.datafeed.local_query import local_query_loader
from guanlan_v2.seats import api as seats_api
from guanlan_v2.watch_alerts import research


@pytest.fixture
def local_history(tmp_path, monkeypatch):
    roots = {"day": tmp_path / "day", "5min": tmp_path / "5min"}
    days = pd.bdate_range(end=date.today() - timedelta(days=1), periods=12)
    minutes = pd.DatetimeIndex([
        stamp for day in days[-3:]
        for stamp in pd.date_range(day + pd.Timedelta(hours=9, minutes=35), periods=48, freq="5min")
    ])
    for freq, timestamps in (("day", days), ("5min", minutes)):
        root = roots[freq]
        (root / "calendars").mkdir(parents=True)
        (root / "features" / "sh600001").mkdir(parents=True)
        formatted = [t.strftime("%Y-%m-%d" if freq == "day" else "%Y-%m-%d %H:%M:%S")
                     for t in timestamps]
        (root / "calendars" / f"{freq}.txt").write_text("\n".join(formatted), encoding="utf-8")
        values = np.arange(len(timestamps), dtype=float) / 100 + (10 if freq == "day" else 100)
        for name, offset in (("close", 0), ("high", 1)):
            raw = np.concatenate(([0.0], values + offset)).astype("<f4")
            (root / "features" / "sh600001" / f"{name}.{freq}.bin").write_bytes(raw.tobytes())

    monkeypatch.setattr(paths, "get_data_paths", lambda: SimpleNamespace(
        qlib_uri={freq: str(root) for freq, root in roots.items()}))
    default_calls = []

    def no_default_loader():
        default_calls.append(True)
        raise AssertionError("query consulted the network-capable default loader")

    monkeypatch.setattr(loader_factory, "get_default_loader", no_default_loader)
    yield SimpleNamespace(roots=roots, days=days, minutes=minutes, default_calls=default_calls)
    assert not default_calls


def _digests(roots):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in roots.values() for p in root.rglob("*") if p.is_file()}


def test_5min_uses_its_own_local_root_without_writes(local_history):
    before = _digests(local_history.roots)
    frame = research._load_5m("SH600001", str(local_history.days[0].date()),
                              str(local_history.days[-1].date()))
    assert len(frame) == 144
    assert frame["close"].iloc[0] == pytest.approx(100.0)
    assert frame.index[1] - frame.index[0] == pd.Timedelta(minutes=5)
    assert _digests(local_history.roots) == before


def test_missing_5min_config_is_not_replaced_with_daily_data(local_history, monkeypatch):
    monkeypatch.setattr(paths, "get_data_paths", lambda: SimpleNamespace(
        qlib_uri=str(local_history.roots["day"])))
    with pytest.raises(ValueError, match="未配置 5min"):
        local_query_loader(freq="5min")
    result = research.research_alert(scope="stock", code="600001",
                                     start="2026-06-01", end="2026-06-03")
    assert result["ok"] is False
    assert result["source"] == "local_qlib_5min"
    assert "未配置 5min" in result["reason"]


def test_stock_watch_research_uses_local_5min(local_history):
    result = research.research_alert(scope="stock", code="600001",
                                     start=str(local_history.days[0].date()),
                                     end=str(local_history.days[-1].date()))
    assert result["ok"] is True and result["bars"] == 144
    assert result["source"] == "local_qlib_5min"
    assert result["suggested_rule"]["enabled"] is False


@pytest.mark.parametrize("start,end,reason", [
    ("20260601", "2026-06-03", "YYYY-MM-DD"),
    ("2026-02-30", "2026-06-03", "YYYY-MM-DD"),
    ("2026-06-03", "2026-06-01", "start 不能晚于"),
    ("2020-01-01", "2026-06-03", "最多 365 天"),
    ("9999-01-01", "9999-01-02", "end 不能晚于今天"),
    ("", "0001-01-01", "太早"),
])
def test_bad_period_rejected_before_any_history_read(monkeypatch, start, end, reason):
    def no_read(*_args, **_kwargs):
        pytest.fail("invalid date period reached data loading")

    monkeypatch.setattr(research, "_load_5m", no_read)
    result = research.research_alert(scope="stock", code="600001", start=start, end=end)
    assert result["ok"] is False and reason in result["reason"]


def test_maximum_period_and_default_are_explicit():
    assert research._research_period("2025-06-03", "2026-06-03") == ("2025-06-03", "2026-06-03")
    assert research._research_period("", "2026-06-03") == ("2025-12-05", "2026-06-03")


def test_ledger_calibration_and_both_performance_queries_are_local(local_history, tmp_path, monkeypatch):
    from guanlan_v2.screen import picks
    from guanlan_v2.strategy.compute import eqw_market

    first = str(local_history.days[0].date())
    log = tmp_path / "decisions.jsonl"
    log.write_text(json.dumps({"kind": "decide", "code": "SH600001", "direction": "买入",
                               "confidence": 85, "asof": first}) + "\n", encoding="utf-8")
    monkeypatch.setattr(seats_api, "_DEC_LOG", log)
    monkeypatch.setattr(seats_api, "_LEDGER_LOG", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(seats_api, "_is_trading_now", lambda: False)
    monkeypatch.setattr(eqw_market, "load_eqw_ret", lambda: None)
    monkeypatch.setattr(picks, "PICKS_PATH", tmp_path / "picks.jsonl")
    for arm in ("data", "rerank"):
        picks.append_pick({"kind": "rerank_ab", "arm": arm, "codes": ["SH600001"],
                           "run_id": "test-local", "ts": first + "T18:00:00", "snapshot": False})
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    client = TestClient(app)
    assert client.post("/seats/ledger", json={"kind": "open", "date": first,
                                             "cash": 10000}).json()["ok"]
    assert client.post("/seats/ledger", json={"kind": "trade", "date": first, "code": "SH600001",
                                             "side": "buy", "price": 10, "qty": 100}).json()["ok"]
    before = _digests(local_history.roots)
    ledger = client.get("/seats/ledger/state").json()
    assert ledger["ok"] and ledger["covered"] == 1 and ledger["equity"] is not None
    calibration = client.get("/seats/calibration?horizon=5").json()
    assert calibration["ok"] and calibration["mature"] == 1
    perf = client.get("/seats/basket_perf", params={"codes": "SH600001", "start": first}).json()
    assert perf["ok"] and perf["matured_n"] == 1
    rerank = client.get("/seats/basket_perf", params={"kind": "rerank_ab"}).json()
    assert rerank["ok"] and rerank["n"] == 1
    assert all(arm["ok"] for arm in rerank["pairs"][0]["arms"].values())

    def missing_history():
        raise AssertionError("live MTM must not require historical data")

    monkeypatch.setattr(seats_api, "local_query_loader", missing_history)
    monkeypatch.setattr(seats_api, "_is_trading_now", lambda: True)
    monkeypatch.setattr(seats_api, "_intraday_quote", lambda _code: 12.0)
    live_ledger = client.get("/seats/ledger/state").json()
    assert live_ledger["mtm_mode"] == "intraday" and live_ledger["equity"] == 10200
    assert _digests(local_history.roots) == before
