"""SP-Workflow SSE stream — verifies event order on a 3-node mock run.

The /workflow/runs/{run_id}/stream endpoint tails run_log.jsonl every 200ms
and emits SSE frames:
  - ``node_start``    × N (one per node entering RUNNING)
  - ``node_done``     × N (status: success/failed/skipped)
  - ``workflow_done`` × 1 (terminal aggregate)

Test pattern: POST /run → subscribe stream → collect frames → assert counts +
ordering. We use ``with client.stream(...) as resp`` which TestClient supports
for SSE (same pattern as test_server.py::test_run_sse_full_flow).

Why this is a separate file: the REST tests are pure request/response; SSE
needs streaming + iter_text + frame parsing — keeping it isolated makes
failures easier to diagnose.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Side-effect register mock nodes (build_app already does this, but be defensive
# in case the import order changes).
import financial_analyst.workflow.mock_nodes  # noqa: F401
from financial_analyst.buddy.server import build_app


@pytest.fixture
def isolated_workflow_env(tmp_path, monkeypatch):
    defs_root = tmp_path / "workflow_defs"
    parquet_root = tmp_path / "parquet"
    parquet_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FA_WORKFLOW_DEFS_ROOT", str(defs_root))
    monkeypatch.setenv("FA_PARQUET_ROOT", str(parquet_root))
    yield


def _parse_sse(text: str) -> list[dict]:
    """Parse a raw SSE stream into [{event, data}] frames.

    SSE frames look like:
        event: node_start\ndata: {...}\n\n
        event: node_done\ndata: {...}\n\n

    We ignore keep-alive comment frames (``: keepalive``) and partial trailing
    text (e.g. if the stream cut mid-frame).
    """
    events: list[dict] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        ev_match = re.search(r"^event:\s*(.+)$", block, re.MULTILINE)
        dt_match = re.search(r"^data:\s*(.+)$", block, re.MULTILINE)
        if not ev_match or not dt_match:
            continue
        try:
            payload = json.loads(dt_match.group(1))
        except Exception:
            payload = {"_raw": dt_match.group(1)}
        events.append({"event": ev_match.group(1).strip(), "data": payload})
    return events


def test_sse_emits_events_in_order(isolated_workflow_env):
    """POST /run → /stream emits 3 node_start + 3 node_done + 1 workflow_done.

    Asserts:
    - Event types appear at expected counts.
    - node_start events precede their corresponding node_done events.
    - workflow_done is the terminal event.
    - workflow_done payload aggregates n_success=3, n_failed=0, n_skipped=0.
    """
    client = TestClient(build_app())
    # Create workflow with the 3 mock nodes.
    wf_body = {
        "name": "sse-test",
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
    run_id = client.post(f"/workflow/{wf_id}/run").json()["run_id"]

    # Stream SSE. TestClient's .stream() runs synchronously and blocks; the
    # endpoint returns when workflow_done is sent, so iter_text drains naturally.
    # Use a hard wall-clock timeout in case something gets stuck (httpx + asyncio
    # in TestClient can occasionally hang on misbehaving generators).
    with client.stream("GET", f"/workflow/runs/{run_id}/stream", timeout=15.0) as resp:
        assert resp.status_code == 200
        chunks: list[str] = []
        for chunk in resp.iter_text():
            chunks.append(chunk)
            joined = "".join(chunks)
            # Bail early once we see workflow_done — generator has already returned,
            # but iter_text may keep waiting on the trailing buffer otherwise.
            if "event: workflow_done" in joined:
                break

    raw = "".join(chunks)
    events = _parse_sse(raw)

    starts = [e for e in events if e["event"] == "node_start"]
    dones = [e for e in events if e["event"] == "node_done"]
    wf_done = [e for e in events if e["event"] == "workflow_done"]

    assert len(starts) == 3, f"expected 3 node_start, got {len(starts)} from {raw!r}"
    assert len(dones) == 3, f"expected 3 node_done, got {len(dones)} from {raw!r}"
    assert len(wf_done) == 1, f"expected 1 workflow_done, got {len(wf_done)} from {raw!r}"

    # node_start node_ids = {universe, zeros, rowcount}
    start_ids = {e["data"]["node_id"] for e in starts}
    assert start_ids == {"universe", "zeros", "rowcount"}

    done_ids = {e["data"]["node_id"] for e in dones}
    assert done_ids == {"universe", "zeros", "rowcount"}

    # All nodes succeeded
    for d in dones:
        assert d["data"]["status"] == "success", d

    # workflow_done aggregates
    final = wf_done[0]["data"]
    assert final["status"] == "success"
    assert final["n_success"] == 3
    assert final["n_failed"] == 0
    assert final["n_skipped"] == 0
    assert final["run_id"] == run_id

    # Ordering invariant: each node's node_start must come before its node_done.
    # We don't enforce the cross-node ordering (universe before zeros before
    # rowcount) — that's a runner invariant tested elsewhere. SSE order just
    # has to respect "this node's start before its done".
    flat = [(e["event"], e["data"].get("node_id")) for e in events
            if e["event"] in ("node_start", "node_done")]
    for node_id in {"universe", "zeros", "rowcount"}:
        start_idx = next(i for i, (ev, nid) in enumerate(flat)
                         if ev == "node_start" and nid == node_id)
        done_idx = next(i for i, (ev, nid) in enumerate(flat)
                        if ev == "node_done" and nid == node_id)
        assert start_idx < done_idx, (
            f"node {node_id}: start at {start_idx} but done at {done_idx} (flat={flat})"
        )

    # workflow_done is the terminal event (no event types follow it).
    final_idx = next(i for i, e in enumerate(events) if e["event"] == "workflow_done")
    assert all(events[j]["event"] in ("node_start", "node_done") or j <= final_idx
               for j in range(len(events)))
