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

import bisect
import math
import statistics
import warnings
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any, ClassVar, Literal, NamedTuple, get_args

from pydantic import Field, model_validator

from guanlan_v2.orchestration.data.calendar import (
    ImmutableTradingCalendar,
    TradingCalendarMaterial,
    build_trading_calendar,
)
from guanlan_v2.orchestration.data.errors import FutureDataRefused
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    content_digest,
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
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now

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
    # scaler + retrieval contracts (Task 5)
    "ExperienceScalerSnapshot",
    "ExperienceQuery",
    "ExperienceNeighbour",
    "ExperienceSelection",
    "MIN_FEATURE_OVERLAP",
    # grader + observed-calendar contracts (Task 6)
    "RegimeGraderSpec",
    "CaseMaturityPending",
    "ObservedTradeDateCalendar",
    "build_observed_calendar",
    "grade_case",
    "mature_pending_cases",
    "matured_only",
    "OBSERVED_CALENDAR_ID",
    "OBSERVED_CALENDAR_MATERIAL_ID",
    "REALIZED_HEAT_UNAVAILABLE_REASON",
    # schema refs
    "REGIME_CASE_SCHEMA_REF",
    "REALIZED_REGIME_SCHEMA_REF",
    "CASE_MATURED_SCHEMA_REF",
    "CASE_REVIEWED_SCHEMA_REF",
    "EXPERIENCE_SCALER_SNAPSHOT_SCHEMA_REF",
    "EXPERIENCE_QUERY_SCHEMA_REF",
    "EXPERIENCE_SELECTION_SCHEMA_REF",
    "REGIME_GRADER_SPEC_SCHEMA_REF",
    # service + fold + retrieval
    "ExperienceLog",
    "fold_case_views",
    "fit_scaler",
    "retrieve_neighbours",
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
# RegimeGraderSpec@1 — the golden-frozen v1 grading policy (no LLM, D10)         #
# --------------------------------------------------------------------------- #
#: v1 realized-heat is honestly ``None`` — a realized-heat definition needs a
#: temperature history Lane 0 does not yet own (spec §13 挂账, plan D10).
REALIZED_HEAT_UNAVAILABLE_REASON = "no_realized_heat_definition_v1"


class RegimeGraderSpec(DigestModel):
    """The versioned, golden-frozen deterministic grading policy (registered
    ``RegimeGraderSpec@1``; spec §5 参数不拍脑袋 / plan D10).

    The grader NEVER runs an LLM. Every threshold lives here (invariant 5: no
    literal threshold in any grading function); v1 values are hand-frozen in
    ``tests/orchestration/golden/regime_grader_policy_v1.json`` and are *provisional*
    — tuning belongs to Phase 4 ``run_optimize`` over matured cases with the
    sealed-holdout discipline, explicitly out of scope here.

    Closed label rules (pinned by tests; the golden's ``notes`` array is the
    human-readable authority):

    * **window** — entry = the first observed session strictly after the case's
      ``as_of`` session (a case dated on a non-session grades from the previous
      session); exit = the ``horizon_trading_days``-th observed session after entry
      **by list position** (never calendar days — a holiday gap can never fake
      maturity).
    * **forward_return** = ∏(1+ret)−1 over the per-session benchmark returns in
      ``(entry, exit]``; **max_drawdown** = the minimum running drawdown of the
      compounded wealth path (≤ 0); **realized_volatility** = the population std of
      those returns.
    * **trend** (R6 词表, shared with ④ ``TrendState``): ``forward_return ≥
      bull_min_return`` ⇒ ``牛``; ``≤ bear_max_return`` ⇒ ``熊``; else ``震荡``.
    * **risk**: ``max_drawdown ≤ risk_off_max_drawdown`` ⇒ ``risk_off`` (drawdown
      precedence); else ``forward_return ≥ risk_on_min_return and max_drawdown >
      risk_on_min_drawdown`` ⇒ ``risk_on``; else ``neutral``.
    * **heat**: ``None`` in v1 with ``heat_unavailable_reason`` = the honest reason.
    """

    schema_version: Literal["1"] = "1"
    grader_version: NonEmptyStr
    horizon_trading_days: PositiveInt
    benchmark_id: LogicalId
    bull_min_return: FiniteFloat
    bear_max_return: FiniteFloat
    risk_off_max_drawdown: FiniteFloat
    risk_on_min_return: FiniteFloat
    risk_on_min_drawdown: FiniteFloat
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "RegimeGraderSpec":
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "RegimeGraderSpec":
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
# ExperienceScalerSnapshot@1 — the versioned point-in-time standardizer          #
# --------------------------------------------------------------------------- #
#: v1 fixes normalized standardized-Euclidean retrieval; the scaler standardizes
#: each feature by its expanding (≤ fit_as_of) z-score, exactly the in-repo
#: precedent ``factor_regime.py::walk_forward_regimes`` (mu/sd fitted on ≤t history
#: and persisted as versioned snapshots) — copied in shape, not in artifacts.
DEFAULT_SCALER_VERSION = "expanding-zscore-v1"


