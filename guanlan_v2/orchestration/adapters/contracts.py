# -*- coding: utf-8 -*-
"""Phase 9 · Task 1 — the public adapter contracts for the honest 完整落子/帷幄
replay (双曲线回放) and the legacy-entry-point retirement gate.

This module is the **contract layer only** — every class is a strict, frozen,
``extra="forbid"`` :class:`~guanlan_v2.orchestration.digest.DigestModel`, carries
no runtime logic, and re-uses the CRIB-frozen Phase 1–8 primitives verbatim
(``Symbol`` / ``ClockSpec`` / ``ContentRef`` / ``TypedPayloadRef`` /
``ExperimentStatus`` / the strict ``digest`` types). It defines **no** CRIB-frozen
payload (``DecisionSchedule`` / ``TargetPortfolioIntent`` / ``TargetPosition`` /
``PortfolioTargetProposal`` are imported by consumers, never re-declared here).

The seven contract families, and the invariants each *structurally* guarantees:

* :class:`ReplayDecisionPoint` — one resolved point of a shadow replay schedule,
  pinning the Phase-6-ruled unified time model
  ``cutoff_at <= decision_as_of < eligible_execution_at`` at the type level. The
  ``next_open ↔ open`` / ``next_bar_close ↔ close`` pairing and the
  frequency/price-field equality against the referenced schedule are the Task-4
  resolver's job — this record only carries the fields; it never re-derives them.
* :class:`ShadowExecutionConfig` — the single 同一
  universe/初始资金/数据快照/交易日历/成交费用模型/clock attestation both curves
  are matched under. Every field is semantic (no audit exclusion): the config's
  own ``semantic_digest()`` is the shared-口径 identity the two curves bind to.
* :class:`ShadowCurvePoint` / :class:`ShadowCurveSeries` — a strictly-increasing
  NAV path plus its kind (``deterministic_strategy`` / ``llm_shadow``); the
  kind ⇔ evidence biconditional (a deterministic curve names its ``rule_id`` and
  carries no applied intents; an llm-shadow curve names its applied intents and no
  rule) is structural.
* :class:`DualCurveReport` — the two curves side by side under one config. The
  shared-口径 red line (both series bind ``execution_config.semantic_digest()``),
  the kind-position lock, and the permanently-true
  ``not_causal_attribution: Literal[True]`` honesty flag are all structural.
* :class:`ShadowReplayRunState` / :class:`ReplayWakeupReceipt` — the durable
  replay head + the maturity-wakeup receipt. The run-state status matrix mirrors
  the Phase-4 ``OptimizeRunState`` maturity precedent (only
  ``WAITING_FOR_MATURITY`` carries ``resume_after``/``wakeup_key``), forbids the
  optimizer-only statuses (``PASSED_VALIDATION`` / ``SEALED_EVALUATING``), and
  pins ``curve_report_ref`` to the registered ``DualCurveReport@1`` in the public
  ``main`` namespace. ``updated_at`` / ``processed_at`` are audit-only.
* :class:`MirrorHarnessCaseResult` / :class:`MirrorHarnessReport` — the
  engine-parity harness verdict (``all_passed`` ⇔ every case passed; a failed
  case names its reason).
* :class:`RetirementCriterion` / :class:`EntryPointRetirementGate` /
  :class:`RetirementCriterionResult` / :class:`RetirementReadinessReport` — the
  measurable retirement gate for each legacy entry point. Removal is never allowed
  without the gate (``removal_allowed_without_gate: Literal[False]``); readiness
  is fail-closed (``ready`` ⇔ every result ``green``; any ``unavailable`` ⇒ not
  ready). :func:`validate_retirement_readiness` is the pure cross-object invariant
  (results cover the gate's criterion ids exactly + the report binds the gate's
  own digest) Task 11's golden calls — it is a checker, never a builder.

Task 4 (the runtime resolver) owns the no-retroactive-intents / PIT rules; the
fields those rules need (``decision_as_of``, ``point_ordinal`` …) live here, but
no runtime logic does.
"""
from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from guanlan_v2.orchestration.context import ClockSpec
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonNegativeInt,
    NonEmptyStr,
    PositiveInt,
    UtcDateTime,
)
from guanlan_v2.orchestration.enums import ExperimentStatus
from guanlan_v2.orchestration.refs import ContentRef, LogicalId, TypedPayloadRef

