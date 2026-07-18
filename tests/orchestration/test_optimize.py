# -*- coding: utf-8 -*-
"""Task 8 — the optimize state machine (``optimize.py``).

Written test-first: with ``optimize.py`` absent these tests are RED on the missing
module (not a collection error elsewhere). They exercise the eight brief invariants
plus the six extra step-1 items over the real Phase 2 in-memory stores
(``RuntimeStores``), the Task 5 :class:`TrialLedger`, the Task 4 :class:`Governor`,
the Task 7 four-layer evaluator seams and the Task 6 sealed evaluator gateway, with
a deterministic clock and scripted ``evaluate_validation`` / ``gate`` / ``improve``
callables:

1. scripted three-round convergence (weak → weak → pass);
2. stall guard (identical improve output → one ``stall_retry`` round then stalled);
3. idempotent reuse (replay invokes the eval callable zero times);
4. ``MaturityPending`` persists ``WAITING_FOR_MATURITY`` and the matured resume
   reveals the reserved trial exactly once;
5. governor budget exhaustion stops before another evaluation;
6. improve failure and L0 refusal terminate honestly with archived evidence;
7. ``max_rounds=99`` clamped to ``OPTIMIZE_MAX_ROUNDS`` inside the loop;
8. ``finalize_candidate`` on a non-passed result raises before any gateway call; on
   a passed result the receipt is returned and no sealed metric value appears in any
   round / state / result payload.

Plus: the full transition table (every legal edge + one illegal ``COMPLETED →
RUNNING``), append-only round archives, gate ``unavailable`` archived distinctly,
candidate payload idempotency, and ``finalize_candidate`` drive + idempotent
re-invocation with exactly one gateway ``reserve_and_lease`` / ``evaluate_once``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import DataBackend, DataMode, ExperimentStatus
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.governor import Governor, derive_study_family
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry
from guanlan_v2.orchestration.sealed import SealedEvaluatorGateway, SealedResultStore
from guanlan_v2.orchestration.trial import (
    Feedback,
    GateResult,
    HoldoutReceipt,
    HoldoutWindow,
    OptimizeCandidate,
    OptimizeResult,
    OptimizeRound,
    OptimizeRunState,
    SealedEvaluationRecord,
    SplitSpec,
    StudyFamily,
    StudySpec,
    TrialRecord,
    ValidationMetrics,
)
from guanlan_v2.orchestration.trial import HoldoutReceipt as _HR  # noqa: F401
from guanlan_v2.orchestration.trial_ledger import (
    PHASE4_STATE_CELL_NAMESPACES,
    TrialLedger,
    compute_holdout_window_attestation,
)
from guanlan_v2.orchestration.optimize import (
    OPTIMIZE_MAX_ROUNDS,
    ExperimentStateStore,
    InvalidExperimentTransition,
    MaturityPending,
    OptimizeError,
    PayloadRoundStore,
    finalize_candidate,
    resume_optimize,
    run_optimize,
)

UTC = timezone.utc
DH = "d" * 64
CH = "c" * 64


def _SR(name: str) -> SchemaRef:
    return SchemaRef(name=name, version="1")


class SteppingClock:
    """Deterministic advancing clock (+1s per ``now()``; ``current`` is settable)."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 7, 17, 1, 0, tzinfo=UTC)

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


def _registry() -> Phase2RuntimeRegistry:
    reg = Phase2RuntimeRegistry()
    for model in (
        StudySpec, StudyFamily, SplitSpec, OptimizeCandidate, ValidationMetrics,
        GateResult, Feedback, HoldoutWindow, TrialRecord, OptimizeRound,
        OptimizeRunState, OptimizeResult, HoldoutReceipt, SealedEvaluationRecord,
    ):
        reg.register(model)
    reg.seal()
    return reg


def _study(*, objective: str = "find alpha", odigest: str = "a" * 64) -> StudySpec:
    return StudySpec(
        objective=objective, objective_digest=odigest,
        label_definition="20d fwd return", label_digest="b" * 64,
        universe_digest="e" * 64, frequency="monthly", split_policy_digest="f" * 64)


def _ctx() -> DataContext:
    from guanlan_v2.orchestration.context import ClockSpec

    as_of = datetime(2026, 6, 30, tzinfo=UTC)
    clock = ClockSpec(as_of=as_of, timezone="Asia/Shanghai", calendar_id="XSHG")
    return DataContext(
        as_of=as_of, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.CACHE,
        strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
        source_config_digest="1" * 64, source_registry_digest="2" * 64,
        routing_snapshot_digest="3" * 64, data_snapshot_id="snap-1",
        data_snapshot_content_digest="7" * 64, built_at=as_of)


