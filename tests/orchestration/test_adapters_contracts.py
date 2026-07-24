# -*- coding: utf-8 -*-
"""Phase 9 · Task 1 — public adapter contracts (replay / curves / maturity /
harness / retirement).

Written test-first (RED until
``guanlan_v2.orchestration.adapters.contracts`` and its classes exist).

These pin, per the Task-1 brief:

* the field surface + validation matrix of every Phase-9 public contract
  (time-ordering, universe canonicality, curve monotonicity + kind matrix,
  shared-口径 config binding, literal-typed honesty flags, run-state status
  matrix incl. the optimizer-only-status ban + the ``TypedPayloadRef`` schema
  pin, harness coherence, retirement-gate literals + fail-closed readiness);
* projection exclusions (``updated_at`` / ``processed_at`` / ``evaluated_at``
  are audit-only — moving them never moves the semantic digest);
* frozen-name discipline (no CRIB-frozen contract is re-defined here).

Run from repo root: ``pytest tests/orchestration/test_adapters_contracts.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.adapters import contracts as C
from guanlan_v2.orchestration.context import ClockSpec
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import ExperimentStatus
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef


# --------------------------------------------------------------------------- #
# small typed constructors                                                    #
# --------------------------------------------------------------------------- #
_UTC = timezone.utc
_T0 = datetime(2026, 7, 20, 1, 0, 0, tzinfo=_UTC)
_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64
_HEX_D = "d" * 64
_HEX_E = "e" * 64
_HEX_F = "f" * 64


def _sym(code: str, exchange: str = "SH", board: str = "main") -> Symbol:
    return Symbol(code=code, exchange=exchange, board=board)


def _clock() -> ClockSpec:
    return ClockSpec(as_of=_T0, timezone="Asia/Shanghai", calendar_id="ashare.xshg")


def _exec_config(**overrides):
    fields = dict(
        universe=(_sym("600000"), _sym("600519")),
        init_cash=1_000_000.0,
        data_snapshot_content_digest=_HEX_A,
        vintage_manifest_digest=_HEX_B,
        calendar_id="ashare.xshg",
        cost_model_digest=_HEX_C,
        matching_engine_version="shadow-match-v1",
        clock=_clock(),
        schedule_digest=_HEX_D,
        intrabar_exit_priority="worst_case",
    )
    fields.update(overrides)
    return C.ShadowExecutionConfig(**fields)


def _points(n: int = 3):
    return tuple(
        C.ShadowCurvePoint(at=_T0 + timedelta(days=i), nav=1_000_000.0 + i)
        for i in range(n)
    )


def _det_series(cfg_digest: str, **overrides):
    fields = dict(
        curve_kind="deterministic_strategy",
        execution_config_digest=cfg_digest,
        points=_points(),
        trade_count=4,
        rule_id="rule.momentum.v1",
    )
    fields.update(overrides)
    return C.ShadowCurveSeries(**fields)


def _llm_series(cfg_digest: str, **overrides):
    fields = dict(
        curve_kind="llm_shadow",
        execution_config_digest=cfg_digest,
        points=_points(),
        trade_count=6,
        applied_intent_digests=(_HEX_E, _HEX_F),
    )
    fields.update(overrides)
    return C.ShadowCurveSeries(**fields)


def _dual_report(**overrides):
    cfg = overrides.pop("execution_config", None) or _exec_config()
    d = cfg.semantic_digest()
    fields = dict(
        execution_config=cfg,
        deterministic=_det_series(d),
        llm_shadow=_llm_series(d),
        interval_start=_T0,
        interval_end=_T0 + timedelta(days=5),
        decision_point_count=3,
        delta_total_return=0.021,
    )
    fields.update(overrides)
    return C.DualCurveReport(**fields)


def _typed_report_ref(name: str = "DualCurveReport", namespace: str = "main"):
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=name, version="1"),
        payload_ref=PayloadRef(
            namespace=namespace, object_id="obj-1", content_digest=_HEX_E
        ),
    )


def _run_state(**overrides):
    fields = dict(
        experiment_id="exp-1",
        run_id="run-1",
        request_id="req-1",
        schedule_digest=_HEX_A,
        execution_config_digest=_HEX_B,
        status=ExperimentStatus.RUNNING,
        completed_points=2,
        total_points=5,
        updated_at=_T0,
    )
    fields.update(overrides)
    return C.ShadowReplayRunState(**fields)


# =========================================================================== #
# ReplayDecisionPoint                                                          #
# =========================================================================== #
def _decision_point(**overrides):
    fields = dict(
        schedule_ref=C.ContentRef(id="sched.daily", version="1", content_digest=_HEX_A),
        schedule_digest=_HEX_A,
        point_ordinal=1,
        scheduled_for=_T0 + timedelta(hours=2),
        cutoff_at=_T0,
        decision_as_of=_T0 + timedelta(hours=1),
        eligible_execution_at=_T0 + timedelta(hours=3),
        execution_price_field="open",
        bar_frequency="1d",
    )
    fields.update(overrides)
    return C.ReplayDecisionPoint(**fields)


def test_decision_point_valid_roundtrips():
    dp = _decision_point()
    assert dp.point_ordinal == 1
    assert dp.execution_price_field == "open"
    assert dp.semantic_digest()


def test_decision_point_time_ordering():
    # cutoff_at > decision_as_of rejected (field-named error)
    with pytest.raises(ValidationError) as ei:
        _decision_point(cutoff_at=_T0 + timedelta(hours=2))
    assert "cutoff_at" in str(ei.value)
    # decision_as_of >= eligible_execution_at rejected
    with pytest.raises(ValidationError) as ej:
        _decision_point(
            decision_as_of=_T0 + timedelta(hours=3),
            eligible_execution_at=_T0 + timedelta(hours=3),
        )
    assert "eligible_execution_at" in str(ej.value)


def test_decision_point_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        _decision_point(scheduled_for=datetime(2026, 7, 20, 3, 0, 0))


def test_decision_point_ordinal_and_frequency_bounds():
    with pytest.raises(ValidationError):
        _decision_point(point_ordinal=0)  # PositiveInt
    with pytest.raises(ValidationError):
        _decision_point(bar_frequency="daily")  # not in closed literal


# =========================================================================== #
# ShadowExecutionConfig                                                        #
# =========================================================================== #
def test_execution_config_valid_roundtrips():
    cfg = _exec_config()
    assert cfg.init_cash == 1_000_000.0
    assert cfg.semantic_digest()


def test_execution_config_universe_canonical():
    # duplicate rejected
    with pytest.raises(ValidationError):
        _exec_config(universe=(_sym("600000"), _sym("600000")))
    # unsorted rejected
    with pytest.raises(ValidationError):
        _exec_config(universe=(_sym("600519"), _sym("600000")))
    # empty rejected
    with pytest.raises(ValidationError):
        _exec_config(universe=())
    # sorted unique accepted
    cfg = _exec_config(universe=(_sym("600000"), _sym("600519")))
    assert len(cfg.universe) == 2


def test_execution_config_init_cash_positive():
    with pytest.raises(ValidationError):
        _exec_config(init_cash=0.0)
    with pytest.raises(ValidationError):
        _exec_config(init_cash=float("nan"))


def test_execution_config_all_semantic():
    """Every field is semantic — flipping any one moves the semantic digest."""
    base = _exec_config()
    base_digest = base.semantic_digest()
    flips = dict(
        universe=(_sym("600000"), _sym("600519"), _sym("601318")),
        init_cash=2_000_000.0,
        data_snapshot_content_digest=_HEX_F,
        vintage_manifest_digest=_HEX_F,
        calendar_id="ashare.xshe",
        cost_model_digest=_HEX_F,
        matching_engine_version="shadow-match-v2",
        clock=ClockSpec(as_of=_T0, timezone="UTC", calendar_id="ashare.xshg"),
        schedule_digest=_HEX_F,
        intrabar_exit_priority="stop_first",
    )
    for field, new_value in flips.items():
        moved = _exec_config(**{field: new_value})
        assert moved.semantic_digest() != base_digest, (
            f"flipping {field} must move the semantic digest (it is semantic)"
        )


# =========================================================================== #
# ShadowCurvePoint / ShadowCurveSeries                                         #
# =========================================================================== #
def test_curve_point_nav_positive_and_nested_no_schema_version():
    with pytest.raises(ValidationError):
        C.ShadowCurvePoint(at=_T0, nav=0.0)
    with pytest.raises(ValidationError):
        C.ShadowCurvePoint(at=_T0, nav=float("inf"))
    # nested component carries no schema_version field
    assert "schema_version" not in C.ShadowCurvePoint.model_fields


def test_curve_series_monotone_points():
    d = _exec_config().semantic_digest()
    # equal timestamps rejected
    with pytest.raises(ValidationError):
        _det_series(
            d,
            points=(
                C.ShadowCurvePoint(at=_T0, nav=1.0),
                C.ShadowCurvePoint(at=_T0, nav=2.0),
            ),
        )
    # decreasing timestamps rejected
    with pytest.raises(ValidationError):
        _det_series(
            d,
            points=(
                C.ShadowCurvePoint(at=_T0 + timedelta(days=1), nav=1.0),
                C.ShadowCurvePoint(at=_T0, nav=2.0),
            ),
        )
    # empty rejected
    with pytest.raises(ValidationError):
        _det_series(d, points=())


def test_curve_series_kind_matrix():
    d = _exec_config().semantic_digest()
    # llm_shadow without applied_intent_digests rejected
    with pytest.raises(ValidationError):
        C.ShadowCurveSeries(
            curve_kind="llm_shadow",
            execution_config_digest=d,
            points=_points(),
            trade_count=1,
            applied_intent_digests=(),
        )
    # deterministic_strategy without rule_id rejected
    with pytest.raises(ValidationError):
        C.ShadowCurveSeries(
            curve_kind="deterministic_strategy",
            execution_config_digest=d,
            points=_points(),
            trade_count=1,
            rule_id=None,
        )
    # the reverse half of the biconditional:
    # deterministic carrying intents rejected
    with pytest.raises(ValidationError):
        C.ShadowCurveSeries(
            curve_kind="deterministic_strategy",
            execution_config_digest=d,
            points=_points(),
            trade_count=1,
            rule_id="rule.x",
            applied_intent_digests=(_HEX_E,),
        )
    # llm_shadow carrying rule_id rejected
    with pytest.raises(ValidationError):
        C.ShadowCurveSeries(
            curve_kind="llm_shadow",
            execution_config_digest=d,
            points=_points(),
            trade_count=1,
            applied_intent_digests=(_HEX_E,),
            rule_id="rule.x",
        )


def test_curve_series_valid_both_kinds():
    d = _exec_config().semantic_digest()
    assert _det_series(d).curve_kind == "deterministic_strategy"
    assert _llm_series(d).curve_kind == "llm_shadow"


# =========================================================================== #
# DualCurveReport                                                             #
# =========================================================================== #
def test_dual_curve_valid_roundtrips():
    rep = _dual_report()
    assert rep.not_causal_attribution is True
    assert rep.decision_point_count == 3
    assert rep.semantic_digest()


def test_dual_curve_config_binding():
    """The shared-口径 red line: both series must bind the config's own digest."""
    cfg = _exec_config()
    good = cfg.semantic_digest()
    # deterministic bound to a wrong config digest rejected
    with pytest.raises(ValidationError):
        C.DualCurveReport(
            execution_config=cfg,
            deterministic=_det_series(_HEX_F),
            llm_shadow=_llm_series(good),
            interval_start=_T0,
            interval_end=_T0 + timedelta(days=5),
            decision_point_count=3,
            delta_total_return=None,
        )
    # llm_shadow bound to a wrong config digest rejected
    with pytest.raises(ValidationError):
        C.DualCurveReport(
            execution_config=cfg,
            deterministic=_det_series(good),
            llm_shadow=_llm_series(_HEX_F),
            interval_start=_T0,
            interval_end=_T0 + timedelta(days=5),
            decision_point_count=3,
            delta_total_return=None,
        )


