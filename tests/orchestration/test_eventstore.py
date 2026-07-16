# -*- coding: utf-8 -*-
"""Phase 2 · Task 2 — typed payload store + append-only event store (``eventstore``).

Written test-first (RED until ``eventstore.py`` exists). The store persists a typed
payload only after registry validation, appends only Phase 1-valid immutable
``RunEvent``s (store-assigned journal/visible sequences — never caller-selected),
and commits multi-command batches atomically through a single copy-on-write
backend + lock so a crash before the barrier exposes nothing and replay after it
exposes everything.

Covers the brief Step 1 bullets: typed put/get + rejection matrix; exact-digest
registry resolution with no "latest" aliasing; dual-cursor monotonicity; staged
invisibility vs LayerCommitted visibility; idempotent vs conflicting retry;
sealed/review/audit refs barred from main events; caller cannot set
cursors/occurred_at + naive clock rejected; reverse/replayed construction;
crash-before/after payload+event+budget batches; staged-ref/CAS unit of work; and
the production ``BudgetEventSink`` running Task 1's exact command vectors.

Run: ``python -m pytest tests/orchestration/test_eventstore.py -v``
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import budget as budget_mod
from guanlan_v2.orchestration.budget import (
    BudgetExceeded,
    BudgetLedger,
    BudgetRequest,
    BudgetTransitionCommand,
    ReserveNodeArgs,
    fold_budget_events,
)
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import PortfolioRating
from guanlan_v2.orchestration.events import EventType, RunEvent
from guanlan_v2.orchestration.refs import (
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry, default_registry
from guanlan_v2.orchestration.schemas import PortfolioDecision, ResearchPlan
from guanlan_v2.orchestration import eventstore as es_mod
from guanlan_v2.orchestration.eventstore import (
    ContentDigestMismatch,
    EventAppendCommand,
    EventAppendRequest,
    EventStoreError,
    IdempotencyConflict,
    NamespaceViolation,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    SchemaRegistryResolver,
    StagedPayloadKey,
    StagedPayloadRef,
    StagedTypedPayloadRef,
    StateCellCompareAndSwapCommand,
    UnitOfWorkError,
    UnknownRegistryDigest,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64


class AdvancingClock:
    def __init__(self, start: datetime | None = None, step: timedelta = timedelta(seconds=1)):
        self._next = start or datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
        self._step = step

    def now(self) -> datetime:
        cur = self._next
        self._next = cur + self._step
        return cur


class NaiveClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 16, 9, 0)  # naive → rejected at stamp time


def _research(rec=PortfolioRating.BUY, rationale="thesis") -> ResearchPlan:
    return ResearchPlan(recommendation=rec, rationale=rationale)


def _stores(*, clock=None, allowed_cell_namespaces=()):
    resolver = SchemaRegistryResolver()
    digest = resolver.register(default_registry())
    stores = RuntimeStores(
        resolver=resolver,
        clock=clock or AdvancingClock(),
        allowed_cell_namespaces=allowed_cell_namespaces,
    )
    return stores, digest


def _put(stores, digest, payload=None, *, namespace="main", idem="p-1",
         schema=SchemaRef(name="ResearchPlan", version="1")):
    return stores.payloads.put(
        schema, payload if payload is not None else _research(),
        registry_digest=digest, namespace=namespace, idempotency_key=idem,
    )


def _append(stores, digest, ref, *, run_id="run-1", partition="main",
            event_type="ArtifactStaged", idem="ev-1",
            schema=SchemaRef(name="ResearchPlan", version="1"), plan_digest=None):
    return stores.events.append(EventAppendRequest(
        run_id=run_id, partition=partition, event_type=event_type,
        payload_schema_ref=schema, payload_ref=ref,
        registry_digest=digest, idempotency_key=idem, plan_digest=plan_digest,
    ))


# =========================================================================== #
# Binding constraint 1 — IdempotencyConflict is the budget module's type       #
# =========================================================================== #
def test_idempotency_conflict_is_the_budget_module_type():
    assert IdempotencyConflict is budget_mod.IdempotencyConflict


# =========================================================================== #
# PayloadStore — typed put/get + rejection matrix                              #
# =========================================================================== #
def test_put_returns_audit_locator_and_get_revalidates():
    stores, digest = _stores()
    ref = _put(stores, digest, _research())
    assert isinstance(ref, PayloadRef)
    assert ref.namespace == "main"
    assert ref.content_digest == content_digest(_research())  # computed, not caller-chosen
    got = stores.payloads.get(ref, expected_schema_ref=SchemaRef(name="ResearchPlan", version="1"))
    assert isinstance(got, ResearchPlan) and got == _research()


def test_put_rejects_payload_that_fails_its_declared_schema():
    stores, digest = _stores()
    # a PortfolioDecision payload declared as ResearchPlan fails registry validation.
    with pytest.raises(ValidationError):
        stores.payloads.put(
            SchemaRef(name="ResearchPlan", version="1"),
            PortfolioDecision(rating=PortfolioRating.BUY, executive_summary="s", investment_thesis="t"),
            registry_digest=digest, namespace="main", idempotency_key="p-bad",
        )


def test_get_with_wrong_expected_schema_ref_is_rejected():
    stores, digest = _stores()
    ref = _put(stores, digest, _research())
    with pytest.raises(EventStoreError):
        stores.payloads.get(ref, expected_schema_ref=SchemaRef(name="PortfolioDecision", version="1"))


def test_get_with_tampered_content_digest_or_namespace_is_rejected():
    stores, digest = _stores()
    ref = _put(stores, digest, _research())
    tampered_content = PayloadRef(namespace=ref.namespace, object_id=ref.object_id, content_digest=DB)
    with pytest.raises(ContentDigestMismatch):
        stores.payloads.get(tampered_content, expected_schema_ref=SchemaRef(name="ResearchPlan", version="1"))
    tampered_ns = PayloadRef(namespace="sealed", object_id=ref.object_id, content_digest=ref.content_digest)
    with pytest.raises(EventStoreError):
        stores.payloads.get(tampered_ns, expected_schema_ref=SchemaRef(name="ResearchPlan", version="1"))


def test_put_under_unknown_registry_digest_is_rejected():
    stores, _ = _stores()
    with pytest.raises(UnknownRegistryDigest):
        stores.payloads.put(
            SchemaRef(name="ResearchPlan", version="1"), _research(),
            registry_digest=DA, namespace="main", idempotency_key="p-x",
        )


def test_identical_payload_put_is_idempotent_conflicting_put_raises():
    stores, digest = _stores()
    r1 = _put(stores, digest, _research(), idem="k1")
    r2 = _put(stores, digest, _research(), idem="k1")
    assert r1.object_id == r2.object_id and r1.content_digest == r2.content_digest
    with pytest.raises(IdempotencyConflict):
        _put(stores, digest, _research(rationale="different"), idem="k1")


# =========================================================================== #
# SchemaRegistryResolver — exact-digest, no "latest" aliasing                   #
# =========================================================================== #
def test_resolver_serves_multiple_sealed_registries_by_exact_digest():
    # simultaneous resolution of "old Phase 1", "current Phase 2" and a fake
    # later sealed registry — three exact digests, no "latest" aliasing.
    reg_a = default_registry()  # the Phase 1 sealed registry

    reg_b = SchemaRegistry()
    reg_b.register(ResearchPlan)
    reg_b.seal()  # a smaller sealed registry — a distinct digest

    reg_c = SchemaRegistry()  # a fake later sealed registry
    for m in (ResearchPlan, PortfolioDecision):
        reg_c.register(m)
    reg_c.seal()

    resolver = SchemaRegistryResolver()
    da = resolver.register(reg_a)
    db = resolver.register(reg_b)
    dc = resolver.register(reg_c)
    assert len({da, db, dc}) == 3
    assert resolver.resolve(da) is reg_a
    assert resolver.resolve(db) is reg_b
    assert resolver.resolve(dc) is reg_c
    # there is deliberately no mutable "latest" authority.
    assert not hasattr(resolver, "latest")
    with pytest.raises(UnknownRegistryDigest):
        resolver.resolve(DB)


def test_later_sealed_registry_cannot_reinterpret_an_existing_payload():
    # a payload stored under the Phase-1 digest re-validates under THAT digest,
    # never a later sealed registry (invariant 2 — no silent rebind).
    stores, digest = _stores()
    ref = _put(stores, digest, _research())

    # add a later, larger sealed registry to the same resolver.
    later = SchemaRegistry()
    for m in (ResearchPlan, PortfolioDecision):
        later.register(m)
    later.seal()
    later_digest = stores.resolver.register(later)
    assert later_digest != digest
    # get still resolves through the payload's own bound digest and validates.
    got = stores.payloads.get(ref, expected_schema_ref=SchemaRef(name="ResearchPlan", version="1"))
    assert got == _research()


def test_resolver_rejects_an_unsealed_registry():
    resolver = SchemaRegistryResolver()
    unsealed = SchemaRegistry()
    unsealed.register(ResearchPlan)
    with pytest.raises(EventStoreError):
        resolver.register(unsealed)


# =========================================================================== #
# EventStore — Phase 1-valid append, dual cursors, monotonicity                 #
# =========================================================================== #
def test_append_builds_a_phase1_valid_immutable_run_event():
    stores, digest = _stores()
    ref = _put(stores, digest, _research())
    ev = _append(stores, digest, ref, event_type="LayerCommitted",
                 schema=SchemaRef(name="ResearchPlan", version="1"))
    assert isinstance(ev, RunEvent)
    # the store re-verifies its own sealed content_digest via the Phase 1 builder.
    assert ev.content_digest == ev.semantic_digest()
    assert ev.journal_seq == 1


def test_append_rejects_a_request_whose_payload_ref_does_not_exist():
    stores, digest = _stores()
    ghost = PayloadRef(namespace="main", object_id="ghost", content_digest=DA)
    with pytest.raises(EventStoreError):
        _append(stores, digest, ghost)


def test_append_rejects_a_payload_ref_with_a_mismatched_content_digest():
    stores, digest = _stores()
    ref = _put(stores, digest, _research())
    wrong = PayloadRef(namespace="main", object_id=ref.object_id, content_digest=DB)
    with pytest.raises(EventStoreError):
        _append(stores, digest, wrong)


def test_append_rejects_a_schema_ref_that_differs_from_the_stored_payload():
    # an event cannot re-declare a ResearchPlan payload as a PortfolioDecision.
    stores, digest = _stores()
    ref = _put(stores, digest, _research())
    with pytest.raises(EventStoreError):
        _append(stores, digest, ref, schema=SchemaRef(name="PortfolioDecision", version="1"))
    assert stores.events.journal("run-1", "main") == ()


def test_append_rejects_a_registry_digest_that_differs_from_the_stored_payload():
    # an event cannot declare a different (even valid, sealed) registry than the
    # exact one its payload was validated/stored under.
    stores, digest = _stores()
    ref = _put(stores, digest, _research())
    later = SchemaRegistry()
    later.register(ResearchPlan)
    later.seal()
    later_digest = stores.resolver.register(later)
    assert later_digest != digest
    with pytest.raises(EventStoreError):
        _append(stores, later_digest, ref, event_type="LayerCommitted")
    assert stores.events.journal("run-1", "main") == ()


def test_journal_and_visible_cursors_are_monotonic_and_store_assigned():
    stores, digest = _stores()
    r1 = _put(stores, digest, _research(rationale="one"), idem="p1")
    r2 = _put(stores, digest, _research(rationale="two"), idem="p2")
    # a main non-staged event is visible; ArtifactStaged is journal-only.
    e_committed = _append(stores, digest, r1, event_type="LayerCommitted", idem="e1")
    e_staged = _append(stores, digest, r2, event_type="ArtifactStaged", idem="e2")

    journal = stores.events.journal("run-1", "main")
    assert [e.journal_seq for e in journal] == [1, 2]
    assert e_committed.visible_seq == 1
    assert e_staged.visible_seq is None
    visible = stores.events.visible("run-1", "main")
    assert [e.journal_seq for e in visible] == [1]  # only the committed event
    assert stores.events.visible("run-1", "main", after=1) == ()


def test_visible_streams_are_partitioned_per_run_and_partition():
    stores, digest = _stores()
    r = _put(stores, digest, _research())
    _append(stores, digest, r, run_id="run-A", event_type="LayerCommitted", idem="a")
    _append(stores, digest, r, run_id="run-B", event_type="LayerCommitted", idem="b")
    ja = stores.events.journal("run-A", "main")
    jb = stores.events.journal("run-B", "main")
    assert len(ja) == 1 and len(jb) == 1
    assert ja[0].journal_seq == 1 and jb[0].journal_seq == 1  # independent counters


# =========================================================================== #
# Staged invisibility + LayerCommitted visibility                              #
# =========================================================================== #
def test_artifact_staged_never_gains_a_visible_sequence():
    stores, digest = _stores()
    r = _put(stores, digest, _research())
    ev = _append(stores, digest, r, event_type="ArtifactStaged")
    assert ev.event_type is EventType.ARTIFACT_STAGED
    assert ev.visible_seq is None
    assert stores.events.visible("run-1", "main") == ()
    # there is no make_visible / promotion API.
    assert not hasattr(stores.events, "make_visible")


# =========================================================================== #
# Idempotency: identical event retry vs conflicting retry                      #
# =========================================================================== #
def test_identical_event_retry_returns_the_stored_event():
    stores, digest = _stores()
    r = _put(stores, digest, _research())
    e1 = _append(stores, digest, r, event_type="LayerCommitted", idem="e")
    e2 = _append(stores, digest, r, event_type="LayerCommitted", idem="e")
    assert e1.content_digest == e2.content_digest
    assert e1.journal_seq == e2.journal_seq
    assert len(stores.events.journal("run-1", "main")) == 1


def test_conflicting_event_idempotency_key_raises():
    stores, digest = _stores()
    r1 = _put(stores, digest, _research(rationale="a"), idem="p1")
    r2 = _put(stores, digest, _research(rationale="b"), idem="p2")
    _append(stores, digest, r1, event_type="LayerCommitted", idem="e")
    with pytest.raises(IdempotencyConflict):
        _append(stores, digest, r2, event_type="LayerCommitted", idem="e")


# =========================================================================== #
# Namespace boundary — sealed/review/audit cannot back a main-public event      #
# =========================================================================== #
@pytest.mark.parametrize("namespace", ["sealed", "review", "audit"])
def test_non_public_payload_cannot_back_a_main_public_event(namespace):
    stores, digest = _stores()
    ref = _put(stores, digest, _research(), namespace=namespace, idem=f"p-{namespace}")
    with pytest.raises(NamespaceViolation):
        _append(stores, digest, ref, partition="main", event_type="ArtifactStaged")


def test_main_payload_cannot_back_a_non_public_event():
    stores, digest = _stores()
    ref = _put(stores, digest, _research(), namespace="main")
    with pytest.raises(NamespaceViolation):
        _append(stores, digest, ref, partition="sealed", event_type="ArtifactStaged")


# =========================================================================== #
# Callers cannot set cursors/occurred_at + naive clock rejected                 #
# =========================================================================== #
def test_event_append_request_has_no_sequence_or_time_fields():
    fields = set(EventAppendRequest.model_fields)
    for forbidden in ("journal_seq", "visible_seq", "occurred_at", "event_id"):
        assert forbidden not in fields
    with pytest.raises(ValidationError):
        EventAppendRequest(
            run_id="run-1", partition="main", event_type="ArtifactStaged",
            payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="o", content_digest=DA),
            registry_digest=DA, idempotency_key="k", occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
        )


def test_append_signature_exposes_no_seq_or_time_parameter():
    params = set(inspect.signature(es_mod.EventStore.append).parameters)
    assert params == {"self", "request"}


def test_naive_authoritative_clock_is_rejected_at_stamp_time():
    stores, digest = _stores(clock=NaiveClock())
    r = _put(stores, digest, _research())
    with pytest.raises(ValueError):
        _append(stores, digest, r, event_type="LayerCommitted")


# =========================================================================== #
# Replay — reverse/replayed construction reproduces journal/cursors/visible      #
# =========================================================================== #
def test_replay_reproduces_journal_cursors_and_visible_view():
    stores, digest = _stores()
    r1 = _put(stores, digest, _research(rationale="1"), idem="p1")
    r2 = _put(stores, digest, _research(rationale="2"), idem="p2")
    _append(stores, digest, r1, event_type="ArtifactStaged", idem="e1")
    _append(stores, digest, r2, event_type="LayerCommitted", idem="e2")

    original_journal = stores.events.journal("run-1", "main")
    original_visible = stores.events.visible("run-1", "main")

    # a fresh EventStore rebuilt purely from the persisted RunEvents (no cached
    # counters) reproduces the same journal, visibility indexes and cursors.
    replayed = stores.events.replay()
    assert replayed.journal("run-1", "main") == original_journal
    assert replayed.visible("run-1", "main") == original_visible
    assert [e.journal_seq for e in replayed.journal("run-1", "main")] == [1, 2]
    assert [e.visible_seq for e in replayed.visible("run-1", "main")] == [1]


# =========================================================================== #
# RuntimeUnitOfWork — atomic payload+event+budget batches (all-or-none)          #
# =========================================================================== #
def _plan_run_budget() -> RunBudget:
    return RunBudget(ledger_id="ledger-1", max_tokens=5000, max_llm_invocations=40, max_concurrency=8)


def _reserve_plan_command(idem="rp") -> BudgetTransitionCommand:
    return BudgetTransitionCommand(
        operation="reserve_plan",
        semantic_args=budget_mod.ReservePlanArgs(
            request_id="req-1", candidate_plan_digest=DA,
            reserved_tokens=1000, reserved_llm_invocations=10, reserved_concurrency=2),
        idempotency_key=idem,
    )


def test_uow_commits_payload_event_and_budget_atomically():
    stores, digest = _stores()
    stores.bind_run_budget(run_id="run-1", run_budget=_plan_run_budget())
    batch = RuntimeBatch(
        idempotency_key="batch-1",
        budget_run_id="run-1",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"),
            schema_ref=SchemaRef(name="ResearchPlan", version="1"),
            namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY,
                              "rationale": "thesis"},
            registry_digest=digest, idempotency_key="pa"),),
        event_appends=(EventAppendCommand(
            run_id="run-1", partition="main", event_type="LayerCommitted",
            payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
            payload_target=StagedPayloadKey(key="a"),
            registry_digest=digest, idempotency_key="ea"),),
        budget_commands=(_reserve_plan_command(),),
    )
    result = stores.unit_of_work.commit(batch)
    final = result.payload_ref("a")
    assert isinstance(final, PayloadRef)
    # the event references the finally-allocated ref, never a pre-commit locator.
    ev = stores.events.journal("run-1", "main")[0]
    assert ev.payload_ref == final
    assert ev.visible_seq == 1
    # the budget event is committed to the same backend and folds.
    ledger = BudgetLedger(sink=stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1"),
                          run_budget=_plan_run_budget())
    assert ledger.get_active_plan("req-1", DA) is not None
    assert stores.payloads.get(final, expected_schema_ref=SchemaRef(name="ResearchPlan", version="1")) == _research()


def test_uow_mid_batch_failure_leaves_no_orphan_payload_event_or_budget():
    stores, digest = _stores()
    stores.bind_run_budget(run_id="run-1", run_budget=_plan_run_budget())
    # the event command targets an unknown staged key → the whole batch fails.
    batch = RuntimeBatch(
        idempotency_key="batch-bad",
        budget_run_id="run-1",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"),
            schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY,
                              "rationale": "thesis"},
            registry_digest=digest, idempotency_key="pa"),),
        event_appends=(EventAppendCommand(
            run_id="run-1", partition="main", event_type="LayerCommitted",
            payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
            payload_target=StagedPayloadKey(key="does-not-exist"),
            registry_digest=digest, idempotency_key="ea"),),
        budget_commands=(_reserve_plan_command(),),
    )
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(batch)
    # nothing was published: no journal row, no budget event, no readable payload.
    assert stores.events.journal("run-1", "main") == ()
    sink = stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1")
    assert sink.budget_events() == ()


def test_uow_budget_precondition_failure_aborts_the_whole_batch():
    # a plan over-reservation against the run budget aborts payload + event too.
    resolver = SchemaRegistryResolver()
    digest = resolver.register(default_registry())
    stores = RuntimeStores(resolver=resolver, clock=AdvancingClock())
    stores.bind_run_budget(run_id="run-1", run_budget=_plan_run_budget())
    over = BudgetTransitionCommand(
        operation="reserve_plan",
        semantic_args=budget_mod.ReservePlanArgs(
            request_id="req-1", candidate_plan_digest=DA,
            reserved_tokens=999999, reserved_llm_invocations=1, reserved_concurrency=1),
        idempotency_key="rp-over",
    )
    batch = RuntimeBatch(
        idempotency_key="batch-over", budget_run_id="run-1",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"),
            schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY,
                              "rationale": "thesis"},
            registry_digest=digest, idempotency_key="pa"),),
        budget_commands=(over,),
    )
    with pytest.raises(BudgetExceeded):
        stores.unit_of_work.commit(batch)
    assert stores.events.journal("run-1", "main") == ()
    assert stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1").budget_events() == ()


def test_batch_cannot_carry_its_own_run_budget():
    # the RunBudget is service authority bound once per run via bind_run_budget;
    # a batch structurally cannot carry maxima of its own (extra=forbid).
    with pytest.raises(ValidationError):
        RuntimeBatch(
            idempotency_key="batch-rb", budget_run_id="run-1",
            budget_run_budget=_plan_run_budget(),
            budget_commands=(_reserve_plan_command(),),
        )


def test_bind_run_budget_rejects_a_non_maxima_only_run_budget():
    # inherited Task 1 semantics: the event fold owns all holds.
    stores, _ = _stores()
    with pytest.raises(EventStoreError):
        stores.bind_run_budget(run_id="run-1", run_budget=RunBudget(
            ledger_id="ledger-1", max_tokens=5000, max_llm_invocations=40,
            max_concurrency=8, reserved_tokens=100))


def test_rebinding_a_different_run_budget_for_the_same_run_is_rejected():
    """Two batches for one run can never validate against inconsistent maxima:
    the second bind with different maxima fails with an honest error, no budget
    event is ever appended against the wrong ceiling, and commits keep
    validating against the originally bound authority."""
    stores, digest = _stores()
    stores.bind_run_budget(run_id="run-1", run_budget=RunBudget(
        ledger_id="ledger-1", max_tokens=1000, max_llm_invocations=10, max_concurrency=2))
    with pytest.raises(EventStoreError) as excinfo:
        stores.bind_run_budget(run_id="run-1", run_budget=RunBudget(
            ledger_id="ledger-1", max_tokens=5000, max_llm_invocations=10, max_concurrency=2))
    assert "already has a bound RunBudget" in str(excinfo.value)
    # a reservation above the ORIGINAL maxima still fails — the inflated ceiling
    # never took effect and no event was appended.
    over = BudgetTransitionCommand(
        operation="reserve_plan",
        semantic_args=budget_mod.ReservePlanArgs(
            request_id="req-1", candidate_plan_digest=DA,
            reserved_tokens=4000, reserved_llm_invocations=1, reserved_concurrency=1),
        idempotency_key="rp-4000")
    with pytest.raises(BudgetExceeded):
        stores.unit_of_work.commit(RuntimeBatch(
            idempotency_key="b-over", budget_run_id="run-1", budget_commands=(over,)))
    assert stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1").budget_events() == ()


def test_identical_rebind_is_idempotent_and_batches_use_the_bound_maxima():
    stores, digest = _stores()
    rb = _plan_run_budget()
    stores.bind_run_budget(run_id="run-1", run_budget=rb)
    stores.bind_run_budget(run_id="run-1", run_budget=rb)  # idempotent, no error
    stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="b-ok", budget_run_id="run-1",
        budget_commands=(_reserve_plan_command(idem="rp-ok"),)))
    # replay is unaffected: a fresh Task 1 ledger over the production sink folds
    # the committed event identically.
    ledger = BudgetLedger(
        sink=stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1"), run_budget=rb)
    assert ledger.get_active_plan("req-1", DA) is not None
    assert ledger.available().tokens == rb.max_tokens - 1000


def test_uow_budget_commands_without_a_bound_run_budget_fail_honestly():
    stores, _ = _stores()
    with pytest.raises(UnitOfWorkError) as excinfo:
        stores.unit_of_work.commit(RuntimeBatch(
            idempotency_key="b-unbound", budget_run_id="run-1",
            budget_commands=(_reserve_plan_command(),)))
    assert "bind_run_budget" in str(excinfo.value)
    assert stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1").budget_events() == ()


def test_identical_uow_replay_returns_the_original_result():
    stores, digest = _stores()

    def make_batch():
        return RuntimeBatch(
            idempotency_key="batch-x",
            payload_puts=(PayloadPutCommand(
                staged_key=StagedPayloadKey(key="a"),
                schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
                payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY,
                                  "rationale": "thesis"},
                registry_digest=digest, idempotency_key="pa"),),
            event_appends=(EventAppendCommand(
                run_id="run-1", partition="main", event_type="LayerCommitted",
                payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
                payload_target=StagedPayloadKey(key="a"),
                registry_digest=digest, idempotency_key="ea"),),
        )

    r1 = stores.unit_of_work.commit(make_batch())
    r2 = stores.unit_of_work.commit(make_batch())
    assert r1.payload_ref("a") == r2.payload_ref("a")
    # the whole-batch replay did not append a second event.
    assert len(stores.events.journal("run-1", "main")) == 1


def _layer_barrier_batch(digest, *, idem, break_it=False):
    """Two staged artifacts + their ArtifactStaged events + one LayerCommitted.

    ``break_it`` injects a mid-batch failure (an unknown staged target on the
    final LayerCommitted command) so the crash-before arm of the matrix can prove
    none of the layer is exposed.
    """
    def art(key, rationale, idem_suffix):
        return PayloadPutCommand(
            staged_key=StagedPayloadKey(key=key),
            schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY,
                              "rationale": rationale},
            registry_digest=digest, idempotency_key=f"{idem}-{idem_suffix}")

    def staged_ev(key, idem_suffix):
        return EventAppendCommand(
            run_id="run-L", partition="main", event_type="ArtifactStaged",
            payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
            payload_target=StagedPayloadKey(key=key),
            registry_digest=digest, idempotency_key=f"{idem}-{idem_suffix}")

    barrier_target = StagedPayloadKey(key="missing" if break_it else "art1")
    return RuntimeBatch(
        idempotency_key=idem,
        payload_puts=(art("art1", "first artifact", "p1"), art("art2", "second artifact", "p2")),
        event_appends=(
            staged_ev("art1", "e1"), staged_ev("art2", "e2"),
            EventAppendCommand(
                run_id="run-L", partition="main", event_type="LayerCommitted",
                payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
                payload_target=barrier_target,
                registry_digest=digest, idempotency_key=f"{idem}-barrier"),
        ),
    )


def test_crash_before_layer_committed_batch_exposes_no_artifact_replay_after_exposes_all():
    stores, digest = _stores()
    # crash BEFORE the atomic barrier batch commits: nothing is exposed at all —
    # no staged payload, no journal row and certainly nothing visible.
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(_layer_barrier_batch(digest, idem="layer-x", break_it=True))
    assert stores.events.journal("run-L", "main") == ()
    assert stores.events.visible("run-L", "main") == ()
    assert stores.payloads_object_count() == 0

    # the same batch, committed successfully: the barrier makes the layer visible.
    stores.unit_of_work.commit(_layer_barrier_batch(digest, idem="layer-x2"))
    journal = stores.events.journal("run-L", "main")
    assert [e.event_type for e in journal] == [
        EventType.ARTIFACT_STAGED, EventType.ARTIFACT_STAGED, EventType.LAYER_COMMITTED]
    assert [e.visible_seq for e in journal] == [None, None, 1]
    # replay after the batch exposes all of it identically.
    replayed = stores.events.replay()
    assert replayed.journal("run-L", "main") == journal
    assert [e.event_type for e in replayed.visible("run-L", "main")] == [EventType.LAYER_COMMITTED]


# =========================================================================== #
# Staged-ref nested control / event targets (sentinel resolution + rejection)   #
# =========================================================================== #
def test_uow_resolves_nested_staged_ref_into_a_typed_payload_without_precommit_id():
    stores, digest = _stores()
    # put A (ResearchPlan), then put B (a TypedPayloadRef whose payload_ref is a
    # staged sentinel pointing at A) — a nested control fact resolved by the UoW.
    batch = RuntimeBatch(
        idempotency_key="batch-nested",
        payload_puts=(
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="a"),
                schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
                payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY, "rationale": "thesis"},
                registry_digest=digest, idempotency_key="pa"),
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="b"),
                schema_ref=SchemaRef(name="TypedPayloadRef", version="1"), namespace="main",
                payload_template={
                    "schema_version": "1",
                    "schema_ref": {"schema_version": "1", "name": "ResearchPlan", "version": "1"},
                    "payload_ref": StagedPayloadRef(
                        staged_key=StagedPayloadKey(key="a"),
                        schema_ref=SchemaRef(name="ResearchPlan", version="1"),
                        namespace="main"),
                },
                registry_digest=digest, idempotency_key="pb"),
        ),
    )
    result = stores.unit_of_work.commit(batch)
    ref_a = result.payload_ref("a")
    typed = stores.payloads.get(result.payload_ref("b"),
                                expected_schema_ref=SchemaRef(name="TypedPayloadRef", version="1"))
    assert isinstance(typed, TypedPayloadRef)
    # the nested ref was substituted with A's finally-allocated locator.
    assert typed.payload_ref.content_digest == ref_a.content_digest
    assert typed.payload_ref.object_id == ref_a.object_id


def test_uow_rejects_staged_ref_with_wrong_declared_schema():
    stores, digest = _stores()
    batch = RuntimeBatch(
        idempotency_key="batch-wrong-schema",
        payload_puts=(
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="a"),
                schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
                payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY, "rationale": "thesis"},
                registry_digest=digest, idempotency_key="pa"),
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="b"),
                schema_ref=SchemaRef(name="TypedPayloadRef", version="1"), namespace="main",
                payload_template={
                    "schema_version": "1",
                    "schema_ref": {"schema_version": "1", "name": "PortfolioDecision", "version": "1"},
                    "payload_ref": StagedPayloadRef(
                        staged_key=StagedPayloadKey(key="a"),
                        schema_ref=SchemaRef(name="PortfolioDecision", version="1"),  # A is ResearchPlan
                        namespace="main"),
                },
                registry_digest=digest, idempotency_key="pb"),
        ),
    )
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(batch)
    assert stores.payloads_object_count() == 0


def test_uow_rejects_unknown_duplicate_and_forward_cyclic_staged_keys():
    stores, digest = _stores()

    def put(key, dep_key, idem):
        return PayloadPutCommand(
            staged_key=StagedPayloadKey(key=key),
            schema_ref=SchemaRef(name="TypedPayloadRef", version="1"), namespace="main",
            payload_template={
                "schema_version": "1",
                "schema_ref": {"schema_version": "1", "name": "ResearchPlan", "version": "1"},
                "payload_ref": StagedPayloadRef(
                    staged_key=StagedPayloadKey(key=dep_key),
                    schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main"),
            },
            registry_digest=digest, idempotency_key=idem)

    # forward reference (a depends on b declared later) is a cyclic/forward ref.
    forward = RuntimeBatch(idempotency_key="b-fwd",
                           payload_puts=(put("a", "b", "pa"), put("b", "a", "pb")))
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(forward)

    # duplicate staged key.
    base = PayloadPutCommand(
        staged_key=StagedPayloadKey(key="dup"),
        schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
        payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY, "rationale": "thesis"},
        registry_digest=digest, idempotency_key="p1")
    dup = RuntimeBatch(idempotency_key="b-dup", payload_puts=(base, base))
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(dup)

    # unknown staged key referenced by an event target.
    unknown = RuntimeBatch(
        idempotency_key="b-unknown",
        event_appends=(EventAppendCommand(
            run_id="run-1", partition="main", event_type="LayerCommitted",
            payload_schema_ref=SchemaRef(name="ResearchPlan", version="1"),
            payload_target=StagedPayloadKey(key="nope"),
            registry_digest=digest, idempotency_key="ea"),))
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(unknown)


def test_uow_semantic_drift_on_batch_retry_conflicts():
    stores, digest = _stores()

    def batch(rationale):
        return RuntimeBatch(
            idempotency_key="batch-drift",
            payload_puts=(PayloadPutCommand(
                staged_key=StagedPayloadKey(key="a"),
                schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
                payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY, "rationale": rationale},
                registry_digest=digest, idempotency_key="pa"),))

    stores.unit_of_work.commit(batch("first"))
    with pytest.raises(IdempotencyConflict):
        stores.unit_of_work.commit(batch("second"))  # same batch key, different content


# =========================================================================== #
# State-cell CAS — put + head-cell + result-cell all-or-none; stale/wrong fail   #
# =========================================================================== #
HEAD_NS = "runtime.repository_head"
RESULT_NS = "runtime.operation_result"
HK = "1" * 64  # head cell key digest
RK = "2" * 64  # result cell key digest


def _cell_stores(clock=None):
    resolver = SchemaRegistryResolver()
    digest = resolver.register(default_registry())
    stores = RuntimeStores(resolver=resolver, clock=clock or AdvancingClock(),
                           allowed_cell_namespaces=(HEAD_NS, RESULT_NS))
    return stores, digest


def _staged_typed(key="a"):
    return StagedTypedPayloadRef(
        staged_key=StagedPayloadKey(key=key),
        schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main")


def _put_and_cas_batch(digest, *, idem, expected_head, rationale="thesis"):
    return RuntimeBatch(
        idempotency_key=idem,
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"),
            schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY, "rationale": rationale},
            registry_digest=digest, idempotency_key=idem + "-pa"),),
        cell_cas=(
            StateCellCompareAndSwapCommand(
                cell_namespace=HEAD_NS, cell_key_digest=HK,
                expected_value=expected_head, new_target=_staged_typed("a")),
            StateCellCompareAndSwapCommand(
                cell_namespace=RESULT_NS, cell_key_digest=RK,
                expected_value=None, new_target=_staged_typed("a")),
        ),
    )


def test_uow_put_plus_two_cell_cas_commit_all_or_none():
    stores, digest = _cell_stores()
    result = stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    typed = result.staged_typed_ref("a")
    assert stores.cells.load(HEAD_NS, HK).semantic_digest() == typed.semantic_digest()
    assert stores.cells.load(RESULT_NS, RK).semantic_digest() == typed.semantic_digest()


def test_uow_cell_load_unknown_namespace_fails():
    stores, _ = _cell_stores()
    with pytest.raises(EventStoreError):
        stores.cells.load("runtime.unregistered", HK)


def test_same_operation_replay_returns_original_ref_after_head_advance():
    stores, digest = _cell_stores()
    op1 = stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    head_after_op1 = stores.cells.load(HEAD_NS, HK)

    # a second, distinct op advances the head (expected = current head).
    op2 = stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="op2",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"),
            schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.SELL, "rationale": "next"},
            registry_digest=digest, idempotency_key="op2-pa"),),
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest=HK,
            expected_value=head_after_op1, new_target=_staged_typed("a")),)))
    assert stores.cells.load(HEAD_NS, HK).semantic_digest() == op2.staged_typed_ref("a").semantic_digest()

    # replaying op1 (identical whole batch) returns op1's original typed ref even
    # though the head has since advanced — the head is NOT rolled back.
    replay = stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    assert replay.staged_typed_ref("a").semantic_digest() == op1.staged_typed_ref("a").semantic_digest()
    assert stores.cells.load(HEAD_NS, HK).semantic_digest() == op2.staged_typed_ref("a").semantic_digest()


def test_new_operation_with_stale_expected_head_fails_without_writes():
    stores, digest = _cell_stores()
    stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    head = stores.cells.load(HEAD_NS, HK)
    stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="op2",
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest=HK,
            expected_value=head, new_target=_staged_typed_concrete(stores, digest)),)))
    head_now = stores.cells.load(HEAD_NS, HK)

    # op3 expects the STALE head (before op2), so its CAS must fail with no writes.
    stale = RuntimeBatch(
        idempotency_key="op3",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"),
            schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.HOLD, "rationale": "stale"},
            registry_digest=digest, idempotency_key="op3-pa"),),
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest=HK,
            expected_value=head, new_target=_staged_typed("a")),))  # head is stale now
    before = stores.payloads_object_count()
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(stale)
    assert stores.cells.load(HEAD_NS, HK).semantic_digest() == head_now.semantic_digest()
    assert stores.payloads_object_count() == before  # no orphan payload


def _staged_typed_concrete(stores, digest):
    # a concrete typed ref (advance the head to a real second payload).
    ref = stores.payloads.put(
        SchemaRef(name="ResearchPlan", version="1"), _research(rationale="advance"),
        registry_digest=digest, namespace="main", idempotency_key="advance-pa")
    return TypedPayloadRef(schema_ref=SchemaRef(name="ResearchPlan", version="1"), payload_ref=ref)


def test_cas_wrong_expected_typed_ref_fails():
    stores, digest = _cell_stores()
    stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    wrong_expected = TypedPayloadRef(
        schema_ref=SchemaRef(name="ResearchPlan", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="wrong", content_digest=DB))
    bad = RuntimeBatch(
        idempotency_key="op-bad",
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest=HK,
            expected_value=wrong_expected, new_target=_staged_typed_concrete(stores, digest)),))
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(bad)


def test_cas_cross_batch_staged_target_fails():
    stores, digest = _cell_stores()
    # batch 1 stages payload "a"; batch 2 tries to CAS to the staged key "a" —
    # a cross-batch staged target is unknown to batch 2 and must fail.
    stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="b1",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"),
            schema_ref=SchemaRef(name="ResearchPlan", version="1"), namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY,
                              "rationale": "thesis"},
            registry_digest=digest, idempotency_key="b1-pa"),)))
    cross = RuntimeBatch(
        idempotency_key="b2",
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest=HK,
            expected_value=None, new_target=_staged_typed("a")),))
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(cross)
    assert stores.cells.load(HEAD_NS, HK) is None


def test_cas_into_unregistered_namespace_fails():
    stores, digest = _cell_stores()
    bad = RuntimeBatch(
        idempotency_key="op-ns",
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace="runtime.not_allowed", cell_key_digest=HK,
            expected_value=None, new_target=_staged_typed_concrete(stores, digest)),))
    with pytest.raises(EventStoreError):
        stores.unit_of_work.commit(bad)


# =========================================================================== #
# Binding constraint 2 — production sink runs Task 1's exact command vectors     #
# =========================================================================== #
def test_production_budget_sink_supports_the_full_task1_command_matrix():
    """The production BudgetEventSink must validate/commit the exact same
    BudgetTransitionCommands Task 1 defined, and a BudgetLedger over it must have
    the same fold authority, block same-head over-reserve and replay equally."""
    stores, _ = _stores()
    sink = stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1")
    rb = RunBudget(ledger_id="ledger-1", max_tokens=1000, max_llm_invocations=40, max_concurrency=8)
    ledger = BudgetLedger(sink=sink, run_budget=rb)

    plan = ledger.reserve_plan(
        request_id="req-1", candidate_plan_digest=DA,
        budget_request=BudgetRequest(tokens=1000, llm_invocations=10, concurrency=4),
        idempotency_key="rp")
    ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n_a", attempt=1,
        tokens=600, llm_invocations=3, concurrency=1, idempotency_key="na")
    # same-head over-reserve is blocked by the re-folded live head.
    with pytest.raises(BudgetExceeded):
        ledger.reserve_node(
            plan_reservation_id=plan.reservation_id, node_id="n_b", attempt=1,
            tokens=600, llm_invocations=3, concurrency=1, idempotency_key="nb")

    # fold authority: a fresh ledger over the same committed events is identical.
    replay = BudgetLedger(sink=stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1"),
                          run_budget=rb)
    assert replay.available() == ledger.available()
    assert replay.get_active_plan("req-1", DA).semantic_digest() == plan.semantic_digest()
    # the folded state matches the module fold over the sink's raw events.
    assert fold_budget_events(sink.budget_events()).reservations.keys() == ledger.replay().reservations.keys()


def test_production_sink_is_a_budget_event_sink_and_conflicts_on_drift():
    from guanlan_v2.orchestration.budget import BudgetEventSink
    stores, _ = _stores()
    sink = stores.budget_event_sink(run_id="run-1", ledger_id="ledger-1")
    assert isinstance(sink, BudgetEventSink)
    c = _reserve_plan_command(idem="k1")
    sink.append(c)
    assert sink.find_by_idempotency_key("k1") is not None
    # a same-key different-command commit conflicts through the shared type.
    drift = BudgetTransitionCommand(
        operation="reserve_plan",
        semantic_args=budget_mod.ReservePlanArgs(
            request_id="req-1", candidate_plan_digest=DA,
            reserved_tokens=2000, reserved_llm_invocations=10, reserved_concurrency=2),
        idempotency_key="k1")
    with pytest.raises(IdempotencyConflict):
        sink.append(drift)


# =========================================================================== #
# Strict-fact (non-DigestModel) payload digesting — the Task-6 unlock          #
# =========================================================================== #
def _phase2_stores():
    """Stores whose resolver additionally carries the cumulative Phase-2 registry
    (which registers strict non-DigestModel runtime facts like RuntimeSupportIssue)."""
    from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry

    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    rt_digest = resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    return RuntimeStores(resolver=resolver, clock=AdvancingClock()), rt_digest


_ISSUE_SR = SchemaRef(name="RuntimeSupportIssue", version="1")


def _issue_fact():
    from guanlan_v2.orchestration.runtime_contracts import RuntimeSupportIssue

    return RuntimeSupportIssue(code="x", model_path="draft.nodes", explanation="why")


def test_strict_fact_put_get_round_trip_digests_via_canonical_json_dump():
    from guanlan_v2.orchestration.runtime_contracts import RuntimeSupportIssue

    stores, rt_digest = _phase2_stores()
    fact = _issue_fact()
    ref = stores.payloads.put(_ISSUE_SR, fact, registry_digest=rt_digest,
                              namespace="main", idempotency_key="sf-1")
    # a strict non-DigestModel fact digests over its canonical JSON dump — the
    # exact reviewed path the Task-2 EventRefusalAuditSink uses for its details.
    assert ref.content_digest == content_digest(fact.model_dump(mode="json"))
    got = stores.payloads.get(ref, expected_schema_ref=_ISSUE_SR)
    assert isinstance(got, RuntimeSupportIssue) and got == fact


def test_strict_fact_digest_stable_across_identical_instances():
    stores, rt_digest = _phase2_stores()
    r1 = stores.payloads.put(_ISSUE_SR, _issue_fact(), registry_digest=rt_digest,
                             namespace="main", idempotency_key="sf-a")
    r2 = stores.payloads.put(_ISSUE_SR, _issue_fact(), registry_digest=rt_digest,
                             namespace="main", idempotency_key="sf-b")
    assert r1.content_digest == r2.content_digest  # one content identity
    assert r1.object_id != r2.object_id  # two audit locators


def test_strict_fact_content_tamper_rejected_on_get():
    from guanlan_v2.orchestration.runtime_contracts import RuntimeSupportIssue

    stores, rt_digest = _phase2_stores()
    ref = stores.payloads.put(_ISSUE_SR, _issue_fact(), registry_digest=rt_digest,
                              namespace="main", idempotency_key="sf-t")
    # a ref claiming different content is refused.
    wrong = PayloadRef(namespace="main", object_id=ref.object_id, content_digest=DB)
    with pytest.raises(ContentDigestMismatch):
        stores.payloads.get(wrong, expected_schema_ref=_ISSUE_SR)
    # a tampered stored model (mutated behind the store) is refused on read.
    stores._shared.backend.payloads[ref.object_id].model = RuntimeSupportIssue(
        code="tampered", model_path="draft.nodes", explanation="why")
    with pytest.raises(ContentDigestMismatch):
        stores.payloads.get(ref, expected_schema_ref=_ISSUE_SR)


def test_digest_model_payload_path_unchanged_by_strict_fact_support():
    stores, digest = _stores()
    ref = _put(stores, digest, _research())
    # a DigestModel still digests through its own canonical semantic projection.
    assert ref.content_digest == content_digest(_research())
    got = stores.payloads.get(ref, expected_schema_ref=SchemaRef(name="ResearchPlan", version="1"))
    assert got == _research()
