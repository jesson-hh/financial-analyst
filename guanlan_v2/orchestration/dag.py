# -*- coding: utf-8 -*-
"""Phase 2 · Task 8 — the bounded, stable, admitted static DAG runner.

:func:`run_plan` is the single execution path that wires everything Phase 2 built
into one deterministic, event-sourced runtime for an *already admitted* static
``Plan``. It never re-derives Plan validity or a competing digest: it dispatches
only through :meth:`PlanAdmissionService.verify_for_dispatch`, which recomputes and
verifies the persisted admission witness (real drift detection). The runner then:

1. derives **stable Kahn depth layers** (nodes sorted by :attr:`PlanNode.id` within
   a layer, ``many`` inputs preserving Plan dependency declaration order);
2. before each layer rechecks cancellation + the active plan reservation;
3. applies the **closed** BLOCK/DEGRADE/SKIP policy to each node against its
   terminal upstream states;
4. for ``BLOCKED``/``SKIPPED`` freezes a Phase-1-valid ``terminal_partial``
   InputSnapshot **first**, then a terminal NodeRun with no bridge token / child
   reservation / RUNNING / executor / output;
5. for executable nodes prepares the support-report-bound bridges (stage 1),
   freezes the exact ``ready`` InputSnapshot whose memory tuple equals the
   Phase-1-canonical union of base + prepared additions, reserves one candidate-
   bound child budget (zero LLM invocations for a deterministic worker, one for an
   LLM worker) and calls Task 7 ``execute_node`` under a semaphore capped by **every**
   bound concurrency limit;
6. settles actuals, stages only COMPLETED/DEGRADED artifacts, then atomically
   ``commit_layer``s in canonical order — only committed artifacts feed the next
   layer;
7. maps sink outcomes onto the frozen ``RunResult`` terminal status and persists it.

Idempotency keys are **deterministic** (run/node/layer/attempt) so a crash before a
``LayerCommitted`` barrier restarts that uncommitted layer, and a crash after it
resumes at the next layer.

**Durable node-terminal records (resume authority).** Every terminal NodeRun —
executed, BLOCKED, SKIPPED, INCOMPLETE, CANCELLED or exception-failed — is
persisted as a registry-validated ``NodeRun@1`` payload plus one ``NodeStateChanged``
RunEvent (one unit of work) *before* its layer commits. Resume derives a committed
layer's per-node terminal status from the latest durable record preceding that
layer's ``LayerCommitted`` barrier — never by inferring status from committed
outputs — so DEGRADED/FAILED/multi-output fidelity is exact and a resumed run's
RunResult equals a clean run's. Artifact availability is verified against the
record's declared ``output_keys``/``output_artifact_ids`` through the pool's
committed index; **resume precondition:** the supplied ArtifactPool must reflect the
same event history (a fresh pool must be ``replay()``ed) or the runner raises an
honest :class:`DagRunError`. Orphaned still-``reserved`` child reservations of a
committed layer (crash between barrier and settlement) are reconciled at resume by
settling the actuals recorded in the durable NodeRun (same idempotency key as the
normal path, keeping resumed == clean settled totals); a reserved child with no
usable record is released with reason ``crash-resume-reconcile``. Plan-level
settle/release is therefore never wedged by crash residue.

**RunResult vocabulary (reported tension).** The brief describes the run terminal
status as ``completed | partial | failed | cancelled``; the frozen Task-5
``RunResult@1`` model's closed vocabulary is
``completed | degraded | incomplete | failed | cancelled``. The frozen model is not
changed (zero golden churn). The brief's sink-mapping rules govern semantics and
are mapped onto the frozen tokens: a required sink that is FAILED/TIMED_OUT/
INCOMPLETE/BLOCKED → ``failed``; a run cancellation → ``cancelled``; usable but
degraded/skipped sinks (the brief's "partial") → ``degraded`` (the frozen token
for a usable-but-lessened run); only fully successful required sinks → ``completed``.
The frozen ``incomplete`` run token is intentionally not emitted by the runner —
the brief folds node-INCOMPLETE sinks into the run-level ``failed`` bucket.

**Model-gateway resolution (reported decision).** A per-LLM-node ``ModelGateway`` is
resolved from the trusted, catalog-ref-keyed factory registry via
``runtime.factories.model_factory(worker.execution.model_tier)(worker=worker)`` (the
carry-forward wiring), unless the caller supplies one service-owned gateway to reuse
across nodes. There is no caller-provided handler/provider/model map: the resolver
and per-node CapabilityGateway are derived from the admitted catalog material.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from guanlan_v2.orchestration.budget import BudgetError, BudgetExceeded, BudgetLedger
from guanlan_v2.orchestration.context import InputArtifactBinding, RunContext
from guanlan_v2.orchestration.digest import audit_digest
from guanlan_v2.orchestration.enums import DependencyPolicy, ExecutionKind, NodeStatus
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    PayloadPutCommand,
    RuntimeBatch,
    StagedPayloadKey,
)
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.runtime_contracts import RunResult
from guanlan_v2.orchestration.schemas import ArtifactRef, NodeRun
from guanlan_v2.orchestration.worker import (
    CapabilityGateway,
    ExecutionRuntime,
    PreflightError,
    WorkerExecutionError,
    execute_node,
    prepare_input,
)

__all__ = ["run_plan", "RunRecorder", "CancellationToken", "DagRunError"]

#: The per-node token reservation ceiling (settlement clamps to actual usage, which
#: never exceeds it). Kept small so a wide layer's simultaneous child holds stay well
#: within the plan reservation; a static-v1 LLM node uses only a handful of tokens.
_NODE_TOKEN_RESERVATION = 4096

_CONTEXT_SCHEMA_REF = SchemaRef(name="ContextSnapshot", version="1")
_RUN_RESULT_SCHEMA_REF = SchemaRef(name="RunResult", version="1")
_LAYER_COMMIT_SCHEMA_REF = SchemaRef(name="LayerCommit", version="1")
_NODE_RUN_SCHEMA_REF = SchemaRef(name="NodeRun", version="1")

_FAILED_SINK_STATUSES = frozenset(
    {NodeStatus.FAILED, NodeStatus.TIMED_OUT, NodeStatus.INCOMPLETE, NodeStatus.BLOCKED}
)
_USABLE_PARTIAL_STATUSES = frozenset({NodeStatus.DEGRADED, NodeStatus.SKIPPED})
_SUCCESS_STATUSES = frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED})


class DagRunError(Exception):
    """A defense-in-depth dispatch/topology failure raised before/at execution."""


class CancellationToken:
    """A cooperative run-cancellation signal (correlated to ``ctx.cancellation_token_id``)."""

    def __init__(self, *, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


class LayerHook(Protocol):
    def __call__(self, layer_index: int, *, committed: frozenset) -> None:  # pragma: no cover
        ...


@dataclass
class RunRecorder:
    """A service-owned in-memory sink for terminal NodeRuns / frozen snapshots / commits.

    The durable authority is the event store: every terminal NodeRun is persisted as
    a registry-validated ``NodeRun@1`` payload behind a ``NodeStateChanged`` RunEvent,
    frozen InputSnapshots are persisted by the pool and LayerCommits by the barrier.
    On resume, the recorder is rebuilt from those durable node/commit records for
    every already-committed layer, so a resumed run's recorder view is complete.
    ``input_snapshots`` is observational only — resumed layers re-expose their
    snapshots via digest on the durable NodeRun, not through this map.
    """

    node_runs: list = field(default_factory=list)
    input_snapshots: dict = field(default_factory=dict)
    layer_commits: list = field(default_factory=list)

    def record_node_run(self, node_run: NodeRun) -> None:
        self.node_runs.append(node_run)

    def record_input_snapshot(self, node_id: str, snapshot) -> None:
        self.input_snapshots[node_id] = snapshot

    def record_layer_commit(self, layer_commit) -> None:
        self.layer_commits.append(layer_commit)


# --------------------------------------------------------------------------- #
# stable Kahn depth layering                                                  #
# --------------------------------------------------------------------------- #
def _kahn_depth_layers(nodes) -> list[list]:
    """Longest-path (Kahn depth) layers; nodes sorted by ``PlanNode.id`` within a layer."""
    from collections import deque

    by_id = {n.id: n for n in nodes}
    preds = {n.id: [d.upstream_node_id for d in n.dependencies] for n in nodes}
    succ: dict[str, list[str]] = {n.id: [] for n in nodes}
    indeg = {n.id: 0 for n in nodes}
    for nid in by_id:
        for p in preds[nid]:
            if p not in by_id:
                raise DagRunError(f"node {nid!r} depends on missing node {p!r}")
            succ[p].append(nid)
            indeg[nid] += 1

    q = deque(sorted(nid for nid in by_id if indeg[nid] == 0))
    order: list[str] = []
    indeg_work = dict(indeg)
    while q:
        nid = q.popleft()
        order.append(nid)
        for s in sorted(succ[nid]):
            indeg_work[s] -= 1
            if indeg_work[s] == 0:
                q.append(s)
    if len(order) != len(by_id):  # pragma: no cover - Phase 1 already rejects cycles
        raise DagRunError("plan DAG contains a cycle (defense-in-depth)")

    depth: dict[str, int] = {}
    for nid in order:
        depth[nid] = 0 if not preds[nid] else max(depth[p] for p in preds[nid]) + 1
    max_depth = max(depth.values(), default=-1)
    layers: list[list] = []
    for lvl in range(max_depth + 1):
        layers.append(sorted((by_id[nid] for nid in by_id if depth[nid] == lvl),
                             key=lambda n: n.id))
    return layers


def _artifact_ref(artifact) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact.artifact_id, schema_ref=artifact.payload_schema_ref,
        producer_node_id=artifact.producer_node_id, slot=artifact.slot,
        output_key=artifact.output_key, content_digest=artifact.content_digest, relation="input")


def _canonical_memory_union(prepared) -> tuple:
    """Base (empty pre-Phase-3) ∪ every completed provider addition, canonically ordered."""
    additions = list(prepared.memory_record_refs())
    additions.sort(key=lambda r: (r.record_id, r.revision_id, r.content_digest))
    return tuple(additions)


# --------------------------------------------------------------------------- #
# gating                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class _Gate:
    outcome: str  # "blocked" | "skipped" | "executable"
    exec_bindings: tuple      # ready read-set: EVERY worker input present (`one` satisfied)
    partial_bindings: tuple   # terminal_partial read-set: only satisfied inputs
    missing_names: tuple
    degraded_inputs: bool


def _evaluate_gate(node, worker, node_status: dict, pool: ArtifactPool) -> _Gate:
    """Apply the closed BLOCK/DEGRADE/SKIP policy against terminal upstream states.

    ``execute_node`` requires the ready snapshot to carry a binding for **every**
    declared worker input (a ``one`` input with exactly one ref, a ``many`` input in
    Plan dependency declaration order — possibly empty). A ``terminal_partial``
    snapshot binds only the inputs actually available and names the unsatisfied ones.
    """
    unsatisfied_block = False
    unsatisfied_skip = False
    degraded_inputs = False
    sat_refs: dict[str, list] = {ib.name: [] for ib in worker.inputs}
    unsatisfied_inputs: set[str] = set()

    for dep in node.dependencies:  # Plan declaration order (semantic for `many`)
        up_status = node_status.get(dep.upstream_node_id)
        up_art = pool.committed_output(dep.upstream_node_id, dep.upstream_output_key)
        ok = up_status is not None and up_status in dep.accept_statuses and up_art is not None
        if ok:
            if dep.inject_as in sat_refs:
                sat_refs[dep.inject_as].append(_artifact_ref(up_art))
        else:
            unsatisfied_inputs.add(dep.inject_as)
            if dep.policy is DependencyPolicy.BLOCK:
                unsatisfied_block = True
            elif dep.policy is DependencyPolicy.SKIP:
                unsatisfied_skip = True
            elif dep.policy is DependencyPolicy.DEGRADE:
                degraded_inputs = True

    exec_b: list[InputArtifactBinding] = []
    part_b: list[InputArtifactBinding] = []
    for ib in worker.inputs:
        refs = sat_refs[ib.name]
        if ib.cardinality == "many":
            exec_b.append(InputArtifactBinding(
                input_name=ib.name, cardinality="many", artifact_refs=tuple(refs)))
            if refs:
                part_b.append(InputArtifactBinding(
                    input_name=ib.name, cardinality="many", artifact_refs=tuple(refs)))
        else:  # one — only bindable when satisfied
            if refs:
                exec_b.append(InputArtifactBinding(
                    input_name=ib.name, cardinality="one", artifact_refs=(refs[0],)))
                part_b.append(InputArtifactBinding(
                    input_name=ib.name, cardinality="one", artifact_refs=(refs[0],)))

    missing = tuple(sorted(unsatisfied_inputs))
    outcome = ("blocked" if unsatisfied_block else
               "skipped" if unsatisfied_skip else "executable")
    return _Gate(outcome, tuple(exec_b), tuple(part_b), missing, degraded_inputs)


# --------------------------------------------------------------------------- #
# terminal NodeRun construction (non-executed branches)                        #
# --------------------------------------------------------------------------- #
def _terminal_node_run(*, run_id, plan, node, worker, status, snapshot, clock,
                       reason_code=None, reason=None) -> NodeRun:
    now = clock_now(clock)
    return NodeRun(
        node_run_id=f"nr-{run_id}-{node.id}-1", run_id=run_id, plan_id=plan.plan_id,
        plan_digest=plan.plan_digest, node_id=node.id, worker_id=worker.id, status=status,
        reason_code=reason_code, reason=reason, attempt_id=f"att-{run_id}-{node.id}-1", attempt=1,
        input_snapshot_digest=snapshot.content_digest, started_at=now, finished_at=now,
        output_keys=(), output_artifact_ids=(), tool_call_records=(), data_result_refs=(),
        execution_evidence_refs=(), input_tokens=0, output_tokens=0)


def _upgrade_to_degraded(node_run: NodeRun) -> NodeRun:
    """Rebuild a COMPLETED NodeRun as DEGRADED (an omitted DEGRADE input degrades success)."""
    if node_run.status is not NodeStatus.COMPLETED:
        return node_run
    data = node_run.model_dump()
    data["status"] = NodeStatus.DEGRADED
    data["reason_code"] = "degraded_inputs"
    data["reason"] = "a DEGRADE dependency was unsatisfied; its input was omitted"
    return NodeRun(**data)


# --------------------------------------------------------------------------- #
# the runner                                                                  #
# --------------------------------------------------------------------------- #
async def run_plan(
    plan_digest: str,
    ctx: RunContext,
    *,
    admission,
    pool: ArtifactPool,
    budget: BudgetLedger,
    runtime: ExecutionRuntime,
    registry,
    stores,
    runtime_limit: int,
    clock: AuthoritativeClock,
    refusal_sink=None,
    model_gateway=None,
    prompt_assembler=None,
    cancellation: CancellationToken | None = None,
    recorder: RunRecorder | None = None,
    layer_hook=None,
) -> RunResult:
    """Execute one admitted static Plan → the frozen :class:`RunResult`.

    ``runtime`` is the Task-7 read-only trusted-material bundle (catalog + bridge
    view + catalog-ref-keyed factories + the runtime registry digest); the per-node
    :class:`ExecutionBridgeResolver` and :class:`CapabilityGateway` are derived from
    it — the caller passes no Plan object, catalog, registry, handler map or raw
    provider map. See the module docstring for the RunResult-vocabulary and
    model-gateway decisions.

    **Resume precondition:** when resuming a crashed run, ``pool`` must reflect the
    same persisted event history as ``stores`` (a freshly constructed pool must be
    ``replay()``ed first). The runner verifies every committed layer's durable
    NodeRun-declared outputs against the pool's committed index and raises
    :class:`DagRunError` on any mismatch instead of silently re-executing.
    """
    rec = recorder if recorder is not None else RunRecorder()

    # ---- (1) verify for dispatch + defense-in-depth ------------------------ #
    bundle = admission.verify_for_dispatch(plan_digest)
    plan = bundle.plan
    if runtime.support_report.report_digest != bundle.support_report.report_digest:
        raise DagRunError("runtime support report does not match the verified dispatch bundle")
    if runtime.catalog.catalog_digest != bundle.plan_admitted.catalog_digest:
        raise DagRunError("runtime catalog digest does not match the admitted catalog")
    if not runtime.support_report.supported:
        raise DagRunError("plan is not runtime-supported")

    plan_reservation = bundle.reservation
    run_id = plan.run_id
    if plan.draft.context_snapshot_ref is not None:
        ctx_ref = TypedPayloadRef(schema_ref=_CONTEXT_SCHEMA_REF,
                                  payload_ref=plan.draft.context_snapshot_ref)
    else:
        # Phase 5 bootstrap (spec §2.0): a `context_snapshot_id=None` plan carries no
        # ContextSnapshot yet — it *produces* one. The frozen InputSnapshot schema
        # still requires a real ContextSnapshot@1 main ref, so the run persists its
        # canonical empty-memory ContextSnapshot once and binds that ref. This branch
        # fires ONLY for a None-ref draft; every static-runtime run keeps the exact
        # prior behavior (bit-unchanged).
        ctx_ref = _synthesize_bootstrap_context_ref(
            ctx, run_id=run_id, stores=stores,
            runtime_registry_digest=runtime.runtime_registry_digest, clock=clock)

    # ---- concurrency bound: min of every declared limit -------------------- #
    bound = min(
        plan.draft.max_concurrency,
        plan_reservation.reserved_concurrency,
        int(runtime_limit),
        ctx.budget.max_concurrency,
    )
    if bound < 1:  # pragma: no cover - all four are positive ints
        raise DagRunError("computed concurrency bound is not positive")

    layers = _kahn_depth_layers(plan.nodes)
    node_records, committed_layers = _load_run_history(stores, run_id)

    node_status: dict[str, NodeStatus] = {}
    committed_outputs: set[tuple[str, str]] = set()
    cancelled = False
    mutation = threading.Lock()

    for layer_index, layer_nodes in enumerate(layers):
        # ---- resume: a committed layer replays its DURABLE node records ----- #
        # (never inferred from committed outputs — exact DEGRADED/FAILED/multi-
        # output fidelity; outputs are consulted only for artifact availability.)
        if layer_index in committed_layers:
            commit_seq, layer_commit = committed_layers[layer_index]
            for node in layer_nodes:
                record = _terminal_record_at_commit(node.id, commit_seq, node_records)
                node_status[node.id] = record.status
                rec.record_node_run(record)
                for pair in _verify_recorded_outputs(record, pool):
                    committed_outputs.add(pair)
                _reconcile_node_reservation(budget, plan_reservation, record, run_id)
            rec.record_layer_commit(layer_commit)
            continue

        # ---- (3) recheck cancellation + active reservation ----------------- #
        if cancellation is not None and cancellation.cancelled:
            cancelled = True
        if not cancelled:
            active = budget.get_active_plan(plan.request_id, plan_digest)
            if active is None or active.reservation_id != plan_reservation.reservation_id:
                raise DagRunError("the active plan reservation is gone at a layer boundary")

        if cancelled:
            _cancel_layer(layer_nodes, run_id=run_id, plan=plan, runtime=runtime, pool=pool,
                          node_status=node_status, ctx_ref=ctx_ref, clock=clock, rec=rec,
                          stores=stores)
            continue

        # ---- (4/5) gating + preparation (sequential, node-id order) -------- #
        prepared_ctxs: list[_PreparedNode] = []
        layer_node_runs: list[NodeRun] = []
        for node in layer_nodes:  # already sorted by id
            worker = runtime.catalog.worker(node.worker_id)
            gate = _evaluate_gate(node, worker, node_status, pool)

            if gate.outcome in ("blocked", "skipped"):
                status = NodeStatus.BLOCKED if gate.outcome == "blocked" else NodeStatus.SKIPPED
                snapshot = pool.freeze_input_snapshot(
                    node, run_id=run_id, plan=plan, layer_index=layer_index, attempt=1,
                    context_snapshot_ref=ctx_ref, bound_artifact_inputs=gate.partial_bindings,
                    readiness="terminal_partial", missing_input_names=gate.missing_names)
                node_run = _terminal_node_run(
                    run_id=run_id, plan=plan, node=node, worker=worker, status=status,
                    snapshot=snapshot, clock=clock,
                    reason_code=("blocking_dependency_unsatisfied" if gate.outcome == "blocked"
                                 else "skip_dependency_unsatisfied"))
                node_status[node.id] = status
                rec.record_input_snapshot(node.id, snapshot)
                rec.record_node_run(node_run)
                _persist_terminal_node_run(stores, runtime, plan, node_run)
                layer_node_runs.append(node_run)
                continue

            # executable: stage-1 bridge prepare (may fail → INCOMPLETE, no reservation).
            try:
                resolver, prepared, seq = prepare_input(
                    plan, node, runtime=runtime, ctx=ctx, stores=stores, registry=registry,
                    context_snapshot_ref=ctx_ref, clock=clock, attempt=1)
            except (PreflightError, WorkerExecutionError) as exc:
                snapshot = pool.freeze_input_snapshot(
                    node, run_id=run_id, plan=plan, layer_index=layer_index, attempt=1,
                    context_snapshot_ref=ctx_ref, bound_artifact_inputs=gate.exec_bindings,
                    memory_record_refs=(), readiness="ready")
                node_run = _terminal_node_run(
                    run_id=run_id, plan=plan, node=node, worker=worker,
                    status=NodeStatus.INCOMPLETE, snapshot=snapshot, clock=clock,
                    reason_code="bridge_preparation_failed", reason=str(exc))
                node_status[node.id] = NodeStatus.INCOMPLETE
                rec.record_input_snapshot(node.id, snapshot)
                rec.record_node_run(node_run)
                _persist_terminal_node_run(stores, runtime, plan, node_run)
                layer_node_runs.append(node_run)
                continue

            memory_union = _canonical_memory_union(prepared)
            snapshot = pool.freeze_input_snapshot(
                node, run_id=run_id, plan=plan, layer_index=layer_index, attempt=1,
                context_snapshot_ref=ctx_ref, bound_artifact_inputs=gate.exec_bindings,
                memory_record_refs=memory_union, readiness="ready")
            rec.record_input_snapshot(node.id, snapshot)

            # ---- (8) reserve the candidate-bound child budget -------------- #
            is_llm = worker.execution.kind is ExecutionKind.LLM
            try:
                node_res = budget.reserve_node(
                    plan_reservation_id=plan_reservation.reservation_id, node_id=node.id, attempt=1,
                    tokens=_NODE_TOKEN_RESERVATION, llm_invocations=(1 if is_llm else 0),
                    concurrency=1, idempotency_key=f"noderes:{run_id}:{node.id}:1")
            except BudgetExceeded as exc:
                node_run = _terminal_node_run(
                    run_id=run_id, plan=plan, node=node, worker=worker,
                    status=NodeStatus.INCOMPLETE, snapshot=snapshot, clock=clock,
                    reason_code="budget_denied", reason=str(exc))
                node_status[node.id] = NodeStatus.INCOMPLETE
                rec.record_node_run(node_run)
                _persist_terminal_node_run(stores, runtime, plan, node_run)
                layer_node_runs.append(node_run)
                continue

            # post-reserve setup: a failure here (e.g. an unbound model factory)
            # must not leak the just-taken child reservation.
            try:
                gw = CapabilityGateway(
                    plan_digest=plan.plan_digest, worker=worker,
                    summaries={s.summary_digest: s for s in runtime.summaries_for(node.id)},
                    catalog=runtime.catalog, factories=runtime.factories, phase1_registry=registry,
                    refusal_sink=refusal_sink, clock=clock, sequencer=seq)
                mg = None
                if is_llm:
                    mg = model_gateway if model_gateway is not None else \
                        runtime.factories.model_factory(worker.execution.model_tier)(worker=worker)
            except Exception as exc:
                _release_if_reserved(budget, node_res, run_id, node.id,
                                     reason=f"executor-setup-failed: {type(exc).__name__}")
                node_run = _terminal_node_run(
                    run_id=run_id, plan=plan, node=node, worker=worker,
                    status=NodeStatus.FAILED, snapshot=snapshot, clock=clock,
                    reason_code="executor_setup_failed", reason=str(exc))
                node_status[node.id] = NodeStatus.FAILED
                rec.record_node_run(node_run)
                _persist_terminal_node_run(stores, runtime, plan, node_run)
                layer_node_runs.append(node_run)
                continue
            prepared_ctxs.append(_PreparedNode(
                node=node, worker=worker, snapshot=snapshot, resolver=resolver, prepared=prepared,
                node_res=node_res, gateway=gw, model_gateway=mg, is_llm=is_llm,
                degraded_inputs=gate.degraded_inputs))

        # ---- (8) bounded concurrent execution (Task 7) --------------------- #
        sem = asyncio.Semaphore(bound)

        async def _exec(pctx: "_PreparedNode"):
            async with sem:
                try:
                    node_run, artifact = await asyncio.to_thread(
                        execute_node, plan, pctx.node, runtime=runtime,
                        prepared_bridges=pctx.prepared, input_snapshot=pctx.snapshot, ctx=ctx,
                        node_reservation=pctx.node_res, bridge_resolver=pctx.resolver,
                        model_gateway=pctx.model_gateway, capability_gateway=pctx.gateway,
                        registry=registry, stores=stores, clock=clock,
                        prompt_assembler=prompt_assembler, attempt=1)
                    if pctx.degraded_inputs:
                        node_run = _upgrade_to_degraded(node_run)
                    return pctx, node_run, artifact, True
                except Exception as exc:
                    # an executor exception (Preflight/WorkerExecution/PromptBinding/…)
                    # must not leak the child reservation nor abort the run silently:
                    # release honestly and persist a reviewed FAILED NodeRun instead.
                    _release_if_reserved(budget, pctx.node_res, run_id, pctx.node.id,
                                         reason=f"executor-exception: {type(exc).__name__}")
                    node_run = _terminal_node_run(
                        run_id=run_id, plan=plan, node=pctx.node, worker=pctx.worker,
                        status=NodeStatus.FAILED, snapshot=pctx.snapshot, clock=clock,
                        reason_code="executor_exception", reason=str(exc))
                    return pctx, node_run, None, False

        exec_results = await asyncio.gather(*(_exec(p) for p in prepared_ctxs))

        # ---- (9/10) settle + stage successes (sequential, deterministic) --- #
        to_settle: list[tuple[_PreparedNode, NodeRun]] = []
        for pctx, node_run, artifact, executed in exec_results:
            node_status[pctx.node.id] = node_run.status
            rec.record_node_run(node_run)
            _persist_terminal_node_run(stores, runtime, plan, node_run)
            layer_node_runs.append(node_run)
            if executed:
                to_settle.append((pctx, node_run))
            if node_run.status in _SUCCESS_STATUSES and artifact is not None:
                with mutation:
                    pool.stage(artifact, layer_index=layer_index, node_run=node_run,
                               idempotency_key=f"stage:{run_id}:{pctx.node.id}:L{layer_index}")

        # ---- (11) barrier: atomically commit the layer's successful outputs  #
        expected = pool.derive_expected_outputs(layer_node_runs)
        layer_commit = pool.commit_layer(
            layer_index, node_runs=layer_node_runs, expected_outputs=expected,
            idempotency_key=f"commit:{run_id}:L{layer_index}")
        rec.record_layer_commit(layer_commit)
        for eo in expected:
            committed_outputs.add((eo.node_id, eo.output_key))

        if layer_hook is not None:
            layer_hook(layer_index, committed=frozenset(committed_outputs))

        # ---- settle actuals (reservations released to the plan pool) ------- #
        for pctx, node_run in to_settle:
            actual_tokens = min(node_run.input_tokens + node_run.output_tokens,
                                _NODE_TOKEN_RESERVATION)
            actual_llm = 1 if (pctx.is_llm and node_run.output_tokens > 0) else 0
            budget.settle(pctx.node_res.reservation_id, actual_tokens=actual_tokens,
                          actual_llm_invocations=actual_llm,
                          idempotency_key=f"settle:{run_id}:{pctx.node.id}:1")

    # ---- (13) map sinks → RunResult and persist ---------------------------- #
    # settled totals are ledger-fold-derived (not in-process counters), so a resumed
    # run reports exactly the same totals as a clean run.
    settled_tokens, settled_llm = _settled_totals(budget, plan_reservation)
    terminal_status = _map_sink_status(plan.sink_node_ids, node_status, cancelled=cancelled)
    result = RunResult(
        run_id=run_id, plan_digest=plan.plan_digest, terminal_status=terminal_status,
        settled_tokens=settled_tokens, settled_llm_invocations=settled_llm)
    _persist_run_result(stores, runtime, plan, result, terminal_status)
    return result


def _synthesize_bootstrap_context_ref(
    ctx: RunContext, *, run_id: str, stores, runtime_registry_digest: str,
    clock: AuthoritativeClock,
) -> TypedPayloadRef:
    """Persist the bootstrap run's canonical empty-memory ContextSnapshot + refs.

    A no-ContextSnapshot bootstrap run still needs a real ``ContextSnapshot@1`` main
    ref for its frozen InputSnapshots (spec §2.0's ``context_snapshot_id=None``
    RunContext produces the snapshot; it does not have one at input time). Persists
    the two canonical empty-memory facts and the empty-memory ContextSnapshot
    (idempotent) and returns its typed ref. The RunContext's ``memory_snapshot_hash``
    already equals the canonical empty-memory hash, so the ContextSnapshot is a pure
    function of the run's DataContext + the canonical empty pair.
    """
    from guanlan_v2.orchestration.context import (
        ContextSnapshot,
        build_empty_memory_binding,
    )

    binding = build_empty_memory_binding()
    snap_sr = SchemaRef(name="EmptyMemorySnapshot", version="1")
    sel_sr = SchemaRef(name="EmptyMemorySelection", version="1")
    stores.payloads.put(
        snap_sr, binding.snapshot, registry_digest=runtime_registry_digest,
        namespace="main", idempotency_key="bootstrap-run:empty-memory-snapshot")
    stores.payloads.put(
        sel_sr, binding.selection, registry_digest=runtime_registry_digest,
        namespace="main", idempotency_key="bootstrap-run:empty-memory-selection")
    context = ContextSnapshot.build(
        snapshot_id=f"bootstrap-run-ctx-{run_id}", data_context=ctx.data,
        memory_snapshot_id=f"bootstrap-run-ms-{run_id}",
        memory_snapshot_hash=binding.snapshot_hash,
        past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, memory_session_id=None, built_at=clock_now(clock))
    ref = stores.payloads.put(
        _CONTEXT_SCHEMA_REF, context, registry_digest=runtime_registry_digest,
        namespace="main", idempotency_key=f"bootstrap-run-ctx:{run_id}")
    return TypedPayloadRef(schema_ref=_CONTEXT_SCHEMA_REF, payload_ref=ref)


@dataclass
class _PreparedNode:
    node: Any
    worker: Any
    snapshot: Any
    resolver: Any
    prepared: Any
    node_res: Any
    gateway: Any
    model_gateway: Any
    is_llm: bool
    degraded_inputs: bool


def _cancel_layer(layer_nodes, *, run_id, plan, runtime, pool, node_status, ctx_ref, clock, rec,
                  stores):
    """Every not-started node freezes its base snapshot and records CANCELLED (no reservation)."""
    for node in layer_nodes:
        worker = runtime.catalog.worker(node.worker_id)
        gate = _evaluate_gate(node, worker, node_status, pool)
        readiness = "terminal_partial" if gate.missing_names else "ready"
        bindings = gate.partial_bindings if readiness == "terminal_partial" else gate.exec_bindings
        snapshot = pool.freeze_input_snapshot(
            node, run_id=run_id, plan=plan, layer_index=0, attempt=1, context_snapshot_ref=ctx_ref,
            bound_artifact_inputs=bindings, readiness=readiness,
            missing_input_names=gate.missing_names if readiness == "terminal_partial" else ())
        node_run = _terminal_node_run(
            run_id=run_id, plan=plan, node=node, worker=worker, status=NodeStatus.CANCELLED,
            snapshot=snapshot, clock=clock, reason_code="run_cancelled")
        node_status[node.id] = NodeStatus.CANCELLED
        rec.record_input_snapshot(node.id, snapshot)
        rec.record_node_run(node_run)
        _persist_terminal_node_run(stores, runtime, plan, node_run)


def _persist_terminal_node_run(stores, runtime, plan, node_run: NodeRun) -> None:
    """Persist one terminal NodeRun payload + ``NodeStateChanged`` event (one UoW).

    The idempotency key embeds the record's Phase-1 audit digest, so a byte-identical
    crash-restart replay returns the stored batch while a semantically different
    restart outcome persists a NEW record — resume disambiguates by taking the latest
    record preceding the layer's ``LayerCommitted`` barrier.
    """
    key = f"nodeterm:{plan.run_id}:{node_run.node_id}:{node_run.attempt}:{audit_digest(node_run)}"
    batch = RuntimeBatch(
        idempotency_key=key,
        payload_puts=(
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="nodeterm"), schema_ref=_NODE_RUN_SCHEMA_REF,
                namespace="main", payload_template=dict(node_run),
                registry_digest=runtime.runtime_registry_digest,
                idempotency_key=f"{key}:payload"),
        ),
        event_appends=(
            EventAppendCommand(
                run_id=plan.run_id, partition="main", event_type="NodeStateChanged",
                payload_schema_ref=_NODE_RUN_SCHEMA_REF,
                payload_target=StagedPayloadKey(key="nodeterm"),
                registry_digest=runtime.runtime_registry_digest,
                idempotency_key=f"{key}:event", plan_digest=plan.plan_digest,
                correlation_id=node_run.node_run_id),
        ),
    )
    stores.unit_of_work.commit(batch)


def _load_run_history(stores, run_id):
    """Event-sourced resume state: durable node-terminal records + committed layers.

    Returns ``(node_records, committed)`` where ``node_records`` is the journal-ordered
    list of ``(journal_seq, NodeRun)`` from ``NodeStateChanged`` events and
    ``committed`` maps ``layer_index -> (journal_seq, LayerCommit)``.
    """
    node_records: list = []
    committed: dict[int, tuple] = {}
    for event in stores.events.journal(run_id, "main"):
        if event.event_type is EventType.NODE_STATE_CHANGED:
            nr = stores.payloads.get(event.payload_ref, expected_schema_ref=_NODE_RUN_SCHEMA_REF)
            node_records.append((event.journal_seq, nr))
        elif event.event_type is EventType.LAYER_COMMITTED:
            lc = stores.payloads.get(event.payload_ref, expected_schema_ref=_LAYER_COMMIT_SCHEMA_REF)
            committed[lc.layer_index] = (event.journal_seq, lc)
    return node_records, committed


def _terminal_record_at_commit(node_id: str, commit_seq: int, node_records) -> NodeRun:
    """The node's terminal status at the barrier: the latest durable record whose
    ``NodeStateChanged`` event precedes the layer's ``LayerCommitted`` event."""
    candidates = [nr for seq, nr in node_records if nr.node_id == node_id and seq < commit_seq]
    if not candidates:
        raise DagRunError(
            f"resume: no durable terminal NodeRun record precedes the layer barrier for "
            f"node {node_id!r} (corrupt or foreign run history)")
    return candidates[-1]