class ExperienceScalerSnapshot(DigestModel):
    """A versioned point-in-time standardizer (registered ``ExperienceScalerSnapshot@1``).

    ``mu``/``sd`` are fitted **only** on cases with ``available_at <= fit_as_of`` and
    a matching ``feature_schema_version`` (the spec red line 标准化器只在查询时点之前的
    数据拟合并版本化). A degenerate feature (fewer than two present observations, or a
    zero/near-zero spread) is *dropped* from ``mu``/``sd`` and listed honestly in
    ``degenerate_features`` — never standardized against a fabricated unit spread.
    Every retained ``sd`` value is strictly positive so the retrieval division is
    always well-defined. ``content_digest`` is this snapshot's identity — it is the
    ``scaler_digest`` recorded on a :class:`RegimeCase` / :class:`ExperienceSelection`
    (a case scored under scaler v1 vs v2 is therefore always distinguishable).
    """

    schema_version: Literal["1"] = "1"
    feature_schema_version: NonEmptyStr
    scaler_version: NonEmptyStr
    fit_as_of: UtcDateTime
    n_obs: NonNegativeInt
    mu: dict[LogicalId, FiniteFloat]
    sd: dict[LogicalId, FiniteFloat]
    degenerate_features: tuple[LogicalId, ...] = ()
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "ExperienceScalerSnapshot":
        if set(self.mu) != set(self.sd):
            raise ValueError("mu and sd must share exactly the same feature keys")
        for fid, spread in self.sd.items():
            if spread <= 0.0:
                raise ValueError(f"sd[{fid!r}] must be > 0 (a degenerate feature is dropped, not kept)")
        degen = list(self.degenerate_features)
        if degen != sorted(degen):
            raise ValueError("degenerate_features must be canonically sorted")
        if len(set(degen)) != len(degen):
            raise ValueError("degenerate_features must be duplicate-free")
        if set(degen) & set(self.mu):
            raise ValueError("degenerate_features must be disjoint from the fitted (mu/sd) keys")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "ExperienceScalerSnapshot":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# ExperienceQuery@1 / ExperienceNeighbour / ExperienceSelection@1                #
# --------------------------------------------------------------------------- #
#: fraction of the query's (scaler-covered) feature keys that must be co-present in
#: a candidate case; a candidate below this is excluded (missing is never imputed).
MIN_FEATURE_OVERLAP = 0.6


class ExperienceQuery(DigestModel):
    """The numeric nearest-neighbour query (registered ``ExperienceQuery@1``).

    A raw feature vector as of ``as_of``; retrieval requires the query's
    ``feature_schema_version`` to equal the scaler's, and ranks over PIT-visible
    matching-schema cases only. ``k`` is bounded (``1 <= k <= 20``).
    """

    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    feature_schema_version: NonEmptyStr
    features: dict[LogicalId, FiniteFloat]
    k: PositiveInt = Field(le=20)
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "ExperienceQuery":
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "ExperienceQuery":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


