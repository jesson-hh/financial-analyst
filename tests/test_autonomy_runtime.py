# -*- coding: utf-8 -*-
import json
import time

from guanlan_v2.autonomy import jobs as J, runtime as R


def _iso(path, monkeypatch, tmp_path):
    monkeypatch.setattr(J, "JOBS_PATH", tmp_path / "jobs.jsonl")
    monkeypatch.setattr(J, "JOBS_DIR", tmp_path / "jobs")


def test_append_and_read_jobs_status(tmp_path, monkeypatch):
    _iso(J, monkeypatch, tmp_path)
    J.append_event({"job_id": "aj_a", "kind": "start", "playbook": "review_officer"})
    J.append_event({"job_id": "aj_a", "kind": "end", "ok": True})
    J.append_event({"job_id": "aj_b", "kind": "start", "playbook": "review_officer"})
    rows = J.read_jobs(limit=10)
    by = {r["job_id"]: r for r in rows}
    assert by["aj_a"]["status"] == "done"
    assert by["aj_b"]["status"] == "interrupted"     # 无 end 且非 running=重启即中断诚实显形
    assert rows[0]["job_id"] == "aj_b"               # 新在前


def test_read_jobs_running_marker(tmp_path, monkeypatch):
    _iso(J, monkeypatch, tmp_path)
    J.append_event({"job_id": "aj_c", "kind": "start", "playbook": "review_officer"})
    rows = J.read_jobs(limit=10, running_job_id="aj_c")
    assert rows[0]["status"] == "running"


def test_budget_charge_and_exhaust():
    b = R.Budget(max_llm=2)
    assert b.charge() and b.charge()
    assert not b.charge()
    assert b.exhausted and b.used == 2


def test_ctx_deadline():
    ctx = R.JobCtx(job_id="aj_x", dir=None, budget=R.Budget(1),
                   progress=lambda **k: None, deadline_ts=time.time() - 1)
    assert ctx.over_deadline()


def test_start_job_bg_single_flight_and_unknown(tmp_path, monkeypatch):
    _iso(J, monkeypatch, tmp_path)
    assert R.start_job_bg("no_such_playbook")["reason"] == "unknown_playbook"
    ran = {}

    def slow_pb(ctx):
        ran["hit"] = True
        time.sleep(0.3)
        return {"ok": True}

    monkeypatch.setitem(R._PLAYBOOKS_FOR_TEST(), "slow", slow_pb)
    r1 = R.start_job_bg("slow")
    assert r1["ok"] and r1["job_id"].startswith("aj_")
    r2 = R.start_job_bg("slow")
    assert r2["ok"] is False and r2["reason"] == "already_running"
    for _ in range(60):
        if not R._AUTONOMY_STATE["running"]:
            break
        time.sleep(0.05)
    assert ran.get("hit") and R._AUTONOMY_STATE["ok"] is True
    rows = J.read_jobs(limit=5)
    assert rows[0]["status"] == "done"


def test_job_thread_records_failure(tmp_path, monkeypatch):
    _iso(J, monkeypatch, tmp_path)

    def boom(ctx):
        raise RuntimeError("x")

    monkeypatch.setitem(R._PLAYBOOKS_FOR_TEST(), "boom", boom)
    R.start_job_bg("boom")
    for _ in range(60):
        if not R._AUTONOMY_STATE["running"]:
            break
        time.sleep(0.05)
    assert R._AUTONOMY_STATE["ok"] is False and "RuntimeError" in (R._AUTONOMY_STATE["error"] or "")
    assert J.read_jobs(limit=5)[0]["status"] == "failed"