def test_dual_curve_kind_positions():
    cfg = _exec_config()
    d = cfg.semantic_digest()
    # a deterministic series passed in the llm_shadow slot rejected
    with pytest.raises(ValidationError):
        C.DualCurveReport(
            execution_config=cfg,
            deterministic=_det_series(d),
            llm_shadow=_det_series(d),
            interval_start=_T0,
            interval_end=_T0 + timedelta(days=5),
            decision_point_count=3,
            delta_total_return=None,
        )
    # an llm_shadow series passed in the deterministic slot rejected
    with pytest.raises(ValidationError):
        C.DualCurveReport(
            execution_config=cfg,
            deterministic=_llm_series(d),
            llm_shadow=_llm_series(d),
            interval_start=_T0,
            interval_end=_T0 + timedelta(days=5),
            decision_point_count=3,
            delta_total_return=None,
        )


def test_dual_curve_interval_ordering():
    with pytest.raises(ValidationError):
        _dual_report(interval_start=_T0 + timedelta(days=5), interval_end=_T0)


def test_not_causal_attribution_literal():
    with pytest.raises(ValidationError):
        _dual_report(not_causal_attribution=False)


# =========================================================================== #
# ShadowReplayRunState                                                        #
# =========================================================================== #
def test_run_state_valid_roundtrips():
    st = _run_state()
    assert st.status is ExperimentStatus.RUNNING
    assert st.semantic_digest()