class ExperienceNeighbour(DigestModel):
    """One retrieved analog case (nested inside :class:`ExperienceSelection`).

    Carries the folded ``state``/``realized``/``lesson`` *as of the query time*: a
    ``pending`` neighbour shows no realized numbers and no lesson (unmatured numbers
    never leak), a ``matured`` neighbour carries realized numbers but no lesson, a
    ``reviewed`` neighbour carries both. Not a registered payload — it is digested
    only through its parent selection.
    """

    case_id: NonEmptyStr
    case_as_of: UtcDateTime
    distance: FiniteFloat = Field(ge=0.0)
    feature_overlap: FiniteFloat = Field(ge=0.0, le=1.0)
    state: Literal["pending", "matured", "reviewed"]
    realized: RealizedRegime | None = None
    lesson: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _verify(self) -> "ExperienceNeighbour":
        if self.state == "pending":
            if self.realized is not None or self.lesson is not None:
                raise ValueError("a pending neighbour carries no realized/lesson (numbers never leak)")
        elif self.state == "matured":
            if self.realized is None:
                raise ValueError("a matured neighbour requires realized numbers")
            if self.lesson is not None:
                raise ValueError("a matured neighbour carries no lesson (review is a later state)")
        else:  # reviewed
            if self.realized is None or self.lesson is None:
                raise ValueError("a reviewed neighbour requires realized + lesson")
        return self


class ExperienceSelection(DigestModel):
    """The retrieval result (registered ``ExperienceSelection@1``).

    ``neighbours`` are sorted ``(distance, case_id)`` and truncated to the query's
    ``k``; ``visible_case_count`` is how many PIT-visible matching-schema cases were
    ranked over; ``scaler_digest`` dereferences the exact
    :class:`ExperienceScalerSnapshot` used. A zero-neighbour result is honest (not an
    error) and always carries the ``cold_start:0_neighbours`` badge.
    """

    schema_version: Literal["1"] = "1"
    query_digest: DigestHex
    scaler_digest: DigestHex
    feature_schema_version: NonEmptyStr
    neighbours: tuple[ExperienceNeighbour, ...]
    visible_case_count: NonNegativeInt
    badges: tuple[NonEmptyStr, ...] = ()
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "ExperienceSelection":
        keys = [(n.distance, n.case_id) for n in self.neighbours]
        if keys != sorted(keys):
            raise ValueError("neighbours must be sorted by (distance, case_id)")
        case_ids = [n.case_id for n in self.neighbours]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("neighbours must be duplicate-free by case_id")
        if not self.neighbours and "cold_start:0_neighbours" not in self.badges:
            raise ValueError("a zero-neighbour selection must carry the cold_start:0_neighbours badge")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "ExperienceSelection":
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# Registered-schema refs (one definition each; downstream pinning).             #
# --------------------------------------------------------------------------- #
REGIME_CASE_SCHEMA_REF: SchemaRef = SchemaRef(name="RegimeCase", version="1")
REALIZED_REGIME_SCHEMA_REF: SchemaRef = SchemaRef(name="RealizedRegime", version="1")
CASE_MATURED_SCHEMA_REF: SchemaRef = SchemaRef(name="CaseMatured", version="1")
CASE_REVIEWED_SCHEMA_REF: SchemaRef = SchemaRef(name="CaseReviewed", version="1")
EXPERIENCE_SCALER_SNAPSHOT_SCHEMA_REF: SchemaRef = SchemaRef(name="ExperienceScalerSnapshot", version="1")
EXPERIENCE_QUERY_SCHEMA_REF: SchemaRef = SchemaRef(name="ExperienceQuery", version="1")
EXPERIENCE_SELECTION_SCHEMA_REF: SchemaRef = SchemaRef(name="ExperienceSelection", version="1")

#: the reviewed public (registered) contract surface of this module (Task 9 stands
#: up the Phase-5 completeness firewall + cumulative registry over it). The nested
#: ``ExperienceNeighbour`` is digested only through ``ExperienceSelection`` and is
#: deliberately not a standalone registered payload.
EXPERIENCE_PUBLIC_MODELS: tuple[type[DigestModel], ...] = (
    RegimeCase,
    RealizedRegime,
    CaseMatured,
    CaseReviewed,
    ExperienceScalerSnapshot,
    ExperienceQuery,
    ExperienceSelection,
    RegimeGraderSpec,
)

REGIME_GRADER_SPEC_SCHEMA_REF: SchemaRef = SchemaRef(name="RegimeGraderSpec", version="1")

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


