# -*- coding: utf-8 -*-
"""Phase 6 · Task 1 — the shadow-consumer proposal contracts.

The shadow consumer runs the fa backtest engine forward as an honest,
non-live "影子消费端": an LLM trader emits a *proposal* over target weights and
exit parameters, the framework validates it against the strict long-only v1
invariants, and — only if it passes — later tasks stage it into a signed intent
envelope + a decision schedule. This module owns the *proposal half* (Task 1):

* :class:`TrancheTrigger`          — one optional batched entry-trigger price
  interval, minimal all-Optional shape, nested inside a target leg (Task 1b);
* :class:`TargetPosition`          — one long-only target-weight leg (+ optional
  stop/take-profit/max-hold exit parameters + optional ``entry_tranches``);
* :class:`PortfolioTargetProposal` — the whole book: positions + cash, a
  non-blank rationale and a closed confidence, with the fixed-order model
  validator that rejects a duplicated symbol, a broken weight-sum identity,
  aggregate leverage, or an off-band target weight (Task 1b's closed
  :data:`TARGET_WEIGHT_BANDS`) **at construction time** (before any staging).

Design red lines encoded here:

* the proposal is the ONLY LLM-writable payload in this phase; it carries **zero
  envelope fields** (no ``intent_id`` / ``authority`` / ``scheduled_for`` / …),
  and the inherited ``extra="forbid"`` rejects any attempt to smuggle one in;
* the weight-sum identity is checked exactly at ``WEIGHT_SUM_TOLERANCE`` and the
  book is **never renormalized** — a passing proposal's weights re-read
  byte-identical;
* shorts are impossible via ``ge=0`` field bounds, NaN/Inf via ``FiniteFloat``,
  so the long-only guarantee is structural, not advisory;
* every field is semantic — no ``SEMANTIC_EXCLUDE``, no self-digest field — so a
  proposal's semantic digest depends on its full content and nothing else.

These two contracts are **not** registered by any Phase 1–5 schema registry;
their reviewed Phase-6 registration (``PHASE6_PUBLIC_MODELS`` + cumulative
registry/golden) lands in a later task. The completeness firewall scopes this
module via ``PHASE6_MODULES`` in ``tests/orchestration/test_contract_completeness.py``.

Reason-code interaction: the ``@model_validator`` raises :class:`PydanticCustomError`
with the closed reason codes so a caller can read the code off the resulting
``ValidationError`` (``err["type"]``); :class:`ProposalRejected` is the paired
stage-前 exception carrying the same closed :data:`PROPOSAL_REASON_CODES`
vocabulary for the staging boundary a later task adds.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from guanlan_v2.orchestration.data.calendar import ImmutableTradingCalendar
from guanlan_v2.orchestration.data.symbols import Symbol
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
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.refs import ContentRef, LogicalId
from guanlan_v2.orchestration.runtime_clock import clock_now

if TYPE_CHECKING:  # annotations only (``from __future__ import annotations``) — no cycle
    from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock
    from guanlan_v2.orchestration.schemas import Artifact
    from guanlan_v2.orchestration.spec import OrchestrationRequest

__all__ = [
    "ShadowContractError",
    "ProposalRejected",
    "PROPOSAL_REASON_CODES",
    "WEIGHT_SUM_TOLERANCE",
    "TARGET_WEIGHT_BANDS",
    "TrancheTrigger",
    "TargetPosition",
    "PortfolioTargetProposal",
    # Task 2 — decision schedule + registry + unique time computation
    "LocalTimeStr",
    "IsoDateStr",
    "SHADOW_MATCHING_ENGINE_VERSION",
    "ASHARE_SESSION_OPEN",
    "ASHARE_SESSION_CLOSE",
    "DecisionSchedule",
    "ScheduleComputationError",
    "UnsupportedBarFrequencyError",
    "ScheduleConflictError",
    "UnknownScheduleError",
    "ScheduleRegistrySealedError",
    "DecisionScheduleRegistry",
    "is_decision_point",
    "compute_scheduled_for",
    "compute_cutoff_at",
    "compute_eligible_execution_at",
    # Task 3 — runtime-only intent envelope + idempotency key families
    "TargetPortfolioIntent",
    "ShadowEnvelopeError",
    "wrap_proposal_as_intent",
    "SHADOW_APPLY_KEY_DOMAIN",
    "target_apply_key",
    "SHADOW_ORDER_ID_DOMAIN",
    "ShadowOrderKind",
    "shadow_order_id",
    "SHADOW_FILL_ID_DOMAIN",
    "shadow_fill_id",
    # Task 4 — shadow run-record contracts + corporate-action event
    "ShadowTargetApplyRecord",
    "ShadowOrderRecord",
    "ShadowFillRecord",
    "ShadowRejectRecord",
    "CorporateActionEvent",
    "SHADOW_METRIC_KEYS",
    "ShadowRunResult",
]


# --------------------------------------------------------------------------- #
# Errors + closed reason-code vocabulary                                       #
# --------------------------------------------------------------------------- #
class ShadowContractError(ValueError):
    """Base for shadow-consumer contract errors (a ``ValueError`` subclass)."""


#: the closed reason-code vocabulary for a rejected proposal. The
#: ``@model_validator`` surfaces the aggregate ones (``duplicate_symbol`` /
#: ``weight_sum_violation`` / ``leverage_or_short``) as
#: :class:`PydanticCustomError` codes; ``non_finite_weight`` / ``negative_weight``
#: are the vocabulary names for the field-bound rejections (NaN/Inf and a short
#: leg), reachable through the stage-前 :class:`ProposalRejected` path a later
#: task adds. Task 1b extends this closed set by EXACTLY one member,
#: ``"non_band_weight"`` — the off-band target-weight rejection surfaced by the
#: proposal validator's step ④ band check.
PROPOSAL_REASON_CODES: frozenset[str] = frozenset(
    {
        "duplicate_symbol",
        "non_finite_weight",
        "negative_weight",
        "weight_sum_violation",
        "leverage_or_short",
        "non_band_weight",
    }
)


class ProposalRejected(ShadowContractError):
    """A proposal rejected *before* staging, carrying a closed ``reason_code``.

    ``reason_code`` must be a member of :data:`PROPOSAL_REASON_CODES`; an
    out-of-vocabulary code is itself a ``ValueError`` so the closed set can never
    be widened by accident. The same codes are what the model validator raises
    via :class:`PydanticCustomError`, so the two rejection paths share one
    vocabulary.
    """

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        if reason_code not in PROPOSAL_REASON_CODES:
            raise ValueError(
                f"unknown proposal reason_code {reason_code!r}; must be one of "
                f"{sorted(PROPOSAL_REASON_CODES)}"
            )
        self.reason_code = reason_code
        super().__init__(message or reason_code)


#: the ONLY tolerance in the weight-sum identity invariant (never a renormalize).
WEIGHT_SUM_TOLERANCE: float = 1e-8

#: the closed target-weight band vocabulary (R2 ruling 2026-07-18): an LLM
#: trader's ``target_weight`` in a proposal/intent must be EXACTLY one of these
#: five values. It is the SINGLE source of truth read by the proposal validator's
#: step ④ (and by ``TargetPortfolioIntent`` in Task 3) — never re-inlined as a
#: literal copy — and Phase 8's ``allowed_actions`` "maximum target-weight band"
#: reuses this exact tuple. Membership is by exact float ``in`` comparison (zero
#: tolerance, never snapped): the five band values are all exactly representable
#: in binary float, so an off-band value simply is unequal to every member.
TARGET_WEIGHT_BANDS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)


# --------------------------------------------------------------------------- #
# TrancheTrigger                                                              #
# --------------------------------------------------------------------------- #
class TrancheTrigger(DigestModel):
    """One optional entry-tranche price interval (分批触发价区间), minimal shape.

    R2 registration: a trader may attach a batched entry trigger — a price band
    and the fraction to deploy in it — to a target leg. This ``@1`` registers only
    the *minimal shape* so Phase 8's matching engine never forces a ``TargetPosition``
    ``@2`` bump: **every numeric field is Optional and defaults to ``None`` with no
    computed default anywhere**, and there are **no cross-field constraints** in
    ``@1`` (e.g. ``price_low <= price_high``, fraction-sum ceilings, and any
    fill/prioritization semantics arrive with a later matching-engine version).

    It is a nested, frozen sub-model that carries **no independent
    ``schema_version``**: it versions and digests *through its host*
    ``TargetPosition`` (the canonical-JSON recursion in
    :func:`~guanlan_v2.orchestration.digest._project` applies this model's own
    semantic projection inside the parent's digest), so a change to any tranche
    field flows into the host position's semantic digest.
    """

    price_low: FiniteFloat | None = None
    price_high: FiniteFloat | None = None
    fraction: FiniteFloat | None = None


# --------------------------------------------------------------------------- #
# TargetPosition                                                              #
# --------------------------------------------------------------------------- #
class TargetPosition(DigestModel):
    """One long-only target-weight leg (+ optional exit parameters).

    ``target_weight`` is REQUIRED (it is the decision substance) and stays
    **continuous** in ``[0, 1]`` at this layer — the closed five-band vocabulary
    (:data:`TARGET_WEIGHT_BANDS`) is imposed ONLY at the proposal/intent layer
    (``PortfolioTargetProposal`` here, ``TargetPortfolioIntent`` in Task 3) and
    **never on ``TargetPosition`` itself**, so the Task 6 deterministic lane
    (``DeterministicTargetSet``) can still express continuous rule-computed
    weights; the band constraint targets the LLM anchor-drift pathology alone.
    Every exit parameter is Optional-None with no computed default (an absent
    stop/take-profit/hold cap is an explicit ``None``, never a fabricated value).

    ``entry_tranches`` (default empty tuple) carries the optional batched entry
    triggers (:class:`TrancheTrigger`); it participates in ``TargetPosition@1``'s
    JSON schema and semantic digest, and the default ``()`` leaves every Task-1
    digest vector unchanged.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    target_weight: FiniteFloat = Field(ge=0, le=1)
    stop_loss_pct: FiniteFloat | None = Field(default=None, gt=0, le=1)
    take_profit_pct: FiniteFloat | None = Field(default=None, gt=0)
    max_hold_bars: PositiveInt | None = None
    entry_tranches: tuple[TrancheTrigger, ...] = ()


# --------------------------------------------------------------------------- #
# Shared long-only portfolio matrix (① duplicate ② sum ③ leverage ④ band)        #
# --------------------------------------------------------------------------- #
def _verify_portfolio_matrix(
    positions: "tuple[TargetPosition, ...]", cash_weight: float
) -> None:
    """The closed, fixed-order long-only v1 book invariant, raising a
    :class:`PydanticCustomError` with the closed :data:`PROPOSAL_REASON_CODES`.

    Extracted so ``PortfolioTargetProposal`` (the LLM-writable payload) and
    ``TargetPortfolioIntent`` (Task 3's runtime envelope) apply the *identical*
    matrix — an intent can never be laxer than a proposal. The step order is
    fixed (① duplicate → ② sum-identity → ③ leverage → ④ closed target-weight
    band): the sequential raise stops at the first breach, so the surfaced code is
    order-defined. :data:`TARGET_WEIGHT_BANDS` / :data:`WEIGHT_SUM_TOLERANCE` are
    read as module globals (late-bound, single source of truth — the band
    monkeypatch contract).
    """
    # ① duplicate symbol by (code, exchange) — the instrument identity a downstream
    #    book keys on; two legs on the same instrument are never a valid book.
    seen: set[tuple[str, str]] = set()
    for p in positions:
        key = (p.symbol.code, p.symbol.exchange)
        if key in seen:
            raise PydanticCustomError(
                "duplicate_symbol",
                "duplicate symbol {code}.{exchange} in positions",
                {"code": p.symbol.code, "exchange": p.symbol.exchange},
            )
        seen.add(key)

    # ② weight-sum identity: sum(target_weight)+cash_weight must equal 1 within
    #    WEIGHT_SUM_TOLERANCE — NEVER renormalized. math.fsum mirrors the house
    #    precedent for summing reported floats (RegimeReport).
    weight_sum = math.fsum(p.target_weight for p in positions)
    if abs(weight_sum + cash_weight - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise PydanticCustomError(
            "weight_sum_violation",
            "sum(target_weight)+cash_weight must equal 1 +/- {tol}, got {total}",
            {"tol": WEIGHT_SUM_TOLERANCE, "total": weight_sum + cash_weight},
        )

    # ③ long-only aggregate leverage guard. With cash_weight >= 0 and ② already
    #    enforcing sum+cash == 1, this is subsumed (any leverage input fails ②
    #    first); it is kept as the explicit named long-only invariant so the
    #    guarantee is legible and survives a future cash model that could admit a
    #    negative cash leg. Shorts are impossible via ge=0.
    if weight_sum > 1.0 + WEIGHT_SUM_TOLERANCE:
        raise PydanticCustomError(
            "leverage_or_short",
            "aggregate target weight {total} exceeds 1 (long-only v1)",
            {"total": weight_sum},
        )

    # ④ closed target-weight band vocabulary — the LLM anchor-drift guard. Each
    #    position's target_weight must be EXACTLY a member of TARGET_WEIGHT_BANDS
    #    (float `in`: zero tolerance, NEVER snapped). Imposed here at the
    #    proposal/intent layer only (DELIBERATELY NOT on TargetPosition itself, so
    #    Task 6's DeterministicTargetSet can express continuous rule-computed
    #    weights). TARGET_WEIGHT_BANDS is read as the single source of truth (no
    #    re-inlined literal), so Phase 8's allowed_actions band ceiling reuses the
    #    identical closed vocabulary.
    for p in positions:
        if p.target_weight not in TARGET_WEIGHT_BANDS:
            raise PydanticCustomError(
                "non_band_weight",
                "target_weight {weight} for {code}.{exchange} is not a member "
                "of the closed band vocabulary {bands}",
                {
                    "weight": p.target_weight,
                    "code": p.symbol.code,
                    "exchange": p.symbol.exchange,
                    "bands": list(TARGET_WEIGHT_BANDS),
                },
            )


# --------------------------------------------------------------------------- #
# PortfolioTargetProposal                                                     #
# --------------------------------------------------------------------------- #
class PortfolioTargetProposal(DigestModel):
    """The whole LLM-writable target book: positions + cash + rationale + confidence.

    An ordered tuple of positions, the cash weight, a non-blank rationale and a
    closed confidence. It carries ZERO envelope fields (invariant 1);
    ``extra="forbid"`` (inherited) rejects any smuggled envelope key. The
    fixed-order model validator (① duplicate → ② sum-identity → ③ leverage →
    ④ closed target-weight band) rejects a bad book at construction, before any
    staging.
    """

    schema_version: Literal["1"] = "1"
    positions: tuple[TargetPosition, ...]
    cash_weight: FiniteFloat = Field(ge=0, le=1)
    rationale: NonEmptyStr
    confidence: Confidence

    @model_validator(mode="after")
    def _verify(self) -> "PortfolioTargetProposal":
        # the closed fixed-order long-only book matrix (① duplicate → ② sum →
        # ③ leverage → ④ band), shared byte-for-byte with TargetPortfolioIntent.
        _verify_portfolio_matrix(self.positions, self.cash_weight)
        return self


# =========================================================================== #
# Task 2 · decision schedule + registry + unique schedule-time computation      #
# =========================================================================== #
# The proposal half (Task 1 above) is the LLM-writable payload; Task 2 owns the
# *when* half: a versioned, content-digested :class:`DecisionSchedule` that pins
# — deterministically, from an immutable trading calendar and a fixed timezone,
# never a wall clock — the three ruled instants of the shadow time model
# ``cutoff_at <= decision_as_of < eligible_execution_at``. Every schedule-time
# function here is a pure function of ``(schedule, session/scheduled instant,
# calendar)``: same inputs → identical UTC instants across calls and processes.

#: strict local wall-time ``HH:MM`` (24-hour, zero-padded). The spec §8 ``time``
#: fields are upgraded to a canonicalizable strict string because ``sha256+cjson-v1``
#: rejects ``datetime.time``.
LocalTimeStr = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
#: strict ISO ``YYYY-MM-DD`` calendar date string (same canonical-JSON rationale).
IsoDateStr = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]

#: the ONLY matching-engine version this phase implements. ``shadow-match-v1``
#: supports ``bar_frequency == "1d"`` only and pins the v1 A-share session anchors
#: below; a schedule may declare another bar frequency (schema-valid for later
#: phases) but :func:`compute_eligible_execution_at` refuses to compute a
#: non-``"1d"`` frequency here. That refusal gates on ``bar_frequency`` ALONE — it
#: does not inspect the declared ``matching_engine_version`` — so this constant
#: names the engine version the phase implements without claiming to enforce it.
SHADOW_MATCHING_ENGINE_VERSION: str = "shadow-match-v1"
#: v1 A-share continuous-auction session anchors (local wall time).
ASHARE_SESSION_OPEN: str = "09:30"
ASHARE_SESSION_CLOSE: str = "15:00"

#: the all-zero placeholder used by :meth:`DecisionSchedule.build` when field
#: pre-projection fails, so the failure surfaces as the model validator's digest
#: mismatch (a valid 64-hex string that can never equal a real content digest).
_SCHEDULE_DIGEST_PLACEHOLDER = "0" * 64

#: the closed pairing between an execution policy and its price field.
_EXECUTION_PRICE_PAIRING: dict[str, str] = {"next_open": "open", "next_bar_close": "close"}


def _hhmm(value: str) -> tuple[int, int]:
    """Split a validated ``HH:MM`` string into an ``(hour, minute)`` tuple."""
    hh, mm = value.split(":")
    return int(hh), int(mm)


def _local_instant_utc(tz_name: str, session_date: str, local_time: str) -> datetime:
    """The UTC instant of ``local_time`` in ``tz_name`` on ``session_date``.

    ``tz_name`` is a schedule-validated resolvable IANA zone, ``session_date`` an
    ISO ``YYYY-MM-DD`` and ``local_time`` a validated ``HH:MM`` — so this never
    fails on a valid schedule; the returned datetime is tz-aware UTC.
    """
    tz = ZoneInfo(tz_name)
    d = date.fromisoformat(session_date)
    hh, mm = _hhmm(local_time)
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz).astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #
class ScheduleComputationError(ShadowContractError):
    """A schedule-time computation cannot proceed (non-decision point, non-session
    date, calendar-id mismatch, or no session after a given instant)."""


