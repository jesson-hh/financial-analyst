# -*- coding: utf-8 -*-
"""P5 再打分端点+状态机单测:裸 FastAPI 挂 router;run 主体打桩。零网络。"""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import guanlan_v2.screen.rescore as rs


def _client():
    app = FastAPI()
    app.include_router(rs.build_rescore_router())
    return TestClient(app)


def _reset(monkeypatch):
    monkeypatch.setattr(rs, "_RESCORE_STATE", {
        "running": False, "phase": "idle", "label": "", "run_id": None,
        "started_at": None, "ended_at": None, "ok": None, "error": None, "lines": []})


def test_run_rescore_end_to_end_rows(monkeypatch, tmp_path):
    """run 主体:池→链分→情绪→综合→落档,行形完整。"""
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n: [{"code": "SH1", "v4pct": 90.0},
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
    latest = rs.read_latest()
    assert latest["run_id"] == "rs_t1" and latest["note"] == "测试"


def test_run_rescore_board_fail_honest(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rs, "v4_pool", lambda n: [{"code": "SH1", "v4pct": 90.0}])

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

    def fake_run(run_id, top_n, note, progress):
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
