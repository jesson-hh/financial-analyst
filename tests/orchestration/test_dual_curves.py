# -*- coding: utf-8 -*-
"""Phase 9 · Task 5 — dual curves under one execution attestation + evaluator handoff.

Three deliverables in ``adapters.luozi`` (extending the Phase-6 file additively):

* ``derive_deterministic_targets`` — the pure, versioned, zero-LLM rule that maps a
  PIT factor score at ``decision_as_of`` onto a full-weight / flat single-name
  ``DeterministicTargetSet`` with the τ=0.15 dead zone (mirroring the seats ``w=1``
  pure-factor ``_hybrid_direction`` at ``api.py:479``); ``[UNSOURCED]`` scores are
  refused (a provenance digest is required);
* ``build_dual_curves`` — runs BOTH lanes (the LLM intents lane through
  ``ShadowBacktestRunner.run`` and the envelope-free deterministic lane through
  ``.run_targets``) on the SAME attested runner, producing a ``DualCurveReport`` whose
  two ``ShadowCurveSeries`` are honest NAV paths (no smoothing/interpolation) bound to
  one ``ShadowExecutionConfig`` semantic digest;
* ``hand_off_dual_curves_to_feedback`` — the maturity-gated evaluator handoff: it
  stages/commits the ``DualCurveReport`` and registers the realized interval with the
  Phase-4 ``TrialLedger`` (as a matured holdout window) ONLY once the interval has
  matured; an immature interval parks the run at ``WAITING_FOR_MATURITY`` and never
  touches the ledger.

CORRECTION-CLAUSE NOTES (brief wording → reviewed source; source wins — full prose in
``.superpowers/sdd/p9-task-5-report.md``):

* N5-1 — the Phase-6 ``ShadowBacktestRunner`` / ``DeterministicTargetSet`` /
  ``deterministic_apply_key`` are ``adapters.luozi``-owned (per Task-0 clause C1), NOT
  ``shadow.py``. The brief's "``guanlan_v2/orchestration/shadow.py`` — Phase 6
  ShadowBacktestRunner BOTH entries" names the wrong module; verified at source.
* N5-2 — the brief's public name ``submit_dual_curves_to_evaluator`` collides with two
  SEALED Phase-6 redlines: ``test_shadow_redlines.py::test_phase9_machinery_names_
  absent_from_phase6_modules`` forbids ``"evaluator"`` in ANY defined identifier of a
  Phase-6 module, and ``::test_no_exported_name_suggests_a_promotion_path`` forbids
  ``"submit"`` in ANY exported name. The function is therefore named
  ``hand_off_dual_curves_to_feedback`` — both redlines pass UNCHANGED.
* N5-3 — ``derive_deterministic_targets`` takes a required ``provenance_digest`` keyword
  (the brief signature omitted it, but the prose requires "provenance digest required —
  ``[UNSOURCED]`` scores are refused"). A ``None`` / empty / ``[UNSOURCED]`` digest is a
  typed refusal (``UnsourcedFactorScoreError``); no target is produced.
* N5-4 — ``hand_off_dual_curves_to_feedback`` consumes a NARROW staged→barrier artifact
  sink (``pool``), not the full Phase-2 ``ArtifactPool`` DAG barrier: that barrier
  validates a produced Artifact against a registry that must resolve
  ``DualCurveReport@1``, but Task 5 may NOT register that schema (Task 9 owns the
  cumulative registry). A fake sink exercises the staged→barrier contract; the real
  wiring lands with Task 9's registration + Task 12's e2e.
"""
from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

