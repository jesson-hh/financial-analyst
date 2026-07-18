# -*- coding: utf-8 -*-
"""Task 5 — event-sourced cross-run :class:`TrialLedger` over the Phase 2 stores.

Written test-first: with ``trial_ledger.py`` absent these tests are RED on the
missing module/behaviour (not a collection error elsewhere). They exercise the
eight brief invariants over the real Phase 2 in-memory stores
(``RuntimeStores`` — ``PayloadStore`` / ``EventStore`` / ``RuntimeStateCellStore``
/ ``RuntimeUnitOfWork``) with a deterministic clock and the Task-3 Trial event
vocabulary:

1. reserve→reveal of one triple across two ledger instances yields one raw trial;
   the second ``reserve_validation`` returns a reused record and
   ``raw_trial_count`` stays 1;
2. same idempotency key + same content replays the stored record; same key +
   different content raises ``IdempotencyConflict``;
3. the holdout one-shot lease: a second candidate on the same ``(family, window)``
   fails before any evaluation; the same candidate replays the reservation;
4. every terminal holdout status closes the lease; a repeat ``exhaust_holdout``
   returns the byte-identical receipt; no API moves ``exhausted → reserved``;
5. window registration rejects overlap / missing prior acknowledgment / unmatured
   ``matured_at`` / attestation mismatch;
6. the ledger only accepts a ``StudySpec`` (a hand-built family is impossible); a
   tampered family head cell payload fails ``verify_family_attestation`` on read;
7. holdout terminals put only a ``HoldoutReceipt`` on the ``main`` journal while
   the full terminal ``TrialRecord`` lives on the ``audit`` partition;
8. ``replay`` reproduces ``effective_trial_stats`` and lease states exactly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.eventstore import (
    IdempotencyConflict,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.governor import derive_study_family
from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry
from guanlan_v2.orchestration.trial import (
    HoldoutWindow,
    OptimizeCandidate,
    SplitSpec,
    StudyFamily,
    StudySpec,
    TrialRecord,
    ValidationMetrics,
)
from guanlan_v2.orchestration.trial_ledger import (
    PHASE4_STATE_CELL_NAMESPACES,
    PHASE4_TRIAL_STATE_CELL_NAMESPACES,
    L1_METRIC_NAMES,
    TRIAL_TRIPLE_DOMAIN,
    FamilyAttestationError,
    HoldoutLeaseExhaustedError,
    HoldoutWindowOverlapError,
    TrialLedger,
    TrialReuseViolation,
    UnknownTrialError,
    compute_holdout_window_attestation,
    trial_triple_key,
)

UTC = timezone.utc
DH = "d" * 64  # a filler DigestHex
CH = "c" * 64


def _SR(name: str) -> SchemaRef:
    return SchemaRef(name=name, version="1")


class SteppingClock:
    """Deterministic advancing clock (each ``now()`` +1s; settable)."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 7, 17, 1, 0, tzinfo=UTC)

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


def _phase4_test_registry() -> Phase2RuntimeRegistry:
    """A minimal sealed registry carrying exactly the payloads the ledger persists.

    Task 9 freezes the cumulative Phase-4 registry golden; Task 5 needs only the
    handful of models it puts, so a self-contained sealed registry keeps the ledger
    test independent of the (later) registry chain.
    """
    reg = Phase2RuntimeRegistry()
    for model in (
        StudySpec,
        StudyFamily,
        SplitSpec,
        OptimizeCandidate,
        ValidationMetrics,
        HoldoutWindow,
        TrialRecord,
    ):
        reg.register(model)
    # HoldoutReceipt registered by name via the trial module import.
    from guanlan_v2.orchestration.trial import HoldoutReceipt

    reg.register(HoldoutReceipt)
    reg.seal()
    return reg


