# -*- coding: utf-8 -*-
"""autonomy 端点 + 调度钩子单测(Task 5·TDD):裸 FastAPI 挂 router(照 research/fundflow
范式,build_screen_router()太重不整体拉);read_jobs/read_report/start_job_bg 全打桩,
零真实副作用、零网络、零 LLM。

覆盖:
- GET /autonomy/jobs、GET /autonomy/report/latest、POST /autonomy/run 三端点透传契约;
- runtime.maybe_enqueue_daily_review 三门(env/note/当日已跑)+ 全过排队,共四测;
- rescore._run_thread finally 钩子只在 ok 分支才尝试排队(自审重点①的直接坐实);
- /screen/health 响应含 review_scheduler 键(合镜既有 rerank_scheduler/regen_scheduler)。
"""
from __future__ import annotations

import datetime as dt

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.autonomy.api import build_autonomy_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_autonomy_router())
    return TestClient(app)


# ── 三端点 ──────────────────────────────────────────────────────────────

def test_jobs_ep_returns_state_and_jobs_running(monkeypatch):
    """running 时 running_job_id 透传给 read_jobs(当前跑的 job 才需在列表里标 running)。"""
    import guanlan_v2.autonomy.jobs as J
    import guanlan_v2.autonomy.runtime as RT

    monkeypatch.setattr(RT, "_autonomy_public_state",
                        lambda: {"running": True, "job_id": "aj_x", "phase": "seats"})
    seen = {}

    def fake_read_jobs(limit, running_job_id=None):
        seen["limit"] = limit
        seen["running_job_id"] = running_job_id
        return [{"job_id": "aj_x", "status": "running"}]

    monkeypatch.setattr(J, "read_jobs", fake_read_jobs)
    j = _client().get("/autonomy/jobs", params={"limit": 5}).json()
    assert j["ok"] is True
    assert j["state"]["job_id"] == "aj_x"
    assert j["jobs"] == [{"job_id": "aj_x", "status": "running"}]
    assert seen == {"limit": 5, "running_job_id": "aj_x"}


def test_jobs_ep_passes_none_running_id_when_idle(monkeypatch):
    """非 running → 绝不把陈旧 job_id 冒充当前 running 传给 read_jobs。"""
    import guanlan_v2.autonomy.jobs as J
    import guanlan_v2.autonomy.runtime as RT

    monkeypatch.setattr(RT, "_autonomy_public_state",
                        lambda: {"running": False, "job_id": "aj_old"})
    seen = {}
    monkeypatch.setattr(
        J, "read_jobs",
        lambda limit, running_job_id=None: seen.setdefault("running_job_id", running_job_id) or [])
    j = _client().get("/autonomy/jobs").json()
    assert j["ok"] is True
    assert seen["running_job_id"] is None


