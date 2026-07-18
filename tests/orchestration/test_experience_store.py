# -*- coding: utf-8 -*-
"""Phase 5 · Task 4 — event-folded ``ExperienceLog`` + PIT-safe case views.

Written test-first (RED until ``memory.experience`` implements the log/fold) over
the REAL Phase 2 in-memory stores (``RuntimeStores`` — ``PayloadStore`` /
``EventStore`` / ``RuntimeUnitOfWork``) with a deterministic clock. Covers the
brief's store matrix:

* append/fold happy path (pending → matured → reviewed across three folds);
* idempotent same-content replay returns the stored event; same-key + different
  content raises ``IdempotencyConflict``;
* matured-before-created refusal; reviewed-before-matured refusal;
  reviewed-without-verifier refusal (fail-closed);
* injected mid-batch failure leaves neither payload nor event (UoW all-or-none);
* PIT visibility — before case availability sees nothing; strictly between case
  and matured availability sees ``pending``; at ≥ matured availability sees
  ``matured``;
* fold purity (same events ⇒ same views tuple, digest-compared);
* a non-``main`` append is rejected before any store call (namespace masquerade);
* direct injection of an orphan ``CaseReviewed`` event is dropped by the fold with
  a deterministic warning.

Run: ``pytest tests/orchestration/test_experience_store.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    IdempotencyConflict,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    SchemaRegistryResolver,
    StagedPayloadKey,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.market.factors import (
    EvidenceAnchor,
    HeatState,
    RegimeReport,
    RiskState,
    TrendState,
)
from guanlan_v2.orchestration.memory.experience import (
    EXPERIENCE_PARTITION,
    EXPERIENCE_STREAM_ID,
    CaseMatured,
    CaseReviewed,
    ExperienceLog,
    ExperienceLogError,
    OrphanExperienceEventWarning,
    RealizedRegime,
    RegimeCase,
    fold_case_views,
)
from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry

UTC = timezone.utc
DH = "ab" * 32
CASE_AVAIL = datetime(2026, 7, 17, 7, 5, tzinfo=UTC)
AS_OF = datetime(2026, 7, 17, 7, 5, tzinfo=UTC)
MATURED_AVAIL = datetime(2026, 8, 15, 7, 5, tzinfo=UTC)


def _SR(name: str) -> SchemaRef:
    return SchemaRef(name=name, version="1")


class SteppingClock:
    """Deterministic advancing clock (+1s per read)."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 7, 17, 1, 0, tzinfo=UTC)

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


class NaiveClock:
    """A misbehaving clock returning a naive datetime — breaks the event append."""

    def now(self) -> datetime:
        return datetime(2026, 7, 17, 1, 0)  # naive on purpose


class StubVerifier:
    """A fail-closed admin verifier accepting exactly one credential."""

    def __init__(self, credential: str = "admin-token") -> None:
        self._credential = credential

    def verify(self, credential: Any):
        from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal

        if credential != self._credential:
            raise PermissionError("admin verification failed (fail closed)")
        return AuthenticatedAdminPrincipal(actor="curator", verified_by="stub")


class AlwaysFailVerifier:
    def verify(self, credential: Any):
        raise PermissionError("no admin authority (fail closed)")


def _experience_registry() -> Phase2RuntimeRegistry:
    reg = Phase2RuntimeRegistry()
    for model in (RegimeCase, RealizedRegime, CaseMatured, CaseReviewed):
        reg.register(model)
    reg.seal()
    return reg


class _Env:
    def __init__(self) -> None:
        self.resolver = SchemaRegistryResolver()
        self.registry = _experience_registry()
        self.digest = self.resolver.register(self.registry)
        self.clock = SteppingClock()
        self.stores = RuntimeStores(resolver=self.resolver, clock=self.clock)

    def log(self) -> ExperienceLog:
        return ExperienceLog(
            event_store=self.stores.events,
            payload_store=self.stores.payloads,
            registry=self.registry,
            clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work,
        )


