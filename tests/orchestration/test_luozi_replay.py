# -*- coding: utf-8 -*-
"""Phase 9 · Task 4 — DecisionSchedule interval-replay driver.

Drives the honest 双曲线回放 (dual-curve replay) walk over a decision schedule with
REAL Phase-6 schedule-time computations, REAL Phase-2 ``BudgetLedger`` semantics and
REAL typed refusals; only the per-point plan admission/execution is a recorded fake
(``ReplayPlanCoordinator``) returning real-contract objects. Covers the 17-row matrix
(8 resolver rows + 6 driver rows + 2 budget rows + 1 watcher row) plus a handful of
red-line extras (degraded-point honesty, envelope realness, deterministic lane).

Run: ``python -m pytest tests/orchestration/test_luozi_replay.py -v``
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from guanlan_v2.orchestration.adapters import luozi as lz
from guanlan_v2.orchestration.adapters.contracts import (
    ReplayDecisionPoint,
    ShadowExecutionConfig,
    ShadowReplayRunState,
)
from guanlan_v2.orchestration.adapters.luozi import (
    DeterministicBook,
    RebalanceDateNotSessionError,
    ReplayApprovalRefused,
    ReplayIntentLedger,
    ReplayPointSnapshot,
    ReplayRuntimeBindings,
    RetroactiveIntentRefused,
    SnapshotBindingRefused,
    reconcile_daily_llm_budget,
    resolve_decision_points,
    run_interval_replay,
)
from guanlan_v2.orchestration.budget import (
    BudgetEvent,
    BudgetLedger,
    BudgetTransitionCommand,
    fold_budget_events,
)
from guanlan_v2.orchestration.context import ClockSpec, RunBudget
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.enums import ApprovalPolicy, Confidence, ExperimentStatus
from guanlan_v2.orchestration.presets import pilot_data_context
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import clock_now
from guanlan_v2.orchestration.shadow import (
    ASHARE_SESSION_CLOSE,
    ASHARE_SESSION_OPEN,
    DecisionSchedule,
    DecisionScheduleRegistry,
    PortfolioTargetProposal,
    TargetPosition,
    UnsupportedBarFrequencyError,
    compute_eligible_execution_at,
    compute_scheduled_for,
)
from guanlan_v2.orchestration.spec import OrchestrationRequest
from guanlan_v2.seats import watcher

UTC = timezone.utc
TZ = "Asia/Shanghai"
CAL_ID = "cn_a_share"


# --------------------------------------------------------------------------- #
# fixtures — schedules + calendars                                            #
# --------------------------------------------------------------------------- #
def _schedule(
    *,
    kind="daily",
    weekdays=(),
    rebalance_dates=(),
    policy="next_open",
    price="open",
    bar_frequency="1d",
    sid="sched-replay",
    version="1",
) -> DecisionSchedule:
    return DecisionSchedule.build(
        id=sid,
        version=version,
        calendar_id=CAL_ID,
        timezone=TZ,
        kind=kind,
        decision_local_time="14:00",
        cutoff_local_time="09:00",
        bar_frequency=bar_frequency,
        execution_policy=policy,
        execution_price_field=price,
        matching_engine_version="shadow-match-v1",
        weekdays=tuple(weekdays),
        rebalance_dates=tuple(rebalance_dates),
        intrabar_exit_priority="worst_case",
    )


def _calendar(sessions, *, mid="mat", mver="1"):
    return build_trading_calendar(
        calendar_id=CAL_ID,
        sessions=[date.fromisoformat(s) for s in sessions],
        material_id=mid,
        material_version=mver,
    )


# July 2026 weekdays: 6=Mon 7=Tue 8=Wed 9=Thu 10=Fri | 13=Mon 14=Tue 15=Wed 16=Thu 17=Fri
_WEEK1 = ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"]
_WEEK2 = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"]


def _utc(iso_day: str, hh: int, mm: int) -> datetime:
    """The UTC instant of local ``hh:mm`` Asia/Shanghai on ``iso_day``."""
    y, m, d = (int(x) for x in iso_day.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(TZ)).astimezone(UTC)


# =========================================================================== #
# Row 1 — daily expansion is deterministic + ordinals dense from 1              #
# =========================================================================== #
def test_daily_expansion_deterministic():
    cal = _calendar(_WEEK1 + ["2026-07-13"])  # trailing session for the last eligible
    sch = _schedule(kind="daily")
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-10", 23, 59)
    a = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    b = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    assert a == b  # byte-identical tuple across calls
    assert tuple(p.point_ordinal for p in a) == (1, 2, 3, 4, 5)  # dense from 1
    assert len(a) == 5
    # each of the five week-1 sessions is a decision point; the three instants come
    # from the real Phase-6 computations.
    p0 = a[0]
    assert p0.scheduled_for == compute_scheduled_for(sch, session_date="2026-07-06", calendar=cal)
    assert p0.decision_as_of == p0.scheduled_for
    assert p0.cutoff_at <= p0.decision_as_of < p0.eligible_execution_at
    assert p0.execution_price_field == "open" and p0.bar_frequency == "1d"


# =========================================================================== #
# Row 2 — weekly expansion: first session of each week (weekday filter)          #
# =========================================================================== #
def test_weekly_expansion():
    cal = _calendar(_WEEK1 + _WEEK2)
    sch = _schedule(kind="weekly", weekdays=(1,))  # Mondays only
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-13", 23, 59)
    pts = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    assert len(pts) == 2
    assert [p.point_ordinal for p in pts] == [1, 2]
    # the two firing days are the two Mondays (isoweekday 1).
    local = [p.scheduled_for.astimezone(ZoneInfo(TZ)).date().isoformat() for p in pts]
    assert local == ["2026-07-06", "2026-07-13"]


# =========================================================================== #
# Row 3 — rebalance_dates: a listed non-session is refused loudly                #
# =========================================================================== #
def test_rebalance_dates_exact():
    # calendar omits 2026-07-08 → it is a NON-session listed in rebalance_dates.
    cal = _calendar(["2026-07-06", "2026-07-07", "2026-07-09", "2026-07-10", "2026-07-13"])
    sch = _schedule(
        kind="rebalance_dates",
        rebalance_dates=("2026-07-06", "2026-07-08", "2026-07-10"),
    )
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-13", 23, 59)
    with pytest.raises(RebalanceDateNotSessionError):
        resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)


def test_rebalance_dates_all_sessions_resolve():
    cal = _calendar(["2026-07-06", "2026-07-08", "2026-07-10", "2026-07-13"])
    sch = _schedule(
        kind="rebalance_dates",
        rebalance_dates=("2026-07-06", "2026-07-08", "2026-07-10"),
    )
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-10", 23, 59)
    pts = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    assert [p.point_ordinal for p in pts] == [1, 2, 3]


# =========================================================================== #
# Row 4 — manual yields no implicit points                                      #
# =========================================================================== #
def test_manual_yields_empty():
    cal = _calendar(_WEEK1)
    sch = _schedule(kind="manual")
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-17", 23, 59)
    assert resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end) == ()


# =========================================================================== #
# Row 5 — interval bounds respected                                             #
# =========================================================================== #
def test_interval_bounds_respected():
    cal = _calendar(_WEEK1 + _WEEK2)
    sch = _schedule(kind="daily")
    # window covers only 07-08 .. 07-10 (the middle of week 1).
    start, end = _utc("2026-07-08", 0, 0), _utc("2026-07-10", 23, 59)
    pts = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    local = [p.scheduled_for.astimezone(ZoneInfo(TZ)).date().isoformat() for p in pts]
    assert local == ["2026-07-08", "2026-07-09", "2026-07-10"]
    assert all(start <= p.scheduled_for <= end for p in pts)


# =========================================================================== #
# Row 6 / 7 — eligible time next_open / next_bar_close (1d)                      #
# =========================================================================== #
def test_eligible_time_next_open():
    cal = _calendar(_WEEK1)
    sch = _schedule(kind="daily", policy="next_open", price="open")
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-09", 23, 59)
    pts = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    p = pts[0]  # 07-06 decision → eligible at the next session (07-07) open 09:30
    assert p.eligible_execution_at == compute_eligible_execution_at(
        sch, scheduled_for=p.scheduled_for, calendar=cal
    )
    assert p.eligible_execution_at == _utc("2026-07-07", 9, 30)
    assert ASHARE_SESSION_OPEN == "09:30"
    assert p.execution_price_field == "open"


def test_eligible_time_next_bar_close_1d():
    cal = _calendar(_WEEK1)
    sch = _schedule(kind="daily", policy="next_bar_close", price="close")
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-09", 23, 59)
    pts = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    p = pts[0]  # 07-06 decision → eligible at the next session (07-07) close 15:00
    assert p.eligible_execution_at == compute_eligible_execution_at(
        sch, scheduled_for=p.scheduled_for, calendar=cal
    )
    assert p.eligible_execution_at == _utc("2026-07-07", 15, 0)
    assert ASHARE_SESSION_CLOSE == "15:00"
    assert p.execution_price_field == "close"


# =========================================================================== #
# Row 8 — non-1d bar frequency refused via the consumed Phase-6 error            #
# =========================================================================== #
def test_non_1d_frequency_refused():
    cal = _calendar(_WEEK1)
    sch = _schedule(kind="daily", bar_frequency="30m")
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-09", 23, 59)
    with pytest.raises(UnsupportedBarFrequencyError):
        resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)


# =========================================================================== #
# driver harness                                                              #
# =========================================================================== #
class _FixedClock:
    def __init__(self, at: datetime):
        self._at = at

    def now(self) -> datetime:
        return self._at


def _proposal_artifact(aid: str):
    prop = PortfolioTargetProposal(
        positions=(TargetPosition(symbol=normalize_symbol("600519"), target_weight=1.0),),
        cash_weight=0.0,
        rationale="shadow advisory only; zero trading authority",
        confidence=Confidence.MEDIUM,
    )
    return SimpleNamespace(
        payload_schema_ref=SchemaRef(name="PortfolioTargetProposal", version="1"),
        payload=prop.model_dump(),
        artifact_id=aid,
        content_digest=prop.semantic_digest(),
    )


class _FakeCoordinator:
    """A recorded fake per-point plan coordinator returning real-contract objects."""

    def __init__(self, *, as_of_override=None):
        self.bootstrap_calls: list[int] = []
        self.llm_calls: list[int] = []
        self.det_calls: list[int] = []
        self._as_of_override = dict(as_of_override or {})

    def bootstrap_context(self, point):
        self.bootstrap_calls.append(point.point_ordinal)
        as_of = self._as_of_override.get(point.point_ordinal, point.decision_as_of)
        return ReplayPointSnapshot(data_context=pilot_data_context(as_of=as_of))

    def llm_proposal(self, point, snapshot):
        self.llm_calls.append(point.point_ordinal)
        return _proposal_artifact(f"art-{point.point_ordinal}")

    def deterministic_targets(self, point, snapshot):
        self.det_calls.append(point.point_ordinal)
        return DeterministicBook(
            rule_id="rule-eqw",
            positions=(TargetPosition(symbol=normalize_symbol("600519"), target_weight=1.0),),
            cash_weight=0.0,
        )


class _FakeBudgetEventSink:
    """In-memory append-only budget-event sink (mirrors test_budget_ledger)."""

    def __init__(self, *, run_id, ledger_id, clock):
        self._run_id, self._ledger_id, self._clock = run_id, ledger_id, clock
        self._events: list[BudgetEvent] = []
        self._by_key: dict[str, BudgetEvent] = {}
        self._seq = 0
        self._res_seq = 0

    def budget_events(self):
        return tuple(self._events)

    def find_by_idempotency_key(self, key):
        return self._by_key.get(key)

    def append(self, command: BudgetTransitionCommand) -> BudgetEvent:
        self._seq += 1
        if command.operation in ("reserve_plan", "reserve_node"):
            self._res_seq += 1
            reservation_id = f"res-{self._res_seq}"
        else:
            reservation_id = command.semantic_args.reservation_id
        ev = BudgetEvent(
            seq=self._seq, event_id=f"be-{self._seq}", run_id=self._run_id,
            ledger_id=self._ledger_id, reservation_id=reservation_id,
            occurred_at=clock_now(self._clock), command=command,
        )
        self._events.append(ev)
        self._by_key[command.idempotency_key] = ev
        return ev


def _run_budget(ledger_id="led-replay"):
    return RunBudget(
        ledger_id=ledger_id, max_tokens=10_000_000, max_llm_invocations=100,
        max_concurrency=64,
    )


def _exec_config(schedule):
    return ShadowExecutionConfig(
        universe=(normalize_symbol("600519"),),
        init_cash=1_000_000.0,
        data_snapshot_content_digest="a" * 64,
        vintage_manifest_digest="b" * 64,
        calendar_id=CAL_ID,
        cost_model_digest="c" * 64,
        matching_engine_version="shadow-match-v1",
        clock=ClockSpec(as_of=_utc("2026-07-06", 14, 0), timezone=TZ, calendar_id=CAL_ID),
        schedule_digest=schedule.content_digest,
        intrabar_exit_priority="worst_case",
    )


def _schedule_ref(schedule):
    return ContentRef(
        id=schedule.id, version=schedule.version, content_digest=schedule.content_digest
    )


def _request(schedule, *, approval=ApprovalPolicy.REQUIRED, rid="req-replay"):
    return OrchestrationRequest(
        request_id=rid, goal="shadow replay", workflow="orchestrate_only",
        approval_policy=approval, decision_schedule_ref=_schedule_ref(schedule),
    )


def _registry(schedule):
    reg = DecisionScheduleRegistry()
    reg.register(schedule)
    return reg


def _bindings(schedule, cal, *, coordinator=None, feed_floors=(), seats_seam=None,
              ledger=None):
    clk = _FixedClock(_utc("2026-07-06", 14, 0))
    sink = _FakeBudgetEventSink(run_id="replay-run.req-replay", ledger_id="led-replay", clock=clk)
    rb = _run_budget()
    budget = BudgetLedger(sink=sink, run_budget=rb)
    b = ReplayRuntimeBindings(
        admission=coordinator or _FakeCoordinator(),
        budget=budget,
        run_budget=rb,
        schedule_registry=_registry(schedule),
        calendar=cal,
        clock_factory=lambda point: _FixedClock(point.decision_as_of),
        seats_budget_seam=seats_seam or SimpleNamespace(load_state=lambda: {"counts": {}, "daily_budget": 24}),
        feed_floors=feed_floors,
        intent_ledger=ledger or ReplayIntentLedger(),
    )
    return b, sink


def _three_point_env():
    # 07-06/07/08 decision points; 07-09 trailing for the last eligible.
    cal = _calendar(["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09"])
    sch = _schedule(kind="daily")
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-08", 23, 59)
    return sch, cal, start, end


# =========================================================================== #
# Row 9 — points admitted strictly in point_ordinal order                       #
# =========================================================================== #
def test_points_run_in_order():
    sch, cal, start, end = _three_point_env()
    coord = _FakeCoordinator()
    binds, _ = _bindings(sch, cal, coordinator=coord)
    state = run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds,
    )
    assert coord.bootstrap_calls == [1, 2, 3]
    assert coord.llm_calls == [1, 2, 3]
    assert coord.det_calls == [1, 2, 3]
    assert state.completed_points == 3 and state.total_points == 3
    assert state.status is ExperimentStatus.RUNNING
    assert len(binds.intent_ledger) == 3
    assert len(binds.intent_ledger.deterministic_targets) == 3


# =========================================================================== #
# Row 10 — snapshot binding enforced structurally before admission               #
# =========================================================================== #
def test_point_snapshot_binding_enforced():
    sch, cal, start, end = _three_point_env()
    # point 2's bootstrap context carries the WRONG as_of.
    bad = {2: _utc("2026-07-08", 14, 0)}
    coord = _FakeCoordinator(as_of_override=bad)
    binds, sink = _bindings(sch, cal, coordinator=coord)
    with pytest.raises(SnapshotBindingRefused):
        run_interval_replay(
            request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
            interval_start=start, interval_end=end, bindings=binds,
        )
    # point 1 processed (2 node reservations), point 2 refused BEFORE any node reserve.
    node_events = [e for e in sink.budget_events() if e.command.operation == "reserve_node"]
    node_ordinals = {e.command.semantic_args.node_id.rsplit("#", 1)[1] for e in node_events}
    assert node_ordinals == {"1"}  # only point 1's nodes; none for the refused point 2
    assert len(binds.intent_ledger) == 1  # only point 1's intent


# =========================================================================== #
# Row 11 — retroactive intent refused (ledger-level red line)                    #
# =========================================================================== #
def _wrap_point_intent(sch, cal, point, *, intent_id):
    reg = _registry(sch)
    req = _request(sch)
    iso = point.decision_as_of.astimezone(ZoneInfo(TZ)).date().isoformat()
    return lz.wrap_proposal_as_intent(
        proposal_artifact=_proposal_artifact(intent_id),
        source_decision_artifact_id="shadow.dec.trader",
        request=req, schedule_registry=reg, calendar=cal, session_date=iso,
        decision_as_of=point.decision_as_of, target_version=1, intent_id=intent_id,
        clock=_FixedClock(point.decision_as_of),
    )


def test_retroactive_intent_refused():
    sch, cal, start, end = _three_point_env()
    pts = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    i2 = _wrap_point_intent(sch, cal, pts[1], intent_id="intent-2")
    i3 = _wrap_point_intent(sch, cal, pts[2], intent_id="intent-3")
    ledger = ReplayIntentLedger()
    ledger.append_intent(i3, expected_as_of=pts[2].decision_as_of)  # HWM = point 3
    with pytest.raises(RetroactiveIntentRefused):
        ledger.append_intent(i2, expected_as_of=pts[1].decision_as_of)  # point 2 backfill
    assert ledger.intents == (i3,)  # ledger unchanged


# =========================================================================== #
# Row 12 — point replay is idempotent (stored intent, no second envelope)        #
# =========================================================================== #
def test_point_replay_idempotent():
    sch, cal, start, end = _three_point_env()
    pts = resolve_decision_points(sch, calendar=cal, interval_start=start, interval_end=end)
    i2 = _wrap_point_intent(sch, cal, pts[1], intent_id="intent-2")
    ledger = ReplayIntentLedger()
    first = ledger.append_intent(i2, expected_as_of=pts[1].decision_as_of)
    second = ledger.append_intent(i2, expected_as_of=pts[1].decision_as_of)  # same apply key
    assert first is second is i2
    assert len(ledger) == 1  # no second envelope appended


# =========================================================================== #
# Row 13 — one RunBudget for the whole interval; det lane reserves zero LLM       #
# =========================================================================== #
def test_one_runbudget_whole_interval():
    sch, cal, start, end = _three_point_env()
    binds, sink = _bindings(sch, cal)
    run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds,
    )
    events = sink.budget_events()
    plan_events = [e for e in events if e.command.operation == "reserve_plan"]
    node_events = [e for e in events if e.command.operation == "reserve_node"]
    assert len(plan_events) == 1  # ONE plan reservation for the whole interval
    plan_res_id = plan_events[0].reservation_id
    # every per-point node reservation is a child of the one plan reservation.
    for e in node_events:
        assert e.command.semantic_args.plan_reservation_id == plan_res_id
    assert len(node_events) == 6  # 3 points × (llm lane + deterministic lane)
    det_nodes = [e for e in node_events if e.command.semantic_args.node_id.startswith("det.")]
    llm_nodes = [e for e in node_events if e.command.semantic_args.node_id.startswith("llm.")]
    assert len(det_nodes) == 3 and len(llm_nodes) == 3
    for e in det_nodes:  # deterministic-lane nodes reserve ZERO LLM invocations
        assert e.command.semantic_args.reserved_llm_invocations == 0
    for e in llm_nodes:
        assert e.command.semantic_args.reserved_llm_invocations == 1
    # the plan reservation folds coherently.
    state = fold_budget_events(events)
    assert state.reservations[plan_res_id].scope_type == "plan"


# =========================================================================== #
# Row 14 — reconcile is replay-exempt (RunBudget-only, seats untouched)          #
# =========================================================================== #
def test_budget_reconcile_replay_exempt():
    seats = {"counts": {"2026-07-10": 20}, "daily_budget": 24}
    n = reconcile_daily_llm_budget(
        seats_watch_state=seats, run_budget=_run_budget(), requested_llm_invocations=7,
        session_date="2026-07-10", is_live_session=False,
    )
    assert n == 7  # bounded by RunBudget only (plenty of room)
    assert seats == {"counts": {"2026-07-10": 20}, "daily_budget": 24}  # untouched (pure)


# =========================================================================== #
# Row 15 — reconcile is live-capped; settlement notes external use exactly once   #
# =========================================================================== #
def test_budget_reconcile_live_capped(tmp_path, monkeypatch):
    seats = {"counts": {"2026-07-10": 20}, "daily_budget": 24}
    n = reconcile_daily_llm_budget(
        seats_watch_state=seats, run_budget=_run_budget(), requested_llm_invocations=10,
        session_date="2026-07-10", is_live_session=True,
    )
    assert n == 4  # min(10, run_remaining, 24-20)

    # settlement reports the admissible count back through the watcher seam exactly once.
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    watcher.save_state({"enabled": True, "daily_budget": 24, "counts": {"2026-07-10": 20}})
    watcher.note_external_llm_use(4, now=datetime(2026, 7, 10, 10, 0))
    assert watcher.load_state()["counts"]["2026-07-10"] == 24  # 20 + 4, one pool


# =========================================================================== #
# Row 16 — watcher tick skips orchestrated codes; others judged as before        #
# =========================================================================== #
def test_watcher_skips_orchestrated_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    watcher.save_state({"enabled": True, "daily_budget": 9, "counts": {}})
    codes = [
        {"code": c, "strategy_id": "s1", "name": "n", "clock": {"decisionFreq": "hourly"},
         "creed": "", "w": 0, "pa": False, "pa_method": "", "refs": []}
        for c in ("300750", "600519")
    ]
    monkeypatch.setattr(watcher, "watching_codes", lambda: codes)
    monkeypatch.setattr(watcher, "orchestrated_codes", lambda: {"300750"})
    calls: list = []
    out = watcher.tick(
        now=datetime(2026, 7, 10, 10, 0),
        decide_fn=lambda p: calls.append(p) or {"ok": True},
        quote_fn=lambda c: {"fresh": True},
        decisions_tail_fn=lambda c: None,
    )
    assert out["skipped"]["300750"] == "orchestrated"  # the orchestrated code is skipped
    assert out["judged"] == ["600519"]  # the other code judged exactly as before
    assert [p["code"] for p in calls] == ["600519"]


def test_watcher_no_orchestrated_codes_is_unchanged(tmp_path, monkeypatch):
    # regression: with an empty orchestrated set the tick gate order is bit-unchanged.
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    watcher.save_state({"enabled": True, "daily_budget": 9, "counts": {}})
    codes = [{"code": "300750", "strategy_id": "s1", "name": "n", "clock": {},
              "creed": "", "w": 0, "pa": False, "pa_method": "", "refs": []}]
    monkeypatch.setattr(watcher, "watching_codes", lambda: codes)
    out = watcher.tick(
        now=datetime(2026, 7, 10, 10, 0), decide_fn=lambda p: {"ok": True},
        quote_fn=lambda c: {"fresh": True}, decisions_tail_fn=lambda c: None,
    )
    assert out["judged"] == ["300750"] and out["skipped"] == {}


# =========================================================================== #
# Row 17 — AUTO approval remains rejected; nothing runs                          #
# =========================================================================== #
def test_auto_approval_still_rejected():
    sch, cal, start, end = _three_point_env()
    coord = _FakeCoordinator()
    binds, sink = _bindings(sch, cal, coordinator=coord)
    with pytest.raises(ReplayApprovalRefused):
        run_interval_replay(
            request=_request(sch, approval=ApprovalPolicy.AUTO), schedule=sch,
            execution_config=_exec_config(sch), interval_start=start, interval_end=end,
            bindings=binds,
        )
    assert coord.bootstrap_calls == []  # nothing ran
    assert sink.budget_events() == ()  # not even a plan reservation


# =========================================================================== #
# extras — end-to-end envelope realness + degraded-point honesty                 #
# =========================================================================== #
def test_driver_produces_real_shadow_intents_and_state():
    sch, cal, start, end = _three_point_env()
    binds, _ = _bindings(sch, cal)
    state = run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds,
    )
    intents = binds.intent_ledger.intents
    assert len(intents) == 3
    for it in intents:
        # every intent is a REAL shadow-only, advisory, LLM-origin envelope.
        assert it.origin == "LLM" and it.authority == "ADVISORY_ONLY"
        assert it.execution_scope == "SHADOW_ONLY"
        assert it.decision_schedule_digest == sch.content_digest
    # intents are strictly ascending in scheduled_for (monotone high-water mark).
    sf = [it.scheduled_for for it in intents]
    assert sf == sorted(sf) and len(set(sf)) == 3
    assert isinstance(state, ShadowReplayRunState)
    assert state.execution_config_digest == _exec_config(sch).semantic_digest()
    assert state.curve_report_ref is None  # set by Task 5's curve stage, never here


def test_degraded_point_is_recorded_not_a_failure():
    from guanlan_v2.orchestration.adapters.replay_data import ArchiveFeedFloor, ARCHIVE_FEED_IDS

    sch, cal, start, end = _three_point_env()
    # a feed whose coverage floor postdates every replay point ⇒ honestly degraded.
    floors = tuple(
        ArchiveFeedFloor(feed_id=fid, floor_date="2027-01-01", archive_root=f"var/archive/{fid}")
        for fid in ARCHIVE_FEED_IDS
    )
    binds, _ = _bindings(sch, cal, feed_floors=floors)
    state = run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=start, interval_end=end, bindings=binds,
    )
    # the run COMPLETES its points (never a failure) and records the degradation.
    assert state.completed_points == 3
    degraded = binds.intent_ledger.degraded_points
    assert set(degraded) == {1, 2, 3}
    for badges in degraded.values():
        assert any(b.startswith("archive_pre_floor:") for b in badges)
