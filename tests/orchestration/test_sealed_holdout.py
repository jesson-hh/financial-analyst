# -*- coding: utf-8 -*-
"""Task 6 — sealed result store + one-shot sealed evaluator gateway (``sealed.py``).

Written test-first: with ``sealed.py`` absent these tests are RED on the missing
module (not a collection error elsewhere). They exercise, over the real Phase 2
in-memory stores and the Task 5 :class:`TrialLedger`, the six brief invariants and
the frozen access-control matrix — the security heart of Phase 4: **sealed holdout
metrics must be UNREACHABLE through every public path**.

Coverage map (brief §Required invariants + access-control matrix):

1. Optimizer-side code holds no read path — module-inspection of ``optimize.py`` /
   ``evaluator.py`` (re-runs after Tasks 7-8; skips until they exist);
2. a forged / expired / mismatched lease fails ``evaluate_once`` **before** the
   evaluation callable runs (spy asserts zero invocations);
3. failure / timeout / inconclusive each exhaust the one-shot window;
4. crash recovery returns the byte-identical original receipt without re-running;
5. sealed payloads are invisible publicly (main journal carries no sealed ref;
   public evidence validators + ``assert_no_sealed_refs`` refuse a sealed ref;
   capability-gated ``get`` succeeds and expired fails);
6. the receipt's ``result_digest`` equals the sealed record's ``result_digest``.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.governor import derive_study_family
from guanlan_v2.orchestration.refs import (
    PUBLIC_PAYLOAD_NAMESPACES,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
    validate_typed_ref_tuple,
)
from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry
from guanlan_v2.orchestration.trial import (
    HoldoutWindow,
    OptimizeCandidate,
    SealedCapability,
    SealedEvaluationRecord,
    SplitSpec,
    StudyFamily,
    StudySpec,
    TrialRecord,
    ValidationMetrics,
)
from guanlan_v2.orchestration.trial import HoldoutReceipt
from guanlan_v2.orchestration.trial_ledger import (
    PHASE4_STATE_CELL_NAMESPACES,
    HoldoutLeaseExhaustedError,
    TrialLedger,
    compute_holdout_window_attestation,
)
from guanlan_v2.orchestration.sealed import (
    CAPABILITY_SIGNATURE_DOMAIN,
    LEASE_SIGNATURE_DOMAIN,
    CapabilityVerificationError,
    LeaseVerificationError,
    SealedAccessError,
    SealedEvaluatorGateway,
    SealedResultStore,
    assert_no_sealed_refs,
    issue_holdout_lease,
    issue_sealed_capability,
    verify_holdout_lease,
    verify_sealed_capability,
)

UTC = timezone.utc
DH = "d" * 64
CH = "c" * 64


def _SR(name: str) -> SchemaRef:
    return SchemaRef(name=name, version="1")


class ManualClock:
    """Deterministic clock: ``now()`` never advances on its own; test-controlled."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 7, 17, 1, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def _study(*, objective: str = "find alpha", freq: str = "monthly",
           odigest: str = "a" * 64) -> StudySpec:
    return StudySpec(
        objective=objective, objective_digest=odigest,
        label_definition="20d fwd return", label_digest="b" * 64,
        universe_digest="e" * 64, frequency=freq, split_policy_digest="f" * 64)


def _sealed_registry() -> Phase2RuntimeRegistry:
    """A minimal sealed registry carrying the ledger payloads + the sealed record."""
    reg = Phase2RuntimeRegistry()
    for model in (
        StudySpec, StudyFamily, SplitSpec, OptimizeCandidate, ValidationMetrics,
        HoldoutWindow, TrialRecord, HoldoutReceipt, SealedEvaluationRecord,
    ):
        reg.register(model)
    reg.seal()
    return reg


