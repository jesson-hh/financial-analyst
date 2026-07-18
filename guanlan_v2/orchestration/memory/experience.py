# -*- coding: utf-8 -*-
"""Phase 5 · Task 4 — experience library (经验库): PIT-safe case contracts +
event-folded ``ExperienceLog``.

The 经验库 is the framework's *pending-judgment* memory: a Lane 0 regime read is
recorded as a :class:`RegimeCase` the moment it is made, its market outcome is
graded later into a :class:`CaseMatured` fact, and a human/curator can distil an
accepted lesson into a :class:`CaseReviewed` fact. ``pending → matured →
reviewed`` is never an in-place edit — the three states are *views folded* from
the three reserved Phase-1 event types (``CaseCreated`` / ``CaseMatured`` /
``CaseReviewed``, ``events.py:131-133`` — reused verbatim, no enum change) over the
Phase-2 append-only store machinery, exactly the house precedent
:mod:`guanlan_v2.orchestration.trial_ledger` set (stateless fold + one
``RuntimeUnitOfWork`` per transition, persist-then-publish, deterministic
idempotency, ``IdempotencyConflict`` on same-key/different-content).

PIT red line (spec v1.1)
------------------------
Every payload carries its own tz-aware ``available_at``; :func:`fold_case_views`
pushes ``available_at <= as_of`` down as the FIRST consideration on every payload,
*before* any state folding — 先可见性谓词再排序. A matured fact whose availability
is still in the future leaves the case visible-but-``pending``; a case whose own
availability is in the future is entirely invisible even if a matured fact would
qualify. Filter-then-fold, never fold-then-filter.

Namespace red line
------------------
Every case/matured/reviewed payload is persisted in the ``main`` namespace (public
facts — the whole point is later retrieval). ``sealed`` / ``review`` / ``audit``
are never used by this module, so a sealed holdout metric can never masquerade as
an experience case by construction; an attempted non-``main`` append is rejected
by the log *before* any store call.

Admin red line ⑦ requires ⑤
---------------------------
:meth:`ExperienceLog.append_reviewed` is admin-gated through the fail-closed
Phase-3 :class:`~guanlan_v2.orchestration.memory.proposals.AdminReviewVerifier`
port: workers can only *propose* lessons through the Phase-3 memory-proposal
boundary; the reviewed append is the human/curator acceptance step and is never
reachable from a worker capability. A review requires a visible maturity (⑤) of
the same case.
"""
from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from typing import Any, ClassVar, Literal, get_args

