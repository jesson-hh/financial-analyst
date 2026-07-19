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

import math
from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, ClassVar, Literal
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, field_validator

from guanlan_v2.orchestration.data.calendar import ImmutableTradingCalendar
from guanlan_v2.orchestration.data.symbols import Symbol, normalize_symbol
from guanlan_v2.orchestration.digest import (
    ContractModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
)
from guanlan_v2.orchestration.shadow import (
    DecisionSchedule,
    ShadowContractError,
    ShadowOrderKind,
    TargetPortfolioIntent,
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