def _cand(expr: str, *, param: int = 0) -> OptimizeCandidate:
    return OptimizeCandidate.build(
        candidate_kind="workflow_graph",
        graph={"nodes": [{"id": "a", "type": "formula", "params": {"expr": expr}}],
               "edges": []},
        params={"seed": param})


def _weak() -> ValidationMetrics:
    return ValidationMetrics(source="run_graph", rank_ic=0.01, sharpe=0.2,
                            oos_verdict="degraded")


def _pass() -> ValidationMetrics:
    return ValidationMetrics(source="run_graph", rank_ic=0.2, sharpe=1.5,
                            oos_verdict="robust")


def _gate(metrics: ValidationMetrics) -> GateResult:
    """Scripted joint gate: pass iff rank_ic>=0.05 and oos robust and sharpe present."""
    if metrics.rank_ic is None and metrics.sharpe is None and metrics.oos_verdict is None:
        return GateResult(status="unavailable", min_rank_ic=0.05,
                          reason="metrics all-None: gate cannot decide")
    passed = (
        metrics.rank_ic is not None and metrics.rank_ic >= 0.05
        and metrics.oos_verdict == "robust" and metrics.sharpe is not None
    )
    return GateResult(
        status="passed" if passed else "failed", min_rank_ic=0.05,
        observed_rank_ic=metrics.rank_ic, observed_sharpe=metrics.sharpe,
        observed_oos_verdict=metrics.oos_verdict,
        reason="joint gate passed" if passed else "joint gate not met")


class _Env:
    def __init__(self) -> None:
        self.resolver = SchemaRegistryResolver()
        self.registry = _registry()
        self.digest = self.resolver.register(self.registry)
        self.clock = SteppingClock()
        self.stores = RuntimeStores(
            resolver=self.resolver, clock=self.clock,
            allowed_cell_namespaces=PHASE4_STATE_CELL_NAMESPACES)
        self._seq = 0

    def _id_factory(self) -> str:
        v = f"tok{self._seq}"
        self._seq += 1
        return v

    def ledger(self) -> TrialLedger:
        return TrialLedger(
            event_store=self.stores.events, payload_store=self.stores.payloads,
            state_cells=self.stores.cells, registry=self.registry, clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work, id_factory=self._id_factory)

    def states(self) -> ExperimentStateStore:
        return ExperimentStateStore(
            payload_store=self.stores.payloads, state_cells=self.stores.cells,
            registry=self.registry, clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work)

    def rounds(self) -> PayloadRoundStore:
        return PayloadRoundStore(
            payload_store=self.stores.payloads, registry=self.registry)


class ScriptedEval:
    """Eval keyed by candidate_hash; records fresh-eval call count."""

    def __init__(self, by_hash) -> None:
        self.by_hash = by_hash
        self.calls = 0

    def __call__(self, cand: OptimizeCandidate, ctx: DataContext) -> ValidationMetrics:
        self.calls += 1
        out = self.by_hash[cand.candidate_hash]
        if isinstance(out, BaseException):
            raise out
        return out


class ChainImprove:
    """Deterministic candidate chain: improve(cand)->next; exhausted->identical."""

    def __init__(self, chain) -> None:
        self.chain = chain
        self.calls = 0

    def __call__(self, cand, metrics, feedback) -> OptimizeCandidate:
        self.calls += 1
        for i, c in enumerate(self.chain):
            if c.candidate_hash == cand.candidate_hash and i + 1 < len(self.chain):
                return self.chain[i + 1]
        return cand


def _run(env, *, seed, study, eval_cb, gate_cb, improve_cb, governor, states,
         rounds, experiment_id, max_rounds=8, ledger=None):
    return run_optimize(
        seed=seed, ctx=_ctx(), study=study, split_spec=SplitSpec(
            scheme="oos_fraction", oos_frac=0.3, label_horizon=5),
        max_rounds=max_rounds, governor=governor, evaluate_validation=eval_cb,
        gate=gate_cb, improve=improve_cb, ledger=ledger or env.ledger(),
        rounds=rounds, states=states, payload_store=env.stores.payloads,
        experiment_id=experiment_id, clock=env.clock, registry=env.registry,
        code_prompt_model_hash=CH)


# --------------------------------------------------------------------------- #
# module constants                                                             #
# --------------------------------------------------------------------------- #
def test_max_rounds_constant():
    assert OPTIMIZE_MAX_ROUNDS == 8