class _Env:
    def __init__(self) -> None:
        self.resolver = SchemaRegistryResolver()
        self.registry = _sealed_registry()
        self.digest = self.resolver.register(self.registry)
        self.clock = ManualClock()
        self.stores = RuntimeStores(
            resolver=self.resolver, clock=self.clock,
            allowed_cell_namespaces=PHASE4_STATE_CELL_NAMESPACES)
        self._seq = 0

    def _id_factory(self) -> str:
        v = f"tok{self._seq}"
        self._seq += 1
        return v

    def ledger(self) -> TrialLedger:
        return TrialLedger(
            event_store=self.stores.events, payload_store=self.stores.payloads,
            state_cells=self.stores.cells, registry=self.registry, clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work, id_factory=self._id_factory)

    def store(self, led: TrialLedger) -> SealedResultStore:
        return SealedResultStore(
            payload_store=self.stores.payloads, ledger=led,
            clock=self.clock, registry=self.registry)

    def put_candidate(self, expr: str = "x") -> tuple[TypedPayloadRef, OptimizeCandidate]:
        cand = OptimizeCandidate.build(
            candidate_kind="workflow_graph",
            graph={"nodes": [{"id": "a", "type": "formula", "params": {"expr": expr}}],
                   "edges": []},
            params={})
        ref = self.stores.payloads.put(
            _SR("OptimizeCandidate"), cand, registry_digest=self.digest,
            namespace="main", idempotency_key=f"cand:{expr}")
        return TypedPayloadRef(schema_ref=_SR("OptimizeCandidate"), payload_ref=ref), cand

    def put_window(
        self, family: StudyFamily, *, window_id: str = "w1",
        start: datetime = datetime(2026, 1, 1, tzinfo=UTC),
        end: datetime = datetime(2026, 2, 1, tzinfo=UTC),
        matured: datetime = datetime(2026, 3, 1, tzinfo=UTC),
        prior: tuple[str, ...] = (),
    ) -> tuple[TypedPayloadRef, HoldoutWindow]:
        att = compute_holdout_window_attestation(
            family_identity_digest=family.identity_digest,
            start_at=start, end_at=end, prior_window_ids=prior)
        win = HoldoutWindow(
            holdout_window_id=window_id, family_identity_digest=family.identity_digest,
            start_at=start, end_at=end, matured_at=matured,
            data_snapshot_id="snap-" + window_id, vintage_manifest_digest="1" * 64,
            prior_window_ids=prior, non_overlap_attestation=att)
        ref = self.stores.payloads.put(
            _SR("HoldoutWindow"), win, registry_digest=self.digest,
            namespace="main", idempotency_key=f"win:{window_id}")
        return TypedPayloadRef(schema_ref=_SR("HoldoutWindow"), payload_ref=ref), win


def _good_metrics() -> ValidationMetrics:
    return ValidationMetrics(source="run_graph", rank_ic=0.05, sharpe=1.3,
                            oos_verdict="robust")


def _empty_metrics() -> ValidationMetrics:
    return ValidationMetrics(source="run_graph")


class SpyEval:
    """A scripted evaluator recording invocation count for the zero-call assertions."""

    def __init__(self, *, metrics: ValidationMetrics | None = None,
                 raises: BaseException | None = None, sleep: float | None = None) -> None:
        self.calls = 0
        self._metrics = metrics
        self._raises = raises
        self._sleep = sleep

    def __call__(self, cand_ref, window):  # noqa: ANN001
        self.calls += 1
        if self._sleep is not None:
            time.sleep(self._sleep)
        if self._raises is not None:
            raise self._raises
        return self._metrics


def _gateway(env: _Env, led: TrialLedger, evaluator, *, study: StudySpec,
             store: SealedResultStore, lease_ttl: int = 3600,
             timeout: int = 1) -> SealedEvaluatorGateway:
    return SealedEvaluatorGateway(
        study=study, ledger=led, sealed_store=store, evaluate_holdout=evaluator,
        clock=env.clock, lease_ttl_seconds=lease_ttl, timeout_seconds=timeout,
        data_snapshot_hash=DH, code_prompt_model_hash=CH)


# --------------------------------------------------------------------------- #
# constants                                                                    #
# --------------------------------------------------------------------------- #
def test_signature_domains_are_frozen():
    assert LEASE_SIGNATURE_DOMAIN == "holdout-lease-v1"
    assert CAPABILITY_SIGNATURE_DOMAIN == "sealed-capability-v1"