def test_run_state_status_matrix():
    # WAITING_FOR_MATURITY missing wakeup_key rejected
    with pytest.raises(ValidationError):
        _run_state(
            status=ExperimentStatus.WAITING_FOR_MATURITY,
            resume_after=_T0 + timedelta(days=1),
            wakeup_key=None,
        )
    # COMPLETED missing curve_report_ref rejected
    with pytest.raises(ValidationError):
        _run_state(
            status=ExperimentStatus.COMPLETED,
            completed_points=5,
            total_points=5,
            curve_report_ref=None,
        )
    # RUNNING carrying wakeup_key rejected
    with pytest.raises(ValidationError):
        _run_state(status=ExperimentStatus.RUNNING, wakeup_key="wk-1")
    # completed_points > total_points rejected
    with pytest.raises(ValidationError):
        _run_state(completed_points=6, total_points=5)


def test_run_state_waiting_and_completed_valid():
    waiting = _run_state(
        status=ExperimentStatus.WAITING_FOR_MATURITY,
        resume_after=_T0 + timedelta(days=1),
        wakeup_key="wk-1",
    )
    assert waiting.wakeup_key == "wk-1"
    completed = _run_state(
        status=ExperimentStatus.COMPLETED,
        completed_points=5,
        total_points=5,
        curve_report_ref=_typed_report_ref(),
    )
    assert completed.completed_points == 5