__all__ = [
    "ReplayDecisionPoint",
    "ShadowExecutionConfig",
    "ShadowCurvePoint",
    "ShadowCurveSeries",
    "DualCurveReport",
    "ShadowReplayRunState",
    "ReplayWakeupReceipt",
    "MirrorHarnessCaseResult",
    "MirrorHarnessReport",
    "RetirementCriterion",
    "EntryPointRetirementGate",
    "RetirementCriterionResult",
    "RetirementReadinessReport",
    "validate_retirement_readiness",
]

#: the closed bar-frequency vocabulary shared with the Phase-6 ``DecisionSchedule``.
BarFrequency = Literal["1d", "60m", "30m", "15m", "5m", "1m"]
#: the closed intrabar-exit priority vocabulary shared with the Phase-6 schedule.
IntrabarExitPriority = Literal["worst_case", "stop_first", "take_profit_first"]
#: the three legacy entry points Phase 9 retires (console report subprocess, the
#: swarm preset CLI loader, the direct research-loop call).
LegacyEntryPoint = Literal[
    "console.report_subprocess",
    "swarm.load_preset_cli",
    "research.loop_direct",
]


# =========================================================================== #
# ReplayDecisionPoint                                                          #
# =========================================================================== #
class ReplayDecisionPoint(DigestModel):
    """One resolved decision point of a shadow-replay schedule.

    Pins the Phase-6-ruled unified time model at the type level:
    ``cutoff_at <= decision_as_of < eligible_execution_at``. ``schedule_ref`` +
    ``schedule_digest`` name the exact :class:`DecisionSchedule` this point was
    resolved from; ``point_ordinal`` is its 1-based position in the resolved
    sequence. ``execution_price_field`` / ``bar_frequency`` are carried so the
    Task-4 resolver can assert they equal the referenced schedule's fields (and
    refuse a non-``1d`` frequency under ``shadow-match-v1``); the pairing itself is
    upstream — this record does not re-derive it. Every field is semantic.
    """

    schema_version: Literal["1"] = "1"
    schedule_ref: ContentRef
    schedule_digest: DigestHex
    point_ordinal: PositiveInt
    scheduled_for: UtcDateTime
    cutoff_at: UtcDateTime
    decision_as_of: UtcDateTime
    eligible_execution_at: UtcDateTime
    execution_price_field: Literal["open", "close"]
    bar_frequency: BarFrequency

    @model_validator(mode="after")
    def _time_ordering(self) -> "ReplayDecisionPoint":
        if self.cutoff_at > self.decision_as_of:
            raise ValueError(
                "cutoff_at must be <= decision_as_of "
                f"(got cutoff_at={self.cutoff_at.isoformat()} > "
                f"decision_as_of={self.decision_as_of.isoformat()})"
            )
        if self.decision_as_of >= self.eligible_execution_at:
            raise ValueError(
                "decision_as_of must be < eligible_execution_at "
                f"(got decision_as_of={self.decision_as_of.isoformat()} >= "
                f"eligible_execution_at={self.eligible_execution_at.isoformat()})"
            )
        return self


# =========================================================================== #
# ShadowExecutionConfig                                                        #
# =========================================================================== #
class ShadowExecutionConfig(DigestModel):
    """The single shared-口径 attestation both replay curves are matched under.

    One 同一 universe / 初始资金 / 数据快照 / 交易日历 / 成交费用模型 / clock
    value: the sorted, unique instrument ``universe``, positive ``init_cash``, the
    data-snapshot + vintage-manifest content digests, the ``calendar_id``, the
    cost-model digest, the ``matching_engine_version``, the frozen ``clock`` and
    the ``schedule_digest`` + ``intrabar_exit_priority``. Every field is semantic —
    this config's own ``semantic_digest()`` is the identity the two
    :class:`ShadowCurveSeries` bind to, so a change to any matching parameter
    invalidates a curve that claimed the old 口径.
    """

    schema_version: Literal["1"] = "1"
    universe: tuple[Symbol, ...]
    init_cash: FiniteFloat = Field(gt=0)
    data_snapshot_content_digest: DigestHex
    vintage_manifest_digest: DigestHex
    calendar_id: NonEmptyStr
    cost_model_digest: DigestHex
    matching_engine_version: NonEmptyStr
    clock: ClockSpec
    schedule_digest: DigestHex
    intrabar_exit_priority: IntrabarExitPriority

    @model_validator(mode="after")
    def _universe_canonical(self) -> "ShadowExecutionConfig":
        if not self.universe:
            raise ValueError("universe must be non-empty")
        codes = [s.code for s in self.universe]
        if codes != sorted(codes):
            raise ValueError("universe must be canonically sorted by code")
        if len(set(codes)) != len(codes):
            raise ValueError("universe must not contain a duplicate code")
        return self


