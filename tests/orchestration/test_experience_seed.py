# -*- coding: utf-8 -*-
"""Phase 5 · Task 7 — cold-start historical replay seeding + future-case invariance.

Written test-first (RED until ``memory.experience`` grows the seed-judgment proxy,
the ``SeedReport`` contract and the time-ordered ``seed_experience_from_history``
walk). The seeder replays HISTORICAL data through the deterministic pipeline
(Task 3 compute → seed judgment proxy → Task 4 case append → Task 6 grader
maturity) to pre-populate the experience library, with the FUTURE-CASE INVARIANCE
acceptance at its heart: seeding history up to date D then querying as-of D' < D
yields byte-identical retrieval to having seeded only up to D'.

Covers the brief's Step-1 matrix:

* the five §6.4 invariants over a synthetic 3-"year" deterministic random-walk
  fixture (fixed seed constant): future-case invariance triple recorded
  before/after seeding a later year, byte-for-byte; re-seed no-op counts; ≤D-only
  judgment differential; two-clock byte-identity of payload digests; coverage-gap
  honesty;
* proxy-judgment rule pins — each branch of the trend/risk/heat rule, the
  unknown-mass rescale, and a fully-missing day yielding modal-unknown LOW;
* seeded-vs-live indistinguishability except the ``links`` marker;
* the Task-5 review carry-a guard (``scaler.fit_as_of <= query.as_of``).

Run: ``pytest tests/orchestration/test_experience_seed.py -v``
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.data.errors import FutureDataRefused
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.market.factors import (
    DailyValueRow,
    FactorSummary,
    HeatState,
    MarketFactorDefinition,
    MarketFactorInputs,
    MarketFactorPoint,
    MarketFactorSetSpec,
    MarketFactorValue,
    RiskState,
    TrendState,
    UpDownRow,
    assemble_market_factor_report,
    build_market_factor_set_v1,
)
from guanlan_v2.orchestration.memory.experience import (
    CASE_AVAILABLE_UTC,
    QUERY_AS_OF_UTC,
    SEED_LINK,
    CaseMatured,
    CaseReviewed,
    CaseView,
    ExperienceLog,
    ExperienceQuery,
    ExperienceScalerSnapshot,
    RealizedRegime,
    RegimeCase,
    RegimeGraderSpec,
    SeedReport,
    build_observed_calendar,
    fit_scaler,
    fold_case_views,
    retrieve_neighbours,
    seed_experience_from_history,
    seed_judgment_proxy,
)
from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry

UTC = timezone.utc
DH = "ab" * 32
FSV = "mfs-v1"
FIXTURE_SEED = 20260718  # the fixed random-walk seed constant (part of the fixture identity)


# --------------------------------------------------------------------------- #
# clock + env harness (mirrors the Task 4/5/6 store harness)                    #
# --------------------------------------------------------------------------- #
class SteppingClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


def _experience_registry() -> Phase2RuntimeRegistry:
    reg = Phase2RuntimeRegistry()
    for model in (RegimeCase, RealizedRegime, CaseMatured, CaseReviewed):
        reg.register(model)
    reg.seal()
    return reg


class _Env:
    def __init__(self, clock_start: datetime | None = None) -> None:
        self.resolver = SchemaRegistryResolver()
        self.registry = _experience_registry()
        self.digest = self.resolver.register(self.registry)
        self.clock = SteppingClock(clock_start or datetime(2030, 1, 1, 1, 0, tzinfo=UTC))
        self.stores = RuntimeStores(resolver=self.resolver, clock=self.clock)

    def log(self) -> ExperienceLog:
        return ExperienceLog(
            event_store=self.stores.events,
            payload_store=self.stores.payloads,
            registry=self.registry,
            clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work,
        )


# --------------------------------------------------------------------------- #
# time helpers (must mirror the seed conventions)                               #
# --------------------------------------------------------------------------- #
def _hhmm(tag: str) -> tuple[int, int]:
    h, m = tag.split(":")
    return int(h), int(m)


def _at(day_iso: str, tag: str) -> datetime:
    d = date.fromisoformat(day_iso)
    h, m = _hhmm(tag)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def _avail(day_iso: str) -> datetime:
    return _at(day_iso, "07:05")


def _weekdays(start_iso: str, n: int) -> tuple[str, ...]:
    d = date.fromisoformat(start_iso)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return tuple(out)


# --------------------------------------------------------------------------- #
# deterministic synthetic-history fixture (tmp-injected, no live data)          #
# --------------------------------------------------------------------------- #
def _synthetic_inputs(dates: tuple[str, ...], *, seed: int = FIXTURE_SEED) -> MarketFactorInputs:
    """A deterministic pseudo-random-walk over ``dates`` for the three series the
    seeder needs (updown / closes_index / astock_temp). Availability is the
    reviewed 07:05Z daily close (so a bar for date D is known only at D's close)."""
    rng = random.Random(seed)
    updown: list[UpDownRow] = []
    closes: list[DailyValueRow] = []
    temp: list[DailyValueRow] = []
    total = 4000
    up = 2000.0
    t = 60.0
    for day in dates:
        up += rng.uniform(-260.0, 260.0)
        up = max(200.0, min(float(total - 200), up))
        up_i = int(round(up))
        updown.append(UpDownRow(date=day, up=up_i, down=total - up_i, total=total, available_at=_avail(day)))
        closes.append(DailyValueRow(date=day, value=rng.uniform(-0.02, 0.02), available_at=_avail(day)))
        t += rng.uniform(-6.0, 6.0)
        t = max(20.0, min(98.0, t))
        temp.append(DailyValueRow(date=day, value=t, available_at=_avail(day)))
    return MarketFactorInputs(
        updown=tuple(updown), closes_index=tuple(closes), astock_temp=tuple(temp)
    )


def _bench_from(inputs: MarketFactorInputs) -> tuple[DailyValueRow, ...]:
    return tuple(inputs.closes_index or ())


def _grader() -> RegimeGraderSpec:
    return RegimeGraderSpec.build(
        grader_version="regime-grader-v1", horizon_trading_days=20, benchmark_id="eqw_all_a",
        bull_min_return=0.03, bear_max_return=-0.03, risk_off_max_drawdown=-0.05,
        risk_on_min_return=0.02, risk_on_min_drawdown=-0.03,
    )


def _seed(
    *, env: _Env, inputs: MarketFactorInputs, calendar, start: str, end: str, spec=None, grader=None
) -> SeedReport:
    return seed_experience_from_history(
        inputs=inputs, spec=spec or build_market_factor_set_v1(), grader=grader or _grader(),
        calendar=calendar, log=env.log(), start=start, end=end, clock=env.clock,
    )


# --------------------------------------------------------------------------- #
# seed-time conventions (versioned constants, part of the seed identity)        #
# --------------------------------------------------------------------------- #
def test_seed_time_conventions_are_the_versioned_constants():
    assert QUERY_AS_OF_UTC == "01:30"       # 09:30 Asia/Shanghai session open
    assert CASE_AVAILABLE_UTC == "07:05"    # 15:05 Asia/Shanghai session close
    assert SEED_LINK == "seed:lane0-replay-v1"


# --------------------------------------------------------------------------- #
# proxy judgment — controlled hand-built reports pin every rule branch          #
# --------------------------------------------------------------------------- #
_RULE_AUX = {
    "breadth.ad_ratio": ("ma20",),
    "breadth.divergence": (),
    "vol.rv": (),
    "temp.astock": (),
}
_FILLERS = (
    "breadth.nhnl", "breadth.limit_up_ema", "flow.northbound", "flow.main_pct",
    "rot.hhi", "rot.diffusion", "rot.dispersion", "val.pct", "breadth.break_rate",
    "breadth.ladder_height", "breadth.promotion_rate", "rot.ladder_theme",
    "rot.leader_persist", "rot.flow_streak", "rot.theme_burst",
)
PIN_AS_OF = datetime(2020, 6, 15, 1, 30, tzinfo=UTC)


def _present(fid: str, value: float, *, aux: dict | None = None) -> MarketFactorValue:
    fam = fid.split(".", 1)[0]
    pt = MarketFactorPoint(date="2020-06-12", value=value, aux=aux or {})
    return MarketFactorValue.build(
        factor_id=fid, definition_version="1", family=fam, value=value, params={},
        universe="all_a", effective_at=PIN_AS_OF, available_at=PIN_AS_OF, status="DEGRADED",
        coverage=0.5, missing_policy="test", series=(pt,), summary=FactorSummary(latest=value),
        n_days=1, first_date="2020-06-12", reason="short test series",
    )


def _unavail(fid: str) -> MarketFactorValue:
    fam = fid.split(".", 1)[0]
    return MarketFactorValue.build(
        factor_id=fid, definition_version="1", family=fam, value=None, params={},
        universe="all_a", effective_at=PIN_AS_OF, available_at=PIN_AS_OF, status="UNAVAILABLE",
        coverage=0.0, missing_policy="test", series=(), summary=None, n_days=0,
        first_date=None, reason="absent in test",
    )


def _pin_report(*, ad20=None, d=None, rv=None, temp=None, fillers=0):
    values: list[MarketFactorValue] = []
    aux_map: dict[str, tuple] = {}
    if ad20 is not None:
        values.append(_present("breadth.ad_ratio", 0.5, aux={"ma20": ad20}))
    else:
        values.append(_unavail("breadth.ad_ratio"))
    aux_map["breadth.ad_ratio"] = ("ma20",)
    for fid, val in (("breadth.divergence", d), ("vol.rv", rv), ("temp.astock", temp)):
        values.append(_present(fid, val) if val is not None else _unavail(fid))
        aux_map[fid] = ()
    for fid in _FILLERS[:fillers]:
        values.append(_unavail(fid))
        aux_map[fid] = ()
    defs = tuple(sorted(
        (MarketFactorDefinition(factor_id=f, definition_version="1", params={},
                                required_inputs=(), min_history_sessions=1, aux_keys=ak)
         for f, ak in aux_map.items()),
        key=lambda x: x.factor_id,
    ))
    spec = MarketFactorSetSpec.build(
        factor_set_version="pin", feature_schema_version="mfs-pin", universe="all_a",
        frequency="day", definitions=defs,
    )
    return assemble_market_factor_report(
        spec=spec, as_of=PIN_AS_OF, clock_mode="eod", universe_registry_version="ureg-test",
        values=tuple(values), data_snapshot_hash=DH,
    )


def test_proxy_trend_branches_bull_bear_range():
    # all four rule factors present ⇒ missing_share 0 ⇒ modal equals the picked label.
    bull = seed_judgment_proxy(_pin_report(ad20=0.15, d=0.0, rv=0.2, temp=50.0))
    assert bull.trend is TrendState.BULL
    bear = seed_judgment_proxy(_pin_report(ad20=-0.15, d=0.0, rv=0.2, temp=50.0))
    assert bear.trend is TrendState.BEAR
    rng = seed_judgment_proxy(_pin_report(ad20=0.0, d=0.0, rv=0.2, temp=50.0))
    assert rng.trend is TrendState.RANGE
    # boundary: exactly +0.10 is 牛, exactly -0.10 is 熊 (inclusive thresholds).
    assert seed_judgment_proxy(_pin_report(ad20=0.10, d=0.0, rv=0.2, temp=50.0)).trend is TrendState.BULL
    assert seed_judgment_proxy(_pin_report(ad20=-0.10, d=0.0, rv=0.2, temp=50.0)).trend is TrendState.BEAR


def test_proxy_risk_branches_riskoff_neutral():
    off = seed_judgment_proxy(_pin_report(ad20=0.0, d=1.5, rv=0.2, temp=50.0))
    assert off.risk_state is RiskState.RISK_OFF          # d >= 1.5
    neu = seed_judgment_proxy(_pin_report(ad20=0.0, d=1.49, rv=0.2, temp=50.0))
    assert neu.risk_state is RiskState.NEUTRAL           # just below


def test_proxy_heat_branches_overheat_normal():
    hot = seed_judgment_proxy(_pin_report(ad20=0.0, d=0.0, rv=0.2, temp=85.0))
    assert hot.heat_state is HeatState.OVERHEAT          # temp >= 85
    cool = seed_judgment_proxy(_pin_report(ad20=0.0, d=0.0, rv=0.2, temp=84.9))
    assert cool.heat_state is HeatState.NORMAL           # just below


def test_proxy_always_low_confidence_and_pit_bindings():
    report = _pin_report(ad20=0.15, d=2.0, rv=0.2, temp=90.0)
    j = seed_judgment_proxy(report)
    assert j.confidence is Confidence.LOW
    assert j.as_of == report.as_of
    assert j.factor_report_digest == report.content_digest
    assert j.conflicts == () and j.analog_case_ids == ()
    # every rule-referenced present factor is anchored; ids sorted + == drivers.
    assert j.evidence_factor_ids == ("breadth.ad_ratio", "breadth.divergence", "temp.astock", "vol.rv")
    assert j.drivers == j.evidence_factor_ids
    assert {a.factor_id for a in j.evidence} == set(j.evidence_factor_ids)
    # no attention (missing_share 0) ⇒ unknown_reason forbidden.
    assert j.unknown_reason is None


def test_proxy_unknown_mass_rescale_pinned():
    # 4 present rule factors + 12 unavailable ⇒ missing_share = 12/16 = 0.75 ⇒ U = 0.75.
    report = _pin_report(ad20=0.15, d=0.0, rv=0.2, temp=50.0, fillers=12)
    n = len(report.values)
    missing = len(report.missing_features)
    assert (n, missing) == (16, 12)
    j = seed_judgment_proxy(report)
    u = 0.75
    # trend: picked 牛 gets 0.6*(1-U); the other two non-unknown split 0.2*(1-U) each.
    assert j.trend_probabilities[TrendState.UNKNOWN] == pytest.approx(u)
    assert j.trend_probabilities[TrendState.BULL] == pytest.approx(0.6 * (1 - u))
    assert j.trend_probabilities[TrendState.BEAR] == pytest.approx(0.2 * (1 - u))
    assert j.trend_probabilities[TrendState.RANGE] == pytest.approx(0.2 * (1 - u))
    # each axis sums to 1.
    for probs in (j.trend_probabilities, j.risk_probabilities, j.heat_probabilities):
        assert sum(probs.values()) == pytest.approx(1.0)
    # U = 0.75 >= 0.25 attention ⇒ unknown_reason required; and 0.6*0.25=0.15 < 0.75
    # ⇒ modal is unknown on every axis ⇒ LOW.
    assert j.unknown_reason is not None
    assert j.trend is TrendState.UNKNOWN
    assert j.risk_state is RiskState.UNKNOWN
    assert j.heat_state is HeatState.UNKNOWN
    assert j.confidence is Confidence.LOW


def test_proxy_fully_missing_day_modal_unknown_low():
    # only temp present (for the anchor), everything else UNAVAILABLE ⇒ U caps at 0.9.
    report = _pin_report(ad20=None, d=None, rv=None, temp=50.0, fillers=15)
    j = seed_judgment_proxy(report)
    assert j.trend is TrendState.UNKNOWN
    assert j.risk_state is RiskState.UNKNOWN
    assert j.heat_state is HeatState.UNKNOWN
    assert j.confidence is Confidence.LOW
    assert j.trend_probabilities[TrendState.UNKNOWN] == pytest.approx(0.9)  # min(0.9, missing_share)
    # still a lawful ≥1-anchor report (the one present rule factor).
    assert j.evidence_factor_ids == ("temp.astock",)
    assert j.unknown_reason is not None


def test_proxy_is_deterministic():
    report = _pin_report(ad20=0.12, d=1.6, rv=0.3, temp=88.0, fillers=4)
    a = seed_judgment_proxy(report)
    b = seed_judgment_proxy(report)
    assert a.content_digest == b.content_digest


# --------------------------------------------------------------------------- #
# Task-5 review carry-a: retrieve_neighbours guards scaler.fit_as_of            #
# --------------------------------------------------------------------------- #
def test_retrieve_refuses_scaler_fitted_after_query_as_of():
    scaler = ExperienceScalerSnapshot.build(
        feature_schema_version=FSV, scaler_version="expanding-zscore-v1",
        fit_as_of=datetime(2026, 8, 1, 1, 30, tzinfo=UTC),  # AFTER the query vantage
        n_obs=3, mu={"breadth.ad_ratio": 0.0}, sd={"breadth.ad_ratio": 1.0}, degenerate_features=(),
    )
    query = ExperienceQuery.build(
        as_of=datetime(2026, 7, 1, 1, 30, tzinfo=UTC), feature_schema_version=FSV,
        features={"breadth.ad_ratio": 0.5}, k=5,
    )
    with pytest.raises(FutureDataRefused):
        retrieve_neighbours(query, views=[], scaler=scaler)


def test_retrieve_allows_scaler_fitted_at_query_as_of():
    at = datetime(2026, 7, 1, 1, 30, tzinfo=UTC)
    scaler = ExperienceScalerSnapshot.build(
        feature_schema_version=FSV, scaler_version="expanding-zscore-v1", fit_as_of=at,
        n_obs=0, mu={}, sd={}, degenerate_features=(),
    )
    query = ExperienceQuery.build(
        as_of=at, feature_schema_version=FSV, features={"breadth.ad_ratio": 0.5}, k=5,
    )
    sel = retrieve_neighbours(query, views=[], scaler=scaler)  # fit_as_of == as_of is legal
    assert sel.badges == ("cold_start:0_neighbours",)


# --------------------------------------------------------------------------- #
# SeedReport contract                                                           #
# --------------------------------------------------------------------------- #
def test_seed_report_fields_and_digest_roundtrip():
    r = SeedReport(
        start="2019-01-02", end="2019-12-31", cases_created=5, cases_skipped_existing=0,
        matured_appended=2, coverage_gap_dates=("2019-01-02",), feature_schema_version=FSV,
        scaler_digest_last=DH,
    )
    assert r.cases_created == 5 and r.scaler_digest_last == DH
    assert content_digest(r) == content_digest(r)  # stable


# --------------------------------------------------------------------------- #
# seeding walk — basic behavior (links marker, gap honesty, counts)             #
# --------------------------------------------------------------------------- #
def _standard_fixture(n: int = 120):
    dates = _weekdays("2019-01-02", n)
    inputs = _synthetic_inputs(dates)
    calendar = build_observed_calendar(dates)
    return dates, inputs, calendar


def test_seed_produces_cases_with_the_seed_link_marker():
    dates, inputs, calendar = _standard_fixture(90)
    env = _Env()
    report = _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[-1])
    assert report.cases_created > 0
    assert report.feature_schema_version == FSV
    log = env.log()
    views = fold_case_views(
        log.visible_case_events(), resolve_payload=log.resolve_payload,
        as_of=datetime(2035, 1, 1, tzinfo=UTC),
    )
    assert views, "the seeder must have created cases"
    for v in views:
        assert v.case.links == (SEED_LINK,)
        assert v.case.id.startswith(f"rc.{FSV}.")
        assert v.case.available_at == _avail(v.case.as_of.astimezone(UTC).date().isoformat())


