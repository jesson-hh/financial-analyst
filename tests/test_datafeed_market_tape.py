# -*- coding: utf-8 -*-
"""datafeed.market_tape 单测(全离线,桩 live_client)。"""
import json
import types
from datetime import date, datetime, timedelta

import pytest

import guanlan_v2.datafeed.market_tape as mt
import guanlan_v2.datafeed.live_client as lc


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    cache = tmp_path / "var" / "live" / "market_tape.json"
    monkeypatch.setattr(mt, "_CACHE_PATH", cache)
    monkeypatch.setattr(mt, "_MEM_CACHE", {"data": None})
    monkeypatch.setattr(mt, "_REFRESH_INFLIGHT", [False])
    # market_tape 已改走 probe_many 批量通道;测试里把它动态路由回(可能被各测试
    # monkeypatch 的)lc.probe,既有逐源桩零改动,且绝不落到真子进程。
    monkeypatch.setattr(lc, "probe_many",
                        lambda reqs, timeout=None: [
                            lc.probe(str(r.get("source") or ""), code=str(r.get("code") or ""),
                                     date=str(r.get("date") or ""), limit=int(r.get("limit") or 20))
                            for r in (reqs or [])])
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


def _core_ids():
    return [lc.resolve_source(alias) or alias for alias in mt._CORE_SOURCE_ALIASES]


def _core_snapshot(pulled_at, content_date, *, status="ok", last_attempt_status=None):
    """Build the complete four-leg contract used by freshness/health tests."""
    return {
        sid: {
            "status": status,
            "n": 0,
            "pulled_at": pulled_at,
            "content_date": content_date,
            "last_attempt_status": last_attempt_status or status,
            "note": "",
            "rows": [],
        }
        for sid in _core_ids()
    }


def test_refresh_batches_all_sources_into_one_probe_many(monkeypatch):
    """2026-07-28 提速契约:_refresh 的 10 源必须合并为一次 probe_many(一个子进程),
    不得退回逐源逐子进程(那是 10-26s 冷刷的旧世界)。"""
    calls = []

    def _many(reqs, timeout=None):
        calls.append(list(reqs))
        return [_probe_ok(str(r.get("source")), date=str(r.get("date") or ""),
                          limit=int(r.get("limit") or 20)) for r in reqs]

    monkeypatch.setattr(lc, "probe_many", _many)
    mt._refresh(ttl_s=180)
    assert len(calls) == 1, "10 源必须一次批量"
    assert len(calls[0]) == len(mt._SOURCES)
    assert calls[0][0]["limit"] == 300  # kw 逐条透传


def test_refresh_mechanical_failure_keeps_previous_round(monkeypatch):
    """评审既有缺陷回归(2026-07-28 修):probe 机械失败信封是 ok:True/status:""——
    成功闸只认 status=="ok",机械失败必须走"保留上轮标(旧)"分支,绝不被当成功空拉
    覆盖真数据、盖新鲜 pulled_at。"""
    monkeypatch.setattr(lc, "probe", _probe_ok)
    mt._refresh(ttl_s=180)                     # 第一轮:真数据落缓存

    def _mech(source, code="", date="", limit=20):
        return {"ok": True, "source": lc.resolve_source(source) or source, "status": "",
                "items": [], "n": 0, "note": "probe 超时(90s),外源可能限流/阻塞", "error": ""}

    monkeypatch.setattr(lc, "probe", _mech)
    data = mt._refresh(ttl_s=180)              # 第二轮:全源机械失败
    zt = lc.resolve_source("em_zt_pool")
    assert data["sources"][zt]["rows"], "机械失败必须保留上轮真数据"
    assert "(旧)" in data["sources"][zt]["note"]


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
    # 2024-05-13 后交易所不再披露北向盘中买卖/总成交额，legacy THS 分腿值
    # 即使长得像真数也必须抑制，不能以沪股通伪精确值补深股通缺口。
    north = lc.resolve_source("northbound")
    assert data["derived"]["north_net"] is None
    assert data["sources"][north]["disclosure_status"] == "official_intraday_net_unavailable"
    assert all(row["hgt_yi"] is None and row["sgt_yi"] is None
               for row in data["sources"][north]["rows"])
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


