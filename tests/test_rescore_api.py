# -*- coding: utf-8 -*-
"""P5 再打分端点+状态机单测:裸 FastAPI 挂 router;run 主体打桩。零网络。"""
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.screen.rescore as rs


def _client():
    app = FastAPI()
    app.include_router(rs.build_rescore_router())
    return TestClient(app)


@pytest.fixture
def client():
    """P6′ picks 端点(kind 过滤)测试用:挂 /screen 全路由组(picks 端点归属 screen.api)。"""
    from guanlan_v2.screen.api import build_screen_router
    app = FastAPI()
    app.include_router(build_screen_router())
    return TestClient(app)


def _reset(monkeypatch):
    monkeypatch.setattr(rs, "_RESCORE_STATE", {
        "running": False, "phase": "idle", "label": "", "run_id": None,
        "started_at": None, "ended_at": None, "ok": None, "error": None, "lines": []})


def test_run_rescore_end_to_end_rows(monkeypatch, tmp_path):
    """run 主体:池→链分→情绪→综合→落档,行形完整。"""
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "_run_rerank_bridge",
                        lambda rows, market: {"ok": False, "reason": "stubbed"})
    monkeypatch.setattr(rs, "v4_pool", lambda n, model="prod": [{"code": "SH1", "v4pct": 90.0},
                                                                {"code": "SH2", "v4pct": 80.0}])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: (
        {"SH1": {"seg": "A1", "seg_name": "算力", "chain": 0.6, "research": 3.0,
                 "therm": 80.0, "quadrant": "hh"}, "SH2": None},
        {"quote_date": "2026-07-03"}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {"SH1": {"tag": "利好", "read": "x", "score": 1.0}, "SH2": None},
        {"llm_calls": 1, "cache_hits": 0, "as_of": "10:00",
         "market_read": "平", "market_tilt": "中性"}))
    end = rs.run_rescore("rs_t1", top_n=2, note="测试", progress=lambda **kw: None)
    assert end["ok"] is True and len(end["rows"]) == 2
    r1 = end["rows"][0]
    assert r1["code"] == "SH1" and r1["chain"]["seg"] == "A1" and r1["parts"] == 3
    r2 = end["rows"][1]
    assert r2["chain"] is None and r2["news"] is None and r2["parts"] == 1
    assert end["stats"]["llm_calls"] == 1
    assert end["stats"]["board_freshness"]["quote_date"] == "2026-07-03"
    assert end["rerank"]["ok"] is False           # 桩真被走到(防重构绕开 rerank 段后静默失覆盖)
    latest = rs.read_latest()
    assert latest["run_id"] == "rs_t1" and latest["note"] == "测试"


def test_run_rescore_board_fail_honest(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n, model="prod": [{"code": "SH1", "v4pct": 90.0}])

    def boom(codes):
        raise rs.RescoreError("产业链板不可用: 板坏了")

    monkeypatch.setattr(rs, "industry_scores", boom)
    end = rs.run_rescore("rs_t2", top_n=1, note="", progress=lambda **kw: None)
    assert end["ok"] is False and "板不可用" in end["error"]
    assert rs.read_latest()["ok"] is False           # 失败也落档显形


def test_endpoint_start_clamps_and_single_flight(monkeypatch, tmp_path):
    _reset(monkeypatch)
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    seen = {}

    def fake_run(run_id, top_n, note, progress, model="prod"):
        seen.update(top_n=top_n)
        progress(phase="score", label="打分中…")
        return {"ok": True, "run_id": run_id, "rows": [], "stats": {}}

    monkeypatch.setattr(rs, "run_rescore", fake_run)
    c = _client()
    j = c.post("/screen/rescore", json={"top_n": 999, "note": "x"}).json()
    assert j["ok"] is True and j["run_id"].startswith("rs_")
    for _ in range(50):
        time.sleep(0.02)
        if not rs._rescore_public_state()["running"]:
            break
    assert seen["top_n"] == 100                      # 钳 [5,100]
    st = rs._rescore_public_state()
    assert st["phase"] == "done" and st["ok"] is True
    assert any("打分中" in ln for ln in st["lines"])


def test_endpoint_latest_empty_honest(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "none.jsonl")
    j = _client().get("/screen/rescore/latest").json()
    assert j["ok"] is True and j["run"] is None


