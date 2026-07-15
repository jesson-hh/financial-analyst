# -*- coding: utf-8 -*-
"""datafeed.overseas 单测(全离线,桩 live_client)。海外个股行情看板的 SWR 取数。"""
import guanlan_v2.datafeed.overseas as ov
import guanlan_v2.datafeed.live_client as lc


def _probe_ok(source, code="", date="", limit=20):
    # 每个 watchlist 代码回一行行情(市场按纯数字=HK / 纯字母=US 粗判)
    mkt = "HK" if code.strip().isdigit() else "US"
    return {"ok": True, "source": "overseas_stock_quote", "status": "ok",
            "items": [{"raw": {"market": mkt, "code": code, "name": code + "名",
                               "price": 100.0, "change_pct": 1.5, "prev_close": 98.5,
                               "high": 101.0, "low": 99.0, "volume": 1000, "amount": 100000}}],
            "n": 1, "note": "", "pulled_at": "2026-07-15T20:00:00"}


def test_watchlist_covers_us_and_hk():
    us = [w for w in ov.WATCHLIST if w["market"] == "US"]
    hk = [w for w in ov.WATCHLIST if w["market"] == "HK"]
    assert len(us) >= 5 and len(hk) >= 5          # 美港各若干只
    assert all({"code", "label", "market"} <= set(w) for w in ov.WATCHLIST)


def test_fetch_all_probes_each_and_shapes_rows(monkeypatch):
    monkeypatch.setattr(lc, "probe", _probe_ok)
    rows = ov._fetch_all()
    assert len(rows) == len(ov.WATCHLIST)          # 每只都出一行
    r0 = rows[0]
    assert {"code", "market", "name", "price", "change_pct", "label"} <= set(r0)
    assert r0["price"] == 100.0 and r0["change_pct"] == 1.5


def test_read_overseas_warming_first_then_serves(monkeypatch):
    monkeypatch.setattr(ov, "_MEM_CACHE", {"data": None})
    fired = {"n": 0}
    monkeypatch.setattr(ov, "_trigger", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = ov.read_overseas()
    assert out["warming"] is True and out["rows"] == [] and fired["n"] == 1

    # 有新鲜缓存 → 直接返回不再触发
    ov._MEM_CACHE["data"] = {"pulled_ts": ov._now_ts(), "rows": [{"code": "AAPL"}], "pulled_at": "x"}
    out2 = ov.read_overseas()
    assert out2["warming"] is False and out2["rows"] == [{"code": "AAPL"}]