# --------------------------------------------------------------------------- #
# fit_scaler — the pure, versioned point-in-time standardizer fit                #
# --------------------------------------------------------------------------- #
def fit_scaler(
    views: Sequence[CaseView],
    *,
    as_of: UtcDateTime,
    feature_schema_version: NonEmptyStr,
    scaler_version: NonEmptyStr = DEFAULT_SCALER_VERSION,
) -> ExperienceScalerSnapshot:
    """Fit a versioned point-in-time standardizer over the folded cases (pure).

    The spec red line: the standardizer is fitted **only** on data before the query
    point and versioned. Concretely — only cases with ``available_at <= as_of`` *and*
    a matching ``feature_schema_version`` contribute; per-feature ``mu``/``sd`` are the
    mean and population standard deviation over the cases where that feature is
    *present* (a missing feature is never imputed as zero). A feature with fewer than
    two present observations, or a zero spread, is degenerate — it is dropped from
    ``mu``/``sd`` and recorded in ``degenerate_features``. ``n_obs == 0`` is a valid
    cold-start snapshot; its retrieval necessarily returns zero neighbours with a
    badge. Population std matches the in-repo precedent (``factor_regime.py:101``).
    """
    qualifying = [
        v.case
        for v in views
        if v.case.feature_schema_version == feature_schema_version
        and v.case.available_at <= as_of
    ]
    present: dict[str, list[float]] = {}
    for case in qualifying:
        for fid, value in case.features.items():
            present.setdefault(fid, []).append(float(value))

    mu: dict[str, float] = {}
    sd: dict[str, float] = {}
    degenerate: list[str] = []
    for fid in sorted(present):
        values = present[fid]
        if len(values) < 2:
            degenerate.append(fid)  # cannot standardize a single observation
            continue
        spread = statistics.pstdev(values)
        if spread <= 0.0:
            degenerate.append(fid)  # zero spread — a constant feature is degenerate
            continue
        mu[fid] = statistics.fmean(values)
        sd[fid] = spread

    return ExperienceScalerSnapshot.build(
        feature_schema_version=feature_schema_version,
        scaler_version=scaler_version,
        fit_as_of=as_of,
        n_obs=len(qualifying),
        mu=mu,
        sd=sd,
        degenerate_features=tuple(sorted(degenerate)),
    )


# --------------------------------------------------------------------------- #
# retrieve_neighbours — the pure numeric nearest-neighbour retrieval             #
# --------------------------------------------------------------------------- #
def retrieve_neighbours(
    query: ExperienceQuery,
    *,
    views: Sequence[CaseView],
    scaler: ExperienceScalerSnapshot,
) -> ExperienceSelection:
    """Rank the PIT-visible analog cases by normalized standardized distance (pure).

    Closed order (spec red line: visibility predicate FIRST, then ranking):

    1. reject on ``feature_schema_version`` mismatch between query and scaler;
    2. re-assert PIT — a candidate whose ``available_at > query.as_of`` is a breach
       and raises :class:`FutureDataRefused` (defense in depth; the caller's fold
       already drops future cases);
    3. rank only cases sharing the query's ``feature_schema_version``;
    4. for each candidate, let ``K`` = keys co-present in the query, the candidate and
       the scaler's non-degenerate set; ``feature_overlap = |K| / |query ∩ scaler|``;
       exclude candidates with overlap ``< MIN_FEATURE_OVERLAP``; distance is the
       ``1/sqrt(|K|)``-normalized standardized Euclidean distance over ``K``;
    5. top-k by ``(distance, case_id)``, embedding each candidate's folded state.

    Zero surviving candidates ⇒ an honest empty selection badged
    ``cold_start:0_neighbours`` — never an error.
    """
    # (1) schema-version contamination is structurally impossible.
    if query.feature_schema_version != scaler.feature_schema_version:
        raise ValueError(
            f"query feature_schema_version {query.feature_schema_version!r} does not match "
            f"scaler {scaler.feature_schema_version!r}; refusing cross-schema retrieval"
        )

    # (2) PIT re-assertion — no future candidate may reach the ranking.
    future = [v for v in views if v.case.available_at > query.as_of]
    if future:
        raise FutureDataRefused(
            f"retrieve_neighbours saw {len(future)} case(s) available after as_of "
            f"{query.as_of.isoformat()} (the fold must filter before retrieval)",
            future_rows=len(future),
        )

    # (3) same-feature-schema candidates only.
    candidates = [v for v in views if v.case.feature_schema_version == query.feature_schema_version]
    visible_case_count = len(candidates)

    # (4) overlap gate + normalized standardized-Euclidean distance.
    scaler_keys = set(scaler.mu)  # the non-degenerate (standardizable) keys
    query_scaler_keys = set(query.features) & scaler_keys
    denominator = len(query_scaler_keys)

    neighbours: list[ExperienceNeighbour] = []
    if denominator:
        for view in candidates:
            case = view.case
            shared = query_scaler_keys & set(case.features)
            overlap = len(shared) / denominator
            if overlap < MIN_FEATURE_OVERLAP or not shared:
                continue
            acc = 0.0
            for fid in shared:
                spread = scaler.sd[fid]
                q_z = (query.features[fid] - scaler.mu[fid]) / spread
                c_z = (case.features[fid] - scaler.mu[fid]) / spread
                delta = q_z - c_z
                acc += delta * delta
            distance = math.sqrt(acc) / math.sqrt(len(shared))
            neighbours.append(
                ExperienceNeighbour(
                    case_id=case.id,
                    case_as_of=case.as_of,
                    distance=distance,
                    feature_overlap=overlap,
                    state=view.state,
                    realized=view.realized,
                    lesson=view.lesson,
                )
            )

    # (5) deterministic top-k by (distance, case_id).
    neighbours.sort(key=lambda n: (n.distance, n.case_id))
    top = tuple(neighbours[: query.k])
    badges: tuple[str, ...] = () if top else ("cold_start:0_neighbours",)

    return ExperienceSelection.build(
        query_digest=query.content_digest,
        scaler_digest=scaler.content_digest,
        feature_schema_version=query.feature_schema_version,
        neighbours=top,
        visible_case_count=visible_case_count,
        badges=badges,
    )