def test_seed_coverage_gap_dates_are_honest_no_fabricated_cases():
    # warm-up: no factor is computable until ≥20 sessions of history ⇒ leading gaps.
    dates, inputs, calendar = _standard_fixture(90)
    env = _Env()
    report = _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[-1])
    assert report.coverage_gap_dates, "the warm-up window must appear as coverage gaps"
    # gap dates carry NO case, and created+skipped == non-gap sessions walked.
    walked = [d for d in dates if dates[0] <= d <= dates[-1]]
    assert report.cases_created + report.cases_skipped_existing == len(walked) - len(report.coverage_gap_dates)
    log = env.log()
    case_ids = set()
    for ev in log.visible_case_events():
        if ev.event_type is EventType.CASE_CREATED:
            from guanlan_v2.orchestration.refs import TypedPayloadRef
            c = log.resolve_payload(TypedPayloadRef(schema_ref=ev.payload_schema_ref, payload_ref=ev.payload_ref))
            case_ids.add(c.id)
    for gap in report.coverage_gap_dates:
        assert f"rc.{FSV}.{gap.replace('-', '')}" not in case_ids


def test_seed_leading_absent_month_is_a_coverage_gap():
    # inputs that begin only from session 30 ⇒ the first 30 sessions have no window.
    dates = _weekdays("2019-01-02", 80)
    late = _synthetic_inputs(dates[30:])
    calendar = build_observed_calendar(dates)
    env = _Env()
    report = _seed(env=env, inputs=late, calendar=calendar, start=dates[0], end=dates[-1])
    for absent in dates[:30]:
        assert absent in report.coverage_gap_dates


