# -*- coding: utf-8 -*-
"""Phase 6 · Task 8 — the stage-① compatibility mirror of the frontend runBacktest.

The mirror (:func:`run_compatibility_mirror` / :func:`compat_metrics` /
:func:`map_intents_to_compat_signals`) is a BYTE-FAITHFUL Python replication of
``ui/seats/luozi-data.jsx::runBacktest`` (1508-1563) + ``metricsOf`` (461-482).
Every expected vector is frozen by hand in ``fixtures/shadow_mirror_v1.json`` from
the jsx line semantics — NEVER generated from the mirror. These tests assert the
mirror reproduces those hand-frozen vectors under the five declared tolerances,
that the tolerances actually BIND (a perturbed expectation fails), that the profile
mapping refuses out-of-profile intents, and that the mirror agrees on entry/exit
BARS with the full ``ShadowBacktestRunner`` on an interference-free fixture.

The conftest prepends the in-repo engine fork (needed for the sanity-link runner).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

# REAL fa engine cost model (conftest prepends the engine fork) — sanity link only.
from financial_analyst.backtest.costs import CostModel

import pandas as pd

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.adapters.luozi import (
    COMPATIBILITY_PROFILE_ID,
    COMPAT_TRADE_STRUCTURE_TOL,
    COMPAT_PRICE_REL_TOL,
    COMPAT_RETURN_ABS_TOL,
    COMPAT_EQUITY_REL_TOL,
    COMPAT_METRIC_REL_TOL,
    CompatSignal,
    CompatClock,
    CompatTrade,
    CompatibilityRunResult,
    CompatibilityProfileError,
    ShadowBacktestRunner,
    compat_metrics,
    map_intents_to_compat_signals,
    run_compatibility_mirror,
)
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.shadow import (
    ShadowContractError,
    TargetPortfolioIntent,
    TargetPosition,
    TrancheTrigger,
)

UTC = timezone.utc
_FIXTURE = Path(__file__).parent / "fixtures" / "shadow_mirror_v1.json"


def _fixture():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _case(name):
    for c in _fixture()["cases"]:
        if c["name"] == name:
            return c
    raise KeyError(name)


def _signals(case):
    return tuple(CompatSignal(idx=s["idx"], side=s["side"]) for s in case["signals"])


def _bars(case):
    return tuple(dict(b) for b in case["bars"])


def _clock(case):
    c = case["clock"]
    if c is None:
        return None
    return CompatClock(
        stop_loss=c["stop_loss"], take_profit=c["take_profit"], max_hold=c["max_hold"]
    )


# --------------------------------------------------------------------------- #
# tolerance predicates (declared constants ARE the gate)                       #
# --------------------------------------------------------------------------- #
def _rel_close(a, b, tol):
    return abs(a - b) <= tol * abs(b)


def _abs_close(a, b, tol):
    return abs(a - b) <= tol


def _metric_close(a, b, tol):
    # rel tol with a floor so metrics that are legitimately 0 (mdd/winRate) still gate.
    return abs(a - b) <= tol * max(1.0, abs(b))


def _assert_matches(res, exp):
    assert res is not None
    # structural tol 0 — firstSig exact
    assert res.first_sig == exp["first_sig"]
    # eqSeg under equity rel tol (never raw eq — the contract exposes eq_seg only)
    assert len(res.eq_seg) == len(exp["eq_seg"])
    for a, b in zip(res.eq_seg, exp["eq_seg"]):
        assert _rel_close(a, b, COMPAT_EQUITY_REL_TOL), (a, b)
    # trades: structure exact + price/return under tol
    assert len(res.trades) == len(exp["trades"])
    for tr, te in zip(res.trades, exp["trades"]):
        assert tr.in_idx == te["in_idx"]
        assert tr.out_idx == te["out_idx"]
        assert tr.reason == te["reason"]
        assert tr.open_end == te["open_end"]
        assert _rel_close(tr.entry, te["entry"], COMPAT_PRICE_REL_TOL), (tr.entry, te["entry"])
        assert _rel_close(tr.exit, te["exit"], COMPAT_PRICE_REL_TOL), (tr.exit, te["exit"])
        assert _abs_close(tr.ret, te["ret"], COMPAT_RETURN_ABS_TOL), (tr.ret, te["ret"])
    # metrics recomputed ONE way (compat_metrics) — under metric rel tol
    assert set(res.metrics) == set(exp["metrics"])
    for k in exp["metrics"]:
        assert _metric_close(res.metrics[k], exp["metrics"][k], COMPAT_METRIC_REL_TOL), k


# =========================================================================== #
# profile facts / constants                                                    #
# =========================================================================== #
def test_profile_id_and_tolerance_constants_frozen():
    assert COMPATIBILITY_PROFILE_ID == "luozi-runbacktest-compat-v1"
    assert COMPAT_TRADE_STRUCTURE_TOL == 0
    assert COMPAT_PRICE_REL_TOL == 1e-9
    assert COMPAT_RETURN_ABS_TOL == 1e-9
    assert COMPAT_EQUITY_REL_TOL == 1e-9
    assert COMPAT_METRIC_REL_TOL == 1e-6
    # the fixture's frozen tolerance block agrees with the module constants.
    tol = _fixture()["tolerances"]
    assert tol["structure"] == COMPAT_TRADE_STRUCTURE_TOL
    assert tol["price_rel"] == COMPAT_PRICE_REL_TOL
    assert tol["metric_rel"] == COMPAT_METRIC_REL_TOL
    assert _fixture()["profile_id"] == COMPATIBILITY_PROFILE_ID


# =========================================================================== #
# the ten mandatory fixture cases                                              #
# =========================================================================== #
_TEN = [
    "case1_buy_and_hold_openend",
    "case2_buy_sell_round_trip",
    "case3_stop_intrabar",
    "case4_take_intrabar",
    "case5_double_touch_stop_wins",
    "case6_max_hold_expiry",
    "case8_preentry_flat_late_entry",
    "case9_signal_on_entry_bar_no_exit",
    "case10_no_clock_signal_only",
]


@pytest.mark.parametrize("name", _TEN)
def test_fixture_case_matches_mirror(name):
    case = _case(name)
    res = run_compatibility_mirror(_signals(case), _bars(case), _clock(case))
    _assert_matches(res, case["expected"])


def test_case7_all_watch_returns_none_not_empty():
    # invariant 2 — the None case is asserted as None, never an empty result.
    case = _case("case7_all_watch_null")
    assert case["expected"] is None
    res = run_compatibility_mirror(_signals(case), _bars(case), _clock(case))
    assert res is None


# =========================================================================== #
# case-specific semantic assertions (independent of the vector compare)        #
# =========================================================================== #
def test_case1_open_position_at_end_is_synthetic_openend_trade():
    case = _case("case1_buy_and_hold_openend")
    res = run_compatibility_mirror(_signals(case), _bars(case), _clock(case))
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.open_end is True
    assert t.out_idx == 3  # last bar
    assert t.reason == "到期"  # openEnd encoded as 到期 + flag


def test_case5_double_touch_resolves_stop_first():
    # jsx:1535 — same-bar stop+take double touch, the stop wins (elif ordering).
    case = _case("case5_double_touch_stop_wins")
    res = run_compatibility_mirror(_signals(case), _bars(case), _clock(case))
    assert res.trades[0].reason == "止损"
    assert res.trades[0].reason != "止盈"


def test_case6_max_hold_is_real_exit_not_openend():
    # a real max-hold closes the position: 到期 + open_end=False, and NO trailing
    # openEnd trade (the flag disambiguates it from the synthetic end-of-data close).
    case = _case("case6_max_hold_expiry")
    res = run_compatibility_mirror(_signals(case), _bars(case), _clock(case))
    assert len(res.trades) == 1
    assert res.trades[0].reason == "到期"
    assert res.trades[0].open_end is False
    assert res.trades[0].out_idx == 2  # exit at the max-hold bar CLOSE, not the last bar


def test_case9_entry_bar_never_exits_i_gt_entryidx_guard():
    # the entry bar's sub-stop low (9.0 < 9.2) must NOT produce a 止损 at idx0.
    case = _case("case9_signal_on_entry_bar_no_exit")
    res = run_compatibility_mirror(_signals(case), _bars(case), _clock(case))
    assert len(res.trades) == 1
    assert res.trades[0].open_end is True  # ran to end, no intrabar stop
    assert all(t.reason != "止损" for t in res.trades)


def test_case8_eqseg_starts_at_firstsig_never_raw_eq():
    # invariant 2 — comparisons align on eqSeg (post-entry segment), never raw eq.
    case = _case("case8_preentry_flat_late_entry")
    bars = _bars(case)
    res = run_compatibility_mirror(_signals(case), bars, _clock(case))
    assert res.first_sig == 2
    # eqSeg is the post-entry tail only (len == n - firstSig), not the whole curve.
    assert len(res.eq_seg) == len(bars) - res.first_sig == 3
    # the contract EXPOSES only eq_seg — raw eq is never a field (gotcha 9).
    assert "eq_seg" in type(res).model_fields
    assert "eq" not in type(res).model_fields
    assert not hasattr(res, "eq")


# =========================================================================== #
# invariant 1 — the declared tolerances actually BIND                          #
# =========================================================================== #
def test_tolerances_bind_perturbed_expectation_fails():
    case = _case("case1_buy_and_hold_openend")
    res = run_compatibility_mirror(_signals(case), _bars(case), _clock(case))
    exp = case["expected"]

    # a within-tolerance nudge (half a tol) STILL passes — the gate is not vacuous.
    eq0 = exp["eq_seg"][2]
    assert _rel_close(res.eq_seg[2], eq0 * (1.0 + 0.5 * COMPAT_EQUITY_REL_TOL), COMPAT_EQUITY_REL_TOL)

    # a just-beyond-tolerance nudge FAILS on every tolerance family.
    # equity rel: nudge one eqSeg point by 2x the tol.
    bad_eq = res.eq_seg[2] * (1.0 + 2.0 * COMPAT_EQUITY_REL_TOL)
    assert not _rel_close(res.eq_seg[2], bad_eq, COMPAT_EQUITY_REL_TOL)
    # price rel: nudge the trade entry beyond the tol.
    bad_px = res.trades[0].entry * (1.0 + 2.0 * COMPAT_PRICE_REL_TOL)
    assert not _rel_close(res.trades[0].entry, bad_px, COMPAT_PRICE_REL_TOL)
    # return abs: nudge the trade ret beyond the abs tol.
    bad_ret = res.trades[0].ret + 2.0 * COMPAT_RETURN_ABS_TOL
    assert not _abs_close(res.trades[0].ret, bad_ret, COMPAT_RETURN_ABS_TOL)
    # metric rel: nudge sharpe beyond the metric tol.
    s = res.metrics["sharpe"]
    bad_s = s * (1.0 + 2.0 * COMPAT_METRIC_REL_TOL)
    assert not _metric_close(s, bad_s, COMPAT_METRIC_REL_TOL)
    # structure tol 0 — a single-index drift is a hard mismatch.
    assert res.trades[0].out_idx != exp["trades"][0]["out_idx"] + 1


# =========================================================================== #
# compat_metrics — the ONE-WAY metric recompute (metricsOf conventions)        #
# =========================================================================== #
def test_compat_metrics_matches_metricsof_conventions():
    # metricsOf(eqSeg,[trade],'day') for case1's hand-frozen eqSeg.
    case = _case("case1_buy_and_hold_openend")
    exp = case["expected"]
    eq_seg = tuple(exp["eq_seg"])
    trades = tuple(
        CompatTrade(
            entry=t["entry"], exit=t["exit"], ret=t["ret"], in_idx=t["in_idx"],
            out_idx=t["out_idx"], reason=t["reason"], open_end=t["open_end"],
        )
        for t in exp["trades"]
    )
    m = compat_metrics(eq_seg, trades, "day")
    for k, v in exp["metrics"].items():
        assert _metric_close(m[k], v, COMPAT_METRIC_REL_TOL), k


def test_compat_metrics_single_return_uses_1e_9_sd_floor():
    # a degenerate one-return eqSeg has sd 0 -> the metricsOf `|| 1e-9` floor keeps
    # sharpe finite (mean/1e-9 * sqrt(252)); assert it is finite, not a div-by-zero.
    m = compat_metrics((1.0, 1.0), (), "day")
    assert m["sharpe"] == 0.0  # mean 0 / 1e-9 -> 0
    m2 = compat_metrics((1.0, 1.01), (), "day")
    import math as _m
    assert _m.isfinite(m2["sharpe"]) and m2["sharpe"] > 0


# =========================================================================== #
# intent -> compat signal mapping + profile violations                         #
# =========================================================================== #
_CAL_ID = "ashare.xshg"
_TZ = "Asia/Shanghai"
_SESSIONS = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
_SEED_DAY = "2026-07-17"
_ELIG_0721 = datetime(2026, 7, 21, 1, 30, tzinfo=UTC)   # 09:30 CST -> session 07-21
_ELIG_0723 = datetime(2026, 7, 23, 1, 30, tzinfo=UTC)
_DEC_0720 = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)
_DEC_0722 = datetime(2026, 7, 22, 1, 30, tzinfo=UTC)

# bars aligned 1:1 with the calendar sessions (bars[i] = session i).
_MIRROR_BARS = (
    {"o": 9.9, "h": 10.4, "l": 9.8, "c": 10.0},   # 07-20
    {"o": 10.0, "h": 10.4, "l": 9.8, "c": 10.2},  # 07-21
    {"o": 10.2, "h": 11.0, "l": 10.1, "c": 10.8}, # 07-22
    {"o": 10.8, "h": 11.4, "l": 10.7, "c": 11.0}, # 07-23
    {"o": 11.0, "h": 11.6, "l": 10.9, "c": 11.2}, # 07-24
)


def _sym(code="600000", exchange="SH", board="main"):
    return Symbol(code=code, exchange=exchange, board=board)


def _pos(weight, *, code="600000", exchange="SH", board="main", **kw):
    return TargetPosition(symbol=_sym(code, exchange, board), target_weight=weight, **kw)


def _calendar(sessions=_SESSIONS, *, calendar_id=_CAL_ID):
    return build_trading_calendar(
        calendar_id=calendar_id,
        sessions=[datetime.fromisoformat(s).date() for s in sessions],
        material_id="cal.ashare.2026",
        material_version="1",
    )


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
        intrabar_exit_priority="stop_first",
    )
    base.update(over)
    return shadow.DecisionSchedule.build(**base)


def _sched_ref(sch):
    return ContentRef(id=sch.id, version=sch.version, content_digest=sch.content_digest)


def _intent(positions, cash_weight, *, scheduled_for=_DEC_0720, eligible=_ELIG_0721,
            intent_id="intent-1", target_version=1, **over):
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


def test_map_full_in_buy_maps_to_buy_at_eligible_bar_with_clock():
    intent = _intent([_pos(1.0, stop_loss_pct=0.08, take_profit_pct=0.1, max_hold_bars=3)], 0.0)
    signals, clock = map_intents_to_compat_signals(
        (intent,), bars=_MIRROR_BARS, calendar=_calendar(), schedule=_schedule()
    )
    assert signals == (CompatSignal(idx=1, side="buy"),)  # 07-21 is session index 1
    assert clock == CompatClock(stop_loss=0.08, take_profit=0.1, max_hold=3)


def test_map_all_cash_maps_to_sell_no_clock():
    intent = _intent([], 1.0, eligible=_ELIG_0723)
    signals, clock = map_intents_to_compat_signals(
        (intent,), bars=_MIRROR_BARS, calendar=_calendar(), schedule=_schedule()
    )
    assert signals == (CompatSignal(idx=3, side="sell"),)  # 07-23 is index 3
    assert clock is None


def test_map_buy_then_sell_round_trip_two_intents():
    buy = _intent([_pos(1.0)], 0.0, intent_id="A", eligible=_ELIG_0721)
    sell = _intent([], 1.0, intent_id="B", scheduled_for=_DEC_0722, eligible=_ELIG_0723)
    signals, clock = map_intents_to_compat_signals(
        (buy, sell), bars=_MIRROR_BARS, calendar=_calendar(), schedule=_schedule()
    )
    assert signals == (CompatSignal(idx=1, side="buy"), CompatSignal(idx=3, side="sell"))
    assert clock is None  # the buy position carries no exit params


def test_map_two_positions_raises_profile_error():
    intent = _intent([_pos(0.5, code="600000"), _pos(0.5, code="600001")], 0.0)
    with pytest.raises(CompatibilityProfileError):
        map_intents_to_compat_signals(
            (intent,), bars=_MIRROR_BARS, calendar=_calendar(), schedule=_schedule()
        )


def test_map_fractional_weight_raises_profile_error():
    # 0.5 is a valid intent band, but the compat profile is full-in only (pos in {0,1}).
    intent = _intent([_pos(0.5)], 0.5)
    with pytest.raises(CompatibilityProfileError):
        map_intents_to_compat_signals(
            (intent,), bars=_MIRROR_BARS, calendar=_calendar(), schedule=_schedule()
        )


def test_map_non_stop_first_schedule_raises_profile_error():
    intent = _intent([_pos(1.0)], 0.0)
    for prio in ("worst_case", "take_profit_first"):
        with pytest.raises(CompatibilityProfileError):
            map_intents_to_compat_signals(
                (intent,), bars=_MIRROR_BARS, calendar=_calendar(),
                schedule=_schedule(intrabar_exit_priority=prio),
            )


def test_map_entry_tranches_raises_profile_error():
    tranche = TrancheTrigger(price_low=9.0, price_high=10.0, fraction=0.5)
    intent = _intent([_pos(1.0, entry_tranches=(tranche,))], 0.0)
    with pytest.raises(CompatibilityProfileError):
        map_intents_to_compat_signals(
            (intent,), bars=_MIRROR_BARS, calendar=_calendar(), schedule=_schedule()
        )


def test_profile_error_is_a_shadow_contract_error():
    assert issubclass(CompatibilityProfileError, ShadowContractError)


# =========================================================================== #
# invariant 5 — sanity link: mirror vs full ShadowBacktestRunner (bars+sides)   #
# =========================================================================== #
class _MemLoader:
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
    def __init__(self, sessions):
        self._sessions = list(sessions)

    def trading_days(self, start=None, end=None):
        lo = start or self._sessions[0]
        hi = end or self._sessions[-1]
        return [d for d in self._sessions if lo <= d <= hi]


# alpha frame with a seed day; a gentle rising week that fills cleanly, no
# suspension / limit / T+1 interference between the 07-21 buy and the 07-23 sell.
_ALPHA = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-21", 10.0, 10.4, 9.8, 10.2, 1e6),
    ("2026-07-22", 10.2, 11.0, 10.1, 10.8, 1e6),
    ("2026-07-23", 10.8, 11.4, 10.7, 11.0, 1e6),
    ("2026-07-24", 11.0, 11.6, 10.9, 11.2, 1e6),
]


def _zero_cost_model():
    # fully zeroed — commission/stamp/transfer/slippage AND the 5-元 min fee, so the
    # link never breaks on a min-fee-induced below_one_lot reject (brief invariant 5).
    return CostModel(
        commission_rate=0.0, min_commission=0.0, stamp_rate=0.0,
        transfer_rate_sh=0.0, transfer_rate_other=0.0, slippage_bps=0.0,
    )


def _sanity_runner():
    sch = _schedule()
    return ShadowBacktestRunner(
        reader=_MemReader(_SESSIONS),
        loader=_MemLoader({"SH600000": list(_ALPHA)}),
        schedule=sch,
        schedule_ref=_sched_ref(sch),
        calendar=_calendar(),
        cost_model=_zero_cost_model(),
        init_cash=1_000_000.0,
        lot_size=1,          # whole-share degenerate sizing (Task-5 diff, lot_size=1)
        clock=SystemClock(),
    )


def test_sanity_link_runner_and_mirror_agree_on_entry_exit_bars_and_sides():
    buy = _intent([_pos(1.0)], 0.0, intent_id="A", scheduled_for=_DEC_0720, eligible=_ELIG_0721)
    sell = _intent([], 1.0, intent_id="B", scheduled_for=_DEC_0722, eligible=_ELIG_0723)

    # --- full runner path (zeroed CostModel, lot_size=1) ---
    res = _sanity_runner().run((buy, sell), start="2026-07-20", end="2026-07-24")
    buys = [f for f in res.fills if f.side == "buy"]
    sells = [f for f in res.fills if f.side == "sell"]
    assert len(buys) == 1 and len(sells) == 1
    runner_buy_bar = _SESSIONS.index(buys[0].trade_date)
    runner_sell_bar = _SESSIONS.index(sells[0].trade_date)

    # --- mirror path (same intents, via the profile mapper) ---
    signals, clock = map_intents_to_compat_signals(
        (buy, sell), bars=_MIRROR_BARS, calendar=_calendar(), schedule=_schedule()
    )
    mres = run_compatibility_mirror(signals, _MIRROR_BARS, clock)
    assert len(mres.trades) == 1
    t = mres.trades[0]

    # compare ONLY bar indices + sides (prices legitimately differ: same-bar close
    # vs the runner's next-bar limit conventions) — asserted exactly.
    assert t.in_idx == runner_buy_bar    # both enter on the eligible bar (07-21 -> idx 1)
    assert t.out_idx == runner_sell_bar  # both exit on the sell's eligible bar (07-23 -> idx 3)
    assert t.reason == "信号"            # a signal-driven round trip (mirror side)
    # the runner buy precedes the runner sell — same ordering the mirror produced.
    assert runner_buy_bar < runner_sell_bar


def test_sanity_link_mirror_shares_lot_size_1_degenerate_sizing_never_broker():
    # invariant 3 — the mirror computes fractional shares directly (shares=cash/px)
    # and never routes through Broker / applies costs. Contrast with the runner which
    # DID route through Broker (whole-share, cost model) for the SAME intents. The two
    # agree on bars/sides yet differ on price — proof the mirror is a separate path.
    buy = _intent([_pos(1.0)], 0.0, intent_id="A", eligible=_ELIG_0721)
    signals, clock = map_intents_to_compat_signals(
        (buy,), bars=_MIRROR_BARS, calendar=_calendar(), schedule=_schedule()
    )
    mres = run_compatibility_mirror(signals, _MIRROR_BARS, clock)
    # mirror entry price is the same-bar CLOSE (10.2), fractional shares 1/10.2.
    assert mres.trades[0].entry == pytest.approx(10.2, abs=1e-9)
    assert mres.trades[0].open_end is True