# =========================================================================== #
# ShadowCurvePoint / ShadowCurveSeries                                         #
# =========================================================================== #
class ShadowCurvePoint(DigestModel):
    """One ``(instant, nav)`` sample of a shadow NAV path (nested; no schema_version).

    ``nav`` is a strictly-positive finite float; ``at`` is a tz-aware instant.
    """

    at: UtcDateTime
    nav: FiniteFloat = Field(gt=0)


class ShadowCurveSeries(DigestModel):
    """One replay curve: its kind, the config it bound to, and its NAV path.

    ``points`` is a non-empty, strictly ``at``-increasing NAV path.
    ``execution_config_digest`` is the :class:`ShadowExecutionConfig` semantic
    digest this curve was produced under (bound by :class:`DualCurveReport`). The
    kind ⇔ evidence biconditional is structural: a ``deterministic_strategy`` curve
    names its ``rule_id`` and carries no ``applied_intent_digests``; an
    ``llm_shadow`` curve names a non-empty ``applied_intent_digests`` and no
    ``rule_id``.
    """

    schema_version: Literal["1"] = "1"
    curve_kind: Literal["deterministic_strategy", "llm_shadow"]
    execution_config_digest: DigestHex
    points: tuple[ShadowCurvePoint, ...]
    trade_count: NonNegativeInt
    applied_intent_digests: tuple[DigestHex, ...] = ()
    rule_id: NonEmptyStr | None = None
    badges: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "ShadowCurveSeries":
        if not self.points:
            raise ValueError("points must be non-empty")
        for prev, cur in zip(self.points, self.points[1:]):
            if cur.at <= prev.at:
                raise ValueError(
                    "points must be strictly increasing in at "
                    f"(got {cur.at.isoformat()} <= {prev.at.isoformat()})"
                )
        has_intents = bool(self.applied_intent_digests)
        has_rule = self.rule_id is not None
        if self.curve_kind == "llm_shadow":
            if not has_intents:
                raise ValueError(
                    "curve_kind 'llm_shadow' requires a non-empty applied_intent_digests"
                )
            if has_rule:
                raise ValueError("curve_kind 'llm_shadow' must not carry a rule_id")
        else:  # deterministic_strategy
            if not has_rule:
                raise ValueError(
                    "curve_kind 'deterministic_strategy' requires a rule_id"
                )
            if has_intents:
                raise ValueError(
                    "curve_kind 'deterministic_strategy' must not carry "
                    "applied_intent_digests"
                )
        return self


# =========================================================================== #
# DualCurveReport                                                             #
# =========================================================================== #
class DualCurveReport(DigestModel):
    """The two shadow curves side by side under one shared execution config.

    The shared-口径 red line is structural: both ``deterministic`` and
    ``llm_shadow`` must bind ``execution_config.semantic_digest()`` (a report can
    never juxtapose two curves matched under different parameters). The kind
    positions are locked (``deterministic`` is the ``deterministic_strategy`` curve,
    ``llm_shadow`` the ``llm_shadow`` curve) and ``not_causal_attribution`` is a
    permanently-true honesty flag — the report never claims the delta is causal.
    ``delta_total_return`` is an optional reported scalar (``None`` when either
    curve's total return is undefined).
    """

    schema_version: Literal["1"] = "1"
    execution_config: ShadowExecutionConfig
    deterministic: ShadowCurveSeries
    llm_shadow: ShadowCurveSeries
    interval_start: UtcDateTime
    interval_end: UtcDateTime
    decision_point_count: PositiveInt
    delta_total_return: FiniteFloat | None = None
    not_causal_attribution: Literal[True] = True
    badges: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "DualCurveReport":
        config_digest = self.execution_config.semantic_digest()
        if self.deterministic.execution_config_digest != config_digest:
            raise ValueError(
                "deterministic.execution_config_digest must equal "
                "execution_config.semantic_digest() (shared-口径 red line)"
            )
        if self.llm_shadow.execution_config_digest != config_digest:
            raise ValueError(
                "llm_shadow.execution_config_digest must equal "
                "execution_config.semantic_digest() (shared-口径 red line)"
            )
        if self.deterministic.curve_kind != "deterministic_strategy":
            raise ValueError(
                "the deterministic slot must carry a 'deterministic_strategy' curve"
            )
        if self.llm_shadow.curve_kind != "llm_shadow":
            raise ValueError("the llm_shadow slot must carry an 'llm_shadow' curve")
        if self.interval_start >= self.interval_end:
            raise ValueError("interval_start must be < interval_end")
        return self


