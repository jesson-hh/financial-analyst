# -*- coding: utf-8 -*-
"""Task 6 — ``NodeRun`` status / counter / attempt matrix (``schemas.py``).

Written test-first (RED until ``schemas.py`` exists). Locks the reviewed
per-status invariants of a node execution record:

* it is a frozen, strict :class:`DigestModel` that forbids extra fields;
* counters (``input_tokens`` / ``output_tokens``) are strict non-negative ints
  (``bool`` rejected) and ``attempt`` starts at 1 (``PositiveInt`` — ``0`` /
  negatives / ``bool`` rejected);
* it freezes the three typed evidence tuples (``tool_call_records`` /
  ``data_result_refs`` / ``execution_evidence_refs``) on **every** terminal
  status, with the shared canonical/main-only/duplicate-free invariants; the
  denormalized ``tool_call_count`` is gone (``len(tool_call_records)`` is the
  only truth);
* ``COMPLETED`` requires declared outputs (``output_keys`` non-empty with a
  matching count of ``output_artifact_ids``);
* ``FAILED`` / ``TIMED_OUT`` / ``CANCELLED`` each require a ``reason_code``;
* non-terminal statuses require neither outputs nor a reason code;
* ``finished_at`` may not precede ``started_at``.

Run from repo root: ``pytest tests/orchestration/test_node_run.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.enums import NodeStatus
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schemas import NodeRun, ToolCallRecord

UTC = timezone.utc


def _dt(hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=UTC)


def _typed_ref(
    *, name: str = "DataResult", object_id: str = "o1", namespace: str = "main",
    content: str = "2" * 64,
) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=name, version="1"),
        payload_ref=PayloadRef(namespace=namespace, object_id=object_id, content_digest=content),
    )


def _tool_call(*, call_ordinal: int = 1, result: str = "e" * 64) -> ToolCallRecord:
    return ToolCallRecord(
        call_ordinal=call_ordinal,
        tool_ref=CapabilityRef(id="news.search", version="1", content_digest="c" * 64),
        request_ref=_typed_ref(name="ToolRequest", object_id="req", content="d" * 64),
        result_ref=_typed_ref(name="ToolResult", object_id="res", content=result),
        call_id="call-1",
        started_at=_dt(9, 0),
        finished_at=_dt(9, 1),
    )


def _node_run(
    status: NodeStatus = NodeStatus.COMPLETED,
    *,
    reason_code: str | None = None,
    reason: str | None = None,
    attempt: int = 1,
    output_keys=None,
    output_artifact_ids=None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    tool_call_records=(),
    data_result_refs=(),
    execution_evidence_refs=(),
    **over,
) -> NodeRun:
    completed = status is NodeStatus.COMPLETED
    if output_keys is None:
        output_keys = ("report",) if completed else ()
    if output_artifact_ids is None:
        output_artifact_ids = ("art-1",) if completed else ()
    base = dict(
        node_run_id="nr-1",
        run_id="run-1",
        plan_id="plan-1",
        plan_digest="a" * 64,
        node_id="node.research",
        worker_id="worker.reader",
        status=status,
        reason_code=reason_code,
        reason=reason,
        attempt_id="att-1",
        attempt=attempt,
        input_snapshot_digest="1" * 64,
        started_at=started_at if started_at is not None else _dt(9, 0),
        finished_at=finished_at if finished_at is not None else _dt(9, 5),
        output_keys=output_keys,
        output_artifact_ids=output_artifact_ids,
        tool_call_records=tuple(tool_call_records),
        data_result_refs=tuple(data_result_refs),
        execution_evidence_refs=tuple(execution_evidence_refs),
        input_tokens=0,
        output_tokens=0,
        warnings=(),
        error_type=None,
    )
    base.update(over)
    return NodeRun(**base)


# --------------------------------------------------------------------------- #
# base contract                                                               #
# --------------------------------------------------------------------------- #
def test_node_run_is_a_digest_model():
    assert issubclass(NodeRun, DigestModel)


def test_happy_completed_node_run():
    r = _node_run(status=NodeStatus.COMPLETED)
    assert r.status is NodeStatus.COMPLETED
    assert r.attempt == 1
    assert r.output_keys == ("report",)


# --------------------------------------------------------------------------- #
# COMPLETED requires declared outputs                                         #
# --------------------------------------------------------------------------- #
def test_completed_requires_declared_outputs():
    with pytest.raises(ValidationError):
        _node_run(status=NodeStatus.COMPLETED, output_keys=(), output_artifact_ids=())


def test_completed_output_keys_and_ids_count_must_match():
    with pytest.raises(ValidationError):
        _node_run(
            status=NodeStatus.COMPLETED,
            output_keys=("a", "b"),
            output_artifact_ids=("art-1",),
        )


# --------------------------------------------------------------------------- #
# FAILED / TIMED_OUT / CANCELLED require a reason code                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status", [NodeStatus.FAILED, NodeStatus.TIMED_OUT, NodeStatus.CANCELLED]
)
def test_failure_statuses_require_reason_code(status):
    ok = _node_run(status=status, reason_code="budget_exhausted", reason="ran out")
    assert ok.reason_code == "budget_exhausted"
    with pytest.raises(ValidationError):
        _node_run(status=status, reason_code=None)


@pytest.mark.parametrize(
    "status", [NodeStatus.FAILED, NodeStatus.TIMED_OUT, NodeStatus.CANCELLED]
)
def test_failure_statuses_reject_blank_reason_code(status):
    with pytest.raises(ValidationError):
        _node_run(status=status, reason_code="   ")


# --------------------------------------------------------------------------- #
# non-terminal statuses need neither outputs nor a reason                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status",
    [
        NodeStatus.PENDING,
        NodeStatus.READY,
        NodeStatus.RUNNING,
        NodeStatus.DEGRADED,
        NodeStatus.INCOMPLETE,
        NodeStatus.BLOCKED,
        NodeStatus.SKIPPED,
    ],
)
def test_non_terminal_statuses_need_neither_outputs_nor_reason(status):
    r = _node_run(status=status, output_keys=(), output_artifact_ids=(), reason_code=None)
    assert r.status is status


# --------------------------------------------------------------------------- #
# attempt starts at 1 (PositiveInt)                                           #
# --------------------------------------------------------------------------- #
def test_attempt_defaults_to_one():
    r = _node_run(status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=())
    assert r.attempt == 1


@pytest.mark.parametrize("bad", [0, -1])
def test_attempt_must_be_positive(bad):
    with pytest.raises(ValidationError):
        _node_run(status=NodeStatus.RUNNING, attempt=bad, output_keys=(), output_artifact_ids=())


def test_attempt_bool_rejected():
    with pytest.raises(ValidationError):
        _node_run(status=NodeStatus.RUNNING, attempt=True, output_keys=(), output_artifact_ids=())


# --------------------------------------------------------------------------- #
# counters are strict non-negative ints                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_counters_reject_negative(field):
    with pytest.raises(ValidationError):
        _node_run(status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(), **{field: -1})


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_counters_reject_bool(field):
    with pytest.raises(ValidationError):
        _node_run(status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(), **{field: True})


# --------------------------------------------------------------------------- #
# wall-clock coherence                                                        #
# --------------------------------------------------------------------------- #
def test_finished_at_may_not_precede_started_at():
    with pytest.raises(ValidationError):
        _node_run(
            status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
            started_at=_dt(10, 0), finished_at=_dt(9, 0),
        )


# --------------------------------------------------------------------------- #
# extra field + mutation                                                      #
# --------------------------------------------------------------------------- #
def test_node_run_rejects_extra_field():
    with pytest.raises(ValidationError):
        _node_run(status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(), bogus=1)


def test_node_run_is_frozen():
    r = _node_run(status=NodeStatus.COMPLETED)
    with pytest.raises(ValidationError):
        r.status = NodeStatus.FAILED


# --------------------------------------------------------------------------- #
# typed evidence tuples (amendment 1) — frozen on every terminal status        #
# --------------------------------------------------------------------------- #
def test_tool_call_count_field_is_removed():
    # the denormalized counter is gone; a worker cannot self-report a count.
    with pytest.raises(ValidationError):
        _node_run(
            status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
            tool_call_count=1,
        )


@pytest.mark.parametrize(
    "status",
    [NodeStatus.INCOMPLETE, NodeStatus.FAILED, NodeStatus.TIMED_OUT, NodeStatus.CANCELLED],
)
def test_evidence_is_retained_on_every_terminal_status(status):
    # evidence cannot disappear because later prompt/model/output work failed:
    # a failed/incomplete run after a successful data/tool step still freezes
    # all three tuples.
    reason = None if status is NodeStatus.INCOMPLETE else "budget_exhausted"
    r = _node_run(
        status=status, reason_code=reason, output_keys=(), output_artifact_ids=(),
        tool_call_records=(_tool_call(call_ordinal=1),),
        data_result_refs=(_typed_ref(content="2" * 64),),
        execution_evidence_refs=(_typed_ref(name="PromptAssembly", content="5" * 64),),
    )
    assert len(r.tool_call_records) == 1
    assert len(r.data_result_refs) == 1
    assert len(r.execution_evidence_refs) == 1


def test_node_run_tool_call_records_reject_disordered_ordinal():
    with pytest.raises(ValidationError):
        _node_run(
            status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
            tool_call_records=(_tool_call(call_ordinal=2), _tool_call(call_ordinal=1)),
        )


def test_node_run_tool_call_records_reject_duplicate_ordinal():
    with pytest.raises(ValidationError):
        _node_run(
            status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
            tool_call_records=(_tool_call(call_ordinal=1), _tool_call(call_ordinal=1)),
        )


def test_node_run_data_result_refs_reject_non_main():
    with pytest.raises(ValidationError):
        _node_run(
            status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
            data_result_refs=(_typed_ref(namespace="sealed", content="2" * 64),),
        )


def test_node_run_evidence_refs_reject_duplicate_semantic_identity():
    dup = (
        _typed_ref(object_id="a", content="2" * 64),
        _typed_ref(object_id="b", content="2" * 64),
    )
    with pytest.raises(ValidationError):
        _node_run(
            status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
            data_result_refs=dup,
        )


def test_node_run_data_result_refs_reject_out_of_order():
    r1 = _typed_ref(name="AData", object_id="o1", content="1" * 64)
    r2 = _typed_ref(name="BData", object_id="o2", content="2" * 64)
    with pytest.raises(ValidationError):
        _node_run(
            status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
            data_result_refs=(r2, r1),
        )


def test_node_run_evidence_change_moves_semantic_digest():
    a = _node_run(
        status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
        data_result_refs=(_typed_ref(content="2" * 64),),
    )
    b = _node_run(
        status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(),
        data_result_refs=(_typed_ref(content="9" * 64),),
    )
    assert a.semantic_digest() != b.semantic_digest()
