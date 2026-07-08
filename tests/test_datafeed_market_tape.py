# -*- coding: utf-8 -*-
"""datafeed.market_tape 单测(全离线,桩 live_client)。"""
import json
import types

import pytest

import guanlan_v2.datafeed.market_tape as mt
import guanlan_v2.datafeed.live_client as lc


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    cache = tmp_path / "var" / "live" / "market_tape.json"
    monkeypatch.setattr(mt, "_CACHE_PATH", cache)
    monkeypatch.setattr(mt, "_MEM_CACHE", {"data": None})
    monkeypatch.setattr(mt, "_REFRESH_INFLIGHT", [False])
    yield


def _probe_ok(source, code="", date="", limit=20):
    canon = lc.resolve_source(source) or source
    fixtures = {
        "em_limit_up_pool": [{"raw": {"code": "000656", "zt_stat": "7天7板", "break_times": 0, "limit_days": 7}},
                             {"raw": {"code": "300001", "zt_stat": "2天2板", "break_times": 1, "limit_days": 2}}],
        "ths_hsgt_realtime": [{"raw": {"time": "15:00", "hgt_yi": -9.28, "sgt_yi": -31.1}},
                              {"raw": {"time": "14:59", "hgt_yi": -10.0, "sgt_yi": -36.0}}],
    }
    items = fixtures.get(canon, [{"raw": {"code": "600000", "x": 1}}])
    return {"ok": True, "source": canon, "status": "ok", "items": items,
            "n": len(items), "note": "", "pulled_at": "2026-07-08T10:15:01"}


# ── Task 1: refresh + derive + 原子缓存写 ──────────────────────────────────────
def test_refresh_pulls_all_sources_writes_cache_and_derives(monkeypatch):
    monkeypatch.setattr(lc, "probe", _probe_ok)
    data = mt._refresh(ttl_s=180)
    assert set(data["sources"]) == {lc.resolve_source(s["sid"]) for s in mt._SOURCES}
    zt = lc.resolve_source("em_zt_pool")
    assert data["sources"][zt]["rows"][0]["code"] == "000656"     # native_rows 平铺保真
    assert data["derived"]["zt_count"] == 2
    assert data["derived"]["max_streak"] == 7
    assert data["derived"]["break_ratio"] == 0.5
    assert data["derived"]["north_net"] == -40.38     # 最新一分钟 hgt_yi+sgt_yi(newest-first)
    assert mt._CACHE_PATH.exists()                                 # 原子落盘
    on_disk = json.loads(mt._CACHE_PATH.read_text(encoding="utf-8"))
    assert on_disk["pulled_at"] == data["pulled_at"]


def test_refresh_failed_source_keeps_prev_entry(monkeypatch):
    # 首轮全成功落盘
    monkeypatch.setattr(lc, "probe", _probe_ok)
    mt._refresh(ttl_s=180)
    zt = lc.resolve_source("em_zt_pool")

    # 次轮涨停池失败 → 保留上轮 rows + 标 note(局部陈旧诚实显形);其它源刷到更新时刻
    def _probe_zt_fails(source, code="", date="", limit=20):
        canon = lc.resolve_source(source) or source
        if canon == "em_limit_up_pool":
            return {"ok": True, "source": canon, "status": "error", "items": [], "n": 0,
                    "note": "", "error": "boom"}
        r = _probe_ok(source, code, date, limit)
        r["pulled_at"] = "2026-07-08T11:00:00"     # 新鲜源(比 zt 保留的 10:15:01 新)
        return r
    monkeypatch.setattr(lc, "probe", _probe_zt_fails)
    data = mt._refresh(ttl_s=180)
    assert data["sources"][zt]["rows"][0]["code"] == "000656"      # 旧 rows 保留
    assert "新失败" in data["sources"][zt]["note"]
    assert data["sources"][zt]["pulled_at"] == "2026-07-08T10:15:01"   # 保留旧龄期
    assert data["pulled_at"] == "2026-07-08T10:15:01"   # overall=min(最旧分量),新源不掩盖陈旧(不伪造新鲜)


# ── Task 2: SWR read_tape + 单飞 + warming ────────────────────────────────────
def test_read_warming_when_no_cache_triggers_refresh(monkeypatch):
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape()
    assert out["warming"] is True and out["sources"] == {} and fired["n"] == 1
    assert "预热" in out["note"]


def test_read_fresh_cache_no_refresh(monkeypatch):
    now = mt.datetime.now().isoformat(timespec="seconds")
    mt._MEM_CACHE["data"] = {"pulled_at": now, "ttl_s": 180,
                             "sources": {"em_limit_up_pool": {"pulled_at": now, "rows": [{"code": "1"}], "n": 1}},
                             "derived": {"zt_count": 1}}
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape(fresh_within_s=180)
    assert out["warming"] is False and out["freshness"]["stale"] is False and fired["n"] == 0
    assert out["derived"]["zt_count"] == 1


def test_read_stale_cache_returns_now_and_triggers(monkeypatch):
    old = "2020-01-01T00:00:00"
    mt._MEM_CACHE["data"] = {"pulled_at": old, "ttl_s": 180, "sources": {}, "derived": {}}
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape(fresh_within_s=180)
    assert out["warming"] is False and out["freshness"]["stale"] is True and fired["n"] == 1
    assert out["pulled_at"] == old              # 本次仍返回旧值(诚实龄期)


def test_trigger_refresh_single_flight(monkeypatch):
    monkeypatch.setattr(mt, "_REFRESH_INFLIGHT", [True])   # 已有刷新在跑
    started = {"n": 0}
    monkeypatch.setattr(mt.threading, "Thread",
                        lambda *a, **k: types.SimpleNamespace(start=lambda: started.__setitem__("n", started["n"] + 1)))
    assert mt._trigger_refresh() is False and started["n"] == 0


def test_trigger_refresh_resets_flag_when_thread_start_fails(monkeypatch):
    """Thread.start() 抛(线程耗尽)→ 立即复位 in-flight 旗,不永久冻结禁刷(评审 minor)。"""
    monkeypatch.setattr(mt, "_REFRESH_INFLIGHT", [False])

    class _BoomThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(mt.threading, "Thread", _BoomThread)
    assert mt._trigger_refresh() is False
    assert mt._REFRESH_INFLIGHT[0] is False        # 旗已复位,下次仍可刷