# =========================================================================== #
# ShadowReplayRunState / ReplayWakeupReceipt                                   #
# =========================================================================== #
#: the optimizer-only statuses a replay run may never take (controller ruling).
_REPLAY_FORBIDDEN_STATUSES: frozenset[ExperimentStatus] = frozenset(
    {ExperimentStatus.PASSED_VALIDATION, ExperimentStatus.SEALED_EVALUATING}
)


class ShadowReplayRunState(DigestModel):
    """The durable head of one shadow-replay run.

    ``updated_at`` is the audit wall-clock (``SEMANTIC_EXCLUDE``). The maturity
    matrix mirrors the Phase-4 :class:`OptimizeRunState` precedent:
    ``resume_after`` and ``wakeup_key`` are present **iff** ``status ==
    WAITING_FOR_MATURITY`` (so a parked wakeup can only exist for a genuinely
    waiting run — this also forbids ``wakeup_key`` on ``RUNNING`` / ``FAILED``). A
    ``COMPLETED`` run has ``completed_points == total_points`` and a set
    ``curve_report_ref``; ``completed_points`` never exceeds ``total_points``. The
    optimizer-only statuses ``PASSED_VALIDATION`` / ``SEALED_EVALUATING`` are
    rejected outright. ``curve_report_ref`` is schema-pinned to the registered
    ``DualCurveReport@1`` in the public ``main`` namespace.
    """

    schema_version: Literal["1"] = "1"
    experiment_id: NonEmptyStr
    run_id: NonEmptyStr
    request_id: NonEmptyStr
    schedule_digest: DigestHex
    execution_config_digest: DigestHex
    status: ExperimentStatus
    completed_points: NonNegativeInt
    total_points: PositiveInt
    last_scheduled_for: UtcDateTime | None = None
    resume_after: UtcDateTime | None = None
    wakeup_key: NonEmptyStr | None = None
    curve_report_ref: TypedPayloadRef | None = None
    updated_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"updated_at"})

    @model_validator(mode="after")
    def _status_matrix(self) -> "ShadowReplayRunState":
        if self.status in _REPLAY_FORBIDDEN_STATUSES:
            raise ValueError(
                f"status {self.status.value!r} is optimizer-only and is never a "
                "valid shadow-replay run status"
            )
        if self.completed_points > self.total_points:
            raise ValueError("completed_points must be <= total_points")

        waiting = self.status == ExperimentStatus.WAITING_FOR_MATURITY
        has_resume = self.resume_after is not None
        has_wakeup = self.wakeup_key is not None
        if waiting:
            if not (has_resume and has_wakeup):
                raise ValueError(
                    "status=WAITING_FOR_MATURITY requires resume_after and wakeup_key"
                )
        elif has_resume or has_wakeup:
            raise ValueError(
                "only status=WAITING_FOR_MATURITY may carry resume_after/wakeup_key"
            )

        if self.status == ExperimentStatus.COMPLETED:
            if self.completed_points != self.total_points:
                raise ValueError(
                    "status=COMPLETED requires completed_points == total_points"
                )
            if self.curve_report_ref is None:
                raise ValueError("status=COMPLETED requires curve_report_ref")

        if self.curve_report_ref is not None:
            ref = self.curve_report_ref
            expected_name = DualCurveReport.__name__
            expected_version = DualCurveReport.model_fields["schema_version"].default
            if (
                ref.schema_ref.name != expected_name
                or ref.schema_ref.version != expected_version
            ):
                raise ValueError(
                    "curve_report_ref.schema_ref must name "
                    f"{expected_name}@{expected_version}, got {ref.schema_ref.key}"
                )
            if ref.payload_ref.namespace != "main":
                raise ValueError(
                    "curve_report_ref.payload_ref.namespace must be 'main', got "
                    f"{ref.payload_ref.namespace!r}"
                )
        return self


