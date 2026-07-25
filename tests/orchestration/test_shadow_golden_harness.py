# -*- coding: utf-8 -*-
"""Phase 9 · Task 8 — mirror stage-② backend golden execution harness.

Loads the hand-authored, hand-computed golden fixture
``tests/orchestration/golden/shadow_execution_golden_v1.json`` and drives every case
through the REAL Phase-6 :class:`ShadowBacktestRunner` (over the frozen fa
``Broker`` / ``VirtualPortfolio`` / ``CostModel``), emitting ONE Task-1
:class:`MirrorHarnessReport`. Every expected fill / reject / cost / NAV / corporate
action in the fixture is computed BY HAND from the engine sources (never regenerated
from code); this harness only CONFIRMS the hand math.

The four required invariants:

1. **Offline + deterministic** — the runner consumes only the fixture bars (an
   in-memory loader/reader), no vendor / LLM / network anywhere; the ``ShadowRunResult``
   is content-sealed over semantic fields only, so the verdict is ordering/thread
   independent.
2. **Fixture-digest gate** — ``content_digest(loaded_fixture)`` must equal the pinned
   :data:`EXPECTED_FIXTURE_DIGEST` BEFORE any case runs; any drift fails the suite
   immediately, and the same digest is bound into ``MirrorHarnessReport.fixture_digest``.
3. **Version pins** — the fixture's ``matching_engine_version`` must equal the live
   :data:`SHADOW_MATCHING_ENGINE_VERSION` module constant (bumping the constant without a
   reviewed fixture edit fails here; editing the fixture's version string flips the digest
   gate). A behavior change that leaves the version untouched is caught by the golden
   expectations themselves going red.
4. **Engine untouched** — the six fa backtest module byte digests still equal the Task-0
   pins, recomputed IN-TEST (no git subprocess).

DRIVER DISPATCH (fixture ``driver`` field):

* ``run_targets`` — the deterministic dual-curve entry (band-exempt continuous weights);
  the primary vehicle for buy/sell/stop/take-profit/max-hold/corporate-action/multi-fill
  cases.
* ``run_intents`` — the LLM-intent lane (``run(intents)``); used only for the apply-once
  case (the intent lane enforces the closed target-weight band vocabulary, so it uses a
  band weight).
* ``broker`` — the REAL fa ``Broker`` directly, for the ONE case the runner's fixed
  limit/stop order construction cannot express: a **market order** (broker.py:113-119).
  This is the single flagged runner-unreachable case; it is still the frozen engine,
  offline and hand-computed.
* ``refuse_construct`` — a contract-boundary refusal (``CorporateActionEvent.kind`` is a
  closed Literal, so ``kind="rights_issue"`` fails Pydantic validation — a typed refusal,
  never a silent ledger effect).
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

# REAL fa engine primitives (the conftest prepends the in-repo engine fork).
from financial_analyst.backtest.broker import Broker, Order
from financial_analyst.backtest.costs import CostModel
from financial_analyst.backtest.portfolio import VirtualPortfolio

from guanlan_v2.orchestration.adapters.contracts import (
    MirrorHarnessCaseResult,
    MirrorHarnessReport,
)
from guanlan_v2.orchestration.adapters.luozi import (
    DeterministicTargetSet,
    ShadowBacktestRunner,
    ShadowRunConfig,
)
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.shadow import (
    SHADOW_MATCHING_ENGINE_VERSION,
    CorporateActionEvent,
    DecisionSchedule,
    TargetPortfolioIntent,
    TargetPosition,
    compute_cutoff_at,
    compute_eligible_execution_at,
    compute_scheduled_for,
)

# the Task-0-recorded engine byte-digest pins (invariant 4 — in-test recompute, no git).
from tests.orchestration.test_phase9_handoff import ENGINE_MODULE_BYTE_DIGESTS

# --------------------------------------------------------------------------- #
# pins + constants                                                            #
# --------------------------------------------------------------------------- #
FIXTURE_PATH = (
    Path(__file__).resolve().parent / "golden" / "shadow_execution_golden_v1.json"
)
#: the canonical semantic digest of the hand-authored fixture (a checksum of the
#: authored file, NOT an engine expectation); any fixture drift changes it and fails
#: the digest gate before any case runs.
EXPECTED_FIXTURE_DIGEST = (
    "eab12e5e7bf0d11092324338a261bca235a5cf61717e6d087a27c167d3db7080"
)

_CAL_ID = "ashare.xshg"
_TZ = "Asia/Shanghai"
_SCHEDULE_ID = "shadow.daily.ashare"

#: float comparison tolerance — the golden decimals are exact hand arithmetic; the
#: tolerance only absorbs IEEE-754 representation noise (mirrors the compat mirror's
#: ``COMPAT_PRICE_REL_TOL``).
_REL_TOL = 1e-9
_ABS_TOL = 1e-6


def _load_fixture() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _close(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=_REL_TOL, abs_tol=_ABS_TOL)


# --------------------------------------------------------------------------- #
# in-memory reader / loader (satisfy prepare_bar's real surface; fixture bars    #
# only — no vendor / network / LLM)                                              #
# --------------------------------------------------------------------------- #
class _MemLoader:
    def __init__(self, frames: dict):
        self._f: dict = {}
        for code, rows in frames.items():
            idx = pd.to_datetime([r[0] for r in rows])
            self._f[code] = pd.DataFrame(
                {
                    "open": [r[1] for r in rows],
                    "high": [r[2] for r in rows],
                    "low": [r[3] for r in rows],
                    "close": [r[4] for r in rows],
                    "vol": [r[5] for r in rows],
                    "factor": [1.0] * len(rows),
                },
                index=idx,
            )

    def _read_bin(self, code, field, freq):
        df = self._f.get(code)
        if df is None or field not in df.columns:
            return None
        return df[field]

    def fetch_quote(self, code, start, end, freq):
        df = self._f.get(code)
        if df is None:
            return None
        lo, hi = pd.Timestamp(start), pd.Timestamp(end)
        sub = df.loc[(df.index >= lo) & (df.index <= hi)]
        if len(sub) == 0:
            return None
        return sub.reset_index().rename(columns={"index": "trade_date"})


class _MemReader:
    def __init__(self, sessions):
        self._sessions = list(sessions)

    def trading_days(self, start=None, end=None):
        lo = start or self._sessions[0]
        hi = end or self._sessions[-1]
        return [d for d in self._sessions if lo <= d <= hi]


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _symbol(spec: dict) -> Symbol:
    return Symbol(code=spec["code"], exchange=spec["exchange"], board=spec["board"])


def _position(spec: dict) -> TargetPosition:
    kw = {}
    for k in ("stop_loss_pct", "take_profit_pct", "max_hold_bars"):
        if k in spec:
            kw[k] = spec[k]
    return TargetPosition(symbol=_symbol(spec), target_weight=spec["target_weight"], **kw)


def _target_set(t: dict) -> DeterministicTargetSet:
    return DeterministicTargetSet(
        rule_id=t["rule_id"],
        point_ordinal=t["point_ordinal"],
        target_version=t["target_version"],
        session_date=t["session_date"],
        positions=tuple(_position(p) for p in t["positions"]),
        cash_weight=t["cash_weight"],
    )


def _corp_event(spec: dict) -> CorporateActionEvent:
    return CorporateActionEvent(
        symbol=_symbol(spec),
        kind=spec["kind"],
        ex_date=spec["ex_date"],
        cash_per_share=spec["cash_per_share"],
        shares_ratio=spec["shares_ratio"],
        available_at=datetime.fromisoformat(spec["available_at"].replace("Z", "+00:00")),
    )


def _schedule(priority: str) -> DecisionSchedule:
    return DecisionSchedule.build(
        id=_SCHEDULE_ID,
        version="1",
        calendar_id=_CAL_ID,
        timezone=_TZ,
        kind="daily",
        decision_local_time="09:30",
        cutoff_local_time="09:00",
        bar_frequency="1d",
        execution_policy="next_open",
        execution_price_field="open",
        matching_engine_version=SHADOW_MATCHING_ENGINE_VERSION,
        intrabar_exit_priority=priority,
    )


def _calendar(sessions):
    return build_trading_calendar(
        calendar_id=_CAL_ID,
        sessions=[date.fromisoformat(s) for s in sessions],
        material_id="cal.ashare.golden",
        material_version="1",
    )


def _cost_model(overrides) -> CostModel:
    return CostModel(**(overrides or {}))


def _build_runner(case, cost_model, corporate_actions):
    sch = _schedule(case.get("intrabar_exit_priority", "worst_case"))
    return ShadowBacktestRunner(
        reader=_MemReader(case["sessions"]),
        loader=_MemLoader(case["frames"]),
        schedule=sch,
        schedule_ref=ContentRef(
            id=sch.id, version=sch.version, content_digest=sch.content_digest
        ),
        calendar=_calendar(case["sessions"]),
        cost_model=cost_model,
        init_cash=case["init_cash"],
        corporate_actions=corporate_actions,
        is_st=case.get("is_st"),
        clock=SystemClock(),
    )


def _build_intent(case, runner) -> TargetPortfolioIntent:
    spec = case["intent"]
    sch = runner._schedule
    cal = runner._calendar
    sd = spec["session_date"]
    scheduled_for = compute_scheduled_for(sch, session_date=sd, calendar=cal)
    eligible = compute_eligible_execution_at(
        sch, scheduled_for=scheduled_for, calendar=cal
    )
    # decision_as_of = scheduled_for: it sits at/after the cutoff and before eligible.
    _ = compute_cutoff_at(sch, session_date=sd)  # asserts the cutoff is computable
    return TargetPortfolioIntent(
        intent_id=spec["intent_id"],
        target_version=spec["target_version"],
        proposal_artifact_id="golden-prop",
        proposal_digest="a" * 64,
        source_decision_artifact_id="golden-dec",
        decision_schedule_id=sch.id,
        decision_schedule_version=sch.version,
        decision_schedule_digest=sch.content_digest,
        scheduled_for=scheduled_for,
        decision_as_of=scheduled_for,
        eligible_execution_at=eligible,
        positions=tuple(_position(p) for p in spec["positions"]),
        cash_weight=spec["cash_weight"],
        rationale="golden thesis",
        confidence=Confidence.MEDIUM,
        created_at=scheduled_for,
    )


# --------------------------------------------------------------------------- #
# comparison of a ShadowRunResult against a case's hand-computed expectation     #
# --------------------------------------------------------------------------- #
def _compare_result(result, expected: dict) -> list[str]:
    problems: list[str] = []

    # version pin travels on the emitted result too.
    if result.matching_engine_version != SHADOW_MATCHING_ENGINE_VERSION:
        problems.append(
            f"result matching_engine_version {result.matching_engine_version!r} "
            f"!= {SHADOW_MATCHING_ENGINE_VERSION!r}"
        )

    # --- fills (order-independent: keyed/sorted by (reason, price)) ---
    exp_fills = sorted(expected.get("fills", []), key=lambda f: (f["reason"], f["price"]))
    act_fills = sorted(result.fills, key=lambda f: (f.reason, f.price))
    if len(exp_fills) != len(act_fills):
        problems.append(
            f"fill count {len(act_fills)} != expected {len(exp_fills)} "
            f"(actual reasons={[f.reason for f in result.fills]})"
        )
    else:
        for e, a in zip(exp_fills, act_fills):
            if a.reason != e["reason"]:
                problems.append(f"fill reason {a.reason!r} != {e['reason']!r}")
                continue
            if a.qty != e["qty"]:
                problems.append(f"fill {e['reason']} qty {a.qty} != {e['qty']}")
            if not _close(a.price, e["price"]):
                problems.append(f"fill {e['reason']} price {a.price} != {e['price']}")
            if not _close(a.gross, e["gross"]):
                problems.append(f"fill {e['reason']} gross {a.gross} != {e['gross']}")
            if not _close(a.cost, e["cost"]):
                problems.append(f"fill {e['reason']} cost {a.cost} != {e['cost']}")

    # --- rejects (multiset of reasons) ---
    exp_rej = sorted(r["reason"] for r in expected.get("rejects", []))
    act_rej = sorted(r.reason for r in result.rejects)
    if exp_rej != act_rej:
        problems.append(f"rejects {act_rej} != expected {exp_rej}")

    # --- NAV series (dates exact, nav within tolerance) ---
    exp_nav = expected.get("nav_series")
    if exp_nav is not None:
        act_nav = list(result.nav_history)
        if len(act_nav) != len(exp_nav):
            problems.append(
                f"nav length {len(act_nav)} != {len(exp_nav)} (actual={act_nav})"
            )
        else:
            for (ed, en), (ad, an) in zip(exp_nav, act_nav):
                if ad != ed:
                    problems.append(f"nav date {ad!r} != {ed!r}")
                elif not _close(an, en):
                    problems.append(f"nav[{ed}] {an} != {en}")

    # --- badges (order-independent) ---
    exp_b = sorted(expected.get("badges", []))
    act_b = sorted(result.badges)
    if exp_b != act_b:
        problems.append(f"badges {act_b} != expected {exp_b}")

    # --- structural: no record swallowed (every order_id / fill_id unique) ---
    order_ids = [o.order_id for o in result.orders]
    if len(order_ids) != len(set(order_ids)):
        problems.append("duplicate order_id (a record was swallowed)")
    fill_ids = [f.fill_id for f in result.fills]
    if len(fill_ids) != len(set(fill_ids)):
        problems.append("duplicate fill_id (a record was swallowed)")

    # --- optional: required order kinds present (multi-fill) ---
    if "order_kinds_present" in expected:
        present = {o.order_kind for o in result.orders}
        for kind in expected["order_kinds_present"]:
            if kind not in present:
                problems.append(f"order_kind {kind!r} absent (present={sorted(present)})")

    # --- optional: exact applied count (apply-once) ---
    if "applied_count" in expected:
        applied = [a for a in result.applies if a.applied]
        if len(applied) != expected["applied_count"]:
            problems.append(
                f"applied_count {len(applied)} != expected {expected['applied_count']}"
            )

    return problems


# --------------------------------------------------------------------------- #
# per-driver runners                                                          #
# --------------------------------------------------------------------------- #
def _run_targets_case(case) -> list[str]:
    cost_model = _cost_model(case.get("cost_model"))
    corp = tuple(_corp_event(e) for e in case.get("corporate_actions", []))
    runner = _build_runner(case, cost_model, corp)
    run_config = ShadowRunConfig(
        start=case["start"],
        end=case["end"],
        init_cash=case["init_cash"],
        cost_model=cost_model,
        corporate_actions=corp,
        is_st=case.get("is_st"),
        lot_size=100,
    )
    result = runner.run_targets(
        tuple(_target_set(t) for t in case["targets"]),
        run_config=run_config,
        calendar=runner._calendar,
        clock=runner._clock,
    )
    return _compare_result(result, case["expected"])


def _run_intents_case(case) -> list[str]:
    cost_model = _cost_model(case.get("cost_model"))
    runner = _build_runner(case, cost_model, ())
    intent = _build_intent(case, runner)
    intents = tuple(intent for _ in range(case["duplicate_count"]))
    result = runner.run(intents, start=case["start"], end=case["end"])
    return _compare_result(result, case["expected"])


def _run_broker_case(case) -> list[str]:
    problems: list[str] = []
    cost_model = _cost_model(case.get("cost_model"))
    broker = Broker(cost_model)
    portfolio = VirtualPortfolio(
        init_cash=case["init_cash"], cash=case["init_cash"], cost_model=cost_model
    )
    o = case["broker_order"]
    order = Order(
        code=o["code"],
        side=o["side"],
        otype=o["otype"],
        limit_price=o.get("limit_price", 0.0),
        qty=o.get("qty"),
        cash_budget=o.get("cash_budget", 0.0),
        stop_loss=o.get("stop_loss", 0.0),
    )
    fill = broker.match(
        order,
        dict(case["bar"]),
        case["prev_close"],
        portfolio,
        next_bar_open=case.get("next_bar_open"),
        next_bar_date=case.get("next_bar_date"),
    )
    exp = case["expected"]
    if fill is not None:
        problems.append(f"expected reject but got a fill at {fill.price}")
    if broker.last_reason != exp["reject_reason"]:
        problems.append(
            f"reject reason {broker.last_reason!r} != {exp['reject_reason']!r}"
        )
    if not _close(portfolio.cash, exp["cash_after"]):
        problems.append(f"cash {portfolio.cash} != {exp['cash_after']}")
    if exp.get("positions_empty") and portfolio.positions:
        problems.append("positions not empty after a rejected order")
    return problems


def _run_refuse_case(case) -> list[str]:
    spec = case["corporate_action"]
    try:
        _corp_event(spec)
    except ValidationError:
        return []  # correctly refused at the contract boundary
    except Exception as exc:  # noqa: BLE001 — any other error is itself a failure
        return [f"expected ValidationError, got {type(exc).__name__}: {exc}"]
    return [
        f"CorporateActionEvent(kind={spec['kind']!r}) constructed — it must be refused"
    ]


_DISPATCH = {
    "run_targets": _run_targets_case,
    "run_intents": _run_intents_case,
    "broker": _run_broker_case,
    "refuse_construct": _run_refuse_case,
}


def _run_case(case) -> tuple[bool, str | None]:
    driver = case["driver"]
    runner = _DISPATCH.get(driver)
    if runner is None:
        return False, f"unknown driver {driver!r}"
    try:
        problems = runner(case)
    except Exception as exc:  # noqa: BLE001 — a raised case is a failed case, not a crash
        return False, f"harness raised {type(exc).__name__}: {exc}"
    if problems:
        return False, "; ".join(problems)
    return True, None


# =========================================================================== #
# invariant 2 — fixture-digest gate + invariant 3 — version pin                #
# =========================================================================== #
def test_fixture_digest_gate_and_version_pin():
    fixture = _load_fixture()
    # digest gate — any drift in the hand-authored fixture fails here.
    assert content_digest(fixture) == EXPECTED_FIXTURE_DIGEST, (
        "fixture drift: recompute EXPECTED_FIXTURE_DIGEST only via a reviewed edit"
    )
    # version pin — a module-constant bump without a reviewed fixture edit fails here;
    # editing the fixture version string flips the digest gate above.
    assert fixture["matching_engine_version"] == SHADOW_MATCHING_ENGINE_VERSION


# =========================================================================== #
# invariant 4 — engine byte-untouched (in-test recompute, no git)              #
# =========================================================================== #
def test_engine_untouched():
    import financial_analyst

    backtest = Path(financial_analyst.__file__).resolve().parent / "backtest"
    for name, frozen in ENGINE_MODULE_BYTE_DIGESTS.items():
        raw = (backtest / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == frozen, (
            f"engine module {name} byte digest drifted — the engine was touched"
        )


# =========================================================================== #
# the harness — drive every case, emit ONE MirrorHarnessReport                  #
# =========================================================================== #
def test_shadow_golden_harness():
    fixture = _load_fixture()

    # invariant 2: the digest gate runs BEFORE any case.
    assert content_digest(fixture) == EXPECTED_FIXTURE_DIGEST
    assert fixture["matching_engine_version"] == SHADOW_MATCHING_ENGINE_VERSION

    results = []
    for case in fixture["cases"]:
        passed, reason = _run_case(case)
        results.append(
            MirrorHarnessCaseResult(case_id=case["case_id"], passed=passed, reason=reason)
        )
    results.sort(key=lambda r: r.case_id)

    report = MirrorHarnessReport(
        fixture_digest=content_digest(fixture),
        matching_engine_version=SHADOW_MATCHING_ENGINE_VERSION,
        results=tuple(results),
        all_passed=all(r.passed for r in results),
    )

    # surface every failing case's hand-math reason if the harness is red.
    failing = [(r.case_id, r.reason) for r in report.results if not r.passed]
    assert report.all_passed, f"golden cases failed: {failing}"

    # the report binds the fixture digest and the pinned engine version.
    assert report.fixture_digest == EXPECTED_FIXTURE_DIGEST
    assert report.matching_engine_version == SHADOW_MATCHING_ENGINE_VERSION
    assert len(report.results) == len(fixture["cases"])


# =========================================================================== #
# a red harness names the offending case (the report contract's failed⇔reason)  #
# =========================================================================== #
def test_report_contract_failed_case_names_a_reason():
    # a synthetic failed verdict must carry a reason (biconditional), and all_passed
    # must be coherent — this guards the report we build above.
    ok = MirrorHarnessCaseResult(case_id="a.pass", passed=True)
    bad = MirrorHarnessCaseResult(case_id="b.fail", passed=False, reason="hand math x")
    rpt = MirrorHarnessReport(
        fixture_digest="0" * 64,
        matching_engine_version=SHADOW_MATCHING_ENGINE_VERSION,
        results=(ok, bad),
        all_passed=False,
    )
    assert rpt.all_passed is False
    with pytest.raises(ValidationError):
        MirrorHarnessCaseResult(case_id="c.bad", passed=False)  # failed w/o reason
    with pytest.raises(ValidationError):
        MirrorHarnessCaseResult(case_id="d.bad", passed=True, reason="x")  # passed w/reason
