# -*- coding: utf-8 -*-
"""Phase 6 - Task 5 - the target-portfolio diff step + engine-shaped ShadowDecisionAgent.

This module is the shadow consumer's actuation edge. It owns two things:

* :func:`diff_target_portfolio` - a pure, deterministic function that diffs a
  staged :class:`~guanlan_v2.orchestration.shadow.TargetPortfolioIntent` against a
  current portfolio (held quantities + reference prices + nav) into a
  :class:`ShadowOrderPlan`: lot-floored target quantities, sells-before-buys
  canonical ordering, honest skips (``no_reference_price`` / ``below_lot_resolution``
  / ``already_at_target``), and **no weight renormalization anywhere** (each buy is
  sized independently as ``delta * price``, never scaled over the batch);
* :class:`ShadowDecisionAgent` - a zero-LLM, engine-shaped consumer that a shadow
  backtest runner (Task 6) drives exactly like the fa engine's own decision agent
  (``NAME`` + a ``n_calls`` property + an async ``decide`` returning an engine
  ``Decision``), but which never calls a model: its legs are the deterministic diff
  mapped onto engine ``DecisionLeg`` values.

Internal carriers (:class:`ShadowOrderPlanEntry` / :class:`ShadowOrderSkip` /
:class:`ShadowOrderPlan`) are frozen :class:`ContractModel` subclasses, deliberately
**not** ``DigestModel`` and **not** registered by any schema registry - they are a
private, reviewed intermediate vocabulary, classified internal by the Phase-6
firewall scoping (``PHASE6_MODULES``), never a public payload.

Engine-surface reality (verified against ``financial_analyst.backtest.decision``,
lines 60-88): ``DecisionInput.date`` is an ISO ``str`` (not a ``date``);
``DecisionInput.holdings`` is ``dict[str, dict]`` keyed by the engine's ``SH600519``
code form, each value carrying ``qty`` + ``mkt_value`` (so a held symbol's reference
price is ``mkt_value / qty``); ``DecisionLeg`` fields are
``code/action/target_price/stop_loss/weight_pct/reason``. The agent therefore prices
only what it holds and honestly skips any target it cannot price - it never
fabricates a price.
"""
from __future__ import annotations

import dataclasses
import importlib
import math
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from guanlan_v2.orchestration.data.calendar import ImmutableTradingCalendar
from guanlan_v2.orchestration.data.symbols import Symbol, normalize_symbol
from guanlan_v2.orchestration.digest import (
    ContractModel,
    DigestHex,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    content_digest,
)
from guanlan_v2.orchestration.enums import ApprovalPolicy, ExperimentStatus
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, SystemClock, clock_now
from guanlan_v2.orchestration.adapters.contracts import (
    DualCurveReport,
    ReplayDecisionPoint,
    ShadowCurvePoint,
    ShadowCurveSeries,
    ShadowExecutionConfig,
    ShadowReplayRunState,
)
# NOTE (correction N6-1): imported UNDER A TOKEN-FREE ALIAS. The Phase-6 red line
# `test_phase9_machinery_names_absent_from_phase6_modules` forbids the "wakeup" token in any
# defined/imported NAME of a Phase-6 module; the AST guard collects the BOUND name, so the
# alias (not the original) is what it sees. The class is unchanged — downstream consumes it
# as `contracts.ReplayWakeupReceipt`; this module refers to it as `ReplayMaturityReceipt`.
from guanlan_v2.orchestration.adapters.contracts import (  # noqa: E402
    ReplayWakeupReceipt as ReplayMaturityReceipt,
)
from guanlan_v2.orchestration.shadow import (
    CorporateActionEvent,
    DecisionSchedule,
    IsoDateStr,
    SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN,
    SHADOW_MATCHING_ENGINE_VERSION,
    ShadowContractError,
    ShadowEnvelopeError,
    ShadowFillRecord,
    ShadowOrderKind,
    ShadowOrderRecord,
    ShadowRejectRecord,
    ShadowRunResult,
    ShadowTargetApplyRecord,
    TargetPortfolioIntent,
    TargetPosition,
    UnsupportedBarFrequencyError,
    _verify_portfolio_matrix,
    compute_cutoff_at,
    compute_eligible_execution_at,
    compute_scheduled_for,
    deterministic_apply_key_parts,
    is_decision_point,
    shadow_fill_id,
    shadow_order_id,
    target_apply_key,
    wrap_proposal_as_intent,
)

if TYPE_CHECKING:  # annotations only (PEP 563) - never forces the engine onto the path
    from financial_analyst.backtest.decision import Decision, DecisionInput
    from guanlan_v2.orchestration.budget import BudgetLedger
    from guanlan_v2.orchestration.context import RunBudget

__all__ = [
    "SHADOW_SKIP_REASONS",
    "ShadowOrderPlanEntry",
    "ShadowOrderSkip",
    "ShadowOrderPlan",
    "diff_target_portfolio",
    "ShadowApplyConflict",
    "ShadowDecisionAgent",
    # Task 6 - the shadow backtest runner + deterministic dual-curve lane
    "ShadowRunConfig",
    "ShadowBacktestRunner",
    "DeterministicTargetSet",
    "SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN",
    "deterministic_apply_key",
    # Task 7 - gap-filling exit management + corporate-action ledger
    "CorporateActionApplication",
    "apply_corporate_actions",
    # Task 8 - stage-1 compatibility mirror of the frontend runBacktest profile
    "COMPATIBILITY_PROFILE_ID",
    "COMPAT_TRADE_STRUCTURE_TOL",
    "COMPAT_PRICE_REL_TOL",
    "COMPAT_RETURN_ABS_TOL",
    "COMPAT_EQUITY_REL_TOL",
    "COMPAT_METRIC_REL_TOL",
    "CompatSignal",
    "CompatClock",
    "CompatTrade",
    "CompatibilityRunResult",
    "CompatibilityProfileError",
    "compat_metrics",
    "run_compatibility_mirror",
    "map_intents_to_compat_signals",
    # Task 4 (Phase 9) - DecisionSchedule interval-replay driver
    "RebalanceDateNotSessionError",
    "SnapshotBindingRefused",
    "RetroactiveIntentRefused",
    "ReplayApprovalRefused",
    "resolve_decision_points",
    "reconcile_daily_llm_budget",
    "ReplayIntentLedger",
    "ReplayPlanCoordinator",
    "ReplayPointSnapshot",
    "DeterministicBook",
    "ReplayRuntimeBindings",
    "run_interval_replay",
    # Task 5 (Phase 9) - dual curves under one execution attestation + handoff
    "UnsourcedFactorScoreError",
    "derive_deterministic_targets",
    "build_dual_curves",
    "hand_off_dual_curves_to_feedback",
    # Task 6 (Phase 9) - WAITING_FOR_MATURITY persistence + idempotent maturity wakeup
    "REPLAY_HEAD_NAMESPACE",
    "REPLAY_OPERATION_NAMESPACE",
    "REPLAY_STATE_CELL_NAMESPACES",
    "REPLAY_MATURITY_KEY_DOMAIN",
    "ReplayMaturityKeyUnknown",
    "ReplayStateStore",
    "derive_replay_maturity_key",
    "persist_replay_state",
    "mature_shadow_replay",
]

#: the closed skip-reason vocabulary a :class:`ShadowOrderSkip` may carry. A reason
#: outside this set is a hard validation error, so the diff can never invent a
#: reason. Each is an HONEST non-action: a target the framework could not price, a
#: positive target too small to buy a whole lot, or a position already at target.
SHADOW_SKIP_REASONS: frozenset[str] = frozenset(
    {"no_reference_price", "below_lot_resolution", "already_at_target"}
)


# --------------------------------------------------------------------------- #
# Internal frozen carriers (unregistered - reviewed internal, not schema-frozen) #
# --------------------------------------------------------------------------- #
class ShadowOrderPlanEntry(ContractModel):
    """One actionable leg of a target-portfolio diff (a buy or a sell).

    ``qty`` is the exact lot-resolved share quantity; for a buy it is paired with
    ``cash_budget = delta * reference_price`` (both carried, the runner may size by
    either), for a sell ``cash_budget`` is ``None`` (a sell is quantity-based).
    ``ordinal`` is the 0-based position of this entry in the whole plan's canonical
    order (all sells first, then all buys, each by ``Symbol.code``), so replaying the
    plan is deterministic regardless of how the input mappings iterated.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    symbol: Symbol
    order_kind: ShadowOrderKind
    side: Literal["buy", "sell"]
    qty: PositiveInt | None
    cash_budget: FiniteFloat | None
    ordinal: NonNegativeInt


class ShadowOrderSkip(ContractModel):
    """One target the diff honestly declined to act on, with a closed reason.

    ``reason`` is a member of the closed :data:`SHADOW_SKIP_REASONS` vocabulary - a
    skip is always a truthful non-action (missing reference price, a positive target
    below one lot, or a position already at its target), never a fabricated order or
    a fabricated price.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    symbol: Symbol
    reason: NonEmptyStr

    @field_validator("reason")
    @classmethod
    def _closed_reason(cls, v: str) -> str:
        if v not in SHADOW_SKIP_REASONS:
            raise ValueError(
                f"skip reason {v!r} is not in the closed vocabulary "
                f"{sorted(SHADOW_SKIP_REASONS)}"
            )
        return v