class _Env:
    def __init__(self, ids: list[str] | None = None) -> None:
        self.resolver = SchemaRegistryResolver()
        self.registry = _phase4_test_registry()
        self.digest = self.resolver.register(self.registry)
        self.clock = SteppingClock()
        self.stores = RuntimeStores(
            resolver=self.resolver,
            clock=self.clock,
            allowed_cell_namespaces=PHASE4_STATE_CELL_NAMESPACES,
        )
        self._ids = ids
        self._seq = 0

    def _id_factory(self) -> str:
        if self._ids is not None:
            v = self._ids[self._seq % len(self._ids)]
        else:
            v = f"tok{self._seq}"
        self._seq += 1
        return v

    def ledger(self) -> TrialLedger:
        return TrialLedger(
            event_store=self.stores.events,
            payload_store=self.stores.payloads,
            state_cells=self.stores.cells,
            registry=self.registry,
            clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work,
            id_factory=self._id_factory,
        )

    # -- payload helpers ---------------------------------------------------- #
    def put_candidate(self, expr: str = "x") -> tuple[TypedPayloadRef, OptimizeCandidate]:
        cand = OptimizeCandidate.build(
            candidate_kind="workflow_graph",
            graph={"nodes": [{"id": "a", "type": "formula", "params": {"expr": expr}}], "edges": []},
            params={},
        )
        ref = self.stores.payloads.put(
            _SR("OptimizeCandidate"), cand, registry_digest=self.digest,
            namespace="main", idempotency_key=f"cand:{expr}")
        return TypedPayloadRef(schema_ref=_SR("OptimizeCandidate"), payload_ref=ref), cand

    def put_split(self, horizon: int = 5) -> tuple[TypedPayloadRef, SplitSpec]:
        split = SplitSpec(scheme="oos_fraction", oos_frac=0.3, label_horizon=horizon)
        ref = self.stores.payloads.put(
            _SR("SplitSpec"), split, registry_digest=self.digest,
            namespace="main", idempotency_key=f"split:{horizon}")
        return TypedPayloadRef(schema_ref=_SR("SplitSpec"), payload_ref=ref), split

    def put_window(
        self, family: StudyFamily, *, window_id: str,
        start: datetime, end: datetime, matured: datetime,
        prior: tuple[str, ...] = (), attestation: str | None = None,
    ) -> tuple[TypedPayloadRef, HoldoutWindow]:
        att = attestation or compute_holdout_window_attestation(
            family_identity_digest=family.identity_digest,
            start_at=start, end_at=end, prior_window_ids=prior)
        win = HoldoutWindow(
            holdout_window_id=window_id, family_identity_digest=family.identity_digest,
            start_at=start, end_at=end, matured_at=matured,
            data_snapshot_id="snap-" + window_id, vintage_manifest_digest="1" * 64,
            prior_window_ids=prior, non_overlap_attestation=att)
        ref = self.stores.payloads.put(
            _SR("HoldoutWindow"), win, registry_digest=self.digest,
            namespace="main", idempotency_key=f"win:{window_id}:{att[:8]}")
        return TypedPayloadRef(schema_ref=_SR("HoldoutWindow"), payload_ref=ref), win


def _study(*, objective: str = "find alpha", freq: str = "monthly",
           odigest: str = "a" * 64) -> StudySpec:
    return StudySpec(
        objective=objective, objective_digest=odigest,
        label_definition="20d fwd return", label_digest="b" * 64,
        universe_digest="e" * 64, frequency=freq, split_policy_digest="f" * 64)


# --------------------------------------------------------------------------- #
# constants / namespace surface                                                #
# --------------------------------------------------------------------------- #
def test_namespace_union_is_canonical_and_includes_trial_names():
    from guanlan_v2.orchestration.memory.models import PHASE3_MEMORY_STATE_CELL_NAMESPACES

    assert PHASE4_TRIAL_STATE_CELL_NAMESPACES == (
        "trial.experiment_head.v1", "trial.family_head.v1",
        "trial.holdout_lease.v1", "trial.window_head.v1")
    # canonical lexicographic union of the memory + trial namespaces.
    expected = tuple(sorted(
        set(PHASE3_MEMORY_STATE_CELL_NAMESPACES) | set(PHASE4_TRIAL_STATE_CELL_NAMESPACES)))
    assert PHASE4_STATE_CELL_NAMESPACES == expected
    assert set(PHASE4_TRIAL_STATE_CELL_NAMESPACES) <= set(PHASE4_STATE_CELL_NAMESPACES)


