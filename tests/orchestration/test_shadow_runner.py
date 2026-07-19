# -*- coding: utf-8 -*-
"""Phase 6 · Task 6 — the ``ShadowBacktestRunner`` apply-once loop + dual-curve lane.

Drives the REAL fa backtest engine primitives (``Broker`` / ``Order`` /
``VirtualPortfolio`` / ``CostModel`` / ``prepare_bar`` / ``compute_metrics`` /
``TradeLog``) through a small, deterministic in-memory ``reader`` / ``loader`` fixture
(OHLCV frames including a suspension day, a one-word limit-up day, and a T+1
scenario). Covers the nine required invariants plus the deterministic lane
(same-bar equivalence, key-family disjointness, no-intent-minted, config-digest
mismatch refusal).

The conftest prepends the in-repo engine fork onto ``sys.path``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

# REAL fa engine cost model (the conftest prepends the in-repo engine fork).
from financial_analyst.backtest.costs import CostModel

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.adapters.luozi import (
    DeterministicTargetSet,
    ShadowApplyConflict,
    ShadowBacktestRunner,
    ShadowRunConfig,
    SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN,
    deterministic_apply_key,
)
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.shadow import (
    CorporateActionEvent,
    ShadowContractError,
    ShadowRunResult,
    TargetPosition,
    TargetPortfolioIntent,
    UnsupportedBarFrequencyError,
    target_apply_key,
)

UTC = timezone.utc
_CAL_ID = "ashare.xshg"
_TZ = "Asia/Shanghai"
_SESSIONS = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
_SEED_DAY = "2026-07-17"

# 09:30 CST == 01:30 UTC
_DECISION_0720 = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)
_DECISION_0720_LATE = datetime(2026, 7, 20, 1, 31, tzinfo=UTC)
_ELIGIBLE_0721 = datetime(2026, 7, 21, 1, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# in-memory reader / loader satisfying prepare_bar's real surface             #
# --------------------------------------------------------------------------- #
class _MemLoader:
    """Satisfies prepare_bar (day freq): ``_read_bin`` (close/factor) + ``fetch_quote``."""

    def __init__(self, frames):
        self._f = {}
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
    """Satisfies the runner's only reader dependency: ``trading_days``."""

    def __init__(self, sessions):
        self._sessions = list(sessions)

    def trading_days(self, start=None, end=None):
        lo = start or self._sessions[0]
        hi = end or self._sessions[-1]
        return [d for d in self._sessions if lo <= d <= hi]


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _sym(code="600000", exchange="SH", board="main"):
    return Symbol(code=code, exchange=exchange, board=board)


def _pos(weight, *, code="600000", exchange="SH", board="main", **kw):
    return TargetPosition(symbol=_sym(code, exchange, board), target_weight=weight, **kw)


def _schedule(**over):
    base = dict(
        id="shadow.daily.ashare",
        version="1",
        calendar_id=_CAL_ID,
        timezone=_TZ,
        kind="daily",
        decision_local_time="09:30",
        cutoff_local_time="09:00",
        bar_frequency="1d",
        execution_policy="next_open",
        execution_price_field="open",
        matching_engine_version=shadow.SHADOW_MATCHING_ENGINE_VERSION,
    )
    base.update(over)
    return shadow.DecisionSchedule.build(**base)


def _sched_ref(sch):
    return ContentRef(id=sch.id, version=sch.version, content_digest=sch.content_digest)


def _calendar(sessions=_SESSIONS, *, calendar_id=_CAL_ID):
    return build_trading_calendar(
        calendar_id=calendar_id,
        sessions=[datetime.fromisoformat(s).date() for s in sessions],
        material_id="cal.ashare.2026",
        material_version="1",
    )


