# -*- coding: utf-8 -*-
"""Task 3 — additive Trial / Holdout event vocabulary (``events.py``).

Written test-first: with ``events.py`` still frozen at the 20 Phase-1 members
these tests are RED (missing enum members / old exact-equality set), not a
collection error. Task 3 then makes them GREEN by an **additive** extension of
``EventType`` — three appended members and a per-type payload-schema validator —
plus the two absence-guard flips (``test_events.py`` exact-equality set and
``test_contract_completeness.py`` registry-absence + module sweep) that ship in
the same change.

It locks the frozen event ↔ payload mapping consumed by Tasks 5–6:

* ``set(EventType)`` is exactly the 23-value frozen vocabulary — the 20 Phase-1
  members byte-identical and in their original order, then ``TrialReserved`` /
  ``TrialRevealed`` / ``TrialExhausted`` appended.
* ``RunEvent.build`` admits each new type only with its allowed payload schema
  name(s): ``TrialReserved`` ⇒ ``TrialRecord``; ``TrialRevealed`` ⇒
  ``TrialRecord`` | ``HoldoutReceipt``; ``TrialExhausted`` ⇒ ``HoldoutReceipt``
  only on the public ``main`` partition, but ``HoldoutReceipt`` | ``TrialRecord``
  on the non-public ``audit`` ledger partition (the metrics restriction is scoped
  to the public partition, so the full terminal holdout ``TrialRecord`` audit copy
  stays constructible).
* the pre-existing namespace-masquerade rule is unaffected — a ``main`` trial
  event cannot reference a ``sealed`` payload — and the ``ArtifactStaged`` /
  ``LayerCommitted`` visibility rules are unchanged.
* the Phase-1 golden manifest bytes are untouched (its sha256 is pinned): the
  additive enum change cannot perturb any registered schema.

Run from repo root: ``pytest tests/orchestration/test_trial_events.py -v``
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.events import (
    PUBLIC_EVENT_PARTITIONS,
    EventType,
    RunEvent,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64

#: The 20 Phase-1 event-type values, in their frozen original order. The three
#: Phase 4 (Task 3) additive members are appended to this list, never inserted.
PHASE1_EVENT_VALUES: tuple[str, ...] = (
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
)
TRIAL_EVENT_VALUES: tuple[str, ...] = (
    "TrialReserved",
    "TrialRevealed",
    "TrialExhausted",
)
FROZEN_EVENT_VALUES: tuple[str, ...] = PHASE1_EVENT_VALUES + TRIAL_EVENT_VALUES

#: sha256 of the reviewed Phase-1 golden manifest, pinned so the additive event
#: change is proven not to touch it (invariant 1 / Step-1 point 5).
PHASE1_SCHEMA_MANIFEST_SHA256 = (
    "5de22e4727f874c4b1c53d1da6a82bb8666a2a85ae97a5b551280c687cae92d8"
)
GOLDEN_MANIFEST = (
    Path(__file__).resolve().parent / "golden" / "schema_manifest_v1.json"
)


def _dt(hour: int = 9, minute: int = 30) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=UTC)


def _schema_ref(name: str, version: str = "1") -> SchemaRef:
    return SchemaRef(name=name, version=version)


def _payload_ref(*, namespace: str = "main", object_id: str = "obj-1") -> PayloadRef:
    return PayloadRef(namespace=namespace, object_id=object_id, content_digest=DA)


def _event_fields(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        event_id="ev-1",
        run_id="run-1",
        partition="main",
        event_type=EventType.TRIAL_RESERVED,
        plan_digest=DB,
        journal_seq=1,
        visible_seq=1,
        idempotency_key="idem-1",
        payload_schema_ref=_schema_ref("TrialRecord"),
        payload_ref=_payload_ref(),
        occurred_at=_dt(),
    )
    base.update(over)
    return base


def _event(**over: Any) -> RunEvent:
    return RunEvent.build(**_event_fields(**over))


# --------------------------------------------------------------------------- #
# 1. the frozen 23-value vocabulary — 20 Phase-1 byte-identical + 3 appended    #
# --------------------------------------------------------------------------- #
def test_event_type_is_exactly_the_23_value_frozen_vocabulary():
    ordered = tuple(m.value for m in EventType)
    # the 20 Phase-1 members are byte-identical AND still in their original order …
    assert ordered[:20] == PHASE1_EVENT_VALUES
    # … the three Trial members are appended (never renumbered / reordered) …
    assert ordered[20:] == TRIAL_EVENT_VALUES
    # … and the whole set is exactly the 23-value frozen vocabulary.
    assert ordered == FROZEN_EVENT_VALUES
    assert {m.value for m in EventType} == set(FROZEN_EVENT_VALUES)
    assert len(EventType) == 23


def test_trial_member_names_and_values():
    assert EventType.TRIAL_RESERVED.value == "TrialReserved"
    assert EventType.TRIAL_REVEALED.value == "TrialRevealed"
    assert EventType.TRIAL_EXHAUSTED.value == "TrialExhausted"


# --------------------------------------------------------------------------- #
# 2. per-type payload-schema rules                                             #
# --------------------------------------------------------------------------- #
def test_trial_reserved_accepts_only_trial_record():
    ok = _event(event_type=EventType.TRIAL_RESERVED, payload_schema_ref=_schema_ref("TrialRecord"))
    assert ok.event_type is EventType.TRIAL_RESERVED
    assert ok.content_digest == ok.semantic_digest()
    # any other schema name — including HoldoutReceipt — is rejected on main.
    with pytest.raises(ValidationError):
        _event(event_type=EventType.TRIAL_RESERVED, payload_schema_ref=_schema_ref("HoldoutReceipt"))
    with pytest.raises(ValidationError):
        _event(event_type=EventType.TRIAL_RESERVED, payload_schema_ref=_schema_ref("OptimizeRunState"))


def test_trial_revealed_accepts_exactly_two_names():
    for name in ("TrialRecord", "HoldoutReceipt"):
        ok = _event(event_type=EventType.TRIAL_REVEALED, payload_schema_ref=_schema_ref(name))
        assert ok.payload_schema_ref.name == name
    with pytest.raises(ValidationError):
        _event(event_type=EventType.TRIAL_REVEALED, payload_schema_ref=_schema_ref("OptimizeRunState"))


def test_trial_exhausted_is_partition_conditional():
    # main (public) partition: HoldoutReceipt only — metrics restriction scoped here.
    ok_main = _event(
        event_type=EventType.TRIAL_EXHAUSTED,
        partition="main",
        payload_ref=_payload_ref(namespace="main"),
        payload_schema_ref=_schema_ref("HoldoutReceipt"),
    )
    assert ok_main.partition == "main"
    with pytest.raises(ValidationError):
        _event(
            event_type=EventType.TRIAL_EXHAUSTED,
            partition="main",
            payload_ref=_payload_ref(namespace="main"),
            payload_schema_ref=_schema_ref("TrialRecord"),
        )
    # audit (non-public) ledger partition: the full terminal holdout TrialRecord
    # audit copy is constructible, and HoldoutReceipt stays allowed.
    ok_audit_tr = _event(
        event_type=EventType.TRIAL_EXHAUSTED,
        partition="audit",
        payload_ref=_payload_ref(namespace="audit"),
        payload_schema_ref=_schema_ref("TrialRecord"),
    )
    assert ok_audit_tr.partition == "audit"
    ok_audit_hr = _event(
        event_type=EventType.TRIAL_EXHAUSTED,
        partition="audit",
        payload_ref=_payload_ref(namespace="audit"),
        payload_schema_ref=_schema_ref("HoldoutReceipt"),
    )
    assert ok_audit_hr.partition == "audit"
    # an unrelated schema name is still rejected on the audit partition.
    with pytest.raises(ValidationError):
        _event(
            event_type=EventType.TRIAL_EXHAUSTED,
            partition="audit",
            payload_ref=_payload_ref(namespace="audit"),
            payload_schema_ref=_schema_ref("OptimizeRunState"),
        )


def test_only_main_is_public_partition_for_the_exhausted_rule():
    # documents the scope of the partition-conditional rule: main is the only
    # public partition, so the metrics restriction applies exactly there.
    assert PUBLIC_EVENT_PARTITIONS == frozenset({"main"})


# --------------------------------------------------------------------------- #
# 3. namespace-masquerade regression (unchanged Phase-1 validator)             #
# --------------------------------------------------------------------------- #
def test_main_trial_event_cannot_reference_sealed_payload():
    # a main-partition TrialRevealed carrying a sealed payload is rejected by the
    # unchanged namespace-masquerade rule — sealed metrics never ride a public event.
    with pytest.raises(ValidationError):
        _event(
            event_type=EventType.TRIAL_REVEALED,
            partition="main",
            payload_schema_ref=_schema_ref("TrialRecord"),
            payload_ref=_payload_ref(namespace="sealed"),
        )


def test_trial_event_payload_rule_runs_with_namespace_rule():
    # both guards trip: a main TrialReserved with a disallowed schema AND a sealed
    # payload is rejected (the additive rule does not weaken the namespace guard).
    with pytest.raises(ValidationError):
        _event(
            event_type=EventType.TRIAL_RESERVED,
            partition="main",
            payload_schema_ref=_schema_ref("HoldoutReceipt"),
            payload_ref=_payload_ref(namespace="sealed"),
        )


# --------------------------------------------------------------------------- #
# 4. pre-existing visibility rules are unaffected                              #
# --------------------------------------------------------------------------- #
def test_artifact_staged_still_journal_only():
    ok = _event(
        event_type=EventType.ARTIFACT_STAGED,
        payload_schema_ref=_schema_ref("NodeStateChangedPayload"),
        visible_seq=None,
    )
    assert ok.visible_seq is None
    with pytest.raises(ValidationError):
        _event(
            event_type=EventType.ARTIFACT_STAGED,
            payload_schema_ref=_schema_ref("NodeStateChangedPayload"),
            visible_seq=7,
        )


def test_layer_committed_still_requires_visible_seq():
    ok = _event(
        event_type=EventType.LAYER_COMMITTED,
        payload_schema_ref=_schema_ref("NodeStateChangedPayload"),
        visible_seq=3,
    )
    assert ok.visible_seq == 3
    with pytest.raises(ValidationError):
        _event(
            event_type=EventType.LAYER_COMMITTED,
            payload_schema_ref=_schema_ref("NodeStateChangedPayload"),
            visible_seq=None,
        )


# --------------------------------------------------------------------------- #
# 5. the Phase-1 golden manifest bytes are unchanged                           #
# --------------------------------------------------------------------------- #
def test_phase1_golden_manifest_bytes_are_unchanged():
    digest = hashlib.sha256(GOLDEN_MANIFEST.read_bytes()).hexdigest()
    assert digest == PHASE1_SCHEMA_MANIFEST_SHA256, (
        "the additive EventType extension must not touch the Phase-1 golden "
        "manifest — no registered schema references EventType"
    )
