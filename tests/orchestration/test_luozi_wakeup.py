# -*- coding: utf-8 -*-
"""Phase 9 · Task 6 — WAITING_FOR_MATURITY persistence + idempotent maturity wakeup.

The durable head of a parked 双曲线回放 (dual-curve replay) run + the idempotent
maturity consumer that advances it, plus the two off-by-default autonomy scheduler
gates (``maybe_enqueue_shadow_wakeup`` / ``maybe_enqueue_lane0_bootstrap``) and their
playbooks.

CORRECTION-CLAUSE NOTE (brief wording → reviewed source; source wins — full prose in
``.superpowers/sdd/p9-task-6-report.md``):

* N6-1 — the brief's public name ``wakeup_shadow_replay`` collides with the SEALED
  Phase-6 red line ``test_shadow_redlines.py::test_phase9_machinery_names_absent_from_
  phase6_modules`` (forbids the "wakeup"/"resume" tokens in ANY *defined/imported* name of
  a Phase-6 module — an AST-name check, docstrings/parameters/attribute-reads exempt). It
  is a PERMANENT lexical hygiene red line (invariant group 7), NOT a "does-not-exist-yet"
  gate; Task 5 hit the identical collision and the reviewer adjudicated the compliant
  rename as correct. The function is therefore ``mature_shadow_replay`` (and
  ``ReplayStateStore`` / ``derive_replay_maturity_key`` / ``ReplayMaturityKeyUnknown``).
  The contract field is still ``ShadowReplayRunState.wakeup_key`` (owned by the Phase-9
  ``contracts`` module, referenced only as an attribute / call-kwarg here).

The 18-row matrix (§Required invariants 1-6) — every row REAL Phase-2 store semantics
(CAS conflicts, whole-batch idempotency, UoW atomicity under an injected crash), REAL
Phase-4 ``TrialLedger`` for the terminal handoff, spies for never-reexecutes/write-scope.
"""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from guanlan_v2.orchestration.adapters import luozi
from guanlan_v2.orchestration.adapters.contracts import (
    DualCurveReport,
    ReplayWakeupReceipt,
    ShadowReplayRunState,
)
from guanlan_v2.orchestration.adapters.luozi import (
    REPLAY_HEAD_NAMESPACE,
    REPLAY_MATURITY_KEY_DOMAIN,
    REPLAY_OPERATION_NAMESPACE,
    REPLAY_STATE_CELL_NAMESPACES,
    ReplayMaturityKeyUnknown,
    ReplayRuntimeBindings,
    ReplayStateStore,
    derive_replay_maturity_key,
    mature_shadow_replay,
    persist_replay_state,
    _replay_maturity_ladder,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import ExperimentStatus
from guanlan_v2.orchestration.eventstore import (
    CasPreconditionFailed,
    IdempotencyConflict,
    RuntimeStores,
    RuntimeUnitOfWork,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.runtime_contracts import Phase2RuntimeRegistry

# real dual-curve report + runner + real/fake ledgers reused from the Task-5 suite.
from tests.orchestration.test_dual_curves import (
    _FakePool,
    _SteppingClock,
    _alpha_frames,
    _points,
    _real_ledger,
    _report_over,
    _runner,
)


# --------------------------------------------------------------------------- #
# environment: a real Phase-2 backend with the Task-6 schemas + namespaces      #
# --------------------------------------------------------------------------- #
class _Env:
    def __init__(self):
        reg = Phase2RuntimeRegistry()
        for model in (ShadowReplayRunState, ReplayWakeupReceipt, DualCurveReport):
            reg.register(model)
        reg.seal()
        self.registry = reg
        self.resolver = SchemaRegistryResolver()
        self.digest = self.resolver.register(reg)
        self.clock = SystemClock()
        self.stores = RuntimeStores(
            resolver=self.resolver, clock=self.clock,
            allowed_cell_namespaces=REPLAY_STATE_CELL_NAMESPACES,
        )
        self.store = ReplayStateStore(
            payload_store=self.stores.payloads, state_cells=self.stores.cells,
            registry=reg, clock=self.clock,
            uow_factory=lambda: self.stores.unit_of_work,
        )

    def put_report(self, report, *, run):
        ref = self.stores.payloads.put(
            SchemaRef(name="DualCurveReport", version="1"), report,
            registry_digest=self.digest, namespace="main",
            idempotency_key=f"report:{run}")
        return TypedPayloadRef(
            schema_ref=SchemaRef(name="DualCurveReport", version="1"), payload_ref=ref)


def _env():
    return _Env()


def _report():
    r = _runner(_alpha_frames())
    points = _points(r)
    return _report_over(r, points)


def _running_state(report, *, exp, run):
    """A RUNNING head (Task-4 shape) — the prior head a first park CASes over."""
    return ShadowReplayRunState(
        experiment_id=exp, run_id=run, request_id=run,
        schedule_digest=report.execution_config.schedule_digest,
        execution_config_digest=report.execution_config.semantic_digest(),
        status=ExperimentStatus.RUNNING,
        completed_points=report.decision_point_count,
        total_points=report.decision_point_count,
        updated_at=report.interval_start,
    )


def _parked_state(report, curve_ref, *, resume_after, exp, run, state_digest="0" * 64):
    maturity_key = derive_replay_maturity_key(
        experiment_id=exp, resume_after=resume_after, state_digest=state_digest)
    return ShadowReplayRunState(
        experiment_id=exp, run_id=run, request_id=run,
        schedule_digest=report.execution_config.schedule_digest,
        execution_config_digest=report.execution_config.semantic_digest(),
        status=ExperimentStatus.WAITING_FOR_MATURITY,
        completed_points=report.decision_point_count,
        total_points=report.decision_point_count,
        resume_after=resume_after, wakeup_key=maturity_key,
        curve_report_ref=curve_ref, updated_at=resume_after,
    )


def _seed_parked(env, report, *, resume_after, exp="replay.exp.1", run="replay-run.1"):
    """Persist a parked head at ``resume_after`` and return it."""
    curve_ref = env.put_report(report, run=run)
    state = _parked_state(report, curve_ref, resume_after=resume_after, exp=exp, run=run)
    persist_replay_state(state, stores=env.store, idempotency_key="park")
    return state


class _SpyCoord:
    """A ReplayPlanCoordinator spy — asserts the wakeup NEVER re-executes a point."""

    def __init__(self):
        self.calls = []

    def bootstrap_context(self, point):  # pragma: no cover - must never run
        self.calls.append(("bootstrap_context", point))
        raise AssertionError("wakeup must not admit/run a decision point")

    def llm_proposal(self, point, snapshot):  # pragma: no cover - must never run
        self.calls.append(("llm_proposal", point))
        raise AssertionError("wakeup must not re-run the LLM lane")

    def deterministic_targets(self, point, snapshot):  # pragma: no cover
        self.calls.append(("deterministic_targets", point))
        raise AssertionError("wakeup must not re-run the deterministic lane")


class _SpyLedger:
    """A TrialLedger spy — records every method touched (untouched-when-immature proof)."""

    def __init__(self):
        self.touched = []

    def register_holdout_window(self, *a, **k):
        self.touched.append("register_holdout_window")

    def __getattr__(self, name):  # any other access is recorded and refused
        def _rec(*a, **k):
            self.touched.append(name)
        return _rec


def _bindings(env, *, ledger, pool=None, admission=None):
    return ReplayRuntimeBindings(
        admission=admission or _SpyCoord(), budget=None, run_budget=None,
        schedule_registry=None, calendar=None,
        clock_factory=lambda p: SystemClock(),
        pool=pool or _FakePool(), trial_ledger=ledger,
        replay_state_store=env.store,
    )


# =========================================================================== #
# Row 1 — persist is a single all-or-none UoW (crash between put and CAS)         #
# =========================================================================== #
class _InjectedCrash(RuntimeError):
    pass


class _CrashBeforePublishUoW(RuntimeUnitOfWork):
    """Applies the WHOLE batch (payload put + head CAS) to the clone, then raises BEFORE
    publishing — the exact 'crash between payload put and head CAS' window."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.armed = True

    def commit(self, batch):
        if self.armed:
            self.armed = False
            with self._shared.lock:
                wb = self._shared.backend.clone()
                self._apply_batch(wb, batch)  # put + CAS applied to the clone
                raise _InjectedCrash("crash after apply, before publish")
        return super().commit(batch)


def test_persist_state_single_uow():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    curve_ref = env.put_report(report, run="replay-run.crash")
    state = _parked_state(report, curve_ref, resume_after=ladder[0],
                          exp="replay.crash", run="replay-run.crash")

    crash_uow = _CrashBeforePublishUoW(
        env.stores._shared, env.stores._resolver, env.stores._clock,
        allowed_cell_namespaces=env.stores._allowed_cell_namespaces,
        run_budgets=env.stores._run_budgets)
    crashing = ReplayStateStore(
        payload_store=env.stores.payloads, state_cells=env.stores.cells,
        registry=env.registry, clock=env.clock, uow_factory=lambda: crash_uow)

    before = env.stores.payloads_object_count()
    with pytest.raises(_InjectedCrash):
        persist_replay_state(state, stores=crashing, idempotency_key="k1")
    # neither the payload nor the head cell is visible — the clone was discarded.
    assert env.stores.payloads_object_count() == before
    assert crashing.load_head("replay.crash") is None

    # retry with the same key lands BOTH (put + head), atomically.
    ref = persist_replay_state(state, stores=crashing, idempotency_key="k1")
    assert isinstance(ref, PayloadRef)
    head = crashing.load_head("replay.crash")
    assert head is not None and head.semantic_digest() == state.semantic_digest()


# =========================================================================== #
# Row 2 — same key / different content ⇒ IdempotencyConflict                     #
# =========================================================================== #
def test_persist_idempotent_conflict():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    curve_ref = env.put_report(report, run="replay-run.conf")
    a = _parked_state(report, curve_ref, resume_after=ladder[0],
                      exp="replay.conf", run="replay-run.conf", state_digest="1" * 64)
    b = _parked_state(report, curve_ref, resume_after=ladder[1],
                      exp="replay.conf", run="replay-run.conf", state_digest="2" * 64)
    assert a.semantic_digest() != b.semantic_digest()
    persist_replay_state(a, stores=env.store, idempotency_key="same")
    with pytest.raises(IdempotencyConflict):
        persist_replay_state(b, stores=env.store, idempotency_key="same")


def test_persist_same_content_is_idempotent_noop():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    state = _seed_parked(env, report, resume_after=ladder[0])
    n1 = env.stores.payloads_object_count()
    # a re-persist of the identical head is a no-op (no new payload) returning the ref.
    ref = persist_replay_state(state, stores=env.store, idempotency_key="park-again")
    assert isinstance(ref, PayloadRef)
    assert env.stores.payloads_object_count() == n1


# =========================================================================== #
# Row 3 — unknown key ⇒ typed error, no state read side effects                  #
# =========================================================================== #
def test_wakeup_unknown_key():
    env = _env()
    report = _report()
    ledger = _SpyLedger()
    bindings = _bindings(env, ledger=ledger)
    with pytest.raises(ReplayMaturityKeyUnknown):
        mature_shadow_replay("never-issued-key", bindings=bindings, now=report.interval_end)
    assert ledger.touched == []


# =========================================================================== #
# Row 4 — now < resume_after ⇒ not_mature, head untouched                        #
# =========================================================================== #
def test_wakeup_not_mature():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    state = _seed_parked(env, report, resume_after=ladder[0])
    before = state.semantic_digest()
    ledger = _SpyLedger()
    receipt = mature_shadow_replay(
        state.wakeup_key, bindings=_bindings(env, ledger=ledger),
        now=ladder[0] - timedelta(seconds=1))
    assert receipt.outcome == "not_mature"
    assert receipt.state_digest_after == before
    # the head is byte-unchanged; the ledger was never touched.
    assert env.store.load_head(state.experiment_id).semantic_digest() == before
    assert ledger.touched == []


# =========================================================================== #
# Row 5 — duplicate delivery (sequential + concurrent) applies effects once      #
# =========================================================================== #
def test_wakeup_duplicate_delivery_sequential():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    now = ladder[-1] + timedelta(days=7)
    state = _seed_parked(env, report, resume_after=ladder[0])
    ledger = _real_ledger(_SteppingClock(now))
    bindings = _bindings(env, ledger=ledger)

    first = mature_shadow_replay(state.wakeup_key, bindings=bindings, now=now)
    second = mature_shadow_replay(state.wakeup_key, bindings=bindings, now=now)
    assert first.outcome == "completed"
    # the second delivery gets the byte-identical STORED receipt.
    assert second.semantic_digest() == first.semantic_digest()
    assert second.state_digest_after == first.state_digest_after
    # effects once: the head is COMPLETED and the ledger registered exactly once.
    head = env.store.load_head(state.experiment_id)
    assert head.status == ExperimentStatus.COMPLETED


def test_wakeup_duplicate_delivery_concurrent():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    now = ladder[-1] + timedelta(days=7)
    state = _seed_parked(env, report, resume_after=ladder[0])
    ledger = _real_ledger(_SteppingClock(now))
    bindings = _bindings(env, ledger=ledger)

    results = {}
    barrier = threading.Barrier(2)

    def _go(i):
        barrier.wait()
        results[i] = mature_shadow_replay(state.wakeup_key, bindings=bindings, now=now)

    ts = [threading.Thread(target=_go, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # both racing callers return a receipt with the identical semantic digest (effects once).
    assert results[0].semantic_digest() == results[1].semantic_digest()
    assert env.store.load_head(state.experiment_id).status == ExperimentStatus.COMPLETED


# =========================================================================== #
# Row 6 — a stale (superseded) key ⇒ already_processed, never double-advances     #
# =========================================================================== #
def test_wakeup_stale_key_superseded():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    assert len(ladder) >= 3
    state = _seed_parked(env, report, resume_after=ladder[0])
    ledger = _real_ledger(_SteppingClock(ladder[-1] + timedelta(days=7)))
    bindings = _bindings(env, ledger=ledger)

    old_key = state.wakeup_key
    # partial wake → re-park under a NEW key; the old key is now superseded.
    partial = mature_shadow_replay(old_key, bindings=bindings, now=ladder[1])
    assert partial.outcome == "resumed"
    head_after = env.store.load_head(state.experiment_id)
    assert head_after.wakeup_key != old_key
    digest_after_repark = head_after.semantic_digest()

    # waking the OLD (stale) key again ⇒ already_processed, and the head is NOT re-advanced.
    replay = mature_shadow_replay(old_key, bindings=bindings, now=ladder[-1] + timedelta(days=7))
    assert replay.semantic_digest() == partial.semantic_digest()  # the stored receipt
    assert env.store.load_head(state.experiment_id).semantic_digest() == digest_after_repark


# =========================================================================== #
# Row 7 — partial maturity re-parks with strictly-later resume_after + new key    #
# =========================================================================== #
def test_wakeup_partial_maturity_reparks():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    assert len(ladder) >= 3
    state = _seed_parked(env, report, resume_after=ladder[0])
    ledger = _SpyLedger()  # partial maturity NEVER touches the ledger
    bindings = _bindings(env, ledger=ledger)

    receipt = mature_shadow_replay(state.wakeup_key, bindings=bindings, now=ladder[1])
    assert receipt.outcome == "resumed"
    head = env.store.load_head(state.experiment_id)
    assert head.status == ExperimentStatus.WAITING_FOR_MATURITY
    # strictly-later resume_after + a DIFFERENT wakeup_key.
    assert head.resume_after > state.resume_after
    assert head.wakeup_key != state.wakeup_key
    # the matured batch was processed but feedback is NOT registered before full maturity.
    assert ledger.touched == []
    # the new key is resolvable (the maturity-index cell was written in the same UoW).
    assert env.store.load_head_by_maturity_key(head.wakeup_key).semantic_digest() == \
        head.semantic_digest()


# =========================================================================== #
# Row 8 — full maturity ⇒ completed with a curve_report_ref                       #
# =========================================================================== #
def test_wakeup_completion():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    now = ladder[-1] + timedelta(days=7)
    state = _seed_parked(env, report, resume_after=ladder[0])
    ledger = _real_ledger(_SteppingClock(now))
    receipt = mature_shadow_replay(
        state.wakeup_key, bindings=_bindings(env, ledger=ledger), now=now)
    assert receipt.outcome == "completed"
    head = env.store.load_head(state.experiment_id)
    assert head.status == ExperimentStatus.COMPLETED
    assert head.curve_report_ref is not None
    assert head.curve_report_ref.schema_ref.name == "DualCurveReport"
    assert head.completed_points == head.total_points


# =========================================================================== #
# Row 9 — the wakeup NEVER re-executes a decision point (admission spy)           #
# =========================================================================== #
def test_wakeup_never_reexecutes_points():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    now = ladder[-1] + timedelta(days=7)
    state = _seed_parked(env, report, resume_after=ladder[0])
    spy = _SpyCoord()
    ledger = _real_ledger(_SteppingClock(now))
    receipt = mature_shadow_replay(
        state.wakeup_key,
        bindings=_bindings(env, ledger=ledger, admission=spy), now=now)
    assert receipt.outcome == "completed"
    # zero decision-plan admissions / lane runs during the wakeup.
    assert spy.calls == []


# =========================================================================== #
# Row 10 — the maturity key is service-derived, never caller-chosen               #
# =========================================================================== #
def test_wakeup_key_service_derived():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    state = _seed_parked(env, report, resume_after=ladder[0])
    ledger = _SpyLedger()
    receipt = mature_shadow_replay(
        state.wakeup_key, bindings=_bindings(env, ledger=ledger), now=ladder[1])
    assert receipt.outcome == "resumed"
    head = env.store.load_head(state.experiment_id)
    # the re-parked key is the pinned domain-tagged digest of (experiment_id, next
    # boundary, superseded-state digest) — no API path lets a caller choose it.
    expected = content_digest({
        "domain": REPLAY_MATURITY_KEY_DOMAIN,
        "experiment_id": state.experiment_id,
        "resume_after": head.resume_after,
        "state_digest": state.semantic_digest(),
    })
    assert head.wakeup_key == expected
    assert REPLAY_MATURITY_KEY_DOMAIN == "adapters-replay-wakeup-v1"
    # derive_replay_maturity_key has no key parameter — the derivation is the only source.
    import inspect
    assert "wakeup_key" not in inspect.signature(derive_replay_maturity_key).parameters


# =========================================================================== #
# Row 11 — one ExperimentStateChanged per transition; journal folds to the head   #
# =========================================================================== #
def test_event_per_transition_and_fold():
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    assert len(ladder) >= 3
    state = _seed_parked(env, report, resume_after=ladder[0])  # transition 1 (park)
    ledger = _real_ledger(_SteppingClock(ladder[-1] + timedelta(days=7)))
    bindings = _bindings(env, ledger=ledger)

    mature_shadow_replay(state.wakeup_key, bindings=bindings, now=ladder[1])  # transition 2 (re-park)
    head = env.store.load_head(state.experiment_id)
    mature_shadow_replay(head.wakeup_key, bindings=bindings,
                         now=ladder[-1] + timedelta(days=7))  # transition 3 (complete)

    # exactly three ExperimentStateChanged events on the experiment's main journal.
    events = env.stores.events.journal(state.experiment_id, "main")
    esc = [e for e in events if e.event_type == EventType.EXPERIMENT_STATE_CHANGED]
    assert len(esc) == 3
    # replay the journal and fold: the last state-change event's payload IS the head.
    replayed = env.stores.events.replay()
    ev = [e for e in replayed.journal(state.experiment_id, "main")
          if e.event_type == EventType.EXPERIMENT_STATE_CHANGED]
    last_ref = ev[-1].payload_ref
    head_ref = env.store._head_ref(state.experiment_id)
    assert last_ref.content_digest == head_ref.payload_ref.content_digest
    assert env.store.load_head(state.experiment_id).status == ExperimentStatus.COMPLETED


# =========================================================================== #
# Row 12 — the shadow-wakeup scheduler gate matrix (off by default)               #
# =========================================================================== #
def test_scheduler_gate_matrix(monkeypatch):
    import guanlan_v2.autonomy.runtime as R
    calls = {"n": 0}
    monkeypatch.setattr(R, "start_job_bg", lambda pb: (calls.__setitem__("n", calls["n"] + 1) or {"ok": True}))
    monkeypatch.setattr(R, "_already_ran_today", lambda pb: False)

    # env unset ⇒ False, no job.
    monkeypatch.delenv("GUANLAN_SHADOW_WAKEUP", raising=False)
    assert R.maybe_enqueue_shadow_wakeup("daily-scheduler") is False
    assert calls["n"] == 0

    monkeypatch.setenv("GUANLAN_SHADOW_WAKEUP", "1")
    # wrong note ⇒ False.
    assert R.maybe_enqueue_shadow_wakeup("manual") is False
    assert calls["n"] == 0
    # already-today ⇒ False.
    monkeypatch.setattr(R, "_already_ran_today", lambda pb: True)
    assert R.maybe_enqueue_shadow_wakeup("daily-scheduler") is False
    assert calls["n"] == 0
    # happy path ⇒ True + one job enqueued.
    monkeypatch.setattr(R, "_already_ran_today", lambda pb: False)
    assert R.maybe_enqueue_shadow_wakeup("daily-scheduler") is True
    assert calls["n"] == 1


# =========================================================================== #
# Row 13 — the gate self-swallows a raising playbook into the rescore seam         #
# =========================================================================== #
def test_gate_self_swallows(monkeypatch):
    import guanlan_v2.autonomy.runtime as R
    monkeypatch.setenv("GUANLAN_SHADOW_WAKEUP", "1")
    monkeypatch.setattr(R, "_already_ran_today", lambda pb: False)

    def _boom(pb):
        raise RuntimeError("scheduler blew up")

    monkeypatch.setattr(R, "start_job_bg", _boom)
    # never raises into the rescore seam — returns False.
    assert R.maybe_enqueue_shadow_wakeup("daily-scheduler") is False


# =========================================================================== #
# Row 14 — the playbook write scope is orchestration stores + job events only      #
# =========================================================================== #
def test_playbook_write_scope(monkeypatch):
    import guanlan_v2.autonomy.playbooks as P
    env = _env()
    report = _report()
    ladder = _replay_maturity_ladder(report)
    now = ladder[-1] + timedelta(days=7)
    state = _seed_parked(env, report, resume_after=ladder[0])

    # a seats-seam spy that records any write attempt (the wakeup must touch none).
    class _SeatsSpy:
        def __init__(self):
            self.writes = []

        def note_external_llm_use(self, *a, **k):  # pragma: no cover - must never run
            self.writes.append("note_external_llm_use")

        def load_state(self):
            return {}

    seats = _SeatsSpy()
    ledger = _real_ledger(_SteppingClock(now))
    bindings = ReplayRuntimeBindings(
        admission=_SpyCoord(), budget=None, run_budget=None, schedule_registry=None,
        calendar=None, clock_factory=lambda p: SystemClock(),
        pool=_FakePool(), trial_ledger=ledger, replay_state_store=env.store,
        seats_budget_seam=seats)

    monkeypatch.setattr(P, "_shadow_wakeup_context", lambda: (env.store, bindings, now))
    out = P._run_shadow_replay_wakeup(object())
    assert out["ok"] is True
    assert out["report"]["count"] == 1
    assert out["report"]["wakeups"][0]["outcome"] == "completed"
    # the orchestration head advanced (allowed write) but NO seats write happened.
    assert env.store.load_head(state.experiment_id).status == ExperimentStatus.COMPLETED
    assert seats.writes == []


def test_playbook_honest_skip_when_unwired(monkeypatch):
    import guanlan_v2.autonomy.playbooks as P
    monkeypatch.setattr(P, "_shadow_wakeup_context", lambda: None)
    out = P._run_shadow_replay_wakeup(object())
    assert out["ok"] is True
    assert "skipped" in out["report"]


# =========================================================================== #
# Row 15 — the lane0 scheduler gate matrix (off by default)                       #
# =========================================================================== #
def test_lane0_gate_matrix(monkeypatch):
    import guanlan_v2.autonomy.runtime as R
    calls = {"n": 0}
    monkeypatch.setattr(R, "start_job_bg", lambda pb: (calls.__setitem__("n", calls["n"] + 1) or {"ok": True}))
    monkeypatch.setattr(R, "_already_ran_today", lambda pb: False)

    monkeypatch.delenv("GUANLAN_LANE0_DAILY", raising=False)
    assert R.maybe_enqueue_lane0_bootstrap("daily-scheduler") is False
    assert calls["n"] == 0

    monkeypatch.setenv("GUANLAN_LANE0_DAILY", "1")
    assert R.maybe_enqueue_lane0_bootstrap("manual") is False
    monkeypatch.setattr(R, "_already_ran_today", lambda pb: True)
    assert R.maybe_enqueue_lane0_bootstrap("daily-scheduler") is False
    monkeypatch.setattr(R, "_already_ran_today", lambda pb: False)
    assert R.maybe_enqueue_lane0_bootstrap("daily-scheduler") is True
    assert calls["n"] == 1


# =========================================================================== #
# Row 16 — lane0 with no active lease ⇒ honest skip, zero admissions              #
# =========================================================================== #
class _FakeLane0Service:
    def __init__(self, *, leased, lease_id=None):
        self._leased = leased
        self._lease_id = lease_id
        self.admissions = []

    def try_lease(self, *, now):
        return (self._leased, self._lease_id)

    def admit_and_run(self, *, actor, now):
        self.admissions.append(actor)
        return {"snapshot_committed": True, "actor_echo": actor,
                "case_seeds": ["seed-1", "seed-2"]}


def test_lane0_no_lease_honest_skip():
    from guanlan_v2.autonomy.playbooks import orchestrate_lane0_bootstrap
    service = _FakeLane0Service(leased=False)
    out = orchestrate_lane0_bootstrap(service=service, now="2026-07-25")
    assert out["admitted"] is False
    assert out["skipped"] == "no active lease"
    # zero admissions — nothing admitted without a lease.
    assert service.admissions == []


# =========================================================================== #
# Row 17 — lane0 leased run admits with actor lease:<id>, commits + seeds           #
# =========================================================================== #
def test_lane0_leased_run_commits_snapshot():
    from guanlan_v2.autonomy.playbooks import orchestrate_lane0_bootstrap
    service = _FakeLane0Service(leased=True, lease_id="lease-abc")
    out = orchestrate_lane0_bootstrap(service=service, now="2026-07-25")
    assert out["admitted"] is True
    assert out["actor"] == "lease:lease-abc"
    # the candidate admitted under actor lease:<id>, committed a snapshot + case seeds.
    assert service.admissions == ["lease:lease-abc"]
    assert out["snapshot_committed"] is True
    assert out["case_seeds"] == ["seed-1", "seed-2"]


def test_lane0_playbook_honest_skip_when_unwired(monkeypatch):
    import guanlan_v2.autonomy.playbooks as P
    monkeypatch.setattr(P, "_lane0_bootstrap_service", lambda: None)
    out = P._run_lane0_bootstrap(object())
    assert out["ok"] is True
    assert "skipped" in out["report"]


# =========================================================================== #
# extra — the two namespaces are the constructor-injected C5 extension            #
# =========================================================================== #
def test_namespaces_are_the_c5_extension():
    assert REPLAY_STATE_CELL_NAMESPACES == (REPLAY_HEAD_NAMESPACE, REPLAY_OPERATION_NAMESPACE)
    assert REPLAY_HEAD_NAMESPACE == "adapters.replay_head.v1"
    assert REPLAY_OPERATION_NAMESPACE == "adapters.replay_operation.v1"
    # a store built WITHOUT the union refuses to construct (constructor-injected, not
    # hardcoded — a missing namespace is a loud misconfiguration).
    env = _env()
    bad = RuntimeStores(resolver=env.resolver, clock=env.clock, allowed_cell_namespaces=())
    with pytest.raises(luozi.ShadowContractError):
        ReplayStateStore(payload_store=bad.payloads, state_cells=bad.cells,
                         registry=env.registry, clock=env.clock,
                         uow_factory=lambda: bad.unit_of_work)