# --------------------------------------------------------------------------- #
# invariant 1: scripted three-round convergence                                #
# --------------------------------------------------------------------------- #
def test_three_round_convergence():
    env = _Env()
    c0, c1, c2 = _cand("x"), _cand("y"), _cand("z")
    ev = ScriptedEval({c0.candidate_hash: _weak(), c1.candidate_hash: _weak(),
                       c2.candidate_hash: _pass()})
    imp = ChainImprove([c0, c1, c2])
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()
    study = _study()

    res = _run(env, seed=c0, study=study, eval_cb=ev, gate_cb=_gate, improve_cb=imp,
               governor=gov, states=states, rounds=rounds, experiment_id="exp1")

    assert res.state.status == ExperimentStatus.PASSED_VALIDATION
    assert res.best_candidate_artifact_id is not None
    archived = rounds.rounds("exp1")
    assert len(archived) == 3
    assert len(res.validation_trial_ids) == 3
    assert ev.calls == 3
    # the winning artifact id is the round-2 candidate's payload object id.
    led = env.ledger()
    assert led.effective_trial_stats(study)["raw_trial_count"] == 3
    # best id resolves to c2's persisted candidate.
    winner = env.stores.payloads.get(
        PayloadRef(namespace="main", object_id=res.best_candidate_artifact_id,
                   content_digest=content_digest(c2)),
        expected_schema_ref=_SR("OptimizeCandidate"))
    assert winner.candidate_hash == c2.candidate_hash
    # the passing round archives its gate as passed.
    assert archived[-1].gate is not None and archived[-1].gate.status == "passed"


# --------------------------------------------------------------------------- #
# invariant 2: stall guard                                                     #
# --------------------------------------------------------------------------- #
def test_stall_guard_one_retry_then_stalled():
    env = _Env()
    c0 = _cand("x")
    # improve always returns the identical seed -> stall at round 1.
    ev = ScriptedEval({c0.candidate_hash: _weak()})
    imp = ChainImprove([c0])  # improve(c0) -> c0 (identical, exhausted chain)
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()

    res = _run(env, seed=c0, study=_study(), eval_cb=ev, gate_cb=_gate,
               improve_cb=imp, governor=gov, states=states, rounds=rounds,
               experiment_id="exp1")

    assert res.state.status == ExperimentStatus.FAILED
    assert res.stop_reason == "stalled"
    archived = rounds.rounds("exp1")
    stall_rounds = [r for r in archived if r.stall_retry]
    assert len(stall_rounds) == 1


def test_param_only_change_is_not_a_stall():
    env = _Env()
    c0 = _cand("x", param=0)
    c1 = _cand("x", param=1)  # same node id, different params -> different hash
    c2 = _cand("x", param=2)
    assert c0.candidate_hash != c1.candidate_hash
    ev = ScriptedEval({c0.candidate_hash: _weak(), c1.candidate_hash: _weak(),
                       c2.candidate_hash: _pass()})
    imp = ChainImprove([c0, c1, c2])
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()

    res = _run(env, seed=c0, study=_study(), eval_cb=ev, gate_cb=_gate,
               improve_cb=imp, governor=gov, states=states, rounds=rounds,
               experiment_id="exp1")
    # a param-only change does NOT count as a stall; the loop converges.
    assert res.state.status == ExperimentStatus.PASSED_VALIDATION
    assert not any(r.stall_retry for r in rounds.rounds("exp1"))


# --------------------------------------------------------------------------- #
# invariant 3: idempotent reuse — replay invokes the eval callable zero times   #
# --------------------------------------------------------------------------- #
def test_idempotent_reuse_replay_zero_eval_calls():
    env = _Env()
    c0, c1, c2 = _cand("x"), _cand("y"), _cand("z")
    ev = ScriptedEval({c0.candidate_hash: _weak(), c1.candidate_hash: _weak(),
                       c2.candidate_hash: _pass()})
    imp = ChainImprove([c0, c1, c2])
    gov = Governor(trial_budget=50, peek_budget=50)
    study = _study()

    # first run over a shared ledger.
    res1 = _run(env, seed=c0, study=study, eval_cb=ev, gate_cb=_gate, improve_cb=imp,
                governor=gov, states=env.states(), rounds=env.rounds(),
                experiment_id="exp1")
    assert res1.state.status == ExperimentStatus.PASSED_VALIDATION
    assert ev.calls == 3
    raw_before = env.ledger().effective_trial_stats(study)["raw_trial_count"]

    # replay over the SAME ledger/payload stores (a second experiment in the same
    # study family): every revealed triple is reused cross-experiment, so the eval
    # callable is never re-invoked and the raw trial count does not grow.
    res2 = _run(env, seed=c0, study=study, eval_cb=ev, gate_cb=_gate, improve_cb=imp,
                governor=gov, states=env.states(), rounds=env.rounds(),
                experiment_id="exp2")
    assert res2.state.status == ExperimentStatus.PASSED_VALIDATION
    assert ev.calls == 3  # zero new evaluations on the replay run
    raw_after = env.ledger().effective_trial_stats(study)["raw_trial_count"]
    assert raw_after == raw_before  # raw_trial_count did not grow