class UnsupportedBarFrequencyError(ScheduleComputationError):
    """``compute_eligible_execution_at`` was asked for a ``bar_frequency`` that
    ``shadow-match-v1`` does not implement (anything other than ``"1d"``)."""


class ScheduleConflictError(ShadowContractError):
    """A different schedule (different content digest) was registered under an
    already-used ``(id, version)`` key."""


class UnknownScheduleError(ShadowContractError):
    """A :class:`ContentRef` resolves to no registered schedule, or its digest does
    not match the schedule registered under its ``(id, version)`` (a stale ref)."""


class ScheduleRegistrySealedError(ShadowContractError):
    """A registration was attempted on a sealed :class:`DecisionScheduleRegistry`."""


# --------------------------------------------------------------------------- #
# DecisionSchedule                                                             #
# --------------------------------------------------------------------------- #
class DecisionSchedule(DigestModel):
    """A versioned, content-sealed cadence + matching contract for one shadow lane.

    It freezes *when* a shadow decision is taken and *how* its resulting intent is
    matched, as one reviewable, content-digested value: the trading ``calendar_id``
    and ``timezone`` (validator-enforced resolvable via :class:`ZoneInfo`), the
    ``kind`` cadence (``daily`` / ``weekly`` / ``rebalance_dates`` / ``manual``) with
    its paired ``weekdays`` / ``rebalance_dates`` selector, the local ``cutoff`` and
    ``decision`` wall times (``cutoff <= decision``), the ``bar_frequency`` and the
    ``execution_policy`` / ``execution_price_field`` pair (``next_open`` ⇔ ``open``,
    ``next_bar_close`` ⇔ ``close``), the ``matching_engine_version`` and the
    ``intrabar_exit_priority``.

    Every non-self-digest field is semantic (spec §8 line 936): a change to the
    timezone, cutoff, calendar, bar frequency, execution policy, price field,
    matching-engine version or intrabar priority moves ``content_digest``. The
    record self-seals like every Phase 4/5 precedent — build it via
    :meth:`build`, which computes the canonical digest and attaches it, and the
    ``@model_validator`` re-verifies it on every load. Pairing and kind-matrix
    violations fail at construction, before any staging.
    """

    schema_version: Literal["1"] = "1"
    id: LogicalId
    version: NonEmptyStr
    calendar_id: NonEmptyStr
    timezone: NonEmptyStr
    kind: Literal["daily", "weekly", "rebalance_dates", "manual"]
    decision_local_time: LocalTimeStr
    cutoff_local_time: LocalTimeStr
    bar_frequency: Literal["1d", "60m", "30m", "15m", "5m", "1m"]
    execution_policy: Literal["next_open", "next_bar_close"]
    execution_price_field: Literal["open", "close"]
    matching_engine_version: NonEmptyStr
    weekdays: tuple[Annotated[int, Field(ge=1, le=7)], ...] = ()
    rebalance_dates: tuple[IsoDateStr, ...] = ()
    intrabar_exit_priority: Literal[
        "worst_case", "stop_first", "take_profit_first"
    ] = "worst_case"
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @field_validator("timezone")
    @classmethod
    def _timezone_resolvable(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"timezone {v!r} is not a resolvable IANA zone: {exc}"
            ) from exc
        return v

    @model_validator(mode="after")
    def _verify(self) -> "DecisionSchedule":
        # cutoff must precede or equal the decision instant (the ruled time model)
        if _hhmm(self.cutoff_local_time) > _hhmm(self.decision_local_time):
            raise ValueError(
                "cutoff_local_time must be <= decision_local_time "
                f"(got {self.cutoff_local_time!r} > {self.decision_local_time!r})"
            )
        # execution_policy <-> execution_price_field pairing
        expected_price = _EXECUTION_PRICE_PAIRING[self.execution_policy]
        if self.execution_price_field != expected_price:
            raise ValueError(
                f"execution_policy {self.execution_policy!r} must pair with "
                f"execution_price_field {expected_price!r}, got "
                f"{self.execution_price_field!r}"
            )
        # weekday selector: unique + sorted
        wk = list(self.weekdays)
        if wk != sorted(wk):
            raise ValueError("weekdays must be sorted ascending")
        if len(set(wk)) != len(wk):
            raise ValueError("weekdays must be unique")
        # rebalance-date selector: unique + sorted
        rb = list(self.rebalance_dates)
        if rb != sorted(rb):
            raise ValueError("rebalance_dates must be sorted ascending")
        if len(set(rb)) != len(rb):
            raise ValueError("rebalance_dates must be unique")
        # kind matrix — the selector present must match the cadence
        has_wk, has_rb = bool(self.weekdays), bool(self.rebalance_dates)
        if self.kind == "weekly":
            if not has_wk or has_rb:
                raise ValueError(
                    "kind 'weekly' requires non-empty weekdays and empty rebalance_dates"
                )
        elif self.kind == "rebalance_dates":
            if not has_rb or has_wk:
                raise ValueError(
                    "kind 'rebalance_dates' requires non-empty rebalance_dates and "
                    "empty weekdays"
                )
        else:  # daily / manual — a fixed or externally-supplied point, no selector
            if has_wk or has_rb:
                raise ValueError(
                    f"kind {self.kind!r} requires empty weekdays and rebalance_dates"
                )
        # self-seal: the declared digest must match the canonical semantic digest
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "DecisionSchedule":
        """Seal + construct: compute the canonical content digest, then validate."""
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _SCHEDULE_DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)


