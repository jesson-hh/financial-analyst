# -*- coding: utf-8 -*-
"""集合竞价适配器、HTTP 路由与 Agent 注册契约（除显式真联调外全离线）。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from guanlan_v2.seats import auction


_CN = ZoneInfo("Asia/Shanghai")


def _tq_report():
    return {
        "ErrorId": "0", "Now": "10.25", "LastClose": "10.00", "Open": "10.20",
        "Max": "10.30", "Min": "9.98", "Volume": "1234", "Amount": "126.5",
        "NowVol": "8", "Inside": "500", "Outside": "734",
        "Buyp": ["10.24", "10.23"], "Buyv": ["100", "80"],
        "Sellp": ["10.25", "10.26"], "Sellv": ["90", "70"],
    }


def _history(_market, _number, _date_int):
    return [
        {"time": "09:15", "price": 10.10, "vol": 0, "buyorsell": 8},
        {"time": "09:20", "price": 10.18, "vol": 0, "buyorsell": 8},
        {"time": "09:24", "price": 10.20, "vol": 0, "buyorsell": 8},
        {"time": "09:25", "price": 10.20, "vol": 321, "buyorsell": 2},
        {"time": "09:30", "price": 10.22, "vol": 10, "buyorsell": 0},
    ]


def test_live_tq_snapshot_is_auction_qualified_only_in_window():
    out = auction.read_tq_snapshot(
        "000630.SZ", now=datetime(2026, 8, 27, 9, 20, tzinfo=_CN),
        fetcher=lambda _code: _tq_report(),
    )
    assert out["ok"] and out["code"] == "SZ000630"
    assert out["mode"] == "live_call_auction" and out["is_call_auction"] is True
    assert out["indicative_price"] == 10.25
    assert out["levels"][0]["bid_vol_lots"] == 100

    after = auction.read_tq_snapshot(
        "000630", now=datetime(2026, 8, 27, 15, 5, tzinfo=_CN),
        fetcher=lambda _code: _tq_report(),
    )
    assert after["ok"] and after["mode"] == "latest_quote_snapshot"
    assert after["is_call_auction"] is False and "不能称为实时竞价" in after["note"]


def test_live_tq_snapshot_falls_back_to_equal_bid_ask_when_now_is_zero():
    report = _tq_report()
    report.update({"Now": "0.00", "Buyp": ["12.77"], "Sellp": ["12.77"],
                   "Buyv": ["42263"], "Sellv": ["42263"], "LastClose": "11.61"})
    out = auction.read_tq_snapshot(
        "600613", now=datetime(2026, 8, 28, 9, 23, tzinfo=_CN),
        fetcher=lambda _code: report,
    )
    assert out["reported_now"] == 0.0 and out["indicative_price"] == 12.77
    assert out["indicative_price_basis"] == "tdx_call_auction_bid1_equals_ask1"
    assert out["common_level1_volume_lots"] == 42263
    assert out["indicative_change_pct"] == 9.9914
    assert "非已成交量" in out["note"]


def test_history_extracts_virtual_path_and_opening_match():
    out = auction.read_history_auction("SZ000630", date(2026, 8, 27), fetcher=_history)
    assert out["ok"] and len(out["points"]) == 4
    assert out["opening_match"] == {"time": "09:25", "price": 10.2, "vol_lots": 321}
    assert out["metrics"]["virtual_observation_count"] == 3
    assert out["metrics"]["start_virtual_price"] == 10.1
    assert out["metrics"]["final_virtual_price"] == 10.2


def test_auto_selects_local_tq_in_window_and_history_afterwards():
    live = auction.read_auction(
        "600519", now=datetime(2026, 8, 27, 9, 18, tzinfo=_CN),
        tq_fetcher=lambda _code: _tq_report(), history_fetcher=_history,
    )
    assert live["source"] == "tdx_local_tq"

    post = auction.read_auction(
        "600519", now=datetime(2026, 8, 27, 18, 0, tzinfo=_CN),
        tq_fetcher=lambda _code: (_ for _ in ()).throw(AssertionError("不应调用 TQ")),
        history_fetcher=_history,
    )
    assert post["source"] == "tdx_public_history_transaction"
    assert post["opening_match"]["vol_lots"] == 321


def test_invalid_mode_and_empty_history_degrade_honestly():
    invalid = auction.read_auction("600519", mode="weird", now=datetime(2026, 8, 27, 18, 0, tzinfo=_CN))
    assert invalid["ok"] is False and invalid["points"] == [] and "mode" in invalid["note"]
    empty = auction.read_history_auction("600519", date(2026, 8, 23), fetcher=lambda *_: [])
    assert empty["ok"] is False and empty["opening_match"] is None


def test_ww_auction_registered_and_formats_history(monkeypatch):
    import guanlan_v2.console.tools as ct

    entry = next(row for row in ct.WW_TOOL_TABLE if row["name"] == "ww_auction")
    assert "ww_auction" in ct.CONSOLE_ALLOWED
    assert entry["confirm"] is False and entry["reachable"] == ["/seats/auction"]
    monkeypatch.setattr(auction, "read_auction", lambda code, date="", mode="auto": {
        "ok": True, "code": "SH600519", "date": "2026-08-27",
        "mode": "postmarket_call_auction", "source": "tdx_public_history_transaction",
        "opening_match": {"time": "09:25", "price": 1304.0, "vol_lots": 164},
        "metrics": {"virtual_observation_count": 2, "start_virtual_price": 1302.8,
                    "final_virtual_price": 1304.0, "virtual_path_change_pct": 0.0921},
        "points": [{"time": "09:25", "price": 1304.0, "vol_lots": 164,
                    "kind": "opening_match"}], "note": "真实逐笔",
    })
    out = ct.auction_impl("600519")
    assert out["ok"] and "09:25 开盘撮合 1304.0 · 164手" in out["content"]
    assert "真实逐笔" in out["content"]


def test_auction_http_route(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.seats.api import build_seats_router

    monkeypatch.setattr(auction, "read_auction", lambda code, date="", mode="auto": {
        "ok": True, "code": "SH600519", "date": "2026-08-27", "mode": "postmarket_call_auction",
        "opening_match": {"time": "09:25", "price": 1304.0, "vol_lots": 164}, "points": [],
    })
    app = FastAPI()
    app.include_router(build_seats_router())
    response = TestClient(app).get("/seats/auction?code=600519&date=2026-08-27")
    assert response.status_code == 200 and response.json()["opening_match"]["vol_lots"] == 164