def test_trial_triple_key_is_domain_tagged_and_field_sensitive():
    base = dict(family_id="fam.0123456789abcdef", candidate_hash="a" * 64,
                data_snapshot_hash="b" * 64, split_spec_hash="c" * 64,
                code_prompt_model_hash="e" * 64)
    k = trial_triple_key(**base)
    assert k == content_digest({"domain": TRIAL_TRIPLE_DOMAIN, **base})
    # each handle perturbs the key.
    for field in ("candidate_hash", "data_snapshot_hash", "split_spec_hash",
                  "code_prompt_model_hash"):
        other = dict(base)
        other[field] = "f" * 64
        assert trial_triple_key(**other) != k


# --------------------------------------------------------------------------- #
# 1. reserve → reveal happy path + cross-instance reuse (invariant 1)          #
# --------------------------------------------------------------------------- #
def test_reserve_then_reveal_emits_exactly_two_main_events():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    cand_ref, _ = env.put_candidate("x")
    split_ref, _ = env.put_split()

    reserved = led.reserve_validation(
        study, cand_ref, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="idem-1")
    assert reserved.stage == "validation"
    assert reserved.status == "reserved"

    revealed = led.reveal_validation(reserved.trial_id, "art-1", "a" * 64)
    assert revealed.status == "revealed"
    assert revealed.metrics_revealed == L1_METRIC_NAMES
    assert revealed.trial_id == reserved.trial_id

    journal = env.stores.events.journal(fam.family_id, "main")
    assert [e.event_type for e in journal] == [
        EventType.TRIAL_RESERVED, EventType.TRIAL_REVEALED]
    assert [e.payload_schema_ref.name for e in journal] == ["TrialRecord", "TrialRecord"]
    # each event's declared digest matches its recomputed semantic digest.
    for e in journal:
        assert e.content_digest == e.semantic_digest()


def test_second_instance_reuses_revealed_triple_without_new_raw_trial():
    env = _Env()
    study = _study()
    cand_ref, _ = env.put_candidate("x")
    split_ref, _ = env.put_split()

    led1 = env.ledger()
    reserved = led1.reserve_validation(
        study, cand_ref, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="idem-1")
    led1.reveal_validation(reserved.trial_id, "art-1", "a" * 64)
    assert led1.effective_trial_stats(study)["raw_trial_count"] == 1

    # a second "process" — a fresh ledger over the same stores (replay).
    led2 = led1.replay(derive_study_family(study).family_id)
    reused = led2.reserve_validation(
        study, cand_ref, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="idem-2")
    assert reused.status == "revealed"
    assert reused.reused_from_trial_id == reserved.trial_id
    assert reused.result_digest == "a" * 64
    # no new raw trial was minted — the triple stays a single revealed trial.
    assert led2.effective_trial_stats(study)["raw_trial_count"] == 1
    # and no *new* TrialReserved raw-trial event was emitted for the reuse.
    fam = derive_study_family(study)
    reserved_events = [
        e for e in env.stores.events.journal(fam.family_id, "main")
        if e.event_type is EventType.TRIAL_RESERVED]
    assert len(reserved_events) == 1


def test_reveal_on_a_reused_record_raises_reuse_violation():
    env = _Env()
    study = _study()
    cand_ref, _ = env.put_candidate("x")
    split_ref, _ = env.put_split()
    led = env.ledger()
    reserved = led.reserve_validation(
        study, cand_ref, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="idem-1")
    led.reveal_validation(reserved.trial_id, "art-1", "a" * 64)
    reused = led.reserve_validation(
        study, cand_ref, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="idem-2")
    # you may reuse cached results but never reveal (run) them again.
    with pytest.raises(TrialReuseViolation):
        led.reveal_validation(reused.trial_id, "art-2", "9" * 64)


