# -*- coding: utf-8 -*-
"""Phase 6 · Task 9 — the two additive shadow-consumer event types.

Written test-first: with ``events.py`` still frozen at the 23-value Phase-4 shape
these tests are RED (missing ``EventType.SHADOW_INTENT_ISSUED`` /
``EventType.SHADOW_TARGET_APPLIED``), not a collection error. Task 9 then makes
them GREEN by an **additive** extension of ``EventType`` — two appended members
plus a per-type partition rule (both shadow types are public advisory evidence and
live only on the ``main`` partition) — and the frozen-set guard flip in
``test_events.py`` shipping in the same change.

It locks the four required invariants (see ``.superpowers/sdd/task-9-brief.md``):

1. **provable additivity** — the pre-existing reviewed 23-value set is a *strict
   subset* of the new 25-value set and every old value string is unchanged;
2. **persist-then-publish** via the REAL Phase 2 ``EventStore`` — appending
   ``ShadowIntentIssued`` twice with the same key + identical payload returns the
   stored event; the same key + different intent content raises
   ``IdempotencyConflict``; ``ShadowTargetApplied`` keyed on the record's
   ``target_apply_key`` collapses a recovering runner's re-reports to exactly one
   visible apply event;
3. **partition rules** — a shadow event on ``partition="sealed"`` / ``"review"``
   fails construction, and a ``main`` shadow event referencing a non-public payload
   namespace fails via the INHERITED namespace-masquerade rule (re-asserted here);
4. **replay + stable cursors** — replaying the journal reproduces the shadow events
   in order with stable visible cursors (Phase 2 semantics, consumed not
   reimplemented).

Payload bindings (convention, asserted here — NOT enforced by ``events.py``):
``ShadowIntentIssued`` carries ``payload_schema_ref = TargetPortfolioIntent@1`` with
idempotency key ``content_digest({"domain": "shadow-intent-issued-v1", "intent":
<intent semantic digest>})``; ``ShadowTargetApplied`` carries
``ShadowTargetApplyRecord@1`` with idempotency key = the record's
``target_apply_key``. The event's ``payload_ref`` is the bare ``PayloadStore``
locator (plain ``PayloadRef``, never the composite ``TypedPayloadRef``).

Run from repo root: ``pytest tests/orchestration/test_shadow_events.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.events import (
    PUBLIC_EVENT_PARTITIONS,
    EventType,
    RunEvent,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.eventstore import (
    EventAppendRequest,
    IdempotencyConflict,
    NamespaceViolation,
    RuntimeStores,
    SchemaRegistryResolver,
)

UTC = timezone.utc
DA = "a" * 64

_SCHED_UTC = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)     # 09:30 CST 07-20
_ELIGIBLE_UTC = datetime(2026, 7, 21, 1, 30, tzinfo=UTC)  # 09:30 CST 07-21 (next open)

#: the SHADOW event-type names + values Task 9 appends (never renumbers).
SHADOW_EVENT_VALUES: tuple[str, ...] = ("ShadowIntentIssued", "ShadowTargetApplied")

#: the pre-existing reviewed 23-value EventType vocabulary (20 Phase-1 + 3 Phase-4
#: Trial members), recorded verbatim so additivity is proven against a fixed set,
#: not re-derived from the live enum this task mutates.
FROZEN_23_VALUES: frozenset[str] = frozenset(
    {
        "RunRequested", "PlanDrafted", "PlanApproved", "PlanRejected", "PlanFrozen",
        "BudgetReserved", "BudgetSettled", "BudgetReleased", "NodeStateChanged",
        "ArtifactStaged", "LayerCommitted", "ContextSnapshotFrozen", "ArtifactRelated",
        "ExperimentStateChanged", "RunCancelled", "RunCompleted", "RunFailed",
        "CaseCreated", "CaseMatured", "CaseReviewed",
        "TrialReserved", "TrialRevealed", "TrialExhausted",
    }
)

#: intent-issued idempotency-key family domain tag (brief convention).
_SHADOW_INTENT_ISSUED_DOMAIN = "shadow-intent-issued-v1"
_INTENT_SCHEMA = SchemaRef(name="TargetPortfolioIntent", version="1")
_APPLY_SCHEMA = SchemaRef(name="ShadowTargetApplyRecord", version="1")


# --------------------------------------------------------------------------- #
# clocks + construction helpers (copied minimally from the shadow test suite)   #
# --------------------------------------------------------------------------- #
class AdvancingClock:
    """A tz-aware AuthoritativeClock advancing one second per read."""

    def __init__(self, start: datetime | None = None,
                 step: timedelta = timedelta(seconds=1)) -> None:
        self._next = start or datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
        self._step = step

    def now(self) -> datetime:
        cur = self._next
        self._next = cur + self._step
        return cur


def _sym(code: str = "600519", exchange: str = "SH", board: str = "main") -> Symbol:
    return Symbol(code=code, exchange=exchange, board=board)


def _pos(weight: float, *, code: str = "600519", exchange: str = "SH",
         board: str = "main", **kw):
    return shadow.TargetPosition(symbol=_sym(code, exchange, board),
                                 target_weight=weight, **kw)


def _intent(**over: Any) -> "shadow.TargetPortfolioIntent":
    """A hand-built, self-consistent ``TargetPortfolioIntent`` (raw constructor)."""
    base: dict[str, Any] = dict(
        intent_id="intent-1",
        target_version=1,
        proposal_artifact_id="art-prop-1",
        proposal_digest="a" * 64,
        source_decision_artifact_id="dec-art-1",
        decision_schedule_id="shadow.daily.ashare",
        decision_schedule_version="1",
        decision_schedule_digest="b" * 64,
        scheduled_for=_SCHED_UTC,
        decision_as_of=_SCHED_UTC,
        eligible_execution_at=_ELIGIBLE_UTC,
        positions=(_pos(0.5),),
        cash_weight=0.5,
        rationale="thesis",
        confidence=Confidence.MEDIUM,
        created_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
    )
    base.update(over)
    return shadow.TargetPortfolioIntent(**base)


def _apply_key(intent_id: str = "intent-1", scheduled_for: datetime = _SCHED_UTC,
               target_version: int = 1) -> str:
    """The intent-lane apply key via the exact Task-3 builder (duck-typed intent)."""
    return shadow.target_apply_key(
        SimpleNamespace(intent_id=intent_id, scheduled_for=scheduled_for,
                        target_version=target_version)
    )


def _apply(**over: Any) -> "shadow.ShadowTargetApplyRecord":
    """A self-consistent intent-lane ``ShadowTargetApplyRecord``."""
    key = _apply_key()
    order_id = shadow.shadow_order_id(
        apply_key=key, symbol=_sym(), order_kind="target_buy",
        trigger_bar="2026-07-21", ordinal=0,
    )
    base: dict[str, Any] = dict(
        target_apply_key=key,
        intent_content_digest="a" * 64,
        intent_id="intent-1",
        scheduled_for=_SCHED_UTC,
        target_version=1,
        trigger_bar="2026-07-21",
        order_ids=(order_id,),
        applied=True,
    )
    base.update(over)
    return shadow.ShadowTargetApplyRecord(**base)


def _intent_issued_key(intent: "shadow.TargetPortfolioIntent") -> str:
    """The brief-convention ``ShadowIntentIssued`` idempotency key."""
    return content_digest(
        {"domain": _SHADOW_INTENT_ISSUED_DOMAIN, "intent": intent.semantic_digest()}
    )


# --- RunEvent.build direct-construction helper (Phase-1 layer, no store) ----- #
def _schema_ref(name: str = "TargetPortfolioIntent", version: str = "1") -> SchemaRef:
    return SchemaRef(name=name, version=version)


def _payload_ref(*, namespace: str = "main", object_id: str = "obj-1",
                 content_digest: str = DA) -> PayloadRef:
    return PayloadRef(namespace=namespace, object_id=object_id,
                      content_digest=content_digest)


def _event_fields(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        event_id="ev-1",
        run_id="run-1",
        partition="main",
        event_type=EventType.SHADOW_INTENT_ISSUED,
        plan_digest="b" * 64,
        journal_seq=1,
        visible_seq=1,
        idempotency_key="idem-1",
        payload_schema_ref=_schema_ref(),
        payload_ref=_payload_ref(),
        occurred_at=datetime(2026, 7, 20, 9, 30, tzinfo=UTC),
    )
    base.update(over)
    return base


def _event(**over: Any) -> RunEvent:
    return RunEvent.build(**_event_fields(**over))


# --- real EventStore wiring (mirrors test_eventstore._stores, shadow registry) #
def _shadow_registry() -> SchemaRegistry:
    """A sealed registry carrying the two Phase-6 shadow payload schemas.

    The Phase-6 cumulative registry does not exist until Task 10; this ad-hoc
    registry is the minimal way to exercise the REAL ``PayloadStore``/``EventStore``
    persist-then-publish path with a genuine ``TargetPortfolioIntent@1`` /
    ``ShadowTargetApplyRecord@1`` payload (both are ``ContractModel`` subclasses
    declaring ``schema_version = "1"``).
    """
    reg = SchemaRegistry()
    reg.register(shadow.TargetPortfolioIntent)
    reg.register(shadow.ShadowTargetApplyRecord)
    reg.seal()
    return reg


def _stores(clock: Any = None) -> tuple[RuntimeStores, str]:
    resolver = SchemaRegistryResolver()
    digest = resolver.register(_shadow_registry())
    stores = RuntimeStores(resolver=resolver, clock=clock or AdvancingClock())
    return stores, digest


def _put(stores: RuntimeStores, digest: str, payload: Any, schema: SchemaRef, *,
         namespace: str = "main", idem: str = "p-1") -> PayloadRef:
    return stores.payloads.put(
        schema, payload, registry_digest=digest, namespace=namespace,
        idempotency_key=idem,
    )


def _append(stores: RuntimeStores, digest: str, ref: PayloadRef, *,
            run_id: str = "run-1", partition: str = "main",
            event_type: str = "ShadowIntentIssued", idem: str = "ev-1",
            schema: SchemaRef = _INTENT_SCHEMA, plan_digest: str | None = None) -> RunEvent:
    return stores.events.append(EventAppendRequest(
        run_id=run_id, partition=partition, event_type=event_type,
        payload_schema_ref=schema, payload_ref=ref,
        registry_digest=digest, idempotency_key=idem, plan_digest=plan_digest,
    ))


# =========================================================================== #
# Invariant 1 — provable additivity: 23-value set ⊂ 25-value set, old unchanged  #
# =========================================================================== #
def test_shadow_members_exist_with_frozen_names_and_values():
    assert EventType.SHADOW_INTENT_ISSUED.value == "ShadowIntentIssued"
    assert EventType.SHADOW_TARGET_APPLIED.value == "ShadowTargetApplied"


def test_event_type_extension_is_provably_additive():
    values = {m.value for m in EventType}
    # the pre-existing reviewed 23-value set is a STRICT subset of the new set …
    assert FROZEN_23_VALUES < values
    # … every one of the 23 old value strings is unchanged (nothing revalued) …
    assert FROZEN_23_VALUES <= values
    # … the two shadow values are the only additions …
    assert values - FROZEN_23_VALUES == set(SHADOW_EVENT_VALUES)
    # … and the whole vocabulary is now exactly 25 values.
    assert len(EventType) == 25
    assert values == FROZEN_23_VALUES | set(SHADOW_EVENT_VALUES)


def test_shadow_members_are_appended_after_the_23_frozen_members():
    ordered = tuple(m.value for m in EventType)
    # the 23 Phase-1+Phase-4 members keep their order; shadow members are appended.
    assert set(ordered[:23]) == FROZEN_23_VALUES
    assert ordered[23:] == SHADOW_EVENT_VALUES


# =========================================================================== #
# Invariant 3 — partition rules (public-only) + inherited namespace masquerade   #
# =========================================================================== #
@pytest.mark.parametrize(
    "event_type",
    [EventType.SHADOW_INTENT_ISSUED, EventType.SHADOW_TARGET_APPLIED],
)
def test_main_partition_shadow_event_constructs(event_type):
    ev = _event(event_type=event_type)
    assert ev.partition == "main"
    assert ev.event_type is event_type
    assert ev.content_digest == ev.semantic_digest()


@pytest.mark.parametrize(
    "event_type",
    [EventType.SHADOW_INTENT_ISSUED, EventType.SHADOW_TARGET_APPLIED],
)
@pytest.mark.parametrize("partition", ["sealed", "review"])
def test_shadow_event_on_sealed_or_review_fails_construction(event_type, partition):
    # a shadow fact is public advisory evidence: it can never live on a non-public
    # partition. A matching non-public payload namespace clears the masquerade rule,
    # so it is the per-type partition rule that must raise here.
    with pytest.raises(ValidationError):
        _event(
            event_type=event_type,
            partition=partition,
            payload_ref=_payload_ref(namespace=partition),
        )


@pytest.mark.parametrize(
    "event_type",
    [EventType.SHADOW_INTENT_ISSUED, EventType.SHADOW_TARGET_APPLIED],
)
def test_shadow_event_on_audit_partition_fails_construction(event_type):
    with pytest.raises(ValidationError):
        _event(
            event_type=event_type,
            partition="audit",
            payload_ref=_payload_ref(namespace="audit"),
        )


@pytest.mark.parametrize(
    "event_type",
    [EventType.SHADOW_INTENT_ISSUED, EventType.SHADOW_TARGET_APPLIED],
)
@pytest.mark.parametrize("ns", ["sealed", "review", "audit"])
def test_main_shadow_event_cannot_reference_non_public_payload(event_type, ns):
    # the INHERITED namespace-masquerade rule (unchanged) re-asserted for the new
    # types: a main shadow event may never carry a sealed/review/audit payload, so
    # audit-only detail can never ride a public shadow fact.
    with pytest.raises(ValidationError):
        _event(event_type=event_type, partition="main",
               payload_ref=_payload_ref(namespace=ns))


def test_only_main_is_public_partition():
    # documents the scope of the shadow partition rule.
    assert PUBLIC_EVENT_PARTITIONS == frozenset({"main"})


# =========================================================================== #
# Invariant 2 — persist-then-publish against the REAL Phase 2 EventStore          #
# =========================================================================== #
def test_intent_issued_identical_retry_returns_the_stored_event():
    stores, digest = _stores()
    intent = _intent()
    key = _intent_issued_key(intent)
    ref = _put(stores, digest, intent, _INTENT_SCHEMA, idem="pay-intent-1")

    e1 = _append(stores, digest, ref, event_type="ShadowIntentIssued", idem=key)
    # a byte-identical re-report under the same key returns the SAME stored event.
    e2 = _append(stores, digest, ref, event_type="ShadowIntentIssued", idem=key)
    assert e1.content_digest == e2.content_digest
    assert e1.journal_seq == e2.journal_seq == 1
    assert len(stores.events.journal("run-1", "main")) == 1
    # convention: it carries TargetPortfolioIntent@1 as its payload schema ref …
    assert e1.payload_schema_ref == _INTENT_SCHEMA
    # … and a bare PayloadStore locator (plain PayloadRef, never TypedPayloadRef).
    assert isinstance(e1.payload_ref, PayloadRef)
    assert not isinstance(e1.payload_ref, TypedPayloadRef)


def test_intent_issued_same_key_different_content_raises_idempotency_conflict():
    stores, digest = _stores()
    intent_a = _intent()
    key = _intent_issued_key(intent_a)
    ref_a = _put(stores, digest, intent_a, _INTENT_SCHEMA, idem="pay-a")
    _append(stores, digest, ref_a, event_type="ShadowIntentIssued", idem=key)

    # a genuinely different intent stored under the SAME (forced) key: the resulting
    # event's content digest differs, so the store refuses to collapse them.
    intent_b = _intent(positions=(_pos(0.75),), cash_weight=0.25)
    ref_b = _put(stores, digest, intent_b, _INTENT_SCHEMA, idem="pay-b")
    with pytest.raises(IdempotencyConflict):
        _append(stores, digest, ref_b, event_type="ShadowIntentIssued", idem=key)


def test_intent_issued_key_is_content_addressed_over_the_intent_semantic_digest():
    # the brief convention: the key is exactly
    # content_digest({"domain": "shadow-intent-issued-v1", "intent": <sem digest>}).
    intent = _intent()
    expected = content_digest(
        {"domain": "shadow-intent-issued-v1", "intent": intent.semantic_digest()}
    )
    assert _intent_issued_key(intent) == expected
    # two intents differing only in the runtime id share a semantic digest, so the
    # issued key is identical (apply-once identity is operational, keyed elsewhere).
    assert _intent_issued_key(_intent(intent_id="intent-2")) == expected


def test_target_applied_recovery_re_reports_collapse_to_one_visible_event():
    stores, digest = _stores()
    record = _apply()
    key = record.target_apply_key  # the apply event is keyed on the record's key
    ref = _put(stores, digest, record, _APPLY_SCHEMA, idem="pay-apply-1")

    # a recovering runner re-reports the SAME applied target three times …
    first = _append(stores, digest, ref, event_type="ShadowTargetApplied",
                    idem=key, schema=_APPLY_SCHEMA)
    again = _append(stores, digest, ref, event_type="ShadowTargetApplied",
                    idem=key, schema=_APPLY_SCHEMA)
    third = _append(stores, digest, ref, event_type="ShadowTargetApplied",
                    idem=key, schema=_APPLY_SCHEMA)
    assert first.content_digest == again.content_digest == third.content_digest
    # … yet exactly ONE apply event is journalled and ONE is visible.
    assert len(stores.events.journal("run-1", "main")) == 1
    visible = stores.events.visible("run-1", "main")
    assert len(visible) == 1
    assert visible[0].event_type is EventType.SHADOW_TARGET_APPLIED
    assert visible[0].payload_schema_ref == _APPLY_SCHEMA


def test_target_applied_key_equals_the_record_target_apply_key():
    # invariant binding: the apply event's idempotency key IS the record's
    # target_apply_key (one visible apply per (intent_id, scheduled_for, version)).
    record = _apply()
    assert record.target_apply_key == _apply_key()


def test_two_distinct_applies_are_two_visible_events():
    stores, digest = _stores()
    r1 = _apply()
    r2 = _apply(
        target_apply_key=_apply_key(target_version=2),
        target_version=2,
        order_ids=(shadow.shadow_order_id(
            apply_key=_apply_key(target_version=2), symbol=_sym(),
            order_kind="target_buy", trigger_bar="2026-07-21", ordinal=0),),
    )
    ref1 = _put(stores, digest, r1, _APPLY_SCHEMA, idem="pay-1")
    ref2 = _put(stores, digest, r2, _APPLY_SCHEMA, idem="pay-2")
    _append(stores, digest, ref1, event_type="ShadowTargetApplied",
            idem=r1.target_apply_key, schema=_APPLY_SCHEMA)
    _append(stores, digest, ref2, event_type="ShadowTargetApplied",
            idem=r2.target_apply_key, schema=_APPLY_SCHEMA)
    assert len(stores.events.visible("run-1", "main")) == 2


def test_main_shadow_event_with_sealed_payload_rejected_by_the_store():
    # the store's namespace boundary (Phase 2) mirrors the Phase-1 masquerade rule:
    # a main shadow event cannot be backed by a sealed payload.
    stores, digest = _stores()
    intent = _intent()
    ref = _put(stores, digest, intent, _INTENT_SCHEMA, namespace="sealed",
               idem="pay-sealed")
    with pytest.raises(NamespaceViolation):
        _append(stores, digest, ref, event_type="ShadowIntentIssued",
                idem=_intent_issued_key(intent))


# =========================================================================== #
# Invariant 4 — replay reproduces the shadow journal + stable visible cursors     #
# =========================================================================== #
def test_replay_reproduces_shadow_journal_order_and_visible_cursors():
    stores, digest = _stores()
    intent = _intent()
    record = _apply()
    ref_intent = _put(stores, digest, intent, _INTENT_SCHEMA, idem="pay-intent")
    ref_apply = _put(stores, digest, record, _APPLY_SCHEMA, idem="pay-apply")
    _append(stores, digest, ref_intent, event_type="ShadowIntentIssued",
            idem=_intent_issued_key(intent))
    _append(stores, digest, ref_apply, event_type="ShadowTargetApplied",
            idem=record.target_apply_key, schema=_APPLY_SCHEMA)

    original_journal = stores.events.journal("run-1", "main")
    original_visible = stores.events.visible("run-1", "main")
    original_cursor = stores.events.cursor("run-1", "main")
    assert [e.event_type for e in original_journal] == [
        EventType.SHADOW_INTENT_ISSUED, EventType.SHADOW_TARGET_APPLIED
    ]

    # a fresh EventStore rebuilt purely from the persisted RunEvents reproduces the
    # same journal, visible view and cursor (Phase 2 semantics, not reimplemented).
    replayed = stores.events.replay()
    assert replayed.journal("run-1", "main") == original_journal
    assert replayed.visible("run-1", "main") == original_visible
    assert replayed.cursor("run-1", "main") == original_cursor
    assert [e.journal_seq for e in replayed.journal("run-1", "main")] == [1, 2]
    assert [e.visible_seq for e in replayed.visible("run-1", "main")] == [1, 2]