# --------------------------------------------------------------------------- #
# invariant 4: MaturityPending + matured resume                                 #
# --------------------------------------------------------------------------- #
class MaturingEval:
    """Raises MaturityPending on the first call for the seed, then matures."""

    def __init__(self, resume_after, wakeup_key, matured) -> None:
        self.resume_after = resume_after
        self.wakeup_key = wakeup_key
        self.matured = matured
        self.calls = 0

    def __call__(self, cand, ctx):
        self.calls += 1
        if self.calls == 1:
            raise MaturityPending(resume_after=self.resume_after,
                                  wakeup_key=self.wakeup_key)
        return self.matured


def test_maturity_pending_persists_waiting_and_resume_reveals_once():
    env = _Env()
    c0 = _cand("x")
    resume_after = datetime(2026, 8, 1, tzinfo=UTC)
    ev = MaturingEval(resume_after, "wk-1", _pass())
    imp = ChainImprove([c0])
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()
    study, fam = _study(), derive_study_family(_study())

    res = _run(env, seed=c0, study=study, eval_cb=ev, gate_cb=_gate, improve_cb=imp,
               governor=gov, states=states, rounds=rounds, experiment_id="exp1")

    assert res.state.status == ExperimentStatus.WAITING_FOR_MATURITY
    assert res.state.resume_after == resume_after
    assert res.state.wakeup_key == "wk-1"
    # the reserved trial is not yet revealed: one TrialReserved, no TrialRevealed.
    main = env.stores.events.journal(fam.family_id, "main")
    assert sum(1 for e in main if e.event_type is EventType.TRIAL_RESERVED) == 1
    assert sum(1 for e in main if e.event_type is EventType.TRIAL_REVEALED) == 0
    assert rounds.rounds("exp1") == ()  # MaturityPending archives no round

    # duplicate resume BEFORE maturity returns the waiting state unchanged.
    env.clock.current = datetime(2026, 7, 20, tzinfo=UTC)
    waiting = resume_optimize(
        wakeup_key="wk-1", seed=c0, ctx=_ctx(), study=study,
        split_spec=SplitSpec(scheme="oos_fraction", oos_frac=0.3, label_horizon=5),
        max_rounds=8, governor=gov, evaluate_validation=ev, gate=_gate, improve=imp,
        ledger=env.ledger(), rounds=rounds, states=states,
        payload_store=env.stores.payloads, experiment_id="exp1", clock=env.clock,
        registry=env.registry, code_prompt_model_hash=CH)
    assert waiting.state.status == ExperimentStatus.WAITING_FOR_MATURITY
    assert ev.calls == 1  # not re-evaluated before maturity

    # after maturity, exactly one continuation reveals the reserved trial once.
    env.clock.current = datetime(2026, 8, 2, tzinfo=UTC)
    done = resume_optimize(
        wakeup_key="wk-1", seed=c0, ctx=_ctx(), study=study,
        split_spec=SplitSpec(scheme="oos_fraction", oos_frac=0.3, label_horizon=5),
        max_rounds=8, governor=gov, evaluate_validation=ev, gate=_gate, improve=imp,
        ledger=env.ledger(), rounds=rounds, states=states,
        payload_store=env.stores.payloads, experiment_id="exp1", clock=env.clock,
        registry=env.registry, code_prompt_model_hash=CH)
    assert done.state.status == ExperimentStatus.PASSED_VALIDATION
    assert ev.calls == 2  # one MaturityPending + one matured evaluation
    main = env.stores.events.journal(fam.family_id, "main")
    assert sum(1 for e in main if e.event_type is EventType.TRIAL_REVEALED) == 1

    # a duplicate resume after the terminal returns the identical result, no new rounds.
    n_rounds = len(rounds.rounds("exp1"))
    again = resume_optimize(
        wakeup_key="wk-1", seed=c0, ctx=_ctx(), study=study,
        split_spec=SplitSpec(scheme="oos_fraction", oos_frac=0.3, label_horizon=5),
        max_rounds=8, governor=gov, evaluate_validation=ev, gate=_gate, improve=imp,
        ledger=env.ledger(), rounds=rounds, states=states,
        payload_store=env.stores.payloads, experiment_id="exp1", clock=env.clock,
        registry=env.registry, code_prompt_model_hash=CH)
    assert again.state.status == ExperimentStatus.PASSED_VALIDATION
    assert again.best_candidate_artifact_id == done.best_candidate_artifact_id
    assert ev.calls == 2  # no further evaluation
    assert len(rounds.rounds("exp1")) == n_rounds