def test_run_state_non_waiting_forbids_maturity_pair():
    # a non-waiting run may not carry resume_after either (mirrors OptimizeRunState)
    with pytest.raises(ValidationError):
        _run_state(status=ExperimentStatus.FAILED, resume_after=_T0 + timedelta(days=1))


def test_run_state_optimizer_statuses_forbidden():
    with pytest.raises(ValidationError):
        _run_state(status=ExperimentStatus.PASSED_VALIDATION)
    with pytest.raises(ValidationError):
        _run_state(status=ExperimentStatus.SEALED_EVALUATING)


def test_curve_report_ref_typed_and_pinned():
    # schema_ref naming another schema rejected
    with pytest.raises(ValidationError):
        _run_state(
            status=ExperimentStatus.COMPLETED,
            completed_points=5,
            total_points=5,
            curve_report_ref=_typed_report_ref(name="ShadowRunResult"),
        )
    # payload_ref namespace != main rejected
    with pytest.raises(ValidationError):
        _run_state(
            status=ExperimentStatus.COMPLETED,
            completed_points=5,
            total_points=5,
            curve_report_ref=_typed_report_ref(namespace="sealed"),
        )
    # correctly pinned accepted
    ok = _run_state(
        status=ExperimentStatus.COMPLETED,
        completed_points=5,
        total_points=5,
        curve_report_ref=_typed_report_ref(),
    )
    assert ok.curve_report_ref is not None


def test_run_state_updated_at_audit_only():
    a = _run_state(updated_at=_T0)
    b = _run_state(updated_at=_T0 + timedelta(hours=9))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


# =========================================================================== #
# ReplayWakeupReceipt                                                         #
# =========================================================================== #
def _wakeup_receipt(**overrides):
    fields = dict(
        wakeup_key="wk-1",
        experiment_id="exp-1",
        outcome="resumed",
        matured_points=2,
        state_digest_after=_HEX_A,
        processed_at=_T0,
    )
    fields.update(overrides)
    return C.ReplayWakeupReceipt(**fields)


def test_wakeup_receipt_valid_and_outcome_closed():
    assert _wakeup_receipt().outcome == "resumed"
    with pytest.raises(ValidationError):
        _wakeup_receipt(outcome="woke_up")  # not in closed literal


def test_wakeup_receipt_processed_at_audit_only():
    a = _wakeup_receipt(processed_at=_T0)
    b = _wakeup_receipt(processed_at=_T0 + timedelta(hours=3))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