from pydantic import Field, model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    PositiveInt,
    UtcDateTime,
)
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    IdempotencyConflict,
    PayloadPutCommand,
    RuntimeBatch,
    StagedPayloadKey,
)
from guanlan_v2.orchestration.events import EventType, RunEvent
from guanlan_v2.orchestration.market.factors import (
    HeatState,
    RegimeReport,
    RiskState,
    TrendState,
)
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.refs import (
    PUBLIC_PAYLOAD_NAMESPACES,
    LogicalId,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock

__all__ = [
    # errors / warnings
    "ExperienceLogError",
    "OrphanExperienceEventWarning",
    # stream identity
    "EXPERIENCE_STREAM_ID",
    "EXPERIENCE_PARTITION",
    "EXPERIENCE_PAYLOAD_NAMESPACE",
    # contracts
    "RegimeCase",
    "RealizedRegime",
    "CaseMatured",
    "CaseReviewed",
    "CaseView",
    # schema refs
    "REGIME_CASE_SCHEMA_REF",
    "REALIZED_REGIME_SCHEMA_REF",
    "CASE_MATURED_SCHEMA_REF",
    "CASE_REVIEWED_SCHEMA_REF",
    # service + fold
    "ExperienceLog",
    "fold_case_views",
    "EXPERIENCE_PUBLIC_MODELS",
]

_DIGEST_PLACEHOLDER = "0" * 64

#: the dedicated cross-run experience stream (D7). Cases outlive single runs; the
#: Phase-2 dual-cursor journal is reused unmodified. The implemented Phase-2
#: ``EventStore`` keys journals on ``(run_id, partition)`` and does NOT constrain
#: ``run_id`` to admitted runs, so the well-known stream id is used directly as the
#: event ``run_id`` (Task-0 clause C2: the append-only / idempotency / PIT
#: semantics are invariant either way).
EXPERIENCE_STREAM_ID = "experience.lane0.v1"
#: all experience events + payloads are public facts → the single ``main`` partition.
EXPERIENCE_PARTITION = "main"
EXPERIENCE_PAYLOAD_NAMESPACE = "main"


# --------------------------------------------------------------------------- #
# R6 — the realized 词表 is Task 2's axis vocabulary MINUS 'unknown'.            #
# --------------------------------------------------------------------------- #
# Grading is deterministic or it does not happen: a realized label is never
# 'unknown'. The realized labels MUST be Task 2's ④ axis-enum values minus
# 'unknown', so 判读 (RegimeReport axes) and realized share ONE 词表 and the ④§4
# calibration analysis is possible. The Literal annotations below are hand-frozen
# (a Literal cannot be built from a runtime tuple); the module-level assertion is
# the loud drift guard if Task 2's enums ever move.
RealizedTrend = Literal["牛", "熊", "震荡"]
RealizedRisk = Literal["risk_on", "risk_off", "neutral"]
RealizedHeat = Literal["normal", "overheat"]

_REALIZED_TREND_VALUES: tuple[str, ...] = get_args(RealizedTrend)
_REALIZED_RISK_VALUES: tuple[str, ...] = get_args(RealizedRisk)
_REALIZED_HEAT_VALUES: tuple[str, ...] = get_args(RealizedHeat)


def _assert_r6_vocabulary() -> None:
    pairs = (
        ("trend", set(_REALIZED_TREND_VALUES), {m.value for m in TrendState if m is not TrendState.UNKNOWN}),
        ("risk", set(_REALIZED_RISK_VALUES), {m.value for m in RiskState if m is not RiskState.UNKNOWN}),
        ("heat", set(_REALIZED_HEAT_VALUES), {m.value for m in HeatState if m is not HeatState.UNKNOWN}),
    )
    for axis, realized, judged in pairs:
        if realized != judged:
            raise RuntimeError(
                f"R6 vocabulary drift on the {axis} axis: realized labels {sorted(realized)} "
                f"!= Task 2 axis enum minus 'unknown' {sorted(judged)}"
            )


_assert_r6_vocabulary()


# --------------------------------------------------------------------------- #
# Errors / warnings                                                             #
# --------------------------------------------------------------------------- #
class ExperienceLogError(ValueError):
    """An :class:`ExperienceLog` append guard refused (subclasses ``ValueError``).

    Raised for a namespace masquerade, a maturity without a visible creation, or a
    review without a visible maturity of the same case — always naming the case.
    """


class OrphanExperienceEventWarning(UserWarning):
    """A well-formed maturity/review event lacked its prerequisite and was dropped.

    Defense in depth: the :class:`ExperienceLog` append guards make an orphan
    unreachable through the log, but a fixture stream injected directly can still
    carry one; :func:`fold_case_views` drops it and emits this deterministic
    warning rather than silently swallowing or raising.
    """


# --------------------------------------------------------------------------- #
# RegimeCase@1 — the pending-judgment case                                      #
# --------------------------------------------------------------------------- #
class RegimeCase(DigestModel):
    """One pending-judgment regime case (registered ``RegimeCase@1``; spec §6.4).

    Binds the PIT feature vector (with per-feature coverage + the honest missing
    set) and the ④-aligned Task 2 :class:`RegimeReport` judgment, embedded
    unchanged. ``available_at`` (validator ≥ ``as_of``) is when the case becomes a
    visible PIT fact. ``build`` seals ``content_digest``; a ``model_validator``
    re-verifies it on load.
    """

    schema_version: Literal["1"] = "1"
    id: NonEmptyStr
    as_of: UtcDateTime
    available_at: UtcDateTime
    feature_schema_version: NonEmptyStr
    scaler_digest: DigestHex
    features: dict[LogicalId, FiniteFloat]
    feature_coverage: dict[LogicalId, FiniteFloat]
    missing_features: tuple[LogicalId, ...] = ()
    judgment: RegimeReport
    links: tuple[NonEmptyStr, ...] = ()
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "RegimeCase":
        if self.available_at < self.as_of:
            raise ValueError("available_at must be >= as_of (a case is a PIT fact)")
        missing = list(self.missing_features)
        if missing != sorted(missing):
            raise ValueError("missing_features must be canonically sorted")
        if len(set(missing)) != len(missing):
            raise ValueError("missing_features must be duplicate-free")
        feature_keys = set(self.features)
        if feature_keys & set(missing):
            raise ValueError(
                "missing_features must be disjoint from the features keys "
                "(a feature is present or missing, never both)"
            )
        if not feature_keys <= set(self.feature_coverage):
            raise ValueError(
                "feature_coverage keys must be a superset of the features keys "
                "(every present feature carries a coverage)"
            )
        if self.judgment.as_of != self.as_of:
            raise ValueError("judgment.as_of must equal the case as_of")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RegimeCase":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# RealizedRegime@1 — the deterministic graded outcome                           #
# --------------------------------------------------------------------------- #
class RealizedRegime(DigestModel):
    """The deterministically graded market outcome of a case (registered
    ``RealizedRegime@1``; spec §6.4).

    Realized labels have **no** ``unknown`` — grading is deterministic or it does
    not happen (R6: the realized 词表 is Task 2's axis vocabulary minus
    ``unknown``). ``available_at`` is the exit bar's availability time (a data-driven
    PIT fact, never a wall clock); ``data_snapshot_hash`` is the content digest of
    the exact PIT benchmark window graded over. v1 ``realized_heat`` is ``None`` with
    a reason (no realized-heat definition yet — D10).
    """

    schema_version: Literal["1"] = "1"
    case_as_of: UtcDateTime
    horizon_trading_days: PositiveInt
    entry_date: NonEmptyStr
    exit_date: NonEmptyStr
    forward_return: FiniteFloat
    max_drawdown: FiniteFloat = Field(le=0.0)
    realized_volatility: FiniteFloat = Field(ge=0.0)
    realized_trend: RealizedTrend
    realized_risk: RealizedRisk
    realized_heat: RealizedHeat | None = None
    heat_unavailable_reason: NonEmptyStr | None = None
    available_at: UtcDateTime
    data_snapshot_hash: DigestHex
    grader_version: NonEmptyStr
    grader_digest: DigestHex
    benchmark_id: LogicalId
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "RealizedRegime":
        if self.realized_heat is None and self.heat_unavailable_reason is None:
            raise ValueError("realized_heat=None requires a heat_unavailable_reason")
        if self.realized_heat is not None and self.heat_unavailable_reason is not None:
            raise ValueError("a present realized_heat forbids heat_unavailable_reason")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RealizedRegime":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# CaseMatured@1 — append-only maturity event payload                            #
# --------------------------------------------------------------------------- #
class CaseMatured(DigestModel):
    """The append-only maturity fact for one case (registered ``CaseMatured@1``).

    Never edits the :class:`RegimeCase`; it carries the graded
    :class:`RealizedRegime` and pins ``available_at == realized.available_at`` so
    the maturity fact and its outcome share one PIT availability instant.
    """

    schema_version: Literal["1"] = "1"
    case_id: NonEmptyStr
    realized: RealizedRegime
    matured_at: UtcDateTime
    available_at: UtcDateTime
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "CaseMatured":
        if self.available_at != self.realized.available_at:
            raise ValueError("CaseMatured.available_at must equal realized.available_at")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "CaseMatured":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# CaseReviewed@1 — append-only accepted-lesson event payload                     #
# --------------------------------------------------------------------------- #
class CaseReviewed(DigestModel):
    """The append-only accepted-lesson fact for one case (registered
    ``CaseReviewed@1``; the human/curator acceptance step ⑦).

    ``maturity_event_id`` binds the exact visible ``CASE_MATURED`` event this lesson
    reviews (⑦ requires ⑤).
    """

    schema_version: Literal["1"] = "1"
    case_id: NonEmptyStr
    maturity_event_id: NonEmptyStr
    lesson: NonEmptyStr
    reviewed_at: UtcDateTime
    available_at: UtcDateTime
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "CaseReviewed":
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "CaseReviewed":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# CaseView — the internal folded read model (NOT a registered payload)          #
# --------------------------------------------------------------------------- #
class CaseView(DigestModel):
    """The folded ``pending``/``matured``/``reviewed`` view of one case.

    A read model (never persisted, never registered); its per-state carriers are
    kept honest by a validator so a ``pending`` view can never carry realized
    numbers and a ``matured`` view can never carry a lesson.
    """

    case: RegimeCase
    state: Literal["pending", "matured", "reviewed"]
    realized: RealizedRegime | None = None
    lesson: NonEmptyStr | None = None
    maturity_event_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _verify(self) -> "CaseView":
        if self.state == "pending":
            if self.realized is not None or self.lesson is not None or self.maturity_event_id is not None:
                raise ValueError("a pending view carries no realized/lesson/maturity")
        elif self.state == "matured":
            if self.realized is None or self.maturity_event_id is None:
                raise ValueError("a matured view requires realized + maturity_event_id")
            if self.lesson is not None:
                raise ValueError("a matured view carries no lesson (review is a later state)")
        else:  # reviewed
            if self.realized is None or self.lesson is None or self.maturity_event_id is None:
                raise ValueError("a reviewed view requires realized + lesson + maturity_event_id")
        return self


# --------------------------------------------------------------------------- #
# Registered-schema refs (one definition each; downstream pinning).             #
# --------------------------------------------------------------------------- #
REGIME_CASE_SCHEMA_REF: SchemaRef = SchemaRef(name="RegimeCase", version="1")
REALIZED_REGIME_SCHEMA_REF: SchemaRef = SchemaRef(name="RealizedRegime", version="1")
CASE_MATURED_SCHEMA_REF: SchemaRef = SchemaRef(name="CaseMatured", version="1")
CASE_REVIEWED_SCHEMA_REF: SchemaRef = SchemaRef(name="CaseReviewed", version="1")

#: the reviewed public (registered) contract surface of this module (Task 9 stands
#: up the Phase-5 completeness firewall + cumulative registry over it).
EXPERIENCE_PUBLIC_MODELS: tuple[type[DigestModel], ...] = (
    RegimeCase,
    RealizedRegime,
    CaseMatured,
    CaseReviewed,
)

_SCHEMA_FOR_EVENT: dict[EventType, str] = {
    EventType.CASE_CREATED: "RegimeCase",
    EventType.CASE_MATURED: "CaseMatured",
    EventType.CASE_REVIEWED: "CaseReviewed",
}
_CASE_EVENT_TYPES = frozenset(_SCHEMA_FOR_EVENT)


# --------------------------------------------------------------------------- #
# ExperienceLog — event-sourced append + read side over the Phase-2 stores      #
# --------------------------------------------------------------------------- #
class ExperienceLog:
    """Append-only experience store over the Phase-2 stores (D7 cross-run stream).

    Holds no mutable state: reads fold the journal fresh and every append is one
    :class:`~guanlan_v2.orchestration.eventstore.RuntimeUnitOfWork` batch (one
    ``main`` payload put + one typed :class:`RunEvent` on a reserved Phase-1 event
    type), persist-then-publish, all-or-none. Constructed over the injected Phase-2
    ``EventStore`` + ``PayloadStore`` (which the reserved
    :class:`~guanlan_v2.orchestration.runtime_clock.AuthoritativeClock` already
    stamps time through), the sealed cumulative ``registry`` the payloads validate
    against, and a ``uow_factory`` yielding the unit of work to commit each append.
    """

    def __init__(
        self,
        *,
        event_store: Any,
        payload_store: Any,
        registry: Any,
        clock: AuthoritativeClock,
        uow_factory: Callable[[], Any],
    ) -> None:
        self._event_store = event_store
        self._payload_store = payload_store
        self._registry = registry
        self._registry_digest = registry if isinstance(registry, str) else registry.registry_digest
        #: the shared authoritative clock the injected EventStore / UoW read time
        #: through (retained for the construction contract; every event timestamp is
        #: assigned store-side, so this module reads no wall clock itself).
        self._clock = clock
        self._uow_factory = uow_factory

    # -- low-level helpers -------------------------------------------------- #
    def resolve_payload(self, ref: TypedPayloadRef) -> Any:
        """Dereference a typed payload ref through the injected PayloadStore.

        The production ``resolve_payload`` :func:`fold_case_views` consumes.
        """
        return self._payload_store.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    def _resolve_event_payload(self, event: RunEvent, schema_name: str) -> Any:
        ref = TypedPayloadRef(schema_ref=event.payload_schema_ref, payload_ref=event.payload_ref)
        if ref.schema_ref.name != schema_name or ref.schema_ref.version != "1":
            raise ExperienceLogError(
                f"event {event.event_id!r} does not resolve a {schema_name}@1 payload"
            )
        return self.resolve_payload(ref)

    def _journal(self) -> tuple[RunEvent, ...]:
        return self._event_store.journal(EXPERIENCE_STREAM_ID, EXPERIENCE_PARTITION)

    def _find_by_idempotency_key(self, key: str) -> RunEvent | None:
        for ev in self._journal():
            if ev.idempotency_key == key:
                return ev
        return None

    def _find_visible_event(self, event_id: str) -> RunEvent | None:
        for ev in self.visible_case_events():
            if ev.event_id == event_id:
                return ev
        return None

    def _has_visible_created(self, case_id: str) -> bool:
        for ev in self.visible_case_events():
            if ev.event_type is EventType.CASE_CREATED:
                case = self._resolve_event_payload(ev, "RegimeCase")
                if case.id == case_id:
                    return True
        return False

    # -- read side ---------------------------------------------------------- #
    def visible_case_events(self) -> tuple[RunEvent, ...]:
        """The visible experience stream in ``visible_seq`` order.

        The sole input :func:`fold_case_views` consumes in production; staged /
        journal-only events never reach the fold (every experience event is a
        ``main``-partition, visible-sequenced case event).
        """
        return self._event_store.visible(EXPERIENCE_STREAM_ID, EXPERIENCE_PARTITION)

    # -- append side -------------------------------------------------------- #
    def _commit_append(
        self,
        *,
        schema: str,
        payload: DigestModel,
        event_type: EventType,
        idempotency_key: str,
        correlation_id: str | None,
        namespace: str = EXPERIENCE_PAYLOAD_NAMESPACE,
    ) -> RunEvent:
        """Persist one payload + append one typed event as a single atomic batch.

        The namespace masquerade guard runs *before* any store call: a non-``main``
        append is rejected outright (the Phase-2 validators would also reject it,
        but the log never lets it reach them).
        """
        if namespace not in PUBLIC_PAYLOAD_NAMESPACES:
            raise ExperienceLogError(
                f"experience payloads live only in {EXPERIENCE_PAYLOAD_NAMESPACE!r}; "
                f"refusing a {namespace!r} append (namespace masquerade)"
            )
        schema_ref = SchemaRef(name=schema, version="1")
        template = {name: getattr(payload, name) for name in type(payload).model_fields}
        batch = RuntimeBatch(
            idempotency_key=f"experience-append:{idempotency_key}",
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="payload"),
                    schema_ref=schema_ref,
                    namespace=namespace,
                    payload_template=template,
                    registry_digest=self._registry_digest,
                    idempotency_key=f"{idempotency_key}:put",
                ),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=EXPERIENCE_STREAM_ID,
                    partition=EXPERIENCE_PARTITION,
                    event_type=event_type.value,
                    payload_schema_ref=schema_ref,
                    payload_target=StagedPayloadKey(key="payload"),
                    registry_digest=self._registry_digest,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                ),
            ),
        )
        result = self._uow_factory().commit(batch)
        return result.events[0]

    def append_case(
        self,
        case: RegimeCase,
        *,
        correlation_run_id: str | None,
        idempotency_key: str,
    ) -> RunEvent:
        """Append the root ``CaseCreated`` fact (creation has no prerequisite).

        Same-key / same-content replay returns the stored event; same-key /
        different-content raises :class:`IdempotencyConflict` — one committed
        :class:`RegimeCase` per id per feature schema, reruns with drifted judgments
        conflict loudly instead of silently duplicating.
        """
        existing = self._find_by_idempotency_key(idempotency_key)
        if existing is not None:
            stored = self._resolve_event_payload(existing, "RegimeCase")
            if stored.content_digest != case.content_digest:
                raise IdempotencyConflict(
                    f"case idempotency key {idempotency_key!r} reused with different content"
                )
            return existing
        return self._commit_append(
            schema="RegimeCase", payload=case, event_type=EventType.CASE_CREATED,
            idempotency_key=idempotency_key, correlation_id=correlation_run_id,
        )

    def append_matured(self, matured: CaseMatured, *, idempotency_key: str) -> RunEvent:
        """Append a ``CaseMatured`` fact — a visible ``CaseCreated`` for the case
        must already exist (append-only; never edits the case).

        Refuses with :class:`ExperienceLogError` (naming the case) when no visible
        ``CaseCreated`` for ``case_id`` exists.
        """
        existing = self._find_by_idempotency_key(idempotency_key)
        if existing is not None:
            stored = self._resolve_event_payload(existing, "CaseMatured")
            if stored.content_digest != matured.content_digest:
                raise IdempotencyConflict(
                    f"matured idempotency key {idempotency_key!r} reused with different content"
                )
            return existing
        if not self._has_visible_created(matured.case_id):
            raise ExperienceLogError(
                f"cannot mature case {matured.case_id!r}: no visible CaseCreated exists"
            )
        return self._commit_append(
            schema="CaseMatured", payload=matured, event_type=EventType.CASE_MATURED,
            idempotency_key=idempotency_key, correlation_id=None,
        )

    def append_reviewed(
        self,
        reviewed: CaseReviewed,
        *,
        actor: AuthenticatedAdminPrincipal | Any,
        verifier: Any,
        idempotency_key: str,
    ) -> RunEvent:
        """Append a ``CaseReviewed`` fact — the admin-gated acceptance step ⑦.

        Fail-closed: a missing verifier is refused before anything else, and the
        injected :class:`AdminReviewVerifier` authenticates ``actor`` (raising on a
        bad credential). ⑦ requires ⑤: ``maturity_event_id`` must resolve to a
        visible ``CaseMatured`` of the *same* case, else
        :class:`ExperienceLogError` (naming the case).
        """
        if verifier is None:
            raise ExperienceLogError(
                f"cannot review case {reviewed.case_id!r}: experience review is disabled "
                "(no fail-closed AdminReviewVerifier injected)"
            )
        verifier.verify(actor)  # fail-closed: raises on an unauthenticated actor
        existing = self._find_by_idempotency_key(idempotency_key)
        if existing is not None:
            stored = self._resolve_event_payload(existing, "CaseReviewed")
            if stored.content_digest != reviewed.content_digest:
                raise IdempotencyConflict(
                    f"reviewed idempotency key {idempotency_key!r} reused with different content"
                )
            return existing
        maturity_event = self._find_visible_event(reviewed.maturity_event_id)
        if maturity_event is None or maturity_event.event_type is not EventType.CASE_MATURED:
            raise ExperienceLogError(
                f"cannot review case {reviewed.case_id!r}: maturity_event_id "
                f"{reviewed.maturity_event_id!r} does not resolve to a visible CaseMatured"
            )
        matured_payload = self._resolve_event_payload(maturity_event, "CaseMatured")
        if matured_payload.case_id != reviewed.case_id:
            raise ExperienceLogError(
                f"cannot review case {reviewed.case_id!r}: maturity event belongs to "
                f"case {matured_payload.case_id!r}"
            )
        return self._commit_append(
            schema="CaseReviewed", payload=reviewed, event_type=EventType.CASE_REVIEWED,
            idempotency_key=idempotency_key, correlation_id=None,
        )