def test_report_latest_ep_passthrough_ok(monkeypatch):
    import guanlan_v2.autonomy.review_officer as RO

    monkeypatch.setattr(
        RO, "read_report",
        lambda date="": {"ok": True, "date": "2026-07-11", "md": "# x", "json": {"a": 1}})
    r = _client().get("/autonomy/report/latest", params={"date": "2026-07-11"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "date": "2026-07-11", "md": "# x", "json": {"a": 1}}


def test_report_latest_ep_no_report_honest(monkeypatch):
    """无报告 → read_report 的 reason 直接透传,HTTP 恒 200(诚实,不编造)。"""
    import guanlan_v2.autonomy.review_officer as RO

    monkeypatch.setattr(RO, "read_report", lambda date="": {"ok": False, "reason": "no_report"})
    r = _client().get("/autonomy/report/latest")
    assert r.status_code == 200
    assert r.json() == {"ok": False, "reason": "no_report"}


def test_run_ep_defaults_playbook_to_review_officer(monkeypatch):
    import guanlan_v2.autonomy.runtime as RT

    seen = {}

    def fake_start(playbook):
        seen["playbook"] = playbook
        return {"ok": True, "job_id": "aj_new"}

    monkeypatch.setattr(RT, "start_job_bg", fake_start)
    j = _client().post("/autonomy/run", json={}).json()
    assert j == {"ok": True, "job_id": "aj_new"}
    assert seen["playbook"] == "review_officer"


def test_run_ep_passes_through_explicit_playbook_and_failure(monkeypatch):
    """未知 playbook 一路透传 start_job_bg 的诚实拒绝(校验落在 start_job_bg 内,端点不重复判)。"""
    import guanlan_v2.autonomy.runtime as RT

    seen = {}

    def fake_start(playbook):
        seen["playbook"] = playbook
        return {"ok": False, "reason": "unknown_playbook"}

    monkeypatch.setattr(RT, "start_job_bg", fake_start)
    j = _client().post("/autonomy/run", json={"playbook": "bogus"}).json()
    assert j == {"ok": False, "reason": "unknown_playbook"}
    assert seen["playbook"] == "bogus"


# ── maybe_enqueue_daily_review:三门(env/note/当日未跑)+ 全过排队 ─────────

def test_maybe_enqueue_env_off_no_enqueue(monkeypatch):
    import guanlan_v2.autonomy.runtime as RT

    monkeypatch.delenv("GUANLAN_REVIEW_DAILY", raising=False)
    called = []
    monkeypatch.setattr(RT, "start_job_bg", lambda pb: called.append(pb) or {"ok": True})
    assert RT.maybe_enqueue_daily_review("daily-scheduler") is False
    assert called == []


def test_maybe_enqueue_note_not_daily_scheduler_no_enqueue(monkeypatch):
    import guanlan_v2.autonomy.runtime as RT

    monkeypatch.setenv("GUANLAN_REVIEW_DAILY", "1")
    called = []
    monkeypatch.setattr(RT, "start_job_bg", lambda pb: called.append(pb) or {"ok": True})
    assert RT.maybe_enqueue_daily_review("manual") is False
    assert called == []


def test_maybe_enqueue_already_ran_today_no_enqueue(monkeypatch):
    import guanlan_v2.autonomy.jobs as J
    import guanlan_v2.autonomy.runtime as RT

    monkeypatch.setenv("GUANLAN_REVIEW_DAILY", "1")
    today = dt.date.today().isoformat()
    monkeypatch.setattr(
        J, "read_jobs",
        lambda limit=20, running_job_id=None: [
            {"job_id": "aj_1", "playbook": "review_officer",
             "started_ts": f"{today}T09:00:00", "status": "done"}])
    called = []
    monkeypatch.setattr(RT, "start_job_bg", lambda pb: called.append(pb) or {"ok": True})
    assert RT.maybe_enqueue_daily_review("daily-scheduler") is False
    assert called == []


def test_maybe_enqueue_all_gates_pass_enqueues(monkeypatch):
    import guanlan_v2.autonomy.jobs as J
    import guanlan_v2.autonomy.runtime as RT

    monkeypatch.setenv("GUANLAN_REVIEW_DAILY", "1")
    monkeypatch.setattr(J, "read_jobs", lambda limit=20, running_job_id=None: [])
    called = []
    monkeypatch.setattr(
        RT, "start_job_bg",
        lambda pb: called.append(pb) or {"ok": True, "job_id": "aj_new"})
    assert RT.maybe_enqueue_daily_review("daily-scheduler") is True
    assert called == ["review_officer"]


def test_maybe_enqueue_ignores_other_days_and_non_terminal_status(monkeypatch):
    """守卫细节:昨日的 job(哪怕 done)不算「今日已跑」;今日但 status=failed/interrupted
    的 job 也不拦(允许补跑)——只有 done/running 才拦。"""
    import guanlan_v2.autonomy.jobs as J
    import guanlan_v2.autonomy.runtime as RT

    monkeypatch.setenv("GUANLAN_REVIEW_DAILY", "1")
    today = dt.date.today().isoformat()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        J, "read_jobs",
        lambda limit=20, running_job_id=None: [
            {"job_id": "aj_0", "playbook": "review_officer",
             "started_ts": f"{yesterday}T09:00:00", "status": "done"},
            {"job_id": "aj_2", "playbook": "review_officer",
             "started_ts": f"{today}T08:00:00", "status": "failed"}])
    called = []
    monkeypatch.setattr(
        RT, "start_job_bg",
        lambda pb: called.append(pb) or {"ok": True, "job_id": "aj_new"})
    assert RT.maybe_enqueue_daily_review("daily-scheduler") is True
    assert called == ["review_officer"]