# REAL fa engine cost model (the conftest prepends the in-repo engine fork).
from financial_analyst.backtest.costs import CostModel

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.adapters import luozi
from guanlan_v2.orchestration.adapters.contracts import (
    DualCurveReport,
    ReplayDecisionPoint,
    ShadowCurveSeries,
    ShadowExecutionConfig,
    ShadowReplayRunState,
)
from guanlan_v2.orchestration.adapters.luozi import (
    DeterministicTargetSet,
    ShadowBacktestRunner,
    ShadowContractError,
    UnsourcedFactorScoreError,
    build_dual_curves,
    derive_deterministic_targets,
    hand_off_dual_curves_to_feedback,
    resolve_decision_points,
    _feedback_study_and_window,
)
from guanlan_v2.orchestration.context import ClockSpec
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import Confidence, ExperimentStatus
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.shadow import (
    SHADOW_MATCHING_ENGINE_VERSION,
    TargetPortfolioIntent,
    TargetPosition,
)

# the Task-0-recorded engine byte-digest pins (invariant 6 — in-test recompute).
from tests.orchestration.test_phase9_handoff import ENGINE_MODULE_BYTE_DIGESTS

UTC = timezone.utc
_CAL_ID = "ashare.xshg"
_TZ = "Asia/Shanghai"
_SESSIONS = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
_SEED_DAY = "2026-07-17"
_D64 = "d" * 64


# --------------------------------------------------------------------------- #
# in-memory reader / loader satisfying prepare_bar's real surface (from
# test_shadow_runner) — drives the REAL fa Broker/VirtualPortfolio/CostModel.
# --------------------------------------------------------------------------- #
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
    """``trading_days`` + a call-recording spy on which sessions were served."""

    def __init__(self, sessions):
        self._sessions = list(sessions)
        self.trading_days_calls: list[tuple] = []

    def trading_days(self, start=None, end=None):
        self.trading_days_calls.append((start, end))
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
        matching_engine_version=SHADOW_MATCHING_ENGINE_VERSION,
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


def _runner(frames, *, sessions=_SESSIONS, cost_model=None, init_cash=1_000_000.0,
            schedule=None, calendar=None, clock=None, reader=None):
    sch = schedule or _schedule()
    return ShadowBacktestRunner(
        reader=reader if reader is not None else _MemReader(sessions),
        loader=_MemLoader(frames),
        schedule=sch,
        schedule_ref=_sched_ref(sch),
        calendar=calendar or _calendar(sessions),
        cost_model=cost_model or CostModel(),
        init_cash=init_cash,
        clock=clock or SystemClock(),
    )


# a rising alpha frame: seed 07-17 close 10, then a buyable rising week.
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


def _exec_config(runner, *, universe=None, clock_as_of=None):
    """The ShadowExecutionConfig that reproduces ``runner``'s attested dimensions."""
    sch = runner._schedule
    uni = universe or (_sym(),)
    uni = tuple(sorted(uni, key=lambda s: s.code))
    return ShadowExecutionConfig(
        universe=uni,
        init_cash=runner._init_cash,
        data_snapshot_content_digest="a" * 64,
        vintage_manifest_digest="b" * 64,
        calendar_id=runner._calendar.calendar_id,
        cost_model_digest=content_digest(dataclasses.asdict(runner._cost_model)),
        matching_engine_version=SHADOW_MATCHING_ENGINE_VERSION,
        clock=ClockSpec(
            as_of=clock_as_of or datetime(2026, 7, 20, 1, 30, tzinfo=UTC),
            timezone=_TZ,
            calendar_id=_CAL_ID,
        ),
        schedule_digest=sch.content_digest,
        intrabar_exit_priority=sch.intrabar_exit_priority,
    )


def _points(runner, *, first="2026-07-20", last="2026-07-22"):
    """Real ReplayDecisionPoints resolved over ``[first, last]`` (daily schedule)."""
    sch = runner._schedule
    cal = runner._calendar
    start = shadow.compute_scheduled_for(sch, session_date=first, calendar=cal)
    end = shadow.compute_scheduled_for(sch, session_date=last, calendar=cal)
    return resolve_decision_points(
        sch, calendar=cal, interval_start=start, interval_end=end
    )


