# -*- coding: utf-8 -*-
"""Task 9 — run event & visibility contracts (``events.py``).

Written test-first: with ``guanlan_v2/orchestration/events.py`` absent the module
fails to import (RED). It then locks, GREEN, the reviewed invariants of the
persist-then-publish event envelope and its visibility boundary:

* ``RunEvent`` is the immutable, self-sealed append record. Its **semantic**
  digest excludes event id / journal & visible sequence / wall-clock (and the
  random causal-wiring ids), and includes event type, partition, plan digest,
  the typed payload ``SchemaRef`` + ``PayloadRef`` and the idempotency identity.
  Wall-clock / sequence / ``PayloadRef.object_id`` therefore move only the audit
  digest, while payload content-digest / type / version / namespace move the
  semantic digest.
* Sequences: ``journal_seq`` is a ``PositiveInt``; ``visible_seq`` is a positive
  int or ``None``. ``ArtifactStaged`` is journal-only and always has
  ``visible_seq=None``; ``LayerCommitted`` is the public visibility boundary and
  requires a ``visible_seq``.
* Namespace boundary: a ``main`` (public) event cannot reference a
  ``sealed`` / ``review`` / ``audit`` payload namespace, and a non-public event
  cannot masquerade as main-public. Trial / Holdout event types are deferred to
  Task 11 and are absent from the frozen ``EventType`` set.
* ``CommittedArtifactRef`` sequences are positive; ``LayerCommit.artifacts`` are
  unique and canonically sorted by ``artifact_seq``.
* ``PlanApproval`` binds one request, an ``ApprovalDecision``, actor/reason and
  the exact pre-freeze ``candidate_plan_digest``; a ``REJECTED`` approval — or a
  request/candidate mismatch — can never authorize a Plan freeze.

Run from repo root: ``pytest tests/orchestration/test_events.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.enums import ApprovalDecision
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.events import (
    CommittedArtifactRef,
    EventCursor,
    EventType,
    LayerCommit,
    PlanApproval,
    RunEvent,
)

UTC = timezone.utc

DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64
WRONG = "0" * 64


def _dt(hour: int = 9, minute: int = 30, *, second: int = 0) -> datetime:
    return datetime(2026, 7, 15, hour, minute, second, tzinfo=UTC)


def _schema_ref(name: str = "NodeStateChangedPayload", version: str = "1") -> SchemaRef:
    return SchemaRef(name=name, version=version)


def _payload_ref(
    *, namespace: str = "main", object_id: str = "obj-1", content_digest: str = DA
) -> PayloadRef:
    return PayloadRef(
        namespace=namespace, object_id=object_id, content_digest=content_digest
    )


def _event_fields(**over: Any) -> dict[str, Any]:
    """The raw RunEvent kwargs (real nested refs) — for build() and tamper tests."""
    base: dict[str, Any] = dict(
        event_id="ev-1",
        run_id="run-1",
        partition="main",
        event_type=EventType.NODE_STATE_CHANGED,
        plan_digest=DB,
        causation_id="ev-0",
        correlation_id="corr-1",
        journal_seq=1,
        visible_seq=1,
        idempotency_key="idem-1",
        payload_schema_ref=_schema_ref(),
        payload_ref=_payload_ref(),
        occurred_at=_dt(),
    )
    base.update(over)
    return base


def _event(**over: Any) -> RunEvent:
    return RunEvent.build(**_event_fields(**over))


# --------------------------------------------------------------------------- #
# 0. all produced models are DigestModels                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cls", [RunEvent, EventCursor, CommittedArtifactRef, LayerCommit, PlanApproval]
)
def test_all_models_are_digest_models(cls):
    assert issubclass(cls, DigestModel)


def test_models_are_frozen_and_forbid_extra():
    ev = _event()
    # unknown field is rejected (extra="forbid")
    with pytest.raises(ValidationError):
        RunEvent.build(**_event_fields(surprise="x"))
    # frozen: assignment is rejected
    with pytest.raises(ValidationError):
        ev.run_id = "run-2"


# --------------------------------------------------------------------------- #
# 1. Trial / Holdout event types are deferred (NOT in the frozen set)         #
# --------------------------------------------------------------------------- #
def test_event_type_set_excludes_trial_and_holdout():
    values = {m.value for m in EventType}
    # the exact Phase-1 set is present …
    assert {
        "RunRequested",
        "PlanDrafted",
        "PlanApproved",
        "PlanRejected",
        "PlanFrozen",
        "BudgetReserved",
        "BudgetSettled",
        "BudgetReleased",
        "NodeStateChanged",
        "ArtifactStaged",
        "LayerCommitted",
        "ContextSnapshotFrozen",
        "ArtifactRelated",
        "ExperimentStateChanged",
        "RunCancelled",
        "RunCompleted",
        "RunFailed",
        "CaseCreated",
        "CaseMatured",
        "CaseReviewed",
    } == values
    # … and the deferred Trial / Holdout types are absent.
    for deferred in ("TrialReserved", "TrialRevealed", "TrialExhausted"):
        assert deferred not in values
    with pytest.raises(ValidationError):
        _event(event_type="TrialReserved")


# --------------------------------------------------------------------------- #
# 2. sealing & round-trip                                                     #
# --------------------------------------------------------------------------- #
def test_build_seals_content_digest_equal_to_semantic_digest():
    ev = _event()
    assert ev.content_digest == ev.semantic_digest()
    assert ev.schema_version == "1"


def test_tampered_content_digest_is_rejected_on_load():
    with pytest.raises(ValidationError):
        RunEvent(**_event_fields(), content_digest=WRONG)


# --------------------------------------------------------------------------- #
# 3. sequence positivity                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [0, -1])
def test_journal_seq_must_be_positive(bad):
    with pytest.raises(ValidationError):
        _event(journal_seq=bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_visible_seq_when_present_must_be_positive(bad):
    with pytest.raises(ValidationError):
        _event(visible_seq=bad)


def test_committed_artifact_seq_must_be_positive():
    CommittedArtifactRef(artifact_id="art-1", artifact_seq=1)
    with pytest.raises(ValidationError):
        CommittedArtifactRef(artifact_id="art-1", artifact_seq=0)
    with pytest.raises(ValidationError):
        CommittedArtifactRef(artifact_id="art-1", artifact_seq=-3)


def test_event_cursor_visible_seq_must_be_positive():
    EventCursor(run_id="run-1", partition="main", visible_seq=1)
    with pytest.raises(ValidationError):
        EventCursor(run_id="run-1", partition="main", visible_seq=0)


# --------------------------------------------------------------------------- #
# 4. per-event-type visibility matrix                                         #
# --------------------------------------------------------------------------- #
def test_artifact_staged_is_journal_only():
    ok = _event(event_type=EventType.ARTIFACT_STAGED, visible_seq=None)
    assert ok.visible_seq is None
    with pytest.raises(ValidationError):
        _event(event_type=EventType.ARTIFACT_STAGED, visible_seq=7)


def test_layer_committed_requires_visible_seq():
    ok = _event(event_type=EventType.LAYER_COMMITTED, visible_seq=3)
    assert ok.visible_seq == 3
    with pytest.raises(ValidationError):
        _event(event_type=EventType.LAYER_COMMITTED, visible_seq=None)


# --------------------------------------------------------------------------- #
# 5. namespace / partition boundary                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ns", ["sealed", "review", "audit"])
def test_main_event_cannot_reference_non_public_payload(ns):
    with pytest.raises(ValidationError):
        _event(partition="main", payload_ref=_payload_ref(namespace=ns))


@pytest.mark.parametrize("partition", ["sealed", "review", "audit"])
def test_non_public_event_cannot_masquerade_as_main_public(partition):
    # a sealed/review/audit event carrying a public (main) payload is rejected …
    with pytest.raises(ValidationError):
        _event(partition=partition, payload_ref=_payload_ref(namespace="main"))
    # … but the same partition with a matching non-public payload is accepted.
    ok = _event(partition=partition, payload_ref=_payload_ref(namespace=partition))
    assert ok.partition == partition


def test_non_public_event_may_mix_non_public_namespaces():
    # both sides non-public: the boundary rule only guards the public frontier.
    ok = _event(partition="sealed", payload_ref=_payload_ref(namespace="review"))
    assert ok.partition == "sealed"


# --------------------------------------------------------------------------- #
# 6. RunEvent digest partition (semantic vs audit)                            #
# --------------------------------------------------------------------------- #
def test_wall_clock_change_is_audit_only():
    a = _event()
    b = _event(occurred_at=_dt(hour=16))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_sequence_change_is_audit_only():
    a = _event()
    b = _event(journal_seq=99, visible_seq=42)
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_event_id_and_causal_ids_are_audit_only():
    a = _event()
    b = _event(event_id="ev-2", causation_id="ev-x", correlation_id="corr-2")
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_payload_object_id_is_audit_only():
    a = _event()
    b = _event(payload_ref=_payload_ref(object_id="obj-2"))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_payload_content_digest_changes_semantic():
    a = _event()
    b = _event(payload_ref=_payload_ref(content_digest=DC))
    assert a.semantic_digest() != b.semantic_digest()


def test_payload_type_and_version_change_semantic():
    a = _event()
    assert a.semantic_digest() != _event(
        payload_schema_ref=_schema_ref(name="OtherPayload")
    ).semantic_digest()
    assert a.semantic_digest() != _event(
        payload_schema_ref=_schema_ref(version="2")
    ).semantic_digest()


def test_payload_namespace_change_is_semantic():
    # hold the (non-public) partition constant so only the payload namespace moves.
    a = _event(partition="sealed", payload_ref=_payload_ref(namespace="sealed"))
    b = _event(partition="sealed", payload_ref=_payload_ref(namespace="review"))
    assert a.semantic_digest() != b.semantic_digest()


@pytest.mark.parametrize("field", ["event_type", "partition", "plan_digest", "idempotency_key"])
def test_semantic_identity_fields_move_semantic_digest(field):
    a = _event()
    changes = {
        "event_type": EventType.RUN_COMPLETED,
        "partition": "review",
        "plan_digest": DD,
        "idempotency_key": "idem-2",
    }
    over: dict[str, Any] = {field: changes[field]}
    if field == "partition":
        over["payload_ref"] = _payload_ref(namespace="review")
    b = _event(**over)
    assert a.semantic_digest() != b.semantic_digest()


# --------------------------------------------------------------------------- #
# 7. LayerCommit — sorted, unique committed artifact sequences                #
# --------------------------------------------------------------------------- #
def _commit(seqs, **over: Any) -> LayerCommit:
    artifacts = tuple(
        CommittedArtifactRef(artifact_id=f"art-{s}", artifact_seq=s) for s in seqs
    )
    base: dict[str, Any] = dict(
        plan_digest=DB,
        layer_index=0,
        node_run_ids=("nr-1", "nr-2"),
        artifacts=artifacts,
        committed_at=_dt(16, 0),
    )
    base.update(over)
    return LayerCommit(**base)


def test_layer_commit_accepts_sorted_unique_sequences():
    lc = _commit([1, 2, 3])
    assert [a.artifact_seq for a in lc.artifacts] == [1, 2, 3]


def test_layer_commit_rejects_unsorted_sequences():
    with pytest.raises(ValidationError):
        _commit([2, 1, 3])


def test_layer_commit_rejects_duplicate_sequences():
    with pytest.raises(ValidationError):
        _commit([1, 2, 2])


def test_layer_commit_committed_at_is_audit_only():
    a = _commit([1, 2])
    b = _commit([1, 2], committed_at=_dt(17, 0))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


# --------------------------------------------------------------------------- #
# 8. PlanApproval — freeze authority                                          #
# --------------------------------------------------------------------------- #
def _approval(**over: Any) -> PlanApproval:
    base: dict[str, Any] = dict(
        request_id="req-1",
        candidate_plan_digest=DA,
        decision=ApprovalDecision.APPROVED,
        actor_id="pm-1",
        decided_at=_dt(10, 0),
        reason="looks disciplined",
    )
    base.update(over)
    return PlanApproval(**base)


def test_approved_matching_approval_authorizes_freeze():
    ap = _approval()
    assert ap.is_approved is True
    assert ap.authorizes_freeze(request_id="req-1", candidate_plan_digest=DA) is True
    ap.require_freeze_authority(request_id="req-1", candidate_plan_digest=DA)


def test_rejected_approval_can_never_satisfy_freeze():
    ap = _approval(decision=ApprovalDecision.REJECTED)
    assert ap.is_approved is False
    assert ap.authorizes_freeze(request_id="req-1", candidate_plan_digest=DA) is False
    with pytest.raises(ValueError):
        ap.require_freeze_authority(request_id="req-1", candidate_plan_digest=DA)


def test_mismatched_request_id_is_not_freeze_authority():
    ap = _approval()
    assert ap.authorizes_freeze(request_id="req-OTHER", candidate_plan_digest=DA) is False
    with pytest.raises(ValueError):
        ap.require_freeze_authority(request_id="req-OTHER", candidate_plan_digest=DA)


def test_mismatched_candidate_digest_is_not_freeze_authority():
    ap = _approval()
    assert ap.authorizes_freeze(request_id="req-1", candidate_plan_digest=DC) is False
    with pytest.raises(ValueError):
        ap.require_freeze_authority(request_id="req-1", candidate_plan_digest=DC)


def test_plan_approval_decided_at_is_audit_only():
    a = _approval()
    b = _approval(decided_at=_dt(11, 0))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_plan_approval_decision_and_candidate_are_semantic():
    a = _approval()
    assert a.semantic_digest() != _approval(
        decision=ApprovalDecision.REJECTED
    ).semantic_digest()
    assert a.semantic_digest() != _approval(
        candidate_plan_digest=DC
    ).semantic_digest()