def test_maybe_enqueue_swallows_exceptions(monkeypatch):
    """函数体自吞异常返回 False(调度钩绝不抛)。"""
    import guanlan_v2.autonomy.jobs as J
    import guanlan_v2.autonomy.runtime as RT

    monkeypatch.setenv("GUANLAN_REVIEW_DAILY", "1")

    def boom(limit=20, running_job_id=None):
        raise RuntimeError("read_jobs 炸了")

    monkeypatch.setattr(J, "read_jobs", boom)
    assert RT.maybe_enqueue_daily_review("daily-scheduler") is False


# ── rescore._run_thread finally 钩子:仅 ok 分支才排队 ────────────────────

def _reset_rescore_state():
    import guanlan_v2.screen.rescore as rs
    with rs._RESCORE_LOCK:
        rs._RESCORE_STATE.update(
            running=True, phase="starting", label="", run_id=None,
            started_at=None, ended_at=None, ok=None, error=None, lines=[])


def test_rescore_hook_fires_only_on_ok_branch(monkeypatch):
    import guanlan_v2.autonomy.runtime as RT
    import guanlan_v2.screen.rescore as rs

    calls = []
    monkeypatch.setattr(RT, "maybe_enqueue_daily_review",
                        lambda note: calls.append(note) or True)

    monkeypatch.setattr(
        rs, "run_rescore",
        lambda run_id, top_n, note, progress, model="prod": {"ok": True, "run_id": run_id})
    _reset_rescore_state()
    rs._run_thread("rs_ok", 10, "daily-scheduler", "prod")
    assert calls == ["daily-scheduler"]

    calls.clear()
    monkeypatch.setattr(
        rs, "run_rescore",
        lambda run_id, top_n, note, progress, model="prod": {"ok": False, "error": "boom"})
    _reset_rescore_state()
    rs._run_thread("rs_fail", 10, "daily-scheduler", "prod")
    assert calls == []                      # 失败 run 绝不排队


def test_rescore_hook_swallows_exceptions_and_still_clears_running(monkeypatch):
    """钩子内部异常绝不冒出到 _run_thread,finally 清 running 不受影响(自审重点①)。"""
    import guanlan_v2.autonomy.runtime as RT
    import guanlan_v2.screen.rescore as rs

    def boom(note):
        raise RuntimeError("排队炸了")

    monkeypatch.setattr(RT, "maybe_enqueue_daily_review", boom)
    monkeypatch.setattr(
        rs, "run_rescore",
        lambda run_id, top_n, note, progress, model="prod": {"ok": True, "run_id": run_id})
    _reset_rescore_state()
    rs._run_thread("rs_boom", 10, "daily-scheduler", "prod")   # 绝不抛
    with rs._RESCORE_LOCK:
        assert rs._RESCORE_STATE["running"] is False
        assert rs._RESCORE_STATE["ok"] is True


# ── /screen/health:review_scheduler 键(合镜 rerank_scheduler 先例)───────

def test_screen_health_has_review_scheduler_block(monkeypatch):
    from guanlan_v2.screen.api import build_screen_router

    monkeypatch.setenv("GUANLAN_REVIEW_DAILY", "1")
    app = FastAPI()
    app.include_router(build_screen_router())
    j = TestClient(app).get("/screen/health").json()
    assert "review_scheduler" in j
    assert j["review_scheduler"] == {
        "enabled": True, "requires": "GUANLAN_RERANK_DAILY=1(随重排落定后排队)"}


def test_screen_health_review_scheduler_defaults_off(monkeypatch):
    from guanlan_v2.screen.api import build_screen_router

    monkeypatch.delenv("GUANLAN_REVIEW_DAILY", raising=False)
    app = FastAPI()
    app.include_router(build_screen_router())
    j = TestClient(app).get("/screen/health").json()
    assert j["review_scheduler"]["enabled"] is False