# --------------------------------------------------------------------------- #
# ObservedTradeDateCalendar — the "N 交易日后" authority (no wall clock, D3)      #
# --------------------------------------------------------------------------- #
#: the observed-trade-date calendar identity bound as ``ClockSpec.calendar_id`` for
#: the bootstrap ``DataContext`` (Task 8/10 consume this value).
OBSERVED_CALENDAR_ID = "cn-ashare-observed-v1"
#: the logical id of the versioned calendar material ref (its ``version`` tracks the
#: content — the list digest prefix by default).
OBSERVED_CALENDAR_MATERIAL_ID = "calendar.cn_ashare_observed"


class ObservedTradeDateCalendar:
    """A Phase-3 :class:`~guanlan_v2.orchestration.data.calendar.TradingCalendar`
    over the observed PIT trade-date list of the grading benchmark series.

    Phase 5 mints no exchange calendar and never uses ``np.busday`` / ``pd.bdate_range``
    for maturity (those are honest-approximation idioms unfit for realized-label
    arithmetic — they invent sessions across suspensions/holidays). The sole
    authority for "N 个交易日后" is the observed benchmark date list itself — bar
    counts / positions in the instrument's own series — which handles long holidays
    by construction and never consults a wall clock.

    Delegates the protocol surface (``calendar_id`` / ``material_ref`` / ``coverage``
    / :meth:`is_session` / :meth:`sessions_between`) to a verified
    :class:`~guanlan_v2.orchestration.data.calendar.ImmutableTradingCalendar` (consume-
    don't-fork), and adds the ordered list-position surface the delayed grader needs
    (``_realized_map`` idiom: the realized date is a *position* in the observed list).
    In PIT_REPLAY the list is frozen by its material digest; in ONLINE it only extends
    forward — a changed historical prefix is a refusal (the extension policy is owned
    by the consuming seeder/bootstrap task, not minted here).
    """

    __slots__ = ("_inner", "_sessions", "_index")

    def __init__(
        self, *, inner: ImmutableTradingCalendar, sessions: tuple[str, ...]
    ) -> None:
        self._inner = inner
        self._sessions = sessions
        self._index = {d: i for i, d in enumerate(sessions)}

    # -- TradingCalendar protocol (delegated to the verified material) ------- #
    @property
    def calendar_id(self) -> str:
        return self._inner.calendar_id

    @property
    def material_ref(self) -> Any:
        return self._inner.material_ref

    @property
    def coverage(self) -> tuple[date, date] | None:
        return self._inner.coverage

    def is_session(self, day: date) -> bool:
        return self._inner.is_session(day)

    def sessions_between(self, start: date, end: date) -> int:
        return self._inner.sessions_between(start, end)

    # -- ordered list-position surface (the position-in-observed-list idiom) -- #
    @property
    def sessions(self) -> tuple[str, ...]:
        """The ordered, strictly-increasing observed ISO trade-date list."""
        return self._sessions

    def index_of(self, day_iso: str) -> int | None:
        """The 0-based position of ``day_iso`` in the observed list, or ``None``."""
        return self._index.get(day_iso)

    def session_on_or_before(self, day_iso: str) -> str | None:
        """The last observed session ``<= day_iso`` (a non-session anchors to the
        previous session), or ``None`` when ``day_iso`` precedes coverage."""
        idx = bisect.bisect_right(self._sessions, day_iso) - 1
        return self._sessions[idx] if idx >= 0 else None

    def next_session(self, day_iso: str) -> str | None:
        """The first observed session strictly after ``day_iso``, or ``None``."""
        idx = bisect.bisect_right(self._sessions, day_iso)
        return self._sessions[idx] if idx < len(self._sessions) else None

    def session_at(self, idx: int) -> str | None:
        """The observed session at list position ``idx`` (bounds-checked)."""
        return self._sessions[idx] if 0 <= idx < len(self._sessions) else None