# --------------------------------------------------------------------------- #
# payload factories                                                            #
# --------------------------------------------------------------------------- #
def _judgment(as_of: datetime = AS_OF) -> RegimeReport:
    return RegimeReport.build(
        as_of=as_of, factor_report_digest=DH,
        trend=TrendState.BULL, risk_state=RiskState.RISK_ON, heat_state=HeatState.NORMAL,
        trend_probabilities={TrendState.BULL: 0.6, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1},
        risk_probabilities={RiskState.RISK_ON: 0.5, RiskState.RISK_OFF: 0.2, RiskState.NEUTRAL: 0.2, RiskState.UNKNOWN: 0.1},
        heat_probabilities={HeatState.NORMAL: 0.7, HeatState.OVERHEAT: 0.2, HeatState.UNKNOWN: 0.1},
        confidence=Confidence.MEDIUM,
        evidence=(EvidenceAnchor(factor_id="breadth.ad_ratio", value=0.7, reading="broadening"),),
        drivers=("breadth broadening",),
        evidence_factor_ids=("breadth.ad_ratio",),
        narrative="Breadth broadening while heat stays contained.",
    )


def _case(case_id: str = "case.a", as_of: datetime = AS_OF, available_at: datetime = CASE_AVAIL,
          feature_schema_version: str = "mfs-v1") -> RegimeCase:
    return RegimeCase.build(
        id=case_id, as_of=as_of, available_at=available_at,
        feature_schema_version=feature_schema_version, scaler_digest=DH,
        features={"breadth.ad_ratio": 0.7, "vol.rv": 0.15},
        feature_coverage={"breadth.ad_ratio": 1.0, "vol.rv": 1.0},
        missing_features=(),
        judgment=_judgment(as_of=as_of), links=(),
    )


def _realized(available_at: datetime = MATURED_AVAIL, grader_version: str = "grader-v1",
              forward_return: float = 0.043) -> RealizedRegime:
    return RealizedRegime.build(
        case_as_of=AS_OF, horizon_trading_days=20, entry_date="2026-07-18", exit_date="2026-08-15",
        forward_return=forward_return, max_drawdown=-0.02, realized_volatility=0.12,
        realized_trend="牛", realized_risk="risk_on", realized_heat=None,
        heat_unavailable_reason="no realized-heat definition in v1",
        available_at=available_at, data_snapshot_hash=DH, grader_version=grader_version,
        grader_digest=DH, benchmark_id="eqw.all_a",
    )


def _matured(case_id: str = "case.a", realized: RealizedRegime | None = None,
             matured_at: datetime = MATURED_AVAIL) -> CaseMatured:
    realized = realized or _realized()
    return CaseMatured.build(
        case_id=case_id, realized=realized, matured_at=matured_at,
        available_at=realized.available_at,
    )


def _reviewed(case_id: str, maturity_event_id: str, lesson: str = "a durable lesson",
              available_at: datetime = MATURED_AVAIL) -> CaseReviewed:
    return CaseReviewed.build(
        case_id=case_id, maturity_event_id=maturity_event_id, lesson=lesson,
        reviewed_at=available_at, available_at=available_at,
    )


def _key_case(cid: str) -> str:
    return f"case.created:{cid}"


def _key_matured(cid: str, grader: str) -> str:
    return f"case.matured:{cid}:{grader}"


def _key_reviewed(cid: str, meid: str) -> str:
    return f"case.reviewed:{cid}:{meid}"


# --------------------------------------------------------------------------- #
# happy path — pending → matured → reviewed                                     #
# --------------------------------------------------------------------------- #
def test_stream_identity_is_main_partition():
    assert EXPERIENCE_STREAM_ID == "experience.lane0.v1"
    assert EXPERIENCE_PARTITION == "main"


