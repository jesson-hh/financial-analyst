# tests/test_seats_watcher.py
# 后端定时盯盘 watcher(2026-07-11 落子改造 Task 1):
# - 状态文件 var/seats_watch.json 往返(缺文件默认 enabled=False/预算24/counts 空)
# - 交易日盘中门(09:30-11:30 / 13:00-15:00;引擎日历失败回退周一~五)
# - 节流:per-code 10min 硬地板 + decisionFreq hourly≥1h / daily 当日一次
# - tick:enabled+盘中+预算余 → 逐 code(quote fresh → 节流 → decide);预算截断;source='watcher'
# - watching_codes:读 var/archive/strat_*.json 的 bind 非空并集,坏 json 跳过
# - refs best-effort 服务端解析(卡→cards、因子→recipe_factors,查不到跳过)
# - _decide_impl 提取:payload 带 source → _persist_decision 落盘可见;不带 → 键不落(旧形状不变)
# 全桩,零网络零 LLM。
import json
from datetime import date, datetime, timezone
from guanlan_v2.seats import watcher

def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    st = watcher.load_state()
    assert st == {"enabled": False, "daily_budget": watcher.DEFAULT_BUDGET, "counts": {}}
    st["enabled"] = True; watcher.save_state(st)
    assert watcher.load_state()["enabled"] is True

def test_market_open_gate():
    assert watcher._is_market_open(datetime(2026, 7, 10, 10, 0)) is True    # 周五盘中
    assert watcher._is_market_open(datetime(2026, 7, 10, 12, 0)) is False   # 午休
    assert watcher._is_market_open(datetime(2026, 7, 11, 10, 0)) is False   # 周六
    assert watcher._is_market_open(datetime(2026, 7, 10, 15, 30)) is False  # 收盘后


def test_market_session_boundaries_and_aware_clock(monkeypatch):
    monkeypatch.setattr(watcher, "_calendar_dates", lambda: {date(2026, 7, 10)})
    cases = [
        ((9, 29, 59), "preopen", False),
        ((9, 30, 0), "open", True),
        ((11, 29, 59), "open", True),
        ((11, 30, 0), "lunch_break", False),
        ((12, 59, 59), "lunch_break", False),
        ((13, 0, 0), "open", True),
        ((14, 59, 59), "open", True),
        ((15, 0, 0), "closed", False),
    ]
    for (h, m, s), phase, is_open in cases:
        out = watcher.market_session_state(datetime(2026, 7, 10, h, m, s))
        assert (out["market_phase"], out["market_open"]) == (phase, is_open)

    # The same instant supplied in UTC is normalized to Shanghai time (10:00).
    aware = watcher.market_session_state(datetime(2026, 7, 10, 2, 0, tzinfo=timezone.utc))
    assert aware["market_phase"] == "open" and aware["market_open"] is True


def test_quote_freshness_requires_open_session_same_day_and_recent(monkeypatch):
    monkeypatch.setattr(watcher, "_calendar_dates", lambda: {date(2026, 8, 14)})

    live = watcher.quote_session_state(
        "20260814100000", datetime(2026, 8, 14, 10, 0, 20))
    assert live["fresh"] is True and live["quote_state"] == "live"

    stale = watcher.quote_session_state(
        "20260814095500", datetime(2026, 8, 14, 10, 0, 20))
    assert stale["fresh"] is False and stale["quote_state"] == "stale"

    after_close = watcher.quote_session_state(
        "20260814161400", datetime(2026, 8, 14, 16, 20))
    assert after_close["fresh"] is False
    assert after_close["market_phase"] == "closed"

    weekend = watcher.quote_session_state(
        "20260814161400", datetime(2026, 8, 15, 10, 0))
    assert weekend["fresh"] is False
    assert weekend["market_phase"] == "non_trading_day"