def _verify_recorded_outputs(record: NodeRun, pool) -> list[tuple[str, str]]:
    """Verify each output the durable record declares resolves as committed.

    Iterates the record's ``output_keys``/``output_artifact_ids`` (never a hardcoded
    key), so multi-output and non-``primary`` outputs are exact. A miss means the
    supplied pool does not reflect the run's event history — the resume precondition
    is violated and an honest error is raised instead of silent re-execution.
    """
    pairs: list[tuple[str, str]] = []
    for key, artifact_id in zip(record.output_keys, record.output_artifact_ids):
        artifact = pool.committed_output(record.node_id, key)
        if artifact is None or artifact.artifact_id != artifact_id:
            raise DagRunError(
                "resume precondition violated: the ArtifactPool does not resolve committed "
                f"output ({record.node_id!r}, {key!r}) declared by the durable NodeRun record "
                "— pass a pool replay()ed from the same event history")
        pairs.append((record.node_id, key))
    return pairs


def _release_if_reserved(budget: BudgetLedger, node_res, run_id: str, node_id: str, *, reason: str) -> None:
    """Release the child reservation iff it is still active (idempotent-safe guard)."""
    current = budget.get(node_res.reservation_id)
    if current is not None and current.status == "reserved":
        budget.release(node_res.reservation_id, reason=reason,
                       idempotency_key=f"release:{run_id}:{node_id}:1")