def test_append_fold_pending_matured_reviewed():
    env = _Env()
    log = env.log()
    case = _case("case.a")
    ev_created = log.append_case(case, correlation_run_id="run-1", idempotency_key=_key_case("case.a"))
    assert ev_created.event_type is EventType.CASE_CREATED
    assert ev_created.correlation_id == "run-1"

    # fold #1 — pending
    views = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=MATURED_AVAIL)
    assert len(views) == 1 and views[0].state == "pending"
    assert views[0].realized is None and views[0].lesson is None

    # mature
    matured = _matured("case.a")
    ev_matured = log.append_matured(matured, idempotency_key=_key_matured("case.a", "grader-v1"))
    assert ev_matured.event_type is EventType.CASE_MATURED

    views = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=MATURED_AVAIL)
    assert len(views) == 1 and views[0].state == "matured"
    assert views[0].realized is not None
    assert views[0].maturity_event_id == ev_matured.event_id
    assert views[0].lesson is None

    # review (admin-gated)
    reviewed = _reviewed("case.a", ev_matured.event_id)
    ev_reviewed = log.append_reviewed(
        reviewed, actor="admin-token", verifier=StubVerifier(),
        idempotency_key=_key_reviewed("case.a", ev_matured.event_id),
    )
    assert ev_reviewed.event_type is EventType.CASE_REVIEWED

    views = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=MATURED_AVAIL)
    assert len(views) == 1 and views[0].state == "reviewed"
    assert views[0].lesson == "a durable lesson"
    assert views[0].realized is not None


def test_all_committed_payloads_and_events_are_main():
    env = _Env()
    log = env.log()
    case = _case("case.a")
    ev = log.append_case(case, correlation_run_id=None, idempotency_key=_key_case("case.a"))
    assert ev.partition == "main"
    assert ev.payload_ref.namespace == "main"


# --------------------------------------------------------------------------- #
# idempotency                                                                   #
# --------------------------------------------------------------------------- #
def test_same_key_same_content_replay_returns_stored_event():
    env = _Env()
    log = env.log()
    case = _case("case.a")
    e1 = log.append_case(case, correlation_run_id="r", idempotency_key=_key_case("case.a"))
    e2 = log.append_case(case, correlation_run_id="r", idempotency_key=_key_case("case.a"))
    assert e1.event_id == e2.event_id
    assert env.stores.payloads_object_count() == 1
    assert len(log.visible_case_events()) == 1


def test_same_key_different_content_raises_conflict():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id="r", idempotency_key=_key_case("case.a"))
    drifted = _case("case.a", feature_schema_version="mfs-v2")  # same id, different content
    with pytest.raises(IdempotencyConflict):
        log.append_case(drifted, correlation_run_id="r", idempotency_key=_key_case("case.a"))


def test_matured_same_key_same_content_replays():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    m = _matured("case.a")
    e1 = log.append_matured(m, idempotency_key=_key_matured("case.a", "grader-v1"))
    e2 = log.append_matured(m, idempotency_key=_key_matured("case.a", "grader-v1"))
    assert e1.event_id == e2.event_id


# --------------------------------------------------------------------------- #
# refusals / guards                                                             #
# --------------------------------------------------------------------------- #
def test_matured_before_created_refused():
    env = _Env()
    log = env.log()
    with pytest.raises(ExperienceLogError) as exc:
        log.append_matured(_matured("case.ghost"), idempotency_key=_key_matured("case.ghost", "grader-v1"))
    assert "case.ghost" in str(exc.value)
    assert env.stores.payloads_object_count() == 0


def test_reviewed_before_matured_refused():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    rv = _reviewed("case.a", "event-experience.lane0.v1-main-999")  # no such matured event
    with pytest.raises(ExperienceLogError) as exc:
        log.append_reviewed(rv, actor="admin-token", verifier=StubVerifier(),
                            idempotency_key=_key_reviewed("case.a", "event-x"))
    assert "case.a" in str(exc.value)


def test_reviewed_wrong_case_for_maturity_event_refused():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    log.append_case(_case("case.b"), correlation_run_id=None, idempotency_key=_key_case("case.b"))
    m_a = log.append_matured(_matured("case.a"), idempotency_key=_key_matured("case.a", "grader-v1"))
    # try to review case.b using case.a's maturity event
    rv = _reviewed("case.b", m_a.event_id)
    with pytest.raises(ExperienceLogError):
        log.append_reviewed(rv, actor="admin-token", verifier=StubVerifier(),
                            idempotency_key=_key_reviewed("case.b", m_a.event_id))