def test_display_sources_use_honest_fallbacks_when_primaries_fail(monkeypatch):
    """行业主源失败时用资金流行业表按涨幅重排；东财热榜失败时复用已拉 THS 热榜。
    两者都有当前可用 rows，但 last_attempt_status=fallback_ok，健康层仍会标 degraded。"""
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": "20260708", "phase": "closed", "active": False, "closed": True,
    })
    industry = lc.resolve_source("industry_rank") or "industry_rank"
    hot = lc.resolve_source("em_hot_rank") or "em_hot_rank"
    ths_hot = lc.resolve_source("ths_hot_list") or "ths_hot_list"
    sector_flow = lc.resolve_source("eastmoney_sector_fund_flow") or "eastmoney_sector_fund_flow"

    def _probe(source, code="", date="", limit=20):
        canon = lc.resolve_source(source) or source
        if canon in (industry, hot):
            return {"ok": True, "source": canon, "status": "stale", "items": [],
                    "n": 0, "note": "primary down", "pulled_at": None}
        if canon == sector_flow:
            assert code == "industry"
            return {"ok": True, "source": canon, "status": "ok", "items": [
                {"raw": {"name": "低涨幅", "change_pct": 1.0}},
                {"raw": {"name": "高涨幅", "change_pct": 5.0}},
            ], "n": 2, "note": "", "pulled_at": "2026-07-08T15:01:00"}
        if canon == ths_hot:
            return {"ok": True, "source": canon, "status": "ok",
                    "items": [{"raw": {"name": "同花顺热股"}}], "n": 1,
                    "note": "", "pulled_at": "2026-07-08T15:01:00"}
        return _probe_ok(source, code, date, limit)

    monkeypatch.setattr(lc, "probe", _probe)
    data = mt._refresh(ttl_s=180)
    industry_item = data["sources"][industry]
    hot_item = data["sources"][hot]
    assert industry_item["fallback_source"] == "eastmoney_sector_fund_flow"
    assert [row["name"] for row in industry_item["rows"]] == ["高涨幅", "低涨幅"]
    assert industry_item["rows"][0]["pct"] == 5.0
    assert industry_item["last_attempt_status"] == "fallback_ok"
    assert hot_item["fallback_source"] == "ths_hot_list"
    assert hot_item["rows"] == data["sources"][ths_hot]["rows"]
    assert hot_item["last_attempt_status"] == "fallback_ok"


def test_refresh_semantic_empty_clears_previous_rows(monkeypatch):
    """合法 empty 是本轮成功的零行事实，必须清掉旧行，不能伪装成源失败后保留。"""
    session = "20260708"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "active", "active": True, "closed": False,
    })
    monkeypatch.setattr(lc, "probe", _probe_ok)
    first = mt._refresh(ttl_s=180)
    dt = lc.resolve_source("em_dt_pool")
    assert first["sources"][dt]["rows"]                 # 首轮确有旧行可被清除

    def _dt_empty(source, code="", date="", limit=20):
        canon = lc.resolve_source(source) or source
        if canon == dt:
            return {"ok": True, "source": canon, "status": "empty", "items": [],
                    "n": 0, "note": "正常空数据", "pulled_at": "2026-07-08T11:00:00"}
        r = _probe_ok(source, code, date, limit)
        r["pulled_at"] = "2026-07-08T11:00:00"
        return r

    monkeypatch.setattr(lc, "probe", _dt_empty)
    data = mt._refresh(ttl_s=180)
    item = data["sources"][dt]
    assert item["status"] == "empty" and item["rows"] == [] and item["n"] == 0
    assert item["last_attempt_status"] == "empty" and item["consecutive_failures"] == 0
    assert "新失败" not in item["note"]
    assert data["derived"]["dt_count"] == 0
    assert data["core_refresh_complete"] is True and data["core_session_aligned"] is True