def build_observed_calendar(
    dates: tuple[NonEmptyStr, ...], *, version: NonEmptyStr | None = None
) -> ObservedTradeDateCalendar:
    """Build an :class:`ObservedTradeDateCalendar` from strictly-increasing ISO dates.

    ``dates`` must be canonically strictly increasing ISO ``YYYY-MM-DD`` strings
    (duplicates or descending order are a loud ``ValueError`` — the observed list is
    a fact, never re-sorted silently). ``version`` labels the material ref; when
    omitted it defaults to the **list content-digest prefix** so the version tracks
    the exact list (PIT_REPLAY identity).
    """
    iso = tuple(str(d) for d in dates)
    prev: str | None = None
    for s in iso:
        if len(s) != 10:
            raise ValueError(f"observed session {s!r} is not an ISO YYYY-MM-DD date")
        try:
            date.fromisoformat(s)
        except ValueError as exc:
            raise ValueError(f"observed session {s!r} is not a valid date: {exc}") from exc
        if prev is not None and s <= prev:
            raise ValueError(
                f"observed trade dates must be strictly increasing; {s!r} follows {prev!r}"
            )
        prev = s
    session_dates = [date.fromisoformat(s) for s in iso]
    if version is None:
        material = TradingCalendarMaterial(calendar_id=OBSERVED_CALENDAR_ID, sessions=iso)
        version = content_digest(material)[:12] or "empty"
    inner = build_trading_calendar(
        calendar_id=OBSERVED_CALENDAR_ID,
        sessions=session_dates,
        material_id=OBSERVED_CALENDAR_MATERIAL_ID,
        material_version=version,
    )
    return ObservedTradeDateCalendar(inner=inner, sessions=iso)


# --------------------------------------------------------------------------- #
# CaseMaturityPending — the Phase-4 WAITING_FOR_MATURITY carrier (clause C5)     #
# --------------------------------------------------------------------------- #
class CaseMaturityPending(NamedTuple):
    """The value a Phase-4 experiment wrapper folds into
    ``ExperimentStatus.WAITING_FOR_MATURITY`` for an un-matured case.

    Same ``resume_after`` / ``wakeup_key`` shape as the Phase-4
    :class:`~guanlan_v2.orchestration.optimize.MaturityPending` carrier (clause C5),
    so a grader-produced pending folds straight into the ledger's waiting state.
    ``resume_after`` is a *scheduling hint only* (a wakeup always re-checks maturity
    from the observed list — it is never a correctness input); ``wakeup_key`` is the
    deterministic ``case.mature:{case_id}:{grader_version}`` grammar Phase 5 owns.
    """

    resume_after: datetime
    wakeup_key: str


# --------------------------------------------------------------------------- #
# grade_case — the pure, LLM-free, delayed deterministic grader                  #
# --------------------------------------------------------------------------- #
def _label_trend(forward_return: float, grader: RegimeGraderSpec) -> str:
    """R6 词表 trend label — thresholds read only from ``grader`` (invariant 5)."""
    if forward_return >= grader.bull_min_return:
        return "牛"
    if forward_return <= grader.bear_max_return:
        return "熊"
    return "震荡"