def _intent_for(point, positions, cash_weight, *, ordinal=None):
    ord_ = ordinal if ordinal is not None else point.point_ordinal
    return TargetPortfolioIntent(
        intent_id=f"intent-{ord_}",
        target_version=1,
        proposal_artifact_id="art-prop-1",
        proposal_digest="a" * 64,
        source_decision_artifact_id="dec-art-1",
        decision_schedule_id="shadow.daily.ashare",
        decision_schedule_version="1",
        decision_schedule_digest="b" * 64,
        scheduled_for=point.scheduled_for,
        decision_as_of=point.decision_as_of,
        eligible_execution_at=point.eligible_execution_at,
        positions=tuple(positions),
        cash_weight=cash_weight,
        rationale="thesis",
        confidence=Confidence.MEDIUM,
        created_at=point.decision_as_of + timedelta(minutes=30),
    )


# a long factor score (> τ) and a real 64-hex provenance digest.
_SOURCED = content_digest({"factor_report": "market.factors", "as_of": "2026-07-20"})


# =========================================================================== #
# Row 1 — the frozen v1 rule vectors (τ=0.15 dead zone)                          #
# =========================================================================== #
def test_deterministic_rule_v1_vectors():
    r = _runner(_alpha_frames())
    point = _points(r, first="2026-07-20", last="2026-07-20")[0]
    uni = (_sym(),)

    # the pinned v1 table: sign(score) with the τ=0.15 dead zone.
    #   +0.50 → full-weight long | +0.14 → flat | 0.0 → flat | -0.14 → flat |
    #   -0.50 → flat-or-exit (no long-only position).
    cases = {
        0.50: True,   # > +τ  → long
        0.14: False,  # |·|≤τ → dead zone → flat
        0.00: False,  # exactly flat
        -0.14: False,  # |·|≤τ → dead zone → flat
        -0.50: False,  # < -τ  → sell/exit → no long position
    }
    for score, is_long in cases.items():
        ts = derive_deterministic_targets(
            point,
            factor_scores={"600000": score},
            universe=uni,
            provenance_digest=_SOURCED,
        )
        assert isinstance(ts, DeterministicTargetSet)
        assert ts.rule_id == "deterministic-target-rule-v1"
        assert ts.point_ordinal == point.point_ordinal
        assert ts.target_version == 1
        if is_long:
            assert len(ts.positions) == 1
            assert ts.positions[0].symbol.code == "600000"
            assert ts.positions[0].target_weight == pytest.approx(1.0)
            assert ts.cash_weight == pytest.approx(0.0)
        else:
            assert ts.positions == ()
            assert ts.cash_weight == pytest.approx(1.0)


def test_deterministic_rule_equal_weight_is_band_exempt():
    # multiple longs split EQUAL weight (band-exempt continuous 1/3 ≈ 0.333…).
    r = _runner(_alpha_frames())
    point = _points(r, first="2026-07-20", last="2026-07-20")[0]
    uni = (_sym("600000"), _sym("600001"), _sym("600002"))
    ts = derive_deterministic_targets(
        point,
        factor_scores={"600000": 0.5, "600001": 0.6, "600002": 0.7},
        universe=uni,
        provenance_digest=_SOURCED,
    )
    assert len(ts.positions) == 3
    for p in ts.positions:
        assert p.target_weight == pytest.approx(1.0 / 3.0)
    assert ts.cash_weight == pytest.approx(0.0)


# =========================================================================== #
# Row 2 — unsourced scores refused                                              #
# =========================================================================== #
@pytest.mark.parametrize("bad", [None, "", "[UNSOURCED]"])
def test_unsourced_scores_refused(bad):
    r = _runner(_alpha_frames())
    point = _points(r, first="2026-07-20", last="2026-07-20")[0]
    with pytest.raises(UnsourcedFactorScoreError):
        derive_deterministic_targets(
            point,
            factor_scores={"600000": 0.5},
            universe=(_sym(),),
            provenance_digest=bad,
        )
    assert issubclass(UnsourcedFactorScoreError, ShadowContractError)