def test_cross_session_core_invalidates_closed_snapshot_and_degrades_derived(monkeypatch):
    """任一核心腿保留上个交易日时，闭市豁免无效且九个板池派生值不得跨日混算。"""
    clock = {"session": "20260828"}
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": clock["session"], "phase": "closed",
        "active": False, "closed": True,
    })
    monkeypatch.setattr(lc, "probe", _probe_ok)
    mt._refresh(ttl_s=180)                              # 先落 08-28 四腿同日快照

    failed = lc.resolve_source("em_zb_pool")
    clock["session"] = "20260829"

    def _one_core_fails(source, code="", date="", limit=20):
        canon = lc.resolve_source(source) or source
        if canon == failed:
            return {"ok": True, "source": canon, "status": "network_error",
                    "items": [], "n": 0, "note": "网络错误", "error": "boom"}
        r = _probe_ok(source, code, date, limit)
        r["pulled_at"] = "2026-08-29T15:01:00"
        return r

    monkeypatch.setattr(lc, "probe", _one_core_fails)
    data = mt._refresh(ttl_s=180)
    fr = mt._freshness(data, fresh_within_s=180)

    assert data["core_session_aligned"] is False
    assert data["derived"]["core_session_aligned"] is False
    assert data["derived"]["degraded_reason"] == "core_session_mixed_or_unknown"
    for key in ("zt_count", "zb_count", "dt_count", "yzt_count", "max_streak",
                "break_ratio", "break_rate", "promotion_rate", "ladder"):
        assert data["derived"][key] is None, key
    assert fr["closed_snapshot_valid"] is False and fr["stale"] is True
    assert failed in fr["mismatched_core_sources"]


def test_weekend_last_session_snapshot_is_valid_even_when_retrieval_is_old(monkeypatch):
    """周末无新交易日；四腿均属最近周五时，不能按自然分钟龄期误报整体停摆。"""
    friday = "20260828"
    old = "2026-08-28T15:00:00"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": friday, "phase": "closed", "active": False, "closed": True,
    })
    data = {
        "pulled_at": old, "core_pulled_at": old, "refresh_attempted_at": old,
        "core_sources": _core_ids(), "core_session_aligned": True,
        "board_date": friday, "sources": _core_snapshot(old, friday), "derived": {},
    }
    fr = mt._freshness(data, fresh_within_s=180)
    assert fr["closed_snapshot_valid"] is True
    assert fr["mismatched_core_sources"] == [] and fr["stale"] is False


def test_closed_market_uses_closed_ttl_for_quality_but_keeps_fallback_warning(monkeypatch):
    """闭市后 3 分钟阈值不再把所有成功源染黄；30 分钟刷新窗内只保留真实 fallback 告警。"""
    session = "20260828"
    recent = (datetime.now() - timedelta(minutes=6)).isoformat(timespec="seconds")
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "closed", "active": False, "closed": True,
    })
    sources = _core_snapshot(recent, session)
    sources["healthy_optional"] = {
        "status": "ok", "pulled_at": recent, "last_attempt_status": "ok", "rows": [],
    }
    sources["fallback_optional"] = {
        "status": "ok", "pulled_at": recent, "last_attempt_status": "fallback_ok",
        "fallback_source": "alternate", "rows": [],
    }
    data = {"pulled_at": recent, "core_pulled_at": recent,
            "refresh_attempted_at": recent, "board_date": session,
            "core_sources": _core_ids(), "sources": sources, "derived": {}}
    fr = mt._freshness(data, fresh_within_s=180)
    assert fr["stale"] is False and fr["closed_snapshot_valid"] is True
    assert fr["degraded_sources"] == ["fallback_optional"]


def test_completed_close_does_not_age_out_session_stable_sources(monkeypatch):
    """收盘定格源不应在 30 分钟后被误报为 8 个上游同时失败。"""
    session = "20260828"
    closed_pull = "2026-08-28T15:01:00"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "closed", "active": False, "closed": True,
    })
    sources = _core_snapshot(closed_pull, session)
    for alias in ("eastmoney_lhb", "industry_rank", "ths_hot_reason"):
        sid = lc.resolve_source(alias) or alias
        sources[sid] = {
            "status": "ok", "n": 1, "pulled_at": closed_pull,
            "last_attempt_at": closed_pull, "last_attempt_status": "ok",
            "market_session": session, "content_date": session,
            "rows": [{"name": sid}], "note": "",
        }
    north = lc.resolve_source("northbound") or "northbound"
    sources[north] = {
        "status": "ok", "n": 1, "pulled_at": closed_pull,
        "last_attempt_at": closed_pull, "last_attempt_status": "ok",
        "market_session": session, "content_date": None,
        "rows": [{"hgt_yi": 1.0, "sgt_yi": 2.0}], "note": "",
    }
    data = {
        "pulled_at": closed_pull, "core_pulled_at": closed_pull,
        "refresh_attempted_at": closed_pull, "expected_session": session,
        "board_date": session, "core_sources": _core_ids(),
        "sources": sources, "derived": {},
    }

    fr = mt._freshness(data, fresh_within_s=180)

    assert fr["closed_snapshot_valid"] is True and fr["stale"] is False
    assert fr["stale_core_sources"] == []
    assert fr["degraded_sources"] == [] and fr["degraded_details"] == []