def test_throttle_floor_and_freq():
    now = datetime(2026, 7, 10, 10, 30)
    assert watcher._throttle_ok("300750", "hourly", None, now) is True
    assert watcher._throttle_ok("300750", "hourly", "2026-07-10T10:25:00", now) is False  # <10min 地板
    assert watcher._throttle_ok("300750", "hourly", "2026-07-10T09:50:00", now) is False  # <1h
    assert watcher._throttle_ok("300750", "hourly", "2026-07-10T09:25:00", now) is True
    assert watcher._throttle_ok("300750", "daily", "2026-07-10T09:35:00", now) is False   # 当日已判
    assert watcher._throttle_ok("300750", "daily", "2026-07-09T14:00:00", now) is True

def test_tick_budget_and_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    watcher.save_state({"enabled": True, "daily_budget": 2, "counts": {}})
    calls = []
    codes = [{"code": c, "strategy_id": "s1", "name": "动量 · 默认",
              "clock": {"decisionFreq": "hourly"}, "creed": "x", "w": 0, "pa": False,
              "pa_method": "", "refs": []} for c in ("300750", "600519", "000001")]
    monkeypatch.setattr(watcher, "watching_codes", lambda: codes)
    out = watcher.tick(now=datetime(2026, 7, 10, 10, 0),
                       decide_fn=lambda p: calls.append(p) or {"ok": True},
                       quote_fn=lambda c: {"fresh": True},
                       decisions_tail_fn=lambda c: None)
    assert out["judged"] == ["300750", "600519"] and len(calls) == 2      # 预算 2 截断
    assert watcher.load_state()["counts"]["2026-07-10"] == 2
    assert calls[0]["source"] == "watcher" and calls[0]["code"] == "300750"
    out2 = watcher.tick(now=datetime(2026, 7, 10, 10, 0), decide_fn=lambda p: {"ok": True},
                        quote_fn=lambda c: {"fresh": True}, decisions_tail_fn=lambda c: None)
    assert out2["judged"] == [] and "budget" in str(out2["skipped"])       # 预算耗尽

def test_tick_skips_stale_quote_and_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    watcher.save_state({"enabled": True, "daily_budget": 9, "counts": {}})
    monkeypatch.setattr(watcher, "watching_codes", lambda: [{"code": "300750", "strategy_id": "s1",
        "name": "n", "clock": {}, "creed": "", "w": 0, "pa": False, "pa_method": "", "refs": []}])
    out = watcher.tick(now=datetime(2026, 7, 10, 10, 0), decide_fn=lambda p: {"ok": True},
                       quote_fn=lambda c: {"fresh": False}, decisions_tail_fn=lambda c: None)
    assert out["judged"] == [] and out["skipped"]["300750"] == "stale_quote"
    watcher.save_state({"enabled": False, "daily_budget": 9, "counts": {}})
    out2 = watcher.tick(now=datetime(2026, 7, 10, 10, 0), decide_fn=lambda p: {"ok": True},
                        quote_fn=lambda c: {"fresh": True}, decisions_tail_fn=lambda c: None)
    assert out2 == {"judged": [], "skipped": {"_": "disabled"}}


# ───────── 以下为补充测试(只增不删:watching_codes 数据源 / refs 桩解析 / source 落盘 / 路由)─────────

