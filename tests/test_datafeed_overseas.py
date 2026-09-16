# -*- coding: utf-8 -*-
"""datafeed.overseas 单测(全离线,桩 live_client)。海外个股行情看板的 SWR 取数。"""
import json

import guanlan_v2.datafeed.overseas as ov
import guanlan_v2.datafeed.live_client as lc


def _probe_ok(source, code="", date="", limit=20):
    # 批量契约:code 可逗号多码,每码回一行(市场按纯数字=HK / 纯字母=US 粗判)
    items = []
    for c in (x.strip() for x in str(code).split(",")):
        if not c:
            continue
        mkt = "HK" if c.isdigit() else "US"
        items.append({"raw": {"market": mkt, "code": c, "name": c + "名",
                              "price": 100.0, "change_pct": 1.5, "prev_close": 98.5,
                              "high": 101.0, "low": 99.0, "volume": 1000, "amount": 100000}})
    return {"ok": True, "source": "overseas_stock_quote", "status": "ok",
            "items": items, "n": len(items), "note": "", "pulled_at": "2026-07-15T20:00:00"}


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
    assert r0["quote_time_status"] == "unknown"
    assert r0["source_trade_ts"] is None
    assert "不得据此称实时成交价" in r0["quote_time_note"]


def test_probe_one_does_not_upgrade_fetch_time_to_trade_time(monkeypatch):
    def _probe(*args, **kwargs):
        return {
            "ok": True, "status": "ok",
            "items": [{"raw": {
                "code": "NVDA", "price": 225.16,
                "publish_ts": "2026-08-16T16:54:19",
                "visible_ts": "2026-08-16T16:54:19",
                "visible_ts_reason": "fetch_time_or_source_time",
            }}],
        }

    monkeypatch.setattr(lc, "probe", _probe)
    row = ov.probe_one("NVDA")
    assert row is not None
    assert row["quote_time_status"] == "unknown"
    assert row["source_trade_ts"] is None
    assert "抓取或可见时间" in row["quote_time_note"]


def test_fetch_all_batches_into_single_probe(monkeypatch):
    """2026-07-28 提速契约:看板 14 只必须合并为一次 probe(此前逐只 14 子进程 ~25s);
    批量结果缺行(退市/缺价)→ 该行落缺价占位,行序恒=WATCHLIST 序。"""
    calls = []

    def _probe(source, code="", date="", limit=20):
        calls.append(code)
        r = _probe_ok(source, code=code, limit=limit)
        r["items"] = [it for it in r["items"] if it["raw"]["code"] != "00700"]  # 模拟缺行
        return r

    monkeypatch.setattr(lc, "probe", _probe)
    rows = ov._fetch_all()
    assert len(calls) == 1 and "," in calls[0], "必须一次批量 probe"
    assert len(rows) == len(ov.WATCHLIST)
    assert [r["code"] for r in rows] == [w["code"] for w in ov.WATCHLIST]
    hole = next(r for r in rows if r["code"] == "00700")
    assert hole["price"] is None and "缺价" in (hole.get("note") or "")


def test_fetch_all_falls_back_to_percode_when_batch_fails(monkeypatch):
    """批量失败(旧 stocks 版本不认逗号返空)→ 诚实回退逐只,绝不整表缺价。"""
    calls = []

    def _probe(source, code="", date="", limit=20):
        calls.append(code)
        if "," in code:
            return {"ok": True, "status": "ok", "items": [], "n": 0}  # 批量不认→空
        return _probe_ok(source, code=code, limit=limit)

    monkeypatch.setattr(lc, "probe", _probe)
    rows = ov._fetch_all()
    assert len(rows) == len(ov.WATCHLIST)
    assert all(r.get("price") == 100.0 for r in rows)
    assert len(calls) == 1 + len(ov.WATCHLIST)  # 1 次批量尝试 + 逐只回退


def test_disk_cache_survives_restart(monkeypatch, tmp_path):
    """2026-07-28:overseas 是唯一重启即冷面 → 加落盘;新进程内存空时从磁盘回旧值
    (stale 显形+后台刷),不再 warming 空表。"""
    monkeypatch.setattr(lc, "probe", _probe_ok)
    monkeypatch.setattr(ov, "_CACHE_PATH", tmp_path / "overseas.json")
    monkeypatch.setattr(ov, "_MEM_CACHE", {"data": None})
    ov._refresh()
    assert (tmp_path / "overseas.json").exists()

    # 模拟重启:内存清空,读盘回填
    monkeypatch.setattr(ov, "_MEM_CACHE", {"data": None})
    fired = {"n": 0}
    monkeypatch.setattr(ov, "_trigger", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = ov.read_overseas(fresh_within_s=0)  # 强制视为过期
    assert out["warming"] is False and len(out["rows"]) == len(ov.WATCHLIST)
    assert out["stale"] is True and fired["n"] == 1  # 旧值显形 stale + 后台刷已触发


def test_disk_cache_rejects_non_numeric_pulled_ts(monkeypatch, tmp_path):
    """评审回归:合法 JSON 但 pulled_ts 非数字的盘缓存必须被拒收(否则 stale 减法处
    永久抛异常且毒化内存缓存)→ 走 warming 路径,不炸。"""
    p = tmp_path / "overseas.json"
    p.write_text(json.dumps({"pulled_ts": "not-a-number", "rows": []}), encoding="utf-8")
    monkeypatch.setattr(ov, "_CACHE_PATH", p)
    monkeypatch.setattr(ov, "_MEM_CACHE", {"data": None})
    monkeypatch.setattr(ov, "_trigger", lambda *a, **k: True)
    out = ov.read_overseas()
    assert out["warming"] is True and ov._MEM_CACHE["data"] is None


def test_read_overseas_warming_first_then_serves(monkeypatch, tmp_path):
    monkeypatch.setattr(ov, "_CACHE_PATH", tmp_path / "absent.json")  # 无盘缓存
    monkeypatch.setattr(ov, "_MEM_CACHE", {"data": None})
    fired = {"n": 0}
    monkeypatch.setattr(ov, "_trigger", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = ov.read_overseas()
    assert out["warming"] is True and out["rows"] == [] and fired["n"] == 1

    # 有新鲜缓存 → 直接返回不再触发
    ov._MEM_CACHE["data"] = {"pulled_ts": ov._now_ts(), "rows": [{"code": "AAPL"}], "pulled_at": "x"}
    out2 = ov.read_overseas()
    assert out2["warming"] is False and out2["rows"][0]["code"] == "AAPL"
    assert out2["rows"][0]["quote_time_status"] == "unknown"