# --------------------------------------------------------------------------- #
# Pure schedule-time functions                                                 #
# --------------------------------------------------------------------------- #
# Calendar-surface resolution (Task-0 correction N6): the reviewed Phase-3
# ``TradingCalendar`` Protocol carries only ``calendar_id`` / ``material_ref`` /
# ``coverage`` — the session predicates ``is_session`` / ``sessions_between`` live
# on the concrete :class:`ImmutableTradingCalendar` (built by
# ``build_trading_calendar``). These functions therefore type ``calendar`` as the
# concrete ``ImmutableTradingCalendar`` and consume ``is_session`` + ``coverage``;
# tests drive a REAL calendar built via ``build_trading_calendar``. This binds
# Tasks 3/6: they too pass ``ImmutableTradingCalendar`` instances.
def _first_session_strictly_after(calendar: ImmutableTradingCalendar, day: date) -> date:
    """The first calendar session strictly after ``day`` within coverage."""
    coverage = calendar.coverage
    if coverage is None:
        raise ScheduleComputationError("calendar has no sessions")
    _, last = coverage
    probe = day + timedelta(days=1)
    while probe <= last:
        if calendar.is_session(probe):
            return probe
        probe += timedelta(days=1)
    raise ScheduleComputationError(
        f"no calendar session strictly after {day.isoformat()} within coverage "
        f"(last session {last.isoformat()})"
    )