def _label_risk(forward_return: float, max_drawdown: float, grader: RegimeGraderSpec) -> str:
    """Risk label with risk_off-by-drawdown precedence — thresholds from ``grader``."""
    if max_drawdown <= grader.risk_off_max_drawdown:
        return "risk_off"
    if forward_return >= grader.risk_on_min_return and max_drawdown > grader.risk_on_min_drawdown:
        return "risk_on"
    return "neutral"


def _maturity_pending(
    case: RegimeCase,
    *,
    sessions: tuple[str, ...],
    exit_idx: int,
    bench: tuple["DailyValueRow", ...],
    grader: RegimeGraderSpec,
) -> CaseMaturityPending:
    """Extrapolate a *scheduling hint* for when the exit session will land.

    The list positions still missing (``exit_idx - last_idx``) are projected forward
    by the mean recent session spacing, anchored on — and clamped to ≥ — the last
    observed session's availability (data-driven, never a wall clock). A wakeup
    re-checks real maturity from the observed list, so this value's only job is to
    stop the loop busy-waiting.
    """
    last_idx = len(sessions) - 1
    positions_beyond = exit_idx - last_idx  # > 0 by construction (not matured)
    recent = min(len(sessions) - 1, grader.horizon_trading_days)
    if recent >= 1:
        gaps = [
            (date.fromisoformat(sessions[i]) - date.fromisoformat(sessions[i - 1])).days
            for i in range(len(sessions) - recent, len(sessions))
        ]
        mean_gap_days = statistics.fmean(gaps)
    else:
        mean_gap_days = 1.0  # single-session fallback
    # the last observed session's close = the latest known bench availability.
    last_close = max(r.available_at for r in bench)
    resume_after = last_close + timedelta(days=positions_beyond * mean_gap_days)
    if resume_after < last_close:  # belt-and-suspenders clamp
        resume_after = last_close
    return CaseMaturityPending(
        resume_after=resume_after,
        wakeup_key=f"case.mature:{case.id}:{grader.grader_version}",
    )


def grade_case(
    case: RegimeCase,
    *,
    bench: tuple["DailyValueRow", ...],
    calendar: ObservedTradeDateCalendar,
    grader: RegimeGraderSpec,
) -> "RealizedRegime | CaseMaturityPending":
    """Deterministically grade one case — never an LLM, never before maturity.

    Closed steps (spec 延迟确定性标注 red line): (1) locate the case's session — the
    last observed session ``<= as_of`` (a non-session anchors to the previous
    session); (2) entry = the next session, exit = the ``horizon_trading_days``-th
    session after entry **by list position**; (3) if the exit session is absent from
    the observed list (the ``basket_perf`` bar-count criterion, inverted) return
    :class:`CaseMaturityPending`; (4) otherwise compound the benchmark returns over
    ``(entry, exit]``, derive drawdown / vol and the R6 labels, stamping
    ``available_at`` = the exit bar's availability (PIT, not a clock) and
    ``data_snapshot_hash`` = the digest of the exact ``(entry, exit]`` window. Bench
    rows carry ``available_at``, so grading at a historical vantage cannot see rows
    beyond it by construction.
    """
    as_of_iso = case.as_of.astimezone(timezone.utc).date().isoformat()
    anchor = calendar.session_on_or_before(as_of_iso)
    if anchor is None:
        raise ValueError(
            f"case {case.id!r} as_of {as_of_iso} precedes the observed calendar "
            "coverage; there is no session to ground grading on"
        )
    sessions = calendar.sessions
    anchor_idx = calendar.index_of(anchor)
    assert anchor_idx is not None  # anchor came from the list
    entry_idx = anchor_idx + 1
    horizon = grader.horizon_trading_days
    exit_idx = entry_idx + horizon
    # (3) maturity: the exit bar must exist in the observed list (basket_perf:36).
    if exit_idx >= len(sessions):
        return _maturity_pending(
            case, sessions=sessions, exit_idx=exit_idx, bench=bench, grader=grader
        )

    entry_date = sessions[entry_idx]
    exit_date = sessions[exit_idx]
    window_dates = sessions[entry_idx + 1 : exit_idx + 1]  # (entry, exit], horizon rows
    by_date = {r.date: r for r in bench}
    missing = [d for d in window_dates if d not in by_date]
    if missing:
        raise ValueError(
            f"benchmark series is missing observed session(s) {missing} for case "
            f"{case.id!r}; the observed calendar must be built from the bench dates"
        )
    window_rows = tuple(by_date[d] for d in window_dates)
    returns = [float(r.value) for r in window_rows]

    # (4) compounded wealth path anchored at 1.0 (entry) + running drawdown.
    wealth = 1.0
    running_max = 1.0
    max_drawdown = 0.0
    for ret in returns:
        wealth *= 1.0 + ret
        if wealth > running_max:
            running_max = wealth
        drawdown = wealth / running_max - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    forward_return = wealth - 1.0
    realized_volatility = statistics.pstdev(returns) if returns else 0.0

    exit_row = by_date[exit_date]
    return RealizedRegime.build(
        case_as_of=case.as_of,
        horizon_trading_days=horizon,
        entry_date=entry_date,
        exit_date=exit_date,
        forward_return=forward_return,
        max_drawdown=max_drawdown,
        realized_volatility=realized_volatility,
        realized_trend=_label_trend(forward_return, grader),
        realized_risk=_label_risk(forward_return, max_drawdown, grader),
        realized_heat=None,
        heat_unavailable_reason=REALIZED_HEAT_UNAVAILABLE_REASON,
        available_at=exit_row.available_at,
        data_snapshot_hash=content_digest(window_rows),
        grader_version=grader.grader_version,
        grader_digest=grader.content_digest,
        benchmark_id=grader.benchmark_id,
    )