def test_endpoint_already_running_no_deadlock(monkeypatch, tmp_path):
    """running 期间第二个 POST 必须秒回 already_running(评审复现过的死锁回归)。"""
    _reset(monkeypatch)
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    release = threading.Event()

    def slow_run(run_id, top_n, note, progress, model="prod"):
        release.wait(timeout=5)
        return {"ok": True, "run_id": run_id, "rows": [], "stats": {}}

    monkeypatch.setattr(rs, "run_rescore", slow_run)
    c = _client()
    j1 = c.post("/screen/rescore", json={"top_n": 5}).json()
    assert j1["ok"] is True
    t0 = time.time()
    j2 = c.post("/screen/rescore", json={"top_n": 5}).json()
    assert time.time() - t0 < 2.0                     # 不卡死
    assert j2["ok"] is False and j2["reason"] == "already_running" and "state" in j2
    release.set()
    for _ in range(100):
        time.sleep(0.02)
        if not rs._rescore_public_state()["running"]:
            break
    assert rs._rescore_public_state()["phase"] == "done"


def test_screen_picks_filters_rerank_ab_by_default(tmp_path, monkeypatch, client):
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    pk.append_pick({"kind": "rerank_ab", "arm": "data", "codes": ["SH600000"],
                    "run_id": "rs_x", "ts": "2026-07-05T10:00:00", "snapshot": False})
    pk.append_pick({"codes": ["SZ000001"], "snapshot": True, "ts": "2026-07-05T10:01:00"})
    r = client.get("/screen/picks").json()
    body = r.get("picks") or r.get("items") or []
    assert all(x.get("kind") != "rerank_ab" for x in body)     # 默认过滤=现有消费方零变化
    r2 = client.get("/screen/picks", params={"kind": "rerank_ab"}).json()
    rows2 = r2.get("picks") or r2.get("items") or []
    assert rows2 and all(x.get("kind") == "rerank_ab" for x in rows2)


def test_v4_ranking_date_reads_parquet(monkeypatch, tmp_path):
    """v4_ranking_date:读所配 prod 榜路径的 date 首行;缺文件 → None(诚实,不猜)。"""
    import pandas as pd
    p = tmp_path / "r.parquet"
    pd.DataFrame({"code": ["SH1"], "lgb_pct": [0.9], "date": ["2026-07-08"]}).to_parquet(p)
    monkeypatch.setattr(rs, "_v4_ranking_path", lambda model="prod": p)
    assert rs.v4_ranking_date() == "2026-07-08"
    monkeypatch.setattr(rs, "_v4_ranking_path", lambda model="prod": tmp_path / "missing.parquet")
    assert rs.v4_ranking_date() is None


def test_run_rescore_records_base_model_and_ranking_date(monkeypatch, tmp_path):
    """口径落档:run 记录带 base_model="prod" + ranking_date(所读榜 date),供口径守卫。"""
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "v4_ranking_date", lambda model="prod": "2026-07-09")
    monkeypatch.setattr(rs, "v4_pool", lambda n, model="prod": [{"code": "SH1", "v4pct": 90.0}])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({"SH1": None}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {"SH1": None}, {"llm_calls": 0, "cache_hits": 0, "as_of": None,
                        "market_read": None, "market_tilt": None}))
    monkeypatch.setattr(rs, "_run_rerank_bridge",
                        lambda rows, market: {"ok": False, "reason": "stubbed"})
    end = rs.run_rescore("rs_t5", top_n=1, note="", progress=lambda **kw: None)
    assert end["ok"] is True
    assert end["base_model"] == "prod" and end["ranking_date"] == "2026-07-09"
    latest = rs.read_latest()
    assert latest["base_model"] == "prod" and latest["ranking_date"] == "2026-07-09"


def test_run_rescore_failure_still_records_base(monkeypatch, tmp_path):
    """失败 run 也落口径字段(ranking_date 未知 → None 诚实),口径守卫对失败档不失明。"""
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "v4_ranking_date", lambda model="prod": None)

    def boom(n, model="prod"):
        raise rs.RescoreError("v4 榜不可用: x")

    monkeypatch.setattr(rs, "v4_pool", boom)
    end = rs.run_rescore("rs_t6", top_n=1, note="", progress=lambda **kw: None)
    assert end["ok"] is False
    assert end["base_model"] == "prod" and end["ranking_date"] is None


# ── T1:变体榜 model 参数化(池来源可切 prod/变体 id,base_model 回真实 model)───────