def test_dynamic_hot_lists_remain_yellow_after_closed_cache_window(monkeypatch):
    """盘后仍变化的人气榜只标缓存旧，不得伪称网络失败或保留上轮。"""
    session = "20260828"
    closed_pull = "2026-08-28T15:01:00"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "closed", "active": False, "closed": True,
    })
    sources = _core_snapshot(closed_pull, session)
    dynamic = []
    for alias in ("em_hot_rank", "ths_hot_list"):
        sid = lc.resolve_source(alias) or alias
        dynamic.append(sid)
        sources[sid] = {
            "status": "ok", "n": 50, "pulled_at": closed_pull,
            "last_attempt_at": closed_pull, "last_attempt_status": "ok",
            "market_session": session, "rows": [{"name": "热股"}], "note": "",
        }
    data = {
        "pulled_at": closed_pull, "core_pulled_at": closed_pull,
        "refresh_attempted_at": closed_pull, "expected_session": session,
        "board_date": session, "core_sources": _core_ids(),
        "sources": sources, "derived": {},
    }

    fr = mt._freshness(data, fresh_within_s=180)
    details = {item["source"]: item for item in fr["degraded_details"]}

    assert fr["stale"] is False and fr["degraded_sources"] == sorted(dynamic)
    assert all(item["reason_codes"] == ["dynamic_cache_expired"] for item in details.values())
    assert all("失败" not in "".join(item["reasons"]) for item in details.values())


def test_northbound_one_leg_is_coverage_degradation_not_source_failure(monkeypatch):
    """HTTP/解析成功但仅沪股通时，源可用且覆盖不全，黄色原因必须精确。"""
    session = "20260828"
    closed_pull = "2026-08-28T15:01:00"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "closed", "active": False, "closed": True,
    })
    sources = _core_snapshot(closed_pull, session)
    north = lc.resolve_source("northbound") or "northbound"
    sources[north] = {
        "status": "ok", "n": 2, "pulled_at": closed_pull,
        "last_attempt_at": closed_pull, "last_attempt_status": "ok",
        "market_session": session,
        "rows": [{"hgt_yi": -9.28, "sgt_yi": None},
                 {"hgt_yi": -10.03, "sgt_yi": None}], "note": "",
    }
    data = {
        "pulled_at": closed_pull, "core_pulled_at": closed_pull,
        "refresh_attempted_at": closed_pull, "expected_session": session,
        "board_date": session, "core_sources": _core_ids(),
        "sources": sources, "derived": {},
    }

    fr = mt._freshness(data, fresh_within_s=180)

    assert fr["degraded_sources"] == [north]
    assert fr["degraded_details"][0]["reason_codes"] == ["partial_market_coverage"]
    assert "深股通" in fr["degraded_details"][0]["reasons"][0]
    assert fr["degraded_details"][0]["last_attempt_status"] == "ok"


def test_northbound_post_disclosure_change_is_explicitly_unavailable(monkeypatch):
    """传输 200 不等于净流入事实可用；两腿均须抑制并给出精确政策原因。"""
    session = "20260828"
    pulled = "2026-08-28T15:01:00"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "closed", "active": False, "closed": True,
    })
    rows, meta = mt._apply_northbound_disclosure_contract(
        [{"time": "15:00", "hgt_yi": -9.28, "sgt_yi": 12.34}], session,
    )
    assert rows[0]["hgt_yi"] is None and rows[0]["sgt_yi"] is None
    assert meta["disclosure_status"] == "official_intraday_net_unavailable"

    north = lc.resolve_source("northbound") or "northbound"
    sources = _core_snapshot(pulled, session)
    sources[north] = {
        "status": "ok", "n": 1, "pulled_at": pulled,
        "last_attempt_at": pulled, "last_attempt_status": "ok",
        "market_session": session, "rows": rows, "note": "", **meta,
    }
    data = {
        "pulled_at": pulled, "core_pulled_at": pulled,
        "refresh_attempted_at": pulled, "expected_session": session,
        "board_date": session, "core_sources": _core_ids(),
        "sources": sources, "derived": {},
    }
    fr = mt._freshness(data, fresh_within_s=180)
    detail = next(item for item in fr["degraded_details"] if item["source"] == north)
    assert detail["reason_codes"] == ["official_intraday_metric_unavailable"]
    assert "沪股通和深股通" in detail["reasons"][0]


