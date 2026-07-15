# -*- coding: utf-8 -*-
"""datafeed.market_tape 单测(全离线,桩 live_client)。"""
import json
import types
from datetime import date

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
        "em_zb_pool": [{"raw": {"code": "111", "x": 1}}, {"raw": {"code": "222", "x": 2}}],   # 炸板 2 家
        "em_yzt_pool": [{"raw": {"code": "aaa", "pct": 10.0}},    # 昨涨停池仅作晋级率分母(len)
                        {"raw": {"code": "bbb", "pct": 3.0}}],    # 分子改数今日 zt 池真连板 limit_days>=2
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
    assert data["derived"]["break_ratio"] == 0.5      # 开板率=涨停中曾开板家数/涨停数(300001 break_times=1)
    # 收敛 limit_up_sentiment 权威口径(零新增拉取,zt/zb/yzt 本已拉):
    assert data["derived"]["zb_count"] == 2 and data["derived"]["yzt_count"] == 2
    assert data["derived"]["break_rate"] == 0.5       # 炸板率=zb/(zt+zb)=2/(2+2)
    # 晋级率=今日涨停池真连板(limit_days>=2)家数/昨涨停池家数;此 fixture 两只今日涨停 limit_days=7/2 皆连板=2/2
    assert data["derived"]["promotion_rate"] == 1.0
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


# ── 最高连板/晋级率口径修复(2026-07-15:zt_stat 抓的是累计涨停次数 ct,非连板 lbc)──
def test_derive_max_streak_uses_limit_days_not_cumulative_ct():
    """max_streak 必须取真连板 limit_days(lbc),不是 zt_stat『N天M板』里的累计涨停次数 M(ct)。
    一只票『10天6板』= 统计窗内累计涨停 6 次,但当前只是首板(limit_days=1)——旧正则抓到 6,虚高。"""
    zt = lc.resolve_source("em_zt_pool")
    sources = {zt: {"rows": [
        {"code": "A", "zt_stat": "10天6板", "limit_days": 1, "break_times": 0},   # ct=6 但真连板=1
        {"code": "B", "zt_stat": "4天4板", "limit_days": 4, "break_times": 0},     # 真 4 连板
    ]}}
    d = mt._derive(sources)
    assert d["max_streak"] == 4     # 真连板 lbc,不是 zt_stat 的累计次数 6


def test_derive_promotion_rate_counts_today_consecutive_boards():
    """晋级率分子改数『今日涨停池里 limit_days>=2 的真连板家数』(数据已在快照),
    不再用昨日涨停池的 pct>=9.8 代理(双创未封误算+,ST 封板漏算-)。分母仍 = 昨日涨停家数。"""
    zt = lc.resolve_source("em_zt_pool")
    yzt = lc.resolve_source("em_yzt_pool")
    sources = {
        zt: {"rows": [
            {"code": "A", "zt_stat": "2天2板", "limit_days": 2, "break_times": 0},   # 真连板 → 计
            {"code": "B", "zt_stat": "1天1板", "limit_days": 1, "break_times": 0},   # 首板 → 不计
        ]},
        yzt: {"rows": [
            {"code": "X", "pct": 12.0},   # 双创大涨未封:旧 pct>=9.8 误计+
            {"code": "Y", "pct": 10.0},   # 旧 pct>=9.8 计
            {"code": "Z", "pct": 3.0},    # 分母
        ]},
    }
    d = mt._derive(sources)
    assert d["promotion_rate"] == round(1 / 3, 4)   # 今日 1 家真连板 / 昨日 3 家涨停,非旧口径 2/3


def test_derive_ladder_histogram_by_limit_days():
    """连板梯队直方图:按真连板 limit_days 分桶(首板/2/3/4/5+ 板各几家)。温度是标量、梯队是形状:
    同 60 家涨停,首板堆量(虚胖)vs 高标接力(真亢奋)标量分辨不出。纯展示零新增拉取。"""
    zt = lc.resolve_source("em_zt_pool")
    sources = {zt: {"rows": [
        {"code": "a", "limit_days": 1, "break_times": 0},   # 首板
        {"code": "b", "limit_days": 1, "break_times": 0},   # 首板
        {"code": "c", "limit_days": 2, "break_times": 0},
        {"code": "d", "limit_days": 3, "break_times": 0},
        {"code": "e", "limit_days": 5, "break_times": 0},   # 5+
        {"code": "f", "limit_days": 7, "break_times": 0},   # 5+
        {"code": "g", "break_times": 0},                    # 缺 limit_days → 首板
    ]}}
    d = mt._derive(sources)
    assert d["ladder"] == {"first": 3, "2": 1, "3": 1, "4": 0, "5plus": 2}