def test_reviewed_without_verifier_refused_fail_closed():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    m = log.append_matured(_matured("case.a"), idempotency_key=_key_matured("case.a", "grader-v1"))
    rv = _reviewed("case.a", m.event_id)
    with pytest.raises(ExperienceLogError):
        log.append_reviewed(rv, actor="admin-token", verifier=None,
                            idempotency_key=_key_reviewed("case.a", m.event_id))


def test_reviewed_failed_verification_refused():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    m = log.append_matured(_matured("case.a"), idempotency_key=_key_matured("case.a", "grader-v1"))
    rv = _reviewed("case.a", m.event_id)
    with pytest.raises(PermissionError):
        log.append_reviewed(rv, actor="bad-token", verifier=StubVerifier(),
                            idempotency_key=_key_reviewed("case.a", m.event_id))
    with pytest.raises(PermissionError):
        log.append_reviewed(rv, actor="whatever", verifier=AlwaysFailVerifier(),
                            idempotency_key=_key_reviewed("case.a", m.event_id))


def test_non_main_append_rejected_before_store_call():
    env = _Env()
    log = env.log()
    case = _case("case.a")
    with pytest.raises(ExperienceLogError):
        log._commit_append(
            schema="RegimeCase", payload=case, event_type=EventType.CASE_CREATED,
            idempotency_key=_key_case("case.a"), correlation_id=None, namespace="sealed",
        )
    assert env.stores.payloads_object_count() == 0
    assert log.visible_case_events() == ()


# --------------------------------------------------------------------------- #
# UoW all-or-none under injected mid-batch failure                              #
# --------------------------------------------------------------------------- #
def test_injected_mid_batch_failure_leaves_no_orphan():
    resolver = SchemaRegistryResolver()
    registry = _experience_registry()
    resolver.register(registry)
    stores = RuntimeStores(resolver=resolver, clock=NaiveClock())
    log = ExperienceLog(
        event_store=stores.events, payload_store=stores.payloads, registry=registry,
        clock=NaiveClock(), uow_factory=lambda: stores.unit_of_work,
    )
    with pytest.raises(ValueError):  # clock_now rejects the naive datetime mid-batch
        log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    # all-or-none: neither the payload nor the event survived
    assert stores.payloads_object_count() == 0
    assert stores.events.journal(EXPERIENCE_STREAM_ID, EXPERIENCE_PARTITION) == ()


# --------------------------------------------------------------------------- #
# PIT visibility                                                                #
# --------------------------------------------------------------------------- #
def test_pit_visibility_progression():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    m = log.append_matured(_matured("case.a"), idempotency_key=_key_matured("case.a", "grader-v1"))
    _ = m

    # before case availability — nothing visible
    before = CASE_AVAIL - timedelta(seconds=1)
    assert fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=before) == ()

    # strictly between case and matured availability — pending
    between = CASE_AVAIL + timedelta(days=1)
    v = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=between)
    assert len(v) == 1 and v[0].state == "pending"

    # at/after matured availability — matured
    v = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=MATURED_AVAIL)
    assert len(v) == 1 and v[0].state == "matured"


def test_pit_invisible_case_hides_its_matured_fact():
    # a case whose available_at > as_of is entirely invisible even if a matured
    # fact would qualify (impossible by validator, tested anyway via a synthetic
    # matured fact whose availability is earlier than the case's).
    env = _Env()
    log = env.log()
    # case available late, matured available early (synthetic inversion)
    late_case = _case("case.a", as_of=CASE_AVAIL, available_at=MATURED_AVAIL)
    log.append_case(late_case, correlation_run_id=None, idempotency_key=_key_case("case.a"))
    early_realized = _realized(available_at=CASE_AVAIL)
    log.append_matured(_matured("case.a", realized=early_realized, matured_at=CASE_AVAIL),
                       idempotency_key=_key_matured("case.a", "grader-v1"))
    mid = CASE_AVAIL + timedelta(days=1)  # case invisible (avail=MATURED_AVAIL), matured visible
    with pytest.warns(OrphanExperienceEventWarning):
        v = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=mid)
    assert v == ()  # the case is entirely invisible


