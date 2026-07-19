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
from typing import Literal

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    PositiveInt,
)
from guanlan_v2.orchestration.enums import Confidence

__all__ = [
    "ShadowContractError",
    "ProposalRejected",
    "PROPOSAL_REASON_CODES",
    "WEIGHT_SUM_TOLERANCE",
    "TARGET_WEIGHT_BANDS",
    "TrancheTrigger",
    "TargetPosition",
    "PortfolioTargetProposal",
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