def _reconcile_node_reservation(budget: BudgetLedger, plan_reservation, record: NodeRun, run_id: str) -> None:
    """Reconcile an orphaned still-``reserved`` child of an already-committed layer.

    A crash between the layer barrier and settlement leaves the executed node's child
    reservation ``reserved``; settle it from the durable record's actual token usage
    under the SAME idempotency key as the normal path (resumed settled totals equal a
    clean run's). If the record cannot settle, release with ``crash-resume-reconcile``
    so plan-level settle/release is never wedged by residue.
    """
    state = budget.replay()
    for res_id, parent_id in state.parent_of.items():
        if parent_id != plan_reservation.reservation_id:
            continue
        res = state.reservations[res_id]
        if res.scope_id != record.node_id or res.status != "reserved":
            continue
        actual_tokens = min(record.input_tokens + record.output_tokens, res.reserved_tokens)
        actual_llm = 1 if (res.reserved_llm_invocations > 0 and record.output_tokens > 0) else 0
        try:
            budget.settle(res_id, actual_tokens=actual_tokens, actual_llm_invocations=actual_llm,
                          idempotency_key=f"settle:{run_id}:{record.node_id}:{record.attempt}")
        except BudgetError:
            budget.release(res_id, reason="crash-resume-reconcile",
                           idempotency_key=f"release:{run_id}:{record.node_id}:reconcile")