# --------------------------------------------------------------------------- #
# fold purity                                                                   #
# --------------------------------------------------------------------------- #
def test_fold_is_pure_same_events_same_views():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    log.append_case(_case("case.b"), correlation_run_id=None, idempotency_key=_key_case("case.b"))
    m = log.append_matured(_matured("case.a"), idempotency_key=_key_matured("case.a", "grader-v1"))
    _ = m
    events = log.visible_case_events()
    v1 = fold_case_views(events, resolve_payload=log.resolve_payload, as_of=MATURED_AVAIL)
    v2 = fold_case_views(events, resolve_payload=log.resolve_payload, as_of=MATURED_AVAIL)
    assert tuple(x.semantic_digest() for x in v1) == tuple(x.semantic_digest() for x in v2)
    # sorted by (as_of, id)
    assert [x.case.id for x in v1] == ["case.a", "case.b"]


def test_fold_ignores_foreign_events_and_is_total():
    # a foreign event type in the stream must be ignored, never raise.
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    events = log.visible_case_events()
    # append an unrelated event type on the same stream via a raw batch
    _inject_foreign_event(env)
    all_events = log.visible_case_events()
    assert len(all_events) >= len(events)
    v = fold_case_views(all_events, resolve_payload=log.resolve_payload, as_of=MATURED_AVAIL)
    assert len(v) == 1 and v[0].state == "pending"


# --------------------------------------------------------------------------- #
# orphan CaseReviewed direct-injection → dropped with a deterministic warning    #
# --------------------------------------------------------------------------- #
def test_direct_orphan_reviewed_dropped_by_fold_with_warning():
    env = _Env()
    log = env.log()
    log.append_case(_case("case.a"), correlation_run_id=None, idempotency_key=_key_case("case.a"))
    # inject a CASE_REVIEWED directly (bypassing the log guard) referencing a
    # non-existent maturity event.
    _inject_reviewed_directly(env, _reviewed("case.a", "event-experience.lane0.v1-main-777"))
    events = log.visible_case_events()
    with pytest.warns(OrphanExperienceEventWarning):
        v = fold_case_views(events, resolve_payload=log.resolve_payload, as_of=MATURED_AVAIL)
    # the case stays pending; the orphan reviewed is dropped
    assert len(v) == 1 and v[0].state == "pending"


# --------------------------------------------------------------------------- #
# raw-injection helpers (bypass the log guards to exercise fold defense)         #
# --------------------------------------------------------------------------- #
def _inject_reviewed_directly(env: _Env, reviewed: CaseReviewed) -> None:
    template = {name: getattr(reviewed, name) for name in type(reviewed).model_fields}
    batch = RuntimeBatch(
        idempotency_key="inject-reviewed",
        payload_puts=(
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="p"), schema_ref=_SR("CaseReviewed"),
                namespace="main", payload_template=template, registry_digest=env.digest,
                idempotency_key="inject-reviewed-put",
            ),
        ),
        event_appends=(
            EventAppendCommand(
                run_id=EXPERIENCE_STREAM_ID, partition="main",
                event_type=EventType.CASE_REVIEWED.value, payload_schema_ref=_SR("CaseReviewed"),
                payload_target=StagedPayloadKey(key="p"), registry_digest=env.digest,
                idempotency_key="inject-reviewed-evt",
            ),
        ),
    )
    env.stores.unit_of_work.commit(batch)


def _inject_foreign_event(env: _Env) -> None:
    # reuse a RegimeCase payload but tag the event as a non-case type (RUN_COMPLETED),
    # which the fold must ignore.
    case = _case("case.foreign")
    template = {name: getattr(case, name) for name in type(case).model_fields}
    batch = RuntimeBatch(
        idempotency_key="inject-foreign",
        payload_puts=(
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="p"), schema_ref=_SR("RegimeCase"),
                namespace="main", payload_template=template, registry_digest=env.digest,
                idempotency_key="inject-foreign-put",
            ),
        ),
        event_appends=(
            EventAppendCommand(
                run_id=EXPERIENCE_STREAM_ID, partition="main",
                event_type=EventType.RUN_COMPLETED.value, payload_schema_ref=_SR("RegimeCase"),
                payload_target=StagedPayloadKey(key="p"), registry_digest=env.digest,
                idempotency_key="inject-foreign-evt",
            ),
        ),
    )
    env.stores.unit_of_work.commit(batch)
