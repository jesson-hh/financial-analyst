"""SP-Workflow REST endpoints on buddy/server.py build_app().

Eight workflow endpoints exercise the Phase 0 workflow framework
(WorkflowRunner / ArtifactStore / RunLog / NodeRegistry) via HTTP and a TestClient.

Pattern parallels test_factor_rest.py:
- Each test starts a fresh ``build_app()`` so module state is clean.
- ``monkeypatch.setenv("FA_WORKFLOW_DEFS_ROOT", tmp_path/...)`` + ``FA_PARQUET_ROOT`` redirects
  workflow defs + run store to tmp so the dev fallback path
  (``G:/stocks/stock_data/...``) never gets touched by CI / fresh boxes.
- Mock node side-effect import (mock_nodes) happens automatically inside
  build_app(), so ``GET /workflow/nodes`` returns the 3 demo nodes.

The conftest fixture ``_ci_safe_defaults`` already patches ``find_config`` to a
fake yaml under tmp; we don't need to override FA_PARQUET_ROOT because the yaml
already points parquet_root at the fake root. But we DO want isolation: the
workflow_defs / workflow_runs directories must live under the per-test tmp_path,
so we set FA_WORKFLOW_DEFS_ROOT explicitly and use a tmp path for parquet too.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Trigger @node side-effect registration so build_app's NodeRegistry.list()
# returns the 3 mock nodes. Harmless if already registered (module cache makes
# re-import a no-op).
import financial_analyst.workflow.mock_nodes  # noqa: F401
from financial_analyst.buddy.server import build_app


# ---------------------------------------------------------------------------
# Isolation fixture — point workflow_defs + workflow_runs at tmp_path so tests
# don't touch real data + don't clobber each other's seeds.
# ---------------------------------------------------------------------------
@pytest.fixture
def isolated_workflow_env(tmp_path, monkeypatch):
    """Redirect workflow root paths to per-test tmp.

    FA_WORKFLOW_DEFS_ROOT explicitly sets the defs dir. FA_PARQUET_ROOT also
    has to be set: the workflow store/runs root is derived from
    ``parquet_root.parent`` (so workflow_store/ sits beside parquet/). Without
    this, conftest's fake yaml points parquet_root at a shared CI tmp, which
    leaks runs between tests.
    """
    defs_root = tmp_path / "workflow_defs"
    parquet_root = tmp_path / "parquet"
    parquet_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FA_WORKFLOW_DEFS_ROOT", str(defs_root))
    monkeypatch.setenv("FA_PARQUET_ROOT", str(parquet_root))
    yield {
        "defs_root": defs_root,
        "parquet_root": parquet_root,
        "runs_root": tmp_path / "workflow_store" / "workflow_runs",
    }


def _client():
    return TestClient(build_app())


# ===========================================================================
# 1. GET /workflow/nodes — returns ≥3 mock nodes, each with params_schema.
# ===========================================================================
def test_list_nodes(isolated_workflow_env):
    client = _client()
    r = client.get("/workflow/nodes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "nodes" in body
    nodes = body["nodes"]
    assert isinstance(nodes, list)
    types = {n["type"] for n in nodes}
    assert {"data.constant_universe", "factor.zeros", "eval.row_count"} <= types

    # Each node entry has the documented shape
    for n in nodes:
        assert "type" in n
        assert "description" in n
        assert "params_schema" in n
        assert "outputs_schema" in n
        assert "risk" in n
        assert "pit" in n

    # data.constant_universe should have a params_schema with codes/array
    uni = next(n for n in nodes if n["type"] == "data.constant_universe")
    schema = uni["params_schema"]
    assert schema.get("type") == "object"
    assert "codes" in (schema.get("properties") or {})


# ===========================================================================
# 2. POST /workflow/create + GET /workflow/{wf_id} — round-trip.
# ===========================================================================
def test_create_get_workflow(isolated_workflow_env):
    client = _client()
    workflow_body = {
        "name": "test-roundtrip",
        "nodes": [
            {
                "id": "universe",
                "type": "data.constant_universe",
                "params": {"codes": ["SH600519", "SH600036"]},
            },
            {
                "id": "zeros",
                "type": "factor.zeros",
                "inputs": {"universe": "universe.output"},
            },
            {
                "id": "rowcount",
                "type": "eval.row_count",
                "inputs": {"frame": "zeros.output"},
            },
        ],
    }
    r = client.post("/workflow/create", json=workflow_body)
    assert r.status_code == 200, r.text
    wf_id = r.json()["wf_id"]
    assert isinstance(wf_id, str)
    assert len(wf_id) == 12  # uuid4 hex[:12]

    # Confirm file landed on disk
    defs_root = isolated_workflow_env["defs_root"]
    assert (defs_root / f"{wf_id}.json").exists()

    # GET reads it back
    r2 = client.get(f"/workflow/{wf_id}")
    assert r2.status_code == 200
    got = r2.json()
    assert got["id"] == wf_id
    assert got["name"] == "test-roundtrip"
    assert len(got["nodes"]) == 3
    assert got["nodes"][0]["id"] == "universe"


def test_get_workflow_404(isolated_workflow_env):
    client = _client()
    r = client.get("/workflow/nonexistent_id")
    assert r.status_code == 404
    assert "error" in r.json()


def test_create_workflow_validation_error(isolated_workflow_env):
    """Empty nodes list should fail Workflow.model_validate (min_length=1)."""
    client = _client()
    r = client.post("/workflow/create", json={"name": "empty", "nodes": []})
    # 422 from Pydantic ValidationError handler
    assert r.status_code in (400, 422), r.text


# ===========================================================================
# 3. POST /workflow/{wf_id}/run — fire async run, poll until done.
# ===========================================================================
def test_run_workflow_sync(isolated_workflow_env):
    """POST /run returns run_id; poll status until success."""
    client = _client()
    # Create workflow first
    wf_body = {
        "name": "demo-run",
        "nodes": [
            {
                "id": "universe",
                "type": "data.constant_universe",
                "params": {"codes": ["SH600519", "SH600036", "SH601318"]},
            },
            {
                "id": "zeros",
                "type": "factor.zeros",
                "inputs": {"universe": "universe.output"},
            },
            {
                "id": "rowcount",
                "type": "eval.row_count",
                "inputs": {"frame": "zeros.output"},
            },
        ],
    }
    wf_id = client.post("/workflow/create", json=wf_body).json()["wf_id"]

    # Run it
    r = client.post(f"/workflow/{wf_id}/run")
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert isinstance(run_id, str)
    assert len(run_id) == 12

    # Poll status with timeout
    deadline = time.monotonic() + 10.0
    final_status = None
    while time.monotonic() < deadline:
        s = client.get(f"/workflow/runs/{run_id}")
        if s.status_code == 200:
            body = s.json()
            if body["status"] in ("ok", "failed", "partial"):
                final_status = body
                break
        time.sleep(0.1)

    assert final_status is not None, "Run did not finish within 10s"
    assert final_status["status"] == "ok", final_status
    assert final_status["n_total"] == 3
    assert final_status["n_success"] == 3
    assert final_status["n_failed"] == 0
    assert final_status["n_skipped"] == 0
    assert final_status["wf_id"] == wf_id


def test_run_status_404(isolated_workflow_env):
    client = _client()
    r = client.get("/workflow/runs/nonexistent_run")
    assert r.status_code == 404


def test_run_workflow_unknown_wf_id_is_404(isolated_workflow_env):
    client = _client()
    r = client.post("/workflow/unknown_wf_id/run")
    assert r.status_code == 404


# ===========================================================================
# 4. GET /workflow/runs/{run_id}/logs + /artifacts/{node_id}
# ===========================================================================
def _create_and_run_demo(client):
    """Helper: create the demo 3-node chain, fire run, wait until done. Returns run_id."""
    wf_body = {
        "name": "demo-for-artifacts",
        "nodes": [
            {
                "id": "universe",
                "type": "data.constant_universe",
                "params": {"codes": ["SH600519", "SZ000858"]},
            },
            {
                "id": "zeros",
                "type": "factor.zeros",
                "inputs": {"universe": "universe.output"},
            },
            {
                "id": "rowcount",
                "type": "eval.row_count",
                "inputs": {"frame": "zeros.output"},
            },
        ],
    }
    wf_id = client.post("/workflow/create", json=wf_body).json()["wf_id"]
    run_id = client.post(f"/workflow/{wf_id}/run").json()["run_id"]
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        s = client.get(f"/workflow/runs/{run_id}").json()
        if s.get("status") in ("ok", "failed", "partial"):
            return run_id
        time.sleep(0.1)
    raise AssertionError(f"Demo run {run_id} did not finish in 10s")


def test_run_logs_artifacts(isolated_workflow_env):
    """After demo run finishes:
    - GET /logs returns 6 NodeRun entries (3 RUNNING + 3 SUCCESS)
    - GET /artifacts/rowcount returns {kind:"json", value:{rows,cols}}
    - GET /artifacts/zeros returns dataframe-shaped payload
    - GET /artifacts/universe returns {kind:"json", value:{codes,n}}
    """
    client = _client()
    run_id = _create_and_run_demo(client)

    # Logs
    r = client.get(f"/workflow/runs/{run_id}/logs")
    assert r.status_code == 200, r.text
    logs = r.json()["logs"]
    assert len(logs) == 6
    statuses = [e["status"] for e in logs]
    assert statuses.count("running") == 3
    assert statuses.count("success") == 3
    node_ids = {e["node_id"] for e in logs}
    assert node_ids == {"universe", "zeros", "rowcount"}

    # Artifact: rowcount (JSON)
    r2 = client.get(f"/workflow/runs/{run_id}/artifacts/rowcount")
    assert r2.status_code == 200
    body = r2.json()
    assert body["kind"] == "json"
    assert body["value"]["rows"] == 2
    assert body["value"]["cols"] == 2

    # Artifact: zeros (DataFrame → records)
    r3 = client.get(f"/workflow/runs/{run_id}/artifacts/zeros")
    assert r3.status_code == 200
    body = r3.json()
    assert body["kind"] == "dataframe"
    assert body["shape"] == [2, 2]
    assert set(body["columns"]) == {"code", "value"}
    assert len(body["records"]) == 2

    # Artifact: universe (JSON dict)
    r4 = client.get(f"/workflow/runs/{run_id}/artifacts/universe")
    assert r4.status_code == 200
    body = r4.json()
    assert body["kind"] == "json"
    assert body["value"] == {"codes": ["SH600519", "SZ000858"], "n": 2}

    # 404 for unknown node
    r5 = client.get(f"/workflow/runs/{run_id}/artifacts/nope")
    assert r5.status_code == 404


def test_logs_404(isolated_workflow_env):
    client = _client()
    r = client.get("/workflow/runs/nonexistent_run/logs")
    assert r.status_code == 404


# ===========================================================================
# 5. GET /workflow — list created workflows.
# ===========================================================================
def test_list_workflows(isolated_workflow_env):
    client = _client()
    # SP-W2C: GET /workflow lazily writes a demo seed (demo-mock-3-nodes.json) on
    # first access, so this list is non-empty after the first call.
    r0 = client.get("/workflow")
    assert r0.status_code == 200
    initial = r0.json()["workflows"]
    initial_ids = {w["wf_id"] for w in initial}
    assert "demo-mock-3-nodes" in initial_ids

    # Add 2 more
    body = {
        "name": "test-list-1",
        "nodes": [
            {"id": "u", "type": "data.constant_universe", "params": {"codes": ["A"]}},
        ],
    }
    wf1 = client.post("/workflow/create", json=body).json()["wf_id"]
    body["name"] = "test-list-2"
    wf2 = client.post("/workflow/create", json=body).json()["wf_id"]

    r = client.get("/workflow")
    assert r.status_code == 200
    listed = r.json()["workflows"]
    ids = {w["wf_id"] for w in listed}
    assert wf1 in ids
    assert wf2 in ids
    # Each entry has the documented shape
    for w in listed:
        assert "wf_id" in w
        assert "name" in w
        assert "mtime" in w
        assert "node_count" in w


# ===========================================================================
# 6. GET /workflow/runs — list completed runs.
# ===========================================================================
def test_list_runs(isolated_workflow_env):
    client = _client()
    run_id = _create_and_run_demo(client)
    r = client.get("/workflow/runs")
    assert r.status_code == 200, r.text
    runs = r.json()["runs"]
    ids = {x["run_id"] for x in runs}
    assert run_id in ids
    # Shape check
    for x in runs:
        assert "run_id" in x
        assert "wf_id" in x
        assert "mtime" in x
        assert "has_logs" in x


def test_list_runs_with_limit(isolated_workflow_env):
    client = _client()
    _create_and_run_demo(client)
    _create_and_run_demo(client)
    r = client.get("/workflow/runs?limit=1")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) == 1


# ===========================================================================
# 7. Demo seed — SP-W2C: lazy seed at first /workflow{,/{wf_id}} access.
# build_app() 不再 eager 写盘 (节省冷启动), 首次访问端点才落 demo-mock-3-nodes.json.
# ===========================================================================
def test_demo_seed_present(isolated_workflow_env):
    """When workflow_defs_root is empty, first GET /workflow/{wf_id} seeds demo.

    SP-W2C: lazy seed. build_app() 不写; GET /workflow 或 GET /workflow/{wf_id}
    第一次触发兜底种入. 这条测试覆盖 GET /workflow/{wf_id} 路径 (即使先没列过).
    """
    client = _client()
    r = client.get("/workflow/demo-mock-3-nodes")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "demo-mock-3-nodes"
    assert len(body["nodes"]) == 3
    # Demo can be run as-is
    rr = client.post("/workflow/demo-mock-3-nodes/run")
    assert rr.status_code == 200
    run_id = rr.json()["run_id"]
    # Poll till done
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        s = client.get(f"/workflow/runs/{run_id}")
        if s.status_code == 200 and s.json()["status"] in ("ok", "failed", "partial"):
            assert s.json()["status"] == "ok"
            return
        time.sleep(0.1)
    pytest.fail("Demo run did not finish")


def test_demo_seed_lazy_not_written_at_build(isolated_workflow_env):
    """SP-W2C: build_app() 自己**不**写 demo seed — defs_root 应是空的, 直到首请求.

    冷启动加速保障: 启动期不碰 *.json 文件系统. 这条测试通过断言"build_app()
    起飞后 defs_root 还是空目录"来锁死这个行为, 防回归.
    """
    from financial_analyst.buddy.server import build_app

    defs_root = isolated_workflow_env["defs_root"]
    # mkdir 由 build_app 做 (parents=True, exist_ok=True), 但内部不写任何 *.json
    _ = build_app()
    # build 后立即检查 — 还没有任何 HTTP 请求触发过 seed
    assert defs_root.exists(), "build_app should mkdir the defs_root"
    json_files = list(defs_root.glob("*.json"))
    assert not json_files, (
        f"build_app() 不应在 defs_root 写文件 (SP-W2C lazy seed), 实际找到: {json_files}"
    )


def test_demo_seed_after_list_endpoint(isolated_workflow_env):
    """SP-W2C: 首次 GET /workflow (列表) 触发 demo seed 写盘."""
    client = _client()
    defs_root = isolated_workflow_env["defs_root"]
    # build_app() 不写; /workflow 列表才触发
    assert not list(defs_root.glob("*.json")), "未请求前不应有 seed"
    r = client.get("/workflow")
    assert r.status_code == 200
    workflows = r.json()["workflows"]
    assert any(w["wf_id"] == "demo-mock-3-nodes" for w in workflows), (
        f"GET /workflow 应触发 demo seed, 实际 workflows={workflows}"
    )
    # 文件落盘
    assert (defs_root / "demo-mock-3-nodes.json").is_file()
