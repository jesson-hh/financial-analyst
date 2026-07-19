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
from collections.abc import Mapping
from datetime import date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Literal
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
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, SystemClock
from guanlan_v2.orchestration.shadow import (
    CorporateActionEvent,
    DecisionSchedule,
    IsoDateStr,
    SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN,
    SHADOW_MATCHING_ENGINE_VERSION,
    ShadowContractError,
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
    compute_eligible_execution_at,
    compute_scheduled_for,
    deterministic_apply_key_parts,
    shadow_fill_id,
    shadow_order_id,
    target_apply_key,
)

if TYPE_CHECKING:  # annotations only (PEP 563) - never forces the engine onto the path
    from financial_analyst.backtest.decision import Decision, DecisionInput

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
        for T in days:
            events_today = [e for e in self._corporate_actions if e.ex_date == T]
            if events_today:  # step 2 seam — reached only with matching events (Task 7)
                self._apply_corporate_actions(portfolio, events_today, T)
            for u in sorted(by_session.get(T, []), key=lambda u: (u.scheduled_for, u.dedup_key)):
                if u.dedup_key in applied:
                    continue
                applied.add(u.dedup_key)
                applies.append(
                    self._apply_unit(u, T, portfolio, broker, orders, fills, rejects, warnings, trade_log)
                )
            if self._pending_exit_state(portfolio):  # step 4 seam — only with exit state (Task 7)
                self._manage_exits(portfolio, T, broker, orders, fills, rejects, trade_log)
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
        self, u, T, portfolio, broker, orders, fills, rejects, warnings, trade_log
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
    def _pending_exit_state(portfolio) -> bool:
        """Whether any held position carries an active stop — the ONLY condition under
        which the Task-7 exit-management seam (step 4) is invoked. Task 6 target runs
        that set no stop never reach it."""
        return any(pos.stop_loss > 0 for pos in portfolio.positions.values())

    # ------------------------------------------------------------------ #
    # Task-7 seams (reached only when there is matching event / exit state)#
    # ------------------------------------------------------------------ #
    def _apply_corporate_actions(self, portfolio, events, T) -> None:
        """Step 2 seam — Task 7 supplies ex_date corporate-action application."""
        raise NotImplementedError(
            "corporate-action application (step 2) is deferred to Task 7; it is reached "
            "only when an event's ex_date falls on a run day"
        )

    def _manage_exits(self, portfolio, T, broker, orders, fills, rejects, trade_log) -> None:
        """Step 4 seam — Task 7 supplies stop/take/max-hold exit-order generation."""
        raise NotImplementedError(
            "exit management (step 4) is deferred to Task 7; it is reached only when a "
            "held position carries active exit state"
        )

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