def _require_matching_calendar(
    schedule: DecisionSchedule, calendar: ImmutableTradingCalendar
) -> None:
    if calendar.calendar_id != schedule.calendar_id:
        raise ScheduleComputationError(
            f"calendar {calendar.calendar_id!r} does not match schedule calendar_id "
            f"{schedule.calendar_id!r}"
        )


def is_decision_point(
    schedule: DecisionSchedule,
    *,
    session_date: IsoDateStr,
    calendar: ImmutableTradingCalendar,
) -> bool:
    """Whether ``session_date`` is a decision point for ``schedule``.

    A calendar-id mismatch raises; a non-session date is never a decision point;
    then ``daily``/``manual`` fire on every session, ``weekly`` on an ISO weekday
    in ``weekdays``, and ``rebalance_dates`` on membership.
    """
    _require_matching_calendar(schedule, calendar)
    day = date.fromisoformat(session_date)
    if not calendar.is_session(day):
        return False
    if schedule.kind in ("daily", "manual"):
        return True
    if schedule.kind == "weekly":
        return day.isoweekday() in schedule.weekdays
    return day.isoformat() in schedule.rebalance_dates


def compute_scheduled_for(
    schedule: DecisionSchedule,
    *,
    session_date: IsoDateStr,
    calendar: ImmutableTradingCalendar,
) -> UtcDateTime:
    """The UTC instant of the decision on ``session_date`` (requires a decision point)."""
    if not is_decision_point(schedule, session_date=session_date, calendar=calendar):
        raise ScheduleComputationError(
            f"{session_date} is not a decision point for schedule "
            f"{schedule.id}@{schedule.version}"
        )
    return _local_instant_utc(
        schedule.timezone, session_date, schedule.decision_local_time
    )


def compute_cutoff_at(
    schedule: DecisionSchedule, *, session_date: IsoDateStr
) -> UtcDateTime:
    """The UTC instant of the upstream data/entry freeze (``cutoff_local_time``)."""
    return _local_instant_utc(
        schedule.timezone, session_date, schedule.cutoff_local_time
    )


def compute_eligible_execution_at(
    schedule: DecisionSchedule,
    *,
    scheduled_for: UtcDateTime,
    calendar: ImmutableTradingCalendar,
) -> UtcDateTime:
    """The UTC instant at which the decision's intent first becomes executable.

    Refuses (``UnsupportedBarFrequencyError``) unless ``bar_frequency == "1d"`` under
    ``shadow-match-v1``. The execution session is the first calendar session
    strictly after the *local* date of ``scheduled_for`` (derived in the schedule's
    timezone, never UTC); ``next_open`` anchors at :data:`ASHARE_SESSION_OPEN`,
    ``next_bar_close`` at :data:`ASHARE_SESSION_CLOSE`, both converted to UTC —
    hence ``eligible_execution_at > scheduled_for`` always.
    """
    if schedule.bar_frequency != "1d":
        raise UnsupportedBarFrequencyError(
            f"{SHADOW_MATCHING_ENGINE_VERSION} supports bar_frequency '1d' only; "
            f"schedule {schedule.id}@{schedule.version} declares "
            f"{schedule.bar_frequency!r}"
        )
    _require_matching_calendar(schedule, calendar)
    if scheduled_for.tzinfo is None or scheduled_for.tzinfo.utcoffset(scheduled_for) is None:
        raise ScheduleComputationError("scheduled_for must be a tz-aware datetime")
    local_date = scheduled_for.astimezone(ZoneInfo(schedule.timezone)).date()
    exec_session = _first_session_strictly_after(calendar, local_date)
    anchor = (
        ASHARE_SESSION_OPEN
        if schedule.execution_policy == "next_open"
        else ASHARE_SESSION_CLOSE
    )
    return _local_instant_utc(schedule.timezone, exec_session.isoformat(), anchor)