# --------------------------------------------------------------------------- #
# fold_case_views — the pure, total, PIT-first read model                       #
# --------------------------------------------------------------------------- #
def fold_case_views(
    events: Sequence[RunEvent],
    *,
    resolve_payload: Callable[[TypedPayloadRef], object],
    as_of: UtcDateTime,
) -> tuple[CaseView, ...]:
    """Fold the visible experience stream into per-case ``CaseView``s (pure, total).

    Order of operations is frozen: (1) keep only visible events of the three case
    types; (2) resolve payloads; (3) **PIT-filter each payload by its own
    ``available_at <= as_of`` before any other consideration** (先可见性谓词再排序);
    (4) fold to per-case state; (5) sort by ``(as_of, id)``. Filter-then-fold, never
    fold-then-filter.

    Total: malformed / foreign event types are ignored, never raise (this is a read
    model, not a validator). A well-formed maturity/review without its prerequisite
    is dropped with a deterministic :class:`OrphanExperienceEventWarning` (defense
    in depth — the append guards make this unreachable through the log).
    """
    created: dict[str, RegimeCase] = {}
    matured: dict[str, list[tuple[int, str, RealizedRegime]]] = {}
    reviewed: dict[str, list[tuple[int, str, str, str]]] = {}

    for ev in events:
        # (1) visible case events only.
        if ev.visible_seq is None or ev.event_type not in _CASE_EVENT_TYPES:
            continue
        # (2) resolve. A schema/type mismatch is a malformed event → ignore (total).
        expected_schema = _SCHEMA_FOR_EVENT[ev.event_type]
        if ev.payload_schema_ref.name != expected_schema or ev.payload_schema_ref.version != "1":
            continue
        payload = resolve_payload(
            TypedPayloadRef(schema_ref=ev.payload_schema_ref, payload_ref=ev.payload_ref)
        )
        # (3) PIT filter each payload by its own availability BEFORE any folding.
        if payload.available_at > as_of:
            continue
        if ev.event_type is EventType.CASE_CREATED:
            if isinstance(payload, RegimeCase):
                created[payload.id] = payload
        elif ev.event_type is EventType.CASE_MATURED:
            if isinstance(payload, CaseMatured):
                matured.setdefault(payload.case_id, []).append(
                    (ev.visible_seq, ev.event_id, payload.realized)
                )
        else:  # CASE_REVIEWED
            if isinstance(payload, CaseReviewed):
                reviewed.setdefault(payload.case_id, []).append(
                    (ev.visible_seq, ev.event_id, payload.maturity_event_id, payload.lesson)
                )

    # (4) fold to per-case state (+ collect deterministic orphan warnings).
    views: list[CaseView] = []
    orphan_msgs: list[str] = []
    for case_id, case in created.items():
        m_list = sorted(matured.get(case_id, []), key=lambda t: t[0])
        r_list = sorted(reviewed.get(case_id, []), key=lambda t: t[0])
        matured_by_event_id = {eid: realized for (_seq, eid, realized) in m_list}

        chosen_review: tuple[str, str] | None = None  # (maturity_event_id, lesson)
        for (_seq, _r_eid, mat_eid, lesson) in r_list:
            if mat_eid in matured_by_event_id:
                chosen_review = (mat_eid, lesson)  # last valid wins (sorted by visible_seq)
            else:
                orphan_msgs.append(
                    f"case {case_id!r}: CaseReviewed references maturity_event_id "
                    f"{mat_eid!r} with no visible CaseMatured (dropped)"
                )
        if chosen_review is not None:
            mat_eid, lesson = chosen_review
            views.append(CaseView(
                case=case, state="reviewed", realized=matured_by_event_id[mat_eid],
                lesson=lesson, maturity_event_id=mat_eid,
            ))
        elif m_list:
            _seq, eid, realized = m_list[-1]  # latest matured by visible_seq
            views.append(CaseView(
                case=case, state="matured", realized=realized, lesson=None, maturity_event_id=eid,
            ))
        else:
            views.append(CaseView(case=case, state="pending"))

    # matured / reviewed whose case is invisible (no visible CaseCreated) are orphans.
    for case_id in sorted(set(matured) | set(reviewed)):
        if case_id not in created:
            orphan_msgs.append(
                f"case {case_id!r}: CaseMatured/CaseReviewed without a visible "
                "CaseCreated (dropped)"
            )

    # (5) sort by (as_of, id).
    views.sort(key=lambda v: (v.case.as_of, v.case.id))

    for msg in sorted(orphan_msgs):
        warnings.warn(msg, OrphanExperienceEventWarning, stacklevel=2)
    return tuple(views)