def test_derive_empty_pool_is_honest_none_not_fake_zero():
    """涨停池空(周末/盘前/回溯失败)→ 打板派生值诚实 None,绝不显示假 0.0%/空梯队冒充真事实
    (假 0.0 会原样进研报证据包与 UI)。非交易日两池皆空即此路径。"""
    d = mt._derive({})     # 无任何源 = 全空
    assert d["zt_count"] == 0
    assert d["break_ratio"] is None       # 开板率:无涨停无从算,非 0.0
    assert d["break_rate"] is None        # 炸板率:无涨停尝试,非 0.0
    assert d["promotion_rate"] is None    # 晋级率:无昨涨停池,非 0.0
    assert d["ladder"] is None            # 梯队:无池即无形状,非全零
    assert d["max_streak"] is None        # 最高连板:空池未定义,非 0


# ── 北向净额 scope:深股通不可靠(sgt 置空)→ 只算沪股通并诚实标 scope ──────────────
def test_derive_north_scope_hgt_only_when_sgt_unreliable():
    """stocks 已把退化的 sgt 整列置空 → 北向净额只算沪股通,scope 诚实标『沪股通』(不冒充沪+深)。"""
    nb = lc.resolve_source("northbound")
    sources = {nb: {"rows": [{"time": "15:00", "hgt_yi": -9.28, "sgt_yi": None},
                             {"time": "14:59", "hgt_yi": -10.0, "sgt_yi": None}]}}
    d = mt._derive(sources)
    assert d["north_net"] == -9.28
    assert d["north_scope"] == "沪股通"


def test_derive_north_scope_both_when_sgt_present():
    """深股通可靠(sgt 有值)→ 北向净额=沪+深合计,scope 标『沪+深股通』。"""
    nb = lc.resolve_source("northbound")
    sources = {nb: {"rows": [{"time": "15:00", "hgt_yi": -9.28, "sgt_yi": -31.1}]}}
    d = mt._derive(sources)
    assert d["north_net"] == -40.38
    assert d["north_scope"] == "沪+深股通"


# ── 涨停池空回溯定日:今日空(周末/盘前)→ 回溯至上一有数据交易日 ────────────────
def test_backfill_walks_back_to_last_trading_day_when_today_empty(monkeypatch):
    """今日涨停池空 → 往回探(非交易日上游静默返空),首个非空=上一交易日;
    用同一日期重拉四池并打「回溯至 MM-DD」徽章。7-12(周日)→回溯 7-10(周五有数据)。"""
    def _probe(source, code="", date="", limit=20):
        canon = lc.resolve_source(source) or source
        if date == "20260710":                        # 上一交易日(周五)有数据
            fx = {"em_limit_up_pool": [{"raw": {"code": "600000", "limit_days": 3, "break_times": 0}}],
                  "em_zb_pool": [{"raw": {"code": "111"}}],
                  "em_dt_pool": [],
                  "em_yzt_pool": [{"raw": {"code": "222", "pct": 10.0}}]}
            items = fx.get(canon, [])
            return {"ok": True, "source": canon, "status": "ok", "items": items,
                    "n": len(items), "pulled_at": "2026-07-10T15:00:00"}
        return {"ok": True, "source": canon, "status": "ok", "items": [], "n": 0,
                "pulled_at": "2026-07-12T09:00:00"}   # 7-11 周六 / 7-12 周日皆空
    monkeypatch.setattr(lc, "probe", _probe)
    sources = {}                                       # 今日(7-12)空
    board_date, backfilled = mt._backfill_board_pools(sources, today=date(2026, 7, 12))
    assert backfilled is True and board_date == "20260710"
    zt = lc.resolve_source("em_zt_pool")
    assert sources[zt]["rows"][0]["code"] == "600000"
    assert "回溯至 07-10" in sources[zt]["note"]
    # 四池同日对齐 + derive 用回溯数据出真值(非诚实空)
    d = mt._derive(sources)
    assert d["zt_count"] == 1 and d["max_streak"] == 3


def test_backfill_noop_when_today_has_data(monkeypatch):
    """今日涨停池有数据 → 不回溯、不额外触网。"""
    def _boom(*a, **k):
        raise AssertionError("今日有数据不应回溯触网")
    monkeypatch.setattr(lc, "probe", _boom)
    zt = lc.resolve_source("em_zt_pool")
    sources = {zt: {"rows": [{"code": "1", "limit_days": 1}], "n": 1}}
    board_date, backfilled = mt._backfill_board_pools(sources, today=date(2026, 7, 15))
    assert backfilled is False and board_date is None


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
