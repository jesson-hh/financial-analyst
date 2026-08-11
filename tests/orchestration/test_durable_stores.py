# -*- coding: utf-8 -*-
"""Phase 9 · Task 1b — durable jsonl/file store backend conformance + durability.

Two test families:

1. **Conformance reuse** — the reviewed Phase 2 in-memory store behavioural matrix
   (``tests/orchestration/test_eventstore.py``) is re-run *verbatim* against the
   durable backend by monkeypatching that module's ``RuntimeStores`` name to a
   durable factory (fresh tmp root per stores instance). Every zero-argument Phase 2
   behavioural assertion must hold byte-for-byte on the durable implementation
   (UoW atomicity, staged sentinel replacement, whole-batch idempotency, CAS
   conflicts, dual-cursor visibility, replay). No hand-mirroring.

2. **Durability-specific** — crash windows (append/fold/UoW kill points), torn-tail
   vs mid-file corruption, payload write-once conflict + digest-verify-on-fold,
   restart survival of a parked maturity head, and the honest startup interrupt
   scan (in-flight attempt ⇒ ``interrupted`` via the reviewed RunResult record path;
   finished / parked runs left untouched; idempotent re-scan).

Run: ``python -m pytest tests/orchestration/test_durable_stores.py -v``
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tests.orchestration.test_eventstore as es_tests
from guanlan_v2.orchestration.enums import NodeStatus, PortfolioRating
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.eventstore import (
    ContentDigestMismatch,
    EventAppendRequest,
    IdempotencyConflict,
    RuntimeStores,
    SchemaRegistryResolver,
    StagedPayloadKey,
    StateCellCompareAndSwapCommand,
    RuntimeBatch,
    PayloadPutCommand,
)
from guanlan_v2.orchestration.data.source import DataRequest
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
from guanlan_v2.orchestration.schema_registry import SchemaRegistry, default_registry
from guanlan_v2.orchestration.schemas import NodeRun, ResearchPlan

from guanlan_v2.orchestration.adapters.durable import (
    DurableStoreCorrupt,
    PayloadWriteConflict,
    build_durable_runtime_stores,
    scan_and_mark_interrupted,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64
_RESEARCH_SR = SchemaRef(name="ResearchPlan", version="1")
_NODE_RUN_SR = SchemaRef(name="NodeRun", version="1")
_RUN_RESULT_SR = SchemaRef(name="RunResult", version="1")
HEAD_NS = "adapters.replay_head.v1"


class _AdvancingClock:
    def __init__(self, start=None, step=timedelta(seconds=1)):
        self._next = start or datetime(2026, 7, 24, 9, 0, tzinfo=UTC)
        self._step = step

    def now(self):
        cur = self._next
        self._next = cur + self._step
        return cur


# =========================================================================== #
# Family 1 — Phase 2 behavioural matrix, re-run against the durable backend     #
# =========================================================================== #
def _durable_factory(base: Path):
    """A ``RuntimeStores``-signature factory that builds a durable backend rooted at
    a fresh subdir of ``base`` per call (test isolation)."""
    counter = {"n": 0}

    def factory(*, resolver, clock, allowed_cell_namespaces=()):
        counter["n"] += 1
        root = base / f"stores-{counter['n']}"
        return build_durable_runtime_stores(
            root, resolver=resolver, clock=clock,
            allowed_cell_namespaces=allowed_cell_namespaces)

    return factory


# every zero-argument Phase 2 behavioural test function, discovered dynamically so
# the durable backend is held to the exact reviewed matrix without copying it.
_CONFORMANCE_NAMES = sorted(
    name
    for name, fn in inspect.getmembers(es_tests, inspect.isfunction)
    if name.startswith("test_")
    and fn.__module__ == es_tests.__name__
    and len(inspect.signature(fn).parameters) == 0
)


def test_conformance_matrix_is_nonempty():
    # guard: the reuse mechanism actually found the Phase 2 matrix.
    assert len(_CONFORMANCE_NAMES) >= 25


@pytest.mark.parametrize("test_name", _CONFORMANCE_NAMES)
def test_durable_matches_inmemory_behaviour(test_name, tmp_path, monkeypatch):
    monkeypatch.setattr(es_tests, "RuntimeStores", _durable_factory(tmp_path))
    getattr(es_tests, test_name)()


# =========================================================================== #
# Family 2 — durability-specific                                                #
# =========================================================================== #
def _stores(root, *, allowed=(HEAD_NS,)):
    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    rt = resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    stores = build_durable_runtime_stores(
        root, resolver=resolver, clock=_AdvancingClock(), allowed_cell_namespaces=allowed)
    return stores, rt


def _research(rationale="thesis"):
    return ResearchPlan(recommendation=PortfolioRating.BUY, rationale=rationale)


def _put(stores, rt, *, rationale="thesis", namespace="main", idem="p-1"):
    return stores.payloads.put(
        _RESEARCH_SR, _research(rationale), registry_digest=rt,
        namespace=namespace, idempotency_key=idem)


def _append(stores, rt, ref, *, run_id="run-1", event_type="LayerCommitted",
            idem="ev-1", plan_digest=DA):
    return stores.events.append(EventAppendRequest(
        run_id=run_id, partition="main", event_type=event_type,
        payload_schema_ref=_RESEARCH_SR, payload_ref=ref,
        registry_digest=rt, idempotency_key=idem, plan_digest=plan_digest))


def _node_run(run_id="run-flight", status=NodeStatus.RUNNING):
    return NodeRun(
        node_run_id="nr-1", run_id=run_id, plan_id="plan-1", plan_digest=DA,
        node_id="n_a", worker_id="w_a", status=status, attempt_id="att-1",
        attempt=1, input_snapshot_digest=DA)


# ---- restart survival ------------------------------------------------------ #
def test_restart_folds_payloads_events_cursors_byte_identically(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    r1 = _put(stores, rt, rationale="one", idem="p1")
    r2 = _put(stores, rt, rationale="two", idem="p2")
    _append(stores, rt, r1, event_type="ArtifactStaged", idem="e1")
    _append(stores, rt, r2, event_type="LayerCommitted", idem="e2")
    journal = stores.events.journal("run-1", "main")
    visible = stores.events.visible("run-1", "main")

    # process death → a fresh store over the same root folds identically.
    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    reopened = build_durable_runtime_stores(
        tmp_path / "s", resolver=resolver, clock=_AdvancingClock(),
        allowed_cell_namespaces=(HEAD_NS,))
    assert reopened.events.journal("run-1", "main") == journal
    assert reopened.events.visible("run-1", "main") == visible
    assert [e.journal_seq for e in reopened.events.journal("run-1", "main")] == [1, 2]
    assert [e.visible_seq for e in reopened.events.visible("run-1", "main")] == [1]
    # the payload folds back and re-validates (digest-verified on read).
    got = reopened.payloads.get(r1, expected_schema_ref=_RESEARCH_SR)
    assert got == _research("one")


def test_restart_survives_a_parked_maturity_head_cell(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    # a parked WAITING_FOR_MATURITY head is a state cell → CAS it in via UoW.
    ref = _put(stores, rt, idem="p-head")
    head = TypedPayloadRef(schema_ref=_RESEARCH_SR, payload_ref=ref)
    stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="park-1",
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest="1" * 64,
            expected_value=None, new_target=head),)))
    before = stores.cells.load(HEAD_NS, "1" * 64)
    assert before is not None

    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    reopened = build_durable_runtime_stores(
        tmp_path / "s", resolver=resolver, clock=_AdvancingClock(),
        allowed_cell_namespaces=(HEAD_NS,))
    after = reopened.cells.load(HEAD_NS, "1" * 64)
    assert after is not None
    assert after.semantic_digest() == before.semantic_digest()


# ---- crash windows --------------------------------------------------------- #
def test_uncommitted_last_commit_is_dropped_on_fold(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    r1 = _put(stores, rt, rationale="a", idem="p1")
    _append(stores, rt, r1, event_type="LayerCommitted", idem="e1")
    r2 = _put(stores, rt, rationale="b", idem="p2")
    _append(stores, rt, r2, event_type="LayerCommitted", idem="e2")

    # simulate a crash during the LAST commit: drop its barrier line.
    commits = tmp_path / "s" / "commits.jsonl"
    lines = commits.read_text(encoding="utf-8").splitlines()
    commits.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    reopened = build_durable_runtime_stores(
        tmp_path / "s", resolver=resolver, clock=_AdvancingClock(),
        allowed_cell_namespaces=(HEAD_NS,))
    # the last (un-barriered) event never becomes visible; the earlier one survives.
    journal = reopened.events.journal("run-1", "main")
    assert [e.idempotency_key for e in journal] == ["e1"]


def test_torn_tail_event_row_dropped_with_warning(tmp_path, caplog):
    stores, rt = _stores(tmp_path / "s")
    r1 = _put(stores, rt, idem="p1")
    _append(stores, rt, r1, event_type="LayerCommitted", idem="e1")

    # a torn (half-written, non-JSON) final append.
    events_file = tmp_path / "s" / "events" / "main.jsonl"
    with open(events_file, "ab") as fh:
        fh.write(b'{"seq": 999, "event": {"schema_ver')  # truncated garbage, no newline

    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    with caplog.at_level("WARNING"):
        reopened = build_durable_runtime_stores(
            tmp_path / "s", resolver=resolver, clock=_AdvancingClock(),
            allowed_cell_namespaces=(HEAD_NS,))
    assert [e.idempotency_key for e in reopened.events.journal("run-1", "main")] == ["e1"]
    assert any("torn" in rec.message.lower() for rec in caplog.records)


def test_mid_file_corruption_is_typed_hard_failure(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    r1 = _put(stores, rt, idem="p1")
    _append(stores, rt, r1, event_type="LayerCommitted", idem="e1")
    r2 = _put(stores, rt, idem="p2")
    _append(stores, rt, r2, event_type="LayerCommitted", idem="e2")

    # corrupt a NON-final (earlier) committed event row — never silently skipped.
    events_file = tmp_path / "s" / "events" / "main.jsonl"
    lines = events_file.read_text(encoding="utf-8").splitlines()
    lines[0] = '{"seq": 1, "event": "not-a-valid-event}'  # malformed, not the final line
    events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    with pytest.raises(DurableStoreCorrupt):
        build_durable_runtime_stores(
            tmp_path / "s", resolver=resolver, clock=_AdvancingClock(),
            allowed_cell_namespaces=(HEAD_NS,))


# ---- payload write-once + digest-verify ------------------------------------ #
def test_payload_write_once_conflict_on_byte_mismatch(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    ref = _put(stores, rt, rationale="canonical", idem="p1")
    # tamper the on-disk content-addressed file to different bytes.
    content_file = tmp_path / "s" / "payloads" / "main" / f"{ref.content_digest}.json"
    content_file.write_text('{"tampered": true}', encoding="utf-8")
    # a second put of the SAME content (same digest → same file) under a new idem
    # key must detect the byte divergence and refuse (write-once integrity).
    with pytest.raises(PayloadWriteConflict):
        stores.payloads.put(
            _RESEARCH_SR, _research("canonical"), registry_digest=rt,
            namespace="main", idempotency_key="p2")


def test_payload_file_corruption_hard_fails_on_fold(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    ref = _put(stores, rt, rationale="real", idem="p1")
    # corrupt the content to a different (valid-JSON) payload — digest will not match.
    content_file = tmp_path / "s" / "payloads" / "main" / f"{ref.content_digest}.json"
    content_file.write_text(
        _research("tampered").model_dump_json(), encoding="utf-8")

    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    with pytest.raises((DurableStoreCorrupt, ContentDigestMismatch)):
        build_durable_runtime_stores(
            tmp_path / "s", resolver=resolver, clock=_AdvancingClock(),
            allowed_cell_namespaces=(HEAD_NS,))


def test_sealed_and_main_payloads_partition_physically(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    _put(stores, rt, namespace="main", idem="m")
    _put(stores, rt, namespace="sealed", idem="s")
    main_dir = tmp_path / "s" / "payloads" / "main"
    sealed_dir = tmp_path / "s" / "payloads" / "sealed"
    assert main_dir.is_dir() and sealed_dir.is_dir()
    assert main_dir != sealed_dir  # sealed never shares a directory with main


# ---- honest startup interrupt scan ----------------------------------------- #
def _seed_in_flight_run(stores, rt, run_id):
    nr = _node_run(run_id=run_id)
    ref = stores.payloads.put(
        _NODE_RUN_SR, dict(nr), registry_digest=rt, namespace="main",
        idempotency_key=f"{run_id}:nr:payload")
    stores.events.append(EventAppendRequest(
        run_id=run_id, partition="main", event_type="NodeStateChanged",
        payload_schema_ref=_NODE_RUN_SR, payload_ref=ref, registry_digest=rt,
        idempotency_key=f"{run_id}:nr:event", plan_digest=DA))


def test_scan_marks_in_flight_attempt_interrupted(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    _seed_in_flight_run(stores, rt, "run-flight")
    # no terminal RUN_* event yet.
    before = stores.events.journal("run-flight", "main")
    assert not any(e.event_type is EventType.RUN_CANCELLED for e in before)

    marked = scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())
    assert marked == ("run-flight",)
    after = stores.events.journal("run-flight", "main")
    cancels = [e for e in after if e.event_type is EventType.RUN_CANCELLED]
    assert len(cancels) == 1
    result = stores.payloads.get(cancels[0].payload_ref, expected_schema_ref=_RUN_RESULT_SR)
    assert result.terminal_status == "cancelled"
    assert result.run_id == "run-flight"


def test_scan_skips_finished_run(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    # a run that already reached a terminal RunCompleted.
    from guanlan_v2.orchestration.runtime_contracts import RunResult
    done = RunResult(run_id="run-done", plan_digest=DA, terminal_status="completed")
    ref = stores.payloads.put(
        _RUN_RESULT_SR, dict(done), registry_digest=rt, namespace="main",
        idempotency_key="done:payload")
    stores.events.append(EventAppendRequest(
        run_id="run-done", partition="main", event_type="RunCompleted",
        payload_schema_ref=_RUN_RESULT_SR, payload_ref=ref, registry_digest=rt,
        idempotency_key="done:event", plan_digest=DA))

    marked = scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())
    assert "run-done" not in marked


def test_scan_leaves_experiment_lifecycle_run_untouched(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    # a run in the experiment lifecycle (WAITING_FOR_MATURITY layer) — never
    # interrupted by the node-execution scan.
    ref = stores.payloads.put(
        _RESEARCH_SR, _research(), registry_digest=rt, namespace="main",
        idempotency_key="exp:payload")
    stores.events.append(EventAppendRequest(
        run_id="run-parked", partition="main", event_type="ExperimentStateChanged",
        payload_schema_ref=_RESEARCH_SR, payload_ref=ref, registry_digest=rt,
        idempotency_key="exp:event", plan_digest=DA))

    marked = scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())
    assert "run-parked" not in marked
    # no terminal event fabricated for it.
    assert not any(
        e.event_type is EventType.RUN_CANCELLED
        for e in stores.events.journal("run-parked", "main"))


# ---- the scan must not interrupt a run that never executed ----------------- #
def _seed_proposed_only_run(stores, rt, run_id, *, approved=False):
    """The writes a REAL ``propose`` (and ``approve``) makes, and nothing else.

    ``lane0_driver._reserve`` → ``PlanAdmissionService.persist_and_reserve_candidate``
    appends exactly one ``PlanDrafted`` under ``lane0-reserve:<run_id>:candidate-event``;
    the ``approve`` verb later appends ``PlanApproved``. Neither is a node execution:
    nothing ran, no token burned, no artifact exists. The scan reads only
    ``event_type`` and ``plan_digest``, so the payload here is a stand-in.
    """
    ref = stores.payloads.put(
        _NODE_RUN_SR, dict(_node_run(run_id=run_id)), registry_digest=rt,
        namespace="main", idempotency_key=f"{run_id}:cand:payload")
    stores.events.append(EventAppendRequest(
        run_id=run_id, partition="main", event_type="PlanDrafted",
        payload_schema_ref=_NODE_RUN_SR, payload_ref=ref, registry_digest=rt,
        idempotency_key=f"lane0-reserve:{run_id}:candidate-event", plan_digest=DA))
    if approved:
        stores.events.append(EventAppendRequest(
            run_id=run_id, partition="main", event_type="PlanApproved",
            payload_schema_ref=_NODE_RUN_SR, payload_ref=ref, registry_digest=rt,
            idempotency_key=f"plan-approval:{run_id}:approval-event", plan_digest=DA))


@pytest.mark.parametrize("approved", [False, True])
def test_scan_leaves_a_merely_proposed_run_untouched(tmp_path, approved):
    # THE 2026-07-29 PRODUCTION DEFECT. Every process that binds the durable
    # stores runs this scan, so it runs in the gap between `propose` (process A)
    # and `approve` (process B). A run that has only been PROPOSED has no node
    # events, no committed layer and no burned token — there is nothing to
    # interrupt. Marking it `cancelled` both states a falsehood and consumes the
    # identity's once-only `runresult:<run_id>:payload` slot, after which the
    # approved run executes, burns its LLM invocations, and dies persisting its
    # own RunResult. That is what bricked the Lane-0 driver: no identity could
    # ever survive its own propose→approve gap.
    stores, rt = _stores(tmp_path / "s")
    _seed_proposed_only_run(stores, rt, "lane0-2026-07-29-r2", approved=approved)

    marked = scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())

    assert marked == ()
    assert not any(
        e.event_type is EventType.RUN_CANCELLED
        for e in stores.events.journal("lane0-2026-07-29-r2", "main"))


def test_scan_does_not_burn_the_runresult_slot_of_a_proposed_run(tmp_path):
    # The falsehood is survivable; the burned slot is not. `runresult:<id>:payload`
    # is write-once and RESTART-DURABLE (`_fold_payloads` reloads `payload_idem`
    # from `payloads/_index.jsonl`), so once the scan takes it the identity can
    # never persist a real RunResult again.
    stores, rt = _stores(tmp_path / "s")
    _seed_proposed_only_run(stores, rt, "lane0-2026-07-29-r2", approved=True)
    scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())

    assert ("runresult:lane0-2026-07-29-r2:payload"
            not in stores._shared.backend.payload_idem)


def test_the_propose_approve_gap_leaves_the_identity_runnable(tmp_path):
    # the end-to-end consequence, stated as the operator experiences it: after
    # propose → scan → approve, the identity must still be able to record the
    # RunResult of a real, successful run.
    from guanlan_v2.orchestration import dag as D
    from guanlan_v2.orchestration.runtime_contracts import RunResult

    stores, rt = _stores(tmp_path / "s")
    _seed_proposed_only_run(stores, rt, "lane0-2026-07-29-r2", approved=True)
    scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())

    plan = type("P", (), {"run_id": "lane0-2026-07-29-r2", "plan_digest": DA})
    runtime = type("R", (), {"runtime_registry_digest": rt})
    result = RunResult(run_id="lane0-2026-07-29-r2", plan_digest=DA,
                       terminal_status="completed",
                       settled_tokens=4321, settled_llm_invocations=2)
    D._persist_run_result(stores, runtime, plan, result, "completed")  # must not raise

    assert any(e.event_type is EventType.RUN_COMPLETED
               for e in stores.events.journal("lane0-2026-07-29-r2", "main"))


def test_scan_is_idempotent(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    _seed_in_flight_run(stores, rt, "run-flight")
    first = scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())
    assert first == ("run-flight",)
    # a second scan sees the durable RunCancelled terminal and marks nothing new.
    second = scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())
    assert second == ()
    cancels = [e for e in stores.events.journal("run-flight", "main")
               if e.event_type is EventType.RUN_CANCELLED]
    assert len(cancels) == 1  # exactly one terminal, never doubled


def test_scan_interrupt_survives_restart(tmp_path):
    stores, rt = _stores(tmp_path / "s")
    _seed_in_flight_run(stores, rt, "run-flight")
    scan_and_mark_interrupted(stores, registry_digest=rt, clock=_AdvancingClock())

    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    reopened = build_durable_runtime_stores(
        tmp_path / "s", resolver=resolver, clock=_AdvancingClock(),
        allowed_cell_namespaces=(HEAD_NS,))
    cancels = [e for e in reopened.events.journal("run-flight", "main")
               if e.event_type is EventType.RUN_CANCELLED]
    assert len(cancels) == 1  # the interrupt marking is itself durable


# =========================================================================== #
# Family 3 — one canonical digest, one byte image                             #
#                                                                             #
# LIVE REGRESSION, run ``deep-78abf946944b9984`` (2026-08-11 09:49 CST, the   #
# first FULL ten-node production deep run): node ``pm`` died with             #
# ``PayloadWriteConflict`` — "content-addressed payload main/0a819a86… on     #
# disk differs from the write (write-once integrity)".                        #
#                                                                             #
# The mechanism, verified against the conflicting on-disk object:             #
# ``pv.technical`` (always_invoke, earlier layer) and ``dec.pm``              #
# (cache_or_invoke, later layer) each dispatched the SAME frozen              #
# ``DataRequest`` — same method, params, as_of and frozen universe — through  #
# the SAME code path (``data/registry.py`` dispatch step 2). ``request_id`` is #
# node-scoped (``data/reader.py``: ``f"data:{node_id}:{method_id}"``) and     #
# lives in ``DataRequest.SEMANTIC_EXCLUDE``, so the two requests share ONE    #
# content digest — one content-addressed file — while their evidence          #
# idempotency keys are node-scoped and therefore differ                       #
# (``data/runtime.py::_DataEvidenceWriterAdapter._scoped``): both puts were   #
# genuinely new, and the second presented DIFFERENT bytes for a digest        #
# already on disk.                                                            #
#                                                                             #
# The write-once byte check is a red line and stays exactly as strict. What   #
# these tests pin is its premise: within one store, equal canonical content   #
# digest ⇒ equal stored bytes.                                                #
# =========================================================================== #
_REQUEST_SR = SchemaRef(name="DataRequest", version="1")


def _request_registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(DataRequest)
    reg.seal()
    return reg


def _request_resolver() -> tuple[SchemaRegistryResolver, str]:
    resolver = SchemaRegistryResolver()
    resolver.register(default_registry())
    resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    return resolver, resolver.register(_request_registry())


def _request_stores(root, *, allowed=(HEAD_NS,)):
    """Durable stores whose resolver also carries a sealed ``DataRequest`` registry."""
    resolver, rt = _request_resolver()
    stores = build_durable_runtime_stores(
        root, resolver=resolver, clock=_AdvancingClock(), allowed_cell_namespaces=allowed)
    return stores, rt


def _frozen_request(request_id: str, *, limit=None) -> DataRequest:
    """The live run's frozen ``verified_snapshot`` request, under ``request_id``.

    Field values are the ones read off the conflicting object
    ``var/orchestration/payloads/main/0a819a86….json`` (as_of 2026-08-10T16:00:00Z,
    ONLINE/LIVE, SZ300750, capture root ``lane0-capture-2026-08-11``); the digest is
    sealed exactly as the sole builder ``build_data_request`` seals it.
    """
    fields = dict(
        request_id=request_id,
        data_snapshot_id="lane0-capture-2026-08-11",
        method_spec_ref=ContentRef(id="verified_snapshot", version="1", content_digest="e" * 64),
        params_schema_ref=SchemaRef(name="VerifiedSnapshotParams", version="1"),
        params={"schema_version": "1", "limit": limit,
                "symbols": [{"code": "300750", "exchange": "SZ", "board": "chinext"}]},
        as_of=datetime(2026, 8, 10, 16, 0, tzinfo=UTC),
        mode=DataMode.ONLINE,
        backend=DataBackend.LIVE,
        data_snapshot_content_digest="1" * 64,
        vintage_manifest_digest=None,
        source_config_digest="2" * 64,
        source_registry_digest="3" * 64,
        routing_snapshot_digest="4" * 64,
        request_context_digest="5" * 64,
        schema_registry_digest="6" * 64,
        resolved_chain_digest="7" * 64,
    )
    return DataRequest(
        **fields, request_digest=DataRequest.digest_of_fields(projection="semantic", **fields))


def test_two_nodes_one_frozen_request_is_write_once_safe(tmp_path):
    """The live pm failure: two nodes, one frozen request, one content-addressed file."""
    stores, rt = _request_stores(tmp_path / "s")
    technical = _frozen_request("data:technical:verified_snapshot")
    pm = _frozen_request("data:pm:verified_snapshot")
    # the live shape: audit-only divergence under ONE canonical digest.
    assert technical != pm
    assert technical.request_digest == pm.request_digest

    ref_a = stores.payloads.put(
        _REQUEST_SR, technical, registry_digest=rt, namespace="main",
        idempotency_key="run-1:technical:a1:data:req:request:evidence")
    # before the fix this second put raised PayloadWriteConflict.
    ref_b = stores.payloads.put(
        _REQUEST_SR, pm, registry_digest=rt, namespace="main",
        idempotency_key="run-1:pm:a1:data:req:request:evidence")

    assert ref_a.content_digest == ref_b.content_digest   # one content-addressed file
    assert ref_a.object_id != ref_b.object_id             # two evidence slots (unchanged)
    # equal canonical digest ⇒ equal stored bytes: the second put binds the object
    # the store already holds rather than minting a byte-divergent twin.
    got_a = stores.payloads.get(ref_a, expected_schema_ref=_REQUEST_SR)
    got_b = stores.payloads.get(ref_b, expected_schema_ref=_REQUEST_SR)
    assert got_a is got_b


def test_one_digest_one_byte_image_survives_restart(tmp_path):
    """The in-memory view now agrees with what the fold has always produced.

    ``_fold_payloads`` reads ONE content file per digest, so after a restart both
    object ids already resolved to the same model. Before the fix the pre-restart
    view disagreed with the post-restart view — when the write did not die outright.
    """
    stores, rt = _request_stores(tmp_path / "s")
    ref_a = stores.payloads.put(
        _REQUEST_SR, _frozen_request("data:technical:verified_snapshot"),
        registry_digest=rt, namespace="main", idempotency_key="k-a")
    ref_b = stores.payloads.put(
        _REQUEST_SR, _frozen_request("data:pm:verified_snapshot"),
        registry_digest=rt, namespace="main", idempotency_key="k-b")

    resolver, _ = _request_resolver()
    reopened = build_durable_runtime_stores(
        tmp_path / "s", resolver=resolver, clock=_AdvancingClock(),
        allowed_cell_namespaces=(HEAD_NS,))
    before = stores.payloads.get(ref_b, expected_schema_ref=_REQUEST_SR)
    after = reopened.payloads.get(ref_b, expected_schema_ref=_REQUEST_SR)
    assert before.request_id == after.request_id
    assert after.request_id == reopened.payloads.get(
        ref_a, expected_schema_ref=_REQUEST_SR).request_id


def test_reuse_is_keyed_on_the_full_stored_identity(tmp_path):
    """Guard: the canonicalization keys on namespace+schema+registry+digest, never digest alone."""
    stores, rt = _request_stores(tmp_path / "s")
    a = _frozen_request("data:technical:verified_snapshot")
    ref_a = stores.payloads.put(_REQUEST_SR, a, registry_digest=rt, namespace="main",
                                idempotency_key="k-a")
    # a different NAMESPACE is a different stored identity: its own object + own file.
    ref_c = stores.payloads.put(_REQUEST_SR, a, registry_digest=rt, namespace="review",
                                idempotency_key="k-c")
    assert ref_c.object_id != ref_a.object_id
    assert ref_c.namespace == "review"
    assert (tmp_path / "s" / "payloads" / "review" / f"{ref_a.content_digest}.json").exists()
    assert (tmp_path / "s" / "payloads" / "main" / f"{ref_a.content_digest}.json").exists()


def test_idempotency_conflict_is_untouched(tmp_path):
    """A reused key with genuinely different content still conflicts (no relaxation)."""
    stores, rt = _request_stores(tmp_path / "s")
    stores.payloads.put(
        _REQUEST_SR, _frozen_request("data:technical:verified_snapshot"),
        registry_digest=rt, namespace="main", idempotency_key="k")
    other = _frozen_request("data:technical:verified_snapshot", limit=5)
    assert other.request_digest != _frozen_request("data:pm:verified_snapshot").request_digest
    with pytest.raises(IdempotencyConflict):
        stores.payloads.put(_REQUEST_SR, other, registry_digest=rt, namespace="main",
                            idempotency_key="k")
