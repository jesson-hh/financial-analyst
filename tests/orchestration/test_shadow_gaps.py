# -*- coding: utf-8 -*-
"""Phase 6 · Task 7 — shadow-side take-profit / max-hold / intrabar-priority + the
corporate-action ledger (the two Task-6 seams in ``adapters.luozi``).

The five required invariant groups over deterministic in-memory OHLCV fixtures:

1. take-profit + max-hold demonstrably do NOT exist in the engine but DO exist in
   shadow runs (``ShadowFillRecord.reason`` == ``take_profit`` / ``max_hold_exit``);
2. gap exits still obey engine reality — a take-profit on a suspended bar rejects
   ``"suspended"`` and re-arms; a stop through a one-word limit-down bar rejects
   ``"one_word_limit_down"`` and re-arms;
3. a double-touch bar produces exactly ONE exit order whose kind follows
   ``schedule.intrabar_exit_priority`` (flipping the field flips both outcome and
   the schedule digest);
4. corporate actions — cash dividend credits exactly ``qty*cash_per_share``;
   bonus/split preserve ``qty*avg_cost`` and rescale locked buckets + stop prices;
   a not-held symbol is a zero-delta no-op; the same event replayed yields zero
   additional applications (digest idempotency); NAV is continuous across a
   10-送-3 ex-date (no phantom drawdown);
5. every gap exit order id uses the frozen key family with the ORIGINATING apply
   key (causation survives exits).

The conftest prepends the in-repo engine fork onto ``sys.path``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

# REAL fa engine primitives (the conftest prepends the in-repo engine fork).
from financial_analyst.backtest.costs import CostModel
from financial_analyst.backtest.portfolio import Position, VirtualPortfolio

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.adapters.luozi import (
    CorporateActionApplication,
    ShadowBacktestRunner,
    apply_corporate_actions,
)
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.shadow import (
    CorporateActionEvent,
    ShadowRunResult,
    TargetPosition,
    TargetPortfolioIntent,
    shadow_order_id,
)

UTC = timezone.utc
_CAL_ID = "ashare.xshg"
_TZ = "Asia/Shanghai"
_SESSIONS = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
_SEED_DAY = "2026-07-17"

# 09:30 CST == 01:30 UTC
_DECISION_0720 = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)
_ELIGIBLE_0721 = datetime(2026, 7, 21, 1, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# in-memory reader / loader satisfying prepare_bar's real surface             #
# --------------------------------------------------------------------------- #
class _MemLoader:
    def __init__(self, frames):
        import pandas as pd

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
        import pandas as pd

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


def _run(frames, positions, cash_weight=0.5, *, schedule=None, corporate_actions=()):
    r = _runner(frames, schedule=schedule, corporate_actions=corporate_actions)
    return r.run((_intent(positions, cash_weight),), start="2026-07-20", end="2026-07-24")


# --------------------------------------------------------------------------- #
# fixtures — deterministic OHLCV frames (entry buys on 07-21, fill 10.4)       #
# --------------------------------------------------------------------------- #
# Take-profit: entry 10.4, tp_pct 0.05 -> tp_px 10.92; touches on 07-22 (high 11.0).
_TP = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),   # prev_close 10.0 for 07-21
    ("2026-07-21", 10.0, 10.4, 9.9, 10.2, 1e6),   # entry fill 10.4; high < tp
    ("2026-07-22", 10.5, 11.0, 10.4, 10.8, 1e6),  # high 11.0 >= 10.92 -> take
    ("2026-07-23", 10.8, 11.4, 10.7, 11.0, 1e6),
    ("2026-07-24", 11.0, 11.6, 10.9, 11.2, 1e6),
]

# Max-hold: no stop/take; forced exit on 07-23 (bars_held 2).
_MH = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-21", 10.0, 10.4, 9.9, 10.2, 1e6),
    ("2026-07-22", 10.2, 10.6, 10.1, 10.4, 1e6),
    ("2026-07-23", 10.4, 11.0, 10.3, 10.8, 1e6),  # forced sell here
    ("2026-07-24", 10.8, 11.2, 10.7, 11.0, 1e6),
]

# Suspension on the tp day: 07-22 vol 0 (rejects "suspended"); re-arms 07-23.
_SUSP = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-21", 10.0, 10.4, 9.9, 10.2, 1e6),   # entry 10.4
    ("2026-07-22", 10.9, 11.0, 10.8, 10.9, 0.0),  # SUSPENDED (vol 0), high >= tp
    ("2026-07-23", 10.9, 11.0, 10.8, 10.9, 1e6),  # tradable, high >= tp -> fill
    ("2026-07-24", 10.9, 11.2, 10.7, 11.0, 1e6),
]

# Stop through a one-word limit-down bar (07-22) -> rejects; re-arms 07-23.
_STOP_1W = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-21", 10.0, 10.4, 9.9, 10.0, 1e6),   # entry 10.4; stop=9.5; low 9.9 > stop
    ("2026-07-22", 9.0, 9.0, 9.0, 9.0, 1e6),      # one-word limit-down (10.0*0.9)
    ("2026-07-23", 9.0, 9.3, 8.8, 9.0, 1e6),      # low 8.8 <= stop 9.5 -> stop fills
    ("2026-07-24", 9.0, 9.4, 8.9, 9.2, 1e6),
]

# Double-touch bar (07-22): low 9.4 <= stop 9.5 AND high 11.0 >= tp 10.92.
_DT = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-21", 10.0, 10.4, 9.9, 10.0, 1e6),   # entry 10.4; stop 9.5; no early trigger
    ("2026-07-22", 10.0, 11.0, 9.4, 10.5, 1e6),   # DOUBLE touch
    ("2026-07-23", 10.5, 11.4, 10.4, 11.0, 1e6),
    ("2026-07-24", 11.0, 11.6, 10.9, 11.2, 1e6),
]

# Bonus NAV continuity: close 13.0 (07-22) -> 10.0 (07-23) == 13.0/1.3 ex-bonus.
_BONUS_NAV = [
    (_SEED_DAY, 11.9, 12.0, 11.8, 12.0, 1e6),
    ("2026-07-20", 12.0, 12.5, 11.8, 12.0, 1e6),  # prev_close 12.0 for 07-21
    ("2026-07-21", 12.0, 12.6, 11.9, 12.5, 1e6),  # entry buy
    ("2026-07-22", 12.5, 13.2, 12.4, 13.0, 1e6),  # mkt close 13.0
    ("2026-07-23", 10.0, 10.3, 9.9, 10.0, 1e6),   # ex-bonus 10-送-3 (13.0/1.3)
    ("2026-07-24", 10.0, 10.3, 9.9, 10.0, 1e6),
]


def _frames(rows):
    return {"SH600000": list(rows)}


# =========================================================================== #
# invariant 1 — take-profit + max-hold exist in shadow, not in the engine      #
# =========================================================================== #
def test_engine_has_no_take_profit_or_max_hold_order_type():
    # Task-0 item 8: the engine Order carries neither a take-profit nor a max-hold
    # field, and its otype vocabulary is limit/market/stop only.
    from financial_analyst.backtest.broker import Order

    fields = set(Order.__dataclass_fields__)
    assert "take_profit" not in fields
    assert "max_hold_bars" not in fields
    assert "take_profit_pct" not in fields


def test_take_profit_exit_produces_fill_record_with_order_kind():
    res = _run(_frames(_TP), [_pos(0.5, take_profit_pct=0.05)])
    tp_fills = [f for f in res.fills if f.reason == "take_profit"]
    assert len(tp_fills) == 1
    assert tp_fills[0].trade_date == "2026-07-22"
    assert tp_fills[0].side == "sell"
    # the opening buy is still present (the two are distinct fills).
    assert any(f.reason == "target_buy" for f in res.fills)


def test_max_hold_exit_produces_fill_record_with_order_kind():
    res = _run(_frames(_MH), [_pos(0.5, max_hold_bars=2)])
    mh_fills = [f for f in res.fills if f.reason == "max_hold_exit"]
    assert len(mh_fills) == 1
    assert mh_fills[0].trade_date == "2026-07-23"  # entry 07-21 + 2 bars
    assert mh_fills[0].side == "sell"


def test_max_hold_does_not_fire_before_the_cap():
    # max_hold_bars 2: no exit on 07-22 (bars_held 1); the sell lands on 07-23.
    res = _run(_frames(_MH), [_pos(0.5, max_hold_bars=2)])
    exit_days = {f.trade_date for f in res.fills if f.reason == "max_hold_exit"}
    assert exit_days == {"2026-07-23"}


# =========================================================================== #
# invariant 2 — gap exits still obey engine reality (reject + re-arm)           #
# =========================================================================== #
def test_take_profit_on_suspended_bar_rejects_and_rearms():
    res = _run(_frames(_SUSP), [_pos(0.5, take_profit_pct=0.05)])
    # 07-22 suspended -> reject "suspended"; the position persists.
    assert any(rj.reason == "suspended" and rj.trade_date == "2026-07-22"
               for rj in res.rejects)
    # 07-23 tradable -> the take-profit re-arms and fills.
    tp_fills = [f for f in res.fills if f.reason == "take_profit"]
    assert len(tp_fills) == 1 and tp_fills[0].trade_date == "2026-07-23"


def test_stop_through_one_word_limit_down_rejects_and_rearms():
    res = _run(_frames(_STOP_1W), [_pos(0.5, stop_loss_pct=0.05)])
    # 07-22 one-word limit-down -> reject; 07-23 -> the stop re-arms and fills.
    assert any(rj.reason == "one_word_limit_down" and rj.trade_date == "2026-07-22"
               for rj in res.rejects)
    stop_fills = [f for f in res.fills if f.reason == "stop_loss"]
    assert len(stop_fills) == 1 and stop_fills[0].trade_date == "2026-07-23"


# =========================================================================== #
# invariant 3 — double-touch -> exactly one exit order, kind by priority        #
# =========================================================================== #
def _exit_kind_on(res, day="2026-07-22"):
    kinds = [f.reason for f in res.fills
             if f.trade_date == day and f.reason in
             ("stop_loss", "take_profit", "max_hold_exit")]
    return kinds


def test_double_touch_worst_case_executes_stop_only():
    res = _run(_frames(_DT), [_pos(0.5, stop_loss_pct=0.05, take_profit_pct=0.05)],
               schedule=_schedule(intrabar_exit_priority="worst_case"))
    assert _exit_kind_on(res) == ["stop_loss"]


def test_double_touch_stop_first_executes_stop_only():
    res = _run(_frames(_DT), [_pos(0.5, stop_loss_pct=0.05, take_profit_pct=0.05)],
               schedule=_schedule(intrabar_exit_priority="stop_first"))
    assert _exit_kind_on(res) == ["stop_loss"]


def test_double_touch_take_profit_first_executes_take_only():
    res = _run(_frames(_DT), [_pos(0.5, stop_loss_pct=0.05, take_profit_pct=0.05)],
               schedule=_schedule(intrabar_exit_priority="take_profit_first"))
    assert _exit_kind_on(res) == ["take_profit"]


def test_double_touch_submits_exactly_one_exit_order():
    res = _run(_frames(_DT), [_pos(0.5, stop_loss_pct=0.05, take_profit_pct=0.05)],
               schedule=_schedule(intrabar_exit_priority="worst_case"))
    exit_orders = [o for o in res.orders
                   if o.trigger_bar == "2026-07-22"
                   and o.order_kind in ("stop_loss", "take_profit", "max_hold_exit")]
    assert len(exit_orders) == 1
    assert exit_orders[0].order_kind == "stop_loss"


def test_flipping_priority_flips_schedule_digest_and_outcome():
    s_wc = _schedule(intrabar_exit_priority="worst_case")
    s_tp = _schedule(intrabar_exit_priority="take_profit_first")
    # the schedule digest is digest-bearing on intrabar_exit_priority.
    assert s_wc.content_digest != s_tp.content_digest
    r_wc = _exit_kind_on(_run(_frames(_DT),
                              [_pos(0.5, stop_loss_pct=0.05, take_profit_pct=0.05)],
                              schedule=s_wc))
    r_tp = _exit_kind_on(_run(_frames(_DT),
                              [_pos(0.5, stop_loss_pct=0.05, take_profit_pct=0.05)],
                              schedule=s_tp))
    assert r_wc == ["stop_loss"] and r_tp == ["take_profit"]


# =========================================================================== #
# invariant 4 — corporate-action ledger                                        #
# =========================================================================== #
def _pf(qty=1000, avg_cost=10.0, stop_loss=9.0, locked=None, cash=100_000.0,
        code="SH600000"):
    pf = VirtualPortfolio(init_cash=cash, cash=cash, cost_model=CostModel())
    pf.positions[code] = Position(
        code=code, qty=qty, avg_cost=avg_cost, stop_loss=stop_loss,
        mkt_value=qty * avg_cost, locked=dict(locked or {}),
    )
    return pf


def _ca(kind, *, cash_per_share=0.0, shares_ratio=0.0, ex_date="2026-07-23",
        symbol=None):
    return CorporateActionEvent(
        symbol=symbol or _sym(), kind=kind, ex_date=ex_date,
        cash_per_share=cash_per_share, shares_ratio=shares_ratio,
        available_at=datetime(2026, 7, 22, 1, 0, tzinfo=UTC),
    )


def test_cash_dividend_credits_exactly_qty_times_cash_per_share():
    pf = _pf(qty=1000, avg_cost=10.0, cash=100_000.0)
    ev = _ca("cash_dividend", cash_per_share=0.5)
    apps = apply_corporate_actions(pf, (ev,), on_date="2026-07-23", applied_digests=set())
    assert pf.cash == pytest.approx(100_000.0 + 1000 * 0.5)
    assert len(apps) == 1
    a = apps[0]
    assert isinstance(a, CorporateActionApplication)
    assert a.cash_credited == pytest.approx(500.0)
    assert a.qty_before == 1000 and a.qty_after == 1000
    assert a.avg_cost_before == pytest.approx(10.0)
    assert a.avg_cost_after == pytest.approx(10.0)
    # qty / avg_cost untouched by a cash dividend.
    assert pf.positions["SH600000"].qty == 1000
    assert pf.positions["SH600000"].avg_cost == pytest.approx(10.0)


def test_stock_bonus_preserves_cost_basis_and_rescales_locked_and_stop():
    pf = _pf(qty=1000, avg_cost=13.0, stop_loss=9.0,
             locked={"2026-07-23": 1000}, cash=100_000.0)
    ev = _ca("stock_bonus", shares_ratio=0.3)  # 10 送 3
    apps = apply_corporate_actions(pf, (ev,), on_date="2026-07-23", applied_digests=set())
    pos = pf.positions["SH600000"]
    assert pos.qty == 1300  # floor(1000 * 1.3)
    assert pos.avg_cost == pytest.approx(13.0 * 1000 / 1300)
    # cost basis preserved within one floor step.
    assert pos.qty * pos.avg_cost == pytest.approx(1000 * 13.0)
    # stop price rescaled by qty / qty_after.
    assert pos.stop_loss == pytest.approx(9.0 * 1000 / 1300)
    # T+1 locked bucket rescaled with the same floor rule (never unlocks early).
    assert pos.locked["2026-07-23"] == 1300
    # no cash for a bonus.
    assert pf.cash == pytest.approx(100_000.0)
    a = apps[0]
    assert a.qty_before == 1000 and a.qty_after == 1300
    assert a.cash_credited == pytest.approx(0.0)


def test_split_preserves_cost_basis():
    pf = _pf(qty=1000, avg_cost=10.0, stop_loss=9.0, cash=100_000.0)
    ev = _ca("split", shares_ratio=2.0)
    apply_corporate_actions(pf, (ev,), on_date="2026-07-23", applied_digests=set())
    pos = pf.positions["SH600000"]
    assert pos.qty == 2000
    assert pos.avg_cost == pytest.approx(5.0)
    assert pos.qty * pos.avg_cost == pytest.approx(1000 * 10.0)
    assert pos.stop_loss == pytest.approx(9.0 * 1000 / 2000)


def test_not_held_symbol_is_zero_delta_noop():
    pf = _pf(qty=1000, cash=100_000.0)  # holds 600000
    ev = _ca("cash_dividend", cash_per_share=0.5,
             symbol=Symbol(code="600001", exchange="SH", board="main"))
    apps = apply_corporate_actions(pf, (ev,), on_date="2026-07-23", applied_digests=set())
    assert len(apps) == 1
    a = apps[0]
    assert a.qty_before == 0 and a.qty_after == 0
    assert a.cash_credited == pytest.approx(0.0)
    assert pf.cash == pytest.approx(100_000.0)  # never credited


def test_replay_same_event_yields_zero_additional_applications():
    pf = _pf(qty=1000, cash=100_000.0)
    ev = _ca("cash_dividend", cash_per_share=0.5)
    applied = set()
    apps1 = apply_corporate_actions(pf, (ev,), on_date="2026-07-23", applied_digests=applied)
    apps2 = apply_corporate_actions(pf, (ev,), on_date="2026-07-23", applied_digests=applied)
    assert len(apps1) == 1
    assert apps2 == ()  # digest idempotency — zero additional applications
    assert pf.cash == pytest.approx(100_000.0 + 500.0)  # credited exactly once


def test_nav_continuous_across_bonus_ex_date_no_phantom_drawdown():
    ev = _ca("stock_bonus", shares_ratio=0.3, ex_date="2026-07-23")
    res = _runner(_frames(_BONUS_NAV), corporate_actions=(ev,)).run(
        (_intent([_pos(0.5)], 0.5),), start="2026-07-20", end="2026-07-24")
    nav = dict(res.nav_history)
    # NAV is continuous across the ex-date: mkt_value(qty*price) is preserved by the
    # ex-bonus (qty up 1.3x, price down to 1/1.3) and no cash moves.
    assert nav["2026-07-23"] == pytest.approx(nav["2026-07-22"], rel=1e-9)
    # phantom-order guard (Task-7 review, finding 2): _pos(0.5) carries NO exit
    # parameter (no stop / take-profit / max-hold), so exit_state is never
    # populated for this position and step 4 (_manage_exits) never reaches it —
    # zero orders of ANY exit kind, corporate action or not.
    exit_kinds = {"stop_loss", "take_profit", "max_hold_exit"}
    assert [o for o in res.orders if o.order_kind in exit_kinds] == []


# =========================================================================== #
# invariant 6 — a share event rescales the LIVE take-profit level              #
#               (Task-7 review, finding 1)                                    #
# =========================================================================== #
# entry fill 10.4 (identical mechanics to _TP: pc 10.0, half-pct ceiling 10.5,
# clipped to the entry bar's high 10.4). tp_pct 0.05 -> un-adjusted tp_px 10.92
# (the level a runner that FAILED to rescale entry_price would still use).
#
# A 10-送-10 stock_bonus (shares_ratio=1.0 -> multiplier 1+ratio = 2.0 exactly)
# on ex_date 07-23 doubles qty for ANY integer qty_before with zero floor
# rounding ambiguity, so qty_before/qty_after is exactly 0.5 regardless of the
# actual sized quantity: entry_price rescales 10.4 -> 5.2, and the ADJUSTED
# tp_px is 5.2 * 1.05 = 5.46 (well under half the un-adjusted 10.92).
#
# 07-23 is priced in the post-bonus regime (~half the pre-bonus level, the
# same halving the fixture's own qty undergoes) so its high (5.50) clears the
# ADJUSTED level while sitting nowhere near the un-adjusted one — the bar
# shape that discriminates "rescale applied" from "rescale skipped": with the
# bug, bar.high (5.50) never reaches 10.92 and no take-profit fires at all.
_ENTRY_PRICE = 10.4
_TP_PCT = 0.05
_UNADJUSTED_TP_PX = _ENTRY_PRICE * (1.0 + _TP_PCT)                  # 10.92
_BONUS_MULT = 2.0                                                    # 1 + shares_ratio(1.0)
_ADJUSTED_TP_PX = (_ENTRY_PRICE / _BONUS_MULT) * (1.0 + _TP_PCT)     # 5.46

_TP_BONUS = [
    (_SEED_DAY, 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-20", 10.0, 10.4, 9.8, 10.0, 1e6),   # prev_close 10.0 for 07-21
    ("2026-07-21", 10.0, 10.4, 9.9, 10.2, 1e6),   # entry fill 10.4; high < un-adj tp
    ("2026-07-22", 10.2, 10.5, 10.1, 10.3, 1e6),  # pre-bonus; high 10.5 still < 10.92
    ("2026-07-23", 5.15, 5.50, 5.05, 5.20, 1e6),  # ex-date 10-送-10; high 5.50 >= adj tp
    ("2026-07-24", 5.20, 5.60, 5.10, 5.30, 1e6),
]


def test_take_profit_fires_at_ex_adjusted_level_after_share_bonus():
    # sanity on the fixture's own arithmetic before running anything: the touch
    # bar's high must clear the adjusted level and stay far under the
    # un-adjusted one (the property this whole test exists to pin).
    assert _ADJUSTED_TP_PX < 5.50 < _UNADJUSTED_TP_PX

    ev = _ca("stock_bonus", shares_ratio=1.0, ex_date="2026-07-23")
    res = _runner(_frames(_TP_BONUS), corporate_actions=(ev,)).run(
        (_intent([_pos(0.5, take_profit_pct=_TP_PCT)], 0.5),),
        start="2026-07-20", end="2026-07-24",
    )
    # no early (pre-bonus, un-adjusted-level) fire on 07-22.
    assert _exit_kind_on(res, "2026-07-22") == []
    tp_fills = [f for f in res.fills if f.reason == "take_profit"]
    assert len(tp_fills) == 1
    assert tp_fills[0].trade_date == "2026-07-23"
    assert tp_fills[0].side == "sell"
    # the fill lands at the ADJUSTED level (small sell-side slippage), never
    # anywhere close to the un-adjusted 10.92 the un-rescaled code would have
    # required this bar to reach (it never does — high tops out at 5.50).
    assert tp_fills[0].price == pytest.approx(_ADJUSTED_TP_PX, rel=2e-3)
    assert tp_fills[0].price < _UNADJUSTED_TP_PX / 2.0
    tp_orders = [o for o in res.orders if o.order_kind == "take_profit"]
    assert len(tp_orders) == 1
    assert tp_orders[0].limit_price == pytest.approx(_ADJUSTED_TP_PX, rel=1e-6)


# =========================================================================== #
# invariant 5 — exit-order causation via the originating apply key             #
# =========================================================================== #
def test_exit_order_id_uses_originating_apply_key():
    res = _run(_frames(_TP), [_pos(0.5, take_profit_pct=0.05)])
    apply_key = res.applies[0].target_apply_key
    exit_orders = [o for o in res.orders if o.order_kind == "take_profit"]
    assert len(exit_orders) == 1
    eo = exit_orders[0]
    # the exit order chains to the ORIGINATING applied target (not a new apply key).
    assert eo.target_apply_key == apply_key
    assert eo.order_id == shadow_order_id(
        apply_key=apply_key, symbol=_sym(), order_kind="take_profit",
        trigger_bar="2026-07-22", ordinal=0,
    )
    # ... and the resulting run result still self-seals (causation is closed).
    assert isinstance(res, ShadowRunResult)
    assert res.content_digest == res.semantic_digest()


def test_stop_exit_order_records_stop_otype():
    # a stop exit is submitted as the engine-native otype="stop" order.
    res = _run(_frames(_STOP_1W), [_pos(0.5, stop_loss_pct=0.05)])
    stop_orders = [o for o in res.orders if o.order_kind == "stop_loss"]
    assert stop_orders and all(o.otype == "stop" for o in stop_orders)


def test_two_runs_with_exits_are_digest_identical():
    a = _run(_frames(_DT), [_pos(0.5, stop_loss_pct=0.05, take_profit_pct=0.05)],
             schedule=_schedule(intrabar_exit_priority="worst_case"))
    b = _run(_frames(_DT), [_pos(0.5, stop_loss_pct=0.05, take_profit_pct=0.05)],
             schedule=_schedule(intrabar_exit_priority="worst_case"))
    assert a.content_digest == b.content_digest
