"""研究回路端点+状态机单测(P2 §3):裸 FastAPI 挂 router;loop 主体打桩,不跑真 LLM。"""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.research.api as rapi


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(rapi.build_research_router())
    return TestClient(app)


def _reset_state(monkeypatch):
    monkeypatch.setattr(rapi, "_RESEARCH_STATE", {
        "running": False, "phase": "idle", "label": "", "round_k": 0, "total_rounds": 0,
        "run_id": None, "started_at": None, "ended_at": None, "ok": None, "error": None,
        "lines": []})


def test_start_requires_goal(monkeypatch):
    _reset_state(monkeypatch)
    j = _client().post("/research/loop/start", json={"goal": "  "}).json()
    assert j["ok"] is False and "goal" in j["reason"]


def test_start_rejects_bad_universe(monkeypatch):
    _reset_state(monkeypatch)
    j = _client().post("/research/loop/start",
                       json={"goal": "找反转", "universe": "csi300"}).json()   # csi300 非法(是 benchmark id)
    assert j["ok"] is False and "universe" in j["reason"]


def test_start_clamps_and_runs(monkeypatch):
    _reset_state(monkeypatch)
    seen = {}

    def fake_loop(run_id, goal, max_rounds, min_rank_ic, universe, freq, start, end, progress):
        seen.update(run_id=run_id, goal=goal, max_rounds=max_rounds, min_rank_ic=min_rank_ic)
        progress(phase="evaluate", label="② 第 1/1 轮…", round_k=0)
        return {"ok": True}

    monkeypatch.setattr(rapi.rloop, "run_research_loop", fake_loop)
    j = _client().post("/research/loop/start",
                       json={"goal": "找反转", "max_rounds": 99, "min_rank_ic": 9.9,
                             "universe": "csi_fast"}).json()
    assert j["ok"] is True and j["run_id"].startswith("rr_")
    for _ in range(50):                                              # 等 daemon 线程收工
        time.sleep(0.02)
        if not rapi._research_public_state()["running"]:
            break
    st = rapi._research_public_state()
    assert st["phase"] == "done" and st["ok"] is True
    assert seen["max_rounds"] == 5 and seen["min_rank_ic"] == 0.2    # 服务端钳制
    assert any("第 1/1 轮" in ln for ln in st["lines"])              # progress 进 lines


def test_start_single_flight(monkeypatch):
    _reset_state(monkeypatch)
    with rapi._RESEARCH_LOCK:
        rapi._RESEARCH_STATE["running"] = True
    j = _client().post("/research/loop/start", json={"goal": "找反转"}).json()
    assert j["ok"] is False and j["reason"] == "already_running"
    with rapi._RESEARCH_LOCK:
        rapi._RESEARCH_STATE["running"] = False


def test_loop_thread_crash_clears_running(monkeypatch):
    _reset_state(monkeypatch)

    def boom(**kw):
        raise RuntimeError("炸")

    monkeypatch.setattr(rapi.rloop, "run_research_loop", boom)
    j = _client().post("/research/loop/start", json={"goal": "找反转"}).json()
    assert j["ok"] is True
    for _ in range(50):
        time.sleep(0.02)
        if not rapi._research_public_state()["running"]:
            break
    st = rapi._research_public_state()
    assert st["running"] is False and st["phase"] == "error" and "炸" in st["error"]


def test_status_and_archive_endpoints(monkeypatch, tmp_path):
    _reset_state(monkeypatch)
    import guanlan_v2.research.store as rs
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "ROUNDS_PATH", tmp_path / "rounds.jsonl")
    rs.append_run({"run_id": "rr_a", "kind": "start", "goal": "x", "ts": "t"})
    rs.append_round({"run_id": "rr_a", "k": 0, "diag": "初始"})
    c = _client()
    assert c.get("/research/loop/status").json()["state"]["phase"] == "idle"
    j = c.get("/research/runs").json()
    assert j["ok"] is True and j["runs"][0]["status"] == "interrupted"   # 无终态且不在跑 → 中断显形
    j2 = c.get("/research/rounds?run_id=rr_a").json()
    assert j2["ok"] is True and j2["n"] == 1