def test_watching_codes_migrates_legacy_bind_once_and_skips_bad_json(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(watcher, "ASSIGNMENTS_PATH", tmp_path / "assignments.json")
    (tmp_path / "strat_a.json").write_text(json.dumps({
        "id": "s_a", "type": "strategy", "name": "动量 · 测试", "bind": ["300750", "600519"],
        "clock": {"decisionFreq": "daily"}, "creed": "突破加仓", "w": 0.2, "pa": True,
        "paMethod": "看长做短", "refs": ["EV-001"]}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "strat_b.json").write_text(json.dumps({
        "id": "s_b", "name": "空绑不盯", "bind": []}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "strat_bad.json").write_text("{oops", encoding="utf-8")   # 坏 json 跳过
    from guanlan_v2.seats import watch_assignments as _wa
    _wa.migrate_legacy_assignments(
        path=watcher.ASSIGNMENTS_PATH, archive_dir=watcher.ARCHIVE_DIR)
    rows = watcher.watching_codes()
    assert [r["code"] for r in rows] == ["300750", "600519"]
    r0 = rows[0]
    assert r0["strategy_id"] == "s_a" and r0["name"] == "动量 · 测试"
    assert r0["clock"]["decisionFreq"] == "daily" and r0["creed"] == "突破加仓"
    assert r0["w"] == 0.2 and r0["pa"] is True and r0["pa_method"] == "看长做短"
    assert r0["refs"] == ["EV-001"]
    assert r0["requested_evidence_tf"] == "day"
    assert r0["effective_evidence_tf"] == "day"
    assert r0["thinking_mode"] == "fast"
    assert r0["assignment_revision"] == 1

def test_resolve_refs_best_effort_with_stubs(monkeypatch):
    """refs 服务端 best-effort 解析:卡 id → cards(decide 既有字段名)、因子 id →
    recipe_factors;查不到(前端专有 GL 实体等)跳过,绝不编造。"""
    monkeypatch.setattr(watcher, "_lookup_card", lambda rid: (
        {"title": "反转卡", "insight": "超跌反弹", "verdict": "通过", "conf": 70, "ic": "0.03"}
        if rid == "EV-001" else None))
    monkeypatch.setattr(watcher, "_lookup_factor", lambda rid: (
        {"name": "lib_turnover_cv20", "expr": "std(turnover,20)", "ic": "0.041"}
        if rid == "lib_turnover_cv20" else None))
    cards, factors = watcher._resolve_refs(["EV-001", "lib_turnover_cv20", "card_ghost"])
    assert cards == [{"name": "反转卡", "insight": "超跌反弹", "verdict": "通过",
                      "conf": 70, "ic": "0.03"}]
    assert factors == [{"id": "lib_turnover_cv20", "name": "lib_turnover_cv20",
                        "ic": "0.041", "expr": "std(turnover,20)"}]

def test_resolve_refs_card_falls_back_to_verdict_insight(monkeypatch):
    monkeypatch.setattr(watcher, "_lookup_card",
                        lambda rid: {"title": "T", "insight": "", "verdict": "存疑", "conf": 0, "ic": ""})
    monkeypatch.setattr(watcher, "_lookup_factor", lambda rid: None)
    cards, factors = watcher._resolve_refs(["EV-002"])
    assert factors == [] and cards[0]["insight"] == "存疑" and cards[0]["ic"] is None


def test_resolve_refs_diagnostics_distinguish_missing_and_store_errors(monkeypatch):
    """旧的 cards/factors 二元返回保持兼容;可观测模式额外记住
    已解析、未解析与存储故障,不再把它们静默压成两个空数组。"""
    def card(rid):
        if rid == "EV-OK":
            return {"title": "卡", "insight": "有效", "verdict": "通过"}
        if rid == "BROKEN":
            raise OSError("card store unavailable")
        return None

    def factor(rid):
        if rid == "FAC-OK":
            return {"name": "FAC-OK", "expr": "close/open"}
        if rid == "BROKEN":
            raise RuntimeError("factor store unavailable")
        return None

    monkeypatch.setattr(watcher, "_lookup_card", card)
    monkeypatch.setattr(watcher, "_lookup_factor", factor)

    # Default remains the exact two-item compatibility seam.
    compat = watcher._resolve_refs(["EV-OK", "FAC-OK"])
    assert len(compat) == 2

    cards, factors, diag = watcher._resolve_refs(
        ["EV-OK", "FAC-OK", "MISSING", "BROKEN"], include_diagnostics=True)
    assert [x["name"] for x in cards] == ["卡"]
    assert [x["name"] for x in factors] == ["FAC-OK"]
    assert diag["requested"] == ["EV-OK", "FAC-OK", "MISSING", "BROKEN"]
    assert diag["resolved"] == [
        {"ref": "EV-OK", "kind": "card"},
        {"ref": "FAC-OK", "kind": "factor"},
    ]
    assert diag["unresolved"] == ["MISSING", "BROKEN"]
    assert [(e["ref"], e["stage"], e["error_type"]) for e in diag["errors"]] == [
        ("BROKEN", "card", "OSError"),
        ("BROKEN", "factor", "RuntimeError"),
    ]
    assert diag["status"] == "resolved_partial"


def test_tick_forwards_allowlisted_fresh_quote_and_unresolved_ref_diagnostic(
        tmp_path, monkeypatch):
    """通过 fresh 门的 quote 以最小结构化证据进 decide;上游附加字段不泄漏。

    策略宣言 ref 但 0 解析时不消耗前就崩掉(独立行情仍可研判),
    但 payload 必须显式 ``unresolved_all``，不能冒充「未配置 refs」。
    """
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "watch.json")
    watcher.save_state({"enabled": True, "daily_budget": 3, "counts": {}})
    monkeypatch.setattr(watcher, "watching_codes", lambda: [{
        "code": "300750", "strategy_id": "s_quote", "name": "短线",
        "clock": {"decisionFreq": "hourly"}, "creed": "x", "w": 0,
        "pa": False, "pa_method": "", "refs": ["GHOST-REF"],
        "requested_evidence_tf": "5min", "effective_evidence_tf": "5min",
        "decision_cadence": "60min", "thinking_mode": "fast",
        "assignment_revision": 1,
    }])
    monkeypatch.setattr(watcher, "_lookup_card", lambda _rid: None)
    monkeypatch.setattr(watcher, "_lookup_factor", lambda _rid: None)
    monkeypatch.setattr(watcher, "_positions_for_payload", lambda: {})
    monkeypatch.setattr(watcher, "_industry_for", lambda _code: "")
    captured = []
    quote = {
        "fresh": True, "name": "宁德时代", "price": 364.71,
        "open": 366.60, "high": 367.33, "low": 357.30,
        "asof": "20260831144700", "volume": 851300.0,
        "amount": 3081234.5, "turnover_rate": 0.55, "vol_ratio": 0.89,
        "source": "fixture", "transport_secret": "must-not-pass",
    }
    out = watcher.tick(
        now=datetime(2026, 8, 31, 14, 47),
        decide_fn=lambda payload: captured.append(payload) or {"ok": True},
        quote_fn=lambda _code: quote,
        decisions_tail_fn=lambda _code: None,
    )
    assert out["judged"] == ["300750"]
    # Keep the decision anchor at second precision: a quote from this same
    # second is not misclassified as future evidence downstream.
    assert captured[0]["date"] == "2026-08-31T14:47:00"
    assert datetime.strptime(captured[0]["quote"]["asof"], "%Y%m%d%H%M%S") \
        <= datetime.fromisoformat(captured[0]["date"])
    assert captured[0]["quote"] == {
        "price": 364.71, "open": 366.60, "high": 367.33, "low": 357.30,
        "asof": "20260831144700", "volume": 851300.0, "amount": 3081234.5,
        "turnover": 0.55, "vol_ratio": 0.89, "source": "fixture", "fresh": True,
    }
    assert "transport_secret" not in captured[0]["quote"]
    assert captured[0]["ref_resolution"] == {
        "requested": ["GHOST-REF"], "resolved": [],
        "unresolved": ["GHOST-REF"], "errors": [], "status": "unresolved_all",
    }
    assert captured[0]["cards"] == [] and captured[0]["recipe_factors"] == []
    assert watcher.load_state()["counts"]["2026-08-31"] == 1


def test_production_tick_anchors_payload_after_quote_without_moving_tick_clock(
        tmp_path, monkeypatch):
    """生产 tick 的 payload 锚点在取 quote 后读取，避免 provider 延迟竞态。

    预算日期、盘中门和节流仍使用 quote 前捕获的 tick 时钟；这里只验证
    payload 的证据锚点不早于 quote.asof。显式 ``now`` 的回放路径见上一个
    测试，保持固定不变。
    """
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "watch.json")
    watcher.save_state({"enabled": True, "daily_budget": 3, "counts": {}})
    monkeypatch.setattr(watcher, "_is_market_open", lambda _now: True)
    monkeypatch.setattr(watcher, "watching_codes", lambda: [{
        "code": "300750", "strategy_id": "s_anchor", "name": "短线",
        "clock": {"decisionFreq": "hourly"}, "creed": "x", "w": 0,
        "pa": False, "pa_method": "", "refs": [],
        "requested_evidence_tf": "5min", "effective_evidence_tf": "5min",
        "decision_cadence": "60min", "thinking_mode": "fast",
        "assignment_revision": 1,
    }])
    monkeypatch.setattr(watcher, "orchestrated_codes", lambda: set())
    monkeypatch.setattr(watcher, "_positions_for_payload", lambda: {})
    monkeypatch.setattr(watcher, "_industry_for", lambda _code: "")
    monkeypatch.setattr(watcher, "_hold_entry_for", lambda _code: None)

    # The first read is the stable tick clock.  The quote provider completes
    # just after that instant; the second read is the post-quote anchor.
    clock = iter((datetime(2026, 8, 31, 14, 47, 0),
                  datetime(2026, 8, 31, 14, 47, 1)))
    monkeypatch.setattr(watcher, "_server_now", lambda: next(clock))
    captured = []
    quote = {"fresh": True, "asof": "20260831144701", "price": 364.71}
    out = watcher.tick(
        decide_fn=lambda payload: captured.append(payload) or {"ok": True},
        quote_fn=lambda _code: quote,
        decisions_tail_fn=lambda _code: None,
    )

    assert out["judged"] == ["300750"]
    assert datetime.fromisoformat(captured[0]["date"]) >= datetime.strptime(
        quote["asof"], "%Y%m%d%H%M%S")
    assert watcher.load_state()["counts"]["2026-08-31"] == 1


def test_quote_for_payload_requires_fresh_and_never_guesses_source():
    assert watcher._quote_for_payload({"fresh": False, "price": 1}) is None
    evidence = watcher._quote_for_payload({"fresh": True, "price": 1})
    assert evidence is not None
    assert evidence["source"] == ""
    assert set(evidence) == {
        "price", "open", "high", "low", "asof", "volume", "amount",
        "turnover", "vol_ratio", "source", "fresh",
    }


def test_quote_production_keeps_full_minimal_market_snapshot(monkeypatch):
    from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector

    raw = {
        "name": "宁德时代", "price": 364.71, "open": 366.60,
        "high": 367.33, "low": 357.30, "asof": "20260831144700",
        "volume": 851300.0, "amount": 3081234.5,
        "turnover_rate": 0.55, "vol_ratio": 0.89,
    }
    monkeypatch.setattr(TencentQuoteCollector, "fetch",
                        lambda _self, _codes: {"SZ300750": raw})
    monkeypatch.setattr(watcher, "_market_now",
                        lambda: watcher._market_local(datetime(2026, 8, 31, 14, 47, 20)))
    monkeypatch.setattr(watcher, "_calendar_dates", lambda: {date(2026, 8, 31)})
    q = watcher._quote_production("300750")
    assert q["fresh"] is True
    assert {k: q[k] for k in (
        "price", "open", "high", "low", "asof", "volume", "amount",
        "turnover", "vol_ratio", "source",
    )} == {
        "price": 364.71, "open": 366.60, "high": 367.33, "low": 357.30,
        "asof": "20260831144700", "volume": 851300.0, "amount": 3081234.5,
        "turnover": 0.55, "vol_ratio": 0.89,
        "source": "tencent_realtime_quote",
    }


class _FakeLLM:
    provider = "deepseek"
    model = "deepseek-chat"

    @classmethod
    def for_agent(cls, name):
        return cls()

    def with_overrides(self, **kw):
        return self

    async def chat(self, messages, **kw):
        return {"choices": [{"message": {
            "content": '{"direction":"观望","confidence":55,"rationale":"桩","key_evidence":["e"]}',
            "reasoning_content": ""}}]}


class _DayLoader:
    def fetch_quote(self, code, start, end, freq):
        import pandas as pd
        ts = pd.date_range(end=str(end)[:10], periods=80, freq="B")
        close = [50 + i * 0.1 for i in range(len(ts))]
        return pd.DataFrame({
            "trade_date": ts, "open": close,
            "high": [x + 0.5 for x in close], "low": [x - 0.5 for x in close],
            "close": close, "vol": 1000.0, "amount": [x * 1000 for x in close],
        })


def _patch_decide_deps(tmp_path, monkeypatch):
    from guanlan_v2.seats import api as seats_api
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _DayLoader())
    monkeypatch.setattr(_llm, "LLMClient", _FakeLLM)
    return seats_api