def test_seed_scaler_n_obs_reflects_the_gap():
    dates, inputs, calendar = _standard_fixture(90)
    env = _Env()
    report = _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[-1])
    log = env.log()
    views = fold_case_views(
        log.visible_case_events(), resolve_payload=log.resolve_payload,
        as_of=datetime(2035, 1, 1, tzinfo=UTC),
    )
    scaler = fit_scaler(views, as_of=datetime(2035, 1, 1, tzinfo=UTC), feature_schema_version=FSV)
    assert scaler.n_obs == report.cases_created  # only non-gap sessions became cases


# --------------------------------------------------------------------------- #
# invariant 2 — re-seeding the same range is a no-op                            #
# --------------------------------------------------------------------------- #
def test_reseed_same_range_is_a_noop():
    dates, inputs, calendar = _standard_fixture(90)
    env = _Env()
    first = _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[-1])
    assert first.cases_created > 0 and first.cases_skipped_existing == 0
    second = _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[-1])
    assert second.cases_created == 0
    assert second.cases_skipped_existing == first.cases_created  # every case already present


# --------------------------------------------------------------------------- #
# invariant 3 — a seeded case uses only ≤D data                                 #
# --------------------------------------------------------------------------- #
def _case_digest_for(env: _Env, day_iso: str) -> str:
    log = env.log()
    from guanlan_v2.orchestration.refs import TypedPayloadRef
    target = f"rc.{FSV}.{day_iso.replace('-', '')}"
    for ev in log.visible_case_events():
        if ev.event_type is EventType.CASE_CREATED:
            c = log.resolve_payload(TypedPayloadRef(schema_ref=ev.payload_schema_ref, payload_ref=ev.payload_ref))
            if c.id == target:
                return c.content_digest
    raise AssertionError(f"no seeded case for {day_iso}")