def test_resume_unknown_wakeup_key_raises():
    env = _Env()
    from guanlan_v2.orchestration.trial_ledger import TrialLedgerError

    with pytest.raises(TrialLedgerError):
        resume_optimize(
            wakeup_key="nope", seed=_cand("x"), ctx=_ctx(), study=_study(),
            split_spec=SplitSpec(scheme="oos_fraction", oos_frac=0.3, label_horizon=5),
            max_rounds=8, governor=Governor(trial_budget=5, peek_budget=5),
            evaluate_validation=ScriptedEval({}), gate=_gate, improve=ChainImprove([]),
            ledger=env.ledger(), rounds=env.rounds(), states=env.states(),
            payload_store=env.stores.payloads, experiment_id="exp1", clock=env.clock,
            registry=env.registry, code_prompt_model_hash=CH)


# --------------------------------------------------------------------------- #
# invariant 5: governor budget exhaustion stops before another evaluation       #
# --------------------------------------------------------------------------- #
def test_budget_exhaustion_stops_before_next_eval():
    env = _Env()
    c0, c1 = _cand("x"), _cand("y")
    ev = ScriptedEval({c0.candidate_hash: _weak(), c1.candidate_hash: _weak()})
    imp = ChainImprove([c0, c1])
    gov = Governor(trial_budget=1, peek_budget=50)  # one raw trial allowed
    states, rounds = env.states(), env.rounds()

    res = _run(env, seed=c0, study=_study(), eval_cb=ev, gate_cb=_gate,
               improve_cb=imp, governor=gov, states=states, rounds=rounds,
               experiment_id="exp1")
    assert res.state.status == ExperimentStatus.FAILED
    assert res.stop_reason == "trial_budget_exhausted"
    assert ev.calls == 1  # the second round's evaluation never ran


# --------------------------------------------------------------------------- #
# invariant 6: improve failure and L0 refusal terminate honestly               #
# --------------------------------------------------------------------------- #
def test_improve_failure_terminates_honestly():
    env = _Env()
    c0 = _cand("x")

    class BoomImprove:
        def __call__(self, cand, metrics, feedback):
            raise RuntimeError("improve crashed")

    ev = ScriptedEval({c0.candidate_hash: _weak()})
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()
    res = _run(env, seed=c0, study=_study(), eval_cb=ev, gate_cb=_gate,
               improve_cb=BoomImprove(), governor=gov, states=states, rounds=rounds,
               experiment_id="exp1")
    assert res.state.status == ExperimentStatus.FAILED
    assert res.stop_reason == "improve_failed"
    # the round with archived evidence exists (no fabricated fallback candidate).
    assert len(rounds.rounds("exp1")) == 1


def test_l0_refusal_terminates_with_archived_evidence():
    env = _Env()
    # a structurally-broken seed (no nodes) is L0-refused before any evaluation.
    seed = OptimizeCandidate.build(
        candidate_kind="workflow_graph", graph={"nodes": [], "edges": []}, params={})
    ev = ScriptedEval({})  # never called
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()
    res = _run(env, seed=seed, study=_study(), eval_cb=ev, gate_cb=_gate,
               improve_cb=ChainImprove([seed]), governor=gov, states=states,
               rounds=rounds, experiment_id="exp1")
    assert res.state.status == ExperimentStatus.FAILED
    assert res.stop_reason == "l0_refused"
    assert ev.calls == 0
    archived = rounds.rounds("exp1")
    assert len(archived) == 1
    assert archived[0].l0 is not None and archived[0].l0.passed is False


# --------------------------------------------------------------------------- #
# invariant 7: max_rounds clamp                                                #
# --------------------------------------------------------------------------- #
def test_max_rounds_clamped_to_optimize_max_rounds():
    env = _Env()
    # a never-passing, never-stalling loop: each round a fresh candidate.
    chain = [_cand("n", param=i) for i in range(30)]
    ev = ScriptedEval({c.candidate_hash: _weak() for c in chain})
    imp = ChainImprove(chain)
    gov = Governor(trial_budget=999, peek_budget=999)
    states, rounds = env.states(), env.rounds()
    res = _run(env, seed=chain[0], study=_study(), eval_cb=ev, gate_cb=_gate,
               improve_cb=imp, governor=gov, states=states, rounds=rounds,
               experiment_id="exp1", max_rounds=99)
    # the loop-level hard clamp caps rounds at OPTIMIZE_MAX_ROUNDS regardless of 99.
    assert len(rounds.rounds("exp1")) == OPTIMIZE_MAX_ROUNDS
    assert ev.calls == OPTIMIZE_MAX_ROUNDS
    assert res.state.status == ExperimentStatus.FAILED


# --------------------------------------------------------------------------- #
# extra 1: full transition table + one illegal edge                            #
# --------------------------------------------------------------------------- #
def _mk_state(exp, fam, status, **kw):
    return OptimizeRunState(
        experiment_id=exp, family_id=fam.family_id, status=status,
        updated_at=datetime(2026, 7, 17, 2, 0, tzinfo=UTC), **kw)


