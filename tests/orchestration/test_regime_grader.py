# -*- coding: utf-8 -*-
"""Phase 5 · Task 6 — delayed deterministic regime grader + observed-trade-date
calendar.

Written test-first (RED until ``memory.experience`` grows the calendar/grader/
maturation surface). The grader NEVER runs an LLM, never grades before maturity,
and grades from realized data only (PIT: realized data as-of the maturity date).
Maturity is counted in **bars of the observed benchmark trade-date list**, never
calendar days — a holiday gap can never fake maturity.

Covers the brief's matrix:

* label rules at every threshold boundary, both sides (牛/熊/震荡 — R6 vocabulary;
  risk_off-by-drawdown precedence over risk_on; risk_on; neutral fall-through);
* forward-return / drawdown / vol pinned numerically on a hand-computed path;
* holiday-gap fixture (a 9-calendar-day gap ⇒ exit on the 20th *session*);
* entry-session convention (first session strictly after ``as_of``'s session; a
  case dated on a non-session grades from the previous session);
* unmatured ⇒ ``CaseMaturityPending`` with a deterministic ``wakeup_key`` and no event;
* maturation batch idempotency (second run appends zero);
* ``available_at`` data-driven differential (a shifted wall clock moves ``matured_at``
  only; the folded historical view is byte-stable);
* heat-None-with-reason in v1;
* golden digest reproduction;
* calendar protocol conformance (``sessions_between`` vs list positions, ``is_session``);
* LLM-free import assertion;
* matured-only downstream gate (a batch with one pending case is refused).

Run: ``pytest tests/orchestration/test_regime_grader.py -v``
"""
from __future__ import annotations

import ast
import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration.data.calendar import TradingCalendar
from guanlan_v2.orchestration.digest import canonical_json, content_digest
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.market.factors import (
    DailyValueRow,
    EvidenceAnchor,
    HeatState,
    RegimeReport,
    RiskState,
    TrendState,
)
from guanlan_v2.orchestration.memory.experience import (
    EXPERIENCE_PARTITION,
    EXPERIENCE_STREAM_ID,
    OBSERVED_CALENDAR_ID,
    OBSERVED_CALENDAR_MATERIAL_ID,
    CaseMatured,
    CaseMaturityPending,
    CaseView,
    ExperienceLog,
    ObservedTradeDateCalendar,
    RealizedRegime,
    RegimeCase,
    RegimeGraderSpec,
    build_observed_calendar,
    fold_case_views,
    grade_case,
    mature_pending_cases,
    matured_only,
)
from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry

UTC = timezone.utc
DH = "ab" * 32
GOLDEN = Path(__file__).parent / "golden" / "regime_grader_policy_v1.json"