# --------------------------------------------------------------------------- #
# 2. idempotency (invariant 2)                                                  #
# --------------------------------------------------------------------------- #
def test_same_key_same_content_replays_and_different_content_conflicts():
    env = _Env()
    study = _study()
    cand_ref, _ = env.put_candidate("x")
    other_ref, _ = env.put_candidate("y")
    split_ref, _ = env.put_split()
    led = env.ledger()

    r1 = led.reserve_validation(
        study, cand_ref, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="k")
    r2 = led.reserve_validation(
        study, cand_ref, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="k")
    assert r1 == r2
    assert r1.trial_id == r2.trial_id

    with pytest.raises(IdempotencyConflict):
        led.reserve_validation(
            study, other_ref, split_ref,
            data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="k")


# --------------------------------------------------------------------------- #
# 3. holdout one-shot lease (invariant 3)                                       #
# --------------------------------------------------------------------------- #
def _register_window(env, led, study):
    fam = derive_study_family(study)
    win_ref, win = env.put_window(
        fam, window_id="w1",
        start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC),
        matured=datetime(2026, 3, 1, tzinfo=UTC))
    led.register_holdout_window(study, win, idempotency_key="win-1")
    return win_ref, win


def test_holdout_second_candidate_raises_before_any_evaluation():
    env = _Env()
    study = _study()
    led = env.ledger()
    win_ref, _ = _register_window(env, led, study)
    cand_a, _ = env.put_candidate("a")
    cand_b, _ = env.put_candidate("b")

    reserved = led.reserve_holdout(
        study, cand_a, win_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="h1")
    assert reserved.stage == "sealed_holdout"
    assert reserved.status == "reserved"
    assert reserved.lease_state == "reserved"
    assert reserved.metrics_revealed == ()

    with pytest.raises(HoldoutLeaseExhaustedError):
        led.reserve_holdout(
            study, cand_b, win_ref,
            data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="h2")

    # the same candidate replays the original reservation (no new lease).
    again = led.reserve_holdout(
        study, cand_a, win_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="h3")
    assert again.trial_id == reserved.trial_id


# --------------------------------------------------------------------------- #
# 4./7. terminal holdout: main receipt only, audit TrialRecord, idempotent     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["revealed", "failed", "timed_out", "inconclusive"])
def test_exhaust_holdout_terminal_partitions_and_idempotency(status):
    env = _Env()
    study = _study()
    led = env.ledger()
    win_ref, _ = _register_window(env, led, study)
    cand_a, _ = env.put_candidate("a")
    reserved = led.reserve_holdout(
        study, cand_a, win_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="h1")

    rd = "a" * 64 if status == "revealed" else None
    receipt = led.exhaust_holdout(reserved.trial_id, status=status, result_digest=rd)
    assert receipt.status == status
    assert receipt.holdout_window_id == "w1"
    assert receipt.trial_id == reserved.trial_id
    # HoldoutReceipt is structurally incapable of dereferencing sealed content.
    assert "curve_ref" not in type(receipt).model_fields
    if status == "revealed":
        assert receipt.result_digest == rd
    else:
        assert receipt.result_digest is None

    fam = derive_study_family(study)
    main = env.stores.events.journal(fam.family_id, "main")
    # every terminal event on the main journal carries HoldoutReceipt, never a
    # TrialRecord holdout terminal (invariant 7).
    terminals = [e for e in main
                 if e.event_type in (EventType.TRIAL_REVEALED, EventType.TRIAL_EXHAUSTED)]
    assert terminals, "a terminal event must exist on main"
    for e in terminals:
        assert e.payload_schema_ref.name == "HoldoutReceipt"
    expected_main = (EventType.TRIAL_REVEALED if status == "revealed"
                     else EventType.TRIAL_EXHAUSTED)
    assert terminals[-1].event_type is expected_main

    # the full terminal TrialRecord lives only on the audit partition.
    audit = env.stores.events.journal(fam.family_id, "audit")
    assert [e.payload_schema_ref.name for e in audit] == ["TrialRecord"]
    audit_rec = env.stores.payloads.get(
        audit[0].payload_ref, expected_schema_ref=audit[0].payload_schema_ref)
    assert audit_rec.stage == "sealed_holdout"
    assert audit_rec.status == status
    assert audit_rec.metrics_revealed == ()
    assert audit_rec.lease_state == ("consumed" if status == "revealed" else "exhausted")

    # idempotent terminal: a repeat returns the byte-identical receipt, no reopen.
    receipt2 = led.exhaust_holdout(reserved.trial_id, status=status, result_digest=rd)
    assert receipt2 == receipt
    # even a different requested status returns the original (no reopen edge).
    receipt3 = led.exhaust_holdout(reserved.trial_id, status="failed")
    assert receipt3 == receipt