def test_v4_pool_reads_variant_ranking(monkeypatch, tmp_path):
    """v4_pool(model=变体):经 model_registry.variant_ranking_path 读变体榜(非 prod 榜),按 pct 降序取 top_n。"""
    import pandas as pd
    from guanlan_v2.screen import model_registry as mr
    p = tmp_path / "variant.parquet"
    pd.DataFrame({"code": ["SZ1", "SZ2", "SZ3"], "date": ["2026-07-10"] * 3,
                  "lgb_pct": [0.2, 0.9, 0.5]}).to_parquet(p)
    monkeypatch.setattr(mr, "variant_ranking_path", lambda vid: p)
    pool = rs.v4_pool(2, model="m_variant_x")
    assert [r["code"] for r in pool] == ["SZ2", "SZ3"]     # 0.9 > 0.5 > 0.2 取前二
    # 口径路由坐实:变体走 variant_ranking_path;prod/空/None 走生产榜 V4_RANKING_PARQUET
    from guanlan_v2.strategy.paths import V4_RANKING_PARQUET
    assert rs._v4_ranking_path("m_variant_x") == p
    assert rs._v4_ranking_path("prod") == V4_RANKING_PARQUET
    assert rs._v4_ranking_path("") == V4_RANKING_PARQUET
    assert rs._v4_ranking_path(None) == V4_RANKING_PARQUET


def test_v4_pool_variant_missing_fails_honest_no_prod_fallback(monkeypatch, tmp_path):
    """变体榜不存在 → RescoreError 诚实失败;绝不静默回落 prod 榜冒充(prod 有效也不返回其行)。"""
    import pandas as pd
    from guanlan_v2.screen import model_registry as mr
    import guanlan_v2.strategy.paths as paths
    prod = tmp_path / "prod.parquet"                       # prod 榜有效且带哨兵码
    pd.DataFrame({"code": ["PRODSENTINEL"], "date": ["2026-07-10"],
                  "lgb_pct": [0.9]}).to_parquet(prod)
    monkeypatch.setattr(paths, "V4_RANKING_PARQUET", prod)
    monkeypatch.setattr(mr, "variant_ranking_path",
                        lambda vid: tmp_path / "does_not_exist.parquet")
    with pytest.raises(rs.RescoreError):
        rs.v4_pool(5, model="m_gone")                      # 变体缺 → 抛,绝不返回 prod 哨兵
    # prod 口径仍正常读得到哨兵(证 prod 榜本身有效,失败纯因变体缺而非环境坏)
    assert rs.v4_pool(5, model="prod")[0]["code"] == "PRODSENTINEL"


def test_run_rescore_variant_base_model_and_flag(monkeypatch, tmp_path):
    """变体 run:base_model 落真实变体 id(非硬编 prod)+ 落 model 旗(口径显形)+ ranking_date 走变体榜。"""
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    seen = {}

    def rd(model="prod"):
        seen["rd_model"] = model
        return "2026-06-30"

    def pool(n, model="prod"):
        seen["pool_model"] = model
        return [{"code": "SZ1", "v4pct": 90.0}]

    monkeypatch.setattr(rs, "v4_ranking_date", rd)
    monkeypatch.setattr(rs, "v4_pool", pool)
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({"SZ1": None}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {"SZ1": None}, {"llm_calls": 0, "cache_hits": 0, "as_of": None,
                        "market_read": None, "market_tilt": None}))
    monkeypatch.setattr(rs, "_run_rerank_bridge",
                        lambda rows, market: {"ok": False, "reason": "stubbed"})
    end = rs.run_rescore("rs_t7", top_n=1, note="", progress=lambda **kw: None,
                         model="m_variant_x")
    assert end["ok"] is True
    assert end["base_model"] == "m_variant_x"              # 回真实 model 非 prod
    assert end["model"] == "m_variant_x"                   # 变体口径旗显形
    assert end["ranking_date"] == "2026-06-30"
    assert seen["pool_model"] == "m_variant_x"             # 池确实按变体取
    assert seen["rd_model"] == "m_variant_x"               # date 也走变体榜
    latest = rs.read_latest()
    assert latest["base_model"] == "m_variant_x" and latest["model"] == "m_variant_x"


def test_run_rescore_prod_default_unchanged(monkeypatch, tmp_path):
    """prod 默认口径零行为变化:base_model=="prod" 且不落 model 旗(前端不误标变体口径)。"""
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "v4_ranking_date", lambda model="prod": "2026-07-09")
    monkeypatch.setattr(rs, "v4_pool", lambda n, model="prod": [{"code": "SH1", "v4pct": 90.0}])
    monkeypatch.setattr(rs, "industry_scores", lambda codes: ({"SH1": None}, {}))
    monkeypatch.setattr(rs, "news_scores", lambda codes, top_n: (
        {"SH1": None}, {"llm_calls": 0, "cache_hits": 0, "as_of": None,
                        "market_read": None, "market_tilt": None}))
    monkeypatch.setattr(rs, "_run_rerank_bridge",
                        lambda rows, market: {"ok": False, "reason": "stubbed"})
    end = rs.run_rescore("rs_t8", top_n=1, note="", progress=lambda **kw: None)  # 默认 model=prod
    assert end["ok"] is True
    assert end["base_model"] == "prod"
    assert "model" not in end                              # prod 不落变体旗