@pytest.mark.parametrize("frm,to,extra", [
    (None, ExperimentStatus.RUNNING, {}),
    (ExperimentStatus.RUNNING, ExperimentStatus.RUNNING, {}),
    (ExperimentStatus.RUNNING, ExperimentStatus.WAITING_FOR_MATURITY,
     {"resume_after": datetime(2026, 8, 1, tzinfo=UTC), "wakeup_key": "wk"}),
    (ExperimentStatus.WAITING_FOR_MATURITY, ExperimentStatus.RUNNING, {}),
    (ExperimentStatus.RUNNING, ExperimentStatus.PASSED_VALIDATION, {}),
    (ExperimentStatus.RUNNING, ExperimentStatus.FAILED, {}),
    (ExperimentStatus.PASSED_VALIDATION, ExperimentStatus.SEALED_EVALUATING, {}),
    (ExperimentStatus.SEALED_EVALUATING, ExperimentStatus.COMPLETED, {}),
    (ExperimentStatus.SEALED_EVALUATING, ExperimentStatus.FAILED, {}),
])
def test_state_store_legal_transitions(frm, to, extra):
    env = _Env()
    states = env.states()
    fam = derive_study_family(_study())
    exp = "expT"
    # drive the head to `frm` (if any) via a legal path, then assert `to` is accepted.
    _drive_to(states, exp, fam, frm)
    # WAITING requires resume_after/wakeup_key on the WAITING state itself.
    if frm == ExperimentStatus.WAITING_FOR_MATURITY:
        pass
    to_kw = dict(extra)
    states.save(_mk_state(exp, fam, to, **to_kw), idempotency_key=f"to:{to.value}")
    assert states.load(exp).status == to


def _drive_to(states, exp, fam, target):
    """Persist a minimal legal path from — up to (and including) `target`."""
    if target is None:
        return
    path = {
        ExperimentStatus.RUNNING: [ExperimentStatus.RUNNING],
        ExperimentStatus.WAITING_FOR_MATURITY: [
            ExperimentStatus.RUNNING, ExperimentStatus.WAITING_FOR_MATURITY],
        ExperimentStatus.PASSED_VALIDATION: [
            ExperimentStatus.RUNNING, ExperimentStatus.PASSED_VALIDATION],
        ExperimentStatus.SEALED_EVALUATING: [
            ExperimentStatus.RUNNING, ExperimentStatus.PASSED_VALIDATION,
            ExperimentStatus.SEALED_EVALUATING],
        ExperimentStatus.COMPLETED: [
            ExperimentStatus.RUNNING, ExperimentStatus.PASSED_VALIDATION,
            ExperimentStatus.SEALED_EVALUATING, ExperimentStatus.COMPLETED],
        ExperimentStatus.FAILED: [ExperimentStatus.RUNNING, ExperimentStatus.FAILED],
    }[target]
    for i, st in enumerate(path):
        kw = {}
        if st == ExperimentStatus.WAITING_FOR_MATURITY:
            kw = {"resume_after": datetime(2026, 8, 1, tzinfo=UTC), "wakeup_key": "wk"}
        states.save(_mk_state(exp, fam, st, **kw), idempotency_key=f"drive:{i}:{st.value}")


def test_state_store_illegal_transition_rejected():
    env = _Env()
    states = env.states()
    fam = derive_study_family(_study())
    exp = "expX"
    _drive_to(states, exp, fam, ExperimentStatus.COMPLETED)
    with pytest.raises(InvalidExperimentTransition):
        states.save(_mk_state(exp, fam, ExperimentStatus.RUNNING),
                    idempotency_key="illegal")


# --------------------------------------------------------------------------- #
# extra 2: append-only round archives (stable across duplicate resume)          #
# --------------------------------------------------------------------------- #
def test_round_archive_append_only_across_duplicate_resume():
    env = _Env()
    c0 = _cand("x")
    resume_after = datetime(2026, 8, 1, tzinfo=UTC)
    ev = MaturingEval(resume_after, "wk-1", _pass())
    imp = ChainImprove([c0])
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()
    study = _study()
    _run(env, seed=c0, study=study, eval_cb=ev, gate_cb=_gate, improve_cb=imp,
         governor=gov, states=states, rounds=rounds, experiment_id="exp1")
    env.clock.current = datetime(2026, 8, 2, tzinfo=UTC)
    kw = dict(seed=c0, ctx=_ctx(), study=study, split_spec=SplitSpec(
        scheme="oos_fraction", oos_frac=0.3, label_horizon=5), max_rounds=8,
        governor=gov, evaluate_validation=ev, gate=_gate, improve=imp,
        ledger=env.ledger(), rounds=rounds, states=states,
        payload_store=env.stores.payloads, experiment_id="exp1", clock=env.clock,
        registry=env.registry, code_prompt_model_hash=CH)
    resume_optimize(wakeup_key="wk-1", **kw)
    before = rounds.rounds("exp1")
    resume_optimize(wakeup_key="wk-1", **kw)  # duplicate resume after terminal
    after = rounds.rounds("exp1")
    assert after == before  # append-only: no round rewritten or duplicated