def test_lease_never_reopens_after_terminal():
    env = _Env()
    study = _study()
    led = env.ledger()
    win_ref, _ = _register_window(env, led, study)
    cand_a, _ = env.put_candidate("a")
    cand_b, _ = env.put_candidate("b")
    reserved = led.reserve_holdout(
        study, cand_a, win_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="h1")
    led.exhaust_holdout(reserved.trial_id, status="failed")
    # window is spent — a new candidate cannot re-lease it.
    with pytest.raises(HoldoutLeaseExhaustedError):
        led.reserve_holdout(
            study, cand_b, win_ref,
            data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="h9")


# --------------------------------------------------------------------------- #
# 5. window registration matrix (invariant 5)                                  #
# --------------------------------------------------------------------------- #
def test_window_registration_matrix():
    env = _Env()
    study = _study()
    led = env.ledger()
    fam = derive_study_family(study)

    # a valid first window registers and increments the count.
    _, w1 = env.put_window(
        fam, window_id="w1",
        start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC),
        matured=datetime(2026, 3, 1, tzinfo=UTC))
    led.register_holdout_window(study, w1, idempotency_key="r1")
    assert led.effective_trial_stats(study)["holdout_windows_registered"] == 1

    # overlap: a second window whose start precedes the first window's end.
    _, w_overlap = env.put_window(
        fam, window_id="w2",
        start=datetime(2026, 1, 15, tzinfo=UTC), end=datetime(2026, 3, 1, tzinfo=UTC),
        matured=datetime(2026, 4, 1, tzinfo=UTC), prior=("w1",))
    with pytest.raises(HoldoutWindowOverlapError):
        led.register_holdout_window(study, w_overlap, idempotency_key="r2")

    # missing prior acknowledgment: a later window that fails to list w1.
    _, w_noprior = env.put_window(
        fam, window_id="w3",
        start=datetime(2026, 2, 1, tzinfo=UTC), end=datetime(2026, 3, 1, tzinfo=UTC),
        matured=datetime(2026, 4, 1, tzinfo=UTC), prior=())
    with pytest.raises(HoldoutWindowOverlapError):
        led.register_holdout_window(study, w_noprior, idempotency_key="r3")

    # unmatured: matured_at in the future.
    future = env.clock.current + timedelta(days=3650)
    _, w_future = env.put_window(
        fam, window_id="w4",
        start=datetime(2026, 2, 1, tzinfo=UTC), end=datetime(2026, 3, 1, tzinfo=UTC),
        matured=future, prior=("w1",))
    with pytest.raises(HoldoutWindowOverlapError):
        led.register_holdout_window(study, w_future, idempotency_key="r4")

    # attestation mismatch: a hand-forged non_overlap_attestation.
    _, w_forged = env.put_window(
        fam, window_id="w5",
        start=datetime(2026, 2, 1, tzinfo=UTC), end=datetime(2026, 3, 1, tzinfo=UTC),
        matured=datetime(2026, 4, 1, tzinfo=UTC), prior=("w1",),
        attestation="0" * 64)
    with pytest.raises(HoldoutWindowOverlapError):
        led.register_holdout_window(study, w_forged, idempotency_key="r5")

    # a valid boundary-touching later window (start_at == prior end_at) registers.
    _, w_boundary = env.put_window(
        fam, window_id="w6",
        start=datetime(2026, 2, 1, tzinfo=UTC), end=datetime(2026, 3, 1, tzinfo=UTC),
        matured=datetime(2026, 4, 1, tzinfo=UTC), prior=("w1",))
    led.register_holdout_window(study, w_boundary, idempotency_key="r6")
    assert led.effective_trial_stats(study)["holdout_windows_registered"] == 2