# --------------------------------------------------------------------------- #
# clocks + env (mirror the Task 4 store harness)                                #
# --------------------------------------------------------------------------- #
class SteppingClock:
    """Deterministic advancing clock (+1s per read)."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


def _experience_registry() -> Phase2RuntimeRegistry:
    reg = Phase2RuntimeRegistry()
    for model in (RegimeCase, RealizedRegime, CaseMatured):
        reg.register(model)
    reg.seal()
    return reg


class _Env:
    def __init__(self, clock_start: datetime | None = None) -> None:
        self.resolver = SchemaRegistryResolver()
        self.registry = _experience_registry()
        self.digest = self.resolver.register(self.registry)
        self.clock = SteppingClock(clock_start or datetime(2026, 9, 1, 1, 0, tzinfo=UTC))
        self.stores = RuntimeStores(resolver=self.resolver, clock=self.clock)

    def log(self) -> ExperienceLog:
        return ExperienceLog(
            event_store=self.stores.events,
            payload_store=self.stores.payloads,
            registry=self.registry,
            clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work,
        )

    def matured_count(self) -> int:
        return sum(
            1
            for ev in self.stores.events.journal(EXPERIENCE_STREAM_ID, EXPERIENCE_PARTITION)
            if ev.event_type is EventType.CASE_MATURED
        )


# --------------------------------------------------------------------------- #
# fixtures / builders                                                           #
# --------------------------------------------------------------------------- #
def _avail(day_iso: str) -> datetime:
    """The reviewed knowable-time for a daily bar: 07:05Z (15:05 Asia/Shanghai)."""
    d = date.fromisoformat(day_iso)
    return datetime(d.year, d.month, d.day, 7, 5, tzinfo=UTC)


def _row(day_iso: str, value: float) -> DailyValueRow:
    return DailyValueRow(date=day_iso, value=value, available_at=_avail(day_iso))


def _weekdays(start_iso: str, n: int) -> tuple[str, ...]:
    d = date.fromisoformat(start_iso)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d.isoformat())
        d += timedelta(days=1)
    return tuple(out)


def _bench(dates: tuple[str, ...], returns: list[float]) -> tuple[DailyValueRow, ...]:
    assert len(dates) == len(returns)
    return tuple(_row(d, r) for d, r in zip(dates, returns))


def _spec(
    *,
    grader_version: str = "regime-grader-test",
    horizon: int = 1,
    bull: float = 0.03,
    bear: float = -0.03,
    risk_off_dd: float = -0.05,
    risk_on_ret: float = 0.02,
    risk_on_dd: float = -0.03,
    benchmark_id: str = "eqw_all_a",
) -> RegimeGraderSpec:
    return RegimeGraderSpec.build(
        grader_version=grader_version,
        horizon_trading_days=horizon,
        benchmark_id=benchmark_id,
        bull_min_return=bull,
        bear_max_return=bear,
        risk_off_max_drawdown=risk_off_dd,
        risk_on_min_return=risk_on_ret,
        risk_on_min_drawdown=risk_on_dd,
    )


def _judgment(as_of: datetime) -> RegimeReport:
    return RegimeReport.build(
        as_of=as_of, factor_report_digest=DH,
        trend=TrendState.BULL, risk_state=RiskState.RISK_ON, heat_state=HeatState.NORMAL,
        trend_probabilities={TrendState.BULL: 0.6, TrendState.BEAR: 0.2, TrendState.RANGE: 0.1, TrendState.UNKNOWN: 0.1},
        risk_probabilities={RiskState.RISK_ON: 0.5, RiskState.RISK_OFF: 0.2, RiskState.NEUTRAL: 0.2, RiskState.UNKNOWN: 0.1},
        heat_probabilities={HeatState.NORMAL: 0.7, HeatState.OVERHEAT: 0.2, HeatState.UNKNOWN: 0.1},
        confidence=Confidence.MEDIUM,
        evidence=(EvidenceAnchor(factor_id="breadth.ad_ratio", value=0.7, reading="broadening"),),
        drivers=("breadth broadening",),
        evidence_factor_ids=("breadth.ad_ratio",),
        narrative="Breadth broadening while heat stays contained.",
    )


def _case(case_id: str, as_of: datetime, available_at: datetime | None = None) -> RegimeCase:
    available_at = available_at or as_of
    return RegimeCase.build(
        id=case_id, as_of=as_of, available_at=available_at,
        feature_schema_version="mfs-v1", scaler_digest=DH,
        features={"breadth.ad_ratio": 0.7}, feature_coverage={"breadth.ad_ratio": 1.0},
        missing_features=(), judgment=_judgment(as_of), links=(),
    )


def _as_of(day_iso: str) -> datetime:
    return _avail(day_iso)


def _grade_horizon1(ret: float, spec: RegimeGraderSpec) -> RealizedRegime:
    """Grade a single-return window (horizon 1): forward == ret, dd == min(0, ret)."""
    dates = _weekdays("2026-01-05", 4)
    bench = _bench(dates, [0.0, 0.0, ret, 0.0])
    cal = build_observed_calendar(dates)
    case = _case("case.x", _as_of(dates[0]))
    result = grade_case(case, bench=bench, calendar=cal, grader=spec)
    assert isinstance(result, RealizedRegime)
    return result


# --------------------------------------------------------------------------- #
# calendar protocol conformance                                                 #
# --------------------------------------------------------------------------- #
def test_calendar_is_a_trading_calendar():
    dates = _weekdays("2026-01-05", 10)
    cal = build_observed_calendar(dates)
    assert isinstance(cal, TradingCalendar)
    assert cal.calendar_id == OBSERVED_CALENDAR_ID == "cn-ashare-observed-v1"
    assert cal.material_ref.id == OBSERVED_CALENDAR_MATERIAL_ID == "calendar.cn_ashare_observed"
    # version defaults to the list-digest prefix (content-tracking identity).
    assert cal.material_ref.version and len(cal.material_ref.version) >= 8


def test_calendar_is_session_membership():
    dates = _weekdays("2026-01-05", 6)
    cal = build_observed_calendar(dates)
    assert cal.is_session(date.fromisoformat(dates[0])) is True
    # a Saturday inside the covered span is not a session.
    sat = date(2026, 1, 10)
    assert sat.weekday() == 5
    assert cal.is_session(sat) is False


def test_calendar_sessions_between_equals_list_positions():
    dates = _weekdays("2026-01-05", 12)
    cal = build_observed_calendar(dates)
    start, end = dates[2], dates[7]
    by_position = sum(1 for d in dates if start <= d <= end)
    assert cal.sessions_between(date.fromisoformat(start), date.fromisoformat(end)) == by_position == 6


def test_build_observed_calendar_requires_strictly_increasing():
    with pytest.raises(ValueError):
        build_observed_calendar(("2026-01-06", "2026-01-05"))  # descending
    with pytest.raises(ValueError):
        build_observed_calendar(("2026-01-05", "2026-01-05"))  # duplicate


# --------------------------------------------------------------------------- #
# entry-session convention                                                       #
# --------------------------------------------------------------------------- #
def test_entry_is_first_session_strictly_after_as_of_session():
    dates = _weekdays("2026-01-05", 6)
    bench = _bench(dates, [0.0] * 6)
    cal = build_observed_calendar(dates)
    case = _case("case.a", _as_of(dates[0]))
    r = grade_case(case, bench=bench, calendar=cal, grader=_spec(horizon=3))
    assert isinstance(r, RealizedRegime)
    assert r.entry_date == dates[1]         # strictly after as_of session dates[0]
    assert r.exit_date == dates[4]          # entry_idx(1) + horizon(3)


def test_case_on_non_session_grades_from_previous_session():
    dates = _weekdays("2026-01-05", 8)  # Mon..; a Saturday sits between Fri and Mon
    bench = _bench(dates, [0.0] * 8)
    cal = build_observed_calendar(dates)
    # dates[4] is Fri 2026-01-09; the as_of is Sat 2026-01-10 (a non-session).
    sat = "2026-01-10"
    assert date.fromisoformat(sat).weekday() == 5
    assert dates[4] == "2026-01-09"
    case = _case("case.sat", _as_of(sat))
    r = grade_case(case, bench=bench, calendar=cal, grader=_spec(horizon=1))
    assert isinstance(r, RealizedRegime)
    # last session <= Sat is Fri dates[4]; entry = next session dates[5].
    assert r.entry_date == dates[5]


# --------------------------------------------------------------------------- #
# maturity counts bars, never calendar days (holiday gap)                        #
# --------------------------------------------------------------------------- #
def test_holiday_gap_exit_lands_on_the_20th_session_not_day_20():
    # 23 sessions with a 9-calendar-day hole between index 5 and 6.
    head = _weekdays("2026-02-02", 6)                 # 6 sessions, ends Mon 2026-02-09
    tail = _weekdays("2026-02-18", 17)                # resume 9 calendar days later
    dates = head + tail
    assert (date.fromisoformat(tail[0]) - date.fromisoformat(head[-1])).days == 9
    bench = _bench(dates, [0.0] * len(dates))
    cal = build_observed_calendar(dates)
    case = _case("case.gap", _as_of(dates[0]))
    r = grade_case(case, bench=bench, calendar=cal, grader=_spec(horizon=20))
    assert isinstance(r, RealizedRegime)
    assert r.entry_date == dates[1]
    assert r.exit_date == dates[21]                   # 20 SESSIONS after entry (positional)
    # proof it is not calendar-day arithmetic: the span is far more than 20 days.
    span_days = (date.fromisoformat(r.exit_date) - date.fromisoformat(r.entry_date)).days
    assert span_days != 20 and span_days > 20


# --------------------------------------------------------------------------- #
# numeric pins — forward return / drawdown / volatility                          #
# --------------------------------------------------------------------------- #
def test_forward_return_drawdown_vol_pinned():
    dates = _weekdays("2026-03-02", 5)
    # window returns (positions 2..4) = +0.10, -0.05, +0.02
    bench = _bench(dates, [0.0, 0.0, 0.10, -0.05, 0.02])
    cal = build_observed_calendar(dates)
    case = _case("case.n", _as_of(dates[0]))
    r = grade_case(case, bench=bench, calendar=cal, grader=_spec(horizon=3))
    assert isinstance(r, RealizedRegime)
    w = 1.10 * 0.95 * 1.02
    assert r.forward_return == pytest.approx(w - 1.0)
    assert r.max_drawdown == pytest.approx(1.045 / 1.10 - 1.0)   # -0.05 dip at the trough
    assert r.realized_volatility == pytest.approx(statistics.pstdev([0.10, -0.05, 0.02]))
    assert r.entry_date == dates[1] and r.exit_date == dates[4]
    assert r.horizon_trading_days == 3


def test_monotonic_up_path_has_zero_drawdown():
    r = _grade_horizon1(0.05, _spec(horizon=1))
    assert r.max_drawdown == 0.0
    assert r.realized_volatility == 0.0


# --------------------------------------------------------------------------- #
# label rules — trend boundaries (R6 vocabulary)                                 #
# --------------------------------------------------------------------------- #
def test_trend_bull_at_and_below_boundary():
    spec = _spec(horizon=1, bull=0.03, bear=-0.03)
    assert _grade_horizon1(0.03, spec).realized_trend == "牛"     # >= bull
    assert _grade_horizon1(0.029, spec).realized_trend == "震荡"  # just below


def test_trend_bear_at_and_above_boundary():
    spec = _spec(horizon=1, bull=0.03, bear=-0.03)
    assert _grade_horizon1(-0.03, spec).realized_trend == "熊"     # <= bear
    assert _grade_horizon1(-0.029, spec).realized_trend == "震荡"  # just above


# --------------------------------------------------------------------------- #
# label rules — risk axis                                                        #
# --------------------------------------------------------------------------- #
def test_risk_off_by_drawdown_takes_precedence():
    spec = _spec(horizon=1, risk_off_dd=-0.05, risk_on_ret=0.02, risk_on_dd=-0.03)
    r = _grade_horizon1(-0.05, spec)   # dd == -0.05 <= risk_off threshold
    assert r.realized_risk == "risk_off"
    assert _grade_horizon1(-0.06, spec).realized_risk == "risk_off"


def test_risk_on_when_return_high_and_shallow_drawdown():
    spec = _spec(horizon=1, risk_off_dd=-0.05, risk_on_ret=0.02, risk_on_dd=-0.03)
    r = _grade_horizon1(0.02, spec)    # forward 0.02 >= min, dd 0 > -0.03
    assert r.realized_risk == "risk_on"


def test_risk_neutral_fall_through():
    spec = _spec(horizon=1, risk_off_dd=-0.05, risk_on_ret=0.02, risk_on_dd=-0.03)
    # forward below risk_on min, drawdown not deep enough for risk_off.
    assert _grade_horizon1(0.019, spec).realized_risk == "neutral"
    assert _grade_horizon1(-0.04, spec).realized_risk == "neutral"


def test_risk_on_vetoed_by_deep_intrapath_drawdown():
    # forward >= risk_on min but the path dipped to <= risk_on_min_drawdown ⇒ NOT risk_on.
    dates = _weekdays("2026-04-06", 5)
    spec = _spec(horizon=2, risk_off_dd=-0.05, risk_on_ret=0.02, risk_on_dd=-0.03)
    # window returns (positions 2..3): -0.03 then +0.06 ⇒ forward ~0.0282, trough dd == -0.03
    bench = _bench(dates, [0.0, 0.0, -0.03, 0.06, 0.0])
    cal = build_observed_calendar(dates)
    case = _case("case.veto", _as_of(dates[0]))
    r = grade_case(case, bench=bench, calendar=cal, grader=spec)
    assert isinstance(r, RealizedRegime)
    assert r.forward_return == pytest.approx(0.97 * 1.06 - 1.0)
    assert r.max_drawdown == pytest.approx(-0.03)
    assert r.realized_risk == "neutral"   # -0.03 is not > risk_on_min_drawdown(-0.03)
    # complementary: a shallower dip (-0.029) restores risk_on.
    bench2 = (_row(dates[0], 0.0), _row(dates[1], 0.0), _row(dates[2], -0.029),
              _row(dates[3], 0.06), _row(dates[4], 0.0))
    r2 = grade_case(case, bench=bench2, calendar=cal, grader=spec)
    assert isinstance(r2, RealizedRegime)
    assert r2.realized_risk == "risk_on"


def test_thresholds_come_from_the_spec_not_hardcoded():
    dates = _weekdays("2026-05-04", 4)
    bench = _bench(dates, [0.0, 0.0, 0.04, 0.0])
    cal = build_observed_calendar(dates)
    case = _case("case.t", _as_of(dates[0]))
    r_a = grade_case(case, bench=bench, calendar=cal, grader=_spec(horizon=1, bull=0.03))
    r_b = grade_case(case, bench=bench, calendar=cal, grader=_spec(horizon=1, bull=0.05))
    assert r_a.realized_trend == "牛"     # 0.04 >= 0.03
    assert r_b.realized_trend == "震荡"   # 0.04 < 0.05 — same data, different spec


# --------------------------------------------------------------------------- #
# heat is honestly None in v1                                                    #
# --------------------------------------------------------------------------- #
def test_heat_none_with_reason_v1():
    r = _grade_horizon1(0.01, _spec(horizon=1))
    assert r.realized_heat is None
    assert r.heat_unavailable_reason == "no_realized_heat_definition_v1"


# --------------------------------------------------------------------------- #
# available_at / grader stamping are data-driven                                 #
# --------------------------------------------------------------------------- #
def test_available_at_is_the_exit_bar_availability():
    dates = _weekdays("2026-06-01", 5)
    bench = _bench(dates, [0.0, 0.0, 0.01, 0.01, 0.01])
    cal = build_observed_calendar(dates)
    case = _case("case.av", _as_of(dates[0]))
    spec = _spec(horizon=3)
    r = grade_case(case, bench=bench, calendar=cal, grader=spec)
    assert isinstance(r, RealizedRegime)
    assert r.available_at == _avail(dates[4])       # exit bar (positional exit) availability
    assert r.grader_version == spec.grader_version
    assert r.grader_digest == spec.content_digest
    assert r.benchmark_id == spec.benchmark_id
    # data_snapshot_hash is the digest of the exact (entry, exit] window.
    window = (bench[2], bench[3], bench[4])
    assert r.data_snapshot_hash == content_digest(window)


def test_grade_case_is_clock_free_and_deterministic():
    dates = _weekdays("2026-06-01", 5)
    bench = _bench(dates, [0.0, 0.0, 0.01, -0.02, 0.03])
    cal = build_observed_calendar(dates)
    case = _case("case.d", _as_of(dates[0]))
    spec = _spec(horizon=3)
    r1 = grade_case(case, bench=bench, calendar=cal, grader=spec)
    r2 = grade_case(case, bench=bench, calendar=cal, grader=spec)
    assert r1.semantic_digest() == r2.semantic_digest()


# --------------------------------------------------------------------------- #
# unmatured ⇒ CaseMaturityPending, deterministic wakeup_key, no event            #
# --------------------------------------------------------------------------- #
def test_unmatured_yields_pending_with_deterministic_wakeup_key():
    dates = _weekdays("2026-07-01", 5)       # only 5 sessions
    bench = _bench(dates, [0.0] * 5)
    cal = build_observed_calendar(dates)
    case = _case("case.p", _as_of(dates[0]))
    spec = _spec(horizon=20)                 # exit far past the observed list
    result = grade_case(case, bench=bench, calendar=cal, grader=spec)
    assert isinstance(result, CaseMaturityPending)
    assert result.wakeup_key == f"case.mature:{case.id}:{spec.grader_version}"
    # resume_after is aware UTC and never earlier than the last observed session close.
    assert result.resume_after.tzinfo is not None
    assert result.resume_after >= _avail(dates[-1])
    # deterministic (a scheduling hint, but still reproducible).
    again = grade_case(case, bench=bench, calendar=cal, grader=spec)
    assert again.resume_after == result.resume_after


def test_mature_pending_cases_leaves_unmatured_pending_with_no_event():
    dates = _weekdays("2026-07-01", 5)
    bench = _bench(dates, [0.0] * 5)
    cal = build_observed_calendar(dates)
    spec = _spec(horizon=20)
    env = _Env()
    log = env.log()
    case = _case("case.p", _as_of(dates[0]), available_at=_as_of(dates[0]))
    log.append_case(case, correlation_run_id=None, idempotency_key=f"case.created:{case.id}")
    vantage = datetime(2026, 12, 31, tzinfo=UTC)
    views = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=vantage)
    assert len(views) == 1 and views[0].state == "pending"
    out = mature_pending_cases(views=views, bench=bench, calendar=cal, grader=spec, log=log, clock=env.clock)
    assert out == ()
    assert env.matured_count() == 0


# --------------------------------------------------------------------------- #
# maturation batch — appends CaseMatured; second run appends zero                 #
# --------------------------------------------------------------------------- #
def _matured_scenario():
    dates = _weekdays("2026-07-01", 25)
    bench = _bench(dates, [0.001] * 25)
    cal = build_observed_calendar(dates)
    spec = _spec(grader_version="regime-grader-v1", horizon=20)
    return dates, bench, cal, spec


def test_mature_pending_cases_appends_and_is_idempotent():
    dates, bench, cal, spec = _matured_scenario()
    env = _Env()
    log = env.log()
    case = _case("case.m", _as_of(dates[0]))
    log.append_case(case, correlation_run_id="run-1", idempotency_key=f"case.created:{case.id}")
    vantage = datetime(2027, 1, 31, tzinfo=UTC)

    views1 = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=vantage)
    out1 = mature_pending_cases(views=views1, bench=bench, calendar=cal, grader=spec, log=log, clock=env.clock)
    assert len(out1) == 1 and isinstance(out1[0], CaseMatured)
    assert out1[0].case_id == "case.m"
    assert out1[0].available_at == out1[0].realized.available_at   # PIT (exit bar), not clock
    first = env.matured_count()
    assert first == 1

    # fold fresh — the case is now matured, so a second run appends nothing.
    views2 = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=vantage)
    assert views2[0].state == "matured"
    out2 = mature_pending_cases(views=views2, bench=bench, calendar=cal, grader=spec, log=log, clock=env.clock)
    assert out2 == ()
    assert env.matured_count() == first


def test_available_at_data_driven_matured_at_clock_driven_differential():
    dates, bench, cal, spec = _matured_scenario()
    # two independent stores whose clocks sit at very different wall times.
    env_a = _Env(clock_start=datetime(2027, 1, 1, 3, 0, tzinfo=UTC))
    env_b = _Env(clock_start=datetime(2030, 6, 15, 9, 0, tzinfo=UTC))
    vantage = datetime(2027, 1, 31, tzinfo=UTC)

    def _mature(env):
        log = env.log()
        case = _case("case.m", _as_of(dates[0]))
        log.append_case(case, correlation_run_id=None, idempotency_key=f"case.created:{case.id}")
        views = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=vantage)
        out = mature_pending_cases(views=views, bench=bench, calendar=cal, grader=spec, log=log, clock=env.clock)
        folded = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=vantage)
        return out[0], folded[0]

    matured_a, view_a = _mature(env_a)
    matured_b, view_b = _mature(env_b)

    # available_at (PIT, exit-bar-driven) is identical; matured_at (audit) differs.
    assert matured_a.available_at == matured_b.available_at == _avail(dates[21])
    assert matured_a.matured_at != matured_b.matured_at
    # the folded historical view is byte-stable regardless of the wall clock.
    assert view_a.state == view_b.state == "matured"
    assert view_a.realized.semantic_digest() == view_b.realized.semantic_digest()
    assert view_a.semantic_digest() == view_b.semantic_digest()


# --------------------------------------------------------------------------- #
# matured-only downstream gate                                                    #
# --------------------------------------------------------------------------- #
def test_matured_only_returns_matured_and_reviewed():
    dates, bench, cal, spec = _matured_scenario()
    case = _case("case.m", _as_of(dates[0]))
    realized = grade_case(case, bench=bench, calendar=cal, grader=spec)
    assert isinstance(realized, RealizedRegime)
    v_matured = CaseView(case=case, state="matured", realized=realized, maturity_event_id="ev-1")
    v_reviewed = CaseView(
        case=_case("case.r", _as_of(dates[0])), state="reviewed",
        realized=realized, lesson="a durable lesson", maturity_event_id="ev-2",
    )
    out = matured_only((v_matured, v_reviewed))
    assert out == (v_matured, v_reviewed)


def test_matured_only_refuses_a_batch_with_a_pending_case():
    dates, bench, cal, spec = _matured_scenario()
    case = _case("case.m", _as_of(dates[0]))
    realized = grade_case(case, bench=bench, calendar=cal, grader=spec)
    v_matured = CaseView(case=case, state="matured", realized=realized, maturity_event_id="ev-1")
    v_pending = CaseView(case=_case("case.pending", _as_of(dates[0])), state="pending")
    with pytest.raises(ValueError) as exc:
        matured_only((v_matured, v_pending))
    assert "case.pending" in str(exc.value)


# --------------------------------------------------------------------------- #
# golden reproduction                                                            #
# --------------------------------------------------------------------------- #
def test_golden_regime_grader_policy_reproduces_digest():
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    policy = doc["policy"]
    spec = RegimeGraderSpec.build(
        grader_version=policy["grader_version"],
        horizon_trading_days=policy["horizon_trading_days"],
        benchmark_id=policy["benchmark_id"],
        bull_min_return=policy["bull_min_return"],
        bear_max_return=policy["bear_max_return"],
        risk_off_max_drawdown=policy["risk_off_max_drawdown"],
        risk_on_min_return=policy["risk_on_min_return"],
        risk_on_min_drawdown=policy["risk_on_min_drawdown"],
    )
    assert spec.content_digest == policy["content_digest"] == doc["content_digest"]
    assert canonical_json(spec) == doc["canonical_json"]
    # the golden is the human-readable authority: one note per label rule.
    assert isinstance(doc["notes"], list) and len(doc["notes"]) >= 4


def test_golden_v1_values_are_the_reviewed_provisionals():
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    p = doc["policy"]
    assert p["grader_version"] == "regime-grader-v1"
    assert p["horizon_trading_days"] == 20
    assert p["benchmark_id"] == "eqw_all_a"
    assert p["bull_min_return"] == 0.03
    assert p["bear_max_return"] == -0.03
    assert p["risk_off_max_drawdown"] == -0.05
    assert p["risk_on_min_return"] == 0.02
    assert p["risk_on_min_drawdown"] == -0.03


# --------------------------------------------------------------------------- #
# LLM-free by construction (import inspection)                                    #
# --------------------------------------------------------------------------- #
def test_experience_module_imports_no_llm_or_gateway():
    src = Path(
        __file__
    ).parents[2].joinpath("guanlan_v2", "orchestration", "memory", "experience.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("llm", "gateway", "openai", "anthropic", "kimi", "httpx", "reasoner", "console")
    leaked = [m for m in imported for f in forbidden if f in m.lower()]
    assert not leaked, f"grader module must be LLM-free; forbidden imports: {leaked}"