# --------------------------------------------------------------------------- #
# DecisionScheduleRegistry                                                     #
# --------------------------------------------------------------------------- #
class DecisionScheduleRegistry:
    """Sealable registry of :class:`DecisionSchedule` values keyed by ``(id, version)``.

    Mirrors the Phase-1 ``SchemaRegistry`` service shape: built empty, populated by
    :meth:`register` (idempotent for an identical content digest, a
    :class:`ScheduleConflictError` for the same key under a different digest,
    refused once sealed), then optionally :meth:`seal`-ed so its
    :attr:`registry_digest` is frozen. :meth:`resolve` verifies the FULL
    ``id``/``version``/``content_digest`` triple carried by a :class:`ContentRef`,
    so a ref with the right key but a stale digest is refused (no schedule can be
    silently swapped under an intent). :meth:`manifest` is sorted by
    ``(id, version)`` so :attr:`registry_digest` is registration-order independent.
    """

    __slots__ = ("_by_key", "_sealed")

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], DecisionSchedule] = {}
        self._sealed: bool = False

    @property
    def sealed(self) -> bool:
        return self._sealed

    def register(self, schedule: DecisionSchedule) -> ContentRef:
        """Register ``schedule`` under ``(id, version)`` and return its content ref."""
        if self._sealed:
            raise ScheduleRegistrySealedError(
                "cannot register into a sealed schedule registry"
            )
        ref = ContentRef(
            id=schedule.id,
            version=schedule.version,
            content_digest=schedule.content_digest,
        )
        key = (schedule.id, schedule.version)
        existing = self._by_key.get(key)
        if existing is not None:
            if existing.content_digest == schedule.content_digest:
                return ref  # idempotent for identical content
            raise ScheduleConflictError(
                f"schedule {schedule.id}@{schedule.version} is already registered with "
                "a different content digest"
            )
        self._by_key[key] = schedule
        return ref

    def seal(self) -> None:
        """Freeze the registry: subsequent :meth:`register` calls fail; reads work."""
        self._sealed = True

    def resolve(self, ref: ContentRef) -> DecisionSchedule:
        """Return the schedule ``ref`` names, verifying the full id/version/digest triple."""
        schedule = self._by_key.get((ref.id, ref.version))
        if schedule is None:
            raise UnknownScheduleError(
                f"no schedule registered for {ref.id}@{ref.version}"
            )
        if (
            ref.id != schedule.id
            or ref.version != schedule.version
            or ref.content_digest != schedule.content_digest
        ):
            raise UnknownScheduleError(
                f"content ref for {ref.id}@{ref.version} does not match the registered "
                "schedule (stale or wrong digest)"
            )
        return schedule

    def manifest(self) -> tuple[ContentRef, ...]:
        """Content refs of every registered schedule, sorted by ``(id, version)``."""
        refs = [
            ContentRef(id=s.id, version=s.version, content_digest=s.content_digest)
            for s in self._by_key.values()
        ]
        refs.sort(key=lambda r: (r.id, r.version))
        return tuple(refs)

    @property
    def registry_digest(self) -> DigestHex:
        """Registration-order-independent digest over the sorted manifest."""
        return content_digest(list(self.manifest()))


# =========================================================================== #
# Task 3 · runtime-only TargetPortfolioIntent envelope + idempotency key families #
# =========================================================================== #
# The proposal (Task 1) is the LLM-writable *what*; the schedule (Task 2) is the
# deterministic *when*. Task 3 binds them into a runtime-only, content-digested
# staging envelope — the :class:`TargetPortfolioIntent` — that no LLM can author
# and no consumer can mutate. The envelope carries the closed single-value
# ``origin``/``authority``/``execution_scope`` Literals so "advisory, shadow-only,
# LLM-originated" is structural, not advisory; its cadence instants are computed
# inside the SOLE constructor :func:`wrap_proposal_as_intent` (never accepted from
# a caller); and its idempotency identity is minted by three pure, domain-tagged
# key families so an apply/order/fill is applied at most once and can never
# collide across families (distinct ``domain`` tags).


class ShadowEnvelopeError(ShadowContractError):
    """Staging a proposal into a shadow intent envelope was refused.

    Raised by :func:`wrap_proposal_as_intent` on each closed refusal path — a
    payload that is not a ``PortfolioTargetProposal@1``, a request that binds no
    (or an unresolvable / digest-stale) decision schedule, a non-decision-point
    ``session_date``, or a ``decision_as_of`` earlier than the schedule cutoff —
    each with distinct reason text and producing NO intent object.
    """


class TargetPortfolioIntent(DigestModel):
    """A runtime-only, content-sealed target-portfolio staging envelope.

    The framework's own binding of an LLM :class:`PortfolioTargetProposal` to a
    registered :class:`DecisionSchedule` point: the proposal's book (positions /
    cash / rationale / confidence, copied **verbatim** including each position's
    ``entry_tranches``) plus the immutable *who / when / from-what* facts an
    honest shadow consumer replays against — the proposal artifact identity, the
    resolved schedule triple, and the three ruled instants
    ``cutoff <= decision_as_of < eligible_execution_at`` (only the latter two are
    stored; the cutoff is enforced at staging).

    Structural red lines (invariant 1): ``origin`` / ``authority`` /
    ``execution_scope`` are closed single-value Literals — an intent can never
    self-report a live/human envelope. :func:`wrap_proposal_as_intent` is the sole
    path that DERIVES the cadence instants (``scheduled_for`` /
    ``eligible_execution_at``) from a registered schedule + calendar rather than
    trusting a caller — the raw constructor still exists (a runtime helper may build
    one directly), so the *structural* guarantees are the closed Literals plus the
    time-order validators below, not an absence of a public constructor. It
    re-applies the SAME closed portfolio matrix as the proposal
    (:func:`_verify_portfolio_matrix`, band check included), so the envelope layer
    can never become a bypass around the band/tranche constraints.

    Digest identity: every business field — the schedule triple and all three
    times included — is semantic; only the runtime-random ``intent_id`` and the
    ``created_at`` wall clock are audit facts (:data:`SEMANTIC_EXCLUDE`), so two
    intents differing only in those two carry an identical ``semantic_digest`` yet
    distinct operational apply keys. The record is frozen (invariant 3): seeing
    realized results can never mutate an intent in place.
    """

    schema_version: Literal["1"] = "1"
    intent_id: NonEmptyStr
    target_version: PositiveInt
    proposal_artifact_id: NonEmptyStr
    proposal_digest: DigestHex
    source_decision_artifact_id: NonEmptyStr
    decision_schedule_id: LogicalId
    decision_schedule_version: NonEmptyStr
    decision_schedule_digest: DigestHex
    scheduled_for: UtcDateTime
    decision_as_of: UtcDateTime
    eligible_execution_at: UtcDateTime
    valid_until: UtcDateTime | None = None
    positions: tuple[TargetPosition, ...]
    cash_weight: FiniteFloat = Field(ge=0, le=1)
    origin: Literal["LLM"] = "LLM"
    authority: Literal["ADVISORY_ONLY"] = "ADVISORY_ONLY"
    execution_scope: Literal["SHADOW_ONLY"] = "SHADOW_ONLY"
    rationale: NonEmptyStr
    confidence: Confidence
    created_at: UtcDateTime

    #: the runtime-random id and the wall clock are audit facts (spec §8 line 931);
    #: every business field — including the schedule triple and all three times —
    #: is semantic.
    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"intent_id", "created_at"})

    @model_validator(mode="after")
    def _verify(self) -> "TargetPortfolioIntent":
        # the SAME closed long-only book matrix a proposal must pass (an intent can
        # never be laxer than a proposal — band check included).
        _verify_portfolio_matrix(self.positions, self.cash_weight)
        # the ruled time model's stored half: cutoff <= decision_as_of is enforced
        # at staging (the cutoff instant is not carried here); the envelope pins the
        # remaining orderings.
        if not (self.decision_as_of < self.eligible_execution_at):
            raise ValueError(
                "decision_as_of must be strictly before eligible_execution_at "
                f"(got {self.decision_as_of.isoformat()} >= "
                f"{self.eligible_execution_at.isoformat()})"
            )
        if not (self.scheduled_for <= self.eligible_execution_at):
            raise ValueError(
                "scheduled_for must be <= eligible_execution_at "
                f"(got {self.scheduled_for.isoformat()} > "
                f"{self.eligible_execution_at.isoformat()})"
            )
        if self.valid_until is not None and not (
            self.eligible_execution_at <= self.valid_until
        ):
            raise ValueError(
                "valid_until must be >= eligible_execution_at "
                f"(got {self.valid_until.isoformat()} < "
                f"{self.eligible_execution_at.isoformat()})"
            )
        return self


#: the canonical ``name@version`` key a proposal-bearing artifact must declare —
#: derived from the proposal contract itself so it can never drift from the model.
_PROPOSAL_SCHEMA_KEY: str = (
    f"{PortfolioTargetProposal.__name__}@"
    f"{PortfolioTargetProposal.model_fields['schema_version'].default}"
)