def test_window_family_identity_mismatch_rejected():
    env = _Env()
    study = _study()
    led = env.ledger()
    other_fam = derive_study_family(_study(objective="other", odigest="9" * 64))
    _, w = env.put_window(
        other_fam, window_id="w1",
        start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC),
        matured=datetime(2026, 3, 1, tzinfo=UTC))
    with pytest.raises(Exception):  # TrialLedgerError family-identity mismatch
        led.register_holdout_window(study, w, idempotency_key="r1")


# --------------------------------------------------------------------------- #
# 6. caller-minted family impossible + tampered head rejected (invariant 6)    #
# --------------------------------------------------------------------------- #
def test_resolve_family_only_accepts_studyspec_signature():
    import inspect

    sig = inspect.signature(TrialLedger.resolve_family)
    ann = sig.parameters["study"].annotation
    assert ann in (StudySpec, "StudySpec")
    # no ledger method exposes a StudyFamily parameter (identity is derived only).
    for name, member in vars(TrialLedger).items():
        if not callable(member) or name.startswith("_"):
            continue
        params = inspect.signature(member).parameters
        assert not any(
            p.annotation in (StudyFamily, "StudyFamily") for p in params.values()), name


def test_tampered_family_head_fails_attestation_on_read():
    env = _Env()
    study = _study()
    led = env.ledger()
    fam = derive_study_family(study)

    # forge a StudyFamily with a valid id-binding but a wrong governor attestation,
    # then plant it directly into the family head cell.
    forged = fam.model_copy(update={"governor_attestation": "f" * 64})
    from guanlan_v2.orchestration.eventstore import (
        PayloadPutCommand,
        RuntimeBatch,
        StagedPayloadKey,
        StagedTypedPayloadRef,
        StateCellCompareAndSwapCommand,
    )
    from guanlan_v2.orchestration.trial_ledger import _family_head_key

    staged = StagedPayloadKey(key="fam")
    env.stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="plant-forged-head",
        payload_puts=(PayloadPutCommand(
            staged_key=staged, schema_ref=_SR("StudyFamily"), namespace="main",
            payload_template={n: getattr(forged, n) for n in type(forged).model_fields},
            registry_digest=env.digest, idempotency_key="forged-put"),),
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace="trial.family_head.v1",
            cell_key_digest=_family_head_key(fam.family_id),
            expected_value=None,
            new_target=StagedTypedPayloadRef(
                staged_key=staged, schema_ref=_SR("StudyFamily"), namespace="main")),),
    ))

    with pytest.raises(FamilyAttestationError):
        led.resolve_family(study)


# --------------------------------------------------------------------------- #
# 7. effective_trial_stats closed key set + value correctness                  #
# --------------------------------------------------------------------------- #
def test_effective_trial_stats_closed_keyset_and_values():
    env = _Env()
    study = _study()
    led = env.ledger()
    fam = derive_study_family(study)

    # two distinct triples revealed.
    cx, _ = env.put_candidate("x")
    cy, _ = env.put_candidate("y")
    split_ref, _ = env.put_split()
    r1 = led.reserve_validation(
        study, cx, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="k1")
    led.reveal_validation(r1.trial_id, "art-x", "1" * 64)
    r2 = led.reserve_validation(
        study, cy, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="k2")
    led.reveal_validation(r2.trial_id, "art-y", "2" * 64)
    # one reuse of the first triple.
    led.reserve_validation(
        study, cx, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="k3")
    # one holdout registered + exhausted (revealed).
    win_typed, w = env.put_window(
        fam, window_id="w1",
        start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC),
        matured=datetime(2026, 3, 1, tzinfo=UTC))
    led.register_holdout_window(study, w, idempotency_key="rw")
    ch, _ = env.put_candidate("h")
    hres = led.reserve_holdout(
        study, ch, win_typed,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="h1")
    led.exhaust_holdout(hres.trial_id, status="revealed", result_digest="9" * 64)

    stats = led.effective_trial_stats(study)
    assert set(stats) == {
        "family_id", "raw_trial_count", "distinct_candidate_count", "reveal_count",
        "holdout_windows_registered", "holdout_windows_exhausted", "last_revealed_at"}
    assert stats["family_id"] == fam.family_id
    assert stats["raw_trial_count"] == 2  # distinct revealed validation triples
    assert stats["distinct_candidate_count"] == 2
    assert stats["reveal_count"] == 1  # one holdout reveal
    assert stats["holdout_windows_registered"] == 1
    assert stats["holdout_windows_exhausted"] == 1
    assert stats["last_revealed_at"] is not None