# =========================================================================== #
# MirrorHarnessCaseResult / MirrorHarnessReport                                #
# =========================================================================== #
def _case(case_id: str, passed: bool = True, reason=None):
    return C.MirrorHarnessCaseResult(case_id=case_id, passed=passed, reason=reason)


def _harness_report(**overrides):
    fields = dict(
        fixture_digest=_HEX_A,
        matching_engine_version="shadow-match-v1",
        results=(_case("case.a"), _case("case.b")),
        all_passed=True,
    )
    fields.update(overrides)
    return C.MirrorHarnessReport(**fields)


def test_harness_case_reason_matrix():
    # a failed case without a reason rejected
    with pytest.raises(ValidationError):
        _case("case.x", passed=False, reason=None)
    # a passed case carrying a reason rejected (biconditional)
    with pytest.raises(ValidationError):
        _case("case.x", passed=True, reason="unexpected")
    # a failed case with reason accepted
    assert _case("case.x", passed=False, reason="fill mismatch").passed is False


def test_harness_report_all_passed_coherence():
    # all_passed=True with a failed result rejected
    with pytest.raises(ValidationError):
        _harness_report(
            results=(_case("case.a"), _case("case.b", passed=False, reason="x")),
            all_passed=True,
        )
    # all_passed=False while every result passed rejected
    with pytest.raises(ValidationError):
        _harness_report(results=(_case("case.a"), _case("case.b")), all_passed=False)


def test_harness_results_sorted_unique():
    # duplicate case_id rejected
    with pytest.raises(ValidationError):
        _harness_report(results=(_case("case.a"), _case("case.a")))
    # unsorted rejected
    with pytest.raises(ValidationError):
        _harness_report(results=(_case("case.b"), _case("case.a")))
    # empty rejected
    with pytest.raises(ValidationError):
        _harness_report(results=())


# =========================================================================== #
# RetirementCriterion / EntryPointRetirementGate                              #
# =========================================================================== #
def _criterion(criterion_id: str, **overrides):
    fields = dict(
        criterion_id=criterion_id,
        description="the report path emits identical bytes via the new adapter",
        evidence_kind="pytest_suite",
        evidence_selector="tests/orchestration/test_x.py::test_y",
    )
    fields.update(overrides)
    return C.RetirementCriterion(**fields)


def _gate(**overrides):
    fields = dict(
        entry_point="console.report_subprocess",
        replacement="guanlan_v2.orchestration.adapters.console_report",
        criteria=(_criterion("crit.parity"), _criterion("crit.suite")),
    )
    fields.update(overrides)
    return C.EntryPointRetirementGate(**fields)


def test_retirement_gate_valid_and_default_flag():
    gate = _gate()
    assert gate.removal_allowed_without_gate is False
    assert gate.entry_point == "console.report_subprocess"


def test_retirement_gate_literal_entry_points():
    # unknown entry_point rejected
    with pytest.raises(ValidationError):
        _gate(entry_point="console.mystery")
    # removal_allowed_without_gate can never be flipped to True
    with pytest.raises(ValidationError):
        _gate(removal_allowed_without_gate=True)


def test_retirement_gate_criteria_non_empty_unique():
    with pytest.raises(ValidationError):
        _gate(criteria=())
    with pytest.raises(ValidationError):
        _gate(criteria=(_criterion("crit.dup"), _criterion("crit.dup")))


def test_retirement_criterion_evidence_kind_closed():
    with pytest.raises(ValidationError):
        _criterion("crit.x", evidence_kind="hunch")


# =========================================================================== #
# RetirementCriterionResult / RetirementReadinessReport                       #
# =========================================================================== #
def _result(criterion_id: str, status: str, evidence_digest=None, reason=None):
    return C.RetirementCriterionResult(
        criterion_id=criterion_id,
        status=status,
        evidence_digest=evidence_digest,
        reason=reason,
    )


def _readiness(**overrides):
    gate = overrides.pop("_gate", None) or _gate()
    fields = dict(
        gate_digest=gate.semantic_digest(),
        entry_point=gate.entry_point,
        results=(
            _result("crit.parity", "green", evidence_digest=_HEX_A),
            _result("crit.suite", "green", evidence_digest=_HEX_B),
        ),
        ready=True,
        evaluated_at=_T0,
    )
    fields.update(overrides)
    return C.RetirementReadinessReport(**fields)


