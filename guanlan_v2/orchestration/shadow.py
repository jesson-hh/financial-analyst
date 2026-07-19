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
from typing import Annotated, Any, ClassVar, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from guanlan_v2.orchestration.data.calendar import ImmutableTradingCalendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.refs import ContentRef, LogicalId

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
        # ① duplicate symbol by (code, exchange) — the instrument identity that a
        #    downstream book keys on; two legs on the same instrument are never a
        #    valid target book (they would silently net or double-count).
        seen: set[tuple[str, str]] = set()
        for p in self.positions:
            key = (p.symbol.code, p.symbol.exchange)
            if key in seen:
                raise PydanticCustomError(
                    "duplicate_symbol",
                    "duplicate symbol {code}.{exchange} in positions",
                    {"code": p.symbol.code, "exchange": p.symbol.exchange},
                )
            seen.add(key)

        # ② weight-sum identity: sum(target_weight)+cash_weight must equal 1
        #    within WEIGHT_SUM_TOLERANCE — NEVER renormalized. math.fsum mirrors
        #    the house precedent for summing reported floats (RegimeReport).
        weight_sum = math.fsum(p.target_weight for p in self.positions)
        if abs(weight_sum + self.cash_weight - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise PydanticCustomError(
                "weight_sum_violation",
                "sum(target_weight)+cash_weight must equal 1 +/- {tol}, got {total}",
                {"tol": WEIGHT_SUM_TOLERANCE, "total": weight_sum + self.cash_weight},
            )

        # ③ long-only aggregate leverage guard. With cash_weight >= 0 and ②
        #    already enforcing sum+cash == 1, this is subsumed (any leverage input
        #    fails ② first); it is kept as the explicit named long-only invariant
        #    so the guarantee is legible and survives a future cash model that
        #    could admit a negative cash leg. Shorts are impossible via ge=0.
        if weight_sum > 1.0 + WEIGHT_SUM_TOLERANCE:
            raise PydanticCustomError(
                "leverage_or_short",
                "aggregate target weight {total} exceeds 1 (long-only v1)",
                {"total": weight_sum},
            )

        # ④ closed target-weight band vocabulary — the LLM anchor-drift guard.
        #    Each position's target_weight must be EXACTLY a member of
        #    TARGET_WEIGHT_BANDS (float `in`: zero tolerance, NEVER snapped to the
        #    nearest band). This is imposed here at the PROPOSAL layer only (and in
        #    TargetPortfolioIntent, Task 3) and DELIBERATELY NOT on TargetPosition
        #    itself, so the Task 6 deterministic lane (DeterministicTargetSet) can
        #    express continuous rule-computed weights — do not move this check down
        #    into TargetPosition. TARGET_WEIGHT_BANDS is read here as the single
        #    source of truth (no re-inlined literal), so Phase 8's allowed_actions
        #    band ceiling reuses the identical closed vocabulary.
        for p in self.positions:
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
#: below; a schedule may declare another engine/frequency (schema-valid for later
#: phases) but :func:`compute_eligible_execution_at` refuses to compute it here.
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
