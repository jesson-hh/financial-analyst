# -*- coding: utf-8 -*-
"""Phase 6 - Task 5 - the engine-shaped, zero-LLM ``ShadowDecisionAgent``.

Written test-first (RED until ``guanlan_v2.orchestration.adapters.luozi`` exists).
Proves the six required invariants of the brief with the agent driven against the
REAL fa engine ``Decision`` / ``DecisionInput`` / ``DecisionLeg`` classes (never
instantiating the engine's stock backtest runner):

1. determinism of the underlying diff (covered in ``test_shadow_diff.py``);
2. sells precede buys (diff test);
3. idempotence at the plan level (diff test);
4. engine agent-shape conformance (``NAME`` / ``n_calls == 0`` before+after /
   coroutine ``decide`` returning an engine ``Decision``) - duck-typed here;
5. totality: any ``DecisionInput`` date yields a valid ``Decision`` (all-hold
   default), never an exception for a missing intent;
6. runner-consumed-only red line: ``adapters.luozi`` neither imports nor
   references the engine's stock runner / leg->order normalization path, and the
   hazard is documented on the agent (docstring citing engine.py:165).

Run from repo root: ``pytest tests/orchestration/test_shadow_agent.py -v``.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

# REAL fa engine classes (the conftest prepends the in-repo engine fork).
from financial_analyst.backtest.decision import (
    Decision,
    DecisionAgent,
    DecisionInput,
    DecisionLeg,
)
from financial_analyst.backtest.pit_reader import VisibleInfo

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.adapters import luozi
from guanlan_v2.orchestration.adapters.luozi import (
    ShadowApplyConflict,
    ShadowDecisionAgent,
    diff_target_portfolio,
)
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.shadow import ShadowContractError, target_apply_key

UTC = timezone.utc
_CAL_ID = "ashare.xshg"
_WEEK = ("2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24")
_SCHED_UTC = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)   # 09:30 CST 07-20
_ELIGIBLE_UTC = datetime(2026, 7, 21, 1, 30, tzinfo=UTC)  # 09:30 CST 07-21 -> session 07-21
_ELIGIBLE_SESSION = "2026-07-21"


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _sym(code: str = "600519", exchange: str = "SH", board: str = "main") -> Symbol:
    return Symbol(code=code, exchange=exchange, board=board)


def _pos(weight: float, *, code: str = "600519", exchange: str = "SH",
         board: str = "main", **kw):
    return shadow.TargetPosition(symbol=_sym(code, exchange, board),
                                 target_weight=weight, **kw)


def _intent(positions, cash_weight, **over):
    base = dict(
        intent_id="intent-1",
        target_version=1,
        proposal_artifact_id="art-prop-1",
        proposal_digest="a" * 64,
        source_decision_artifact_id="dec-art-1",
        decision_schedule_id="shadow.daily.ashare",
        decision_schedule_version="1",
        decision_schedule_digest="b" * 64,
        scheduled_for=_SCHED_UTC,
        decision_as_of=_SCHED_UTC,
        eligible_execution_at=_ELIGIBLE_UTC,
        positions=tuple(positions),
        cash_weight=cash_weight,
        rationale="thesis",
        confidence=Confidence.MEDIUM,
        created_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
    )
    base.update(over)
    return shadow.TargetPortfolioIntent(**base)


def _schedule(**overrides):
    base = dict(
        id="shadow.daily.ashare",
        version="1",
        calendar_id=_CAL_ID,
        timezone="Asia/Shanghai",
        kind="daily",
        decision_local_time="09:30",
        cutoff_local_time="09:00",
        bar_frequency="1d",
        execution_policy="next_open",
        execution_price_field="open",
        matching_engine_version=shadow.SHADOW_MATCHING_ENGINE_VERSION,
    )
    base.update(overrides)
    return shadow.DecisionSchedule.build(**base)


def _calendar(sessions_iso=_WEEK, *, calendar_id=_CAL_ID):
    return build_trading_calendar(
        calendar_id=calendar_id,
        sessions=[datetime.fromisoformat(s).date() for s in sessions_iso],
        material_id="cal.ashare.2026",
        material_version="1",
    )


def _agent(intents, **kw):
    return ShadowDecisionAgent(
        intents=tuple(intents),
        calendar=kw.pop("calendar", _calendar()),
        schedule=kw.pop("schedule", _schedule()),
        **kw,
    )


def _pos_dict(qty, price, *, stop_loss=0.0, avg_cost=None):
    return {"qty": qty, "avg_cost": avg_cost if avg_cost is not None else price,
            "stop_loss": stop_loss, "mkt_value": qty * price}


def _inp(date, holdings=None, *, nav=10000.0, cash=0.0):
    return DecisionInput(
        date=date, as_of="09:30",
        visible=VisibleInfo(date=date, as_of="09:30", boundary_ts=f"{date} 09:30:00",
                            news=[], events=[], policy=[], market_eod_prev=None),
        candidates=[], rev20_rank={}, holdings=holdings or {}, cash=cash, nav=nav,
    )


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# invariant 4 - engine agent shape                                            #
# --------------------------------------------------------------------------- #
def test_name_is_the_pinned_shadow_agent_name():
    assert ShadowDecisionAgent.NAME == "shadow-intent-agent"
    assert _agent([_intent([_pos(0.5)], 0.5)]).NAME == "shadow-intent-agent"


def test_agent_conforms_to_engine_agent_shape_without_a_runner():
    agent = _agent([_intent([_pos(0.5)], 0.5)])
    # n_calls is a property (as on the real engine DecisionAgent) always 0
    assert isinstance(inspect.getattr_static(ShadowDecisionAgent, "n_calls"), property)
    assert isinstance(inspect.getattr_static(DecisionAgent, "n_calls"), property)
    assert agent.n_calls == 0
    # decide is an async coroutine function, matching the engine agent contract
    assert inspect.iscoroutinefunction(agent.decide)
    assert inspect.iscoroutinefunction(DecisionAgent.decide)


def test_n_calls_stays_zero_before_and_after_decide():
    agent = _agent([_intent([_pos(0.5)], 0.5)])
    assert agent.n_calls == 0
    _run(agent.decide(_inp(_ELIGIBLE_SESSION,
                           {"SH600519": _pos_dict(200, 10.0)})))
    assert agent.n_calls == 0


def test_decide_returns_a_real_engine_decision_with_engine_legs():
    agent = _agent([_intent([_pos(0.5)], 0.5)])
    dec = _run(agent.decide(_inp(_ELIGIBLE_SESSION,
                                 {"SH600519": _pos_dict(200, 10.0)})))
    assert isinstance(dec, Decision)
    assert isinstance(dec.decisions, list)
    for leg in dec.decisions:
        assert isinstance(leg, DecisionLeg)


# --------------------------------------------------------------------------- #
# invariant 5 - totality / all-hold default                                   #
# --------------------------------------------------------------------------- #
def test_missing_intent_session_yields_all_hold_never_raises():
    agent = _agent([_intent([_pos(0.5)], 0.5)])  # eligible only on 2026-07-21
    dec = _run(agent.decide(_inp("2026-07-22")))  # a session with no intent
    assert isinstance(dec, Decision)
    assert dec.decisions == []


def test_agent_is_total_across_arbitrary_dates():
    agent = _agent([_intent([_pos(0.5)], 0.5)])
    for d in ("2026-07-20", "2026-07-21", "2026-07-22", "2026-01-01", "2027-12-31"):
        dec = _run(agent.decide(_inp(d, {"SH600519": _pos_dict(100, 10.0)})))
        assert isinstance(dec, Decision)


def test_agent_never_fabricates_an_intent_nor_mutates_its_own():
    intent = _intent([_pos(0.5)], 0.5)
    agent = _agent([intent])
    before = intent.semantic_digest()
    _run(agent.decide(_inp("2026-07-23")))  # no intent for this session
    assert intent.semantic_digest() == before  # frozen, untouched


# --------------------------------------------------------------------------- #
# the closed leg-mapping table                                                #
# --------------------------------------------------------------------------- #
def test_full_exit_maps_to_action_sell():
    agent = _agent([_intent([_pos(0.0, code="600519")], 1.0)])
    dec = _run(agent.decide(_inp(_ELIGIBLE_SESSION,
                                 {"SH600519": _pos_dict(300, 10.0)})))
    legs = {l.code: l for l in dec.decisions}
    assert legs["SH600519"].action == "sell"


def test_partial_reduce_maps_to_action_reduce():
    agent = _agent([_intent([_pos(0.25, code="600519")], 0.75)])
    dec = _run(agent.decide(_inp(_ELIGIBLE_SESSION,
                                 {"SH600519": _pos_dict(500, 10.0)})))
    legs = {l.code: l for l in dec.decisions}
    # target 250, held 500 -> reduce 250 (partial)
    assert legs["SH600519"].action == "reduce"


def test_buy_maps_to_action_buy_with_delta_weight_and_stop_loss():
    agent = _agent([_intent([_pos(0.5, code="600519", stop_loss_pct=0.1)], 0.5)])
    dec = _run(agent.decide(_inp(_ELIGIBLE_SESSION,
                                 {"SH600519": _pos_dict(200, 10.0)}, nav=10000.0)))
    leg = {l.code: l for l in dec.decisions}["SH600519"]
    assert leg.action == "buy"
    # delta 300 shares @10 = 3000 budget -> 30% of nav
    assert leg.weight_pct == pytest.approx(30.0)
    # stop_loss absolute = price * (1 - 0.1) = 9.0
    assert leg.stop_loss == pytest.approx(9.0)


def test_leg_reason_is_the_semantic_digest_prefix():
    intent = _intent([_pos(0.5, code="600519")], 0.5)
    agent = _agent([intent])
    dec = _run(agent.decide(_inp(_ELIGIBLE_SESSION,
                                 {"SH600519": _pos_dict(200, 10.0)})))
    expected = "shadow-intent:" + intent.semantic_digest()[:16]
    assert dec.decisions
    for leg in dec.decisions:
        assert leg.reason == expected


def test_unpriceable_target_is_an_honest_skip_surfaced_as_a_warning():
    # a target the agent cannot price (not held) is skipped honestly, never a buy.
    agent = _agent([_intent([_pos(0.5, code="000001", exchange="SZ",
                                  board="main")], 0.5)])
    dec = _run(agent.decide(_inp(_ELIGIBLE_SESSION, {})))  # empty portfolio
    assert dec.decisions == []
    assert any("no_reference_price" in w for w in dec.warnings)


# --------------------------------------------------------------------------- #
# constructor conflict / collapse                                             #
# --------------------------------------------------------------------------- #
def test_same_apply_key_different_content_raises_apply_conflict():
    a = _intent([_pos(0.5, code="600519")], 0.5)
    b = _intent([_pos(0.25, code="600519")], 0.75)  # same apply-key fields, diff book
    assert target_apply_key(a) == target_apply_key(b)
    assert a.semantic_digest() != b.semantic_digest()
    with pytest.raises(ShadowApplyConflict):
        _agent([a, b])


def test_apply_conflict_is_a_shadow_contract_error():
    assert issubclass(ShadowApplyConflict, ShadowContractError)


def test_identical_duplicate_intents_collapse_without_error():
    a = _intent([_pos(0.5, code="600519")], 0.5)
    b = _intent([_pos(0.5, code="600519")], 0.5)  # byte-identical
    agent = _agent([a, b])  # must not raise
    dec = _run(agent.decide(_inp(_ELIGIBLE_SESSION,
                                 {"SH600519": _pos_dict(200, 10.0)})))
    assert isinstance(dec, Decision)


def test_two_distinct_intents_on_the_same_session_raise_apply_conflict():
    a = _intent([_pos(0.5, code="600519")], 0.5, intent_id="intent-A")
    b = _intent([_pos(0.25, code="000001", exchange="SZ", board="main")], 0.75,
                intent_id="intent-B")
    assert target_apply_key(a) != target_apply_key(b)  # distinct apply keys
    # both eligible on the same execution session -> ambiguous, refused
    with pytest.raises(ShadowApplyConflict):
        _agent([a, b])


# --------------------------------------------------------------------------- #
# invariant 6 - runner-consumed-only red line                                 #
# --------------------------------------------------------------------------- #
def test_module_never_references_the_engine_stock_runner_or_leg_normalizer():
    src = Path(luozi.__file__).read_text(encoding="utf-8")
    assert "legs_to_orders" not in src
    # the engine's own stock runner class ``BacktestRunner`` is never referenced.
    # Task 6 adds the shadow runner ``ShadowBacktestRunner`` (a distinct class) — so
    # the check excludes that prefix rather than matching the bare substring.
    assert re.search(r"(?<!Shadow)BacktestRunner", src) is None
    # not present as module attributes either (the module exposes ShadowBacktestRunner)
    assert not hasattr(luozi, "legs_to_orders")
    assert not hasattr(luozi, "BacktestRunner")


def test_only_engine_import_is_the_decision_module():
    src = Path(luozi.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fa_targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("financial_analyst"):
            fa_targets.append(node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("financial_analyst"):
                    fa_targets.append(alias.name)
    assert fa_targets, "the agent must consume the engine decision module"
    for module in fa_targets:
        assert module == "financial_analyst.backtest.decision", (
            f"only the engine decision module may be imported, got {module!r}"
        )


def test_hazard_is_documented_on_the_agent_citing_engine_line():
    assert ShadowDecisionAgent.__doc__ is not None
    assert "engine.py:165" in ShadowDecisionAgent.__doc__


# --------------------------------------------------------------------------- #
# completeness firewall - the module is Phase-6 scoped, carriers unregistered   #
# --------------------------------------------------------------------------- #
def test_completeness_firewall_scopes_the_adapters_module():
    completeness = importlib.import_module(
        "tests.orchestration.test_contract_completeness"
    )
    assert "guanlan_v2.orchestration.adapters.luozi" in completeness.PHASE6_MODULES
    # the internal carriers never leak into the sealed Phase-1 registry
    from guanlan_v2.orchestration.schema_registry import default_registry
    registered = {e.schema_ref.name for e in default_registry().manifest()}
    for name in ("ShadowOrderPlanEntry", "ShadowOrderSkip", "ShadowOrderPlan"):
        assert name not in registered
