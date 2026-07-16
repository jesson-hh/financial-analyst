# -*- coding: utf-8 -*-
"""Phase 2 · Task 8 — the bounded/stable admitted static DAG runner.

Locks :func:`guanlan_v2.orchestration.dag.run_plan`: verify-for-dispatch entry,
stable Kahn depth layers, closed BLOCK/DEGRADE/SKIP gating, per-node child budget
correlation (zero LLM for deterministic, one for LLM), bounded concurrency, the
staged→barrier commit boundary, sink→RunResult status mapping and crash/replay
idempotent resume.

The scenario builder constructs a *bridge-free* catalog (workers activate no
execution bridge — an empty active-bridge set is fully runtime-supported) so the
runner is exercised end-to-end without the provider/capability machinery, which is
locked by ``test_worker.py``. Deterministic workers need only a handler; LLM
workers need only a system prompt + a fake single-shot ModelGateway.

Run: ``python -m pytest tests/orchestration/test_dag.py -v``
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import pytest

from guanlan_v2.orchestration import dag as D
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    WorkerSpec,
    build_catalog_snapshot,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
)
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DataMode,
    DependencyPolicy,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType, PlanApproval
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    RunResult,
    default_audit_detail_registry,
    phase2_runtime_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.enums import PortfolioRating
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.schemas import ResearchPlan
from guanlan_v2.orchestration.spec import (
    Dependency,
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
)
from guanlan_v2.orchestration.admission import ApprovalSubmission, PlanAdmissionService

from tests.orchestration.test_runtime_support import _context, _text

UTC = timezone.utc

#: worker output schema — a Phase-1 public payload that is in the cumulative Phase-2
#: registry (so the ArtifactPool, which validates against the runtime registry, and
#: Phase-1 validation, which uses ``default_registry``, both resolve it).
SR_OUT = SchemaRef(name="ResearchPlan", version="1")


def _payload(node_id: str) -> ResearchPlan:
    return ResearchPlan(recommendation=PortfolioRating.HOLD, rationale=f"rationale-{node_id}")


class FixedClock:
    """A deterministic aware-UTC clock.

    Returns a *constant* instant so that re-executing a node on idempotent resume
    produces a byte-identical Artifact (``created_at`` is part of the record), which
    lets the pool's stage/commit idempotency re-establish the prior state cleanly.
    """

    def now(self) -> datetime:
        return datetime(2026, 7, 17, 2, 0, tzinfo=UTC)


class FakeModelGateway:
    """A fake single-shot model gateway that enforces the exact prompt binding."""

    def __init__(self, stores):
        self._stores = stores

    def invoke(self, request, *, prompt_assembly_ref):
        W.verify_model_request_binding(request, prompt_assembly_ref, reader=self._stores.payloads)
        return W.ModelResult(
            payload=_payload("model"), rendered_text="rendered", number_anchors=(),
            input_tokens=11, output_tokens=7, provider="fake", model="fake-model",
            provider_response_id="resp-1")


@dataclass
class Control:
    """Closure-captured per-node deterministic behaviour + optional concurrency gate.

    ``behaviours[node_id]``: ``"ok"`` → COMPLETED, ``"degrade"`` → DEGRADED,
    ``"fail"`` → handler raises → FAILED. ``gate`` is an optional
    ``callable(node_id)`` invoked on handler entry (observe/bound concurrency).
    """

    behaviours: dict = field(default_factory=dict)
    gate = None


def make_handler_factory(control: Control):
    def factory(*, worker, resolved):
        def handler(*, node, input_snapshot, contributions, data_result_refs):
            if control.gate is not None:
                control.gate(node.id)
            outcome = control.behaviours.get(node.id, "ok")
            if outcome == "fail":
                raise RuntimeError(f"scripted failure at {node.id}")
            if outcome == "timeout":
                raise W._TimeoutSignal(f"scripted timeout at {node.id}")
            degraded = outcome == "degrade"
            return W.ModelResult(
                payload=_payload(node.id), rendered_text=f"r-{node.id}",
                number_anchors=(), input_tokens=0, output_tokens=0, degraded=degraded,
                degradation_reasons=("scripted-degrade",) if degraded else ())
        return handler
    return factory


@dataclass
class NodeSpec:
    id: str
    deps: tuple = ()
    behaviour: str = "ok"
    worker: str = "root"   # "root" (no inputs) | "chain" (one) | "fan" (many)


@dataclass
class DagEnv:
    plan: object
    admitted: object
    service: PlanAdmissionService
    pool: ArtifactPool
    budget: BudgetLedger
    runtime: object
    registry: object
    stores: RuntimeStores
    clock: FixedClock
    run_ctx: RunContext
    run_budget: RunBudget
    refusal_sink: object
    rt_digest: str
    control: Control
    model_gateway: object
    bundle: object
    snapshot: object
    view: object
    catalog_runtime: object
    factories: object

    def run(self, *, runtime_limit=8, recorder=None, cancellation=None, layer_hook=None,
            model_gateway="__default__", budget=None, runtime=None):
        rec = recorder if recorder is not None else D.RunRecorder()
        mg = self.model_gateway if model_gateway == "__default__" else model_gateway
        result = asyncio.run(D.run_plan(
            self.plan.plan_digest, self.run_ctx, admission=self.service, pool=self.pool,
            budget=budget or self.budget, runtime=runtime or self.runtime, registry=self.registry,
            stores=self.stores, runtime_limit=runtime_limit, clock=self.clock,
            refusal_sink=self.refusal_sink, model_gateway=mg, cancellation=cancellation,
            recorder=rec, layer_hook=layer_hook))
        return result, rec


def _dep(upstream_node_id, inject_as, policy, cardinality="one"):
    slot = f"slot-{upstream_node_id}"
    if policy is DependencyPolicy.BLOCK:
        accept = frozenset({NodeStatus.COMPLETED})
    else:
        accept = frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED})
    return Dependency(upstream_node_id=upstream_node_id, artifact_slot=slot,
                      inject_as=inject_as, policy=policy, accept_statuses=accept)


def _manifest(ref, kind, name):
    return ContentManifestEntry(ref=ref, kind=kind, name=name, description="d",
                                source_identity="gl.dag")


def build_dag_env(
    node_specs,
    *,
    sink_node_ids,
    kind: Literal["deterministic", "llm"] = "deterministic",
    tool_calls=ToolCallRequirement.OPTIONAL,
    plan_tokens=2_000_000,
    plan_llm=1000,
    max_concurrency=8,
    run_concurrency=16,
) -> DagEnv:
    control = Control()
    for ns in node_specs:
        control.behaviours[ns.id] = ns.behaviour

    registry = default_registry()
    clock = FixedClock()

    content = []
    mats = []
    if kind == "deterministic":
        handler_ref, handler_mat = _text("handler.dag.w", "handler", "def run(ctx): return ctx")
        content.append(_manifest(handler_ref, "handler", "H"))
        mats.append(handler_mat)

        def _exec():
            return ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler_ref)
        system_prompt_ref = None
    else:
        prompt_ref, prompt_mat = _text("prompt.dag.w", "prompt", "You are the DAG worker.")
        content.append(_manifest(prompt_ref, "prompt", "P"))
        mats.append(prompt_mat)

        def _exec():
            return ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner")
        system_prompt_ref = prompt_ref

    def _worker(wid, inputs):
        return WorkerSpec(
            id=wid, catalog_role="final", selection_scope="dynamic_allowed", lane="quant",
            persona="calc", tier=Tier.WRITER, execution=_exec(), system_prompt_ref=system_prompt_ref,
            capability_allowlist=(), read_categories=(), inputs=inputs,
            outputs=(OutputBinding(name="primary", schema_ref=SR_OUT),),
            evidence_policy=EvidencePolicy(tool_calls=tool_calls),
            supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none")

    w_root = _worker("dag.root", ())
    w_chain = _worker("dag.chain", (
        InputBinding(name="in_one", schema_ref=SR_OUT, required=False, cardinality="one"),))
    w_fan = _worker("dag.fan", (
        InputBinding(name="in_many", schema_ref=SR_OUT, required=False, cardinality="many"),))
    workers = (w_root, w_chain, w_fan)
    _wid = {"root": "dag.root", "chain": "dag.chain", "fan": "dag.fan"}

    snapshot = build_catalog_snapshot(
        catalog_version="dag-v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=(), workers=workers, resolved_material=tuple(mats))
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats}, capabilities={})
    catalog_runtime = CatalogRuntime.build(snapshot, source)
    view = BridgeCatalogView.build(catalog_runtime, {})

    nodes = tuple(
        PlanNode(id=ns.id, worker_id=_wid[ns.worker], writes_slot=f"slot-{ns.id}",
                 dependencies=tuple(ns.deps))
        for ns in node_specs)
    context = _context()
    ctx_ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.dag", run_id="run-dag", request_id="req-dag", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=clock.now(), mode=DataMode.ONLINE,
        context_snapshot_ref=ctx_ref, nodes=nodes, sink_node_ids=tuple(sink_node_ids),
        catalog_version=snapshot.catalog_version, catalog_digest=snapshot.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=plan_tokens, budget_request_llm_invocations=plan_llm,
        max_concurrency=max_concurrency)
    request = OrchestrationRequest(request_id="req-dag", goal="g", workflow="orchestrate_only",
                                   approval_policy=ApprovalPolicy.REQUIRED)

    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_reg = phase2_runtime_registry(default_registry().registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    audit_reg = default_audit_detail_registry()
    audit_reg.seal()
    refusal_sink = EventRefusalAuditSink(detail_registry=audit_reg, clock=clock)
    run_budget = RunBudget(ledger_id="led-dag", max_tokens=2_000_000, max_llm_invocations=1000,
                           max_concurrency=run_concurrency)

    factories = TrustedFactoryRegistry(catalog_runtime)
    model_gateway = None
    if kind == "deterministic":
        factories.register_handler(w_root.execution.handler_ref, make_handler_factory(control))
    else:
        model_gateway = FakeModelGateway(stores)

    approvals = {}
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request}, drafts={draft.id: draft},
        contexts={context.content_digest: context}, attestations={}, approvals=approvals,
        catalog=catalog_runtime, bridge_view=view, phase1_registry=registry,
        runtime_registry_digest=rt_digest, profile=static_runtime_profile(), stores=stores,
        run_budget=run_budget, clock=clock)
    prep = service.prepare_candidate(draft.id, request_id=request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
    approvals[(request.request_id, cand.candidate_plan_digest)] = PlanApproval(
        request_id=request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor_id="human-1", decided_at=clock.now())
    ev = service.record_approval(
        cand.candidate_plan_digest,
        ApprovalSubmission(request_id=request.request_id,
                           candidate_plan_digest=cand.candidate_plan_digest,
                           decision=ApprovalDecision.APPROVED),
        authenticated_actor="human-1", idempotency_key="approve-1")
    plan, admitted = service.freeze_and_admit_candidate(
        cand.candidate_plan_digest, reservation_id=res.reservation_id, approval_event_id=ev.event_id,
        idempotency_key="freeze-1")
    bundle = service.verify_for_dispatch(plan.plan_digest)

    pool = ArtifactPool(stores=stores, registry_digest=rt_digest, plan=plan, catalog=snapshot,
                        clock=clock)
    budget = BudgetLedger(
        sink=stores.budget_event_sink(run_id=draft.run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)
    runtime = W.ExecutionRuntime(
        catalog=catalog_runtime, bridge_view=view, factories=factories,
        support_report=bundle.support_report, runtime_registry_digest=rt_digest)
    run_ctx = RunContext(
        run_id=draft.run_id, data=context.data_context, context_snapshot_id=context.snapshot_id,
        memory_snapshot_hash=context.memory_snapshot_hash, budget=run_budget,
        cancellation_token_id="cancel-dag")

    return DagEnv(
        plan=plan, admitted=admitted, service=service, pool=pool, budget=budget, runtime=runtime,
        registry=registry, stores=stores, clock=clock, run_ctx=run_ctx, run_budget=run_budget,
        refusal_sink=refusal_sink, rt_digest=rt_digest, control=control, model_gateway=model_gateway,
        bundle=bundle, snapshot=snapshot, view=view, catalog_runtime=catalog_runtime,
        factories=factories)


def _status(rec, node_id):
    runs = [nr for nr in rec.node_runs if nr.node_id == node_id]
    assert runs, f"no NodeRun recorded for {node_id}"
    return runs[-1].status


def _budget_events(env):
    return [ev for ev in env.stores.events.journal(env.plan.run_id, "main")
            if ev.event_type in (EventType.BUDGET_RESERVED, EventType.BUDGET_SETTLED,
                                  EventType.BUDGET_RELEASED)]


def _node_reservations(env):
    st = env.budget.replay()
    return [r for r in st.reservations.values() if r.scope_type == "node"]


# =========================================================================== #
# 1. two-node one-binding + fan-in many ordering                               #
# =========================================================================== #
def test_two_node_one_binding_flows_committed_artifact():
    env = build_dag_env(
        [NodeSpec("a"),
         NodeSpec("b", worker="chain", deps=(_dep("a", "in_one", DependencyPolicy.BLOCK),))],
        sink_node_ids=("b",))
    result, rec = env.run()
    assert isinstance(result, RunResult)
    assert result.terminal_status == "completed"
    assert _status(rec, "a") is NodeStatus.COMPLETED
    assert _status(rec, "b") is NodeStatus.COMPLETED
    assert env.pool.committed_output("a", "primary") is not None
    assert env.pool.committed_output("b", "primary") is not None
    # b's frozen ready snapshot bound a's committed artifact in in_one.
    snap = rec.input_snapshots["b"]
    one = [x for x in snap.artifact_inputs if x.input_name == "in_one"][0]
    assert [r.producer_node_id for r in one.artifact_refs] == ["a"]


def test_fan_in_many_preserves_plan_declaration_order():
    deps = (
        _dep("u2", "in_many", DependencyPolicy.BLOCK, cardinality="many"),
        _dep("u1", "in_many", DependencyPolicy.BLOCK, cardinality="many"),
        _dep("u3", "in_many", DependencyPolicy.BLOCK, cardinality="many"),
    )
    env = build_dag_env(
        [NodeSpec("u1"), NodeSpec("u2"), NodeSpec("u3"),
         NodeSpec("j", worker="fan", deps=deps)],
        sink_node_ids=("j",))
    result, rec = env.run()
    assert result.terminal_status == "completed"
    snap = rec.input_snapshots["j"]
    many = [b for b in snap.artifact_inputs if b.input_name == "in_many"][0]
    assert [r.producer_node_id for r in many.artifact_refs] == ["u2", "u1", "u3"]


# =========================================================================== #
# 2. BLOCK / DEGRADE / SKIP independently                                       #
# =========================================================================== #
def test_unsatisfied_block_yields_blocked_terminal_partial():
    env = build_dag_env(
        [NodeSpec("a", behaviour="fail"),
         NodeSpec("b", worker="chain", deps=(_dep("a", "in_one", DependencyPolicy.BLOCK),))],
        sink_node_ids=("b",))
    result, rec = env.run()
    assert _status(rec, "a") is NodeStatus.FAILED
    assert _status(rec, "b") is NodeStatus.BLOCKED
    # BLOCKED froze a terminal_partial snapshot naming the missing input; no output.
    snap = rec.input_snapshots["b"]
    assert snap.readiness == "terminal_partial"
    assert "in_one" in snap.missing_input_names
    b_run = [nr for nr in rec.node_runs if nr.node_id == "b"][-1]
    assert b_run.output_artifact_ids == ()
    assert b_run.tool_call_records == () and b_run.execution_evidence_refs == ()
    # BLOCKED reserved no child budget.
    assert not any(r.scope_id == "b" for r in _node_reservations(env))
    assert result.terminal_status == "failed"  # a required sink is BLOCKED


def test_unsatisfied_skip_yields_skipped():
    env = build_dag_env(
        [NodeSpec("a", behaviour="fail"),
         NodeSpec("b", worker="chain", deps=(_dep("a", "in_one", DependencyPolicy.SKIP),))],
        sink_node_ids=("b",))
    result, rec = env.run()
    assert _status(rec, "a") is NodeStatus.FAILED
    assert _status(rec, "b") is NodeStatus.SKIPPED
    snap = rec.input_snapshots["b"]
    assert snap.readiness == "terminal_partial"
    assert not any(r.scope_id == "b" for r in _node_reservations(env))


def test_unsatisfied_degrade_omits_input_and_executes():
    env = build_dag_env(
        [NodeSpec("a", behaviour="fail"),
         NodeSpec("b", worker="fan",
                  deps=(_dep("a", "in_many", DependencyPolicy.DEGRADE, cardinality="many"),))],
        sink_node_ids=("b",))
    result, rec = env.run()
    assert _status(rec, "a") is NodeStatus.FAILED
    # DEGRADE: b still runs (its degraded dep is simply omitted); success is at least DEGRADED.
    assert _status(rec, "b") is NodeStatus.DEGRADED
    snap = rec.input_snapshots["b"]
    assert snap.readiness == "ready"  # DEGRADE produces a ready snapshot
    # in_many present but empty (the degraded dep left it unsatisfied).
    many = [x for x in snap.artifact_inputs if x.input_name == "in_many"][0]
    assert many.artifact_refs == ()
    # DEGRADE followed normal preparation/reservation.
    assert any(r.scope_id == "b" for r in _node_reservations(env))
    assert env.pool.committed_output("b", "primary") is not None


# =========================================================================== #
# 3. LLM vs deterministic reservation counts                                    #
# =========================================================================== #
def test_deterministic_reserves_zero_llm_invocations():
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",))
    result, rec = env.run()
    assert result.terminal_status == "completed"
    node_res = [r for r in _node_reservations(env) if r.scope_id == "a"][0]
    assert node_res.reserved_llm_invocations == 0
    assert result.settled_llm_invocations == 0


def test_llm_reserves_one_llm_invocation():
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",), kind="llm")
    result, rec = env.run()
    assert result.terminal_status == "completed"
    node_res = [r for r in _node_reservations(env) if r.scope_id == "a"][0]
    assert node_res.reserved_llm_invocations == 1
    assert result.settled_llm_invocations == 1


def test_llm_model_gateway_resolved_from_factory_when_not_supplied():
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",), kind="llm")
    # do NOT pass a model gateway: the runner must resolve it via the trusted
    # model factory bound to the worker's model tier.
    env.factories.register_model_factory("reasoner", lambda **kw: FakeModelGateway(env.stores))
    result, rec = env.run(model_gateway=None)
    assert result.terminal_status == "completed"
    assert _status(rec, "a") is NodeStatus.COMPLETED


# =========================================================================== #
# 4. bounded concurrency under different completion orders                      #
# =========================================================================== #
def test_bounded_concurrency_never_exceeds_the_min_limit():
    specs = [NodeSpec(f"n{i}") for i in range(4)]
    env = build_dag_env(specs, sink_node_ids=tuple(f"n{i}" for i in range(4)),
                        max_concurrency=8, run_concurrency=16)
    lock = threading.Lock()
    live = {"cur": 0, "max": 0}
    barrier_evt = threading.Event()

    def gate(node_id):
        with lock:
            live["cur"] += 1
            live["max"] = max(live["max"], live["cur"])
        barrier_evt.wait(0.5)
        with lock:
            live["cur"] -= 1

    env.control.gate = gate
    # runtime_limit is the binding minimum → bound = 2.
    barrier_evt.set()  # let handlers proceed (they still overlap under the sem)
    result, rec = env.run(runtime_limit=2)
    assert result.terminal_status == "completed"
    assert live["max"] <= 2, live["max"]
    for i in range(4):
        assert _status(rec, f"n{i}") is NodeStatus.COMPLETED


def test_stable_artifact_sequence_independent_of_completion_order():
    # two roots whose handlers finish in reversed order still commit in (node_id,key).
    specs = [NodeSpec("z"), NodeSpec("a")]
    env = build_dag_env(specs, sink_node_ids=("a", "z"))
    order = []

    def gate(node_id):
        order.append(node_id)
        # make 'z' finish after 'a' by making z wait briefly.
        if node_id == "z":
            import time
            time.sleep(0.05)

    env.control.gate = gate
    result, rec = env.run(runtime_limit=8)
    assert result.terminal_status == "completed"
    lc = rec.layer_commits[0]
    # artifact_seq assigned by (node_id, output_key): a → 1, z → 2.
    by_seq = {c.artifact_seq: env.pool.committed(_ref_of(env, c.artifact_id)).producer_node_id
              for c in lc.artifacts}
    assert by_seq[1] == "a" and by_seq[2] == "z"


def _ref_of(env, artifact_id):
    # reconstruct the committed ArtifactRef from the recorder's node output.
    for a in (env.pool.committed_output("a", "primary"), env.pool.committed_output("z", "primary")):
        if a is not None and a.artifact_id == artifact_id:
            from guanlan_v2.orchestration.schemas import ArtifactRef
            return ArtifactRef(artifact_id=a.artifact_id, schema_ref=a.payload_schema_ref,
                               producer_node_id=a.producer_node_id, slot=a.slot,
                               output_key=a.output_key, content_digest=a.content_digest,
                               relation="input")
    raise AssertionError("artifact not found")


# =========================================================================== #
# 5. budget exhaustion → allocation-denied non-success NodeRun                  #
# =========================================================================== #
def test_budget_exhaustion_denies_allocation_without_hidden_reservation():
    # plan reserves only one LLM invocation; two LLM nodes in sequence → the
    # second reserve is denied (allocation-denied), no hidden reservation.
    env = build_dag_env(
        [NodeSpec("a"), NodeSpec("b", worker="chain", deps=(_dep("a", "in_one", DependencyPolicy.BLOCK),))],
        sink_node_ids=("b",), kind="llm", plan_llm=1)
    result, rec = env.run()
    assert _status(rec, "a") is NodeStatus.COMPLETED
    assert _status(rec, "b") is NodeStatus.INCOMPLETE
    b_run = [nr for nr in rec.node_runs if nr.node_id == "b"][-1]
    assert b_run.reason_code == "budget_denied"
    assert b_run.output_artifact_ids == ()
    # b got a REAL frozen ready snapshot but NO reservation and NO execution.
    assert rec.input_snapshots["b"].readiness == "ready"
    assert not any(r.scope_id == "b" for r in _node_reservations(env))
    assert result.terminal_status == "failed"


# =========================================================================== #
# 6. no next-layer read before LayerCommitted                                   #
# =========================================================================== #
def test_next_layer_reads_only_committed_artifacts():
    env = build_dag_env(
        [NodeSpec("a"), NodeSpec("b", worker="chain", deps=(_dep("a", "in_one", DependencyPolicy.BLOCK),))],
        sink_node_ids=("b",))
    committed_at_layer = {}

    def layer_hook(layer_index, *, committed):
        # snapshot what is committed AFTER this layer's barrier.
        committed_at_layer[layer_index] = set(committed)

    result, rec = env.run(layer_hook=layer_hook)
    assert result.terminal_status == "completed"
    # a's artifact was committed by layer 0's barrier and is the input to b (layer 1).
    assert ("a", "primary") in committed_at_layer[0]
    assert ("b", "primary") not in committed_at_layer[0]
    assert len(rec.layer_commits) == 2
    assert rec.layer_commits[0].layer_index == 0
    assert rec.layer_commits[1].layer_index == 1


# =========================================================================== #
# 7. actual NodeRun ids in LayerCommit (never fabricated nr:{node_id})          #
# =========================================================================== #
def test_layer_commit_binds_real_node_run_ids():
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",))
    result, rec = env.run()
    a_run = [nr for nr in rec.node_runs if nr.node_id == "a"][-1]
    lc = rec.layer_commits[0]
    assert a_run.node_run_id in lc.node_run_ids
    assert f"nr:{a_run.node_id}" not in lc.node_run_ids
    assert a_run.node_run_id != f"nr:{a_run.node_id}"


# =========================================================================== #
# 8. memory-union equality before reservation/RUNNING                           #
# =========================================================================== #
def test_ready_snapshot_memory_refs_equal_canonical_union():
    # bridge-free workers carry the canonical empty base union: exactly ().
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",))
    result, rec = env.run()
    assert result.terminal_status == "completed"
    assert tuple(rec.input_snapshots["a"].memory_record_refs) == ()


# =========================================================================== #
# 9. sink status mapping                                                         #
# =========================================================================== #
def test_degraded_sink_maps_to_partial_degraded_status():
    env = build_dag_env([NodeSpec("a", behaviour="degrade")], sink_node_ids=("a",))
    result, rec = env.run()
    assert _status(rec, "a") is NodeStatus.DEGRADED
    assert result.terminal_status == "degraded"  # frozen-model token for the brief's "partial"


def test_skipped_sink_maps_to_partial_degraded_status():
    # 'a' (not a sink) fails; the sink 'b' SKIPs → usable-skipped → partial (degraded).
    env = build_dag_env(
        [NodeSpec("a", behaviour="fail"),
         NodeSpec("b", worker="chain", deps=(_dep("a", "in_one", DependencyPolicy.SKIP),))],
        sink_node_ids=("b",))
    result, rec = env.run()
    assert _status(rec, "b") is NodeStatus.SKIPPED
    assert result.terminal_status == "degraded"


def test_failed_sink_maps_to_failed_status():
    env = build_dag_env([NodeSpec("a", behaviour="fail")], sink_node_ids=("a",))
    result, rec = env.run()
    assert _status(rec, "a") is NodeStatus.FAILED
    assert result.terminal_status == "failed"


def test_timeout_propagates_timed_out_status_and_failed_sink():
    env = build_dag_env([NodeSpec("a", behaviour="timeout")], sink_node_ids=("a",))
    result, rec = env.run()
    assert _status(rec, "a") is NodeStatus.TIMED_OUT
    assert result.terminal_status == "failed"
    # a timed-out node executed → it holds exactly one real correlated child reservation.
    assert len([r for r in _node_reservations(env) if r.scope_id == "a"]) == 1
    assert env.pool.committed_output("a", "primary") is None


# =========================================================================== #
# 13. bridge-preparation failure → deterministic INCOMPLETE / no reservation    #
# =========================================================================== #
def test_bridge_preparation_failure_incomplete_no_reservation(monkeypatch):
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",))

    def boom(*args, **kwargs):
        raise D.PreflightError("scripted bridge preparation failure")

    monkeypatch.setattr(D, "prepare_input", boom)
    result, rec = env.run()
    assert _status(rec, "a") is NodeStatus.INCOMPLETE
    a_run = [nr for nr in rec.node_runs if nr.node_id == "a"][-1]
    assert a_run.reason_code == "bridge_preparation_failed"
    # no child reservation, no execution, no output.
    assert not any(r.scope_id == "a" for r in _node_reservations(env))
    assert a_run.output_artifact_ids == ()
    assert env.pool.committed_output("a", "primary") is None
    assert result.terminal_status == "failed"


# =========================================================================== #
# 10. cancellation                                                              #
# =========================================================================== #
def test_cancellation_before_layer_records_cancelled_and_commits_nothing():
    env = build_dag_env([NodeSpec("a"), NodeSpec("b")], sink_node_ids=("a", "b"))
    token = D.CancellationToken()
    token.cancel()
    result, rec = env.run(cancellation=token)
    assert result.terminal_status == "cancelled"
    # not-started nodes froze a base snapshot, skipped bridge/reservation/RUNNING,
    # recorded CANCELLED, and no reservation was taken.
    assert _status(rec, "a") is NodeStatus.CANCELLED
    assert _status(rec, "b") is NodeStatus.CANCELLED
    assert _node_reservations(env) == []
    assert env.pool.committed_output("a", "primary") is None
    assert rec.layer_commits == []


# =========================================================================== #
# 11. dispatch rejection on drift + defense-in-depth                            #
# =========================================================================== #
def test_dispatch_rejected_on_reservation_drift():
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",))
    # release the active plan reservation → verify_for_dispatch must fail.
    active = env.budget.get_active_plan("req-dag", env.plan.plan_digest)
    env.budget.release(active.reservation_id, reason="test-drift", idempotency_key="drift-rel")
    with pytest.raises(Exception):
        env.run()


def test_defense_in_depth_mismatched_support_report_rejected():
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",))
    # build a runtime whose support report is bound to a DIFFERENT plan.
    other = build_dag_env([NodeSpec("q")], sink_node_ids=("q",))
    bad_runtime = W.ExecutionRuntime(
        catalog=env.catalog_runtime, bridge_view=env.view, factories=env.factories,
        support_report=other.bundle.support_report, runtime_registry_digest=env.rt_digest)
    with pytest.raises(Exception):
        env.run(runtime=bad_runtime)


# =========================================================================== #
# 12. crash-before / crash-after replay + idempotent resume                     #
# =========================================================================== #
def test_crash_after_layer_commit_resumes_next_layer():
    env = build_dag_env(
        [NodeSpec("a"), NodeSpec("b", worker="chain", deps=(_dep("a", "in_one", DependencyPolicy.BLOCK),))],
        sink_node_ids=("b",))

    class _Crash(Exception):
        pass

    def layer_hook(layer_index, *, committed):
        if layer_index == 0:
            raise _Crash("crash after layer 0 commit")

    with pytest.raises(_Crash):
        env.run(layer_hook=layer_hook)
    # layer 0 committed before the crash; a's output is durable/visible.
    assert env.pool.committed_output("a", "primary") is not None
    assert env.pool.committed_output("b", "primary") is None
    # resume: a fresh run skips the committed layer 0 and finishes layer 1.
    result, rec = env.run()
    assert result.terminal_status == "completed"
    assert env.pool.committed_output("b", "primary") is not None
    # no duplicate node reservation for 'a' (idempotent resume).
    a_res = [r for r in _node_reservations(env) if r.scope_id == "a"]
    assert len(a_res) == 1


def test_crash_before_layer_commit_leaves_no_visible_artifact_then_replays():
    env = build_dag_env([NodeSpec("a")], sink_node_ids=("a",))

    class _Crash(Exception):
        pass

    calls = {"n": 0}
    real_commit = env.pool.commit_layer

    def crashing_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # stage happened; blow up BEFORE the commit persists.
            raise _Crash("crash before commit")
        return real_commit(*args, **kwargs)

    env.pool.commit_layer = crashing_commit  # type: ignore
    with pytest.raises(_Crash):
        env.run()
    assert env.pool.committed_output("a", "primary") is None  # nothing visible
    # restore + resume: the same idempotency keys re-stage/commit cleanly.
    env.pool.commit_layer = real_commit  # type: ignore
    result, rec = env.run()
    assert result.terminal_status == "completed"
    assert env.pool.committed_output("a", "primary") is not None
