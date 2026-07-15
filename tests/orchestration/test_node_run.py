# -*- coding: utf-8 -*-
"""Task 6 — ``NodeRun`` status / counter / attempt matrix (``schemas.py``).

Written test-first (RED until ``schemas.py`` exists). Locks the reviewed
per-status invariants of a node execution record:

* it is a frozen, strict :class:`DigestModel` that forbids extra fields;
* counters (``tool_call_count`` / ``input_tokens`` / ``output_tokens``) are
  strict non-negative ints (``bool`` rejected) and ``attempt`` starts at 1
  (``PositiveInt`` — ``0`` / negatives / ``bool`` rejected);
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
from guanlan_v2.orchestration.schemas import NodeRun

UTC = timezone.utc


def _dt(hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=UTC)


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
        tool_call_count=0,
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
@pytest.mark.parametrize("field", ["tool_call_count", "input_tokens", "output_tokens"])
def test_counters_reject_negative(field):
    with pytest.raises(ValidationError):
        _node_run(status=NodeStatus.RUNNING, output_keys=(), output_artifact_ids=(), **{field: -1})


@pytest.mark.parametrize("field", ["tool_call_count", "input_tokens", "output_tokens"])
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