# --------------------------------------------------------------------------- #
# 8. replay reproduces stats + lease states (invariant 8)                      #
# --------------------------------------------------------------------------- #
def test_replay_reproduces_stats_and_lease_states():
    env = _Env()
    study = _study()
    led = env.ledger()
    fam = derive_study_family(study)
    cx, _ = env.put_candidate("x")
    split_ref, _ = env.put_split()
    r1 = led.reserve_validation(
        study, cx, split_ref,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="k1")
    led.reveal_validation(r1.trial_id, "art-x", "1" * 64)
    _, w = env.put_window(
        fam, window_id="w1",
        start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC),
        matured=datetime(2026, 3, 1, tzinfo=UTC))
    led.register_holdout_window(study, w, idempotency_key="rw")
    ch, _ = env.put_candidate("h")
    win_typed, _ = env.put_window(
        fam, window_id="w1",
        start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC),
        matured=datetime(2026, 3, 1, tzinfo=UTC))
    hres = led.reserve_holdout(
        study, ch, win_typed,
        data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="h1")
    led.exhaust_holdout(hres.trial_id, status="revealed", result_digest="9" * 64)

    before = led.effective_trial_stats(study)
    replayed = led.replay(fam.family_id)
    after = replayed.effective_trial_stats(study)
    assert after == before
    # lease state survives the fold reconstruction exactly.
    assert replayed.get_trial(hres.trial_id).lease_state == "consumed"
    assert replayed.get_trial(r1.trial_id).status == "revealed"


# --------------------------------------------------------------------------- #
# 8b. all-or-none: an injected UoW failure persists nothing                    #
# --------------------------------------------------------------------------- #
def test_injected_uow_failure_persists_nothing_in_reserve_holdout():
    env = _Env()
    study = _study()
    fam = derive_study_family(study)
    # register the window through a healthy ledger first.
    healthy = env.ledger()
    win_ref, _ = _register_window(env, healthy, study)
    cand_a, _ = env.put_candidate("a")

    class _BoomUoW:
        def commit(self, batch):  # noqa: ANN001
            raise RuntimeError("injected commit failure")

    boom = TrialLedger(
        event_store=env.stores.events, payload_store=env.stores.payloads,
        state_cells=env.stores.cells, registry=env.registry, clock=env.clock,
        uow_factory=lambda: _BoomUoW(), id_factory=env._id_factory)

    from guanlan_v2.orchestration.trial_ledger import _holdout_lease_key

    with pytest.raises(RuntimeError):
        boom.reserve_holdout(
            study, cand_a, win_ref,
            data_snapshot_hash=DH, code_prompt_model_hash=CH, idempotency_key="hboom")

    # no lease cell, and no holdout reservation event was persisted.
    assert env.stores.cells.load(
        "trial.holdout_lease.v1", _holdout_lease_key(fam.family_id, "w1")) is None
    reserved_events = [
        e for e in env.stores.events.journal(fam.family_id, "main")
        if e.event_type is EventType.TRIAL_RESERVED]
    assert reserved_events == []