def test_green_requires_evidence_digest():
    # a green result without an evidence_digest rejected
    with pytest.raises(ValidationError):
        _result("crit.x", "green", evidence_digest=None)
    # a green result carrying a reason rejected (biconditional)
    with pytest.raises(ValidationError):
        _result("crit.x", "green", evidence_digest=_HEX_A, reason="ok")
    # a non-green result requires a reason and forbids an evidence_digest
    with pytest.raises(ValidationError):
        _result("crit.x", "red", reason=None)
    with pytest.raises(ValidationError):
        _result("crit.x", "red", evidence_digest=_HEX_A, reason="broke")
    assert _result("crit.x", "red", reason="broke").status == "red"


def test_readiness_valid_roundtrips():
    r = _readiness()
    assert r.ready is True
    assert r.semantic_digest()


def test_readiness_fail_closed():
    # ready=True with an unavailable result rejected (fail-closed)
    with pytest.raises(ValidationError):
        _readiness(
            results=(
                _result("crit.parity", "green", evidence_digest=_HEX_A),
                _result("crit.suite", "unavailable", reason="fixture missing"),
            ),
            ready=True,
        )
    # ready=False with the same set accepted
    ok = _readiness(
        results=(
            _result("crit.parity", "green", evidence_digest=_HEX_A),
            _result("crit.suite", "unavailable", reason="fixture missing"),
        ),
        ready=False,
    )
    assert ok.ready is False


def test_readiness_ready_true_requires_all_green():
    # ready=True with a red result rejected
    with pytest.raises(ValidationError):
        _readiness(
            results=(
                _result("crit.parity", "green", evidence_digest=_HEX_A),
                _result("crit.suite", "red", reason="parity broke"),
            ),
            ready=True,
        )


def test_readiness_results_sorted_unique_non_empty():
    with pytest.raises(ValidationError):
        _readiness(results=())
    with pytest.raises(ValidationError):
        _readiness(
            results=(
                _result("crit.suite", "green", evidence_digest=_HEX_B),
                _result("crit.parity", "green", evidence_digest=_HEX_A),
            )
        )
    with pytest.raises(ValidationError):
        _readiness(
            results=(
                _result("crit.parity", "green", evidence_digest=_HEX_A),
                _result("crit.parity", "green", evidence_digest=_HEX_B),
            )
        )


def test_readiness_evaluated_at_audit_only():
    a = _readiness(evaluated_at=_T0)
    b = _readiness(evaluated_at=_T0 + timedelta(hours=7))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


def test_readiness_coverage_exact():
    """The readiness report's results must cover the gate's criterion ids exactly."""
    gate = _gate()
    # missing a criterion id rejected
    with pytest.raises(ValueError):
        C.validate_retirement_readiness(
            gate,
            _readiness(
                _gate=gate,
                results=(_result("crit.parity", "green", evidence_digest=_HEX_A),),
                ready=False,
            ),
        )
    # an extra criterion id rejected
    with pytest.raises(ValueError):
        C.validate_retirement_readiness(
            gate,
            _readiness(
                _gate=gate,
                results=(
                    _result("crit.parity", "green", evidence_digest=_HEX_A),
                    _result("crit.suite", "green", evidence_digest=_HEX_B),
                    _result("crit.extra", "green", evidence_digest=_HEX_C),
                ),
            ),
        )
    # a gate_digest that does not match the gate rejected
    with pytest.raises(ValueError):
        C.validate_retirement_readiness(gate, _readiness(_gate=gate, gate_digest=_HEX_F))
    # entry_point mismatch rejected
    with pytest.raises(ValueError):
        C.validate_retirement_readiness(
            gate, _readiness(_gate=gate, entry_point="research.loop_direct")
        )
    # exact coverage accepted
    C.validate_retirement_readiness(gate, _readiness(_gate=gate))


# =========================================================================== #
# Frozen-name discipline                                                       #
# =========================================================================== #
def test_frozen_names_are_imports():
    """No CRIB-frozen contract name is *defined* in the adapters.contracts module."""
    frozen = (
        "DecisionSchedule",
        "TargetPortfolioIntent",
        "TargetPosition",
        "PortfolioTargetProposal",
    )
    for name in frozen:
        obj = getattr(C, name, None)
        if obj is None:
            continue
        assert getattr(obj, "__module__", None) != C.__name__, (
            f"{name} must be imported, never defined in contracts.py"
        )