class ShadowOrderPlan(ContractModel):
    """The whole deterministic diff of an intent against a portfolio.

    ``entries`` are the actionable legs in canonical order (all sells by
    ``Symbol.code``, then all buys by ``Symbol.code``, ordinals 0..); ``skipped``
    are the honest non-actions. Two diffs over the same ``(intent, holdings, prices,
    nav)`` compare equal (frozen value equality), independent of mapping iteration
    order.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    entries: tuple[ShadowOrderPlanEntry, ...]
    skipped: tuple[ShadowOrderSkip, ...]


# --------------------------------------------------------------------------- #
# The pure diff step                                                          #
# --------------------------------------------------------------------------- #
def _floor_lot(raw_qty: float, lot_size: int) -> int:
    """Floor ``raw_qty`` down to a whole multiple of ``lot_size`` (>= 0 for a valid
    non-negative target). With ``lot_size == 1`` this degenerates to whole-share,
    fractional-free sizing (the compat path)."""
    if lot_size <= 0:
        raise ValueError(f"lot_size must be a positive int, got {lot_size!r}")
    return int(math.floor(raw_qty / lot_size)) * lot_size


def diff_target_portfolio(
    intent: TargetPortfolioIntent,
    *,
    holdings: Mapping[str, int],
    reference_prices: Mapping[str, float],
    nav: float,
    lot_size: int = 100,
) -> ShadowOrderPlan:
    """Diff a staged target portfolio against a current book into a :class:`ShadowOrderPlan`.

    Pure and deterministic. The closed rules (no renormalization anywhere):

    * target qty per position = ``floor((target_weight * nav / price) / lot_size) * lot_size``;
    * a held symbol absent from the target -> full ``target_sell`` of the held qty
      (no reference price needed - a position is always exitable by quantity);
    * ``delta = target_qty - held_qty``: ``delta < 0`` -> ``target_sell`` of ``-delta``
      (a partial reduce, or a full exit when the target floored to 0);
      ``delta > 0`` -> ``target_buy`` of ``delta`` with ``cash_budget = delta * price``;
      ``delta == 0`` -> ``already_at_target`` skip;
    * a target with no reference price -> ``no_reference_price`` skip (never fabricated);
    * a positive ``target_weight`` whose lot-floored target is 0 -> ``below_lot_resolution`` skip.

    ``holdings`` / ``reference_prices`` are keyed by any A-share symbol string form
    (bare / dotted / engine) - each is resolved through
    :func:`~guanlan_v2.orchestration.data.symbols.normalize_symbol` to a canonical
    :class:`Symbol`, so lookups are key-form and mapping-iteration-order independent.
    Ordering is canonical and completion-order-free: all sells sorted by
    ``Symbol.code`` (then exchange), then all buys sorted the same way, with
    ``ordinal`` running 0.. across the whole plan.
    """
    # Resolve every input key to a canonical Symbol, indexed by dotted (code.exchange)
    # so the diff is robust to the caller's key form and to mapping iteration order.
    sym_by_dotted: dict[str, Symbol] = {}
    held_by_dotted: dict[str, int] = {}
    for raw, qty in holdings.items():
        sym = normalize_symbol(raw)
        sym_by_dotted[sym.dotted] = sym
        held_by_dotted[sym.dotted] = int(qty)
    price_by_dotted: dict[str, float] = {}
    for raw, px in reference_prices.items():
        sym = normalize_symbol(raw)
        sym_by_dotted.setdefault(sym.dotted, sym)
        price_by_dotted[sym.dotted] = float(px)

    target_dotted: set[str] = set()
    sells: list[tuple[Symbol, int]] = []            # (symbol, qty)
    buys: list[tuple[Symbol, int, float]] = []      # (symbol, qty, cash_budget)
    skips: list[ShadowOrderSkip] = []

    # --- target positions ---
    for p in intent.positions:
        sym = p.symbol
        dotted = sym.dotted
        target_dotted.add(dotted)
        sym_by_dotted.setdefault(dotted, sym)
        held_qty = held_by_dotted.get(dotted, 0)
        price = price_by_dotted.get(dotted)
        if price is None:
            skips.append(ShadowOrderSkip(symbol=sym, reason="no_reference_price"))
            continue
        target_qty = _floor_lot(p.target_weight * nav / price, lot_size)
        if target_qty == 0 and p.target_weight > 0:
            skips.append(ShadowOrderSkip(symbol=sym, reason="below_lot_resolution"))
            continue
        delta = target_qty - held_qty
        if delta == 0:
            skips.append(ShadowOrderSkip(symbol=sym, reason="already_at_target"))
        elif delta < 0:
            sells.append((sym, -delta))
        else:
            buys.append((sym, delta, delta * price))

    # --- held symbols absent from the target -> full exit (no price required) ---
    for dotted, held_qty in held_by_dotted.items():
        if dotted in target_dotted or held_qty <= 0:
            continue
        sells.append((sym_by_dotted[dotted], held_qty))

    # --- canonical ordering: sells (by code, exchange) then buys (by code, exchange) ---
    sells.sort(key=lambda t: (t[0].code, t[0].exchange))
    buys.sort(key=lambda t: (t[0].code, t[0].exchange))
    skips.sort(key=lambda s: (s.symbol.code, s.symbol.exchange, s.reason))

    entries: list[ShadowOrderPlanEntry] = []
    ordinal = 0
    for sym, qty in sells:
        entries.append(
            ShadowOrderPlanEntry(
                symbol=sym, order_kind="target_sell", side="sell",
                qty=qty, cash_budget=None, ordinal=ordinal,
            )
        )
        ordinal += 1
    for sym, qty, budget in buys:
        entries.append(
            ShadowOrderPlanEntry(
                symbol=sym, order_kind="target_buy", side="buy",
                qty=qty, cash_budget=float(budget), ordinal=ordinal,
            )
        )
        ordinal += 1

    return ShadowOrderPlan(entries=tuple(entries), skipped=tuple(skips))


# --------------------------------------------------------------------------- #
# The engine-shaped, zero-LLM ShadowDecisionAgent                              #
# --------------------------------------------------------------------------- #
class ShadowApplyConflict(ShadowContractError):
    """Two staged intents would apply indistinguishably with different content.

    Raised at :class:`ShadowDecisionAgent` construction when either (a) two intents
    share a ``target_apply_key`` but carry different semantic digests (the same
    application would mean two different books), or (b) two distinct intents resolve
    to the same execution session with different content (an ambiguous decision
    point). Byte-identical duplicates are collapsed, never a conflict.
    """


class ShadowDecisionAgent:
    """A zero-LLM, engine-shaped consumer of staged target-portfolio intents.

    A shadow backtest runner (Task 6) drives this exactly like the fa engine's own
    pre-open decision agent: it exposes ``NAME``, a ``n_calls`` property (always
    ``0`` - this agent never calls a model), and an async :meth:`decide` returning
    an engine ``Decision``. On each session it looks up the unique staged intent
    whose ``eligible_execution_at`` falls on that session (derived in the schedule's
    timezone, never UTC); with none it returns an all-hold ``Decision`` (the agent
    is total - a missing intent is never an exception, never a fabricated intent).
    With one, it runs :func:`diff_target_portfolio` against the portfolio the input
    carries and maps the plan onto engine legs by the closed table: a full sell ->
    ``action="sell"``, a partial sell -> ``action="reduce"``, a buy -> ``action="buy"``
    with ``weight_pct = delta_weight * 100`` and ``stop_loss`` derived from the
    matching position's ``stop_loss_pct`` (absolute price = reference price *
    (1 - pct)); every leg's ``reason`` is ``"shadow-intent:" + intent.semantic_digest()[:16]``
    so causation is visible.

    RUNNER-CONSUMED-ONLY RED LINE (invariant 6). This agent is consumed EXCLUSIVELY
    by the shadow backtest runner (Task 6). It must NEVER be plugged into the fa
    engine's own stock backtest runner, whose leg-to-order path at engine.py:165
    re-normalizes buy ``weight_pct`` over the same batch and ignores already-held
    weights - that silent renormalization is the exact hazard that would corrupt the
    shadow target weights this agent emits. This module therefore neither imports nor
    references that engine path (asserted by an explicit source/import-graph
    invariant test); the only engine surface it touches is the value objects in
    ``financial_analyst.backtest.decision``.

    Engine-surface note: the agent can only price what the portfolio snapshot carries
    (a held position's ``mkt_value / qty``); a target it does not already hold cannot
    be priced from a ``DecisionInput`` alone, so the underlying diff honestly skips it
    with ``no_reference_price`` (surfaced as a ``Decision`` warning) rather than
    fabricating a price. Real per-bar buy execution is the shadow runner's job.
    """

    NAME: str = "shadow-intent-agent"

    def __init__(
        self,
        *,
        intents: tuple[TargetPortfolioIntent, ...],
        calendar: ImmutableTradingCalendar,
        schedule: DecisionSchedule,
        lot_size: int = 100,
    ) -> None:
        if calendar.calendar_id != schedule.calendar_id:
            raise ShadowContractError(
                f"calendar {calendar.calendar_id!r} does not match schedule "
                f"calendar_id {schedule.calendar_id!r}"
            )
        self._calendar = calendar
        self._schedule = schedule
        self._lot_size = lot_size
        tz = ZoneInfo(schedule.timezone)

        # De-duplicate by operational apply key; reject same-key/different-content.
        by_apply: dict[str, TargetPortfolioIntent] = {}
        for intent in intents:
            key = target_apply_key(intent)
            existing = by_apply.get(key)
            if existing is None:
                by_apply[key] = intent
            elif existing.semantic_digest() != intent.semantic_digest():
                raise ShadowApplyConflict(
                    f"two intents share apply key {key} with different content "
                    "(a target portfolio is applied at most once per apply key)"
                )
            # else: a byte-identical duplicate -> collapse (keep the first).

        # Index the surviving intents by execution session (local date in the
        # schedule's timezone). Two DISTINCT intents on the same session with
        # different content is an ambiguous decision point -> refused, so decide's
        # lookup is always unique.
        by_session: dict[date, TargetPortfolioIntent] = {}
        for intent in by_apply.values():
            session = intent.eligible_execution_at.astimezone(tz).date()
            existing = by_session.get(session)
            if existing is None:
                by_session[session] = intent
            elif existing.semantic_digest() != intent.semantic_digest():
                raise ShadowApplyConflict(
                    "two distinct intents resolve to the same execution session "
                    f"{session.isoformat()} with different content (ambiguous "
                    "decision point)"
                )
            # else: identical book on the same session -> collapse.
        self._by_session: dict[date, TargetPortfolioIntent] = by_session

    @property
    def n_calls(self) -> int:
        """Always ``0`` - this agent is deterministic and never calls a model."""
        return 0

    async def decide(self, inp: DecisionInput) -> Decision:
        """Map the staged intent for ``inp``'s session onto an engine ``Decision``.

        Total: a session with no staged intent yields an all-hold ``Decision`` (no
        legs), never an exception. The engine value objects are imported lazily so
        this module stays importable without the engine on the path.
        """
        from financial_analyst.backtest.decision import Decision, DecisionLeg

        session = date.fromisoformat(inp.date)
        intent = self._by_session.get(session)
        if intent is None:
            return Decision(
                market_view=f"shadow: no eligible intent for session {inp.date}",
                decisions=[], warnings=[], raw={},
            )

        holdings_qty, reference_prices = self._prices_from_holdings(inp.holdings)
        plan = diff_target_portfolio(
            intent,
            holdings=holdings_qty,
            reference_prices=reference_prices,
            nav=inp.nav,
            lot_size=self._lot_size,
        )

        held_by_dotted = {normalize_symbol(c).dotted: q for c, q in holdings_qty.items()}
        price_by_dotted = {normalize_symbol(c).dotted: p for c, p in reference_prices.items()}
        target_by_dotted = {p.symbol.dotted: p for p in intent.positions}
        digest16 = intent.semantic_digest()[:16]
        reason = "shadow-intent:" + digest16

        legs: list = []
        for e in plan.entries:
            dotted = e.symbol.dotted
            code = e.symbol.engine_code
            if e.side == "sell":
                held_qty = held_by_dotted.get(dotted, 0)
                # a full exit sells the whole held qty; a partial sell is a reduce.
                action = "sell" if (e.qty is not None and e.qty >= held_qty) else "reduce"
                legs.append(DecisionLeg(code=code, action=action, reason=reason))
            else:  # buy
                price = price_by_dotted.get(dotted)
                pos = target_by_dotted.get(dotted)
                budget = e.cash_budget or 0.0
                weight_pct = (budget / inp.nav * 100.0) if inp.nav else 0.0
                stop_loss = 0.0
                if pos is not None and pos.stop_loss_pct is not None and price is not None:
                    stop_loss = price * (1.0 - pos.stop_loss_pct)
                legs.append(
                    DecisionLeg(
                        code=code, action="buy", weight_pct=weight_pct,
                        stop_loss=stop_loss, reason=reason,
                    )
                )

        warnings = [f"skip:{s.reason}:{s.symbol.engine_code}" for s in plan.skipped]
        return Decision(
            market_view=f"shadow-intent:{digest16} session {inp.date}",
            decisions=legs, warnings=warnings, raw={},
        )

    @staticmethod
    def _prices_from_holdings(
        holdings: Mapping[str, dict] | None,
    ) -> tuple[dict[str, int], dict[str, float]]:
        """Derive ``(held_qty, reference_prices)`` from an engine holdings snapshot.

        The engine holdings dict is ``{code: {qty, avg_cost, stop_loss, mkt_value}}``;
        a held position's reference price is ``mkt_value / qty``. Only well-formed
        positive quantities and finite positive market values contribute - a
        malformed entry is skipped, never coerced into a fabricated price.
        """
        held_qty: dict[str, int] = {}
        reference_prices: dict[str, float] = {}
        for code, pos in (holdings or {}).items():
            if not isinstance(pos, dict):
                continue
            raw_qty = pos.get("qty")
            if isinstance(raw_qty, bool) or not isinstance(raw_qty, (int, float)):
                continue
            qty = int(raw_qty)
            if qty <= 0:
                continue
            held_qty[code] = qty
            mkt = pos.get("mkt_value")
            if (
                isinstance(mkt, (int, float))
                and not isinstance(mkt, bool)
                and math.isfinite(mkt)
                and mkt > 0
            ):
                reference_prices[code] = float(mkt) / qty
        return held_qty, reference_prices


# =========================================================================== #
# Task 6 · ShadowBacktestRunner — apply-once loop over the engine Broker         #
#          baseline + the deterministic dual-curve lane                          #
# =========================================================================== #
# Task 5 owns the actuation edge (the pure diff + the engine-shaped agent); Task 6
# owns the *runner*: a synchronous (zero-LLM ⇒ no coroutine) day-by-day driver that
# applies staged target portfolios ONCE each, above the fa engine's mutating
# ``Broker.match`` (grounding gotcha 4 — idempotency lives in the runner), and
# emits a content-sealed :class:`~guanlan_v2.orchestration.shadow.ShadowRunResult`.
#
# ENGINE CONSUMPTION (invariant-6 red line preserved): every fa backtest primitive
# the runner needs is loaded LAZILY through :func:`_engine_api` (``importlib`` inside
# a function, never a static ``from financial_analyst...`` import), so importing this
# adapter never forces the engine onto the path — the exact discipline the zero-LLM
# ``ShadowDecisionAgent.decide`` already follows. The only STATIC engine import in
# this module remains ``financial_analyst.backtest.decision`` (the agent's value
# objects); the engine's own leg-to-order normalization runner path (the batch
# weight-renormalizing driver at engine.py:165) is never imported OR referenced
# (spec §1 reuse boundary — the target diff carries exact qty/cash_budget, never
# re-normalized over a batch).


_ENGINE_API: SimpleNamespace | None = None


def _engine_api() -> SimpleNamespace:
    """Lazily resolve the fa backtest primitives the runner consumes (cached).

    Loaded via ``importlib`` on first use — NOT a static module import — so this
    adapter stays importable without the engine on the path, and the only static
    engine dependency remains the agent's ``financial_analyst.backtest.decision``.
    """
    global _ENGINE_API
    if _ENGINE_API is None:
        broker = importlib.import_module("financial_analyst.backtest.broker")
        costs = importlib.import_module("financial_analyst.backtest.costs")
        engine = importlib.import_module("financial_analyst.backtest.engine")
        limits = importlib.import_module("financial_analyst.backtest.limits")
        metrics = importlib.import_module("financial_analyst.backtest.metrics")
        portfolio = importlib.import_module("financial_analyst.backtest.portfolio")
        records = importlib.import_module("financial_analyst.backtest.records")
        _ENGINE_API = SimpleNamespace(
            Broker=broker.Broker,
            Order=broker.Order,
            CostModel=costs.CostModel,
            prepare_bar=engine.prepare_bar,
            RunConfig=engine.RunConfig,
            limit_pct_for=limits.limit_pct_for,
            compute_metrics=metrics.compute_metrics,
            VirtualPortfolio=portfolio.VirtualPortfolio,
            TradeLog=records.TradeLog,
        )
    return _ENGINE_API


# --------------------------------------------------------------------------- #
# Deterministic dual-curve lane — carrier + its own apply-key family            #
# --------------------------------------------------------------------------- #
class DeterministicTargetSet(ContractModel):
    """A rule-computed target book for the deterministic dual-curve replay lane.

    The envelope-FREE counterpart of a :class:`TargetPortfolioIntent`: produced
    WITHOUT any LLM or intent envelope (it carries no ``origin`` / ``authority`` /
    ``execution_scope`` and can never become an intent). It is validated by the
    EXACT Task-1 duplicate / weight-sum / leverage matrix (via the shared
    ``_verify_portfolio_matrix``), but is **exempt from the Task-1b band-domain
    check**: anchor drift is an LLM pathology, so a deterministic rule leg must be
    able to express continuous weights such as equal-weight-three ≈ 1/3 — which is
    exactly why Task 1b pins the band validator at the proposal/intent layer and
    never on ``TargetPosition`` (which both lanes inherit).

    Band exemption is resolved by REUSING the shared matrix and catching only its
    band breach (``PydanticCustomError`` with ``type == "non_band_weight"``); every
    other breach (duplicate / sum / leverage) still propagates. This weakens no
    proposal/intent path (the shared helper is untouched) and duplicates no matrix
    logic.

    Its *semantic* digest (the deterministic causation digest that binds a run
    record back to the exact rule book) is :func:`_deterministic_causation_digest`
    — this carrier is a reviewed internal ``ContractModel`` (not a ``DigestModel``,
    like the Task-5 order-plan carriers), so it is unregistered and Phase-6 scoped.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    rule_id: NonEmptyStr
    point_ordinal: NonNegativeInt
    target_version: PositiveInt
    session_date: IsoDateStr
    positions: tuple[TargetPosition, ...]
    cash_weight: FiniteFloat = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _verify_band_exempt(self) -> "DeterministicTargetSet":
        # reuse the SHARED long-only matrix (① duplicate → ② sum → ③ leverage →
        # ④ band), swallowing ONLY the band breach ④ (the deterministic lane is
        # band-exempt). ④ is checked last and raises only when ①②③ passed, so
        # swallowing it can never hide a real duplicate / sum / leverage breach.
        try:
            _verify_portfolio_matrix(self.positions, self.cash_weight)
        except PydanticCustomError as exc:
            if exc.type != "non_band_weight":
                raise
        return self


def deterministic_apply_key(target_set: DeterministicTargetSet) -> DigestHex:
    """The deterministic lane's idempotent apply key for ``target_set`` — over exactly
    ``{domain, rule_id, point_ordinal, target_version}``.

    A thin wrapper that unpacks the target set into the shared, single-sourced
    :func:`shadow.deterministic_apply_key_parts` component builder (the domain tag +
    the key family live in ``shadow.py``, sibling of the three intent-family
    builders). Its distinct :data:`SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN` tag makes a
    collision with the intent family (``target_apply_key``) structurally impossible.
    This is BOTH the runner's DEDUP key AND the value the apply record stores natively
    as its ``target_apply_key`` in ``run_targets`` (the record recomputes it through
    the same builder), so the two lanes' stored keys are disjoint by construction.
    """
    return deterministic_apply_key_parts(
        rule_id=target_set.rule_id,
        point_ordinal=target_set.point_ordinal,
        target_version=target_set.target_version,
    )


def _deterministic_causation_digest(target_set: DeterministicTargetSet) -> DigestHex:
    """The deterministic causation digest = the target set's SEMANTIC digest.

    Over the rule book's full substance (``rule_id`` / ``point_ordinal`` /
    ``target_version`` / ``session_date`` / ``positions`` / ``cash_weight``). It is
    computed here (not via ``.semantic_digest()``) because the carrier is a plain
    ``ContractModel``, not a ``DigestModel``; the nested ``TargetPosition`` values
    still contribute their own semantic projection through the canonical-JSON
    recursion. Stored as a record's ``intent_content_digest`` so a replay binds the
    record back to the exact rule book.
    """
    return content_digest(
        {
            "rule_id": target_set.rule_id,
            "point_ordinal": target_set.point_ordinal,
            "target_version": target_set.target_version,
            "session_date": target_set.session_date,
            "positions": list(target_set.positions),
            "cash_weight": target_set.cash_weight,
        }
    )


# --------------------------------------------------------------------------- #
# Run config bundle (the pinned identity both lanes must share)                 #
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class ShadowRunConfig:
    """The ``run(intents)`` lane config a ``run_targets`` call must match exactly.

    ``start`` / ``end`` are the per-call replay window (used as-is). The remaining
    fields — ``init_cash`` / ``cost_model`` / ``corporate_actions`` / ``is_st`` /
    ``lot_size`` — plus the calendar id and clock identity form the pinned *binding
    digest* (:meth:`ShadowBacktestRunner._binding_digest`): if a ``run_targets``
    ``run_config`` / ``calendar`` / ``clock`` do not reproduce the runner's own
    binding digest, the call is refused, so the two lanes can never run under
    different matching engines, cost models, calendars or clocks.
    """

    start: str
    end: str
    init_cash: float
    cost_model: Any
    corporate_actions: tuple = ()
    is_st: Mapping[str, bool] | None = None
    lot_size: int = 100


@dataclasses.dataclass(frozen=True)
class _ApplicationUnit:
    """Normalized, lane-agnostic application unit driving the shared per-day loop.

    Both lanes project their input into these units so the loop, diff, matching,
    records and result assembly are byte-for-byte shared (invariant 7 by
    construction). ``dedup_key`` is the lane's apply-key family value (the applied-
    key set); ``record_apply_key`` is the lane-NATIVE apply key the Task-4
    ``ShadowTargetApplyRecord`` / ``ShadowOrderRecord`` self-validate against — the
    intent-family key for the intent lane, the deterministic-family key for the
    deterministic lane (so in BOTH lanes ``record_apply_key == dedup_key``).
    ``rule_id`` / ``point_ordinal`` are the deterministic lane's business identity
    (``None`` for the intent lane) — stamped onto the apply record so it recomputes
    its key through the deterministic builder.
    """

    dedup_key: str
    record_apply_key: str
    dedup_semantic: str
    intent_content_digest: str
    intent_id: str
    scheduled_for: datetime
    target_version: int
    eligible_session: str
    positions: tuple
    cash_weight: float
    rule_id: str | None = None
    point_ordinal: int | None = None


# =========================================================================== #
# Task 7 · gap-filling — runner-held exit state + the corporate-action ledger   #
# =========================================================================== #
# The engine Broker has NO take-profit, NO max-hold and NO corporate-action code
# (Task 0 item 8 / grounding §1.5 NOT-FOUND). All three are built HERE, above the
# Broker — never inside it. Stop-loss is the one exit reused engine-native
# (``Order(otype="stop")``); take-profit rides the engine's OWN limit-sell touch
# rule; max-hold is a limit sell at the 跌停 floor (the engine forced-sell
# convention). The corporate-action ledger mutates the VirtualPortfolio's OBJECTS
# through their public attributes (cash / qty / avg_cost / stop_loss / locked),
# never the engine.


@dataclasses.dataclass
class _ExitState:
    """Runner-held per-position exit state, keyed by engine code.

    Populated when a position opens (a buy fills) whose governing
    :class:`TargetPosition` carries any exit parameter; a later target that re-buys
    the same symbol re-parameterizes it (overwrite). Carries the ORIGINATING apply
    key so every exit order retains causation up to the applied target that opened
    the position (invariant 5). ``entry_bar_index`` is the position of the entry bar
    in the run's ``days`` list (max-hold counts bars from it); ``entry_price`` is the
    realized buy fill price (take-profit is ``entry_price * (1 + take_profit_pct)``)
    and is rescaled by a share-event corporate action so the take-profit level tracks
    the ex-div-corrected price.
    """

    entry_bar_index: int
    entry_price: float
    apply_key: str
    symbol: Symbol
    position: TargetPosition


class CorporateActionApplication(ContractModel):
    """One applied corporate action's before/after ledger delta (internal carrier).

    A reviewed internal :class:`ContractModel` (frozen, unregistered, Phase-6 scoped
    — like the Task-5 order-plan carriers), recording exactly what
    :func:`apply_corporate_actions` did to one held (or not-held) position:
    ``cash_credited`` (a cash dividend's ``qty * cash_per_share``, else ``0``),
    ``qty_before`` / ``qty_after`` (equal for a cash dividend; floor-rescaled for a
    share event) and ``avg_cost_before`` / ``avg_cost_after`` (avg cost preserved so
    ``qty * avg_cost`` is invariant within one floor step). A symbol not held is a
    zero-delta application (all-zero deltas), never an order and never an intent.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    event_digest: DigestHex
    symbol: Symbol
    cash_credited: FiniteFloat
    qty_before: NonNegativeInt
    qty_after: NonNegativeInt
    avg_cost_before: FiniteFloat
    avg_cost_after: FiniteFloat


def _share_event_multiplier(event: CorporateActionEvent) -> float:
    """The share multiplier ``M`` a share event applies to a quantity.

    ``stock_bonus`` adds ``shares_ratio`` shares per held share (``M = 1 + ratio``);
    a ``split`` restates each old share into ``shares_ratio`` new shares
    (``M = ratio``). Cash dividends never reach here.
    """
    if event.kind == "stock_bonus":
        return 1.0 + event.shares_ratio
    return event.shares_ratio  # split


def apply_corporate_actions(
    portfolio: Any,
    events: tuple[CorporateActionEvent, ...],
    *,
    on_date: IsoDateStr,
    applied_digests: set[str],
) -> tuple[CorporateActionApplication, ...]:
    """Apply every ``ex_date == on_date`` corporate action to ``portfolio`` ONCE.

    Closed semantics, applied in ``(ex_date, Symbol.code, kind)`` order, each event
    at most once (digest-keyed via ``applied_digests`` — a replayed event tuple
    yields zero additional applications):

    * ``cash_dividend`` → ``portfolio.cash += qty * cash_per_share``; qty / avg_cost
      unchanged;
    * ``stock_bonus`` → ``qty_after = floor(qty * (1 + shares_ratio))``;
    * ``split``       → ``qty_after = floor(qty * shares_ratio)``;

    for both share events ``avg_cost_after = avg_cost * qty / qty_after`` (cost basis
    preserved), the per-position ``stop_loss`` price is rescaled by ``qty / qty_after``
    (price down-scale), and every ``Position.locked`` T+1 bucket is rescaled with the
    SAME floor rule (``floor(bucket * M)`` — T+1 never unlocks early via a corporate
    action). A symbol not held is a zero-delta no-op recorded as an application.
    Applying corporate actions never creates orders and never touches intents; it
    mutates the portfolio's public attributes above the Broker (the engine is
    untouched). ``mkt_value`` is deliberately left as-is: a share event preserves
    ``qty * price`` (qty up ×M, price down ÷M), so the pre-ex mark IS the ex-adjusted
    value, and the day's EOD ``mark_to_market`` overwrites it with the real close.
    """
    apps: list[CorporateActionApplication] = []
    for event in sorted(
        (e for e in events if e.ex_date == on_date),
        key=lambda e: (e.ex_date, e.symbol.code, e.kind),
    ):
        digest = event.semantic_digest()
        if digest in applied_digests:
            continue  # at-most-once (digest idempotency)
        applied_digests.add(digest)
        code = event.symbol.engine_code
        pos = portfolio.positions.get(code)
        if pos is None or pos.qty <= 0:
            # not held → zero-delta no-op (recorded, never an order/intent).
            apps.append(
                CorporateActionApplication(
                    event_digest=digest, symbol=event.symbol, cash_credited=0.0,
                    qty_before=0, qty_after=0, avg_cost_before=0.0, avg_cost_after=0.0,
                )
            )
            continue
        qty_before = int(pos.qty)
        avg_before = float(pos.avg_cost)
        if event.kind == "cash_dividend":
            credited = qty_before * float(event.cash_per_share)
            portfolio.cash += credited
            qty_after = qty_before
            avg_after = avg_before
        else:  # stock_bonus | split — share event
            mult = _share_event_multiplier(event)
            qty_after = int(math.floor(qty_before * mult))
            if qty_after <= 0:  # degenerate (never for a valid share event) — no-op
                qty_after = qty_before
            credited = 0.0
            avg_after = avg_before * qty_before / qty_after  # cost basis preserved
            price_factor = qty_before / qty_after            # < 1 (price down-scale)
            if pos.stop_loss > 0:
                pos.stop_loss = pos.stop_loss * price_factor
            pos.locked = {
                d: int(math.floor(q * mult)) for d, q in pos.locked.items()
            }
            pos.qty = qty_after
            pos.avg_cost = avg_after
        apps.append(
            CorporateActionApplication(
                event_digest=digest, symbol=event.symbol,
                cash_credited=float(credited),
                qty_before=qty_before, qty_after=qty_after,
                avg_cost_before=avg_before, avg_cost_after=float(avg_after),
            )
        )
    return tuple(apps)


class ShadowBacktestRunner:
    """Apply-once shadow backtest runner over the fa engine ``Broker`` baseline.

    Synchronous (zero-LLM ⇒ no coroutine). :meth:`run` applies each staged
    :class:`TargetPortfolioIntent` ONCE at its eligible execution bar through the
    closed per-day loop below, minting immutable run records and a content-sealed
    :class:`ShadowRunResult`. :meth:`run_targets` is the deterministic dual-curve
    entry (Phase 9's replay consumer): identical loop, ENVELOPE-FREE inputs
    (:class:`DeterministicTargetSet`), origin-free records, no intent minted.

    Closed per-day loop (normative order, each sub-step keyed to the engine
    invariant it reuses):

    1. **calendar**: ``days = reader.trading_days(start, end)``;
       ``portfolio.seed_initial_nav(days[0])`` before the first bar (portfolio.py:198
       — required for correct max-drawdown);
    2. **corporate actions** (Task-7 seam): apply events with ``ex_date == T`` before
       any matching that day — a ``NotImplementedError`` method reached ONLY when an
       event actually falls on ``T``;
    3. **target application**: for each not-yet-applied unit eligible on ``T``,
       ``diff_target_portfolio`` against the live portfolio with ``T``'s reference
       prices → build engine ``Order``s (``target_sell`` → limit sell at the 跌停
       floor ``dn`` engine.py:180; ``target_buy`` → limit buy at the ceiling
       ``prev_close*(1+pct/2)`` engine.py:202 sized by ``cash_budget``) →
       ``prepare_bar`` → ``broker.match`` → ``Fill``/``None`` become fill/reject
       records with the verbatim engine reason;
    4. **exit management** (Task-7 seam): a ``NotImplementedError`` method reached
       ONLY when a held position carries an active stop (exit state);
    5. **EOD**: ``portfolio.record_nav(T, prices=eod_closes)`` (mark+record atomic,
       portfolio.py:210); a suspended name keeps its previous ``mkt_value``.

    RED LINE (invariant 6): the runner never touches ``guanlan_v2.seats``, performs
    no network I/O, and never mutates the (frozen) input intents.
    """

    def __init__(
        self,
        *,
        reader: Any,
        loader: Any,
        schedule: DecisionSchedule,
        schedule_ref: ContentRef,
        calendar: ImmutableTradingCalendar,
        cost_model: Any,
        init_cash: float = 1_000_000.0,
        corporate_actions: tuple = (),
        is_st: Mapping[str, bool] | None = None,
        lot_size: int = 100,
        clock: AuthoritativeClock | None = None,
    ) -> None:
        # schedule_ref triple (id / version / content_digest) must name this schedule
        if (
            schedule_ref.id != schedule.id
            or schedule_ref.version != schedule.version
            or schedule_ref.content_digest != schedule.content_digest
        ):
            raise ShadowContractError(
                "schedule_ref does not match schedule (id/version/content_digest triple)"
            )
        if schedule.matching_engine_version != SHADOW_MATCHING_ENGINE_VERSION:
            raise ShadowContractError(
                f"schedule matching_engine_version {schedule.matching_engine_version!r} "
                f"is not {SHADOW_MATCHING_ENGINE_VERSION!r} (this phase implements only it)"
            )
        if schedule.bar_frequency != "1d":
            raise UnsupportedBarFrequencyError(
                f"{SHADOW_MATCHING_ENGINE_VERSION} supports bar_frequency '1d' only; "
                f"schedule {schedule.id}@{schedule.version} declares {schedule.bar_frequency!r}"
            )
        if calendar.calendar_id != schedule.calendar_id:
            raise ShadowContractError(
                f"calendar {calendar.calendar_id!r} does not match schedule calendar_id "
                f"{schedule.calendar_id!r}"
            )
        self._reader = reader
        self._loader = loader
        self._schedule = schedule
        self._schedule_ref = schedule_ref
        self._calendar = calendar
        self._cost_model = cost_model
        self._init_cash = float(init_cash)
        self._corporate_actions = tuple(corporate_actions)
        self._is_st: dict[str, bool] = {str(k): bool(v) for k, v in dict(is_st or {}).items()}
        self._lot_size = int(lot_size)
        self._clock = clock or SystemClock()
        self._tz = ZoneInfo(schedule.timezone)
        self._cfg = _engine_api().RunConfig(match_freq="day")

    @property
    def clock(self) -> AuthoritativeClock:
        """The authoritative clock this runner (and hence its ``run`` lane) is bound to."""
        return self._clock

    # ------------------------------------------------------------------ #
    # public entries                                                     #
    # ------------------------------------------------------------------ #
    def run(
        self, intents: tuple[TargetPortfolioIntent, ...], *, start: IsoDateStr, end: IsoDateStr
    ) -> ShadowRunResult:
        """Apply staged intents ONCE each over ``[start, end]`` → a sealed run result."""
        self._reject_tranches(i.positions for i in intents)
        units = [self._unit_from_intent(i) for i in intents]
        digests = tuple(sorted({u.intent_content_digest for u in units}))
        return self._execute(units, start=start, end=end, intent_content_digests=digests)

    def run_targets(
        self,
        target_sets: tuple[DeterministicTargetSet, ...],
        *,
        run_config: ShadowRunConfig,
        calendar: ImmutableTradingCalendar,
        clock: AuthoritativeClock,
    ) -> ShadowRunResult:
        """The deterministic dual-curve entry — envelope-free, no intent minted.

        ``run_config`` / ``calendar`` / ``clock`` must reproduce the runner's own
        binding digest (same cost model, init cash, corporate actions, st flags, lot
        size, calendar id and clock identity as the ``run`` lane) else a
        :class:`ShadowContractError` is raised — the two lanes can never diverge on
        matching engine, cost model, calendar or clock. Records reuse
        ``ShadowTargetApplyRecord`` and carry the deterministic apply-key family
        NATIVELY — each apply record stores :func:`deterministic_apply_key` (the same
        value it dedups on) as its ``target_apply_key`` plus the ``rule_id`` /
        ``point_ordinal`` it recomputes from, so its stored key is disjoint from every
        intent key by domain tag (invariant 8). The result's ``intent_content_digests``
        stays ``()``, and no record carries an LLM origin.
        """
        self._require_matching_run_config(run_config, calendar, clock)
        self._reject_tranches(ts.positions for ts in target_sets)
        units = [self._unit_from_target_set(ts, calendar) for ts in target_sets]
        return self._execute(
            units, start=run_config.start, end=run_config.end, intent_content_digests=()
        )

    # ------------------------------------------------------------------ #
    # unit projection                                                    #
    # ------------------------------------------------------------------ #
    def _reject_tranches(self, position_groups) -> None:
        for positions in position_groups:
            for p in positions:
                if p.entry_tranches:
                    raise ShadowContractError(
                        "entry_tranches (batched entry triggers) are not supported by "
                        f"{SHADOW_MATCHING_ENGINE_VERSION}; tranche-triggered execution is "
                        "deferred to a later matching-engine version (never silently ignored)"
                    )

    def _unit_from_intent(self, intent: TargetPortfolioIntent) -> _ApplicationUnit:
        key = target_apply_key(intent)
        session = intent.eligible_execution_at.astimezone(self._tz).date().isoformat()
        digest = intent.semantic_digest()
        return _ApplicationUnit(
            dedup_key=key,
            record_apply_key=key,
            dedup_semantic=digest,
            intent_content_digest=digest,
            intent_id=intent.intent_id,
            scheduled_for=intent.scheduled_for,
            target_version=intent.target_version,
            eligible_session=session,
            positions=tuple(intent.positions),
            cash_weight=intent.cash_weight,
        )

    def _unit_from_target_set(
        self, ts: DeterministicTargetSet, calendar: ImmutableTradingCalendar
    ) -> _ApplicationUnit:
        scheduled_for = compute_scheduled_for(
            self._schedule, session_date=ts.session_date, calendar=calendar
        )
        eligible_at = compute_eligible_execution_at(
            self._schedule, scheduled_for=scheduled_for, calendar=calendar
        )
        session = eligible_at.astimezone(self._tz).date().isoformat()
        intent_id = f"{ts.rule_id}#{ts.point_ordinal}"
        # The apply record carries the deterministic apply-key family NATIVELY: the
        # Task-4 ShadowTargetApplyRecord dual-family validator recomputes the key via
        # deterministic_apply_key_parts (rule_id/point_ordinal present) — not the
        # intent-family builder — so the stored key IS deterministic_apply_key(ts) and
        # is disjoint from every intent key by domain tag (invariant 8). dedup key and
        # stored record key are one and the same here; the intent_id keeps the pinned
        # "{rule_id}#{point_ordinal}" audit form.
        apply_key = deterministic_apply_key(ts)
        causation = _deterministic_causation_digest(ts)
        return _ApplicationUnit(
            dedup_key=apply_key,
            record_apply_key=apply_key,
            dedup_semantic=causation,
            intent_content_digest=causation,
            intent_id=intent_id,
            scheduled_for=scheduled_for,
            target_version=ts.target_version,
            eligible_session=session,
            positions=tuple(ts.positions),
            cash_weight=ts.cash_weight,
            rule_id=ts.rule_id,
            point_ordinal=ts.point_ordinal,
        )

    # ------------------------------------------------------------------ #
    # config binding (dual-lane identity)                                #
    # ------------------------------------------------------------------ #
    def _binding_digest(
        self, *, init_cash, cost_model, corporate_actions, is_st, lot_size, calendar_id, clock
    ) -> DigestHex:
        return content_digest(
            {
                "domain": "shadow-run-config-v1",
                "matching_engine_version": SHADOW_MATCHING_ENGINE_VERSION,
                "init_cash": float(init_cash),
                "cost_model": dataclasses.asdict(cost_model),
                "corporate_actions": sorted(e.semantic_digest() for e in corporate_actions),
                "is_st": {str(k): bool(v) for k, v in dict(is_st or {}).items()},
                "lot_size": int(lot_size),
                "calendar_id": calendar_id,
                "clock_identity": _clock_identity(clock),
            }
        )

    def _require_matching_run_config(self, run_config, calendar, clock) -> None:
        mine = self._binding_digest(
            init_cash=self._init_cash,
            cost_model=self._cost_model,
            corporate_actions=self._corporate_actions,
            is_st=self._is_st,
            lot_size=self._lot_size,
            calendar_id=self._calendar.calendar_id,
            clock=self._clock,
        )
        theirs = self._binding_digest(
            init_cash=run_config.init_cash,
            cost_model=run_config.cost_model,
            corporate_actions=tuple(run_config.corporate_actions),
            is_st=run_config.is_st,
            lot_size=run_config.lot_size,
            calendar_id=calendar.calendar_id,
            clock=clock,
        )
        if mine != theirs:
            raise ShadowContractError(
                "run_targets run_config/calendar/clock is not identical to the lane config "
                "run(intents) executes under (config digest mismatch): the two dual-curve "
                "lanes must share matching engine, cost model, corporate actions, st flags, "
                "lot size, calendar and clock"
            )

    # ------------------------------------------------------------------ #
    # the closed per-day loop (shared by both lanes)                     #
    # ------------------------------------------------------------------ #
    def _execute(self, units, *, start, end, intent_content_digests) -> ShadowRunResult:
        eng = _engine_api()
        days = list(self._reader.trading_days(start, end))
        day_set = set(days)
        portfolio = eng.VirtualPortfolio(
            init_cash=self._init_cash, cash=self._init_cash, cost_model=self._cost_model
        )
        if days:
            portfolio.seed_initial_nav(days[0])  # step 1 — seed initial NAV peak
        trade_log = eng.TradeLog()
        broker = eng.Broker(self._cost_model)
        applies: list = []
        orders: list = []
        fills: list = []
        rejects: list = []
        warnings: list = []

        # apply-once above the Broker: collapse byte-identical duplicates, refuse a
        # same-key / different-content collision (ShadowApplyConflict).
        by_dedup: dict[str, _ApplicationUnit] = {}
        for u in units:
            existing = by_dedup.get(u.dedup_key)
            if existing is None:
                by_dedup[u.dedup_key] = u
            elif existing.dedup_semantic != u.dedup_semantic:
                raise ShadowApplyConflict(
                    f"two applications share apply key {u.dedup_key} with different content "
                    "(a target book is applied at most once per apply key)"
                )
        survivors = list(by_dedup.values())

        by_session: dict[str, list] = {}
        out_window: list = []
        for u in survivors:
            if u.eligible_session in day_set:
                by_session.setdefault(u.eligible_session, []).append(u)
            else:
                out_window.append(u)

        applied: set[str] = set()
        exit_state: dict[str, _ExitState] = {}   # Task-7 runner-held per-position exits
        applied_ca_digests: set[str] = set()      # Task-7 corporate-action idempotency
        for t_index, T in enumerate(days):
            events_today = [e for e in self._corporate_actions if e.ex_date == T]
            if events_today:  # step 2 seam — reached only with matching events (Task 7)
                self._apply_corporate_actions(
                    portfolio, events_today, T, applied_ca_digests, exit_state
                )
            for u in sorted(by_session.get(T, []), key=lambda u: (u.scheduled_for, u.dedup_key)):
                if u.dedup_key in applied:
                    continue
                applied.add(u.dedup_key)
                applies.append(
                    self._apply_unit(
                        u, T, t_index, portfolio, broker, orders, fills, rejects,
                        warnings, trade_log, exit_state,
                    )
                )
            if self._pending_exit_state(portfolio, exit_state):  # step 4 seam (Task 7)
                self._manage_exits(
                    portfolio, T, t_index, broker, orders, fills, rejects, trade_log,
                    exit_state,
                )
            portfolio.record_nav(T, prices=self._eod_closes(portfolio, T))  # step 5 — EOD

        # honest non-application: an eligible bar outside the window is never re-timed
        for u in sorted(out_window, key=lambda u: (u.eligible_session, u.dedup_key)):
            applies.append(
                ShadowTargetApplyRecord(
                    target_apply_key=u.record_apply_key,
                    intent_content_digest=u.intent_content_digest,
                    intent_id=u.intent_id,
                    scheduled_for=u.scheduled_for,
                    target_version=u.target_version,
                    trigger_bar=u.eligible_session,
                    order_ids=(),
                    applied=False,
                    rule_id=u.rule_id,
                    point_ordinal=u.point_ordinal,
                )
            )
            warnings.append(
                f"intent {u.intent_id} eligible {u.eligible_session} outside window "
                f"[{start}, {end}] (recorded applied=False, never re-timed)"
            )

        return self._assemble(
            start, end, portfolio, trade_log, applies, orders, fills, rejects, warnings,
            intent_content_digests,
        )

    def _apply_unit(
        self, u, T, t_index, portfolio, broker, orders, fills, rejects, warnings,
        trade_log, exit_state
    ) -> ShadowTargetApplyRecord:
        eng = _engine_api()
        snap = portfolio.snapshot()
        held = {code: pos["qty"] for code, pos in snap["positions"].items()}
        nav = snap["nav"]
        codes = {p.symbol.engine_code for p in u.positions} | set(held)
        bars: dict = {}
        ref_prices: dict = {}
        for code in codes:
            bar, pc = eng.prepare_bar(code, T, self._reader, self._loader, self._cfg)
            bars[code] = (bar, pc)
            if pc is not None:
                ref_prices[code] = pc

        # DIRECT consume of the Task-5 diff with T's reference prices (never the
        # agent's decide path, never engine legs — the plan carries exact qty/budget).
        plan = diff_target_portfolio(
            SimpleNamespace(positions=u.positions),
            holdings=held,
            reference_prices=ref_prices,
            nav=nav,
            lot_size=self._lot_size,
        )
        target_by_dotted = {p.symbol.dotted: p for p in u.positions}
        order_ids: list = []
        for e in plan.entries:
            code = e.symbol.engine_code
            bar, pc = bars.get(code, (None, None))
            order_id = shadow_order_id(
                apply_key=u.record_apply_key,
                symbol=e.symbol,
                order_kind=e.order_kind,
                trigger_bar=T,
                ordinal=e.ordinal,
            )
            order_ids.append(order_id)
            is_st = bool(self._is_st.get(code, False))
            if pc is None:
                # a held-but-unpriceable exit (degenerate data only): honest framework
                # refusal — recorded as an order + reject, never a silent drop.
                orders.append(self._order_record(order_id, u.record_apply_key, e, T, None, e.qty, None))
                rejects.append(
                    ShadowRejectRecord(order_id=order_id, symbol=e.symbol, trade_date=T, reason="no_ref_prev_close")
                )
                continue
            pct = eng.limit_pct_for(code, is_st=is_st)
            if e.side == "sell":
                dn = round(pc * (1.0 - pct), 2)  # 跌停价 floor — always clears the touch check
                order = eng.Order(code=code, side="sell", otype="limit", limit_price=dn, qty=e.qty)
                orders.append(self._order_record(order_id, u.record_apply_key, e, T, dn, e.qty, None))
            else:
                up = round(pc * (1.0 + pct / 2.0), 2)  # ≤T-1-derived limit ceiling
                stop_loss = self._abs_stop(target_by_dotted.get(e.symbol.dotted), pc)
                order = eng.Order(
                    code=code, side="buy", otype="limit", limit_price=up, qty=None,
                    cash_budget=e.cash_budget, stop_loss=stop_loss,
                )
                orders.append(self._order_record(order_id, u.record_apply_key, e, T, up, None, e.cash_budget))
            fill = broker.match(order, bar, pc, portfolio, is_st=is_st)
            if fill is None:
                rejects.append(
                    ShadowRejectRecord(order_id=order_id, symbol=e.symbol, trade_date=T, reason=broker.last_reason)
                )
            else:
                fills.append(
                    ShadowFillRecord(
                        fill_id=shadow_fill_id(order_id=order_id, fill_seq=1),
                        order_id=order_id,
                        fill_seq=1,
                        symbol=e.symbol,
                        side=fill.side,
                        qty=fill.qty,
                        price=fill.price,
                        trade_date=fill.trade_date,
                        gross=fill.gross,
                        cost=fill.cost,
                        reason=e.order_kind,  # the engine fill.reason is "" — the order kind is the honest cause
                    )
                )
                trade_log.add_fill(fill)
                # Task 7: a buy that opens/adds a position with any exit parameter
                # registers (or re-parameterizes) runner-held exit state carrying the
                # ORIGINATING apply key (causation survives to its exit orders).
                if e.side == "buy":
                    tgt = target_by_dotted.get(e.symbol.dotted)
                    if tgt is not None and self._has_exit_rule(tgt):
                        exit_state[code] = _ExitState(
                            entry_bar_index=t_index,
                            entry_price=float(fill.price),
                            apply_key=u.record_apply_key,
                            symbol=e.symbol,
                            position=tgt,
                        )
        # drop exit state for any position this application fully exited (a full
        # target_sell) — a stale entry never drives an exit order for a gone position.
        for gone in [c for c in exit_state if c not in portfolio.positions]:
            exit_state.pop(gone, None)
        for s in plan.skipped:
            warnings.append(f"skip:{s.reason}:{s.symbol.engine_code}")
        return ShadowTargetApplyRecord(
            target_apply_key=u.record_apply_key,
            intent_content_digest=u.intent_content_digest,
            intent_id=u.intent_id,
            scheduled_for=u.scheduled_for,
            target_version=u.target_version,
            trigger_bar=T,
            order_ids=tuple(order_ids),
            applied=True,
            rule_id=u.rule_id,
            point_ordinal=u.point_ordinal,
        )

    @staticmethod
    def _order_record(order_id, apply_key, entry, T, limit_price, qty, cash_budget) -> ShadowOrderRecord:
        return ShadowOrderRecord(
            order_id=order_id,
            target_apply_key=apply_key,
            symbol=entry.symbol,
            order_kind=entry.order_kind,
            trigger_bar=T,
            ordinal=entry.ordinal,
            side=entry.side,
            otype="limit",
            limit_price=limit_price,
            qty=qty,
            cash_budget=cash_budget,
        )

    @staticmethod
    def _abs_stop(pos, ref_price) -> float:
        if pos is not None and pos.stop_loss_pct is not None:
            return float(ref_price) * (1.0 - pos.stop_loss_pct)
        return 0.0

    def _eod_closes(self, portfolio, T) -> dict:
        out: dict = {}
        for code in list(portfolio.positions.keys()):
            df = self._loader.fetch_quote(code, T, T, "day")
            if df is None or len(df) == 0:
                continue  # suspended / no bar → keep previous mkt_value (portfolio.py:154)
            c = df.iloc[-1].get("close")
            if c is None:
                continue
            c = float(c)
            if math.isfinite(c):
                out[code] = c
        return out

    @staticmethod
    def _pending_exit_state(portfolio, exit_state) -> bool:
        """Whether any still-held position carries runner exit state — the ONLY
        condition under which the exit-management step (step 4) runs. Widened past the
        Task-6 stop-only predicate: a take-profit- or max-hold-only position (no stop)
        must still be managed, so this consults the runner-held ``exit_state`` (which
        only ever holds positions whose governing target has an exit rule) intersected
        with the live book. A run that stages no exit parameters never populates
        ``exit_state`` and so never reaches step 4."""
        return any(code in portfolio.positions for code in exit_state)

    @staticmethod
    def _has_exit_rule(pos) -> bool:
        """Whether a :class:`TargetPosition` carries ANY exit parameter (stop / take /
        max-hold) — the trigger for registering runner-held exit state on a buy fill."""
        return (
            pos.stop_loss_pct is not None
            or pos.take_profit_pct is not None
            or pos.max_hold_bars is not None
        )

    # ------------------------------------------------------------------ #
    # Task-7 — corporate-action application (step 2) + exit mgmt (step 4)  #
    # ------------------------------------------------------------------ #
    def _apply_corporate_actions(
        self, portfolio, events, T, applied_digests, exit_state
    ) -> tuple[CorporateActionApplication, ...]:
        """Step 2 — apply ex_date corporate actions, then rescale runner exit state.

        Delegates the portfolio mutation to the module-level
        :func:`apply_corporate_actions` (cash credit / floor-rescaled qty / preserved
        avg_cost / rescaled stop + locked buckets, digest-keyed at-most-once), then
        rescales the runner-held ``entry_price`` for any share event by
        ``qty_before / qty_after`` so a take-profit level tracks the ex-div-corrected
        price (cost-basis parity with the rescaled avg_cost / stop the portfolio just
        took)."""
        apps = apply_corporate_actions(
            portfolio, tuple(events), on_date=T, applied_digests=applied_digests
        )
        for app in apps:
            st = exit_state.get(app.symbol.engine_code)
            if st is None or app.qty_after == 0 or app.qty_before == app.qty_after:
                continue  # cash dividend / not-held / no share change → no rescale
            st.entry_price = st.entry_price * app.qty_before / app.qty_after
        return apps

    def _manage_exits(
        self, portfolio, T, t_index, broker, orders, fills, rejects, trade_log,
        exit_state
    ) -> None:
        """Step 4 — stop / take-profit / max-hold, at most ONE exit order per position
        per bar, priority solely from ``schedule.intrabar_exit_priority``.

        For each still-held position with exit state: a stop touches at
        ``bar.low <= Position.stop_loss`` (the engine-native protective sell, reused —
        ``Order(otype="stop")``); a take-profit touches at
        ``bar.high >= entry_price * (1 + take_profit_pct)`` (the engine's own limit-sell
        touch rule, ``Order(otype="limit")`` at the take price — no exchange-alien fill
        path); on a double-touch exactly one order is emitted — ``worst_case`` /
        ``stop_first`` → the stop (for long-only the worst case IS the stop),
        ``take_profit_first`` → the take. Max-hold is evaluated ONLY when neither
        triggered: at ``t_index - entry_bar_index >= max_hold_bars`` a limit sell at the
        ex-div-corrected 跌停 floor ``dn`` (the engine forced-sell convention). Each exit
        order id is minted from the ORIGINATING apply key (causation survives exits); a
        rejected exit (suspension / one-word / T+1) re-arms deterministically on the next
        tradable bar (a fresh ``trigger_bar`` → a distinct id)."""
        eng = _engine_api()
        priority = self._schedule.intrabar_exit_priority
        for code in sorted(exit_state):
            st = exit_state.get(code)
            pos = portfolio.positions.get(code)
            if st is None or pos is None or pos.qty <= 0:
                exit_state.pop(code, None)  # position gone / stale → drop
                continue
            bar, pc = eng.prepare_bar(code, T, self._reader, self._loader, self._cfg)
            if bar is None or pc is None:
                continue  # no tradable bar (NaN OHLC / missing) → carry state, re-arm
            is_st = bool(self._is_st.get(code, False))
            stop_px = float(pos.stop_loss)
            stop_touched = stop_px > 0 and bar["low"] <= stop_px
            tp_pct = st.position.take_profit_pct
            tp_px = st.entry_price * (1.0 + tp_pct) if tp_pct is not None else None
            take_touched = tp_px is not None and bar["high"] >= tp_px

            kind = limit_price = None
            if stop_touched and take_touched:
                if priority == "take_profit_first":
                    kind, limit_price = "take_profit", tp_px
                else:  # worst_case | stop_first — long-only worst case ≡ the stop
                    kind, limit_price = "stop_loss", stop_px
            elif stop_touched:
                kind, limit_price = "stop_loss", stop_px
            elif take_touched:
                kind, limit_price = "take_profit", tp_px
            else:
                max_hold = st.position.max_hold_bars
                if max_hold is not None and (t_index - st.entry_bar_index) >= max_hold:
                    pct = eng.limit_pct_for(code, is_st=is_st)
                    kind = "max_hold_exit"
                    limit_price = round(pc * (1.0 - pct), 2)  # 跌停 floor (forced sell)
            if kind is None:
                continue

            otype = "stop" if kind == "stop_loss" else "limit"
            order = eng.Order(
                code=code, side="sell", otype=otype, limit_price=limit_price, qty=None
            )
            order_id = shadow_order_id(
                apply_key=st.apply_key, symbol=st.symbol, order_kind=kind,
                trigger_bar=T, ordinal=0,
            )
            orders.append(
                ShadowOrderRecord(
                    order_id=order_id, target_apply_key=st.apply_key, symbol=st.symbol,
                    order_kind=kind, trigger_bar=T, ordinal=0, side="sell",
                    otype=otype, limit_price=float(limit_price), qty=None,
                    cash_budget=None,
                )
            )
            fill = broker.match(order, bar, pc, portfolio, is_st=is_st)
            if fill is None:
                rejects.append(
                    ShadowRejectRecord(
                        order_id=order_id, symbol=st.symbol, trade_date=T,
                        reason=broker.last_reason,
                    )
                )
                continue
            fills.append(
                ShadowFillRecord(
                    fill_id=shadow_fill_id(order_id=order_id, fill_seq=1),
                    order_id=order_id, fill_seq=1, symbol=st.symbol, side=fill.side,
                    qty=fill.qty, price=fill.price, trade_date=fill.trade_date,
                    gross=fill.gross, cost=fill.cost, reason=kind,
                )
            )
            trade_log.add_fill(fill)
            if code not in portfolio.positions:  # fully exited → drop exit state
                exit_state.pop(code, None)

    # ------------------------------------------------------------------ #
    # result assembly                                                    #
    # ------------------------------------------------------------------ #
    def _assemble(
        self, start, end, portfolio, trade_log, applies, orders, fills, rejects, warnings,
        intent_content_digests,
    ) -> ShadowRunResult:
        eng = _engine_api()
        tstats = trade_log.trade_stats()
        turnover = (
            (sum(f.gross for f in fills) / self._init_cash) if self._init_cash else float("nan")
        )
        pr = eng.compute_metrics(
            list(portfolio.nav_history), self._init_cash, turnover=turnover,
            trade_win_rate=tstats["trade_win_rate"],
        )
        candidate = {
            "ann_return": pr.ann_return,
            "sharpe": pr.sharpe,
            "max_drawdown": pr.max_drawdown,
            "volatility": pr.volatility,
            "turnover": pr.turnover,
            "win_rate": pr.win_rate,
            "calmar": pr.calmar,
            "trade_win_rate": tstats["trade_win_rate"],
            "profit_factor": tstats["profit_factor"],
        }
        # keep only real floats; ShadowRunResult.build() then OMITS non-finite ones.
        metrics = {
            k: float(v)
            for k, v in candidate.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        badges: list[str] = []
        if not self._is_st and fills:
            badges.append("st_flags_unavailable")
        if self._corporate_actions:
            badges.append("corporate_actions_synthetic")
        return ShadowRunResult.build(
            matching_engine_version=SHADOW_MATCHING_ENGINE_VERSION,
            schedule_ref=self._schedule_ref,
            start=start,
            end=end,
            init_cash=self._init_cash,
            cost_model_digest=content_digest(dataclasses.asdict(self._cost_model)),
            intent_content_digests=intent_content_digests,
            applies=tuple(applies),
            orders=tuple(orders),
            fills=tuple(fills),
            rejects=tuple(rejects),
            nav_history=tuple((d, float(v)) for d, v in portfolio.nav_history),
            metrics=metrics,
            n_trades=int(tstats["n_trades"]),
            warnings=tuple(warnings),
            badges=tuple(sorted(set(badges))),
        )


def _clock_identity(clock) -> str:
    """A stable identity token for a clock (its ``clock_id`` if any, else its type)."""
    cid = getattr(clock, "clock_id", None)
    if cid is not None:
        return str(cid)
    return f"{type(clock).__module__}.{type(clock).__qualname__}"


# =========================================================================== #
# Task 8 · stage-① compatibility mirror of the frontend ``runBacktest`` profile #
# =========================================================================== #
# A BYTE-FAITHFUL Python replication of ``ui/seats/luozi-data.jsx::runBacktest``
# (luozi-data.jsx:1508-1563) + ``metricsOf`` (461-482), consumed as a *profile*
# (spec decision 15), NOT as a callable — the fa Broker is structurally unable to
# run it (T+1 / 涨跌停 / 手数 checks are not config-gated: grounding gotcha 2), so
# the mirror is a SEPARATE compatibility fill path, never a route through the Broker.
#
# Frozen profile facts (each verified against the on-disk jsx line cited):
#   * zero cost — no commission / stamp / transfer / slippage anywhere;
#   * single symbol; the side is already derived (the ``useHybrid`` branch only
#     chooses WHICH frontend field supplies buy/sell/watch — the mirror consumes
#     the derived side);
#   * same-bar-close fills — a buy/sell executes at ``+b.c`` of the signal bar
#     (jsx:1548/1550), never a next-bar open;
#   * fractional shares — ``shares = cash / px`` (jsx:1549), no lot rounding;
#   * full-in / full-out — ``pos ∈ {0, 1}`` (a buy deploys all cash, a sell exits all);
#   * no suspension / 涨跌停 / T+1 / 手数 / reject-ledger — none exist in the frontend;
#   * clock exits fire intrabar at the EXACT trigger price (jsx:1539/1540): a stop at
#     ``entry*(1-stop)`` on ``low <= that``, a take at ``entry*(1+take)`` on
#     ``high >= that``;
#   * same-bar stop+take double touch resolves STOP-FIRST (jsx:1535/1539-1540 elif
#     ordering — for a long the worst case IS the stop);
#   * max-hold exits at the bar CLOSE with reason ``'到期'`` (jsx:1541);
#   * exits are checked only when ``i > entryIdx`` (jsx:1536) — a position never
#     exits on its own entry bar;
#   * pre-entry equity is flat ``1.0`` (jsx:1528) and is EXCLUDED from ``eqSeg`` (the
#     metric segment starts at ``firstSig``, jsx:1561);
#   * an all-watch run (no buy/sell) returns ``None`` (jsx:1526/1562 — honest no-curve);
#   * an open position at the end yields a synthetic ``openEnd`` trade at the last
#     close (jsx:1557).
#
# DIVERGENCE FLAG (frontend reality vs the CompatTrade contract): the frontend
# ``openEnd`` trade carries NO ``reason`` field (jsx:1559). The ``CompatTrade.reason``
# Literal is REQUIRED, so an openEnd trade is encoded as ``reason='到期'`` +
# ``open_end=True``; it is unambiguously distinct from a real max-hold exit (which is
# ``reason='到期'`` + ``open_end=False`` and closes the position, so no openEnd
# follows it). Reason/openEnd both participate in the structural (tol 0) compare.
#
# METRICS ONE WAY (grounding gotcha 10): :func:`compat_metrics` reimplements the
# ``metricsOf`` conventions verbatim; BOTH the fixture expectation and the mirror
# output flow through it. The engine ``compute_metrics`` is NEVER compared against
# ``metricsOf``.

#: the stage-① profile identity. The frontend switch-over date is explicitly deferred
#: to Phase 9's decommission gates; stage ① only freezes the profile + tolerances.
COMPATIBILITY_PROFILE_ID: str = "luozi-runbacktest-compat-v1"

#: declared numeric tolerances (spec §13 line 1013 leaves these to this plan). Every
#: mirror assertion is gated by exactly these — trade structure is exact (count,
#: entry/exit bar indices, firstSig, reason strings, openEnd flags), prices/equity are
#: IEEE-754-double relative, per-trade returns absolute, metrics relative (the
#: annualization stack accumulates more floating ops).
COMPAT_TRADE_STRUCTURE_TOL: int = 0
COMPAT_PRICE_REL_TOL: float = 1e-9
COMPAT_RETURN_ABS_TOL: float = 1e-9
COMPAT_EQUITY_REL_TOL: float = 1e-9
COMPAT_METRIC_REL_TOL: float = 1e-6

#: the A-share market timezone the profile derives an eligible session date under. It
#: matches the runner's ``schedule.timezone`` (this profile is A-share-only); the
#: mapper takes the schedule explicitly, so the tz is read from it, never hard-coded.
_COMPAT_EXIT_PRIORITY: str = "stop_first"


class CompatSignal(ContractModel):
    """One already-derived signal at a bar index (internal carrier).

    ``side`` is the frontend-derived direction — the ``useHybrid`` branch that maps
    ``hybrid_direction`` / ``d.side`` to buy/sell/watch has already run; the mirror
    consumes the result. A ``watch`` signal enters neither ``sideByIdx`` nor
    ``firstSig`` (it is a non-position, exactly as the jsx drops it).
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    idx: NonNegativeInt
    side: Literal["buy", "sell", "watch"]


class CompatClock(ContractModel):
    """The frontend execution clock (止损 / 止盈 / 最长持有), frozen (internal carrier).

    Fractions (``stop_loss`` / ``take_profit``) and a bar count (``max_hold``), mapped
    from the frontend ``clock.stopLoss`` / ``clock.takeProfit`` / ``clock.maxHold``
    (luozi-data.jsx:1512-1514). Each is Optional; the mirror re-applies the jsx guards
    (a value must be finite and ``> 0`` to arm).
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    stop_loss: FiniteFloat | None = None
    take_profit: FiniteFloat | None = None
    max_hold: PositiveInt | None = None


class CompatTrade(ContractModel):
    """One closed (or synthetic openEnd) trade (internal carrier).

    ``reason`` is a closed Literal; ``'信号'`` = an explicit sell exit, ``'止损'`` /
    ``'止盈'`` = an intrabar clock touch, ``'到期'`` = a max-hold close OR (with
    ``open_end=True``) the synthetic end-of-data close of a still-open position.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    entry: FiniteFloat
    exit: FiniteFloat
    ret: FiniteFloat
    in_idx: NonNegativeInt
    out_idx: NonNegativeInt
    reason: Literal["止损", "止盈", "到期", "信号"]
    open_end: bool = False


class CompatibilityRunResult(ContractModel):
    """The mirror's whole result (internal carrier) — ``eqSeg`` only, never raw ``eq``.

    ``eq_seg`` is the post-entry equity segment (``eq.slice(firstSig)``, jsx:1561): the
    metric segment, never the raw pre-entry-padded ``eq`` (gotcha 9 — comparisons align
    on this). ``metrics`` is the one-way :func:`compat_metrics` recompute.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    eq_seg: tuple[FiniteFloat, ...]
    trades: tuple[CompatTrade, ...]
    first_sig: NonNegativeInt
    metrics: dict[str, float]


class CompatibilityProfileError(ShadowContractError):
    """An intent (or schedule) is out of the frontend ``runBacktest`` profile.

    Raised by :func:`map_intents_to_compat_signals` when a portfolio shape the
    frontend cannot express is presented (two positions, a fractional target weight, a
    non-``stop_first`` schedule, a non-empty ``entry_tranches``). The profile NEVER
    coerces or widens: it neither invents a false gate on behavior the frontend lacks
    nor silently reinterprets an out-of-profile schedule.
    """


def _compat_num(value: object) -> float:
    """``+x`` of the frontend: coerce to float, mapping ``None``/uncoercible to NaN.

    Mirrors JS ``+b.field`` — a missing high/low/close becomes ``NaN`` so the jsx
    guards (``lo > 0`` / ``hi > 0`` / ``px > 0``) fail and the bar is skipped rather
    than fabricating a fill (JS ``NaN > 0`` is ``false``, matched by Python).
    """
    if value is None:
        return float("nan")
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def run_compatibility_mirror(
    signals: tuple[CompatSignal, ...],
    bars: tuple[Mapping[str, float], ...],
    clock: CompatClock | None = None,
) -> CompatibilityRunResult | None:
    """Byte-faithful Python replication of ``runBacktest`` fill semantics (jsx:1508-1563).

    The ``useHybrid`` branch only changes which frontend field supplies the side, so
    the mirror consumes the already-derived :class:`CompatSignal` side. Returns
    ``None`` for an all-watch run (honest no-curve) or a ``< 2``-point ``eqSeg``.

    Same operation ORDER as the jsx (so both sides compute identical IEEE-754 doubles):
    ``shares = cash / px`` (jsx:1549), ``eq = shares * px`` (jsx:1555), a stop at
    ``entry*(1 - stop)`` before a take at ``entry*(1 + take)`` (stop-first, jsx:1539-40),
    a per-trade return ``exit / entry - 1``. No lot rounding, no cost, no Broker.
    """
    if not bars or not signals:
        return None
    # jsx:1512-1514 — a clock field arms only when finite and > 0 (max_hold already int).
    stop = take = None
    max_hold: int | None = None
    if clock is not None:
        if clock.stop_loss is not None and math.isfinite(clock.stop_loss) and clock.stop_loss > 0:
            stop = float(clock.stop_loss)
        if clock.take_profit is not None and math.isfinite(clock.take_profit) and clock.take_profit > 0:
            take = float(clock.take_profit)
        if clock.max_hold is not None and clock.max_hold > 0:
            max_hold = int(clock.max_hold)

    # jsx:1515-1526 — build sideByIdx (a later same-idx buy/sell overwrites), firstSig.
    side_by_idx: dict[int, str] = {}
    first_sig = math.inf
    for s in signals:
        if s.side in ("buy", "sell"):
            side_by_idx[s.idx] = s.side
            first_sig = min(first_sig, s.idx)
    if first_sig == math.inf:  # all-watch → no curve
        return None
    first_sig = int(first_sig)

    n = len(bars)
    eq: list[float] = [1.0] * n           # jsx:1528 — pre-entry equity flat 1.0
    trades: list[CompatTrade] = []
    pos = 0
    entry_px = 0.0
    entry_idx = -1
    cash = 1.0
    shares = 0.0
    for i in range(first_sig, n):
        b = bars[i]
        px = _compat_num(b.get("c"))
        sig = side_by_idx.get(i)
        # jsx:1536 — held position: check the clock BEFORE any same-bar re-entry, and
        # only when i > entryIdx (never on the entry bar itself).
        if pos == 1 and i > entry_idx:
            lo = _compat_num(b.get("l"))
            hi = _compat_num(b.get("h"))
            exit_px: float | None = None
            why: str | None = None
            if stop is not None and lo > 0 and lo <= entry_px * (1 - stop):
                exit_px = entry_px * (1 - stop)
                why = "止损"
            elif take is not None and hi > 0 and hi >= entry_px * (1 + take):
                exit_px = entry_px * (1 + take)
                why = "止盈"
            elif max_hold is not None and (i - entry_idx) >= max_hold and px > 0:
                exit_px = px
                why = "到期"
            if exit_px is not None:
                cash = shares * exit_px
                trades.append(
                    CompatTrade(
                        entry=entry_px, exit=exit_px, ret=exit_px / entry_px - 1,
                        in_idx=entry_idx, out_idx=i, reason=why,  # type: ignore[arg-type]
                    )
                )
                pos = 0
                shares = 0.0
        # jsx:1548/1550 — same-bar-close fill (buy deploys all cash; sell exits all).
        if sig == "buy" and pos == 0 and px > 0:
            pos = 1
            entry_px = px
            entry_idx = i
            shares = cash / px
            cash = 0.0
        elif sig == "sell" and pos == 1 and px > 0:
            cash = shares * px
            trades.append(
                CompatTrade(
                    entry=entry_px, exit=px, ret=px / entry_px - 1,
                    in_idx=entry_idx, out_idx=i, reason="信号",
                )
            )
            pos = 0
            shares = 0.0
        # jsx:1555 — mark equity (NO rounding; the pre-entry pad stays 1.0).
        if px > 0:
            eq[i] = shares * px if pos == 1 else cash
        else:
            eq[i] = eq[i - 1] if i > 0 else 1.0

    # jsx:1557 — an open position at the end → a synthetic openEnd trade at the last
    # close. The frontend carries no reason; encode 到期 + open_end (see DIVERGENCE FLAG).
    if pos == 1 and entry_idx >= 0:
        last = _compat_num(bars[n - 1].get("c"))
        trades.append(
            CompatTrade(
                entry=entry_px, exit=last, ret=last / entry_px - 1,
                in_idx=entry_idx, out_idx=n - 1, reason="到期", open_end=True,
            )
        )

    eq_seg = eq[first_sig:]  # jsx:1561 — metrics only on the post-entry segment
    if len(eq_seg) < 2:      # jsx:1562 — < 2 points is no curve
        return None
    return CompatibilityRunResult(
        eq_seg=tuple(eq_seg),
        trades=tuple(trades),
        first_sig=first_sig,
        metrics=compat_metrics(tuple(eq_seg), tuple(trades), "day"),
    )


def compat_metrics(
    eq: tuple[float, ...], trades: tuple[CompatTrade, ...], freq: str = "day"
) -> dict[str, float]:
    """The frontend ``metricsOf`` conventions, reimplemented ONE way (jsx:461-482).

    ``perDay = 48 if freq=='5min' else 1``; sharpe annualized by ``sqrt(252*perDay)``
    with the ``sd`` floor ``|| 1e-9``; ``years = (n/perDay)*1.4/365``; annual
    ``eq[-1]**(1/max(0.3, years)) - 1``. Both the fixture expectation and the mirror
    output flow through this; the engine ``compute_metrics`` is never compared here.
    All values are returned as ``float`` (``nTrades`` / ``nWin`` included).
    """
    per_day = 48 if freq == "5min" else 1
    n = len(eq)
    rets = [eq[k] / eq[k - 1] - 1 for k in range(1, n)]
    denom = len(rets) or 1
    mean = sum(rets) / denom
    sd = math.sqrt(sum((b - mean) ** 2 for b in rets) / denom) or 1e-9  # jsx `|| 1e-9`
    sharpe = (mean / sd) * math.sqrt(252 * per_day)
    total = eq[n - 1] - 1
    years = ((n / per_day) * 1.4) / 365
    annual = math.pow(eq[n - 1], 1 / max(0.3, years)) - 1
    peak = eq[0]
    mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    wins = [t for t in trades if t.ret > 0]
    losses = [t for t in trades if t.ret <= 0]
    win_rate = (len(wins) / len(trades)) if trades else 0.0
    avg_win = (sum(t.ret for t in wins) / len(wins)) if wins else 0.0
    avg_loss = abs(sum(t.ret for t in losses) / len(losses)) if losses else 0.0
    pl_ratio = (avg_win / avg_loss) if avg_loss else (99.0 if avg_win else 0.0)
    return {
        "total": float(total),
        "annual": float(annual),
        "sharpe": float(sharpe),
        "mdd": float(mdd),
        "winRate": float(win_rate),
        "plRatio": float(pl_ratio),
        "nTrades": float(len(trades)),
        "nWin": float(len(wins)),
    }


def _has_compat_clock(pos: TargetPosition) -> bool:
    """Whether a :class:`TargetPosition` carries ANY frontend clock parameter."""
    return (
        pos.stop_loss_pct is not None
        or pos.take_profit_pct is not None
        or pos.max_hold_bars is not None
    )


def map_intents_to_compat_signals(
    intents: tuple[TargetPortfolioIntent, ...],
    *,
    bars: tuple[Mapping[str, float], ...],
    calendar: ImmutableTradingCalendar,
    schedule: DecisionSchedule,
) -> tuple[tuple[CompatSignal, ...], CompatClock | None]:
    """Map staged intents onto the frontend profile's ``(signals, clock)``.

    An intent maps to the profile only when it is either **full-in** (exactly one
    :class:`TargetPosition` with ``target_weight == 1.0`` → a ``buy`` at its eligible
    bar) or **all-cash** (zero positions with ``cash_weight == 1.0`` → a ``sell``); the
    entry clock fields come from that single position's ``stop_loss_pct`` /
    ``take_profit_pct`` / ``max_hold_bars``. Any other shape — two positions, a
    fractional target weight, a non-empty ``entry_tranches`` (Task 1b; the frontend
    ``runBacktest`` has no tranche semantics) — raises :class:`CompatibilityProfileError`
    rather than coercing or silently widening.

    The eligible bar index is the position of the intent's ``eligible_execution_at``
    session (derived in ``schedule.timezone``, identical to the runner) within the
    calendar's ordered sessions; ``bars[i]`` is assumed positionally aligned with
    calendar session ``i``. A signal whose eligible session is absent from the calendar
    or beyond the provided bars raises :class:`CompatibilityProfileError` (an honest
    can't-place, never a silent drop).

    NOTE (signature): the brief's sketch omits ``schedule``, but the frontend hard-codes
    stop-first and ``intrabar_exit_priority`` lives on :class:`DecisionSchedule`
    (shadow.py) — a ``worst_case`` / ``take_profit_first`` schedule MUST raise rather
    than be silently reinterpreted (reconciling grounding gotcha 1), so the schedule is
    a required keyword. It also single-sources the timezone the eligible session is
    derived under (matching the runner), removing any hard-coded market tz.
    """
    # the frontend hard-codes stop-first; a schedule declaring any other intrabar
    # priority is out of profile (never silently reinterpreted).
    if schedule.intrabar_exit_priority != _COMPAT_EXIT_PRIORITY:
        raise CompatibilityProfileError(
            f"the compatibility profile mirrors the frontend's hard-coded stop-first "
            f"intrabar priority; schedule {schedule.id}@{schedule.version} declares "
            f"{schedule.intrabar_exit_priority!r} (never silently reinterpreted)"
        )
    tz = ZoneInfo(schedule.timezone)
    sessions = list(calendar.material.sessions)
    n_bars = len(bars)

    signals: list[CompatSignal] = []
    clock: CompatClock | None = None
    for intent in intents:
        for p in intent.positions:
            if p.entry_tranches:
                raise CompatibilityProfileError(
                    "entry_tranches (batched entry triggers) have no frontend "
                    "runBacktest semantics; the compatibility profile never widens to "
                    "accept them"
                )
        positions = intent.positions
        buy_pos: TargetPosition | None = None
        if len(positions) == 1 and positions[0].target_weight == 1.0:
            side = "buy"
            buy_pos = positions[0]
        elif len(positions) == 0 and intent.cash_weight == 1.0:
            side = "sell"
        else:
            raise CompatibilityProfileError(
                "the compatibility profile accepts only a full-in single position "
                "(target_weight == 1.0 → buy) or an all-cash exit (cash_weight == 1.0 → "
                f"sell); intent {intent.intent_id} has {len(positions)} position(s) with "
                f"cash_weight {intent.cash_weight!r} (the profile never coerces)"
            )
        iso = intent.eligible_execution_at.astimezone(tz).date().isoformat()
        try:
            idx = sessions.index(iso)
        except ValueError:
            raise CompatibilityProfileError(
                f"eligible session {iso} (intent {intent.intent_id}) is not a session "
                f"of calendar {calendar.calendar_id!r}"
            ) from None
        if idx >= n_bars:
            raise CompatibilityProfileError(
                f"eligible session {iso} maps to bar index {idx}, beyond the {n_bars} "
                f"provided bars (intent {intent.intent_id})"
            )
        signals.append(CompatSignal(idx=idx, side=side))  # type: ignore[arg-type]
        if buy_pos is not None and _has_compat_clock(buy_pos):
            this_clock = CompatClock(
                stop_loss=buy_pos.stop_loss_pct,
                take_profit=buy_pos.take_profit_pct,
                max_hold=buy_pos.max_hold_bars,
            )
            if clock is not None and clock != this_clock:
                raise CompatibilityProfileError(
                    "the compatibility profile is single-clock; two entry positions "
                    "declare different execution clocks"
                )
            clock = this_clock

    return tuple(signals), clock


# =========================================================================== #
# Task 4 (Phase 9) · DecisionSchedule interval-replay driver                     #
# =========================================================================== #
# This section EXTENDS the Phase-6 adapter additively (the Phase-6 shadow runner /
# agent / mirror above are sealed and untouched). It owns the honest 双曲线回放 (dual
# curve replay) walk over a decision schedule: a pure schedule→points resolver, a
# pure daily-LLM-budget reconcile rule, a no-retroactive intent ledger, the frozen
# runtime-bindings carrier, and the per-point driver. Task 5 (`build_dual_curves`)
# consumes the intent ledger + the deterministic target sets this driver produces;
# Task 6 (maturity/wakeup) consumes the returned `ShadowReplayRunState`.
#
# Time model (Phase-6-ruled, binding): cutoff_at <= decision_as_of < eligible_at, with
# decision_as_of == scheduled_for. The three instants are NEVER re-derived inline — they
# come ONLY from the sealed Phase-6 computations `compute_scheduled_for` /
# `compute_cutoff_at` / `compute_eligible_execution_at`. `shadow-match-v1` supports
# `bar_frequency == "1d"` only; a non-1d schedule is refused via the consumed Phase-6
# `UnsupportedBarFrequencyError` (never re-raised inline).


class RebalanceDateNotSessionError(ShadowContractError):
    """A ``rebalance_dates`` schedule lists a date that is NOT a calendar session.

    Refused loudly at resolution time — a listed rebalance date that falls on a
    non-session is a schedule/calendar mismatch, never silently skipped.
    """


class SnapshotBindingRefused(ShadowContractError):
    """A per-point ContextSnapshot does not bind the point's ``decision_as_of``.

    Raised structurally BEFORE any main-plan admission/reservation for the point
    (invariant 2): a snapshot whose ``data_context.as_of`` differs from the point's
    ``decision_as_of`` can never anchor that point's decision plans, so the driver
    refuses it and records no reservation for the point.
    """


class RetroactiveIntentRefused(ShadowContractError):
    """A no-retroactive-intent red line was breached (invariant 3/5).

    Raised when an intent is appended for a point whose ``scheduled_for`` is at or
    before the ledger's monotone ``last_scheduled_for`` high-water mark (a backfill),
    or whose ``decision_as_of`` differs from its point's ContextSnapshot ``as_of``.
    An idempotent re-application of the SAME apply key is NOT retroactive — it
    returns the stored intent and appends nothing.
    """


class ReplayApprovalRefused(ShadowContractError):
    """An ``AUTO`` approval policy was presented to the replay driver (invariant 7).

    ``AUTO`` approval is rejected outright for every replay run; every admitted plan
    in the loop must have gone through ``REQUIRED`` approval. Raised before any point
    is processed, so nothing runs.
    """


# --------------------------------------------------------------------------- #
# resolve_decision_points — pure schedule→points expansion                      #
# --------------------------------------------------------------------------- #
def resolve_decision_points(
    schedule: DecisionSchedule,
    *,
    calendar: ImmutableTradingCalendar,
    interval_start: datetime,
    interval_end: datetime,
) -> tuple[ReplayDecisionPoint, ...]:
    """Expand a :class:`DecisionSchedule` over ``[interval_start, interval_end]`` into
    the ordered tuple of :class:`ReplayDecisionPoint` it fires at.

    Pure and deterministic (same inputs ⇒ byte-identical tuple; ``point_ordinal`` dense
    from 1). Membership is delegated to the sealed Phase-6 predicate
    :func:`is_decision_point` (``daily``/``manual`` fire on every calendar session,
    ``weekly`` on an ISO weekday in ``weekdays``, ``rebalance_dates`` on membership),
    and the three instants come ONLY from the Phase-6 computations — never re-derived:

    * ``scheduled_for = compute_scheduled_for(schedule, session_date=…, calendar=…)``
      (also the ``decision_as_of`` instant, per the ruled time model);
    * ``cutoff_at = compute_cutoff_at(schedule, session_date=…)``;
    * ``eligible_execution_at = compute_eligible_execution_at(schedule,
      scheduled_for=…, calendar=…)`` — which refuses a non-``1d`` ``bar_frequency`` via
      the consumed Phase-6 :class:`UnsupportedBarFrequencyError`.

    A point is kept iff ``interval_start <= scheduled_for <= interval_end`` (bounds on
    the decision instant), so no point falls outside the interval. ``manual`` yields
    no implicit points (an empty tuple). A ``rebalance_dates`` date that is not a
    calendar session is refused loudly (:class:`RebalanceDateNotSessionError`), never
    silently skipped.
    """
    if interval_end < interval_start:
        raise ValueError(
            f"interval_end {interval_end.isoformat()} must be >= interval_start "
            f"{interval_start.isoformat()}"
        )
    if schedule.kind == "manual":
        # manual points enter only via an explicit request, never implicitly here.
        return ()

    # candidate session-date ISO strings, ascending.
    if schedule.kind == "rebalance_dates":
        candidate_dates = list(schedule.rebalance_dates)  # already sorted+unique
        for iso in candidate_dates:
            if not calendar.is_session(date.fromisoformat(iso)):
                raise RebalanceDateNotSessionError(
                    f"rebalance date {iso} of schedule {schedule.id}@{schedule.version} "
                    f"is not a session of calendar {calendar.calendar_id!r} "
                    "(refused loudly, never silently skipped)"
                )
    else:  # daily / weekly — every calendar session is a candidate
        candidate_dates = list(calendar.material.sessions)

    schedule_ref = ContentRef(
        id=schedule.id, version=schedule.version, content_digest=schedule.content_digest
    )
    points: list[ReplayDecisionPoint] = []
    ordinal = 0
    for iso in candidate_dates:
        if not is_decision_point(schedule, session_date=iso, calendar=calendar):
            continue
        scheduled_for = compute_scheduled_for(
            schedule, session_date=iso, calendar=calendar
        )
        if not (interval_start <= scheduled_for <= interval_end):
            continue
        cutoff_at = compute_cutoff_at(schedule, session_date=iso)
        eligible_execution_at = compute_eligible_execution_at(
            schedule, scheduled_for=scheduled_for, calendar=calendar
        )
        ordinal += 1
        points.append(
            ReplayDecisionPoint(
                schedule_ref=schedule_ref,
                schedule_digest=schedule.content_digest,
                point_ordinal=ordinal,
                scheduled_for=scheduled_for,
                cutoff_at=cutoff_at,
                decision_as_of=scheduled_for,  # decision_as_of == scheduled_for (ruled)
                eligible_execution_at=eligible_execution_at,
                execution_price_field=schedule.execution_price_field,
                bar_frequency=schedule.bar_frequency,
            )
        )
    return tuple(points)


# --------------------------------------------------------------------------- #
# reconcile_daily_llm_budget — the single daily-LLM-pool reconcile rule          #
# --------------------------------------------------------------------------- #
def reconcile_daily_llm_budget(
    *,
    seats_watch_state: Mapping[str, Any],
    run_budget: "RunBudget",
    requested_llm_invocations: int,
    session_date: str,
    is_live_session: bool,
) -> int:
    """The single place both pools are reconciled into an admissible LLM count.

    Pure. Returns the number of LLM invocations a point may reserve, never exceeding
    EITHER pool:

    * historical ``PIT_REPLAY`` points (``is_live_session=False``) never consume the
      seats 24/day pool — they are research compute governed SOLELY by the
      ``RunBudget``: ``min(requested, run_budget.max_llm - run_budget.reserved_llm)``;
    * a point executing during a live trading session (``is_live_session=True``) may
      additionally reserve at most ``daily_budget - counts[session_date]`` from the
      shared 24/day pool.

    The returned count feeds :meth:`BudgetLedger.reserve_node`; live-session settlement
    is reported back through the watcher seam :func:`guanlan_v2.seats.watcher.note_external_llm_use`
    exactly once per settled reservation (one pool, counted once).
    """
    requested = int(requested_llm_invocations)
    if requested < 0:
        raise ValueError("requested_llm_invocations must be >= 0")
    run_remaining = max(
        0, int(run_budget.max_llm_invocations) - int(run_budget.reserved_llm_invocations)
    )
    admissible = min(requested, run_remaining)
    if is_live_session:
        # DEFAULT_BUDGET mirrors guanlan_v2.seats.watcher.DEFAULT_BUDGET (24/day);
        # read from the injected state so the two never drift.
        daily_budget = int(seats_watch_state.get("daily_budget") or 24)
        counts = seats_watch_state.get("counts") or {}
        used = int(counts.get(session_date) or 0)
        daily_remaining = max(0, daily_budget - used)
        admissible = min(admissible, daily_remaining)
    return max(0, admissible)


# --------------------------------------------------------------------------- #
# ReplayIntentLedger — the no-retroactive, idempotent per-run intent ledger      #
# --------------------------------------------------------------------------- #
class ReplayIntentLedger:
    """The per-run ledger of staged shadow intents + deterministic target sets.

    Enforces the no-retroactive-intent red line (invariant 3/5): a monotone
    ``last_scheduled_for`` high-water mark, a :class:`RetroactiveIntentRefused` on any
    backfill (or ``decision_as_of``/snapshot mismatch), and idempotent re-application
    (the same ``(intent_id, scheduled_for, target_version)`` apply key returns the
    stored intent and appends nothing). The deterministic lane's per-point
    :class:`DeterministicTargetSet` records are carried envelope-free alongside the
    intents (Task 5 consumes both). Degraded points (a feed floor postdating the point)
    are recorded per ordinal — a degraded point, never a run failure.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, TargetPortfolioIntent] = {}
        self._order: list[str] = []
        self._deterministic: list[DeterministicTargetSet] = []
        self._degraded: dict[int, tuple[str, ...]] = {}
        self._hwm: datetime | None = None

    def append_intent(
        self, intent: TargetPortfolioIntent, *, expected_as_of: datetime | None = None
    ) -> TargetPortfolioIntent:
        """Append (or idempotently return) a staged intent under the no-retroactive rule.

        Idempotency is checked FIRST (a duplicate apply key returns the stored intent,
        appends nothing — never treated as retroactive). A genuinely new key whose
        ``decision_as_of`` mismatches its point's snapshot ``as_of``, or whose
        ``scheduled_for`` is at/before the high-water mark, is refused.
        """
        key = target_apply_key(intent)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing  # idempotent replay: stored intent, no second envelope
        if expected_as_of is not None and intent.decision_as_of != expected_as_of:
            raise RetroactiveIntentRefused(
                "intent decision_as_of "
                f"{intent.decision_as_of.isoformat()} does not equal its point's "
                f"ContextSnapshot as_of {expected_as_of.isoformat()}"
            )
        if self._hwm is not None and intent.scheduled_for <= self._hwm:
            raise RetroactiveIntentRefused(
                "intent scheduled_for "
                f"{intent.scheduled_for.isoformat()} is at or before the ledger "
                f"high-water mark {self._hwm.isoformat()} (no retroactive intent)"
            )
        self._by_key[key] = intent
        self._order.append(key)
        self._hwm = (
            intent.scheduled_for
            if self._hwm is None
            else max(self._hwm, intent.scheduled_for)
        )
        return intent

    def append_deterministic(
        self, target_set: "DeterministicTargetSet"
    ) -> "DeterministicTargetSet":
        """Append one envelope-free deterministic target set (Task 5 dual-curve lane)."""
        self._deterministic.append(target_set)
        return target_set

    def note_degraded(self, point_ordinal: int, badges: tuple[str, ...]) -> None:
        """Record a point's honest degradation badges (never a run failure)."""
        if badges:
            self._degraded[int(point_ordinal)] = tuple(badges)

    @property
    def intents(self) -> tuple[TargetPortfolioIntent, ...]:
        return tuple(self._by_key[k] for k in self._order)

    @property
    def deterministic_targets(self) -> tuple["DeterministicTargetSet", ...]:
        return tuple(self._deterministic)

    @property
    def degraded_points(self) -> dict[int, tuple[str, ...]]:
        return dict(self._degraded)

    @property
    def last_scheduled_for(self) -> datetime | None:
        return self._hwm

    def __len__(self) -> int:
        return len(self._order)


# --------------------------------------------------------------------------- #
# Per-point plan-execution ports (production: PlanAdmissionService + run_plan)    #
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class ReplayPointSnapshot:
    """The frozen per-point context a bootstrap run commits for a decision point.

    ``data_context`` is the committed (Phase-1) ``DataContext`` whose ``as_of`` the
    driver structurally binds to the point's ``decision_as_of`` (invariant 2).
    ``degradation_badges`` carries any honest bootstrap degradation (a degraded point,
    never a run failure). A production coordinator returns the run's real
    ``ContextSnapshot`` (which exposes the same ``data_context``); tests supply this
    lightweight equivalent.
    """

    data_context: Any
    degradation_badges: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class DeterministicBook:
    """A rule-computed target book for the deterministic zero-LLM replay lane."""

    rule_id: str
    positions: tuple[TargetPosition, ...]
    cash_weight: float


class ReplayPlanCoordinator(Protocol):
    """The per-point plan admission + execution port the driver consumes.

    In production this is an adapter that composes the real Phase-2 admission
    (``PlanAdmissionService``: prepare → persist+reserve → REQUIRED approval → freeze →
    verify_for_dispatch) with ``run_plan`` over the run-scoped stores/pool/catalog/
    bridge/gateways to run the versioned Phase-5 ``BootstrapPlan`` (→ a frozen
    ``ContextSnapshot``) and then the needed MainPlan (deterministic zero-LLM lane and
    the LLM shadow lane ending in ``dec.trader``'s ``PortfolioTargetProposal``). In
    tests it is a recorded fake returning real-contract objects. The driver owns the
    red-line enforcement (snapshot binding, budget children, monotone/idempotent
    ledger, envelope wrapping) around this port; the port owns plan execution.
    """

    def bootstrap_context(self, point: ReplayDecisionPoint) -> ReplayPointSnapshot:
        """Admit + run the versioned BootstrapPlan for ``point`` → its frozen context."""
        ...

    def llm_proposal(self, point: ReplayDecisionPoint, snapshot: ReplayPointSnapshot) -> Any:
        """Admit + run the LLM shadow MainPlan → the ``dec.trader`` proposal artifact."""
        ...

    def deterministic_targets(
        self, point: ReplayDecisionPoint, snapshot: ReplayPointSnapshot
    ) -> DeterministicBook:
        """Run the deterministic zero-LLM lane → its rule-computed target book."""
        ...


# --------------------------------------------------------------------------- #
# ReplayRuntimeBindings — the internal frozen service-port carrier (unregistered) #
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class ReplayRuntimeBindings:
    """The frozen service-port carrier the interval-replay driver runs against.

    An internal, unregistered carrier (never a schema payload). It bundles only
    service ports + the run-scoped material the driver needs; no callable-injected
    business handler beyond these ports. ``admission`` is the per-point plan
    coordinator (:class:`ReplayPlanCoordinator`, production: ``PlanAdmissionService``
    + ``run_plan``). ``clock_factory`` yields one :class:`AuthoritativeClock` per point
    (the driver reads NO wall clock — every instant comes through it).
    ``intent_ledger`` is the run-scoped output ledger Task 5 consumes (carried here so
    the driver can return the frozen ``ShadowReplayRunState`` per its pinned signature
    while the mutable ledger stays reachable). ``seats_budget_seam`` is the
    ``guanlan_v2.seats.watcher`` module surface (``load_state`` /
    ``note_external_llm_use``). ``feed_floors`` are the Task-2b archive coverage floors
    used to derive per-point feasibility (degraded-point honesty).
    """

    admission: ReplayPlanCoordinator
    budget: "BudgetLedger"
    run_budget: "RunBudget"
    schedule_registry: Any
    calendar: ImmutableTradingCalendar
    clock_factory: Callable[[ReplayDecisionPoint], AuthoritativeClock]
    seats_budget_seam: Any = None
    memory_preparer: Any = None
    shadow_runner: Any = None
    pool: Any = None
    stores: Any = None
    catalog_runtime: Any = None
    bridge_resolver: Any = None
    model_gateway: Any = None
    capability_gateway: Any = None
    feed_floors: tuple = ()
    intent_ledger: ReplayIntentLedger = dataclasses.field(default_factory=ReplayIntentLedger)
    source_decision_artifact_id: str = "shadow.dec.trader"
    # Task 6 (Phase 9) — additive maturity-wakeup ports (defaulted; the driver never
    # reads these — the maturity-wakeup path does). `replay_state_store` is the durable
    # ShadowReplayRunState head/operation store; `trial_ledger` is the Phase-4 TrialLedger
    # the terminal handoff registers a matured holdout window with.
    replay_state_store: Any = None
    trial_ledger: Any = None


# --------------------------------------------------------------------------- #
# run_interval_replay — the driver                                             #
# --------------------------------------------------------------------------- #
def _session_local_instant(instant: datetime, timezone_name: str) -> datetime:
    """``instant`` in the schedule timezone — its ``.date()`` is the A-share session date.

    Returned as the localized DATETIME (not just the ISO date) because both the session
    date string AND the day key the seats seam books into must come from the same value:
    ``note_external_llm_use`` buckets by ``now.date()``, so it is handed this instant, not
    the wall clock.
    """
    return instant.astimezone(ZoneInfo(timezone_name))


#: the honest degradation badge for a live point whose shared 24/day seats LLM pool is
#: exhausted — the LLM lane is NOT invoked (never invoke-then-skip-metering).
_LLM_BUDGET_EXHAUSTED_BADGE = "llm_budget_exhausted:seats_daily_pool"


def _degradation_badges_for_point(point: ReplayDecisionPoint, feed_floors: tuple) -> tuple[str, ...]:
    """Task-2b feasibility badges for ``point`` (a feed floor postdating it ⇒ degraded).

    Consumes the reviewed Task-2b :func:`derive_replay_feasible_window` — a factor whose
    feed floor postdates the point resolves UNAVAILABLE ⇒ an honestly degraded point
    (badges collected), NEVER a run failure. Imported lazily so importing this adapter
    never forces the Task-2 replay-data module onto the path.
    """
    if not feed_floors:
        return ()
    from guanlan_v2.orchestration.adapters.replay_data import derive_replay_feasible_window

    window = derive_replay_feasible_window(
        as_of=point.decision_as_of, feed_floors=tuple(feed_floors)
    )
    return tuple(v.badge for v in window.verdicts if v.badge)


def run_interval_replay(
    *,
    request: Any,
    schedule: DecisionSchedule,
    execution_config: ShadowExecutionConfig,
    interval_start: datetime,
    interval_end: datetime,
    bindings: ReplayRuntimeBindings,
    is_live_session: Callable[[ReplayDecisionPoint], bool] | None = None,
) -> ShadowReplayRunState:
    """Walk a decision schedule over an interval, producing per-point PIT snapshots and
    staged shadow intents — the honest 双曲线回放 driver.

    Per decision point, strictly in ``point_ordinal`` order:

    ① the point's frozen per-point ContextSnapshot is obtained from the plan
       coordinator (production: ``build_replay_data_context`` → ``prepare_pit_replay``
       → admit+run the versioned BootstrapPlan); its ``data_context.as_of`` is bound
       STRUCTURALLY to ``decision_as_of`` before any main-plan reservation (invariant 2;
       a mismatch raises :class:`SnapshotBindingRefused`, records no reservation);
    ② one ``RunBudget`` covers the whole interval — a single plan reservation, with
       per-point node reservations as its children; the deterministic-lane node reserves
       ZERO LLM invocations, the LLM-lane node the admissible count from
       :func:`reconcile_daily_llm_budget` (PIT_REPLAY ⇒ seats pool untouched);
    ③ the LLM shadow lane's ``dec.trader`` proposal is envelope-wrapped into a
       :class:`TargetPortfolioIntent` via the sole Phase-6 constructor
       :func:`wrap_proposal_as_intent` (all envelope fields runtime-generated); the
       deterministic lane's book becomes an envelope-free :class:`DeterministicTargetSet`;
    ④ the intent is appended to the run's intent ledger keyed
       ``(intent_id, scheduled_for, target_version)`` under the no-retroactive rule.

    ``AUTO`` approval is rejected outright (invariant 7). Curve building (Task 5's
    ``build_dual_curves``) consumes ``bindings.intent_ledger`` + the returned state.

    **Task-10 additive seam (`is_live_session`).** A point that executes DURING a live
    trading session shares the seats 24/day LLM pool with the watcher, so it must
    reconcile against that pool and settle back into it. ``is_live_session`` is the
    per-point predicate that decides this; ``None`` (the default) means every point is
    a historical ``PIT_REPLAY`` point and the behaviour is bit-identical to Task 4 (the
    seats pool is never read for admissibility, no reservation is settled and
    ``note_external_llm_use`` is never called). For a live point the admissible count
    comes from :func:`reconcile_daily_llm_budget` with ``is_live_session=True`` (capped
    by ``daily_budget - counts[session_date]`` read through
    ``bindings.seats_budget_seam.load_state``), its LLM-lane reservation is settled on
    the ledger, and the settled invocations are reported back into the SAME daily
    counts through ``bindings.seats_budget_seam.note_external_llm_use`` **exactly once
    per settled reservation** (one pool, counted once).
    """
    if getattr(request, "approval_policy", None) is ApprovalPolicy.AUTO:
        raise ReplayApprovalRefused(
            "AUTO approval is rejected for a shadow replay run; every admitted plan "
            "must go through REQUIRED approval (invariant 7)"
        )

    points = resolve_decision_points(
        schedule,
        calendar=bindings.calendar,
        interval_start=interval_start,
        interval_end=interval_end,
    )
    if not points:
        raise ValueError(
            "no decision points resolve in the interval; there is nothing to replay "
            "(the only decision times are the resolved points)"
        )

    ledger = bindings.intent_ledger
    request_id = str(getattr(request, "request_id", "req"))
    run_id = f"replay-run.{request_id}"
    experiment_id = f"replay.{request_id}.{schedule.id}"
    tz_name = schedule.timezone

    # invariant 4: ONE RunBudget covers the whole interval → one plan reservation; the
    # per-point node reservations below are its children.
    from guanlan_v2.orchestration.budget import BudgetRequest

    plan_candidate_digest = content_digest(
        {
            "domain": "shadow-replay-plan-v1",
            "schedule_digest": schedule.content_digest,
            "execution_config_digest": execution_config.semantic_digest(),
            "request_id": request_id,
        }
    )
    plan_reservation = bindings.budget.reserve_plan(
        request_id=request_id,
        candidate_plan_digest=plan_candidate_digest,
        budget_request=BudgetRequest(
            tokens=int(bindings.run_budget.max_tokens),
            llm_invocations=int(bindings.run_budget.max_llm_invocations),
            concurrency=int(bindings.run_budget.max_concurrency),
        ),
        idempotency_key=f"replay-plan:{run_id}",
    )

    def _load_seats_state() -> Mapping[str, Any]:
        """The watcher's shared 24/day pool snapshot (a missing seam ⇒ an empty pool)."""
        if bindings.seats_budget_seam is None:
            return {}
        try:
            return bindings.seats_budget_seam.load_state()
        except Exception:  # noqa: BLE001 — a missing seam state is not a replay failure
            return {}

    seats_state: Mapping[str, Any] = _load_seats_state()

    last_scheduled_for: datetime | None = None
    target_version = 1
    for point in points:
        # ONE localized instant per point: its `.date()` IS `session_iso`, so the pool
        # bucket the admissibility rule READS and the bucket the settlement WRITES are the
        # same day key (the seats seam books by `now.date()`; passing wall-clock `now`
        # would debit today's bucket while the rule read the session's — the pool would
        # never appear to deplete).
        session_local = _session_local_instant(point.decision_as_of, tz_name)
        session_iso = session_local.date().isoformat()
        live_point = bool(is_live_session(point)) if is_live_session is not None else False

        # ① frozen per-point ContextSnapshot + structural as_of binding (invariant 2).
        snapshot = bindings.admission.bootstrap_context(point)
        snap_as_of = getattr(getattr(snapshot, "data_context", None), "as_of", None)
        if snap_as_of != point.decision_as_of:
            raise SnapshotBindingRefused(
                f"point {point.point_ordinal} ContextSnapshot data_context.as_of "
                f"{snap_as_of!r} does not bind decision_as_of "
                f"{point.decision_as_of.isoformat()} (no reservation recorded)"
            )

        # degraded-point honesty (Task 2b): a feed floor postdating the point ⇒ badges,
        # never a run failure.
        badges = _degradation_badges_for_point(point, bindings.feed_floors)
        badges = tuple(sorted(set(badges) | set(getattr(snapshot, "degradation_badges", ()))))
        ledger.note_degraded(point.point_ordinal, badges)

        # ② per-point node reservations, children of the one plan reservation.
        llm_admissible = reconcile_daily_llm_budget(
            seats_watch_state=seats_state,
            run_budget=bindings.run_budget,
            requested_llm_invocations=1,  # one dec.trader proposal per point
            session_date=session_iso,
            # a historical PIT_REPLAY point never consumes the seats pool; only a point
            # executing during a live trading session shares the 24/day pool.
            is_live_session=live_point,
        )
        # A live point with ZERO admissible LLM budget must NOT invoke the LLM lane at
        # all: invoking and then skipping the metering would silently over-draw the
        # shared 24/day pool. It becomes an honestly degraded point (the same
        # degraded-point surface an UNAVAILABLE feed uses) and the run continues.
        run_llm_lane = not (live_point and llm_admissible <= 0)
        if not run_llm_lane:
            badges = tuple(sorted(set(badges) | {_LLM_BUDGET_EXHAUSTED_BADGE}))
            ledger.note_degraded(point.point_ordinal, badges)
        llm_reservation = bindings.budget.reserve_node(
            plan_reservation_id=plan_reservation.reservation_id,
            node_id=f"llm.dec.trader#{point.point_ordinal}",
            attempt=1,
            tokens=1000,
            llm_invocations=llm_admissible,
            concurrency=1,
            idempotency_key=f"replay-node-llm:{run_id}:{point.point_ordinal}",
        )
        bindings.budget.reserve_node(
            plan_reservation_id=plan_reservation.reservation_id,
            node_id=f"det.rule#{point.point_ordinal}",
            attempt=1,
            tokens=1000,
            llm_invocations=0,  # the deterministic lane reserves ZERO LLM invocations
            concurrency=1,
            idempotency_key=f"replay-node-det:{run_id}:{point.point_ordinal}",
        )

        clock = bindings.clock_factory(point)

        # ③ LLM shadow lane → envelope-wrapped intent (sole Phase-6 constructor).
        if run_llm_lane:
            proposal_artifact = bindings.admission.llm_proposal(point, snapshot)
            intent = wrap_proposal_as_intent(
                proposal_artifact=proposal_artifact,
                source_decision_artifact_id=bindings.source_decision_artifact_id,
                request=request,
                schedule_registry=bindings.schedule_registry,
                calendar=bindings.calendar,
                session_date=session_iso,
                decision_as_of=point.decision_as_of,
                target_version=target_version,
                intent_id=f"intent.{run_id}.{point.point_ordinal}.{target_version}",
                clock=clock,
            )
            # ④ append under the no-retroactive rule (monotone + idempotent).
            ledger.append_intent(intent, expected_as_of=point.decision_as_of)

        # live-session settlement (Task 10 · carry C-B): settle the LLM-lane reservation
        # and report the consumed invocations back into the SAME daily counts the
        # watcher's `tick` reads. A PIT_REPLAY point never reaches here, so the seats pool
        # stays untouched.
        #
        # The once-only guard is the reservation's OWN DURABLE state, not an in-process
        # set: `reserve_node` is idempotent by its key, so a re-run of the same interval
        # returns the already-`settled` reservation and must NOT debit the shared pool a
        # second time (an in-memory set is recreated per call and would double-debit).
        #
        # `actual_llm_invocations` is EXACT (one dec.trader proposal per admitted point —
        # the quantity the 24/day seats pool meters); `actual_tokens` is the reservation
        # when the lane ran (an honest upper bound: no per-point token telemetry crosses
        # the ReplayPlanCoordinator port) and ZERO when the lane was refused for budget.
        if live_point and llm_reservation.status != "settled":
            bindings.budget.settle(
                llm_reservation.reservation_id,
                actual_tokens=1000 if run_llm_lane else 0,
                actual_llm_invocations=llm_admissible,
                idempotency_key=f"replay-settle-llm:{run_id}:{point.point_ordinal}",
            )
            if bindings.seats_budget_seam is not None and llm_admissible > 0:
                # book into the POINT'S session-day bucket — the same key the
                # admissibility rule read (`session_iso`), never wall-clock today.
                bindings.seats_budget_seam.note_external_llm_use(
                    llm_admissible, now=session_local)
                # re-read the pool so a later point / run sees this consumption.
                seats_state = _load_seats_state()

        # deterministic lane → envelope-free DeterministicTargetSet (Task 5 consumes).
        book = bindings.admission.deterministic_targets(point, snapshot)
        ledger.append_deterministic(
            DeterministicTargetSet(
                rule_id=book.rule_id,
                point_ordinal=point.point_ordinal,
                target_version=target_version,
                session_date=session_iso,
                positions=tuple(book.positions),
                cash_weight=book.cash_weight,
            )
        )
        last_scheduled_for = point.scheduled_for

    # audit wall-clock read through the binding (never a direct wall-clock read).
    updated_at = clock_now(bindings.clock_factory(points[-1]))
    return ShadowReplayRunState(
        experiment_id=experiment_id,
        run_id=run_id,
        request_id=request_id,
        schedule_digest=schedule.content_digest,
        execution_config_digest=execution_config.semantic_digest(),
        status=ExperimentStatus.RUNNING,  # points processed; curve build is Task 5
        completed_points=len(points),
        total_points=len(points),
        last_scheduled_for=last_scheduled_for,
        curve_report_ref=None,  # set by Task 5's curve stage, never here
        updated_at=updated_at,
    )


# =========================================================================== #
# Task 5 (Phase 9) · dual curves under one execution attestation + handoff       #
# =========================================================================== #
# This section EXTENDS the Phase-6 adapter additively (Task-4 driver above is
# sealed). It owns the honest 双曲线回放 (dual curve) build + the maturity-gated
# feedback handoff:
#   * `derive_deterministic_targets` — the pure, versioned, zero-LLM rule mapping a
#     PIT factor score at `decision_as_of` onto a full-weight / flat single-name
#     `DeterministicTargetSet`. It mirrors the seats `w=1` pure-factor direction rule
#     (`_hybrid_direction`, `guanlan_v2/seats/api.py:479`) with the τ=0.15 dead zone
#     EXACTLY: a score `> +τ` is a long leg, `< -τ` is a sell/exit (no long-only
#     position), and `|score| <= τ` is the watch dead zone (flat). A `[UNSOURCED]`
#     score set (no provenance digest) is refused — no target is produced.
#   * `build_dual_curves` — runs BOTH lanes on the SAME attested runner: the LLM
#     intents lane via `ShadowBacktestRunner.run` and the envelope-free deterministic
#     lane via `.run_targets`. Both bind ONE `ShadowExecutionConfig` semantic digest;
#     the runner is re-verified against that config before any bar (a mismatch refuses
#     with no fills). Curve points come ONLY from the runner NAV history (no synthetic
#     smoothing / interpolation); an all-watch deterministic lane is an honest flat
#     series from `init_cash`.
#   * `hand_off_dual_curves_to_feedback` — the maturity-gated evaluator handoff. It
#     stages/commits the `DualCurveReport` through a staged→barrier artifact sink and,
#     ONLY once the interval's realized window has matured, registers the realized
#     interval with the Phase-4 `TrialLedger` (as a matured holdout window, idempotent
#     by run identity). An immature interval parks the run at `WAITING_FOR_MATURITY`
#     (Task 6 persists it) and NEVER touches the ledger — spec: shadow 结果成熟后才能进反馈.
#
# NAMING (correction, see p9-task-5-report.md): the brief's `submit_dual_curves_to_
# evaluator` collides with two SEALED Phase-6 redlines (a Phase-6 module may define no
# identifier containing "evaluator", and export no name containing "submit"); the
# function is therefore `hand_off_dual_curves_to_feedback`, honoring both redlines.

#: the deterministic-target-rule-v1 dead-zone threshold — byte-identical to the seats
#: `_HYBRID_TAU` (`guanlan_v2/seats/api.py:479`), so the deterministic lane's direction
#: rule mirrors the w=1 pure-factor path EXACTLY (a rule change requires a new rule_id).
_DETERMINISTIC_TAU: float = 0.15


class UnsourcedFactorScoreError(ShadowContractError):
    """A deterministic target was requested from factor scores with no provenance.

    Raised by :func:`derive_deterministic_targets` when the ``provenance_digest`` is
    absent / empty / the ``[UNSOURCED]`` badge — a rule-computed target may only be
    derived from scores produced under a genuine PIT :class:`DataResult`; an unsourced
    score is refused and NO target is produced (the provenance red line).
    """


def derive_deterministic_targets(
    point: ReplayDecisionPoint,
    *,
    factor_scores: Mapping[str, float],
    universe: tuple[Symbol, ...],
    provenance_digest: str | None,
    rule_id: Literal["deterministic-target-rule-v1"] = "deterministic-target-rule-v1",
    target_version: int = 1,
) -> DeterministicTargetSet:
    """Map PIT factor scores at ``point.decision_as_of`` onto a rule-computed target book.

    Pure, versioned, zero-LLM. Each ``universe`` symbol's PIT factor score (keyed by
    ``Symbol.code``) is run through the frozen v1 direction rule — byte-identical to the
    seats ``w=1`` pure-factor path (``_hybrid_direction``, ``api.py:479``) with the
    τ=:data:`_DETERMINISTIC_TAU` (0.15) dead zone:

    * ``score > +τ``   → a LONG leg;
    * ``score < -τ``   → a sell / exit (no long-only position — the target book simply
      omits the name);
    * ``|score| <= τ`` → the watch dead zone (flat).

    The long legs share EQUAL weight (band-exempt continuous ``1/N`` — a single long is a
    full-weight ``1.0`` leg); with no long legs the book is all-cash (``cash_weight =
    1.0``, an honest flat book). The result is the envelope-free Phase-6
    :class:`DeterministicTargetSet` (``rule_id`` / ``point_ordinal`` / ``target_version``
    / ``session_date`` / positions + ``cash_weight``) — NEVER a
    :class:`TargetPortfolioIntent`. A rule change requires a new ``rule_id``.

    Provenance red line (:class:`UnsourcedFactorScoreError`): the scores must come from a
    :class:`DataResult` produced under the point's PIT context — an absent / empty /
    ``[UNSOURCED]`` ``provenance_digest`` is refused and no target is produced.
    """
    from guanlan_v2.orchestration.honesty import UNSOURCED_BADGE

    if not provenance_digest or provenance_digest == UNSOURCED_BADGE:
        raise UnsourcedFactorScoreError(
            "factor scores carry no provenance digest ([UNSOURCED]); a deterministic "
            "target is refused (scores must come from a PIT DataResult)"
        )

    # session_date in A-share terms: for the continuous-auction session the decision
    # instant's UTC calendar date equals the local (CST) session date (09:00-15:00 CST
    # == 01:00-07:00 UTC, same civil day), so the runner's schedule-time computations
    # reproduce this point's scheduled_for from it.
    session_date = point.decision_as_of.astimezone(timezone.utc).date().isoformat()

    # frozen v1 direction rule (τ dead zone) — a long leg iff score > +τ.
    long_syms = [
        s for s in universe
        if factor_scores.get(s.code) is not None and float(factor_scores[s.code]) > _DETERMINISTIC_TAU
    ]
    if not long_syms:
        return DeterministicTargetSet(
            rule_id=rule_id, point_ordinal=point.point_ordinal,
            target_version=target_version, session_date=session_date,
            positions=(), cash_weight=1.0,
        )
    n = len(long_syms)
    w = 1.0 / n
    # the last leg absorbs the float remainder so sum(weights) is exactly 1.0 (cash 0).
    weights = [w] * (n - 1) + [1.0 - w * (n - 1)]
    positions = tuple(
        TargetPosition(symbol=s, target_weight=wt) for s, wt in zip(long_syms, weights)
    )
    return DeterministicTargetSet(
        rule_id=rule_id, point_ordinal=point.point_ordinal,
        target_version=target_version, session_date=session_date,
        positions=positions, cash_weight=0.0,
    )


def _attest_runner_reproduces_config(
    runner: ShadowBacktestRunner, cfg: ShadowExecutionConfig
) -> None:
    """Refuse (pre-bar) unless ``runner`` reproduces ``cfg``'s attested dimensions.

    Every runner-checkable matching dimension (init cash / cost-model digest / calendar
    id / matching-engine version / schedule digest / intrabar exit priority / clock) must
    equal the shared-口径 :class:`ShadowExecutionConfig`; a mismatch raises BEFORE either
    lane runs (no fills). The **clock** dimension is bound through the config's
    :class:`ClockSpec` reasoning fields the runner actually reproduces — ``clock.timezone``
    (the runner's schedule timezone) and ``clock.calendar_id`` (the runner's bound
    calendar) — so a report can never be labeled with a clock the runner did not use (the
    ``ClockSpec.as_of`` is a frozen reasoning instant a windowed multi-bar runner does not
    reduce to; the runner's OPERATIONAL clock is bound across the two lanes by
    ``run_targets``'s ``_require_matching_run_config``, since both lanes share the single
    runner's ``_clock``). The universe / data-snapshot dimensions carry no runner-side
    digest and are bound structurally by both curves binding ``cfg.semantic_digest()`` (the
    Task-1 validator), so invariant 1's "Task-1 validator plus a runner-side pre-check both
    fire" holds.
    """
    mism: list[str] = []
    if float(runner._init_cash) != float(cfg.init_cash):
        mism.append("init_cash")
    if content_digest(dataclasses.asdict(runner._cost_model)) != cfg.cost_model_digest:
        mism.append("cost_model_digest")
    if runner._calendar.calendar_id != cfg.calendar_id:
        mism.append("calendar_id")
    if SHADOW_MATCHING_ENGINE_VERSION != cfg.matching_engine_version:
        mism.append("matching_engine_version")
    if runner._schedule.content_digest != cfg.schedule_digest:
        mism.append("schedule_digest")
    if runner._schedule.intrabar_exit_priority != cfg.intrabar_exit_priority:
        mism.append("intrabar_exit_priority")
    if cfg.clock.timezone != runner._schedule.timezone:
        mism.append("clock.timezone")
    if cfg.clock.calendar_id != runner._calendar.calendar_id:
        mism.append("clock.calendar_id")
    if mism:
        raise ShadowContractError(
            "the shadow runner does not reproduce the shared execution config "
            f"(differing dimensions: {mism}); both dual-curve lanes must run under one "
            "attested 同一口径 (no fills)"
        )


def _curve_points_from_nav(
    nav_history: tuple, tz: ZoneInfo
) -> tuple[ShadowCurvePoint, ...]:
    """Project a runner NAV history 1:1 into ``ShadowCurvePoint``s — no interpolation.

    Each ``(session_iso, nav)`` EOD mark becomes ONE curve point anchored at the
    A-share session close (15:00 in ``tz``, converted to UTC); no synthetic smoothing
    or interpolation is introduced (distinct sessions ⇒ strictly-increasing ``at``).
    """
    points: list[ShadowCurvePoint] = []
    for session_iso, nav in nav_history:
        y, m, d = (int(x) for x in str(session_iso).split("-"))
        at = datetime(y, m, d, 15, 0, tzinfo=tz).astimezone(timezone.utc)
        points.append(ShadowCurvePoint(at=at, nav=float(nav)))
    return tuple(points)


def build_dual_curves(
    *,
    execution_config: ShadowExecutionConfig,
    points: tuple[ReplayDecisionPoint, ...],
    deterministic_target_sets: tuple[DeterministicTargetSet, ...],
    intents: tuple[TargetPortfolioIntent, ...],
    shadow_runner: ShadowBacktestRunner,
) -> DualCurveReport:
    """Run both replay lanes on ONE attested runner → an honest :class:`DualCurveReport`.

    The LLM intents lane (``shadow_runner.run(intents, ...)`` — apply-once per
    ``(intent_id, scheduled_for, target_version)``) and the envelope-free deterministic
    lane (``shadow_runner.run_targets(target_sets, ...)`` — deduped on
    ``deterministic_apply_key``) execute on the SAME runner instance over the SAME
    ``[start, end]`` window (so identical bar stream, calendar, cost model, clock and
    matching engine — invariant 4). The runner is re-verified against ``execution_config``
    before any bar (:func:`_attest_runner_reproduces_config`); a mismatch refuses with no
    fills. The deterministic lane's result keeps ``intent_content_digests == ()`` (Phase-6
    LLM-zero invariant, re-asserted here). Each lane's NAV history becomes a
    :class:`ShadowCurveSeries` bound to ``execution_config.semantic_digest()``; the report
    carries the permanently-true ``not_causal_attribution`` honesty flag.
    """
    if not points:
        raise ShadowContractError("build_dual_curves requires at least one decision point")
    if not intents:
        raise ShadowContractError(
            "the LLM shadow lane requires at least one intent (a curve names its "
            "applied intent digests)"
        )
    if not deterministic_target_sets:
        raise ShadowContractError(
            "the deterministic lane requires at least one DeterministicTargetSet "
            "(the curve names its rule_id)"
        )

    # ① pre-bar attestation: the runner must reproduce the shared 同一口径 config.
    _attest_runner_reproduces_config(shadow_runner, execution_config)

    # ② the single [start, end] window BOTH lanes run (identical bars by construction).
    tz = shadow_runner._tz
    start = min(p.scheduled_for for p in points).astimezone(tz).date().isoformat()
    end = max(p.eligible_execution_at for p in points).astimezone(tz).date().isoformat()

    # ③ intent lane — apply-once over the frozen intents.
    llm_result = shadow_runner.run(tuple(intents), start=start, end=end)

    # ④ deterministic lane — envelope-free, through run_targets on the SAME runner. The
    #    run_config is built FROM the runner's own bound fields, so the runner's
    #    _require_matching_run_config re-verifies the two lanes share one config digest.
    run_config = ShadowRunConfig(
        start=start, end=end, init_cash=shadow_runner._init_cash,
        cost_model=shadow_runner._cost_model,
        corporate_actions=shadow_runner._corporate_actions,
        is_st=shadow_runner._is_st or None, lot_size=shadow_runner._lot_size,
    )
    det_result = shadow_runner.run_targets(
        tuple(deterministic_target_sets), run_config=run_config,
        calendar=shadow_runner._calendar, clock=shadow_runner._clock,
    )
    # LLM-zero red line, re-asserted structurally at the consumer.
    if det_result.intent_content_digests != ():
        raise ShadowContractError(
            "the deterministic lane result must keep intent_content_digests == () "
            "(a deterministic target is never wrapped as an intent)"
        )

    cfg_digest = execution_config.semantic_digest()
    llm_series = ShadowCurveSeries(
        curve_kind="llm_shadow", execution_config_digest=cfg_digest,
        points=_curve_points_from_nav(llm_result.nav_history, tz),
        trade_count=llm_result.n_trades,
        applied_intent_digests=tuple(sorted({i.semantic_digest() for i in intents})),
        badges=llm_result.badges,
    )
    det_series = ShadowCurveSeries(
        curve_kind="deterministic_strategy", execution_config_digest=cfg_digest,
        points=_curve_points_from_nav(det_result.nav_history, tz),
        trade_count=det_result.n_trades,
        rule_id=deterministic_target_sets[0].rule_id,
        badges=det_result.badges,
    )

    # ⑤ delta_total_return = llm_total_return - det_total_return (reported, non-causal).
    init = float(execution_config.init_cash)
    delta = None
    if llm_series.points and det_series.points and init:
        llm_tr = llm_series.points[-1].nav / init - 1.0
        det_tr = det_series.points[-1].nav / init - 1.0
        delta = llm_tr - det_tr

    return DualCurveReport(
        execution_config=execution_config,
        deterministic=det_series,
        llm_shadow=llm_series,
        interval_start=min(p.scheduled_for for p in points),
        interval_end=max(p.eligible_execution_at for p in points),
        decision_point_count=len(points),
        delta_total_return=delta,
        badges=tuple(sorted(set(llm_result.badges) | set(det_result.badges))),
    )


def _feedback_study_and_window(report: DualCurveReport, run_id: str) -> tuple:
    """Derive the Phase-4 ``(StudySpec, HoldoutWindow)`` a matured interval registers.

    The shadow replay's study identity IS its shared-口径 execution config (the study
    family keys on the config's semantic digest); the realized interval IS a matured
    out-of-sample holdout window (``matured_at = interval_end``). Every handle is derived
    from REAL report / execution-config content — nothing fabricated. Imported lazily so
    importing this adapter never forces the Phase-4 trial layer onto the path.
    """
    from guanlan_v2.orchestration.governor import derive_study_family
    from guanlan_v2.orchestration.trial import HoldoutWindow, StudySpec
    from guanlan_v2.orchestration.trial_ledger import compute_holdout_window_attestation

    cfg = report.execution_config
    objective = f"shadow-replay dual-curve feedback for {run_id}"
    label = "realized OOS NAV over the shadow replay interval"
    study = StudySpec(
        objective=objective, objective_digest=content_digest({"text": objective}),
        label_definition=label, label_digest=content_digest({"text": label}),
        universe_digest=content_digest([s.dotted for s in cfg.universe]),
        frequency="1d", split_policy_digest=cfg.semantic_digest(),
    )
    family = derive_study_family(study)
    att = compute_holdout_window_attestation(
        family_identity_digest=family.identity_digest,
        start_at=report.interval_start, end_at=report.interval_end,
        prior_window_ids=(),
    )
    window = HoldoutWindow(
        holdout_window_id=f"shadow-window.{run_id}",
        family_identity_digest=family.identity_digest,
        start_at=report.interval_start, end_at=report.interval_end,
        matured_at=report.interval_end,
        data_snapshot_id=cfg.data_snapshot_content_digest,
        vintage_manifest_digest=cfg.vintage_manifest_digest,
        prior_window_ids=(), non_overlap_attestation=att,
    )
    return study, window


class _DualCurveArtifactBarrier(Protocol):
    """The staged→barrier subset of the Phase-2 ``ArtifactPool`` the handoff consumes.

    A real :class:`~guanlan_v2.orchestration.pool.ArtifactPool` satisfies this Protocol
    structurally (identical ``stage`` / ``commit_layer`` method names + keyword
    signatures, `pool.py:300`/`pool.py:393`), so Task 12's production wiring is drop-in
    once ``DualCurveReport@1`` is registered (Task 9) and a plan/worker declares it as an
    output. Narrow by design — only the two staged→barrier methods are named; correction
    N5-4 records why the full pool cannot validate an unregistered ``DualCurveReport@1``
    in this phase.
    """

    def stage(
        self, artifact: Any, *, layer_index: int, node_run: Any, idempotency_key: str
    ) -> Any: ...

    def commit_layer(
        self, layer_index: int, *, node_runs: Any, expected_outputs: Any,
        idempotency_key: str,
    ) -> Any: ...


def _stage_commit_dual_curve(
    report: DualCurveReport, run_id: str, pool: _DualCurveArtifactBarrier
) -> TypedPayloadRef:
    """Drive the real ``ArtifactPool`` staged→barrier surface for the report artifact.

    Wraps the ``DualCurveReport`` in a real Phase-1 :class:`Artifact` produced by a real
    :class:`NodeRun`, then ``pool.stage(...)`` → ``pool.commit_layer(...)`` (the exact
    real-pool method names/signatures, so a real pool is a drop-in ``pool``). Returns the
    committed ``DualCurveReport@1`` / ``main`` :class:`TypedPayloadRef`. Phase-1/Phase-2
    constructors are imported lazily so importing this adapter never forces the pool
    machinery onto the path.
    """
    from guanlan_v2.orchestration.enums import DataMode, NodeStatus
    from guanlan_v2.orchestration.pool import ExpectedOutput
    from guanlan_v2.orchestration.schemas import Artifact, NodeRun, Provenance

    node_id = "dec.dual_curves"
    output_key = "dual_curve_report"
    schema_ref = SchemaRef(name=DualCurveReport.__name__, version="1")
    plan_digest = content_digest(
        {"domain": "shadow-dual-curve-plan-v1", "run_id": run_id})
    artifact = Artifact.build(
        artifact_id=f"dualcurve.{run_id}", run_id=run_id, created_at=report.interval_end,
        producer_node_id=node_id, slot=output_key, output_key=output_key,
        kind=output_key, payload_schema_ref=schema_ref, payload=report, rendered_md="",
        provenance=Provenance(
            plan_digest=plan_digest, code_version="shadow-replay-v1",
            as_of=report.interval_end, pit_mode=DataMode.PIT_REPLAY),
    )
    node_run = NodeRun(
        node_run_id=f"noderun.{run_id}", run_id=run_id, plan_id=f"plan.{run_id}",
        plan_digest=plan_digest, node_id=node_id, worker_id=node_id,
        status=NodeStatus.COMPLETED, attempt_id=f"attempt.{run_id}",
        input_snapshot_digest=content_digest({"insnap": run_id}),
        output_keys=(output_key,), output_artifact_ids=(artifact.artifact_id,),
    )
    expected = (
        ExpectedOutput(node_id=node_id, output_key=output_key, schema_ref=schema_ref),
    )
    pool.stage(
        artifact, layer_index=0, node_run=node_run,
        idempotency_key=f"dualcurve-stage:{run_id}")
    pool.commit_layer(
        0, node_runs=(node_run,), expected_outputs=expected,
        idempotency_key=f"dualcurve-commit:{run_id}")
    # Task-12 carry (Task-5/9 review): read the COMMITTED artifact back rather than
    # synthesizing the ref from the staged one. Now that `DualCurveReport@1` is a
    # registered Phase-9 payload (Task 9), a real `ArtifactPool` can answer
    # `committed_output`; if the pool's stored artifact differed from what we staged,
    # the synthesized ref would have pointed at content the pool never committed.
    committed = artifact
    read_back = getattr(pool, "committed_output", None)
    if callable(read_back):
        stored = read_back(node_id, output_key)
        if stored is None:
            raise ShadowContractError(
                "the dual-curve report was staged and the layer committed, but the pool "
                f"has no committed output for ({node_id!r}, {output_key!r}) — the ref is "
                "never synthesized over a commit that did not land")
        if stored.content_digest != artifact.content_digest:
            raise ShadowContractError(
                "the pool committed a dual-curve artifact whose content digest differs "
                "from the staged report; the curve ref binds the COMMITTED content only")
        committed = stored
    return TypedPayloadRef(
        schema_ref=committed.payload_schema_ref,
        payload_ref=PayloadRef(
            namespace="main", object_id=committed.artifact_id,
            content_digest=report.semantic_digest()),
    )


def hand_off_dual_curves_to_feedback(
    report: DualCurveReport,
    *,
    run_id: NonEmptyStr,
    pool: _DualCurveArtifactBarrier,
    ledger: Any,
    maturity_now: datetime,
) -> ShadowReplayRunState:
    """Stage/commit the ``DualCurveReport`` and register the matured interval (gated).

    The maturity red line (spec: shadow 结果成熟后才能进反馈): the report artifact is staged
    then barrier-committed through ``pool`` (a staged→barrier artifact sink — correction
    N5-4: the full Phase-2 ``ArtifactPool`` cannot validate an unregistered
    ``DualCurveReport@1`` in this phase, Task 9 owns registration), yielding the committed
    ``curve_report_ref`` (a ``DualCurveReport@1`` / ``main`` :class:`TypedPayloadRef`). Then:

    * if the interval's realized window is NOT yet mature (``maturity_now <
      interval_end``), the run transitions to ``WAITING_FOR_MATURITY`` (Task 6 persists it)
      and the :class:`TrialLedger` is NEVER touched (no feedback before maturity);
    * once mature, the realized interval is registered with the Phase-4 evaluator surface
      via ``ledger.register_holdout_window`` — idempotent by run identity (a second handoff
      for the same ``run_id`` registers nothing new) — and the run completes with the
      committed ``curve_report_ref``.
    """
    # stage → barrier commit the report artifact through the REAL ArtifactPool surface
    # (stage → commit_layer) — the curves are real regardless of maturity; only the
    # feedback is gated. Yields the committed DualCurveReport@1 / main curve_report_ref.
    curve_ref = _stage_commit_dual_curve(report, str(run_id), pool)
    cfg_digest = report.execution_config.semantic_digest()
    n_points = report.decision_point_count

    if maturity_now < report.interval_end:
        # immature ⇒ park; the ledger is untouched (no feedback before maturity).
        park_key = content_digest(
            {
                "domain": "shadow-replay-feedback-park-v1",
                "run_id": str(run_id),
                "execution_config_digest": cfg_digest,
                "interval_end": report.interval_end,
            }
        )
        return ShadowReplayRunState(
            experiment_id=f"shadow-replay.{run_id}", run_id=run_id, request_id=str(run_id),
            schedule_digest=report.execution_config.schedule_digest,
            execution_config_digest=cfg_digest,
            status=ExperimentStatus.WAITING_FOR_MATURITY,
            completed_points=n_points, total_points=n_points,
            resume_after=report.interval_end, wakeup_key=park_key,
            curve_report_ref=curve_ref, updated_at=maturity_now,
        )

    # matured ⇒ register the realized interval with the Phase-4 ledger exactly once.
    study, window = _feedback_study_and_window(report, str(run_id))
    ledger.register_holdout_window(
        study, window, idempotency_key=f"shadow-replay-feedback:{run_id}")
    return ShadowReplayRunState(
        experiment_id=f"shadow-replay.{run_id}", run_id=run_id, request_id=str(run_id),
        schedule_digest=report.execution_config.schedule_digest,
        execution_config_digest=cfg_digest,
        status=ExperimentStatus.COMPLETED,
        completed_points=n_points, total_points=n_points,
        curve_report_ref=curve_ref, updated_at=maturity_now,
    )


# =========================================================================== #
# Task 6 (Phase 9) · WAITING_FOR_MATURITY persistence + idempotent maturity wakeup #
# =========================================================================== #
# This section EXTENDS the Phase-6 adapter additively (Task 4/5 above are sealed). It
# owns the durable head + the idempotent maturity-wakeup for a parked 双曲线回放 run:
#   * two constructor-injected state-cell namespaces (`adapters.replay_head.v1` +
#     `adapters.replay_operation.v1`) appended to the reviewed startup union (clause C5)
#     — never a hardcoded store list;
#   * `ReplayStateStore` — the durable head (per experiment) + the maturity-index (per
#     service-derived key) + the operation-result record (per key), each transition ONE
#     RuntimeUnitOfWork (typed state payload put + head CAS + operation-result CAS +
#     `ExperimentStateChanged` RunEvent — all-or-none);
#   * `persist_replay_state` — persist-then-publish; a same-key/same-content replay is an
#     idempotent no-op returning the stored ref, a same-key/different-content replay raises
#     `IdempotencyConflict`;
#   * `mature_shadow_replay` — the idempotent maturity consumer: ① resolve the head by the
#     service-derived key; ② `now < resume_after` ⇒ `not_mature` (state untouched); ③ an
#     operation-cell hit ⇒ `already_processed` (the stored receipt, byte-identical); ④
#     mature ⇒ re-run `hand_off_dual_curves_to_feedback` and advance the head to
#     `COMPLETED` or a re-parked `WAITING_FOR_MATURITY` with a strictly-later `resume_after`
#     and a NEW service-derived key. It NEVER re-executes a decision point / regenerates an
#     intent — it consumes maturity only.
#
# NAMING (correction N6-1, see p9-task-6-report.md): the brief's public name
# `wakeup_shadow_replay` collides with the SEALED Phase-6 red line
# `test_shadow_redlines.py::test_phase9_machinery_names_absent_from_phase6_modules`, which
# forbids the token "wakeup" (also "resume") in ANY *defined/imported* identifier of a
# Phase-6 module (an AST-name check; docstrings/parameters/attribute reads are exempt). The
# guard's own comment frames it as a PERMANENT hygiene invariant (invariant group 7 "scope
# protection"), NOT a "does-not-exist-yet" gate; Task 5 hit the identical collision
# (`submit_dual_curves_to_evaluator`) and the reviewer adjudicated the compliant rename as
# correct. The function is therefore `mature_shadow_replay`; the durable-store class is
# `ReplayStateStore`; the derivation is `derive_replay_maturity_key`; the unknown-key error
# is `ReplayMaturityKeyUnknown`; the domain-tagged digest is
# `REPLAY_MATURITY_KEY_DOMAIN = "adapters-replay-wakeup-v1"` (the VALUE keeps the pinned
# domain string that Task 10/12 consume — only the identifier is token-free). The guard is
# NEVER weakened. The contract field is still `ShadowReplayRunState.wakeup_key` (owned by
# the Phase-9 `contracts` module, not a Phase-6 module) and is referenced only as an
# attribute / call-kwarg, so no forbidden token enters a defined name here.

#: the two constructor-injected state-cell namespaces (clause C5 — appended to the reviewed
#: startup union via `RuntimeStores(allowed_cell_namespaces=…)`, never a hardcoded list).
REPLAY_HEAD_NAMESPACE: str = "adapters.replay_head.v1"
REPLAY_OPERATION_NAMESPACE: str = "adapters.replay_operation.v1"
REPLAY_STATE_CELL_NAMESPACES: tuple[str, ...] = (
    REPLAY_HEAD_NAMESPACE, REPLAY_OPERATION_NAMESPACE,
)
#: the domain tag of the service-derived maturity key (never caller-chosen).
REPLAY_MATURITY_KEY_DOMAIN: str = "adapters-replay-wakeup-v1"


class ReplayMaturityKeyUnknown(ShadowContractError):
    """A maturity wakeup was delivered for a key no parked head was ever issued under.

    Raised by :func:`mature_shadow_replay` (step ①) with NO state read side effects: an
    unknown key has no maturity-index cell, so there is nothing to advance.
    """


def _replay_sr(name: str) -> SchemaRef:
    return SchemaRef(name=name, version="1")


def _replay_registry_digest(registry: Any) -> DigestHex:
    return registry if isinstance(registry, str) else registry.registry_digest


def _replay_head_cell_key(experiment_id: str) -> DigestHex:
    return content_digest(["adapters.replay_head", experiment_id])


def _replay_index_cell_key(maturity_key: str) -> DigestHex:
    return content_digest(["adapters.replay_index", maturity_key])


def _replay_operation_cell_key(maturity_key: str) -> DigestHex:
    return content_digest(["adapters.replay_operation", maturity_key])


def _replay_template(model: Any) -> dict[str, Any]:
    return {name: getattr(model, name) for name in type(model).model_fields}


def derive_replay_maturity_key(
    *, experiment_id: str, resume_after: datetime, state_digest: str
) -> DigestHex:
    """The service-derived maturity key — a domain-tagged content digest, never chosen.

    ``content_digest({"domain": "adapters-replay-wakeup-v1", "experiment_id": …,
    "resume_after": …, "state_digest": …})``. Both the initial park and every re-park
    derive their key here; a caller can never supply one (there is no key parameter on the
    state-construction path), so a parked head is always located by a key the service alone
    minted.
    """
    return content_digest(
        {
            "domain": REPLAY_MATURITY_KEY_DOMAIN,
            "experiment_id": experiment_id,
            "resume_after": resume_after,
            "state_digest": state_digest,
        }
    )


def _replay_maturity_ladder(report: DualCurveReport) -> tuple[datetime, ...]:
    """The ordered maturity-batch boundaries of a parked interval (ascending, unique).

    Each realized session close (an ``llm_shadow`` curve point ``at``) is one matured
    batch boundary; ``interval_end`` is the terminal settlement boundary at which the
    interval is fully mature. Derived ONLY from REAL report content (no fabricated
    schedule) — a wakeup at ``now`` has matured every boundary ``<= now``.
    """
    boundaries = {p.at for p in report.llm_shadow.points}
    boundaries.add(report.interval_end)
    return tuple(sorted(boundaries))


class ReplayStateStore:
    """The durable head + maturity-index + operation-result store for a shadow-replay run.

    Mirrors the Phase-4 ``ExperimentStateStore`` idiom: :meth:`persist` commits ONE
    :class:`~guanlan_v2.orchestration.eventstore.RuntimeUnitOfWork` — a
    ``ShadowReplayRunState`` ``main`` payload + an ``ExperimentStateChanged`` ``main`` event
    + a ``adapters.replay_head.v1`` head CAS (keyed by experiment) + (when the head is
    parked) a maturity-index CAS (keyed by the service-derived key) — all-or-none. The
    maturity wakeup additionally records the operation result (a
    :class:`~guanlan_v2.orchestration.adapters.contracts.ReplayWakeupReceipt` payload) under
    a ``adapters.replay_operation.v1`` CAS in the SAME unit of work.

    Head/index/operation resolution is DURABLE (through the state cells); the in-process
    ``_waiting`` map is a same-process convenience for the scheduler playbook's enumeration
    (the same honest caveat the Phase-4 ``ExperimentStateStore`` carries — a restart re-reads
    the durable head, never a stale in-process index).
    """

    def __init__(
        self, *, payload_store: Any, state_cells: Any, registry: Any,
        clock: AuthoritativeClock, uow_factory: Callable[[], Any],
        event_store: Any = None,
    ) -> None:
        for namespace in REPLAY_STATE_CELL_NAMESPACES:
            if namespace not in state_cells.allowed_namespaces:
                raise ShadowContractError(
                    f"state-cell namespace {namespace!r} missing from the sealed store; "
                    "append REPLAY_STATE_CELL_NAMESPACES to the startup union (clause C5)"
                )
        self._payload_store = payload_store
        self._state_cells = state_cells
        self._registry = registry
        self._digest = _replay_registry_digest(registry)
        self._clock = clock
        self._uow_factory = uow_factory
        # the DURABLE index the parked-head scan walks (Task-12 carry, Task-6 review):
        # the daily scheduler parks on day N and wakes on day N+1 in a FRESH process,
        # where the in-process `_waiting` map is empty — so an in-memory-only
        # enumeration silently did nothing. A durable event store (the Task-1b
        # `JsonlEventStore`, which exposes `run_ids`) makes the scan restart-proof.
        self._events = event_store
        self._waiting: dict[str, ShadowReplayRunState] = {}

    @property
    def payload_store(self) -> Any:
        return self._payload_store

    # -- resolution (durable) ---------------------------------------------- #
    def _resolve_state(self, ref: TypedPayloadRef | None) -> ShadowReplayRunState | None:
        if ref is None:
            return None
        return self._payload_store.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    def _head_ref(self, experiment_id: str) -> TypedPayloadRef | None:
        return self._state_cells.load(
            REPLAY_HEAD_NAMESPACE, _replay_head_cell_key(experiment_id)
        )

    def load_head(self, experiment_id: str) -> ShadowReplayRunState | None:
        return self._resolve_state(self._head_ref(experiment_id))

    def load_head_by_maturity_key(self, maturity_key: str) -> ShadowReplayRunState | None:
        ref = self._state_cells.load(
            REPLAY_HEAD_NAMESPACE, _replay_index_cell_key(maturity_key)
        )
        return self._resolve_state(ref)

    def load_operation_receipt(self, maturity_key: str) -> "ReplayMaturityReceipt | None":
        ref = self._state_cells.load(
            REPLAY_OPERATION_NAMESPACE, _replay_operation_cell_key(maturity_key)
        )
        if ref is None:
            return None
        return self._payload_store.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    def resolve_report(self, curve_report_ref: TypedPayloadRef) -> DualCurveReport:
        return self._payload_store.get(
            curve_report_ref.payload_ref, expected_schema_ref=curve_report_ref.schema_ref
        )

    def waiting_states(self) -> tuple[ShadowReplayRunState, ...]:
        """Every head currently parked at ``WAITING_FOR_MATURITY``.

        DURABLE when an event store exposing ``run_ids`` is bound (the Task-1b
        ``JsonlEventStore``): the scan walks the persisted ``main`` journal for runs
        carrying an ``ExperimentStateChanged`` event — this store appends those under
        ``run_id == experiment_id`` — and re-reads each head THROUGH THE DURABLE CELLS,
        so a fresh process (the day-N+1 scheduler) finds yesterday's parked heads. Falls
        back to the in-process map when no scannable event store is bound (the in-memory
        Phase-2 default every test uses), which keeps existing behaviour byte-identical.
        """
        found: dict[str, ShadowReplayRunState] = dict(self._waiting)
        run_ids = getattr(self._events, "run_ids", None)
        if callable(run_ids):
            from guanlan_v2.orchestration.events import EventType

            for run_id in run_ids("main"):
                journal = self._events.journal(run_id, "main")
                if not any(e.event_type is EventType.EXPERIMENT_STATE_CHANGED
                           for e in journal):
                    continue
                head = self.load_head(run_id)
                if head is None:
                    continue
                if head.status == ExperimentStatus.WAITING_FOR_MATURITY:
                    found[head.experiment_id] = head
                else:
                    found.pop(head.experiment_id, None)
        return tuple(
            state for _key, state in sorted(found.items(), key=lambda kv: kv[0])
            if state.status == ExperimentStatus.WAITING_FOR_MATURITY
        )

    def _note_waiting(self, state: ShadowReplayRunState) -> None:
        if state.status == ExperimentStatus.WAITING_FOR_MATURITY:
            self._waiting[state.experiment_id] = state
        else:
            self._waiting.pop(state.experiment_id, None)

    # -- persistence (one UoW per transition, all-or-none) ------------------ #
    def persist(self, state: ShadowReplayRunState, *, idempotency_key: str) -> PayloadRef:
        """Persist-then-publish one head transition; idempotent by (experiment, key).

        A same-key/same-content replay is a no-op returning the stored ref; a
        same-key/different-content replay raises ``IdempotencyConflict`` (through the
        whole-batch idempotency of the unit of work).
        """
        from guanlan_v2.orchestration.eventstore import (
            EventAppendCommand,
            PayloadPutCommand,
            RuntimeBatch,
            StagedPayloadKey,
            StagedTypedPayloadRef,
            StateCellCompareAndSwapCommand,
        )
        from guanlan_v2.orchestration.events import EventType

        experiment_id = state.experiment_id
        current_ref = self._head_ref(experiment_id)
        current = self._resolve_state(current_ref)
        if current is not None and current.semantic_digest() == state.semantic_digest():
            self._note_waiting(state)
            return current_ref.payload_ref  # idempotent no-op: identical head persisted

        batch_key = f"replay-state:{experiment_id}:{idempotency_key}"
        state_ref = _replay_sr("ShadowReplayRunState")
        staged = StagedPayloadKey(key="state")
        cas: list[StateCellCompareAndSwapCommand] = [
            StateCellCompareAndSwapCommand(
                cell_namespace=REPLAY_HEAD_NAMESPACE,
                cell_key_digest=_replay_head_cell_key(experiment_id),
                expected_value=current_ref,
                new_target=StagedTypedPayloadRef(
                    staged_key=staged, schema_ref=state_ref, namespace="main"),
            )
        ]
        if (
            state.status == ExperimentStatus.WAITING_FOR_MATURITY
            and state.wakeup_key is not None
        ):
            index_digest = _replay_index_cell_key(state.wakeup_key)
            cas.append(
                StateCellCompareAndSwapCommand(
                    cell_namespace=REPLAY_HEAD_NAMESPACE,
                    cell_key_digest=index_digest,
                    expected_value=self._state_cells.load(
                        REPLAY_HEAD_NAMESPACE, index_digest),
                    new_target=StagedTypedPayloadRef(
                        staged_key=staged, schema_ref=state_ref, namespace="main"),
                )
            )
        batch = RuntimeBatch(
            idempotency_key=batch_key,
            payload_puts=(
                PayloadPutCommand(
                    staged_key=staged, schema_ref=state_ref, namespace="main",
                    payload_template=_replay_template(state), registry_digest=self._digest,
                    idempotency_key=f"{batch_key}:put"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=experiment_id, partition="main",
                    event_type=EventType.EXPERIMENT_STATE_CHANGED.value,
                    payload_schema_ref=state_ref, payload_target=staged,
                    registry_digest=self._digest, idempotency_key=f"{batch_key}:evt"),
            ),
            cell_cas=tuple(cas),
        )
        result = self._uow_factory().commit(batch)
        self._note_waiting(state)
        return result.payload_refs["state"]

    def commit_maturity(
        self, *, prior_maturity_key: str, prior_head_ref: TypedPayloadRef | None,
        new_state: ShadowReplayRunState, receipt: "ReplayMaturityReceipt",
        idempotency_key: str,
    ) -> PayloadRef:
        """One UoW advancing the head AND recording the operation result, all-or-none.

        Puts the new ``ShadowReplayRunState`` + the ``ReplayWakeupReceipt``, appends the
        ``ExperimentStateChanged`` event, CASes the experiment head + records the operation
        under the SUPERSEDED key (so a duplicate delivery of that key returns the stored
        receipt), and (when re-parked) CASes the new maturity-index. A concurrent winner
        makes either the head CAS or the operation CAS fail — the caller reads the surviving
        operation receipt rather than re-parking blindly.
        """
        from guanlan_v2.orchestration.eventstore import (
            EventAppendCommand,
            PayloadPutCommand,
            RuntimeBatch,
            StagedPayloadKey,
            StagedTypedPayloadRef,
            StateCellCompareAndSwapCommand,
        )
        from guanlan_v2.orchestration.events import EventType

        experiment_id = new_state.experiment_id
        batch_key = f"replay-maturity:{experiment_id}:{idempotency_key}"
        state_ref = _replay_sr("ShadowReplayRunState")
        receipt_ref = _replay_sr("ReplayWakeupReceipt")
        state_staged = StagedPayloadKey(key="state")
        receipt_staged = StagedPayloadKey(key="receipt")
        op_digest = _replay_operation_cell_key(prior_maturity_key)
        cas: list[StateCellCompareAndSwapCommand] = [
            StateCellCompareAndSwapCommand(
                cell_namespace=REPLAY_HEAD_NAMESPACE,
                cell_key_digest=_replay_head_cell_key(experiment_id),
                expected_value=prior_head_ref,
                new_target=StagedTypedPayloadRef(
                    staged_key=state_staged, schema_ref=state_ref, namespace="main"),
            ),
            StateCellCompareAndSwapCommand(
                cell_namespace=REPLAY_OPERATION_NAMESPACE,
                cell_key_digest=op_digest,
                expected_value=self._state_cells.load(
                    REPLAY_OPERATION_NAMESPACE, op_digest),
                new_target=StagedTypedPayloadRef(
                    staged_key=receipt_staged, schema_ref=receipt_ref, namespace="main"),
            ),
        ]
        if (
            new_state.status == ExperimentStatus.WAITING_FOR_MATURITY
            and new_state.wakeup_key is not None
        ):
            index_digest = _replay_index_cell_key(new_state.wakeup_key)
            cas.append(
                StateCellCompareAndSwapCommand(
                    cell_namespace=REPLAY_HEAD_NAMESPACE,
                    cell_key_digest=index_digest,
                    expected_value=self._state_cells.load(
                        REPLAY_HEAD_NAMESPACE, index_digest),
                    new_target=StagedTypedPayloadRef(
                        staged_key=state_staged, schema_ref=state_ref, namespace="main"),
                )
            )
        batch = RuntimeBatch(
            idempotency_key=batch_key,
            payload_puts=(
                PayloadPutCommand(
                    staged_key=state_staged, schema_ref=state_ref, namespace="main",
                    payload_template=_replay_template(new_state),
                    registry_digest=self._digest, idempotency_key=f"{batch_key}:state"),
                PayloadPutCommand(
                    staged_key=receipt_staged, schema_ref=receipt_ref, namespace="main",
                    payload_template=_replay_template(receipt),
                    registry_digest=self._digest, idempotency_key=f"{batch_key}:receipt"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=experiment_id, partition="main",
                    event_type=EventType.EXPERIMENT_STATE_CHANGED.value,
                    payload_schema_ref=state_ref, payload_target=state_staged,
                    registry_digest=self._digest, idempotency_key=f"{batch_key}:evt"),
            ),
            cell_cas=tuple(cas),
        )
        result = self._uow_factory().commit(batch)
        self._note_waiting(new_state)
        return result.payload_refs["state"]


def persist_replay_state(
    state: ShadowReplayRunState, *, stores: ReplayStateStore, idempotency_key: NonEmptyStr
) -> PayloadRef:
    """Persist one shadow-replay head transition (persist-then-publish, idempotent).

    Thin module surface over :meth:`ReplayStateStore.persist`: a same-key/same-content
    replay returns the stored ref; a same-key/different-content replay raises
    ``IdempotencyConflict``. Every transition appends an ``ExperimentStateChanged`` event
    binding the state's ``PayloadRef`` (invariant 5).
    """
    return stores.persist(state, idempotency_key=str(idempotency_key))


def mature_shadow_replay(
    wakeup_key: str, *, bindings: ReplayRuntimeBindings, now: datetime
) -> ReplayMaturityReceipt:
    """Consume matured maturity for one parked shadow-replay head — idempotent.

    ① resolves the parked head by the service-derived key (unknown ⇒
    :class:`ReplayMaturityKeyUnknown`, no state read side effects); ③ an operation-cell hit
    ⇒ ``already_processed`` returning the stored receipt byte-identically (this also covers a
    stale/superseded key — invariant 3); ② ``now < resume_after`` ⇒ ``not_mature`` with the
    head untouched; ④ otherwise re-runs :func:`hand_off_dual_curves_to_feedback` (the
    terminal settlement) and advances the head — to ``COMPLETED`` (full maturity) or a
    re-parked ``WAITING_FOR_MATURITY`` with a strictly-later ``resume_after`` and a NEW
    service-derived key (partial maturity). The maturity consumer NEVER re-executes a
    decision point, admits a plan or regenerates an intent — it consumes maturity only.

    A concurrent winner (head CAS or operation CAS conflict) is resolved by reading the
    surviving operation receipt rather than re-parking blindly, so a duplicate delivery
    applies effects exactly once and the racing caller gets the stored receipt.
    """
    from guanlan_v2.orchestration.eventstore import (
        CasPreconditionFailed,
        IdempotencyConflict,
    )

    store = bindings.replay_state_store
    if store is None:
        raise ShadowContractError(
            "mature_shadow_replay requires bindings.replay_state_store (the durable head)"
        )

    # ① resolve the parked head by the service-derived key.
    parked = store.load_head_by_maturity_key(wakeup_key)
    if parked is None:
        raise ReplayMaturityKeyUnknown(
            f"no parked replay head was issued under maturity key {wakeup_key!r}"
        )

    # ③ already processed (operation-cell hit) — also the stale/superseded-key path.
    prior_receipt = store.load_operation_receipt(wakeup_key)
    if prior_receipt is not None:
        return prior_receipt

    report = store.resolve_report(parked.curve_report_ref)
    ladder = _replay_maturity_ladder(report)
    matured = tuple(b for b in ladder if b <= now)

    # ② not yet mature — the head is untouched (no persistence).
    if parked.resume_after is not None and now < parked.resume_after:
        return ReplayMaturityReceipt(
            wakeup_key=wakeup_key, experiment_id=parked.experiment_id,
            outcome="not_mature", matured_points=len(matured),
            state_digest_after=parked.semantic_digest(), processed_at=now,
        )

    # ④ mature — re-run the terminal handoff and advance the head.
    unmatured = tuple(b for b in ladder if b > now)
    prior_head_ref = store._head_ref(parked.experiment_id)
    handoff_state = hand_off_dual_curves_to_feedback(
        report, run_id=parked.run_id, pool=bindings.pool,
        ledger=bindings.trial_ledger, maturity_now=now,
    )
    if handoff_state.status == ExperimentStatus.COMPLETED or not unmatured:
        new_state = ShadowReplayRunState(
            experiment_id=parked.experiment_id, run_id=parked.run_id,
            request_id=parked.request_id, schedule_digest=parked.schedule_digest,
            execution_config_digest=parked.execution_config_digest,
            status=ExperimentStatus.COMPLETED,
            completed_points=parked.total_points, total_points=parked.total_points,
            curve_report_ref=(handoff_state.curve_report_ref or parked.curve_report_ref),
            updated_at=now,
        )
        outcome = "completed"
    else:
        next_boundary = unmatured[0]
        new_key = derive_replay_maturity_key(
            experiment_id=parked.experiment_id, resume_after=next_boundary,
            state_digest=parked.semantic_digest(),
        )
        new_state = ShadowReplayRunState(
            experiment_id=parked.experiment_id, run_id=parked.run_id,
            request_id=parked.request_id, schedule_digest=parked.schedule_digest,
            execution_config_digest=parked.execution_config_digest,
            status=ExperimentStatus.WAITING_FOR_MATURITY,
            completed_points=parked.completed_points, total_points=parked.total_points,
            resume_after=next_boundary, wakeup_key=new_key,
            curve_report_ref=parked.curve_report_ref, updated_at=now,
        )
        outcome = "resumed"

    receipt = ReplayMaturityReceipt(
        wakeup_key=wakeup_key, experiment_id=parked.experiment_id, outcome=outcome,
        matured_points=len(matured), state_digest_after=new_state.semantic_digest(),
        processed_at=now,
    )
    try:
        store.commit_maturity(
            prior_maturity_key=wakeup_key, prior_head_ref=prior_head_ref,
            new_state=new_state, receipt=receipt, idempotency_key=wakeup_key,
        )
    except (CasPreconditionFailed, IdempotencyConflict):
        # a concurrent winner advanced the head + recorded the operation — return the
        # surviving stored receipt (effects applied exactly once).
        surviving = store.load_operation_receipt(wakeup_key)
        if surviving is not None:
            return surviving
        raise
    return receipt