def wrap_proposal_as_intent(
    *,
    proposal_artifact: "Artifact",
    source_decision_artifact_id: NonEmptyStr,
    request: "OrchestrationRequest",
    schedule_registry: DecisionScheduleRegistry,
    calendar: ImmutableTradingCalendar,
    session_date: IsoDateStr,
    decision_as_of: UtcDateTime,
    target_version: PositiveInt,
    intent_id: NonEmptyStr,
    clock: "AuthoritativeClock",
    valid_until: UtcDateTime | None = None,
) -> TargetPortfolioIntent:
    """Stage a sealed :class:`PortfolioTargetProposal` artifact into a runtime-only
    :class:`TargetPortfolioIntent` — the **sole** intent constructor path.

    Closed order (each step a :class:`ShadowEnvelopeError` refusal with distinct
    reason text, producing no intent object):

    ① the artifact's ``payload_schema_ref`` must be ``PortfolioTargetProposal@1``
       and its payload must re-validate as a :class:`PortfolioTargetProposal`;
    ② ``request.decision_schedule_ref`` must be present and
       :meth:`DecisionScheduleRegistry.resolve` it (the full id/version/digest
       triple — a stale digest is refused, so no schedule is silently swapped);
    ③ ``scheduled_for`` / ``eligible_execution_at`` are COMPUTED from the resolved
       schedule + calendar (never caller-supplied); ``session_date`` must be a
       decision point;
    ④ the ruled ``cutoff <= decision_as_of`` is enforced (the cutoff is not stored
       on the envelope; Phase 9 replay consumes the stored ordering);
    ⑤ the envelope is assembled entirely from these runtime arguments — the
       proposal's book (positions / cash / rationale / confidence) copied
       **verbatim** (``entry_tranches`` included), the proposal artifact identity,
       the resolved schedule triple, and ``created_at`` read from ``clock``.
    """
    # ⓪ entry guard: the shadow time model is defined only in UTC, so a naive
    #    decision_as_of must fail here as a loud, localized refusal (never later as a
    #    cryptic aware-vs-naive comparison TypeError inside the cutoff ordering).
    if (
        not isinstance(decision_as_of, datetime)
        or decision_as_of.tzinfo is None
        or decision_as_of.tzinfo.utcoffset(decision_as_of) is None
    ):
        raise ShadowEnvelopeError(
            "decision_as_of must be a tz-aware datetime; a naive datetime is rejected "
            "at the wrap entry (the shadow time model is defined only in UTC)"
        )
    decision_as_of = decision_as_of.astimezone(timezone.utc)

    # ① payload contract: exact schema key + a genuine re-validation of the payload.
    schema_key = proposal_artifact.payload_schema_ref.key
    if schema_key != _PROPOSAL_SCHEMA_KEY:
        raise ShadowEnvelopeError(
            f"proposal_artifact.payload_schema_ref {schema_key!r} is not the "
            f"required {_PROPOSAL_SCHEMA_KEY!r}"
        )
    try:
        proposal = PortfolioTargetProposal.model_validate(proposal_artifact.payload)
    except ValidationError as exc:
        raise ShadowEnvelopeError(
            f"proposal_artifact payload does not re-validate as "
            f"{_PROPOSAL_SCHEMA_KEY}: {exc}"
        ) from exc

    # ② the request must bind a registered schedule (triple-verified resolve).
    ref = request.decision_schedule_ref
    if ref is None:
        raise ShadowEnvelopeError(
            "any request producing a shadow intent must bind a registered decision "
            "schedule (request.decision_schedule_ref is absent)"
        )
    try:
        schedule = schedule_registry.resolve(ref)
    except (UnknownScheduleError, ScheduleConflictError) as exc:
        raise ShadowEnvelopeError(
            f"request.decision_schedule_ref does not resolve to a registered "
            f"schedule: {exc}"
        ) from exc

    # ③ compute the cadence instants (never caller-supplied); a non-decision-point
    #    session_date is a distinct envelope refusal.
    if not is_decision_point(schedule, session_date=session_date, calendar=calendar):
        raise ShadowEnvelopeError(
            f"session_date {session_date} is not a decision point for schedule "
            f"{schedule.id}@{schedule.version}"
        )
    scheduled_for = compute_scheduled_for(
        schedule, session_date=session_date, calendar=calendar
    )
    eligible_execution_at = compute_eligible_execution_at(
        schedule, scheduled_for=scheduled_for, calendar=calendar
    )

    # ④ the ruled ordering cutoff <= decision_as_of (cutoff = upstream data/entry
    #    freeze; decision_as_of = the decision instant).
    cutoff_at = compute_cutoff_at(schedule, session_date=session_date)
    if not (cutoff_at <= decision_as_of):
        raise ShadowEnvelopeError(
            f"decision_as_of {decision_as_of.isoformat()} is before the schedule "
            f"cutoff {cutoff_at.isoformat()} (the ruled ordering is "
            "cutoff <= decision_as_of)"
        )

    # ⑤ assemble entirely from runtime arguments; the book is copied verbatim.
    return TargetPortfolioIntent(
        intent_id=intent_id,
        target_version=target_version,
        proposal_artifact_id=proposal_artifact.artifact_id,
        proposal_digest=proposal_artifact.content_digest,
        source_decision_artifact_id=source_decision_artifact_id,
        decision_schedule_id=schedule.id,
        decision_schedule_version=schedule.version,
        decision_schedule_digest=schedule.content_digest,
        scheduled_for=scheduled_for,
        decision_as_of=decision_as_of,
        eligible_execution_at=eligible_execution_at,
        valid_until=valid_until,
        positions=proposal.positions,
        cash_weight=proposal.cash_weight,
        rationale=proposal.rationale,
        confidence=proposal.confidence,
        created_at=clock_now(clock),
    )


# --------------------------------------------------------------------------- #
# Idempotency key families (pure; domain-tagged content digests)               #
# --------------------------------------------------------------------------- #
# Each family digests a domain-tagged mapping via Phase 1 :func:`content_digest`.
# Datetimes are passed as their aware-UTC values and rendered by the house
# canonicalizer as the ``sha256+cjson-v1`` canonical UTC string
# (``YYYY-MM-DDTHH:MM:SS.ffffffZ``) — no format is invented here. The distinct
# ``domain`` tag per family makes a cross-family key collision structurally
# impossible (an apply key can never equal an order id can never equal a fill id).

#: apply-key family domain tag: a target portfolio is applied at most once per
#: ``(intent_id, scheduled_for, target_version)``.
SHADOW_APPLY_KEY_DOMAIN: str = "shadow-apply-key-v1"


def target_apply_key(intent: TargetPortfolioIntent) -> DigestHex:
    """The idempotent apply key of ``intent`` — over exactly
    ``{domain, intent_id, scheduled_for, target_version}``.

    Apply identity is *operational*: keyed on the runtime ``intent_id`` (not the
    semantic content), so two intents with identical books but distinct ids apply
    separately, while re-deriving the key from the same intent is deterministic
    and survives a JSON round-trip.
    """
    return content_digest(
        {
            "domain": SHADOW_APPLY_KEY_DOMAIN,
            "intent_id": intent.intent_id,
            "scheduled_for": intent.scheduled_for,
            "target_version": intent.target_version,
        }
    )


#: the closed order-kind vocabulary of the shadow order-id family.
ShadowOrderKind = Literal[
    "target_buy", "target_sell", "stop_loss", "take_profit", "max_hold_exit"
]

#: order-id family domain tag.
SHADOW_ORDER_ID_DOMAIN: str = "shadow-order-id-v1"