def test_seed_case_uses_only_data_up_to_D():
    dates = _weekdays("2019-01-02", 80)
    inputs = _synthetic_inputs(dates)
    calendar = build_observed_calendar(dates)
    d_iso = dates[40]

    env_full = _Env()
    _seed(env=env_full, inputs=inputs, calendar=calendar, start=dates[0], end=d_iso)

    # truncate every series to dates <= D (removing everything strictly after D).
    # case D's judgment only sees data windowed to D's 01:30 open (≤ D-1's close),
    # so removing post-D rows must leave case D byte-identical.
    def _trunc(seq):
        return tuple(r for r in (seq or ()) if r.date <= d_iso) or None
    truncated = MarketFactorInputs(
        updown=_trunc(inputs.updown), closes_index=_trunc(inputs.closes_index),
        astock_temp=_trunc(inputs.astock_temp),
    )
    # the calendar IS the benchmark trade-date list — truncated in lockstep.
    trunc_cal = build_observed_calendar(tuple(r.date for r in (truncated.closes_index or ())))
    env_trunc = _Env()
    _seed(env=env_trunc, inputs=truncated, calendar=trunc_cal, start=dates[0], end=d_iso)

    assert _case_digest_for(env_full, d_iso) == _case_digest_for(env_trunc, d_iso)