def _intent(positions, cash_weight, *, scheduled_for=_DECISION_0720,
            eligible=_ELIGIBLE_0721, intent_id="intent-1", target_version=1, **over):
    base = dict(
        intent_id=intent_id,
        target_version=target_version,
        proposal_artifact_id="art-prop-1",
        proposal_digest="a" * 64,
        source_decision_artifact_id="dec-art-1",
        decision_schedule_id="shadow.daily.ashare",
        decision_schedule_version="1",
        decision_schedule_digest="b" * 64,
        scheduled_for=scheduled_for,
        decision_as_of=scheduled_for,
        eligible_execution_at=eligible,
        positions=tuple(positions),
        cash_weight=cash_weight,
        rationale="thesis",
        confidence=Confidence.MEDIUM,
        created_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
    )
    base.update(over)
    return TargetPortfolioIntent(**base)


def _target_set(positions, cash_weight, *, rule_id="rule.equal", point_ordinal=0,
                target_version=1, session_date="2026-07-20"):
    return DeterministicTargetSet(
        rule_id=rule_id,
        point_ordinal=point_ordinal,
        target_version=target_version,
        session_date=session_date,
        positions=tuple(positions),
        cash_weight=cash_weight,
    )


def _runner(frames, *, sessions=_SESSIONS, cost_model=None, init_cash=1_000_000.0,
            corporate_actions=(), is_st=None, lot_size=100, schedule=None,
            calendar=None, clock=None):
    sch = schedule or _schedule()
    return ShadowBacktestRunner(
        reader=_MemReader(sessions),
        loader=_MemLoader(frames),
        schedule=sch,
        schedule_ref=_sched_ref(sch),
        calendar=calendar or _calendar(sessions),
        cost_model=cost_model or CostModel(),
        init_cash=init_cash,
        corporate_actions=corporate_actions,
        is_st=is_st,
        lot_size=lot_size,
        clock=clock or SystemClock(),
    )


# a normal alpha frame: seed 07-17 close 10, then a buyable rising week.
_ALPHA = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-21", 10.0, 10.4, 9.8, 10.2, 1e6),
    ("2026-07-22", 10.2, 11.0, 10.1, 10.8, 1e6),
    ("2026-07-23", 10.8, 11.4, 10.7, 11.0, 1e6),
    ("2026-07-24", 11.0, 11.6, 10.9, 11.2, 1e6),
]


def _alpha_frames():
    return {"SH600000": list(_ALPHA)}


# =========================================================================== #
# constructor validation                                                      #
# =========================================================================== #
def test_constructor_rejects_schedule_ref_triple_mismatch():
    sch = _schedule()
    bad_ref = ContentRef(id=sch.id, version=sch.version, content_digest="c" * 64)
    with pytest.raises(ShadowContractError):
        ShadowBacktestRunner(
            reader=_MemReader(_SESSIONS), loader=_MemLoader(_alpha_frames()),
            schedule=sch, schedule_ref=bad_ref, calendar=_calendar(),
            cost_model=CostModel(),
        )


def test_constructor_rejects_wrong_matching_engine_version():
    sch = _schedule(matching_engine_version="shadow-match-v2")
    with pytest.raises(ShadowContractError):
        ShadowBacktestRunner(
            reader=_MemReader(_SESSIONS), loader=_MemLoader(_alpha_frames()),
            schedule=sch, schedule_ref=_sched_ref(sch), calendar=_calendar(),
            cost_model=CostModel(),
        )


def test_constructor_refuses_non_1d_bar_frequency():
    sch = _schedule(bar_frequency="5m")
    with pytest.raises(UnsupportedBarFrequencyError):
        ShadowBacktestRunner(
            reader=_MemReader(_SESSIONS), loader=_MemLoader(_alpha_frames()),
            schedule=sch, schedule_ref=_sched_ref(sch), calendar=_calendar(),
            cost_model=CostModel(),
        )


def test_constructor_rejects_calendar_id_mismatch():
    sch = _schedule()
    with pytest.raises(ShadowContractError):
        ShadowBacktestRunner(
            reader=_MemReader(_SESSIONS), loader=_MemLoader(_alpha_frames()),
            schedule=sch, schedule_ref=_sched_ref(sch),
            calendar=_calendar(calendar_id="ashare.other"), cost_model=CostModel(),
        )


