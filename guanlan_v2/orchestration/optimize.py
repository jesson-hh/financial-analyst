# -*- coding: utf-8 -*-
"""The optimize state machine — ``run_optimize`` / ``resume_optimize`` /
``finalize_candidate`` (Phase 4 Task 8).

This module generalises the proven ``research.loop.run_research_loop`` state machine
into the contract-governed Evaluator-Optimizer loop that composes the four Phase 4
layers built in Tasks 1–7: the Task 5 :class:`~guanlan_v2.orchestration.trial_ledger.TrialLedger`
(cross-run raw-trial accounting + idempotent reuse), the Task 4
:class:`~guanlan_v2.orchestration.governor.Governor` (L2 overfitting governance +
budget stop), the Task 7 four-layer evaluator seams (L0 honesty gate, L1 metrics,
L2 governance delegate, L3 attribution feedback) and the Task 6 one-shot sealed
holdout gateway. Every research product routes to *draft* status for human review;
the optimizer holds **zero trading authority** and no holdout reader capability.

Honest termination (parity with ``research.loop``)
--------------------------------------------------
The loop terminates honestly, never a template fallback: a propose/``improve``
failure (``stop_reason="improve_failed"``), an L0 refusal (``"l0_refused"``), a
governor budget exhaustion (``"trial_budget_exhausted"`` / ``"peek_budget_exhausted"``)
and a stall (``"stalled"``) each stop with archived evidence. A gate pass stops at
``PASSED_VALIDATION`` naming the winning candidate; exhaustion selects the best
observed round (first gate-passed — never reached, since a pass exits early — else
max ``rank_ic``), and zero revealed rounds fail with ``"no_revealed_rounds"``.

Persisted maturity wakeup (no busy waiting)
-------------------------------------------
An ``evaluate_validation`` binding whose data has not matured raises
:class:`MaturityPending`; the loop persists ``OptimizeRunState(status=WAITING_FOR_MATURITY,
resume_after, wakeup_key, candidate_hash)`` and returns — the process frees. The
matured :func:`resume_optimize` continues **from the archived round index**, and only
matured batches are processed: the reserved-but-unrevealed trial stays ``reserved``
and the continuation reveals it exactly once. A duplicate wakeup before maturity
returns the same waiting state; after the terminal it rebuilds the persisted result
idempotently (the experiment-head CAS is the concurrency guard).

Sealed-holdout isolation (structural)
-------------------------------------
:func:`finalize_candidate` drives ``PASSED_VALIDATION → SEALED_EVALUATING →
COMPLETED``/``FAILED`` and asks the one-shot sealed evaluator gateway for a holdout
receipt. The *only* holdout fact this module ever holds is the opaque
:class:`~guanlan_v2.orchestration.trial.HoldoutReceipt`: detailed holdout metrics
never enter the returned :class:`~guanlan_v2.orchestration.trial.OptimizeResult`, any
round archive, ``improve``, L3 or memory. This module imports **no** sealed read path
— a module-inspection guard in ``tests/orchestration/test_sealed_holdout.py`` pins
that structurally.

Signature note
--------------
``run_optimize`` freezes the spec parameter names (``seed`` / ``ctx`` / ``study`` /
``split_spec`` / ``max_rounds`` / ``governor`` / ``evaluate_validation`` / ``gate`` /
``improve``) plus keyword-only service dependencies; ``finalize_candidate`` keeps its
two spec-named parameters (``optimized`` / ``sealed_evaluator``) and adds the
keyword-only service dependencies it genuinely needs — the :class:`ExperimentStateStore`
that ran the experiment and the OOT ``window_ref`` to seal against — exactly as
``run_optimize`` threads its service dependencies. The persisted-terminal caches
(results / receipts / candidate refs / the wakeup index) reproduce same-process
semantics with the Phase 2 in-memory backend, the same honest caveat Phase 2/3 and
the Task 5 ledger document.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.digest import DigestHex, content_digest
from guanlan_v2.orchestration.enums import Confidence, ExperimentStatus
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    PayloadPutCommand,
    RuntimeBatch,
    StagedPayloadKey,
    StagedTypedPayloadRef,
    StateCellCompareAndSwapCommand,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.evaluator import (
    l0_candidate_gate,
    l2_govern,
    l3_feedback,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.trial import (
    Feedback,
    GateResult,
    HoldoutReceipt,
    OptimizeCandidate,
    OptimizeResult,
    OptimizeRound,
    OptimizeRunState,
    SplitSpec,
    StudyFamily,
    StudySpec,
    ValidationMetrics,
)
from guanlan_v2.orchestration.trial_ledger import TrialLedger, TrialLedgerError

if TYPE_CHECKING:  # runtime-import-free: keep the optimizer side of the import graph
    from guanlan_v2.orchestration.governor import Governor
    from guanlan_v2.orchestration.sealed import SealedEvaluatorGateway

__all__ = [
    "OPTIMIZE_MAX_ROUNDS",
    "EXPERIMENT_HEAD_NAMESPACE",
    "OptimizeError",
    "MaturityPending",
    "InvalidExperimentTransition",
    "OptimizeRoundStore",
    "PayloadRoundStore",
    "ExperimentStateStore",
    "run_optimize",
    "resume_optimize",
    "finalize_candidate",
]

#: loop-level hard clamp: the *authoritative* round ceiling, applied inside
#: ``run_optimize`` itself (``max_rounds = max(1, min(max_rounds, OPTIMIZE_MAX_ROUNDS))``)
#: so a caller / endpoint that forgot to clamp can never run an unbounded study.
OPTIMIZE_MAX_ROUNDS = 8

#: the Phase-4 recovery-cell namespace the optimize run state is CAS-persisted in
#: (reserved by the Task 5 ledger; this module is its sole writer).
EXPERIMENT_HEAD_NAMESPACE = "trial.experiment_head.v1"

_ES = ExperimentStatus


# --------------------------------------------------------------------------- #
# Exceptions                                                                   #
# --------------------------------------------------------------------------- #
class OptimizeError(Exception):
    """Base for every optimize state-machine failure."""


class MaturityPending(Exception):
    """Raised by an ``evaluate_validation`` binding whose data has not matured.

    Carries the earliest instant a resume may re-attempt (``resume_after``) and the
    idempotent ``wakeup_key`` the persisted :class:`~guanlan_v2.orchestration.trial.OptimizeRunState`
    is located by. The loop persists ``WAITING_FOR_MATURITY`` and returns rather than
    busy-waiting, so the process frees between attempts.
    """

    def __init__(self, *, resume_after: Any, wakeup_key: str) -> None:
        super().__init__(f"data not matured; resume after {resume_after!r} via {wakeup_key!r}")
        self.resume_after = resume_after
        self.wakeup_key = wakeup_key


class InvalidExperimentTransition(OptimizeError):
    """An :class:`ExperimentStateStore.save` was asked for an undeclared edge.

    The frozen transition table (Task 8) admits only the declared edges; any other
    ``(from, to)`` pair is refused before the CAS is even attempted. The
    ``trial.experiment_head.v1`` CAS additionally refuses a concurrent writer that
    advanced the head underneath a declared transition (expected-head mismatch).
    """


# --------------------------------------------------------------------------- #
# Frozen transition table                                                      #
# --------------------------------------------------------------------------- #
#: The only legal ``(from_status, to_status)`` edges; ``None`` is "no prior head".
_LEGAL_TRANSITIONS: frozenset[tuple[ExperimentStatus | None, ExperimentStatus]] = frozenset(
    {
        (None, _ES.RUNNING),
        (_ES.RUNNING, _ES.RUNNING),
        (_ES.RUNNING, _ES.WAITING_FOR_MATURITY),
        (_ES.WAITING_FOR_MATURITY, _ES.RUNNING),
        (_ES.RUNNING, _ES.PASSED_VALIDATION),
        (_ES.RUNNING, _ES.FAILED),
        (_ES.PASSED_VALIDATION, _ES.SEALED_EVALUATING),
        (_ES.SEALED_EVALUATING, _ES.COMPLETED),
        (_ES.SEALED_EVALUATING, _ES.FAILED),
    }
)
_TERMINAL_STATUSES = frozenset({_ES.PASSED_VALIDATION, _ES.COMPLETED, _ES.FAILED})
#: the closed L1 validation-metric field set the loop reveals through the ledger.
_L1_FIELDS = ("rank_ic", "sharpe", "ann_return", "oos_verdict", "n_dates", "factor")


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #
def _SR(name: str) -> SchemaRef:
    return SchemaRef(name=name, version="1")


def _registry_digest(registry: Any) -> DigestHex:
    return registry if isinstance(registry, str) else registry.registry_digest


def _exp_head_key(experiment_id: str) -> DigestHex:
    return content_digest(["trial.experiment_head", experiment_id])


def _template(model: Any) -> dict[str, Any]:
    return {name: getattr(model, name) for name in type(model).model_fields}


def _empty_metrics() -> ValidationMetrics:
    return ValidationMetrics(source="run_graph")


def _stall_feedback() -> Feedback:
    return Feedback(
        target_worker_ids=(),
        evidence_artifact_ids=(),
        reason=(
            "【停滞警告】上一份改进候选与上一轮签名一致,求值确定性下指标必然不变;"
            "必须对图做出实质修改(换表达式/换模型或超参/改合成或回测参数)"
        ),
        confidence=Confidence.LOW,
        ambiguous=True,
        source="deterministic",
    )


# --------------------------------------------------------------------------- #
# Round store                                                                  #
# --------------------------------------------------------------------------- #
@runtime_checkable
class OptimizeRoundStore(Protocol):
    """The append-only archive of one experiment's rounds.

    ``append_round`` raises on failure (loud — unlike the research jsonl store's
    swallow-and-return-bool contract) and is idempotent by ``idempotency_key``: a
    replay with identical content is a no-op, a replay with different content raises.
    ``rounds`` returns the append-ordered tuple; rounds are never rewritten.
    """

    def append_round(self, round: OptimizeRound, *, idempotency_key: str) -> None: ...

    def rounds(self, experiment_id: str) -> tuple[OptimizeRound, ...]: ...


class PayloadRoundStore:
    """Production :class:`OptimizeRoundStore`: persists each round as a ``main``
    payload and keeps the append-ordered archive.

    Each round is a durable ``main`` payload (idempotent by ``candidate_hash`` /
    round index) plus the append-ordered in-process list — an ``ExperimentStateChanged``-correlated
    audit trail is emitted separately by :class:`ExperimentStateStore` per round.
    Append-only: a replayed idempotency key with identical semantic content is a
    no-op; a different content raises (never a silent rewrite).
    """

    def __init__(self, *, payload_store: Any, registry: Any) -> None:
        self._payload_store = payload_store
        self._digest = _registry_digest(registry)
        self._rounds: dict[str, list[OptimizeRound]] = {}
        self._seen: dict[str, dict[str, OptimizeRound]] = {}

    def append_round(self, round: OptimizeRound, *, idempotency_key: str) -> None:
        experiment_id = round.experiment_id
        seen = self._seen.setdefault(experiment_id, {})
        prev = seen.get(idempotency_key)
        if prev is not None:
            if content_digest(prev) != content_digest(round):
                raise OptimizeError(
                    f"round idempotency key {idempotency_key!r} reused with different content"
                )
            return  # append-only idempotent no-op
        self._payload_store.put(
            _SR("OptimizeRound"), round, registry_digest=self._digest,
            namespace="main", idempotency_key=f"round:{experiment_id}:{idempotency_key}",
        )
        self._rounds.setdefault(experiment_id, []).append(round)
        seen[idempotency_key] = round

    def rounds(self, experiment_id: str) -> tuple[OptimizeRound, ...]:
        return tuple(self._rounds.get(experiment_id, ()))


# --------------------------------------------------------------------------- #
# Experiment state store                                                       #
# --------------------------------------------------------------------------- #
class ExperimentStateStore:
    """The persisted experiment run state + terminal-fact caches.

    :meth:`save` commits one :class:`~guanlan_v2.orchestration.eventstore.RuntimeUnitOfWork`:
    an ``OptimizeRunState`` ``main`` payload + an ``ExperimentStateChanged`` ``main``
    event + a ``trial.experiment_head.v1`` CAS, all-or-none. A save is refused unless
    the ``(from, to)`` edge is in the frozen transition table, and the CAS refuses a
    concurrent head advance (expected-head mismatch). :meth:`load` /
    :meth:`load_by_wakeup_key` read the folded head; the terminal-result / receipt /
    candidate-ref caches and the wakeup index reproduce same-process semantics with
    the Phase 2 in-memory backend (the same honest caveat as the Task 5 ledger).
    """

    def __init__(
        self, *, payload_store: Any, state_cells: Any, registry: Any,
        clock: AuthoritativeClock, uow_factory: Callable[[], Any],
    ) -> None:
        self._payload_store = payload_store
        self._state_cells = state_cells
        self._registry = registry
        self._digest = _registry_digest(registry)
        self._clock = clock
        self._uow_factory = uow_factory
        if EXPERIMENT_HEAD_NAMESPACE not in state_cells.allowed_namespaces:
            raise OptimizeError(
                f"state-cell namespace {EXPERIMENT_HEAD_NAMESPACE!r} missing from the sealed store"
            )
        self._by_wakeup: dict[str, str] = {}
        self._results: dict[str, OptimizeResult] = {}
        self._receipts: dict[str, HoldoutReceipt] = {}
        self._cand_by_hash: dict[tuple[str, str], TypedPayloadRef] = {}
        self._cand_by_artifact: dict[tuple[str, str], TypedPayloadRef] = {}

    # -- state persistence -------------------------------------------------- #
    def _resolve(self, ref: TypedPayloadRef | None) -> OptimizeRunState | None:
        if ref is None:
            return None
        return self._payload_store.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    def save(self, state: OptimizeRunState, *, idempotency_key: str) -> None:
        key = _exp_head_key(state.experiment_id)
        current_ref = self._state_cells.load(EXPERIMENT_HEAD_NAMESPACE, key)
        current = self._resolve(current_ref)
        if current is not None and current.semantic_digest() == state.semantic_digest():
            if state.wakeup_key is not None:
                self._by_wakeup[state.wakeup_key] = state.experiment_id
            return  # idempotent no-op: identical state already persisted
        frm = current.status if current is not None else None
        if (frm, state.status) not in _LEGAL_TRANSITIONS:
            raise InvalidExperimentTransition(
                f"illegal experiment transition {frm} -> {state.status} "
                f"for {state.experiment_id!r}"
            )
        batch_key = f"exp-state:{state.experiment_id}:{idempotency_key}"
        batch = RuntimeBatch(
            idempotency_key=batch_key,
            payload_puts=(
                PayloadPutCommand(
                    staged_key=StagedPayloadKey(key="state"),
                    schema_ref=_SR("OptimizeRunState"), namespace="main",
                    payload_template=_template(state), registry_digest=self._digest,
                    idempotency_key=f"{batch_key}:put"),
            ),
            event_appends=(
                EventAppendCommand(
                    run_id=state.experiment_id, partition="main",
                    event_type=EventType.EXPERIMENT_STATE_CHANGED.value,
                    payload_schema_ref=_SR("OptimizeRunState"),
                    payload_target=StagedPayloadKey(key="state"),
                    registry_digest=self._digest, idempotency_key=f"{batch_key}:evt"),
            ),
            cell_cas=(
                StateCellCompareAndSwapCommand(
                    cell_namespace=EXPERIMENT_HEAD_NAMESPACE, cell_key_digest=key,
                    expected_value=current_ref,
                    new_target=StagedTypedPayloadRef(
                        staged_key=StagedPayloadKey(key="state"),
                        schema_ref=_SR("OptimizeRunState"), namespace="main")),
            ),
        )
        self._uow_factory().commit(batch)
        if state.wakeup_key is not None:
            self._by_wakeup[state.wakeup_key] = state.experiment_id

    def load(self, experiment_id: str) -> OptimizeRunState | None:
        ref = self._state_cells.load(EXPERIMENT_HEAD_NAMESPACE, _exp_head_key(experiment_id))
        return self._resolve(ref)

    def load_by_wakeup_key(self, wakeup_key: str) -> OptimizeRunState | None:
        experiment_id = self._by_wakeup.get(wakeup_key)
        if experiment_id is None:
            return None
        return self.load(experiment_id)

    # -- terminal-fact caches ---------------------------------------------- #
    def save_result(self, result: OptimizeResult) -> None:
        self._results[result.state.experiment_id] = result
        self._payload_store.put(
            _SR("OptimizeResult"), result, registry_digest=self._digest,
            namespace="main", idempotency_key=f"opt-result:{result.state.experiment_id}")

    def load_result(self, experiment_id: str) -> OptimizeResult | None:
        return self._results.get(experiment_id)

    def save_receipt(self, experiment_id: str, receipt: HoldoutReceipt) -> None:
        self._receipts[experiment_id] = receipt

    def load_receipt(self, experiment_id: str) -> HoldoutReceipt | None:
        return self._receipts.get(experiment_id)

    def note_candidate(
        self, experiment_id: str, candidate_hash: str, ref: TypedPayloadRef
    ) -> None:
        self._cand_by_hash[(experiment_id, candidate_hash)] = ref
        self._cand_by_artifact[(experiment_id, ref.payload_ref.object_id)] = ref

    def candidate_ref_by_hash(
        self, experiment_id: str, candidate_hash: str
    ) -> TypedPayloadRef | None:
        return self._cand_by_hash.get((experiment_id, candidate_hash))

    def candidate_ref_by_artifact(
        self, experiment_id: str, object_id: str
    ) -> TypedPayloadRef | None:
        return self._cand_by_artifact.get((experiment_id, object_id))


# --------------------------------------------------------------------------- #
# Loop context (bundles the many service dependencies)                          #
# --------------------------------------------------------------------------- #
class _Ctx:
    """Immutable bundle of the resolved loop dependencies (internal)."""

    __slots__ = (
        "ctx", "study", "family", "split_spec", "split_ref", "max_rounds", "governor",
        "evaluate_validation", "gate", "improve", "ledger", "rounds", "states",
        "payload_store", "registry_digest", "experiment_id", "clock",
        "code_prompt_model_hash", "attribution", "goal", "save_epoch",
    )

    def __init__(self, **kw: Any) -> None:
        for name in self.__slots__:
            setattr(self, name, kw[name])

    def now(self) -> Any:
        return clock_now(self.clock)


# --------------------------------------------------------------------------- #
# Public entry points                                                          #
# --------------------------------------------------------------------------- #
def run_optimize(
    *,
    seed: OptimizeCandidate,
    ctx: DataContext,
    study: StudySpec,
    split_spec: SplitSpec,
    max_rounds: int,
    governor: "Governor",
    evaluate_validation: Callable[[OptimizeCandidate, DataContext], ValidationMetrics],
    gate: Callable[[ValidationMetrics], GateResult],
    improve: Callable[[OptimizeCandidate, ValidationMetrics, Feedback], OptimizeCandidate],
    ledger: TrialLedger,
    rounds: OptimizeRoundStore,
    states: ExperimentStateStore,
    payload_store: Any,
    experiment_id: str,
    clock: AuthoritativeClock,
    registry: Any,
    code_prompt_model_hash: DigestHex,
    attribution: Any | None = None,
    goal: str = "",
) -> OptimizeResult:
    """Run the contract-governed Evaluator-Optimizer loop over ``seed``.

    The ``max_rounds`` argument is clamped to :data:`OPTIMIZE_MAX_ROUNDS` inside the
    loop itself (the authoritative ceiling). See the module docstring for the honest
    termination + persisted-maturity semantics.
    """
    existing = states.load(experiment_id)
    if existing is not None and existing.status in _TERMINAL_STATUSES:
        cached = states.load_result(experiment_id)
        if cached is not None:
            return cached  # idempotent: an already-terminal experiment is not re-run

    context = _prepare(
        ctx=ctx, study=study, split_spec=split_spec, max_rounds=max_rounds,
        governor=governor, evaluate_validation=evaluate_validation, gate=gate,
        improve=improve, ledger=ledger, rounds=rounds, states=states,
        payload_store=payload_store, experiment_id=experiment_id, clock=clock,
        registry=registry, code_prompt_model_hash=code_prompt_model_hash,
        attribution=attribution, goal=goal, save_epoch="run")
    return _drive(context, cand=seed, prev_hash=None, last_metrics=None, start_index=0)


def resume_optimize(
    *,
    wakeup_key: str,
    seed: OptimizeCandidate,
    ctx: DataContext,
    study: StudySpec,
    split_spec: SplitSpec,
    max_rounds: int,
    governor: "Governor",
    evaluate_validation: Callable[[OptimizeCandidate, DataContext], ValidationMetrics],
    gate: Callable[[ValidationMetrics], GateResult],
    improve: Callable[[OptimizeCandidate, ValidationMetrics, Feedback], OptimizeCandidate],
    ledger: TrialLedger,
    rounds: OptimizeRoundStore,
    states: ExperimentStateStore,
    payload_store: Any,
    experiment_id: str,
    clock: AuthoritativeClock,
    registry: Any,
    code_prompt_model_hash: DigestHex,
    attribution: Any | None = None,
    goal: str = "",
) -> OptimizeResult:
    """Resume a persisted ``WAITING_FOR_MATURITY`` experiment by its ``wakeup_key``.

    An unknown key raises :class:`~guanlan_v2.orchestration.trial_ledger.TrialLedgerError`.
    A state that is no longer waiting rebuilds and returns the persisted terminal
    result idempotently (no new rounds). Before ``resume_after`` the waiting state is
    returned unchanged. Otherwise the loop continues from the archived round index —
    only matured batches are processed.
    """
    state = states.load_by_wakeup_key(wakeup_key)
    if state is None:
        raise TrialLedgerError(f"unknown wakeup key {wakeup_key!r}")
    if state.status != _ES.WAITING_FOR_MATURITY:
        cached = states.load_result(state.experiment_id)
        if cached is not None:
            return cached
        return OptimizeResult(state=state, stop_reason=None)  # pragma: no cover - defensive
    if clock_now(clock) < state.resume_after:
        return OptimizeResult(state=state)  # waiting unchanged; process frees again

    context = _prepare(
        ctx=ctx, study=study, split_spec=split_spec, max_rounds=max_rounds,
        governor=governor, evaluate_validation=evaluate_validation, gate=gate,
        improve=improve, ledger=ledger, rounds=rounds, states=states,
        payload_store=payload_store, experiment_id=state.experiment_id, clock=clock,
        registry=registry, code_prompt_model_hash=code_prompt_model_hash,
        attribution=attribution, goal=goal, save_epoch="resume")

    archived = rounds.rounds(state.experiment_id)
    start_index = len(archived)
    pending_ref = states.candidate_ref_by_hash(state.experiment_id, state.candidate_hash)
    if pending_ref is not None:
        cand = payload_store.get(
            pending_ref.payload_ref, expected_schema_ref=pending_ref.schema_ref)
    else:
        cand = seed  # first-round maturity: the pending candidate is the seed
    prev_hash = archived[-1].candidate_hash if start_index > 0 else None
    last_metrics = archived[-1].metrics if start_index > 0 else None
    return _drive(context, cand=cand, prev_hash=prev_hash, last_metrics=last_metrics,
                  start_index=start_index)


def finalize_candidate(
    *,
    optimized: OptimizeResult,
    sealed_evaluator: "SealedEvaluatorGateway",
    states: ExperimentStateStore,
    window_ref: TypedPayloadRef,
    idempotency_key: str = "finalize",
) -> HoldoutReceipt:
    """Seal the validated winner against a one-shot OOT holdout window.

    Requires ``optimized.state.status == PASSED_VALIDATION`` (a non-passed result
    raises before any gateway call). Drives ``PASSED_VALIDATION → SEALED_EVALUATING →
    COMPLETED``/``FAILED``, rebuilds the frozen candidate ref from
    ``best_candidate_artifact_id`` (+ the recorded ``OptimizeCandidate@1`` schema),
    then asks the gateway to reserve, lease and evaluate the holdout exactly once. The
    returned :class:`~guanlan_v2.orchestration.trial.HoldoutReceipt` is the only
    holdout fact this module ever holds — no sealed metric enters any round / state /
    result payload. Re-invocation returns the identical receipt without new gateway
    calls (idempotent recovery over the persisted terminal state).
    """
    if optimized.state.status != _ES.PASSED_VALIDATION:
        raise OptimizeError(
            "finalize_candidate requires a PASSED_VALIDATION result; got "
            f"{optimized.state.status}"
        )
    experiment_id = optimized.state.experiment_id
    family_id = optimized.state.family_id

    persisted = states.load(experiment_id)
    if persisted is not None and persisted.status in (_ES.COMPLETED, _ES.FAILED):
        receipt = states.load_receipt(experiment_id)
        if receipt is not None:
            return receipt  # idempotent re-invocation: already finalized, no gateway call

    artifact_id = optimized.best_candidate_artifact_id
    if artifact_id is None:
        raise OptimizeError("PASSED_VALIDATION result carries no best_candidate_artifact_id")
    frozen_candidate_ref = states.candidate_ref_by_artifact(experiment_id, artifact_id)
    if frozen_candidate_ref is None:
        raise OptimizeError(
            f"cannot rebuild the frozen candidate ref for artifact {artifact_id!r}"
        )

    states.save(
        _mk_state(experiment_id, family_id, _ES.SEALED_EVALUATING,
                  clock_now, states, candidate_hash=optimized.state.candidate_hash),
        idempotency_key=f"{experiment_id}:sealed-eval")

    reserved, lease = sealed_evaluator.reserve_and_lease(
        frozen_candidate_ref, window_ref=window_ref,
        idempotency_key=f"{experiment_id}:holdout:{idempotency_key}")
    receipt = sealed_evaluator.evaluate_once(
        frozen_candidate_ref, holdout_reservation_id=reserved.trial_id,
        lease_token=lease)

    terminal = _ES.COMPLETED if receipt.status == "revealed" else _ES.FAILED
    states.save(
        _mk_state(experiment_id, family_id, terminal, clock_now, states,
                  candidate_hash=optimized.state.candidate_hash),
        idempotency_key=f"{experiment_id}:sealed-terminal")
    states.save_receipt(experiment_id, receipt)
    return receipt


# --------------------------------------------------------------------------- #
# Internal loop                                                                #
# --------------------------------------------------------------------------- #
def _prepare(*, ctx, study, split_spec, max_rounds, governor, evaluate_validation,
             gate, improve, ledger, rounds, states, payload_store, experiment_id,
             clock, registry, code_prompt_model_hash, attribution, goal,
             save_epoch) -> _Ctx:
    digest = _registry_digest(registry)
    family = governor.resolve_family(study)
    split_payload_ref = _put(payload_store, digest, "SplitSpec", split_spec,
                             f"opt-split:{experiment_id}")
    split_ref = TypedPayloadRef(schema_ref=_SR("SplitSpec"), payload_ref=split_payload_ref)
    clamped = max(1, min(int(max_rounds), OPTIMIZE_MAX_ROUNDS))
    return _Ctx(
        ctx=ctx, study=study, family=family, split_spec=split_spec, split_ref=split_ref,
        max_rounds=clamped, governor=governor, evaluate_validation=evaluate_validation,
        gate=gate, improve=improve, ledger=ledger, rounds=rounds, states=states,
        payload_store=payload_store, registry_digest=digest, experiment_id=experiment_id,
        clock=clock, code_prompt_model_hash=code_prompt_model_hash,
        attribution=attribution, goal=goal, save_epoch=save_epoch)


def _drive(c: _Ctx, *, cand: OptimizeCandidate, prev_hash: str | None,
           last_metrics: ValidationMetrics | None, start_index: int) -> OptimizeResult:
    validation_trial_ids = _reconstruct_trial_ids(c)

    for round_index in range(start_index, c.max_rounds):
        # 1) stall guard: an identical improve output burns a round for nothing.
        stall_retry = False
        if prev_hash is not None and cand.candidate_hash == prev_hash:
            try:
                cand = c.improve(cand, last_metrics or _empty_metrics(), _stall_feedback())
            except Exception as exc:  # noqa: BLE001 - honest termination, no fallback
                return _fail(c, cand, "improve_failed", validation_trial_ids)
            stall_retry = True
            if cand.candidate_hash == prev_hash:
                _archive(c, round_index, cand.candidate_hash, stall_retry=True,
                         error="停滞中断: 两次改进产出与上一轮候选签名一致,指标不会变")
                return _fail(c, cand, "stalled", validation_trial_ids)

        # 2) persist RUNNING at round start (— / RUNNING / WAITING -> RUNNING).
        _save(c, _ES.RUNNING, candidate_hash=cand.candidate_hash,
              tag=f"r{round_index}:running")

        # 3) persist the candidate payload idempotently by candidate_hash.
        cand_ref = _put_candidate(c, cand)
        cand_artifact_id = cand_ref.payload_ref.object_id
        c.states.note_candidate(c.experiment_id, cand.candidate_hash, cand_ref)

        # 4) L0 honesty gate (pure function of the candidate; no evaluation touched).
        l0 = l0_candidate_gate(cand)
        if not l0.passed:
            _archive(c, round_index, cand.candidate_hash, l0=l0, stall_retry=stall_retry,
                     error="L0 拒绝: " + "; ".join(l0.reasons))
            return _fail(c, cand, "l0_refused", validation_trial_ids)

        # 5) reserve the validation trial (idempotent reuse short-circuits eval).
        reserved = c.ledger.reserve_validation(
            c.study, cand_ref, c.split_ref,
            data_snapshot_hash=c.ctx.data_snapshot_content_digest,
            code_prompt_model_hash=c.code_prompt_model_hash,
            idempotency_key=f"{c.experiment_id}:val:{round_index}:{cand.candidate_hash}")
        trial_id = reserved.trial_id
        eval_error: str | None = None

        if reserved.status == "revealed":
            metrics = _load_metrics(c, reserved)
            metrics_artifact_id = reserved.validation_result_artifact_id
        else:
            try:
                metrics = c.evaluate_validation(cand, c.ctx)
            except MaturityPending as mp:
                _save(c, _ES.WAITING_FOR_MATURITY, candidate_hash=cand.candidate_hash,
                      resume_after=mp.resume_after, wakeup_key=mp.wakeup_key,
                      tag=f"r{round_index}:waiting")
                return OptimizeResult(
                    state=c.states.load(c.experiment_id),
                    validation_trial_ids=tuple(validation_trial_ids))
            except Exception as exc:  # noqa: BLE001 - eval failure is a continue (parity)
                metrics = _empty_metrics()
                eval_error = f"求值失败: {type(exc).__name__}: {exc}"
                metrics_artifact_id = None

            if eval_error is None:
                metrics_ref = _put(c.payload_store, c.registry_digest, "ValidationMetrics",
                                   metrics, f"opt-metrics:{trial_id}")
                metrics_artifact_id = metrics_ref.object_id
                c.ledger.reveal_validation(
                    trial_id, result_artifact_id=metrics_ref.object_id,
                    result_digest=metrics_ref.content_digest)
                validation_trial_ids.append(trial_id)

        if reserved.status == "revealed" and trial_id not in validation_trial_ids:
            validation_trial_ids.append(trial_id)

        # 6) gate.
        g = c.gate(metrics)
        if g.status == "passed" and eval_error is None:
            _archive(c, round_index, cand.candidate_hash, trial_id=trial_id, l0=l0,
                     metrics=metrics, gate=g, stall_retry=stall_retry)
            _save(c, _ES.PASSED_VALIDATION, candidate_hash=cand.candidate_hash,
                  tag=f"r{round_index}:passed")
            result = OptimizeResult(
                state=c.states.load(c.experiment_id),
                best_candidate_artifact_id=cand_artifact_id,
                validation_trial_ids=tuple(validation_trial_ids))
            c.states.save_result(result)
            return result

        # 7) L2 governance + budget stop.
        stats = c.ledger.effective_trial_stats(c.study)
        governance = l2_govern(
            governor=c.governor, family=c.family, stats=stats, returns=None,
            perf_matrix=None, candidate=cand, split_spec=c.split_spec)
        stop = c.governor.should_stop(governance)
        if stop is not None:
            _archive(c, round_index, cand.candidate_hash, trial_id=trial_id, l0=l0,
                     metrics=metrics, gate=g, governance=governance,
                     stall_retry=stall_retry, error=eval_error)
            return _fail(c, cand, stop, validation_trial_ids)

        # 8) L3 attribution feedback + improve (honest termination on improve failure).
        evidence = (metrics_artifact_id,) if metrics_artifact_id is not None else ()
        feedback = l3_feedback(
            goal=c.goal, metrics=metrics, candidate=cand, evidence_artifact_ids=evidence,
            port=c.attribution)
        is_last = round_index == c.max_rounds - 1
        next_cand: OptimizeCandidate | None = None
        improve_error: str | None = None
        if not is_last:
            try:
                next_cand = c.improve(cand, metrics, feedback)
            except Exception as exc:  # noqa: BLE001 - honest termination, no fallback
                improve_error = f"improve 失败: {type(exc).__name__}: {exc}"

        # 9) archive the round (append-only) — all facts produced this round.
        _archive(c, round_index, cand.candidate_hash, trial_id=trial_id, l0=l0,
                 metrics=metrics, gate=g, governance=governance, feedback=feedback,
                 stall_retry=stall_retry, error=eval_error or improve_error)

        if improve_error is not None:
            return _fail(c, cand, "improve_failed", validation_trial_ids)
        if is_last:
            break
        prev_hash, last_metrics, cand = cand.candidate_hash, metrics, next_cand

    # exhaustion: best-round selection over the full append-only archive.
    return _finalize_exhaustion(c, validation_trial_ids)


# --------------------------------------------------------------------------- #
# Loop helpers                                                                 #
# --------------------------------------------------------------------------- #
def _put(payload_store: Any, digest: str, schema: str, model: Any, key: str) -> PayloadRef:
    return payload_store.put(
        _SR(schema), model, registry_digest=digest, namespace="main", idempotency_key=key)


def _put_candidate(c: _Ctx, cand: OptimizeCandidate) -> TypedPayloadRef:
    ref = _put(c.payload_store, c.registry_digest, "OptimizeCandidate", cand,
               f"opt-cand:{cand.candidate_hash}")
    return TypedPayloadRef(schema_ref=_SR("OptimizeCandidate"), payload_ref=ref)


def _load_metrics(c: _Ctx, record: Any) -> ValidationMetrics:
    ref = PayloadRef(
        namespace="main", object_id=record.validation_result_artifact_id,
        content_digest=record.result_digest)
    return c.payload_store.get(ref, expected_schema_ref=_SR("ValidationMetrics"))


def _reconstruct_trial_ids(c: _Ctx) -> list[str]:
    """The revealed validation trial ids already archived (for the resume continuation)."""
    out: list[str] = []
    for r in c.rounds.rounds(c.experiment_id):
        if r.trial_id is not None and r.metrics is not None and r.gate is not None \
                and r.error is None:
            out.append(r.trial_id)
    return out


def _mk_state(experiment_id: str, family_id: str, status: ExperimentStatus,
              clock_fn, states: ExperimentStateStore, *, candidate_hash=None,
              resume_after=None, wakeup_key=None) -> OptimizeRunState:
    return OptimizeRunState(
        experiment_id=experiment_id, family_id=family_id, status=status,
        candidate_hash=candidate_hash, resume_after=resume_after, wakeup_key=wakeup_key,
        updated_at=clock_fn(states._clock))


def _save(c: _Ctx, status: ExperimentStatus, *, candidate_hash=None, resume_after=None,
          wakeup_key=None, tag: str) -> None:
    state = OptimizeRunState(
        experiment_id=c.experiment_id, family_id=c.family.family_id, status=status,
        candidate_hash=candidate_hash, resume_after=resume_after, wakeup_key=wakeup_key,
        updated_at=c.now())
    c.states.save(state, idempotency_key=f"{c.save_epoch}:{tag}")


def _archive(c: _Ctx, round_index: int, candidate_hash: str, *, trial_id=None, l0=None,
             metrics=None, gate=None, governance=None, feedback=None,
             stall_retry: bool = False, error: str | None = None) -> None:
    c.rounds.append_round(
        OptimizeRound(
            experiment_id=c.experiment_id, round_index=round_index,
            candidate_hash=candidate_hash, trial_id=trial_id, l0=l0, metrics=metrics,
            gate=gate, governance=governance, feedback=feedback, stall_retry=stall_retry,
            error=error, created_at=c.now()),
        idempotency_key=f"r{round_index}")


def _fail(c: _Ctx, cand: OptimizeCandidate | None, stop_reason: str,
          validation_trial_ids: list[str], *, best_artifact_id: str | None = None
          ) -> OptimizeResult:
    _save(c, _ES.FAILED, candidate_hash=(cand.candidate_hash if cand else None),
          tag=f"fail:{stop_reason}")
    result = OptimizeResult(
        state=c.states.load(c.experiment_id),
        best_candidate_artifact_id=best_artifact_id,
        validation_trial_ids=tuple(validation_trial_ids), stop_reason=stop_reason)
    c.states.save_result(result)
    return result


def _finalize_exhaustion(c: _Ctx, validation_trial_ids: list[str]) -> OptimizeResult:
    best = _best_round(c)
    if best is None:
        return _fail(c, None, "no_revealed_rounds", validation_trial_ids)
    best_hash, best_artifact_id = best
    # exhaustion without a gate pass is an honest FAILED that still surfaces the best
    # observed candidate for the human reviewer.
    _save(c, _ES.FAILED, candidate_hash=best_hash, tag="exhausted")
    result = OptimizeResult(
        state=c.states.load(c.experiment_id), best_candidate_artifact_id=best_artifact_id,
        validation_trial_ids=tuple(validation_trial_ids),
        stop_reason="exhausted_no_validation_pass")
    c.states.save_result(result)
    return result


def _best_round(c: _Ctx) -> tuple[str, str] | None:
    """Best archived round: gate-passed wins outright, else max ``rank_ic``."""
    best: tuple[float, str, str] | None = None
    for r in c.rounds.rounds(c.experiment_id):
        if r.error is not None or r.metrics is None or r.gate is None:
            continue
        ric = r.metrics.rank_ic
        if ric is None:
            continue
        ref = c.states.candidate_ref_by_hash(c.experiment_id, r.candidate_hash)
        if ref is None:
            continue
        artifact_id = ref.payload_ref.object_id
        if r.gate.status == "passed":
            return r.candidate_hash, artifact_id
        if best is None or ric > best[0]:
            best = (ric, r.candidate_hash, artifact_id)
    if best is None:
        return None
    return best[1], best[2]
