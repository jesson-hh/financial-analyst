"""Phase 0 — Workflow schema 形状契约测试.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §3.

JSON 形状是契约, Python 类名 / 模块路径变更不应该破坏存盘的 workflow 文件。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from financial_analyst.workflow.schema import (
    Edge,
    Node,
    NodeRun,
    NodeStatus,
    RunResult,
    Workflow,
)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


def test_workflow_model_validate_accepts_minimal_dict() -> None:
    """合法 JSON dict 能 round-trip."""
    payload = {
        "id": "wf_test",
        "name": "minimal",
        "nodes": [{"id": "a", "type": "data.constant_universe"}],
    }
    wf = Workflow.model_validate(payload)
    assert wf.id == "wf_test"
    assert wf.name == "minimal"
    assert wf.version == 1  # default
    assert len(wf.nodes) == 1
    assert wf.nodes[0].type == "data.constant_universe"
    assert wf.nodes[0].params == {}
    assert wf.nodes[0].inputs == {}
    assert wf.edges == []
    assert wf.meta == {}


def test_workflow_rejects_empty_nodes() -> None:
    """nodes 必须 len >= 1 (spec §3.4)."""
    with pytest.raises(ValidationError):
        Workflow.model_validate({"id": "wf", "name": "n", "nodes": []})


def test_workflow_rejects_missing_nodes_field() -> None:
    with pytest.raises(ValidationError):
        Workflow.model_validate({"id": "wf", "name": "n"})


def test_workflow_meta_accepts_arbitrary_dict() -> None:
    wf = Workflow.model_validate(
        {
            "id": "wf",
            "name": "n",
            "nodes": [{"id": "a", "type": "t"}],
            "meta": {"owner": "alice", "created_at": "2026-06-02"},
        }
    )
    assert wf.meta["owner"] == "alice"


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def test_node_requires_id_and_type() -> None:
    with pytest.raises(ValidationError):
        Node.model_validate({"id": "a"})  # missing type
    with pytest.raises(ValidationError):
        Node.model_validate({"type": "t"})  # missing id


def test_node_inputs_dict_form() -> None:
    n = Node.model_validate(
        {"id": "b", "type": "factor.zeros", "inputs": {"universe": "a.output"}}
    )
    assert n.inputs == {"universe": "a.output"}


# ---------------------------------------------------------------------------
# Edge — `from` 是 Python 关键字, 用 alias
# ---------------------------------------------------------------------------


def test_edge_from_alias_roundtrip() -> None:
    """JSON 字段 `from` <-> Python 属性 `from_` 双向."""
    e = Edge.model_validate({"from": "a.output", "to": "b.universe"})
    assert e.from_ == "a.output"
    assert e.to == "b.universe"
    # Dump back to JSON dict — alias 应当被保留
    dumped = e.model_dump(by_alias=True)
    assert dumped == {"from": "a.output", "to": "b.universe"}


def test_edge_populate_by_name() -> None:
    """由 Python 侧构造时也能用 from_ 关键字."""
    e = Edge(from_="a.x", to="b.y")
    assert e.from_ == "a.x"
    assert e.to == "b.y"


# ---------------------------------------------------------------------------
# NodeRun
# ---------------------------------------------------------------------------


def test_node_run_json_roundtrip_preserves_enum_as_string() -> None:
    """run_log.jsonl 是字符串文件, NodeRun 必须能 json round-trip."""
    nr = NodeRun(
        run_id="r1",
        workflow_id="wf",
        node_id="a",
        node_type="data.constant_universe",
        status=NodeStatus.SUCCESS,
        input_hash="0123456789abcdef",
        output_artifact_uri="workflow_runs/r1/nodes/a/output.json",
        started_at="2026-06-02T01:00:00Z",
        ended_at="2026-06-02T01:00:01Z",
        duration_ms=1000,
        error=None,
    )
    js = nr.model_dump_json()
    payload = json.loads(js)
    # Enum 必须落字符串 (run_log.jsonl 跨进程读不出 Enum)
    assert payload["status"] == "success"
    back = NodeRun.model_validate(payload)
    assert back == nr


def test_node_run_minimal_pending() -> None:
    """PENDING 阶段 ended_at / duration_ms / output_artifact_uri 都可空."""
    nr = NodeRun(
        run_id="r1",
        workflow_id="wf",
        node_id="a",
        node_type="t",
        status=NodeStatus.PENDING,
        started_at="2026-06-02T01:00:00Z",
    )
    assert nr.ended_at is None
    assert nr.duration_ms is None
    assert nr.output_artifact_uri is None
    assert nr.error is None
    assert nr.input_hash is None


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------


def test_run_result_minimal() -> None:
    rr = RunResult(
        run_id="r1",
        workflow_id="wf",
        status=NodeStatus.SUCCESS,
        node_runs=[],
        artifacts_root="/tmp/workflow_runs/r1",
    )
    assert rr.status == NodeStatus.SUCCESS
    assert rr.node_runs == []