def test_run_refuses_entry_tranches():
    tranche = shadow.TrancheTrigger(price_low=9.0, price_high=10.0, fraction=0.5)
    intent = _intent([_pos(0.5, entry_tranches=(tranche,))], 0.5)
    r = _runner(_alpha_frames())
    with pytest.raises(ShadowContractError):
        r.run((intent,), start="2026-07-20", end="2026-07-24")


# =========================================================================== #
# a real buy end to end + result shape                                        #
# =========================================================================== #
def test_run_buys_and_produces_a_sealed_result():
    r = _runner(_alpha_frames())
    intent = _intent([_pos(0.5)], 0.5)
    res = r.run((intent,), start="2026-07-20", end="2026-07-24")
    assert isinstance(res, ShadowRunResult)
    # self-seal holds (re-validates on load)
    assert res.content_digest == res.semantic_digest()
    assert res.matching_engine_version == shadow.SHADOW_MATCHING_ENGINE_VERSION
    assert len(res.fills) == 1
    f = res.fills[0]
    assert f.side == "buy" and f.reason == "target_buy"
    assert f.trade_date == "2026-07-21"
    # exactly one apply, applied True, its order present
    assert len(res.applies) == 1 and res.applies[0].applied is True
    assert res.orders[0].order_id in res.applies[0].order_ids
    # nav path has the seed point + 5 sessions
    assert res.nav_history[0][0] == "2026-07-20"
    assert [d for d, _ in res.nav_history] == _SESSIONS


