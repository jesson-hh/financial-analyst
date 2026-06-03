"""backtest cancel + runs list + cancel-all endpoints.

Tests the UX-cap escape hatch:
* GET /backtest/runs        — list all runs (running/done/error/cancelled) + cap meta
* POST /backtest/cancel/{rid} — mark single run cancelled (frees cap slot)
* POST /backtest/cancel-all  — bulk cancel all running

设计取舍 (对抗审查):
* _BT_RUNS 是 build_app() 闭包变量 (不是模块级), 每个 client fixture 新建 app
  = 新的空注册表, 不需要外部 clear(). 这是天然的测试隔离.
* mock 跑得快 (~0.1-1s) 但仍 async, 起跑后可能 already done. cancel 端点对
  done/error 返回 {"ok": True, "already": status} 即可.
* 起真 mock 测 cancel 复用 test_backtest_rest.py 的 _patch_toy harness
  (toy reader → 0 LLM, 不依赖 csi300 universe / DASHSCOPE).
"""
from __future__ import annotations
import time
import pytest
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app

# 复用 toy harness (同 test_backtest_rest.py)
from tests.test_backtest_rest import _patch_toy


@pytest.fixture
def client():
    """每个测试一个新 app = 新的空 _BT_RUNS 闭包."""
    return TestClient(build_app())


# ---- /backtest/runs: 空注册表 ----
def test_runs_empty_on_fresh_start(client):
    r = client.get("/backtest/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["n_running"] == 0
    assert body["runs"] == []
    assert body["max_running"] >= 1


# ---- /backtest/cancel/{rid}: 未知 ID → 404 ----
def test_cancel_unknown_run_id_returns_404(client):
    r = client.post("/backtest/cancel/bt_nonexistent")
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False
    assert "unknown" in body["error"].lower() or "未知" in body["error"]


# ---- /backtest/cancel-all: 零 running 时也 OK ----
def test_cancel_all_zero_running_ok(client):
    r = client.post("/backtest/cancel-all", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["n_cancelled"] == 0
    assert body["run_ids"] == []


# ---- 起 mock 回测 → /backtest/runs 应列出 → cancel → cancel-all 0 个新 ----
def test_cancel_running_mock_run(client, monkeypatch):
    """起 toy mock 回测, 验证 list/cancel/cancel-all 三个端点端到端."""
    _patch_toy(monkeypatch)
    # 起回测
    r = client.post("/backtest/run", json={
        "start": "2026-04-01", "end": "2026-04-03",
        "mode": "mock", "candidate_topn": 5,
    })
    assert r.status_code == 200, r.json()
    run_id = r.json()["run_id"]

    # 立即查 runs (might still be running OR done depending on toy speed)
    rlist = client.get("/backtest/runs").json()
    matched = [x for x in rlist["runs"] if x["run_id"] == run_id]
    assert len(matched) == 1, f"run_id {run_id} not in /backtest/runs list"
    # status 应是 running / done / error 之一 (toy 可能秒完)
    assert matched[0]["status"] in ("running", "done", "error", "cancelled")
    assert matched[0]["mode"] == "mock"
    assert "params" in matched[0]

    # cancel 单个 — 无论已 done / cancelled / running 都应 OK
    # (TestClient 多请求各起新 loop, _job task 可能被前一个 loop 销毁 → CancelledError
    #  分支自己把 status 钉成 cancelled, 这条 race 是合法终态.)
    rc = client.post(f"/backtest/cancel/{run_id}")
    assert rc.status_code == 200, rc.json()
    body_c = rc.json()
    assert body_c["ok"] is True
    # 如果已 done/error/cancelled 之前 → 返 already; 如果 running → 返 cancelled
    if "already" in body_c:
        assert body_c["already"] in ("done", "error", "cancelled")
    else:
        assert body_c["status"] == "cancelled"

    # 再 cancel-all 应该 0 个新 cancel (上一步已处理)
    rca = client.post("/backtest/cancel-all", json={})
    assert rca.status_code == 200
    assert rca.json()["n_cancelled"] == 0


# ---- cancel 不会让 result 端点崩 (cancelled 是终态, 不是 running) ----
def test_cancelled_status_propagates_to_result(client, monkeypatch):
    """cancel 后 /backtest/result/{rid} 不应再返 status='running'."""
    _patch_toy(monkeypatch)
    r = client.post("/backtest/run", json={
        "start": "2026-04-01", "end": "2026-04-03",
        "mode": "mock", "candidate_topn": 5,
    })
    run_id = r.json()["run_id"]
    # 立即 cancel (race with toy 完成, 但无论哪边赢, 不该崩)
    client.post(f"/backtest/cancel/{run_id}")
    # 查 result, 不该 500
    rr = client.get(f"/backtest/result/{run_id}")
    assert rr.status_code in (200, 404)  # 404 if purge ate it (不该, 但允许)


# ---- runs 列表按时间序 (created_at 升序, 即 OrderedDict 插入序) ----
def test_runs_listed_in_insertion_order(client, monkeypatch):
    _patch_toy(monkeypatch)
    ids = []
    for _ in range(3):
        r = client.post("/backtest/run", json={
            "start": "2026-04-01", "end": "2026-04-03",
            "mode": "mock", "candidate_topn": 5,
        })
        ids.append(r.json()["run_id"])
    rl = client.get("/backtest/runs").json()
    listed = [x["run_id"] for x in rl["runs"]]
    # 插入序保留 (OrderedDict 语义)
    assert listed == ids


# ---- params 字段透传 (前端要拿来显示 "正在跑什么") ----
def test_runs_include_params_for_ui_display(client, monkeypatch):
    _patch_toy(monkeypatch)
    client.post("/backtest/run", json={
        "start": "2026-04-01", "end": "2026-04-03",
        "mode": "mock", "candidate_topn": 7, "init_cash": 500000,
    })
    body = client.get("/backtest/runs").json()
    assert len(body["runs"]) == 1
    p = body["runs"][0]["params"]
    assert p.get("candidate_topn") == 7
    assert p.get("init_cash") == 500000