# --------------------------------------------------------------------------- #
# extra 3: gate unavailable archived distinctly, not a pass                     #
# --------------------------------------------------------------------------- #
def test_gate_unavailable_archived_distinctly_not_passed():
    env = _Env()
    c0, c1 = _cand("x"), _cand("y")
    empty = ValidationMetrics(source="run_graph")  # all-None -> gate unavailable
    ev = ScriptedEval({c0.candidate_hash: empty, c1.candidate_hash: _pass()})
    imp = ChainImprove([c0, c1])
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()
    res = _run(env, seed=c0, study=_study(), eval_cb=ev, gate_cb=_gate,
               improve_cb=imp, governor=gov, states=states, rounds=rounds,
               experiment_id="exp1")
    archived = rounds.rounds("exp1")
    assert archived[0].gate is not None and archived[0].gate.status == "unavailable"
    # unavailable did not count as a pass: the loop continued to round 1 (which passes).
    assert res.state.status == ExperimentStatus.PASSED_VALIDATION
    assert len(archived) == 2


# --------------------------------------------------------------------------- #
# extra: candidate payload idempotency across a stall retry                     #
# --------------------------------------------------------------------------- #
def test_candidate_payload_idempotent_across_stall_retry():
    env = _Env()
    c0 = _cand("x")
    ev = ScriptedEval({c0.candidate_hash: _weak()})
    imp = ChainImprove([c0])
    gov = Governor(trial_budget=50, peek_budget=50)
    states, rounds = env.states(), env.rounds()
    before = env.stores.payloads_object_count()
    _run(env, seed=c0, study=_study(), eval_cb=ev, gate_cb=_gate, improve_cb=imp,
         governor=gov, states=states, rounds=rounds, experiment_id="exp1")
    # exactly one OptimizeCandidate payload object exists for the single candidate
    # hash, despite the stall retry re-persisting the same candidate.
    cand_objs = [
        p for p in env.stores._shared.backend.payloads.values()
        if p.schema_key == "OptimizeCandidate@1"]
    assert len(cand_objs) == 1
    assert before <= env.stores.payloads_object_count()


# --------------------------------------------------------------------------- #
# finalize_candidate                                                           #
# --------------------------------------------------------------------------- #
SENTINEL_RIC = 0.987654321


class _HoldoutEval:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, cand_ref, window):
        self.calls += 1
        return ValidationMetrics(source="case_grader", rank_ic=SENTINEL_RIC,
                                 sharpe=2.0, oos_verdict="robust")


class SpyGateway:
    """Counts reserve_and_lease / evaluate_once around a real gateway."""

    def __init__(self, gw) -> None:
        self._gw = gw
        self.reserves = 0
        self.evaluates = 0

    def reserve_and_lease(self, cand_ref, *, window_ref, idempotency_key):
        self.reserves += 1
        return self._gw.reserve_and_lease(
            cand_ref, window_ref=window_ref, idempotency_key=idempotency_key)

    def evaluate_once(self, cand_ref, *, holdout_reservation_id, lease_token):
        self.evaluates += 1
        return self._gw.evaluate_once(
            cand_ref, holdout_reservation_id=holdout_reservation_id,
            lease_token=lease_token)


def _passed_run(env, study, states, rounds):
    c0, c1, c2 = _cand("x"), _cand("y"), _cand("z")
    ev = ScriptedEval({c0.candidate_hash: _weak(), c1.candidate_hash: _weak(),
                       c2.candidate_hash: _pass()})
    imp = ChainImprove([c0, c1, c2])
    gov = Governor(trial_budget=50, peek_budget=50)
    res = _run(env, seed=c0, study=study, eval_cb=ev, gate_cb=_gate, improve_cb=imp,
               governor=gov, states=states, rounds=rounds, experiment_id="exp1")
    assert res.state.status == ExperimentStatus.PASSED_VALIDATION
    return res