def test_northbound_pre_change_rows_remain_available():
    rows = [{"time": "15:00", "hgt_yi": 1.0, "sgt_yi": 2.0}]
    safe, meta = mt._apply_northbound_disclosure_contract(rows, "20240510")
    assert safe == rows and meta == {}


def test_preclose_snapshot_cannot_masquerade_as_completed_close(monkeypatch):
    """同交易日 14:59 的核心池不是收盘定格，闭市后必须 stale。"""
    session = "20260828"
    preclose_pull = "2026-08-28T14:59:00"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "closed", "active": False, "closed": True,
    })
    data = {
        "pulled_at": preclose_pull, "core_pulled_at": preclose_pull,
        "refresh_attempted_at": preclose_pull, "expected_session": session,
        "board_date": session, "core_sources": _core_ids(),
        "sources": _core_snapshot(preclose_pull, session), "derived": {},
    }

    fr = mt._freshness(data, fresh_within_s=180)

    assert fr["closed_snapshot_valid"] is False and fr["stale"] is True
    assert fr["degraded_sources"] == sorted(_core_ids())
    assert all("cache_expired" in item["reason_codes"] for item in fr["degraded_details"])


def test_trading_day_preopen_expects_previous_completed_session(monkeypatch):
    """交易日 09:15 前沿用上一完成交易日，不能在午夜把周五收盘快照提前作废。"""
    class _PreopenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 8, 45, 0)

    def _latest(day=None):
        return "20260831" if day >= date(2026, 8, 31) else "20260828"

    monkeypatch.setattr(mt, "datetime", _PreopenDateTime)
    monkeypatch.setattr(mt, "_latest_session_yyyymmdd", _latest)
    friday = "20260828"
    old = "2026-08-28T15:00:00"
    clock = mt._market_clock()
    assert clock["calendar_latest_session"] == "20260831"
    assert clock["latest_session"] == friday and clock["phase"] == "preopen"

    data = {
        "pulled_at": old, "core_pulled_at": old, "refresh_attempted_at": old,
        "core_sources": _core_ids(), "core_session_aligned": True,
        "board_date": friday, "sources": _core_snapshot(old, friday), "derived": {},
    }
    fr = mt._freshness(data, fresh_within_s=180)
    assert fr["latest_session"] == friday and fr["closed_snapshot_valid"] is True
    assert fr["stale"] is False


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


def test_backfill_rest_semantic_empty_has_session_and_attempt_metadata(monkeypatch):
    """命中回溯日后，其余三池即使合法空也必须同日对齐并写完整尝试元数据。"""
    def _probe(source, code="", date="", limit=20):
        canon = lc.resolve_source(source) or source
        if date == "20260710" and canon == lc.resolve_source("em_zt_pool"):
            return {"ok": True, "source": canon, "status": "ok",
                    "items": [{"raw": {"code": "600000", "limit_days": 1}}],
                    "n": 1, "pulled_at": "2026-07-10T15:00:00"}
        return {"ok": True, "source": canon, "status": "empty", "items": [],
                "n": 0, "note": "正常空数据", "pulled_at": "2026-07-10T15:00:00"}

    monkeypatch.setattr(lc, "probe", _probe)
    sources = {}
    board_date, backfilled = mt._backfill_board_pools(sources, today=date(2026, 7, 12))
    assert board_date == "20260710" and backfilled is True
    for alias in mt._POOL_ALIASES[1:]:
        item = sources[lc.resolve_source(alias) or alias]
        assert item["status"] == "empty" and item["rows"] == []
        assert item["content_date"] == "20260710"
        assert item["last_attempt_status"] == "empty" and item["last_attempt_at"]
        assert item["last_attempt_error"] == "" and item["consecutive_failures"] == 0


# ── Task 2: SWR read_tape + 单飞 + warming ────────────────────────────────────
def test_read_warming_when_no_cache_triggers_refresh(monkeypatch):
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape()
    assert out["warming"] is True and out["sources"] == {} and fired["n"] == 1
    assert "预热" in out["note"]