def test_trade_dates_are_iso_yyyy_mm_dd_end_to_end():
    r = _runner(_alpha_frames())
    res = r.run((_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24")
    for f in res.fills:
        assert f.trade_date == "2026-07-21"
        datetime.fromisoformat(f.trade_date)  # parses
    for d, _ in res.nav_history:
        datetime.fromisoformat(d)


def test_cost_and_slippage_match_cost_model_on_a_hand_computed_vector():
    cm = CostModel()
    r = _runner(_alpha_frames(), cost_model=cm)
    res = r.run((_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24")
    f = res.fills[0]
    # prev_close(07-21)=close[07-20]=10.0; up=round(10*1.05,2)=10.5;
    # raw=slip_buy(min(10.5, high=10.4))=10.4*1.0005=10.4052; clipped to high 10.4.
    assert f.price == pytest.approx(10.4, abs=1e-9)
    assert f.gross == pytest.approx(f.price * f.qty, abs=1e-6)
    # hand-computed A-share cost: max(gross*0.00025, 5) + gross*0.0001 (SH transfer)
    gross = f.price * f.qty
    expected_cost = max(gross * 0.00025, 5.0) + gross * 0.0001
    assert f.cost == pytest.approx(expected_cost, abs=1e-6)
    assert f.cost == pytest.approx(cm.buy_cost(f.price, f.qty, "SH600000"), abs=1e-9)


# =========================================================================== #
# invariant 2 — engine reject reasons pass through verbatim                    #
# =========================================================================== #
def test_suspended_bar_rejects_suspended_verbatim():
    frames = {"SH600001": [
        (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
        ("2026-07-20", 9.9, 10.1, 9.7, 10.0, 1e6),   # prev_close source
        ("2026-07-21", 10.0, 10.2, 9.9, 10.1, 0.0),  # valid OHLC, zero volume -> suspended
    ]}
    r = _runner(frames)
    res = r.run((_intent([_pos(0.5, code="600001")], 0.5),),
                start="2026-07-20", end="2026-07-24")
    assert res.fills == ()
    assert any(rj.reason == "suspended" for rj in res.rejects)


def test_one_word_limit_up_rejects_buy_verbatim():
    frames = {"SH600002": [
        (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
        ("2026-07-20", 9.9, 10.1, 9.7, 10.0, 1e6),   # prev_close 10.0
        ("2026-07-21", 11.0, 11.0, 11.0, 11.0, 1e6),  # one-word limit-up (10*1.1)
    ]}
    r = _runner(frames)
    res = r.run((_intent([_pos(0.5, code="600002")], 0.5),),
                start="2026-07-20", end="2026-07-24")
    assert res.fills == ()
    assert any(rj.reason == "one_word_limit_up" for rj in res.rejects)


def test_buy_below_one_lot_rejects_verbatim():
    # high-priced name: target qty floors to exactly 100 shares, but the fill
    # price (up ceiling) makes 100 shares unaffordable at the budget -> below_one_lot.
    frames = {"SZ000001": [
        (_SEED_DAY, 1990.0, 2000.0, 1980.0, 2000.0, 1e6),
        ("2026-07-20", 1990.0, 2010.0, 1980.0, 2000.0, 1e6),  # prev_close 2000
        ("2026-07-21", 2080.0, 2100.0, 2050.0, 2090.0, 1e6),  # up=2100 -> fill 2100
    ]}
    r = _runner(frames)
    res = r.run((_intent([_pos(0.25, code="000001", exchange="SZ")], 0.75),),
                start="2026-07-20", end="2026-07-24")
    assert res.fills == ()
    assert any(rj.reason == "below_one_lot" for rj in res.rejects)


def test_same_day_resell_of_t1_locked_buy_rejects_t1_locked_or_empty():
    # Two intents on the same execution bar (07-21): A buys X, B exits X the same
    # day -> the sell hits T+1-locked shares -> t1_locked_or_empty (verbatim).
    r = _runner(_alpha_frames())
    a = _intent([_pos(0.5)], 0.5, intent_id="intent-A", scheduled_for=_DECISION_0720)
    b = _intent([], 1.0, intent_id="intent-B", scheduled_for=_DECISION_0720_LATE)
    res = r.run((a, b), start="2026-07-20", end="2026-07-24")
    assert any(f.side == "buy" for f in res.fills)         # A filled
    assert any(rj.reason == "t1_locked_or_empty" for rj in res.rejects)  # B rejected


# =========================================================================== #
# invariant 3 — causation chain resolvable                                     #
# =========================================================================== #
def test_causation_chain_is_closed_and_resolvable():
    r = _runner(_alpha_frames())
    res = r.run((_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24")
    order_ids = {o.order_id for o in res.orders}
    apply_keys = {a.target_apply_key for a in res.applies}
    for f in res.fills:
        assert f.order_id in order_ids
    for o in res.orders:
        assert o.target_apply_key in apply_keys
    for a in res.applies:
        for oid in a.order_ids:
            assert oid in order_ids
    # the applied intent digest is referenced only via intent_content_digests
    assert res.applies[0].intent_content_digest in res.intent_content_digests


# =========================================================================== #
# invariant 1 — apply-once above the Broker                                    #
# =========================================================================== #
def test_duplicate_intent_tuple_is_apply_once_byte_identical():
    intent = _intent([_pos(0.5)], 0.5)
    r1 = _runner(_alpha_frames())
    r2 = _runner(_alpha_frames())
    single = r1.run((intent,), start="2026-07-20", end="2026-07-24")
    dup = r2.run((intent, intent), start="2026-07-20", end="2026-07-24")
    assert single.content_digest == dup.content_digest
    assert len(dup.fills) == 1  # the duplicate contributes no second order


def test_same_apply_key_different_content_conflicts():
    a = _intent([_pos(0.5)], 0.5, intent_id="same")
    b = _intent([_pos(0.25)], 0.75, intent_id="same")  # same apply key, diff content
    assert target_apply_key(a) == target_apply_key(b)
    r = _runner(_alpha_frames())
    with pytest.raises(ShadowApplyConflict):
        r.run((a, b), start="2026-07-20", end="2026-07-24")


# =========================================================================== #
# invariant — applied=False when the eligible bar is outside the window        #
# =========================================================================== #
def test_intent_eligible_outside_window_is_recorded_applied_false():
    r = _runner(_alpha_frames())
    intent = _intent([_pos(0.5)], 0.5)
    # window ends 07-20: eligible 07-21 is outside -> applied False, no orders
    res = r.run((intent,), start="2026-07-20", end="2026-07-20")
    assert res.fills == ()
    assert len(res.applies) == 1 and res.applies[0].applied is False
    assert res.applies[0].order_ids == ()


# =========================================================================== #
# invariant 6 — determinism / no seats / no mutation                           #
# =========================================================================== #
def test_two_runs_over_same_fixture_are_digest_identical():
    intent = _intent([_pos(0.5)], 0.5)
    a = _runner(_alpha_frames()).run((intent,), start="2026-07-20", end="2026-07-24")
    b = _runner(_alpha_frames()).run((intent,), start="2026-07-20", end="2026-07-24")
    assert a.content_digest == b.content_digest


def test_run_never_mutates_input_intents():
    intent = _intent([_pos(0.5)], 0.5)
    before = intent.semantic_digest()
    _runner(_alpha_frames()).run((intent,), start="2026-07-20", end="2026-07-24")
    assert intent.semantic_digest() == before


def test_module_never_imports_seats_or_network():
    import ast
    from pathlib import Path
    from guanlan_v2.orchestration.adapters import luozi
    src = Path(luozi.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("guanlan_v2.seats")
            assert node.module not in ("socket", "http", "urllib", "requests", "httpx")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("guanlan_v2.seats")


# =========================================================================== #
# badges                                                                       #
# =========================================================================== #
def test_st_flags_unavailable_badge_when_is_st_absent_and_order_matched():
    res = _runner(_alpha_frames()).run(
        (_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24")
    assert "st_flags_unavailable" in res.badges


def test_no_st_badge_when_is_st_supplied():
    res = _runner(_alpha_frames(), is_st={"SH600000": False}).run(
        (_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24")
    assert "st_flags_unavailable" not in res.badges


def test_corporate_actions_synthetic_badge_when_events_supplied():
    # an out-of-window ex_date so the (Task-7) corporate-action seam is never hit.
    ev = CorporateActionEvent(
        symbol=_sym(), kind="cash_dividend", ex_date="2026-08-15",
        cash_per_share=0.5, shares_ratio=0.0,
        available_at=datetime(2026, 8, 14, 1, 0, tzinfo=UTC),
    )
    res = _runner(_alpha_frames(), corporate_actions=(ev,)).run(
        (_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24")
    assert "corporate_actions_synthetic" in res.badges


# =========================================================================== #
# deterministic dual-curve lane                                                #
# =========================================================================== #
def _run_config(**over):
    base = dict(start="2026-07-20", end="2026-07-24", init_cash=1_000_000.0,
                cost_model=CostModel(), corporate_actions=(), is_st=None, lot_size=100)
    base.update(over)
    return ShadowRunConfig(**base)


def test_deterministic_target_set_allows_continuous_off_band_weights():
    # equal-weight-three ~ 1/3 each: off-band, but the deterministic lane is exempt.
    ts = _target_set(
        [_pos(1.0 / 3.0, code="600000"), _pos(1.0 / 3.0, code="600001")],
        1.0 - 2.0 / 3.0,
    )
    assert ts.cash_weight == pytest.approx(1.0 / 3.0)


def test_deterministic_target_set_still_rejects_duplicate_and_sum_violation():
    with pytest.raises(Exception):
        _target_set([_pos(0.5), _pos(0.5)], 0.5)  # duplicate symbol
    with pytest.raises(Exception):
        _target_set([_pos(0.5)], 0.9)  # weight-sum violation


def test_deterministic_apply_key_domain_and_disjointness():
    ts = _target_set([_pos(0.5)], 0.5)
    dk = deterministic_apply_key(ts)
    assert SHADOW_DETERMINISTIC_APPLY_KEY_DOMAIN == "shadow-deterministic-apply-key-v1"
    # adversarial: an intent whose apply-key components mirror the target set's
    intent = _intent([_pos(0.5)], 0.5, intent_id="rule.equal#0", target_version=1)
    assert dk != target_apply_key(intent)
    # no intent apply key can equal the deterministic key (distinct domains)


def test_run_targets_produces_origin_free_result_no_intent_minted():
    r = _runner(_alpha_frames())
    ts = _target_set([_pos(0.5)], 0.5)
    res = r.run_targets((ts,), run_config=_run_config(),
                        calendar=_calendar(), clock=r.clock)
    assert isinstance(res, ShadowRunResult)
    assert res.intent_content_digests == ()          # no intent minted
    assert res.applies[0].intent_id == "rule.equal#0"
    assert len(res.fills) == 1 and res.fills[0].reason == "target_buy"


def test_dual_lane_same_bar_equivalence():
    # identical positions/cash at the same decision point -> identical fills/costs.
    intent = _intent([_pos(0.5)], 0.5)
    ts = _target_set([_pos(0.5)], 0.5, session_date="2026-07-20")
    r_i = _runner(_alpha_frames())
    r_d = _runner(_alpha_frames())
    res_i = r_i.run((intent,), start="2026-07-20", end="2026-07-24")
    res_d = r_d.run_targets((ts,), run_config=_run_config(),
                            calendar=_calendar(), clock=r_d.clock)

    def econ(res):
        return (
            sorted((f.symbol.dotted, f.side, f.qty, round(f.price, 6),
                    round(f.gross, 6), round(f.cost, 6), f.reason, f.trade_date)
                   for f in res.fills),
            sorted((rj.symbol.dotted, rj.trade_date, rj.reason) for rj in res.rejects),
        )

    assert econ(res_i) == econ(res_d)
    # only the apply-key family / provenance differ
    assert res_i.applies[0].target_apply_key != res_d.applies[0].target_apply_key


def test_run_targets_dedup_disjoint_from_intent_lane_keys():
    r = _runner(_alpha_frames())
    ts = _target_set([_pos(0.5)], 0.5)
    res = r.run_targets((ts,), run_config=_run_config(),
                        calendar=_calendar(), clock=r.clock)
    det_key = deterministic_apply_key(ts)
    # no record's applied key equals the deterministic dedup key's family, and the
    # deterministic dedup key never collides with any intent-family apply key.
    intent = _intent([_pos(0.5)], 0.5)
    assert det_key != target_apply_key(intent)
    assert res is not None


def test_run_targets_config_digest_mismatch_refused_on_cost_model():
    r = _runner(_alpha_frames(), cost_model=CostModel())
    ts = _target_set([_pos(0.5)], 0.5)
    bad = _run_config(cost_model=CostModel(slippage_bps=99.0))  # different cost model
    with pytest.raises(ShadowContractError):
        r.run_targets((ts,), run_config=bad, calendar=_calendar(), clock=r.clock)


def test_run_targets_config_digest_mismatch_refused_on_init_cash():
    r = _runner(_alpha_frames(), init_cash=1_000_000.0)
    ts = _target_set([_pos(0.5)], 0.5)
    bad = _run_config(init_cash=500_000.0)
    with pytest.raises(ShadowContractError):
        r.run_targets((ts,), run_config=bad, calendar=_calendar(), clock=r.clock)


def test_run_targets_refuses_calendar_id_mismatch():
    r = _runner(_alpha_frames())
    ts = _target_set([_pos(0.5)], 0.5)
    with pytest.raises(ShadowContractError):
        r.run_targets((ts,), run_config=_run_config(),
                      calendar=_calendar(calendar_id="ashare.other"), clock=r.clock)


def test_run_targets_refuses_clock_identity_mismatch():
    r = _runner(_alpha_frames(), clock=SystemClock())
    ts = _target_set([_pos(0.5)], 0.5)

    class _OtherClock:
        def now(self):
            return datetime.now(UTC)

    with pytest.raises(ShadowContractError):
        r.run_targets((ts,), run_config=_run_config(),
                      calendar=_calendar(), clock=_OtherClock())