# --------------------------------------------------------------------------- #
# invariant 4 — batch wall-clock independence                                   #
# --------------------------------------------------------------------------- #
def test_seed_two_clocks_yield_byte_identical_payload_digests():
    dates = _weekdays("2019-01-02", 120)
    inputs = _synthetic_inputs(dates)
    calendar = build_observed_calendar(dates)

    def _run(clock_start: datetime):
        env = _Env(clock_start=clock_start)
        _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[-1])
        log = env.log()
        return fold_case_views(
            log.visible_case_events(), resolve_payload=log.resolve_payload,
            as_of=datetime(2035, 1, 1, tzinfo=UTC),
        )

    views_a = _run(datetime(2030, 1, 1, 3, 0, tzinfo=UTC))
    views_b = _run(datetime(2033, 6, 15, 9, 0, tzinfo=UTC))
    assert len(views_a) == len(views_b) and views_a
    # case + realized (folded view) digests are byte-identical; only the CaseMatured
    # envelope's matured_at audit fact differs (never in the folded view).
    for va, vb in zip(views_a, views_b):
        assert va.case.content_digest == vb.case.content_digest
        assert va.semantic_digest() == vb.semantic_digest()
        if va.realized is not None:
            assert vb.realized is not None
            assert va.realized.content_digest == vb.realized.content_digest
    assert any(v.state == "matured" for v in views_a), "the fixture must mature some cases"