# --------------------------------------------------------------------------- #
# holdout lease issuance / verification                                        #
# --------------------------------------------------------------------------- #
def test_issue_and_verify_holdout_lease_roundtrip():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved = led.reserve_holdout(
        study, cand, win_ref, data_snapshot_hash=DH, code_prompt_model_hash=CH,
        idempotency_key="h1")

    lease = issue_holdout_lease(trial=reserved, clock=env.clock, ttl_seconds=3600,
                                nonce="n1")
    assert lease.lease_id == reserved.holdout_lease_id
    assert lease.trial_id == reserved.trial_id
    assert lease.candidate_hash == reserved.candidate_hash
    assert lease.holdout_window_id == reserved.holdout_window_id
    assert lease.expires_at > lease.issued_at

    # verify passes against its reserved record and clock.
    verify_holdout_lease(lease, trial=reserved, clock=env.clock)

    # v1 signature is a domain-tagged recomputable digest over the semantic fields.
    expected = content_digest({
        "domain": LEASE_SIGNATURE_DOMAIN, "lease_id": lease.lease_id,
        "trial_id": lease.trial_id, "candidate_hash": lease.candidate_hash,
        "holdout_window_id": lease.holdout_window_id, "issued_at": lease.issued_at,
        "expires_at": lease.expires_at, "nonce": lease.nonce})
    assert lease.signature == expected


def test_issue_holdout_lease_requires_reserved_holdout_record():
    env = _Env()
    validation = TrialRecord(
        trial_id="fam.0123456789abcdef::t", family_id="fam.0123456789abcdef",
        candidate_hash="a" * 64, data_snapshot_hash=DH, split_spec_hash="c" * 64,
        code_prompt_model_hash=CH, stage="validation", status="reserved",
        lease_state="none", idempotency_key="k", created_at=env.clock.now())
    with pytest.raises(SealedAccessError):
        issue_holdout_lease(trial=validation, clock=env.clock, ttl_seconds=3600,
                            nonce="n1")


def test_verify_holdout_lease_rejects_tamper_binding_and_expiry():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved = led.reserve_holdout(
        study, cand, win_ref, data_snapshot_hash=DH, code_prompt_model_hash=CH,
        idempotency_key="h1")
    lease = issue_holdout_lease(trial=reserved, clock=env.clock, ttl_seconds=3600,
                                nonce="n1")

    # tampered candidate_hash → signature no longer recomputes.
    with pytest.raises(LeaseVerificationError):
        verify_holdout_lease(lease.model_copy(update={"candidate_hash": "0" * 64}),
                             trial=reserved, clock=env.clock)
    # tampered signature.
    with pytest.raises(LeaseVerificationError):
        verify_holdout_lease(lease.model_copy(update={"signature": "0" * 64}),
                             trial=reserved, clock=env.clock)
    # bound to a different trial (recompute matches, binding does not).
    other = reserved.model_copy(update={"candidate_hash": "9" * 64})
    with pytest.raises(LeaseVerificationError):
        verify_holdout_lease(lease, trial=other, clock=env.clock)
    # expired: advance the authoritative clock past expires_at.
    env.clock.advance(10_000)
    with pytest.raises(LeaseVerificationError):
        verify_holdout_lease(lease, trial=reserved, clock=env.clock)


# --------------------------------------------------------------------------- #
# sealed capability issuance / verification                                    #
# --------------------------------------------------------------------------- #
def test_issue_and_verify_sealed_capability_roundtrip_expiry_and_forgery():
    env = _Env()
    cap = issue_sealed_capability(scope="final_report", principal_id="alice",
                                  clock=env.clock, ttl_seconds=3600)
    assert cap.scope == "final_report"
    assert cap.principal_id == "alice"
    verify_sealed_capability(cap, clock=env.clock)

    expected = content_digest({
        "domain": CAPABILITY_SIGNATURE_DOMAIN, "token_id": cap.token_id,
        "scope": cap.scope, "principal_id": cap.principal_id,
        "expires_at": cap.expires_at})
    assert cap.signature == expected

    # forged signature.
    with pytest.raises(CapabilityVerificationError):
        verify_sealed_capability(cap.model_copy(update={"signature": "0" * 64}),
                                 clock=env.clock)
    # expired.
    env.clock.advance(10_000)
    with pytest.raises(CapabilityVerificationError):
        verify_sealed_capability(cap, clock=env.clock)