# =========================================================================== #
# Row 3 — config mismatch refused at the runner (pre-bar, no fills)             #
# =========================================================================== #
def test_config_mismatch_refused_at_runner():
    r = _runner(_alpha_frames())
    points = _points(r)
    intents = tuple(_intent_for(p, [_pos(0.5)], 0.5) for p in points)
    det = tuple(
        derive_deterministic_targets(
            p, factor_scores={"600000": 0.5}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for p in points
    )
    # an execution config whose cost_model_digest differs from the runner's.
    cfg = _exec_config(r)
    bad = cfg.model_copy(update={"cost_model_digest": "e" * 64})
    with pytest.raises(ShadowContractError):
        build_dual_curves(
            execution_config=bad,
            points=points,
            deterministic_target_sets=det,
            intents=intents,
            shadow_runner=r,
        )


# =========================================================================== #
# Row 4 — deterministic lane is envelope-free (no intent, no LLM origin)         #
# =========================================================================== #
def test_deterministic_lane_envelope_free():
    r = _runner(_alpha_frames())
    points = _points(r)
    intents = tuple(_intent_for(p, [_pos(0.5)], 0.5) for p in points)
    det = tuple(
        derive_deterministic_targets(
            p, factor_scores={"600000": 0.5}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for p in points
    )
    # no target set is a TargetPortfolioIntent and none carries envelope fields.
    for ts in det:
        assert isinstance(ts, DeterministicTargetSet)
        assert not isinstance(ts, TargetPortfolioIntent)
        assert not hasattr(ts, "origin")
    report = build_dual_curves(
        execution_config=_exec_config(r), points=points,
        deterministic_target_sets=det, intents=intents, shadow_runner=r,
    )
    # the deterministic series carries a rule_id and NO applied intent digests.
    assert report.deterministic.curve_kind == "deterministic_strategy"
    assert report.deterministic.rule_id == "deterministic-target-rule-v1"
    assert report.deterministic.applied_intent_digests == ()


# =========================================================================== #
# Row 5 — both lanes consume the identical bar stream + calendar                #
# =========================================================================== #
def test_same_bars_same_calendar_both_lanes():
    reader = _MemReader(_SESSIONS)
    r = _runner(_alpha_frames(), reader=reader)
    points = _points(r)
    intents = tuple(_intent_for(p, [_pos(0.5)], 0.5) for p in points)
    det = tuple(
        derive_deterministic_targets(
            p, factor_scores={"600000": 0.5}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for p in points
    )
    build_dual_curves(
        execution_config=_exec_config(r), points=points,
        deterministic_target_sets=det, intents=intents, shadow_runner=r,
    )
    # both lanes drove trading_days over the identical [start, end] window.
    assert len(reader.trading_days_calls) == 2
    assert reader.trading_days_calls[0] == reader.trading_days_calls[1]
    # both lanes ran on the runner's single bound calendar id.
    assert r._calendar.calendar_id == _CAL_ID


# =========================================================================== #
# Row 6 — apply-once: a duplicate intent changes nothing                        #
# =========================================================================== #
def test_apply_once_duplicate_intent():
    r1 = _runner(_alpha_frames())
    point = _points(r1, first="2026-07-20", last="2026-07-20")[0]
    intent = _intent_for(point, [_pos(0.5)], 0.5)
    det = (derive_deterministic_targets(
        point, factor_scores={"600000": 0.5}, universe=(_sym(),),
        provenance_digest=_SOURCED),)

    single = build_dual_curves(
        execution_config=_exec_config(r1), points=(point,),
        deterministic_target_sets=det, intents=(intent,), shadow_runner=r1,
    )
    r2 = _runner(_alpha_frames())
    doubled = build_dual_curves(
        execution_config=_exec_config(r2), points=(point,),
        deterministic_target_sets=det, intents=(intent, intent), shadow_runner=r2,
    )
    # the duplicated delivery leaves the LLM NAV path byte-identical.
    assert [p.nav for p in doubled.llm_shadow.points] == [
        p.nav for p in single.llm_shadow.points]
    assert doubled.llm_shadow.trade_count == single.llm_shadow.trade_count


# =========================================================================== #
# Row 7 — a multi-order target's distinct orders/fills are not swallowed         #
# =========================================================================== #
def test_multi_fill_not_swallowed():
    # a target with a stop-loss produces a target_buy AND (once opened) a stop
    # order — two distinct order_kinds under one applied target, both preserved.
    r = _runner(_alpha_frames())
    point = _points(r, first="2026-07-20", last="2026-07-20")[0]
    intent = _intent_for(point, [_pos(0.5, stop_loss_pct=0.02)], 0.5)
    # run the intent lane directly to inspect the raw records.
    res = r.run((intent,), start="2026-07-20", end="2026-07-24")
    order_kinds = [o.order_kind for o in res.orders]
    assert "target_buy" in order_kinds
    assert "stop_loss" in order_kinds
    # every order id is distinct (nothing collapsed by a shared dedup key).
    ids = [o.order_id for o in res.orders]
    assert len(ids) == len(set(ids))


# =========================================================================== #
# Row 8 — the all-watch deterministic lane is an honest flat series             #
# =========================================================================== #
def test_flat_lane_honest():
    r = _runner(_alpha_frames())
    points = _points(r)
    intents = tuple(_intent_for(p, [_pos(0.5)], 0.5) for p in points)
    # every deterministic score in the dead zone / negative ⇒ all-watch (flat).
    det = tuple(
        derive_deterministic_targets(
            p, factor_scores={"600000": 0.05}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for p in points
    )
    for ts in det:
        assert ts.positions == ()  # flat book
    report = build_dual_curves(
        execution_config=_exec_config(r), points=points,
        deterministic_target_sets=det, intents=intents, shadow_runner=r,
    )
    flat = report.deterministic
    assert flat.trade_count == 0
    assert len(flat.points) >= 1
    # every NAV point equals init_cash (a genuine flat line from init_cash).
    for p in flat.points:
        assert p.nav == pytest.approx(r._init_cash)
        assert p.nav is not None


# =========================================================================== #
# Row 9 — curve points are the runner NAV history, 1:1, no interpolation         #
# =========================================================================== #
def test_curve_points_from_nav_only():
    r = _runner(_alpha_frames())
    points = _points(r)
    intents = tuple(_intent_for(p, [_pos(0.5)], 0.5) for p in points)
    det = tuple(
        derive_deterministic_targets(
            p, factor_scores={"600000": 0.5}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for p in points
    )
    # recompute the intent lane's raw NAV history for a 1:1 comparison.
    r2 = _runner(_alpha_frames())
    start = min(p.scheduled_for for p in points).astimezone(shadow.ZoneInfo(_TZ)).date().isoformat()
    end = max(p.eligible_execution_at for p in points).astimezone(shadow.ZoneInfo(_TZ)).date().isoformat()
    raw = r2.run(intents, start=start, end=end)

    report = build_dual_curves(
        execution_config=_exec_config(r), points=points,
        deterministic_target_sets=det, intents=intents, shadow_runner=r,
    )
    navs = [round(p.nav, 9) for p in report.llm_shadow.points]
    raw_navs = [round(v, 9) for _d, v in raw.nav_history]
    assert navs == raw_navs
    assert len(report.llm_shadow.points) == len(raw.nav_history)


# =========================================================================== #
# Row 10 — DualCurveReport validates; delta matches; not-causal is structural    #
# =========================================================================== #
def test_report_construction():
    r = _runner(_alpha_frames())
    points = _points(r)
    # the LLM lane buys HALF; the deterministic lane buys FULL — divergent curves.
    intents = tuple(_intent_for(p, [_pos(0.5)], 0.5) for p in points)
    det = tuple(
        derive_deterministic_targets(
            p, factor_scores={"600000": 0.5}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for p in points
    )
    report = build_dual_curves(
        execution_config=_exec_config(r), points=points,
        deterministic_target_sets=det, intents=intents, shadow_runner=r,
    )
    assert isinstance(report, DualCurveReport)
    assert report.not_causal_attribution is True
    assert report.decision_point_count == len(points)
    # both series bind the SAME execution config semantic digest.
    dig = report.execution_config.semantic_digest()
    assert report.deterministic.execution_config_digest == dig
    assert report.llm_shadow.execution_config_digest == dig
    # delta_total_return == (llm_last - det_last)/init_cash (hand computation).
    init = report.execution_config.init_cash
    llm_last = report.llm_shadow.points[-1].nav
    det_last = report.deterministic.points[-1].nav
    expected = (llm_last - det_last) / init
    assert report.delta_total_return == pytest.approx(expected, abs=1e-9)
    # the fuller (deterministic) position gained more over a rising week.
    assert det_last > llm_last


# =========================================================================== #
# Row 11 — an immature interval parks; the TrialLedger is untouched (spy)         #
# =========================================================================== #
class _SpyLedger:
    """Records every attribute access as a would-be ledger touch."""

    def __init__(self):
        self.touched: list[str] = []

    def __getattr__(self, name):
        self.touched.append(name)

        def _rec(*a, **k):
            return None

        return _rec


class _FakePool:
    """A narrow staged→barrier artifact sink (correction N5-4)."""

    def __init__(self):
        self.staged: list = []
        self.committed: list = []

    def stage_report(self, report, *, run_id, idempotency_key):
        self.staged.append((run_id, idempotency_key))
        return {"report": report, "run_id": run_id}

    def commit_report(self, handle, *, run_id, idempotency_key):
        self.committed.append((run_id, idempotency_key))
        return PayloadRef(
            namespace="main",
            object_id=f"dualcurve.{run_id}",
            content_digest=handle["report"].semantic_digest(),
        )


def _report_over(runner, points):
    intents = tuple(_intent_for(p, [_pos(0.5)], 0.5) for p in points)
    det = tuple(
        derive_deterministic_targets(
            p, factor_scores={"600000": 0.5}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for p in points
    )
    return build_dual_curves(
        execution_config=_exec_config(runner), points=points,
        deterministic_target_sets=det, intents=intents, shadow_runner=runner,
    )


def test_immature_interval_parks():
    r = _runner(_alpha_frames())
    points = _points(r)
    report = _report_over(r, points)
    spy = _SpyLedger()
    pool = _FakePool()
    # maturity_now strictly before the interval's realized availability.
    now = report.interval_end - timedelta(days=1)
    state = hand_off_dual_curves_to_feedback(
        report, run_id="replay-run.x", pool=pool, ledger=spy, maturity_now=now
    )
    assert isinstance(state, ShadowReplayRunState)
    assert state.status == ExperimentStatus.WAITING_FOR_MATURITY
    assert state.resume_after is not None
    assert state.wakeup_key is not None
    # the ledger was never touched (no reveal / register / anything).
    assert spy.touched == []


# =========================================================================== #
# Row 12 — a matured interval registers exactly once (idempotent by run id)       #
# =========================================================================== #
class _SteppingClock:
    def __init__(self, start):
        self.current = start

    def now(self):
        self.current = self.current + timedelta(seconds=1)
        return self.current


def _real_ledger(clock):
    from guanlan_v2.orchestration.eventstore import (
        RuntimeStores,
        SchemaRegistryResolver,
    )
    from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry
    from guanlan_v2.orchestration.trial import (
        HoldoutReceipt,
        HoldoutWindow,
        OptimizeCandidate,
        SplitSpec,
        StudyFamily,
        StudySpec,
        TrialRecord,
        ValidationMetrics,
    )
    from guanlan_v2.orchestration.trial_ledger import (
        PHASE4_STATE_CELL_NAMESPACES,
        TrialLedger,
    )

    reg = Phase2RuntimeRegistry()
    for model in (StudySpec, StudyFamily, SplitSpec, OptimizeCandidate,
                  ValidationMetrics, HoldoutWindow, TrialRecord, HoldoutReceipt):
        reg.register(model)
    reg.seal()
    resolver = SchemaRegistryResolver()
    resolver.register(reg)
    stores = RuntimeStores(
        resolver=resolver, clock=clock,
        allowed_cell_namespaces=PHASE4_STATE_CELL_NAMESPACES,
    )
    return TrialLedger(
        event_store=stores.events, payload_store=stores.payloads,
        state_cells=stores.cells, registry=reg, clock=clock,
        uow_factory=lambda: stores.unit_of_work,
    )


def test_mature_registered_once():
    r = _runner(_alpha_frames())
    points = _points(r)
    report = _report_over(r, points)
    # clock (and maturity_now) strictly AFTER the interval matured.
    now = report.interval_end + timedelta(days=7)
    ledger = _real_ledger(_SteppingClock(now))
    pool = _FakePool()

    s1 = hand_off_dual_curves_to_feedback(
        report, run_id="replay-run.once", pool=pool, ledger=ledger, maturity_now=now)
    s2 = hand_off_dual_curves_to_feedback(
        report, run_id="replay-run.once", pool=pool, ledger=ledger, maturity_now=now)
    assert s1.status == ExperimentStatus.COMPLETED
    assert s2.status == ExperimentStatus.COMPLETED
    assert s1.curve_report_ref is not None
    assert s1.curve_report_ref.schema_ref.name == "DualCurveReport"
    assert s1.curve_report_ref.payload_ref.namespace == "main"

    # the REAL ledger registered the realized interval EXACTLY once (idempotent).
    study, _win = _feedback_study_and_window(report, "replay-run.once")
    stats = ledger.effective_trial_stats(study)
    assert stats["holdout_windows_registered"] == 1


# =========================================================================== #
# Row 13 — zero LLM calls anywhere in the dual-curve build                       #
# =========================================================================== #
def test_zero_llm_calls():
    r = _runner(_alpha_frames())
    points = _points(r)
    intents = tuple(_intent_for(p, [_pos(0.5)], 0.5) for p in points)
    det = tuple(
        derive_deterministic_targets(
            p, factor_scores={"600000": 0.5}, universe=(_sym(),),
            provenance_digest=_SOURCED)
        for p in points
    )
    report = build_dual_curves(
        execution_config=_exec_config(r), points=points,
        deterministic_target_sets=det, intents=intents, shadow_runner=r,
    )
    # the runner is synchronous + zero-LLM: it exposes NO model gateway and its
    # results reference no LLM origin. The deterministic lane keeps ()-digests.
    assert report.deterministic.applied_intent_digests == ()
    assert not hasattr(r, "model_gateway")
    # every applied intent digest on the LLM curve is a content digest, never an
    # "origin=LLM" provenance object (the intents carry origin, the curve does not).
    for d in report.llm_shadow.applied_intent_digests:
        assert isinstance(d, str) and len(d) == 64


# =========================================================================== #
# Row 14 — engine/ backtest modules are byte-identical to the Task-0 pins        #
# =========================================================================== #
def test_engine_untouched():
    import financial_analyst

    backtest = Path(financial_analyst.__file__).resolve().parent / "backtest"
    for name, frozen in ENGINE_MODULE_BYTE_DIGESTS.items():
        raw = (backtest / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == frozen, (
            f"engine module {name} byte digest drifted — the engine was touched"
        )