def test_start_rescore_bg_passes_variant_model(monkeypatch):
    """start_rescore_bg(model=变体) 透传给 worker→run_rescore,且 state.base_model 落真实 model。"""
    import time as _t
    calls = {}

    def fake_run(run_id, top_n, note, progress, model="prod"):
        calls["model"] = model
        return {"ok": True, "run_id": run_id, "rows": [], "stats": {}}

    monkeypatch.setattr(rs, "run_rescore", fake_run)
    r = rs.start_rescore_bg(top_n=7, note="x", model="m_variant_x")
    assert r["ok"] and r["state"]["base_model"] == "m_variant_x"   # state 落真实 model
    for _ in range(50):
        if calls.get("model"):
            break
        _t.sleep(0.05)
    assert calls["model"] == "m_variant_x"                # 透传坐实
    for _ in range(50):
        if not rs._RESCORE_STATE.get("running"):
            break
        _t.sleep(0.05)
    assert not rs._RESCORE_STATE.get("running")


def test_status_exposes_base_model_and_ranking_date(monkeypatch, tmp_path):
    """/screen/rescore/status:state 带 base_model/ranking_date(progress 通道回填)。"""
    _reset(monkeypatch)
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")

    def fake_run(run_id, top_n, note, progress, model="prod"):
        progress(phase="pool", label="池", base_model="prod", ranking_date="2026-07-09")
        return {"ok": True, "run_id": run_id, "rows": [], "stats": {}}

    monkeypatch.setattr(rs, "run_rescore", fake_run)
    c = _client()
    assert c.post("/screen/rescore", json={"top_n": 5}).json()["ok"] is True
    for _ in range(50):
        time.sleep(0.02)
        if not rs._rescore_public_state()["running"]:
            break
    st = c.get("/screen/rescore/status").json()["state"]
    assert st["base_model"] == "prod" and st["ranking_date"] == "2026-07-09"


def test_start_rescore_bg_module_level(monkeypatch):
    import time as _t

    from guanlan_v2.screen import rescore as rs
    calls = {}
    monkeypatch.setattr(rs, "run_rescore",
                        lambda run_id, top_n, note, progress, model="prod": calls.setdefault(
                            "args", (top_n, note)) or {"ok": True})
    r = rs.start_rescore_bg(top_n=7, note="daily-scheduler")
    assert r["ok"] and r["started"] and r["run_id"].startswith("rs_")
    for _ in range(50):
        if calls.get("args"):
            break
        _t.sleep(0.05)
    assert calls["args"] == (7, "daily-scheduler")
    for _ in range(50):                       # finally 必清 running
        if not rs._RESCORE_STATE.get("running"):
            break
        _t.sleep(0.05)
    assert not rs._RESCORE_STATE.get("running")


def test_daily_rerank_hook_default_off(monkeypatch):
    """GUANLAN_RERANK_DAILY 缺省 → 绝不调 start_rescore_bg(零行为变化);
    =1 且榜 date==今天(守卫过)→ 调。"""
    import datetime as dt
    import guanlan_v2.screen.api as sapi
    from guanlan_v2.screen import rescore as rs
    today = dt.date.today().isoformat()
    monkeypatch.delenv("GUANLAN_RERANK_DAILY", raising=False)
    called = []
    monkeypatch.setattr(rs, "start_rescore_bg",
                        lambda **k: called.append(k) or {"ok": True})
    sapi._maybe_daily_rerank(today)
    assert called == []
    monkeypatch.setenv("GUANLAN_RERANK_DAILY", "1")
    sapi._maybe_daily_rerank(today)
    assert called and called[0].get("note") == "daily-scheduler"


def test_daily_rerank_guard_skips_stale_or_unknown(monkeypatch):
    """日跑守卫:榜 date≠今天/未知 → 跳过不烧 LLM(防脏 A/B 档);==今天(含时间戳串)→ 放行。"""
    import datetime as dt
    import guanlan_v2.screen.api as sapi
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setenv("GUANLAN_RERANK_DAILY", "1")
    called = []
    monkeypatch.setattr(rs, "start_rescore_bg",
                        lambda **k: called.append(k) or {"ok": True})
    sapi._maybe_daily_rerank("2020-01-01")           # 旧榜
    sapi._maybe_daily_rerank(None)                   # 未知
    sapi._maybe_daily_rerank("")                     # 空串
    assert called == []
    today = dt.date.today().isoformat()
    sapi._maybe_daily_rerank(today + " 00:00:00")    # 时间戳串归一 [:10] 后命中
    assert len(called) == 1