# --------------------------------------------------------------------------- #
# assert_no_sealed_refs — defense-in-depth public-pool guard                    #
# --------------------------------------------------------------------------- #
def test_assert_no_sealed_refs_matrix():
    main_ref = PayloadRef(namespace="main", object_id="o1", content_digest="a" * 64)
    assert_no_sealed_refs([main_ref])  # public → allowed
    assert_no_sealed_refs(
        [TypedPayloadRef(schema_ref=_SR("HoldoutReceipt"), payload_ref=main_ref)])

    for ns in ("sealed", "review", "audit"):
        bad = PayloadRef(namespace=ns, object_id="ox", content_digest="b" * 64)
        with pytest.raises(SealedAccessError):
            assert_no_sealed_refs([bad])
        with pytest.raises(SealedAccessError):
            assert_no_sealed_refs(
                [main_ref,
                 TypedPayloadRef(schema_ref=_SR("SealedEvaluationRecord"), payload_ref=bad)])


# --------------------------------------------------------------------------- #
# SealedResultStore.put/get — capability gate + idempotency                     #
# --------------------------------------------------------------------------- #
def test_put_persists_sealed_and_reveals_then_get_requires_capability():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved = led.reserve_holdout(
        study, cand, win_ref, data_snapshot_hash=DH, code_prompt_model_hash=CH,
        idempotency_key="h1")
    lease = issue_holdout_lease(trial=reserved, clock=env.clock, ttl_seconds=3600,
                                nonce="n1")
    record = SealedEvaluationRecord(
        trial_id=reserved.trial_id, result_artifact_id="art",
        result_digest="9" * 64, metrics_payload={"rank_ic": 0.05},
        created_at=env.clock.now())

    receipt = store.put(lease.lease_id, record)
    assert receipt.status == "revealed"
    assert receipt.result_digest == "9" * 64  # invariant 6: opaque digest matches

    # get requires a valid capability; both scopes read the full record.
    for scope in ("final_report", "human_review"):
        cap = issue_sealed_capability(scope=scope, principal_id="p", clock=env.clock,
                                      ttl_seconds=3600)
        got = store.get(reserved.trial_id, capability=cap)
        assert got.trial_id == reserved.trial_id
        assert got.result_digest == "9" * 64

    # forged capability is refused.
    good = issue_sealed_capability(scope="final_report", principal_id="p",
                                   clock=env.clock, ttl_seconds=3600)
    with pytest.raises(CapabilityVerificationError):
        store.get(reserved.trial_id, capability=good.model_copy(update={"signature": "0" * 64}))
    # expired capability is refused.
    env.clock.advance(10_000)
    with pytest.raises(CapabilityVerificationError):
        store.get(reserved.trial_id, capability=good)


def test_put_rejects_lease_id_not_the_live_reservation():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved = led.reserve_holdout(
        study, cand, win_ref, data_snapshot_hash=DH, code_prompt_model_hash=CH,
        idempotency_key="h1")
    record = SealedEvaluationRecord(
        trial_id=reserved.trial_id, result_artifact_id="art",
        result_digest="9" * 64, metrics_payload={"rank_ic": 0.05},
        created_at=env.clock.now())
    with pytest.raises(LeaseVerificationError):
        store.put("lease.bogus", record)


def test_second_put_returns_original_receipt_without_writing():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved = led.reserve_holdout(
        study, cand, win_ref, data_snapshot_hash=DH, code_prompt_model_hash=CH,
        idempotency_key="h1")
    lease = issue_holdout_lease(trial=reserved, clock=env.clock, ttl_seconds=3600,
                                nonce="n1")
    rec = SealedEvaluationRecord(
        trial_id=reserved.trial_id, result_artifact_id="art",
        result_digest="9" * 64, metrics_payload={"rank_ic": 0.05},
        created_at=env.clock.now())
    r1 = store.put(lease.lease_id, rec)

    # a second put with *different* content returns the original receipt, no write.
    rec2 = SealedEvaluationRecord(
        trial_id=reserved.trial_id, result_artifact_id="art2",
        result_digest="8" * 64, metrics_payload={"rank_ic": 0.9},
        created_at=env.clock.now())
    r2 = store.put(lease.lease_id, rec2)
    assert r2 == r1
    assert content_digest(r2) == content_digest(r1)

    cap = issue_sealed_capability(scope="final_report", principal_id="p",
                                  clock=env.clock, ttl_seconds=3600)
    got = store.get(reserved.trial_id, capability=cap)
    assert got.result_digest == "9" * 64  # the first sealed record stands