# --------------------------------------------------------------------------- #
# invariant 1 — future-case invariance (the acceptance heart)                    #
# --------------------------------------------------------------------------- #
def _probe_triple(log: ExperienceLog, *, q_as_of: datetime, query: ExperienceQuery):
    views = fold_case_views(log.visible_case_events(), resolve_payload=log.resolve_payload, as_of=q_as_of)
    scaler = fit_scaler(views, as_of=q_as_of, feature_schema_version=FSV)
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    return content_digest(tuple(views)), scaler.content_digest, sel.content_digest


def test_future_case_invariance_byte_identical_triple():
    dates = _weekdays("2019-01-02", 260)   # a synthetic multi-"year" span
    inputs = _synthetic_inputs(dates)
    calendar = build_observed_calendar(dates)
    env = _Env()

    # seed [year A .. year B] (through session 179).
    _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[179])

    # a probe date Q strictly earlier than the later-seeded region.
    q_as_of = _avail(dates[135])
    query = ExperienceQuery.build(
        as_of=q_as_of, feature_schema_version=FSV,
        features={"breadth.ad_ratio": 0.0, "vol.rv": 0.01, "temp.astock": 60.0}, k=5,
    )
    before = _probe_triple(env.log(), q_as_of=q_as_of, query=query)
    assert before[2]  # a real selection digest

    # seed the additional later year into the SAME store (extend to session 259).
    extra = _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[259])
    assert extra.cases_created > 0                        # genuinely added later cases
    assert extra.cases_skipped_existing > 0               # and re-skipped the earlier ones
    assert len(env.log().visible_case_events()) > 0

    after = _probe_triple(env.log(), q_as_of=q_as_of, query=query)
    assert after == before, "future seeded cases must be invisible at an earlier as_of"