# --------------------------------------------------------------------------- #
# mature_pending_cases — grade every pending view, append CaseMatured facts      #
# --------------------------------------------------------------------------- #
def mature_pending_cases(
    *,
    views: Sequence[CaseView],
    bench: tuple["DailyValueRow", ...],
    calendar: ObservedTradeDateCalendar,
    grader: RegimeGraderSpec,
    log: ExperienceLog,
    clock: AuthoritativeClock,
) -> tuple[CaseMatured, ...]:
    """Grade every ``pending`` view; append a :class:`CaseMatured` for the matured
    ones, leaving the rest pending.

    Idempotent via the Task-4 keys (``case.matured:{case_id}:{grader_version}``): a
    re-run over freshly-folded views finds those cases already ``matured`` (not
    ``pending``) and appends nothing new. ``CaseMatured.available_at`` = the exit
    bar's availability (the PIT fact, data-driven), while ``matured_at`` is the
    wall-clock audit fact read once per batch through :func:`clock_now` — for replay
    the two must never be conflated.
    """
    matured_at = clock_now(clock)  # wall-clock audit stamp (rejects a naive clock)
    out: list[CaseMatured] = []
    for view in views:
        if view.state != "pending":
            continue
        graded = grade_case(view.case, bench=bench, calendar=calendar, grader=grader)
        if isinstance(graded, CaseMaturityPending):
            continue  # not matured yet — leave pending, emit no event
        matured = CaseMatured.build(
            case_id=view.case.id,
            realized=graded,
            matured_at=matured_at,
            available_at=graded.available_at,
        )
        log.append_matured(
            matured,
            idempotency_key=f"case.matured:{view.case.id}:{grader.grader_version}",
        )
        out.append(matured)
    return tuple(out)


# --------------------------------------------------------------------------- #
# matured_only — the downstream validation/distillation gate (_pair_matured)     #
# --------------------------------------------------------------------------- #
def matured_only(views: Sequence[CaseView]) -> tuple[CaseView, ...]:
    """Return the batch iff every case is matured/reviewed; **raise** otherwise.

    The ``_pair_matured`` distillation rule transplanted (``console/tools.py:1117``):
    a batch is usable for validation/distillation only when every sampled case is
    matured — pending cases never leak un-realized numbers into feedback. Unlike the
    ``calibration.py`` silent-filter behavior (deliberately not copied for feedback
    paths), a pending case in a requested validation batch is a loud ``ValueError``
    naming the offending ids.
    """
    pending_ids = [v.case.id for v in views if v.state == "pending"]
    if pending_ids:
        raise ValueError(
            "a validation/distillation batch must contain no pending cases; refusing "
            f"— pending case id(s): {pending_ids}"
        )
    return tuple(views)