def test_get_unknown_trial_refused_even_with_valid_capability():
    env = _Env()
    led = env.ledger()
    store = env.store(led)
    cap = issue_sealed_capability(scope="human_review", principal_id="p",
                                  clock=env.clock, ttl_seconds=3600)
    with pytest.raises(SealedAccessError):
        store.get("fam.0123456789abcdef::nope", capability=cap)


# --------------------------------------------------------------------------- #
# Gateway happy path: reserve → lease → evaluate → seal + reveal                #
# --------------------------------------------------------------------------- #
def test_gateway_success_seals_metrics_and_reveals_opaque_receipt():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    spy = SpyEval(metrics=_good_metrics())
    gw = _gateway(env, led, spy, study=study, store=store)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)

    reserved, lease = gw.reserve_and_lease(cand, window_ref=win_ref, idempotency_key="h1")
    assert reserved.stage == "sealed_holdout"
    assert reserved.status == "reserved"
    assert reserved.metrics_revealed == ()
    assert lease.trial_id == reserved.trial_id
    # reservation strictly precedes any window-data read: no evaluator call yet.
    assert spy.calls == 0

    receipt = gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id,
                               lease_token=lease)
    assert spy.calls == 1
    assert receipt.status == "revealed"
    assert receipt.result_digest is not None
    assert receipt.trial_id == reserved.trial_id
    # the public receipt cannot dereference sealed content.
    assert "curve_ref" not in type(receipt).model_fields

    # invariant 6: the sealed record's digest equals the public receipt digest.
    cap = issue_sealed_capability(scope="human_review", principal_id="p",
                                  clock=env.clock, ttl_seconds=3600)
    record = store.get(reserved.trial_id, capability=cap)
    assert record.result_digest == receipt.result_digest
    assert record.metrics_payload["rank_ic"] == 0.05


# --------------------------------------------------------------------------- #
# Invariant 5: sealed payloads are invisible on the public journal              #
# --------------------------------------------------------------------------- #
def test_main_journal_never_references_a_sealed_payload():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    gw = _gateway(env, led, SpyEval(metrics=_good_metrics()), study=study, store=store)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved, lease = gw.reserve_and_lease(cand, window_ref=win_ref, idempotency_key="h1")
    gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id, lease_token=lease)

    main = env.stores.events.journal(fam.family_id, "main")
    assert main, "a main journal must exist for the family"
    for e in main:
        assert e.payload_ref.namespace in PUBLIC_PAYLOAD_NAMESPACES
        assert e.payload_schema_ref.name in {"StudyFamily", "TrialRecord", "HoldoutReceipt"}
    terminals = [e for e in main
                 if e.event_type in (EventType.TRIAL_REVEALED, EventType.TRIAL_EXHAUSTED)]
    assert terminals
    assert all(e.payload_schema_ref.name == "HoldoutReceipt" for e in terminals)


def test_public_evidence_validators_refuse_a_sealed_ref():
    # a sealed typed ref cannot cross into a require_main public-evidence tuple
    # (Phase 1 namespace rule) nor the defense-in-depth adapter guard.
    sealed = TypedPayloadRef(
        schema_ref=_SR("SealedEvaluationRecord"),
        payload_ref=PayloadRef(namespace="sealed", object_id="s1",
                               content_digest="a" * 64))
    with pytest.raises(ValueError):
        validate_typed_ref_tuple([sealed], require_main=True, field_name="evidence")
    with pytest.raises(SealedAccessError):
        assert_no_sealed_refs([sealed])