def test_read_warming_query_only_does_not_trigger_refresh(monkeypatch):
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape(allow_refresh=False)
    assert out["warming"] is True and out["sources"] == {} and fired["n"] == 0
    assert "未启动刷新" in out["note"]


def test_read_fresh_cache_no_refresh(monkeypatch):
    now = mt.datetime.now().isoformat(timespec="seconds")
    session = "20260829"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "active", "active": True, "closed": False,
    })
    mt._MEM_CACHE["data"] = {
        "pulled_at": now, "core_pulled_at": now, "refresh_attempted_at": now,
        "ttl_s": 180, "core_sources": _core_ids(), "core_session_aligned": True,
        "board_date": session, "sources": _core_snapshot(now, session),
        "derived": {"zt_count": 1},
    }
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape(fresh_within_s=180)
    assert out["warming"] is False and out["freshness"]["stale"] is False and fired["n"] == 0
    assert out["derived"]["zt_count"] == 1


def test_recent_failed_attempt_prevents_repeated_refresh_hammer(monkeypatch):
    """核心数据虽旧，刚失败过的刷新尝试仍需遵守 TTL，不能每个读请求重打十源。"""
    now = datetime.now().isoformat(timespec="seconds")
    old = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    session = "20260829"
    monkeypatch.setattr(mt, "_market_clock", lambda now=None: {
        "latest_session": session, "phase": "active", "active": True, "closed": False,
    })
    sources = _core_snapshot(old, session, last_attempt_status="network_error")
    for item in sources.values():
        item["note"] = "(旧)|新失败:network_error"
    mt._MEM_CACHE["data"] = {
        "pulled_at": old, "core_pulled_at": old, "refresh_attempted_at": now,
        "ttl_s": 180, "core_sources": _core_ids(), "core_session_aligned": True,
        "board_date": session, "sources": sources, "derived": {},
    }
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh",
                        lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    first = mt.read_tape(fresh_within_s=180)
    second = mt.read_tape(fresh_within_s=180)
    assert first["freshness"]["stale"] is True
    assert first["freshness"]["refresh_due"] is False
    assert second["freshness"]["refresh_due"] is False and fired["n"] == 0


def test_read_stale_cache_returns_now_and_triggers(monkeypatch):
    old = "2020-01-01T00:00:00"
    mt._MEM_CACHE["data"] = {"pulled_at": old, "ttl_s": 180, "sources": {}, "derived": {}}
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape(fresh_within_s=180)
    assert out["warming"] is False and out["freshness"]["stale"] is True and fired["n"] == 1
    assert out["pulled_at"] == old              # 本次仍返回旧值(诚实龄期)


def test_read_stale_cache_query_only_returns_without_refresh(monkeypatch):
    old = "2020-01-01T00:00:00"
    mt._MEM_CACHE["data"] = {"pulled_at": old, "ttl_s": 180, "sources": {}, "derived": {}}
    fired = {"n": 0}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *a, **k: fired.__setitem__("n", fired["n"] + 1) or True)
    out = mt.read_tape(fresh_within_s=180, allow_refresh=False)
    assert out["warming"] is False and out["freshness"]["stale"] is True
    assert fired["n"] == 0 and "未启动刷新" in out["note"]
    assert out["refresh_state"] == "read_only"


def test_read_stale_cache_reports_refresh_start_failure_honestly(monkeypatch):
    mt._MEM_CACHE["data"] = {"pulled_at": "2020-01-01T00:00:00", "sources": {}, "derived": {}}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *args, **kwargs: False)
    monkeypatch.setattr(mt, "_REFRESH_INFLIGHT", [False])
    result = mt.read_tape()
    assert result["refresh_state"] == "start_failed"
    assert "未能启动" in result["note"] and "已触发" not in result["note"]
    assert result["pulled_at"] == "2020-01-01T00:00:00"


def test_read_stale_cache_distinguishes_existing_refresh_from_start_failure(monkeypatch):
    mt._MEM_CACHE["data"] = {"pulled_at": "2020-01-01T00:00:00", "sources": {}, "derived": {}}
    monkeypatch.setattr(mt, "_trigger_refresh", lambda *args, **kwargs: False)
    monkeypatch.setattr(mt, "_REFRESH_INFLIGHT", [True])
    result = mt.read_tape()
    assert result["refresh_state"] == "inflight"
    assert result["freshness"]["stale"] is True


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