class ReplayWakeupReceipt(DigestModel):
    """The receipt of one maturity-wakeup attempt against a parked replay head.

    ``processed_at`` is the audit wall-clock (``SEMANTIC_EXCLUDE``). ``outcome`` is
    a closed vocabulary; ``state_digest_after`` records the run-state semantic
    digest the wakeup left behind (audit-linkable without dereferencing it).
    """

    schema_version: Literal["1"] = "1"
    wakeup_key: NonEmptyStr
    experiment_id: NonEmptyStr
    outcome: Literal["resumed", "not_mature", "already_processed", "completed"]
    matured_points: NonNegativeInt
    state_digest_after: DigestHex
    processed_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"processed_at"})


# =========================================================================== #
# MirrorHarnessCaseResult / MirrorHarnessReport                                #
# =========================================================================== #
class MirrorHarnessCaseResult(DigestModel):
    """One engine-parity harness case verdict (nested; no schema_version).

    A failed case names its ``reason``; a passed case carries none (biconditional).
    """

    case_id: LogicalId
    passed: bool
    reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _reason_matrix(self) -> "MirrorHarnessCaseResult":
        if self.passed and self.reason is not None:
            raise ValueError("a passed case must not carry a reason")
        if not self.passed and self.reason is None:
            raise ValueError("a failed case requires a reason")
        return self


class MirrorHarnessReport(DigestModel):
    """The verdict of the engine-parity mirror harness over a fixture.

    ``results`` is a non-empty, ``case_id``-sorted, unique tuple; ``all_passed`` is
    coherent — it is ``True`` iff every case passed.
    """

    schema_version: Literal["1"] = "1"
    fixture_digest: DigestHex
    matching_engine_version: NonEmptyStr
    results: tuple[MirrorHarnessCaseResult, ...]
    all_passed: bool

    @model_validator(mode="after")
    def _verify(self) -> "MirrorHarnessReport":
        if not self.results:
            raise ValueError("results must be non-empty")
        ids = [r.case_id for r in self.results]
        if ids != sorted(ids):
            raise ValueError("results must be sorted by case_id")
        if len(set(ids)) != len(ids):
            raise ValueError("results must not contain a duplicate case_id")
        if self.all_passed != all(r.passed for r in self.results):
            raise ValueError(
                "all_passed must equal whether every result passed"
            )
        return self


# =========================================================================== #
# Retirement gate + readiness                                                  #
# =========================================================================== #
class RetirementCriterion(DigestModel):
    """One measurable criterion of a legacy-entry-point retirement gate (nested).

    ``evidence_kind`` names the reviewed evidence class and ``evidence_selector``
    the concrete pointer (a pytest node id, a parity-fixture id, a reviewed
    artifact ref, an operational run-log selector) that the criterion is proven by.
    """

    criterion_id: LogicalId
    description: NonEmptyStr
    evidence_kind: Literal[
        "pytest_suite", "parity_fixture", "reviewed_artifact", "operational_run_log"
    ]
    evidence_selector: NonEmptyStr