# --------------------------------------------------------------------------- #
# Invariant 2: a bad lease refuses evaluate_once before the callable runs       #
# --------------------------------------------------------------------------- #
def test_evaluate_once_refuses_bad_lease_before_the_callable():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    spy = SpyEval(metrics=_good_metrics())
    gw = _gateway(env, led, spy, study=study, store=store)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved, lease = gw.reserve_and_lease(cand, window_ref=win_ref, idempotency_key="h1")

    # tampered candidate_hash.
    with pytest.raises(LeaseVerificationError):
        gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id,
                         lease_token=lease.model_copy(update={"candidate_hash": "0" * 64}))
    # tampered signature.
    with pytest.raises(LeaseVerificationError):
        gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id,
                         lease_token=lease.model_copy(update={"signature": "0" * 64}))
    # a genuinely-signed lease bound to a different reservation.
    cand2, _ = env.put_candidate("b")
    win2_ref, _ = env.put_window(
        fam, window_id="w2", start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 4, 1, tzinfo=UTC), matured=datetime(2026, 5, 1, tzinfo=UTC))
    reserved2, lease2 = gw.reserve_and_lease(cand2, window_ref=win2_ref, idempotency_key="h2")
    with pytest.raises(LeaseVerificationError):
        gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id, lease_token=lease2)
    # expired lease.
    env.clock.advance(10_000)
    with pytest.raises(LeaseVerificationError):
        gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id, lease_token=lease)

    assert spy.calls == 0  # the evaluation callable never ran on any refusal


# --------------------------------------------------------------------------- #
# Invariant 3: failure / timeout / inconclusive each exhaust the window         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode,status", [
    ("raise", "failed"),
    ("timeout", "timed_out"),
    ("inconclusive", "inconclusive"),
])
def test_terminal_failure_paths_exhaust_the_one_shot_window(mode, status):
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    if mode == "raise":
        spy = SpyEval(raises=RuntimeError("boom"))
    elif mode == "timeout":
        spy = SpyEval(metrics=_good_metrics(), sleep=3.0)
    else:
        spy = SpyEval(metrics=_empty_metrics())
    gw = _gateway(env, led, spy, study=study, store=store, timeout=1)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved, lease = gw.reserve_and_lease(cand, window_ref=win_ref, idempotency_key="h1")

    receipt = gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id,
                               lease_token=lease)
    assert receipt.status == status
    assert receipt.result_digest is None  # a failed terminal carries no digest
    assert receipt.revealed_at is None

    # the window is spent: a different candidate cannot re-lease it.
    cand2, _ = env.put_candidate("b")
    with pytest.raises(HoldoutLeaseExhaustedError):
        gw.reserve_and_lease(cand2, window_ref=win_ref, idempotency_key="h2")


# --------------------------------------------------------------------------- #
# Invariant 4: crash recovery returns the byte-identical receipt, no re-run      #
# --------------------------------------------------------------------------- #
def test_crash_recovery_returns_identical_receipt_without_recalling():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    spy = SpyEval(metrics=_good_metrics())
    gw = _gateway(env, led, spy, study=study, store=store)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved, lease = gw.reserve_and_lease(cand, window_ref=win_ref, idempotency_key="h1")

    r1 = gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id, lease_token=lease)
    assert spy.calls == 1
    r2 = gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id, lease_token=lease)
    assert spy.calls == 1  # not re-run
    assert r2 == r1
    assert content_digest(r2) == content_digest(r1)


def test_crash_recovery_after_failure_returns_identical_receipt():
    env = _Env()
    led = env.ledger()
    study = _study()
    fam = derive_study_family(study)
    store = env.store(led)
    spy = SpyEval(raises=RuntimeError("boom"))
    gw = _gateway(env, led, spy, study=study, store=store)
    cand, _ = env.put_candidate("a")
    win_ref, _ = env.put_window(fam)
    reserved, lease = gw.reserve_and_lease(cand, window_ref=win_ref, idempotency_key="h1")

    r1 = gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id, lease_token=lease)
    assert r1.status == "failed"
    assert spy.calls == 1
    r2 = gw.evaluate_once(cand, holdout_reservation_id=reserved.trial_id, lease_token=lease)
    assert spy.calls == 1  # a terminal window never reopens or re-runs
    assert r2 == r1


# --------------------------------------------------------------------------- #
# Invariant 1: the optimizer-side modules hold no sealed read path              #
# --------------------------------------------------------------------------- #
def test_optimizer_side_modules_import_no_sealed_read_path():
    import guanlan_v2.orchestration as pkg

    base = Path(pkg.__file__).resolve().parent
    forbidden = ("SealedResultStore", "SealedEvaluationRecord")
    checked = 0
    for name in ("optimize.py", "evaluator.py"):
        path = base / name
        if not path.exists():
            continue
        checked += 1
        src = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in src, f"{name} must not reference {token} (sealed isolation)"
    if checked == 0:
        pytest.skip("optimize.py/evaluator.py not yet implemented (Tasks 7-8)")