def test_decide_impl_persists_source(tmp_path, monkeypatch):
    """tick payload 的 source:'watcher' 必须经 _decide_impl → _persist_decision 落盘可见;
    不带 source(手动/旧路径)→ 键不落,旧记录形状不变。"""
    import asyncio
    seats_api = _patch_decide_deps(tmp_path, monkeypatch)
    res = asyncio.run(seats_api._decide_impl({
        "code": "SZ300750", "name": "宁德时代", "date": "2026-07-10",
        "seat_cn": "动量 · 默认", "creed": "x", "mode": "fast", "source": "watcher"}))
    assert res["ok"] is True
    rec = json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec.get("kind") == "decide" and rec.get("source") == "watcher"
    res2 = asyncio.run(seats_api._decide_impl({
        "code": "SZ300750", "date": "2026-07-10", "mode": "fast"}))
    assert res2["ok"] is True
    rec2 = json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert "source" not in rec2

def test_watch_routes_status_and_toggle(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.seats import api as seats_api
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    monkeypatch.setattr(watcher, "watching_codes", lambda: [])
    monkeypatch.setattr(watcher, "_is_market_open", lambda now: False)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    c = TestClient(app)
    j = c.get("/seats/watch/status").json()
    assert j["ok"] is True and j["enabled"] is False and j["watching"] == []
    assert j["daily_budget"] == watcher.DEFAULT_BUDGET and j["market_open"] is False
    j2 = c.post("/seats/watch/toggle", json={"on": True}).json()
    assert j2["ok"] is True and j2["enabled"] is True
    assert watcher.load_state()["enabled"] is True                 # 落盘生效
    j3 = c.post("/seats/watch/toggle", json={"on": False}).json()
    assert j3["enabled"] is False and watcher.load_state()["enabled"] is False

def test_get_status_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    monkeypatch.setattr(watcher, "watching_codes", lambda: [
        {"code": "300750", "strategy_id": "s1", "name": "a", "clock": {}, "creed": "",
         "w": 0, "pa": False, "pa_method": "", "refs": []},
        {"code": "300750", "strategy_id": "s2", "name": "b", "clock": {}, "creed": "",
         "w": 0, "pa": False, "pa_method": "", "refs": []}])
    monkeypatch.setattr(watcher, "_is_market_open", lambda now: True)
    st = watcher.get_status()
    assert st["enabled"] is False and st["daily_budget"] == watcher.DEFAULT_BUDGET
    assert st["watching"] == ["300750"]                            # 去重
    assert st["today_count"] == 0 and st["last_tick"] is None and st["market_open"] is True