class EntryPointRetirementGate(DigestModel):
    """The measurable gate a legacy entry point must clear before removal.

    ``entry_point`` is one of the three legacy surfaces; ``replacement`` names the
    Phase-9 adapter that supersedes it; ``criteria`` is a non-empty tuple of
    unique-id :class:`RetirementCriterion`. ``removal_allowed_without_gate`` is a
    permanently-``False`` literal — no caller can authorize removing an entry point
    without clearing its gate.
    """

    schema_version: Literal["1"] = "1"
    entry_point: LegacyEntryPoint
    replacement: NonEmptyStr
    criteria: tuple[RetirementCriterion, ...]
    removal_allowed_without_gate: Literal[False] = False

    @model_validator(mode="after")
    def _verify(self) -> "EntryPointRetirementGate":
        if not self.criteria:
            raise ValueError("criteria must be non-empty")
        ids = [c.criterion_id for c in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("criteria must not contain a duplicate criterion_id")
        return self

    @property
    def criterion_ids(self) -> frozenset[str]:
        return frozenset(c.criterion_id for c in self.criteria)


class RetirementCriterionResult(DigestModel):
    """One evaluated criterion result (nested).

    A ``green`` result names its ``evidence_digest`` and carries no ``reason``; a
    non-green (``red`` / ``unavailable``) result names its ``reason`` and carries no
    ``evidence_digest`` (biconditional both ways).
    """

    criterion_id: LogicalId
    status: Literal["green", "red", "unavailable"]
    evidence_digest: DigestHex | None = None
    reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _verify(self) -> "RetirementCriterionResult":
        green = self.status == "green"
        if green:
            if self.evidence_digest is None:
                raise ValueError("a green result requires an evidence_digest")
            if self.reason is not None:
                raise ValueError("a green result must not carry a reason")
        else:
            if self.reason is None:
                raise ValueError("a non-green result requires a reason")
            if self.evidence_digest is not None:
                raise ValueError("a non-green result must not carry an evidence_digest")
        return self


class RetirementReadinessReport(DigestModel):
    """The evaluated readiness of one legacy entry point against its gate.

    ``gate_digest`` binds the :class:`EntryPointRetirementGate` this report was
    evaluated against; ``results`` is a non-empty, ``criterion_id``-sorted, unique
    tuple. ``ready`` is fail-closed and coherent: it is ``True`` iff every result is
    ``green`` (any ``red`` or ``unavailable`` result forces ``ready=False``).
    ``evaluated_at`` is the audit wall-clock (``SEMANTIC_EXCLUDE``). The exact
    coverage of the gate's criterion ids is a cross-object invariant checked by
    :func:`validate_retirement_readiness` (the report alone holds only the digest).
    """

    schema_version: Literal["1"] = "1"
    gate_digest: DigestHex
    entry_point: LegacyEntryPoint
    results: tuple[RetirementCriterionResult, ...]
    ready: bool
    evaluated_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"evaluated_at"})

    @model_validator(mode="after")
    def _verify(self) -> "RetirementReadinessReport":
        if not self.results:
            raise ValueError("results must be non-empty")
        ids = [r.criterion_id for r in self.results]
        if ids != sorted(ids):
            raise ValueError("results must be sorted by criterion_id")
        if len(set(ids)) != len(ids):
            raise ValueError("results must not contain a duplicate criterion_id")
        all_green = all(r.status == "green" for r in self.results)
        if self.ready != all_green:
            raise ValueError(
                "ready must be True iff every result is green (fail-closed)"
            )
        return self

    @property
    def result_ids(self) -> frozenset[str]:
        return frozenset(r.criterion_id for r in self.results)


def validate_retirement_readiness(
    gate: EntryPointRetirementGate, report: RetirementReadinessReport
) -> None:
    """Cross-object invariant binding a readiness report to its gate.

    A pure checker (never a builder): it raises :class:`ValueError` unless the
    report's ``entry_point`` and ``gate_digest`` match the gate, and the report's
    result criterion ids cover the gate's criterion ids **exactly** (no missing, no
    extra). Task 11's golden calls this after constructing both records; the report
    contract alone can only self-check sorting/uniqueness/ready-coherence because it
    holds the gate's digest, not the gate.
    """
    if report.entry_point != gate.entry_point:
        raise ValueError(
            f"readiness entry_point {report.entry_point!r} does not match gate "
            f"entry_point {gate.entry_point!r}"
        )
    if report.gate_digest != gate.semantic_digest():
        raise ValueError(
            "readiness gate_digest does not match the gate's semantic digest"
        )
    missing = gate.criterion_ids - report.result_ids
    extra = report.result_ids - gate.criterion_ids
    if missing or extra:
        raise ValueError(
            "readiness results must cover the gate's criterion ids exactly "
            f"(missing={sorted(missing)}, extra={sorted(extra)})"
        )
