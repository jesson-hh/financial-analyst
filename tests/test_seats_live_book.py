# -*- coding: utf-8 -*-
"""落子五档盘口/逐笔/报价failover 单测(全离线,桩 live_fn)。"""
from guanlan_v2.seats import live_book as lb


def _fn(payload):
    def _f(source, code="", limit=30):
        return payload
    return _f


def test_read_orderbook_normalizes_five_levels():
    book = {"code": "000630", "price": 10.5, "last_close": 10.0, "open": 10.1,
            "high": 10.8, "low": 9.9,
            **{f"bid{i}": 10.5 - i * 0.01 for i in range(1, 6)},
            **{f"bid_vol{i}": 100 * i for i in range(1, 6)},
            **{f"ask{i}": 10.5 + i * 0.01 for i in range(1, 6)},
            **{f"ask_vol{i}": 200 * i for i in range(1, 6)}}
    out = lb.read_orderbook("SZ000630", live_fn=_fn({"ok": True, "rows": [book], "n": 1, "note": ""}))
    assert out["ok"] and out["code"] == "000630" and out["price"] == 10.5
    assert len(out["levels"]) == 5
    assert out["levels"][0] == {"level": 1, "bid": 10.49, "bid_vol": 100.0, "ask": 10.51, "ask_vol": 200.0}


def test_read_orderbook_partial_levels_skip_empty():
    book = {"code": "000630", "price": 10.5, "bid1": 10.49, "bid_vol1": 100,
            "ask1": 10.51, "ask_vol1": 200, "bid2": 10.48, "bid_vol2": 50,
            "ask2": 10.52, "ask_vol2": 80}   # 仅两档
    out = lb.read_orderbook("000630", live_fn=_fn({"ok": True, "rows": [book], "n": 1, "note": ""}))
    assert out["ok"] and len(out["levels"]) == 2   # 3~5 档整体缺失被跳过,不塞 0 价假档


def test_read_orderbook_unavailable_is_honest():
    out = lb.read_orderbook("000630", live_fn=_fn({"ok": False, "rows": [], "n": 0, "note": "tdx TCP 不可达"}))
    assert out["ok"] is False and out["levels"] == [] and "tdx" in out["note"]


def test_read_ticks_newest_first_and_maps_side_vol():
    # pytdx 升序(最旧在前)→ read_ticks 反转成最新在前(否则层层 [:lim] 丢最新);side/vol 归一。
    rows = [{"time": "14:59:57", "price": 10.5, "vol": 12, "buyorsell": 0},    # buy,最旧
            {"time": "14:59:58", "price": 10.49, "volume": 8, "buyorsell": 1},  # sell
            {"time": "14:59:59", "price": 10.51, "vol": 3, "buyorsell": 2}]     # neutral,最新
    out = lb.read_ticks("000630", limit=30, live_fn=_fn({"ok": True, "rows": rows, "n": 3, "note": ""}))
    assert out["ok"] and out["n"] == 3
    assert [t["side"] for t in out["ticks"]] == ["neutral", "sell", "buy"]      # 最新在前
    assert out["ticks"][0]["time"] == "14:59:59"                               # 最新成交居首
    assert out["ticks"][1]["vol"] == 8.0                                       # volume 兜底映射


def test_read_orderbook_empty_levels_degrades():
    # tdx 返回退化 book(有 code 壳但无任何 bid/ask 档:退市/空报价)→ ok:False+note,不静默空面板
    book = {"code": "000630", "market": None, "price": None}
    out = lb.read_orderbook("000630", live_fn=_fn({"ok": True, "rows": [book], "n": 1, "note": ""}))
    assert out["ok"] is False and out["levels"] == [] and "无挂单档" in out["note"]


def test_read_ticks_slices_to_limit_newest():
    # 满窗口反转后只返最新 lim 笔,n 与返回条数一致(不报满窗口大小)
    rows = [{"time": f"14:0{i}", "price": 10 + i * 0.01, "vol": i, "buyorsell": 0} for i in range(5)]  # 升序
    out = lb.read_ticks("000630", limit=2, live_fn=_fn({"ok": True, "rows": rows, "n": 5, "note": ""}))
    assert out["n"] == 2 and len(out["ticks"]) == 2
    assert [t["time"] for t in out["ticks"]] == ["14:04", "14:03"]   # 最新 2 笔,最新在前


def test_read_ticks_empty_is_honest():
    out = lb.read_ticks("000630", live_fn=_fn({"ok": True, "rows": [], "n": 0, "note": ""}))
    assert out["ok"] is False and out["ticks"] == [] and "无逐笔" in out["note"]


def test_read_quote_failover_computes_change():
    q = {"code": "000630", "price": 10.5, "last_close": 10.0, "open": 10.1,
         "high": 10.8, "low": 9.9, "vol": 123456, "amount": 1.3e6}
    out = lb.read_quote_failover("SZ000630", live_fn=_fn({"ok": True, "rows": [q], "n": 1, "note": ""}))
    assert out["ok"] and out["source"] == "tdx" and out["price"] == 10.5
    assert out["prevClose"] == 10.0 and out["change"] == 0.5 and out["changePercent"] == 5.0
    assert "failover" in out["note"] and "诚实降级" in out["note"]


def test_read_quote_failover_unavailable_is_honest():
    out = lb.read_quote_failover("000630", live_fn=_fn({"ok": False, "rows": [], "n": 0, "note": "tdx down"}))
    assert out["ok"] is False and out["source"] == "tdx"


# ── 路由接线(TestClient,桩 live_book 只验端点归一/降级,不重测取数逻辑)────────────
def _seat_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.seats.api import build_seats_router
    app = FastAPI()
    app.include_router(build_seats_router())
    return TestClient(app)


def test_orderbook_route_returns_levels(monkeypatch):
    monkeypatch.setattr(lb, "read_orderbook", lambda code: {
        "ok": True, "code": "000630", "price": 10.5,
        "levels": [{"level": 1, "bid": 10.49, "bid_vol": 100, "ask": 10.51, "ask_vol": 200}], "note": ""})
    r = _seat_client().get("/seats/orderbook?code=SZ000630")
    assert r.status_code == 200 and r.json()["ok"] and len(r.json()["levels"]) == 1


def test_orderbook_route_empty_code_honest():
    r = _seat_client().get("/seats/orderbook?code=")
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["levels"] == []


def test_ticks_route_returns_ticks(monkeypatch):
    monkeypatch.setattr(lb, "read_ticks", lambda code, limit: {
        "ok": True, "code": "000630",
        "ticks": [{"time": "14:59", "price": 10.5, "vol": 12, "side": "buy"}], "n": 1, "note": ""})
    r = _seat_client().get("/seats/ticks?code=000630&limit=10")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["n"] == 1