def _settled_totals(budget: BudgetLedger, plan_reservation) -> tuple[int, int]:
    """Ledger-fold-derived settled (tokens, llm) over this plan's node children —
    replay-derived, so a resumed run reports the same totals as a clean run."""
    state = budget.replay()
    tokens = 0
    llm = 0
    for res_id, parent_id in state.parent_of.items():
        if parent_id != plan_reservation.reservation_id:
            continue
        res = state.reservations[res_id]
        if res.status == "settled":
            tokens += res.actual_tokens
            llm += res.actual_llm_invocations
    return tokens, llm


def _map_sink_status(sink_node_ids, node_status: dict, *, cancelled: bool) -> str:
    if cancelled:
        return "cancelled"
    statuses = [node_status.get(sid) for sid in sink_node_ids]
    if any(s is NodeStatus.CANCELLED for s in statuses):
        return "cancelled"
    if any(s in _FAILED_SINK_STATUSES or s is None for s in statuses):
        return "failed"
    if any(s in _USABLE_PARTIAL_STATUSES for s in statuses):
        return "degraded"
    return "completed"


def _persist_run_result(stores, runtime, plan, result: RunResult, terminal_status: str) -> None:
    """Persist the final RunResult payload + one terminal RunEvent (idempotent)."""
    event_type = {
        "completed": "RunCompleted", "degraded": "RunCompleted", "incomplete": "RunCompleted",
        "failed": "RunFailed", "cancelled": "RunCancelled",
    }[terminal_status]
    batch = RuntimeBatch(
        idempotency_key=f"runresult:{plan.run_id}",
        payload_puts=(
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="runresult"), schema_ref=_RUN_RESULT_SCHEMA_REF,
                namespace="main", payload_template=dict(result),
                registry_digest=runtime.runtime_registry_digest,
                idempotency_key=f"runresult:{plan.run_id}:payload"),
        ),
        event_appends=(
            EventAppendCommand(
                run_id=plan.run_id, partition="main", event_type=event_type,
                payload_schema_ref=_RUN_RESULT_SCHEMA_REF,
                payload_target=StagedPayloadKey(key="runresult"),
                registry_digest=runtime.runtime_registry_digest,
                idempotency_key=f"runresult-ev:{plan.run_id}", plan_digest=plan.plan_digest),
        ),
    )
    stores.unit_of_work.commit(batch)