def shadow_order_id(
    *,
    apply_key: DigestHex,
    symbol: Symbol,
    order_kind: ShadowOrderKind,
    trigger_bar: NonEmptyStr,
    ordinal: NonNegativeInt,
) -> DigestHex:
    """The idempotent order id — over exactly ``{domain, target_apply_key,
    symbol (Symbol.dotted), order_kind, trigger_bar, ordinal}``.

    Derives from the owning ``target_apply_key`` so every order retains its
    causation up to the applied target; ``symbol`` enters as its ``dotted`` string
    form.
    """
    return content_digest(
        {
            "domain": SHADOW_ORDER_ID_DOMAIN,
            "target_apply_key": apply_key,
            "symbol": symbol.dotted,
            "order_kind": order_kind,
            "trigger_bar": trigger_bar,
            "ordinal": ordinal,
        }
    )


#: fill-id family domain tag.
SHADOW_FILL_ID_DOMAIN: str = "shadow-fill-id-v1"


def shadow_fill_id(*, order_id: DigestHex, fill_seq: PositiveInt) -> DigestHex:
    """The idempotent fill id — over exactly ``{domain, order_id, fill_seq}``.

    Derives from the owning ``order_id`` so every fill retains its causation up to
    the order that produced it.
    """
    return content_digest(
        {
            "domain": SHADOW_FILL_ID_DOMAIN,
            "order_id": order_id,
            "fill_seq": fill_seq,
        }
    )


# =========================================================================== #
# Task 4 · shadow run-record contracts + corporate-action event                 #
# =========================================================================== #
# Task 3 minted the runtime intent envelope and the three domain-tagged
# idempotency key families; Task 4 owns the *consumer* half — the immutable record
# vocabulary an honest shadow replay emits: what the framework APPLIED
# (:class:`ShadowTargetApplyRecord`), the ORDERS it staged
# (:class:`ShadowOrderRecord`), the FILLS + REJECTS the matching engine returned
# (:class:`ShadowFillRecord` / :class:`ShadowRejectRecord`), the PIT corporate
# actions it must honor (:class:`CorporateActionEvent`), and the whole
# content-sealed run (:class:`ShadowRunResult`). Every id-bearing record RECOMPUTES
# its key from its own components via the exact Task-3 builders, so a forged
# apply/order/fill id is unconstructible; the run result closes causation over its
# own record set and references staged intents ONLY by ``intent_content_digests``
# (never an embedded, mutable intent). These records are NOT registered by any
# Phase 1–5 schema registry; their reviewed Phase-6 registration lands in a later
# task (``shadow`` is already scoped into ``PHASE6_MODULES``).


class ShadowTargetApplyRecord(DigestModel):
    """One application of a staged target portfolio at an execution bar.

    Records that an intent's target book became effective (or honestly did NOT —
    ``applied is False`` when the eligible bar was never tradable inside the run
    window, which is a truthful non-application, not an error). ``target_apply_key``
    is self-consistent: it must re-derive from ``(intent_id, scheduled_for,
    target_version)`` through the exact :func:`target_apply_key` builder, so a
    forged key fails construction. The raw ``intent_id`` is audit-only causation
    (:data:`SEMANTIC_EXCLUDE`) — its effect already flows into the semantic digest
    through the operational ``target_apply_key`` — while ``intent_content_digest``
    carries the *semantic* identity of the applied intent so a replay can bind the
    record back to the exact target book it applied.
    """

    schema_version: Literal["1"] = "1"
    target_apply_key: DigestHex
    intent_content_digest: DigestHex
    intent_id: NonEmptyStr
    scheduled_for: UtcDateTime
    target_version: PositiveInt
    trigger_bar: NonEmptyStr
    order_ids: tuple[DigestHex, ...]
    applied: bool

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"intent_id"})

    @model_validator(mode="after")
    def _verify_apply_key(self) -> "ShadowTargetApplyRecord":
        recomputed = target_apply_key(
            SimpleNamespace(
                intent_id=self.intent_id,
                scheduled_for=self.scheduled_for,
                target_version=self.target_version,
            )
        )
        if self.target_apply_key != recomputed:
            raise ValueError(
                "target_apply_key is not self-consistent with (intent_id, "
                "scheduled_for, target_version) — a forged apply key is rejected"
            )
        return self


class ShadowOrderRecord(DigestModel):
    """One staged order emitted for an applied target, self-consistent by ``order_id``.

    ``order_id`` must re-derive from ``(target_apply_key, symbol, order_kind,
    trigger_bar, ordinal)`` through the exact :func:`shadow_order_id` builder, and
    ``target_apply_key`` retains the causation up to the applied target. ``qty`` is
    tri-state, mirroring engine ``Order.qty``: an explicit ``None`` means a
    cash-budget-sized buy (``cash_budget`` supplies the amount), a positive int
    means an exact share quantity. ``limit_price`` / ``cash_budget`` are likewise
    explicit ``None`` when they do not apply — never a fabricated zero. These
    sizing/price fields are deliberately NOT part of the id (only the five
    causation components are), so two orders that differ only in price still carry
    distinct ordinals to earn distinct ids.
    """

    schema_version: Literal["1"] = "1"
    order_id: DigestHex
    target_apply_key: DigestHex
    symbol: Symbol
    order_kind: ShadowOrderKind
    trigger_bar: NonEmptyStr
    ordinal: NonNegativeInt
    side: Literal["buy", "sell"]
    otype: Literal["limit", "market", "stop"]
    limit_price: FiniteFloat | None
    qty: PositiveInt | None
    cash_budget: FiniteFloat | None

    @model_validator(mode="after")
    def _verify_order_id(self) -> "ShadowOrderRecord":
        recomputed = shadow_order_id(
            apply_key=self.target_apply_key,
            symbol=self.symbol,
            order_kind=self.order_kind,
            trigger_bar=self.trigger_bar,
            ordinal=self.ordinal,
        )
        if self.order_id != recomputed:
            raise ValueError(
                "order_id is not self-consistent with (target_apply_key, symbol, "
                "order_kind, trigger_bar, ordinal) — a forged order id is rejected"
            )
        return self


class ShadowFillRecord(DigestModel):
    """One realized fill returned by the matching engine, self-consistent by ``fill_id``.

    ``fill_id`` must re-derive from ``(order_id, fill_seq)`` through the exact
    :func:`shadow_fill_id` builder, and ``order_id`` retains the causation up to the
    order that produced it. ``gross`` is the notional (price × qty) and ``cost`` the
    modeled transaction cost, both reported floats; ``reason`` is the verbatim
    engine fill reason (e.g. ``"target_buy"`` / ``"stop_loss"``).
    """

    schema_version: Literal["1"] = "1"
    fill_id: DigestHex
    order_id: DigestHex
    fill_seq: PositiveInt
    symbol: Symbol
    side: Literal["buy", "sell"]
    qty: PositiveInt
    price: FiniteFloat
    trade_date: NonEmptyStr
    gross: FiniteFloat
    cost: FiniteFloat
    reason: NonEmptyStr

    @model_validator(mode="after")
    def _verify_fill_id(self) -> "ShadowFillRecord":
        recomputed = shadow_fill_id(order_id=self.order_id, fill_seq=self.fill_seq)
        if self.fill_id != recomputed:
            raise ValueError(
                "fill_id is not self-consistent with (order_id, fill_seq) — a forged "
                "fill id is rejected"
            )
        return self


class ShadowRejectRecord(DigestModel):
    """One order the matching engine refused to fill, with the verbatim reason.

    ``reason`` is the engine's ``Broker.last_reason`` verbatim (``"suspended"``,
    ``"one_word_limit_up"``, ``"below_one_lot"``, ``"t1_locked_or_empty"``, …). A
    reject carries no id of its own — it names the ``order_id`` it refused so a
    replay can bind it back to the staged order.
    """

    schema_version: Literal["1"] = "1"
    order_id: DigestHex
    symbol: Symbol
    trade_date: NonEmptyStr
    reason: NonEmptyStr