def _window_ref(env, fam):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 6, 1, tzinfo=UTC)
    matured = datetime(2025, 7, 1, tzinfo=UTC)
    att = compute_holdout_window_attestation(
        family_identity_digest=fam.identity_digest, start_at=start, end_at=end,
        prior_window_ids=())
    win = HoldoutWindow(
        holdout_window_id="hw1", family_identity_digest=fam.identity_digest,
        start_at=start, end_at=end, matured_at=matured, data_snapshot_id="snap-hw1",
        vintage_manifest_digest="1" * 64, prior_window_ids=(),
        non_overlap_attestation=att)
    ref = env.stores.payloads.put(_SR("HoldoutWindow"), win, registry_digest=env.digest,
                                  namespace="main", idempotency_key="win:hw1")
    return TypedPayloadRef(schema_ref=_SR("HoldoutWindow"), payload_ref=ref)


def test_finalize_on_non_passed_raises_before_gateway():
    env = _Env()
    fam = derive_study_family(_study())
    # a FAILED optimize result: finalize must raise before any gateway call.
    failed = OptimizeResult(
        state=_mk_state("expF", fam, ExperimentStatus.FAILED),
        stop_reason="stalled")
    led = env.ledger()
    store = SealedResultStore(payload_store=env.stores.payloads, ledger=led,
                              clock=env.clock, registry=env.registry)
    he = _HoldoutEval()
    gw = SpyGateway(SealedEvaluatorGateway(
        study=_study(), ledger=led, sealed_store=store, evaluate_holdout=he,
        clock=env.clock, lease_ttl_seconds=3600, timeout_seconds=5,
        data_snapshot_hash=DH, code_prompt_model_hash=CH))
    with pytest.raises(OptimizeError):
        finalize_candidate(optimized=failed, sealed_evaluator=gw, states=env.states(),
                           window_ref=_window_ref(env, fam))
    assert gw.reserves == 0 and gw.evaluates == 0 and he.calls == 0


def test_finalize_happy_path_drives_states_and_is_idempotent():
    env = _Env()
    study = _study()
    fam = derive_study_family(study)
    states, rounds = env.states(), env.rounds()
    led = env.ledger()
    res = _passed_run(env, study, states, rounds)

    store = SealedResultStore(payload_store=env.stores.payloads, ledger=led,
                              clock=env.clock, registry=env.registry)
    he = _HoldoutEval()
    gw = SpyGateway(SealedEvaluatorGateway(
        study=study, ledger=led, sealed_store=store, evaluate_holdout=he,
        clock=env.clock, lease_ttl_seconds=3600, timeout_seconds=5,
        data_snapshot_hash=_ctx().data_snapshot_content_digest,
        code_prompt_model_hash=CH))
    win_ref = _window_ref(env, fam)

    receipt = finalize_candidate(optimized=res, sealed_evaluator=gw, states=states,
                                 window_ref=win_ref)
    assert receipt.status == "revealed"
    assert gw.reserves == 1 and gw.evaluates == 1
    # state machine drove PASSED_VALIDATION -> SEALED_EVALUATING -> COMPLETED.
    assert states.load("exp1").status == ExperimentStatus.COMPLETED

    # re-invocation returns the identical receipt without new gateway calls.
    receipt2 = finalize_candidate(optimized=res, sealed_evaluator=gw, states=states,
                                  window_ref=win_ref)
    assert receipt2 == receipt
    assert gw.reserves == 1 and gw.evaluates == 1
    assert he.calls == 1


def test_finalize_never_leaks_sealed_metric_into_public_payloads():
    env = _Env()
    study = _study()
    fam = derive_study_family(study)
    states, rounds = env.states(), env.rounds()
    led = env.ledger()
    res = _passed_run(env, study, states, rounds)
    store = SealedResultStore(payload_store=env.stores.payloads, ledger=led,
                              clock=env.clock, registry=env.registry)
    he = _HoldoutEval()
    gw = SealedEvaluatorGateway(
        study=study, ledger=led, sealed_store=store, evaluate_holdout=he,
        clock=env.clock, lease_ttl_seconds=3600, timeout_seconds=5,
        data_snapshot_hash=_ctx().data_snapshot_content_digest,
        code_prompt_model_hash=CH)
    receipt = finalize_candidate(optimized=res, sealed_evaluator=gw, states=states,
                                 window_ref=_window_ref(env, fam))
    assert receipt.status == "revealed"

    # deep-scan every persisted payload: the sealed sentinel rank_ic value appears
    # ONLY inside the sealed-namespace record, never in a main/round/state/result.
    sentinel = repr(SENTINEL_RIC)
    leaked, sealed_seen = [], False
    for stored in env.stores._shared.backend.payloads.values():
        dumped = repr(stored.model.model_dump(mode="json"))
        if sentinel in dumped:
            if stored.namespace == "sealed":
                sealed_seen = True
            else:
                leaked.append(stored.schema_key)
    assert sealed_seen, "the sealed record must carry the metric"
    assert leaked == [], f"sealed metric leaked into public payloads: {leaked}"