def test_future_case_invariance_bisectable_by_probe_date():
    # invariance holds at several probe vantages, not only one.
    dates = _weekdays("2019-01-02", 240)
    inputs = _synthetic_inputs(dates)
    calendar = build_observed_calendar(dates)
    env = _Env()
    _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[159])

    probes = [dates[80], dates[110], dates[140]]
    query_of = lambda q: ExperienceQuery.build(
        as_of=_avail(q), feature_schema_version=FSV,
        features={"breadth.ad_ratio": 0.0, "temp.astock": 55.0}, k=5,
    )
    before = {q: _probe_triple(env.log(), q_as_of=_avail(q), query=query_of(q)) for q in probes}

    _seed(env=env, inputs=inputs, calendar=calendar, start=dates[0], end=dates[239])
    after = {q: _probe_triple(env.log(), q_as_of=_avail(q), query=query_of(q)) for q in probes}
    assert after == before


# --------------------------------------------------------------------------- #
# invariant 5 — seeded and live cases are indistinguishable except `links`        #
# --------------------------------------------------------------------------- #
def _judgment_for(as_of: datetime):
    # a minimal lawful RegimeReport via the proxy over a one-factor report.
    return seed_judgment_proxy(_pin_report_at(as_of, temp=60.0))


def _pin_report_at(as_of: datetime, *, temp: float):
    val = _present_at("temp.astock", temp, as_of)
    spec = MarketFactorSetSpec.build(
        factor_set_version="pin", feature_schema_version=FSV, universe="all_a", frequency="day",
        definitions=(MarketFactorDefinition(
            factor_id="temp.astock", definition_version="1", params={}, required_inputs=(),
            min_history_sessions=1, aux_keys=()),),
    )
    return assemble_market_factor_report(
        spec=spec, as_of=as_of, clock_mode="eod", universe_registry_version="ureg-test",
        values=(val,), data_snapshot_hash=DH,
    )