class CorporateActionEvent(DigestModel):
    """One PIT corporate action the shadow replay must honor at ``ex_date``.

    The closed ``kind`` × amount matrix is validator-enforced (never a free-form
    combination):

    * ``cash_dividend`` — ``cash_per_share > 0`` AND ``shares_ratio == 0``;
    * ``stock_bonus``   — ``shares_ratio > 0`` (additional shares per held share)
      AND ``cash_per_share == 0``;
    * ``split``         — ``shares_ratio > 0`` and ``!= 1`` (new shares per old
      share) AND ``cash_per_share == 0``.

    ``available_at`` is a structurally required PIT fact (the instant the action
    became knowable) — there is no silent missing-availability, so a replay can
    never consume an action before it was observable.
    """

    schema_version: Literal["1"] = "1"
    symbol: Symbol
    kind: Literal["cash_dividend", "stock_bonus", "split"]
    ex_date: IsoDateStr
    cash_per_share: FiniteFloat = Field(ge=0)
    shares_ratio: FiniteFloat = Field(ge=0)
    available_at: UtcDateTime

    @model_validator(mode="after")
    def _verify_matrix(self) -> "CorporateActionEvent":
        if self.kind == "cash_dividend":
            if not (self.cash_per_share > 0):
                raise ValueError("cash_dividend requires cash_per_share > 0")
            if self.shares_ratio != 0:
                raise ValueError("cash_dividend requires shares_ratio == 0")
        elif self.kind == "stock_bonus":
            if not (self.shares_ratio > 0):
                raise ValueError("stock_bonus requires shares_ratio > 0")
            if self.cash_per_share != 0:
                raise ValueError("stock_bonus requires cash_per_share == 0")
        else:  # split
            if not (self.shares_ratio > 0):
                raise ValueError("split requires shares_ratio > 0")
            if self.shares_ratio == 1:
                raise ValueError("split requires shares_ratio != 1")
            if self.cash_per_share != 0:
                raise ValueError("split requires cash_per_share == 0")
        return self


#: the closed metrics-key vocabulary a :class:`ShadowRunResult` may carry. A key
#: outside this set is a hard validation error; a non-finite *value* is OMITTED by
#: :meth:`ShadowRunResult.build` (never smuggled as a sentinel), and a non-finite
#: value can never be smuggled past the ``FiniteFloat`` field on a direct construct.
SHADOW_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "ann_return",
        "sharpe",
        "max_drawdown",
        "volatility",
        "turnover",
        "win_rate",
        "calmar",
        "trade_win_rate",
        "profit_factor",
    }
)

#: the all-zero placeholder used by :meth:`ShadowRunResult.build` when field
#: pre-projection fails, so the failure surfaces as the model validator's digest
#: mismatch rather than as a raw exception from the digest computation.
_RUN_RESULT_DIGEST_PLACEHOLDER = "0" * 64


def _drop_non_finite_metrics(
    metrics: "dict[str, Any]",
) -> "dict[str, Any]":
    """Return ``metrics`` with every non-finite float value OMITTED (not replaced).

    A NaN/±Inf metric is dropped entirely — never coerced to a sentinel like
    ``0.0`` or ``-999`` — so a reported metrics mapping only ever carries genuinely
    finite values. Non-float values pass through untouched (the ``FiniteFloat``
    field then rejects anything that is not a finite float at construction).
    """
    return {
        k: v
        for k, v in metrics.items()
        if not (isinstance(v, float) and not math.isfinite(v))
    }


class ShadowRunResult(DigestModel):
    """The whole content-sealed shadow run — applies / orders / fills / rejects.

    The immutable, canonically-digested record of one honest shadow replay: the
    matching-engine version and the resolved schedule ref it ran under, the window
    (``start`` / ``end``) and ``init_cash``, the cost-model digest, the staged
    intents referenced ONLY by ``intent_content_digests`` (invariant 1 — a result
    can never embed or mutate an intent), the four record tuples, the NAV path, the
    closed-vocabulary ``metrics``, a strict-int ``n_trades`` (counts are never
    floats and never enter the float metrics dict), and free-form ``warnings`` /
    ``badges``.

    Causation is closed over the record set (invariant 4): every fill's ``order_id``
    and every apply's ``order_ids`` entry must appear among ``orders``, and every
    order's ``target_apply_key`` must appear among ``applies`` — a dangling
    reference fails construction. The record self-seals like every Phase 4/5
    precedent: build it via :meth:`build`, which OMITS non-finite metrics, computes
    the canonical digest and attaches it; the ``@model_validator`` re-verifies both
    causation and the self-seal on every load.
    """

    schema_version: Literal["1"] = "1"
    matching_engine_version: NonEmptyStr
    schedule_ref: ContentRef
    start: IsoDateStr
    end: IsoDateStr
    init_cash: FiniteFloat = Field(gt=0)
    cost_model_digest: DigestHex
    intent_content_digests: tuple[DigestHex, ...]
    applies: tuple[ShadowTargetApplyRecord, ...]
    orders: tuple[ShadowOrderRecord, ...]
    fills: tuple[ShadowFillRecord, ...]
    rejects: tuple[ShadowRejectRecord, ...]
    nav_history: tuple[tuple[NonEmptyStr, FiniteFloat], ...]
    metrics: dict[NonEmptyStr, FiniteFloat] = {}
    n_trades: NonNegativeInt = 0
    warnings: tuple[NonEmptyStr, ...] = ()
    badges: tuple[NonEmptyStr, ...] = ()
    content_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"content_digest"})

    @field_validator("metrics")
    @classmethod
    def _metrics_in_vocabulary(cls, v: "dict[str, float]") -> "dict[str, float]":
        unknown = set(v) - SHADOW_METRIC_KEYS
        if unknown:
            raise ValueError(
                "metrics keys must be within the closed vocabulary "
                f"{sorted(SHADOW_METRIC_KEYS)}; unknown: {sorted(unknown)}"
            )
        return v

    @model_validator(mode="after")
    def _verify(self) -> "ShadowRunResult":
        order_ids = {o.order_id for o in self.orders}
        apply_keys = {a.target_apply_key for a in self.applies}
        # every fill's order_id is present among orders
        for f in self.fills:
            if f.order_id not in order_ids:
                raise ValueError(
                    f"fill {f.fill_id} references order_id {f.order_id} absent from "
                    "orders (dangling causation)"
                )
        # every apply's order_ids entry is present among orders
        for a in self.applies:
            for oid in a.order_ids:
                if oid not in order_ids:
                    raise ValueError(
                        f"apply {a.target_apply_key} references order_id {oid} absent "
                        "from orders (dangling causation)"
                    )
        # every order's target_apply_key is present among applies
        for o in self.orders:
            if o.target_apply_key not in apply_keys:
                raise ValueError(
                    f"order {o.order_id} references target_apply_key "
                    f"{o.target_apply_key} absent from applies (dangling causation)"
                )
        # self-seal: the declared digest must match the canonical semantic digest
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "ShadowRunResult":
        """Seal + construct: OMIT non-finite metrics, compute the digest, validate.

        Non-finite metric values are dropped here (:func:`_drop_non_finite_metrics`)
        BEFORE the digest is computed, so the sealed digest is over the surviving
        finite metrics only — a NaN/Inf metric leaves no trace and is never smuggled
        through as a sentinel. The digest itself is computed over the cleaned field
        values and re-verified by the constructor's self-seal.
        """
        if "metrics" in fields and fields["metrics"] is not None:
            fields = {**fields, "metrics": _drop_non_finite_metrics(fields["metrics"])}
        try:
            digest = cls.digest_of_fields(projection="semantic", **fields)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _RUN_RESULT_DIGEST_PLACEHOLDER
        return cls(**fields, content_digest=digest)
