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
from datetime import datetime
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

def test_watching_codes_reads_bind_and_skips_bad_json(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "ARCHIVE_DIR", tmp_path)
    (tmp_path / "strat_a.json").write_text(json.dumps({
        "id": "s_a", "type": "strategy", "name": "动量 · 测试", "bind": ["300750", "600519"],
        "clock": {"decisionFreq": "daily"}, "creed": "突破加仓", "w": 0.2, "pa": True,
        "paMethod": "看长做短", "refs": ["EV-001"]}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "strat_b.json").write_text(json.dumps({
        "id": "s_b", "name": "空绑不盯", "bind": []}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "strat_bad.json").write_text("{oops", encoding="utf-8")   # 坏 json 跳过
    rows = watcher.watching_codes()
    assert [r["code"] for r in rows] == ["300750", "600519"]
    r0 = rows[0]
    assert r0["strategy_id"] == "s_a" and r0["name"] == "动量 · 测试"
    assert r0["clock"]["decisionFreq"] == "daily" and r0["creed"] == "突破加仓"
    assert r0["w"] == 0.2 and r0["pa"] is True and r0["pa_method"] == "看长做短"
    assert r0["refs"] == ["EV-001"]

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
        ts = pd.date_range("2026-04-01", periods=80, freq="D")
        return pd.DataFrame({"trade_date": ts, "open": 50.0, "high": 51.0, "low": 49.0,
                             "close": [50 + i * 0.1 for i in range(80)], "vol": 1000.0})


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