def _present_at(fid: str, value: float, as_of: datetime) -> MarketFactorValue:
    d_iso = as_of.astimezone(UTC).date().isoformat()
    pt = MarketFactorPoint(date=d_iso, value=value, aux={})
    return MarketFactorValue.build(
        factor_id=fid, definition_version="1", family=fid.split(".", 1)[0], value=value,
        params={}, universe="all_a", effective_at=as_of, available_at=as_of, status="DEGRADED",
        coverage=0.5, missing_policy="test", series=(pt,), summary=FactorSummary(latest=value),
        n_days=1, first_date=d_iso, reason="short test series",
    )


def test_seeded_and_live_cases_indistinguishable_to_retrieval_except_links():
    as_of = datetime(2020, 6, 15, 1, 30, tzinfo=UTC)
    avail = datetime(2020, 6, 15, 7, 5, tzinfo=UTC)
    judgment = _judgment_for(as_of)

    def _mk(case_id: str, links: tuple[str, ...]) -> RegimeCase:
        return RegimeCase.build(
            id=case_id, as_of=as_of, available_at=avail, feature_schema_version=FSV,
            scaler_digest=DH, features={"breadth.ad_ratio": 0.5}, feature_coverage={"breadth.ad_ratio": 1.0},
            missing_features=(), judgment=judgment, links=links,
        )

    seeded = _mk("rc.mfs-v1.20200615", (SEED_LINK,))
    live = _mk("rc.mfs-v1.live", ())
    scaler = ExperienceScalerSnapshot.build(
        feature_schema_version=FSV, scaler_version="expanding-zscore-v1",
        fit_as_of=datetime(2020, 6, 16, 1, 30, tzinfo=UTC), n_obs=2,
        mu={"breadth.ad_ratio": 0.5}, sd={"breadth.ad_ratio": 0.1}, degenerate_features=(),
    )
    query = ExperienceQuery.build(
        as_of=datetime(2020, 6, 16, 1, 30, tzinfo=UTC), feature_schema_version=FSV,
        features={"breadth.ad_ratio": 0.5}, k=5,
    )
    views = [CaseView(case=seeded, state="pending"), CaseView(case=live, state="pending")]
    sel = retrieve_neighbours(query, views=views, scaler=scaler)
    by_id = {n.case_id: n for n in sel.neighbours}
    assert set(by_id) == {"rc.mfs-v1.20200615", "rc.mfs-v1.live"}
    # identical features ⇒ identical distance; retrieval never reads links.
    assert by_id["rc.mfs-v1.20200615"].distance == pytest.approx(by_id["rc.mfs-v1.live"].distance)
    # the ONLY difference between the two cases is the links marker.
    assert seeded.links == (SEED_LINK,) and live.links == ()
    seeded_fields = seeded.model_dump(exclude={"links", "id", "content_digest"})
    live_fields = live.model_dump(exclude={"links", "id", "content_digest"})
    assert seeded_fields == live_fields
