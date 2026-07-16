# -*- coding: utf-8 -*-
"""Phase 2 · Task 7 — the capability-confined typed worker executor.

Locks :func:`guanlan_v2.orchestration.worker.execute_node` and its gateways: trusted
resolution only (no handler/model/provider injection point), the two-stage bridge
protocol, the executor-owned evidence sequencer + atomic evidence journal, the closed
CapabilityGateway state machine, prompt confinement + digest-bound single model call,
the evidence classifier and the full terminal status matrix.

Run: ``python -m pytest tests/orchestration/test_worker.py -v``
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog import (
    EvidencePolicy,
    ExecutionSpec,
    OutputBinding,
    WorkerSpec,
)
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ExecutionKind,
    NodeStatus,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import (
    GenericRefusalDetails,
    PromptUntrustedBlockRef,
    default_audit_detail_registry,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration.eventstore import EventRefusalAuditSink
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.schemas import Artifact, NodeRun, NumberAnchor, ToolCallRecord
from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry, CatalogMaterialError

from guanlan_v2.orchestration.admission import ApprovalSubmission, PlanAdmissionService
from guanlan_v2.orchestration.events import PlanApproval

from tests.orchestration.test_runtime_support import (
    OtherOut,
    SR_OUT,
    _Analyzer,
    _BridgeSpec,
    build_scenario,
)

UTC = timezone.utc


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 17, 1, 30, tzinfo=UTC)


# =========================================================================== #
# fake trusted backends (registered behind the same resolver/gateway boundary) #
# =========================================================================== #
class EchoBackend:
    """A trusted capability backend that echoes the request as the result."""

    def invoke(self, *, capability_ref, request):
        return OtherOut(note="tool-result")


@dataclass
class ScriptedProvider:
    """A fake bridge provider whose scripted operations exercise one bridge.

    Runs entirely behind the ExecutionBridgeResolver / CapabilityGateway /
    BridgeEvidenceWriter boundary (it is resolved from the TrustedFactoryRegistry by
    the exact provider handler ref).
    """

    bridge: object
    summary: object
    do_capability: bool = True
    cache_hit: bool = False
    emit_block: bool = True
    force_block: bool = False
    n_calls: int = 1
    raise_in_freeze: BaseException | None = None
    raise_after_capability: BaseException | None = None
    forge_token: bool = False
    forge_record: bool = False
    memory_refs: tuple = ()

    def prepare_input(self, request: "W.BridgePrepareRequest") -> "W.BridgeStageOutcome":
        contribution = W.BridgeInputContribution(memory_refs=tuple(self.memory_refs))
        handle = W.PreparedBridgeHandle(
            bridge_id=self.bridge.bridge_id, bridge_priority=self.bridge.priority,
            summary_digest=self.summary.summary_digest, token=request.token,
            input_contribution=contribution)
        return W.BridgeStageOutcome(status="prepared", input_contribution=contribution,
                                    prepared_handle=handle)

    def open_execution(self, request: "W.BridgeOpenRequest"):
        return _ScriptedSession(self, request)


class _ScriptedSession:
    def __init__(self, provider: ScriptedProvider, request: "W.BridgeOpenRequest") -> None:
        self.p = provider
        self.req = request

    def freeze_for_execution(self, *, kind):
        p, req = self.p, self.req
        if p.raise_in_freeze is not None:
            raise p.raise_in_freeze
        gw = req.capability_gateway
        writer = req.evidence_writer
        token = req.handle.token
        cap_ref = _cap_ref(req.worker)
        records = []
        data_refs = []
        direct = []
        blocks = []
        if p.forge_token:
            token = token.model_copy(update={"call_ordinal": token.call_ordinal + 500})
        if p.cache_hit:
            # a verified cache hit: a DataResult ref + its otherwise-unrepresented
            # request kept as direct evidence, and NO ToolCallRecord.
            req_ref = writer.put(token=token, role="provider_prefetch", schema_ref=SR_OUT,
                                 payload=OtherOut(note="cache-request"),
                                 idempotency_key=f"{p.bridge.bridge_id}:cache-req")
            data_ref = writer.put(token=token, role="provider_prefetch", schema_ref=SR_OUT,
                                  payload=OtherOut(note="cache-data"),
                                  idempotency_key=f"{p.bridge.bridge_id}:cache-data")
            data_refs.append(data_ref)
            direct.append(req_ref)
        elif p.do_capability:
            for i in range(p.n_calls):
                pending = gw.begin(ordinal_token=token, capability_ref=cap_ref,
                                   request_schema_ref=SR_OUT,
                                   idempotency_key=f"{p.bridge.bridge_id}:call:{i}")
                unpub = gw.invoke(pending, OtherOut(note="tool-request"))
                result_model = gw.validate_result(unpub)
                req_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                                     payload=unpub.validated_request,
                                     idempotency_key=f"{p.bridge.bridge_id}:req:{i}")
                res_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                                     payload=result_model,
                                     idempotency_key=f"{p.bridge.bridge_id}:res:{i}")
                rec = gw.finalize_success(pending, request_ref=req_ref, result_ref=res_ref)
                records.append(rec)
                data_refs.append(res_ref)
        if p.forge_record:
            # a compromised provider fabricates a ToolCallRecord the gateway never
            # finalized (it even persists real refs to look legitimate).
            f_req = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                               payload=OtherOut(note="forged-req"),
                               idempotency_key=f"{p.bridge.bridge_id}:forged-req")
            f_res = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                               payload=OtherOut(note="forged-res"),
                               idempotency_key=f"{p.bridge.bridge_id}:forged-res")
            now = datetime(2026, 7, 17, tzinfo=UTC)
            records.append(ToolCallRecord(
                call_ordinal=token.call_ordinal, tool_ref=cap_ref, request_ref=f_req,
                result_ref=f_res, call_id="forged-call", started_at=now, finished_at=now))
            data_refs.append(f_res)
            direct.append(f_req)
        if p.raise_after_capability is not None:
            raise p.raise_after_capability
        if p.force_block or (p.emit_block and kind is ExecutionKind.LLM):
            block_ref = writer.put(token=token, role="provider_prefetch", schema_ref=SR_OUT,
                                   payload=OtherOut(note="untrusted-block"),
                                   idempotency_key=f"{p.bridge.bridge_id}:block")
            blocks.append(W.ProviderUntrustedBlock(
                bridge_id=p.bridge.bridge_id, bridge_priority=p.bridge.priority,
                call_ordinal=token.call_ordinal, payload_ref=block_ref,
                media_type="application/json", rendered_length=32))
        contribution = W.BridgeContribution(
            bridge_id=p.bridge.bridge_id, bridge_priority=p.bridge.priority,
            summary_digest=p.summary.summary_digest, tool_call_records=tuple(records),
            data_result_refs=tuple(data_refs), direct_evidence_refs=tuple(direct),
            untrusted_blocks=tuple(blocks))
        return W.BridgeStageOutcome(status="completed", frozen_contribution=contribution)


class FakeModelGateway:
    """A fake single-shot model gateway that enforces the exact prompt binding."""

    def __init__(self, stores, *, spy=None, payload=None, degraded=False,
                 degradation_reasons=(), raise_exc=None):
        self._stores = stores
        self.spy = spy if spy is not None else []
        self._payload = payload if payload is not None else OtherOut(note="model-out")
        self._degraded = degraded
        self._degradation_reasons = tuple(degradation_reasons)
        self._raise = raise_exc

    def invoke(self, request, *, prompt_assembly_ref):
        # the gateway MUST verify the binding BEFORE producing provider bytes.
        W.verify_model_request_binding(request, prompt_assembly_ref, reader=self._stores.payloads)
        self.spy.append(("invoke", request.request_digest))
        if self._raise is not None:
            raise self._raise
        return W.ModelResult(
            payload=self._payload, rendered_text="model rendered output",
            number_anchors=(), input_tokens=11, output_tokens=7,
            provider="fake-provider", model="fake-model", provider_response_id="resp-1",
            degraded=self._degraded, degradation_reasons=self._degradation_reasons)


def _cap_ref(worker):
    return worker.capability_allowlist[0]


def make_provider_factory(**kw):
    def factory(*, bridge, summary):
        return ScriptedProvider(bridge=bridge, summary=summary, **kw)
    return factory


# =========================================================================== #
# environment: full admission -> node reservation -> input snapshot            #
# =========================================================================== #
@dataclass
class Env:
    sc: object
    clock: FixedClock
    resolver: object
    rt_digest: str
    stores: RuntimeStores
    run_budget: RunBudget
    factories: TrustedFactoryRegistry
    runtime: object
    plan: object
    admitted: object
    plan_res: object
    node_res: object
    ctx: RunContext
    node: object
    worker: object
    input_snapshot: object
    refusal_sink: object

    def sequencer_and_prepared(self, *, provider_factory=None, observer=None):
        provider_factory = provider_factory or make_provider_factory()
        self._register_provider(provider_factory)
        ctx_ref = self._input_context_ref()
        resolver, prepared, seq = W.prepare_input(
            self.plan, self.node, runtime=self.runtime, ctx=self.ctx, stores=self.stores,
            registry=self.sc.registry, context_snapshot_ref=ctx_ref, clock=self.clock,
            observer=observer)
        self._last_seq = seq
        return resolver, prepared, seq

    def _register_provider(self, provider_factory):
        # (re)build a fresh factory registry each call so tests can rebind cleanly.
        self.factories = TrustedFactoryRegistry(self.sc.runtime)
        for rb_id in self.sc.view.bridge_ids():
            rb = self.sc.view.resolve(rb_id)
            self.factories.register_handler(rb.provider_ref, provider_factory)
        self.runtime = W.ExecutionRuntime(
            catalog=self.sc.runtime, bridge_view=self.sc.view, factories=self.factories,
            support_report=self.admitted_support_report, runtime_registry_digest=self.rt_digest)

    def _input_context_ref(self):
        return self.input_snapshot.context_snapshot_ref

    def capability_gateway(self, *, backend=None):
        # the trusted backend binds ONLY through the TrustedFactoryRegistry, keyed
        # by the exact catalog capability identity (never a caller callable).
        chosen = backend or EchoBackend()
        for cap in self.worker.capability_allowlist:
            self.factories.register_capability_backend(cap, lambda **kw: chosen)
        summaries = {s.summary_digest: s for s in self.runtime.summaries_for(self.node.id)}
        gw = W.CapabilityGateway(
            plan_digest=self.plan.plan_digest, worker=self.worker, summaries=summaries,
            catalog=self.sc.runtime, factories=self.factories,
            phase1_registry=self.sc.registry, refusal_sink=self.refusal_sink, clock=self.clock,
            sequencer=self._last_seq)
        return gw

    def model_gateway(self, **kw):
        return FakeModelGateway(self.stores, **kw)

    def run(self, *, provider_factory=None, model_gateway=None, observer=None, backend=None,
            prompt_assembler=None):
        resolver, prepared, seq = self.sequencer_and_prepared(
            provider_factory=provider_factory, observer=observer)
        gw = self.capability_gateway(backend=backend)
        mg = model_gateway if model_gateway is not None else (
            self.model_gateway() if self.worker.execution.kind is ExecutionKind.LLM else None)
        return W.execute_node(
            self.plan, self.node, runtime=self.runtime, prepared_bridges=prepared,
            input_snapshot=self.input_snapshot, ctx=self.ctx, node_reservation=self.node_res,
            bridge_resolver=resolver, model_gateway=mg, capability_gateway=gw,
            registry=self.sc.registry, stores=self.stores, clock=self.clock,
            prompt_assembler=prompt_assembler, observer=observer)


def build_env(*, worker_override=None, deterministic=False, **scenario_kwargs) -> Env:
    sc = build_scenario(**scenario_kwargs)
    if deterministic:
        sc = _make_deterministic(sc)
    if worker_override is not None:
        sc = worker_override(sc)

    clock = FixedClock()
    resolver = SchemaRegistryResolver()
    resolver.register(sc.registry)
    rt_reg = phase2_runtime_registry(default_registry().registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    audit_reg = default_audit_detail_registry()
    audit_reg.seal()
    refusal_sink = EventRefusalAuditSink(detail_registry=audit_reg, clock=clock)
    run_budget = RunBudget(ledger_id="led-1", max_tokens=2_000_000,
                           max_llm_invocations=1000, max_concurrency=16)

    # generous draft budget so the child node reservation fits.
    draft = sc.draft.model_copy(update={
        "budget_request_tokens": 500_000, "budget_request_llm_invocations": 100,
        "max_concurrency": 8})
    sc.draft = draft
    node = draft.nodes[0]
    worker = sc.runtime.worker(node.worker_id)

    # -- run the full admission pipeline ---------------------------------- #
    contexts = {sc.context.content_digest: sc.context}
    approvals = {}
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={sc.request.request_id: sc.request},
        drafts={draft.id: draft}, contexts=contexts, attestations={}, approvals=approvals,
        catalog=sc.runtime, bridge_view=sc.view, phase1_registry=sc.registry,
        runtime_registry_digest=rt_digest, profile=sc.profile, stores=stores,
        run_budget=run_budget, clock=clock)
    prep = service.prepare_candidate(draft.id, request_id=sc.request.request_id)
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
    approvals[(sc.request.request_id, cand.candidate_plan_digest)] = PlanApproval(
        request_id=sc.request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor_id="human-1", decided_at=clock.now())
    ev = service.record_approval(
        cand.candidate_plan_digest,
        ApprovalSubmission(request_id=sc.request.request_id,
                           candidate_plan_digest=cand.candidate_plan_digest,
                           decision=ApprovalDecision.APPROVED),
        authenticated_actor="human-1", idempotency_key="approve-1")
    plan, admitted = service.freeze_and_admit_candidate(
        cand.candidate_plan_digest, reservation_id=res.reservation_id,
        approval_event_id=ev.event_id, idempotency_key="freeze-1")
    bundle = service.verify_for_dispatch(plan.plan_digest)

    # -- reserve the child node budget ------------------------------------ #
    ledger = BudgetLedger(sink=stores.budget_event_sink(run_id=draft.run_id, ledger_id=run_budget.ledger_id),
                          run_budget=run_budget)
    node_res = ledger.reserve_node(
        plan_reservation_id=res.reservation_id, node_id=node.id, attempt=1,
        tokens=1000, llm_invocations=1, concurrency=1, idempotency_key="node-res-1")

    # -- persist the ContextSnapshot + build the frozen InputSnapshot ----- #
    ctx_pref = stores.payloads.put(
        SchemaRef(name="ContextSnapshot", version="1"), sc.context,
        registry_digest=rt_digest, namespace="main", idempotency_key="ctx-snap")
    ctx_ref = TypedPayloadRef(schema_ref=SchemaRef(name="ContextSnapshot", version="1"),
                              payload_ref=ctx_pref)
    from guanlan_v2.orchestration.context import InputSnapshot
    input_snapshot = InputSnapshot.build(
        snapshot_id="isnap-1", run_id=draft.run_id, plan_id=plan.plan_id,
        plan_digest=plan.plan_digest, node_id=node.id, layer_index=0, attempt=1,
        context_snapshot_ref=ctx_ref, artifact_inputs=(), data_result_refs=(),
        memory_record_refs=(), readiness="ready", built_at=clock.now())

    run_ctx = RunContext(
        run_id=draft.run_id, data=sc.context.data_context,
        context_snapshot_id=sc.context.snapshot_id,
        memory_snapshot_hash=sc.context.memory_snapshot_hash, budget=run_budget,
        cancellation_token_id="cancel-1")

    env = Env(
        sc=sc, clock=clock, resolver=resolver, rt_digest=rt_digest, stores=stores,
        run_budget=run_budget, factories=None, runtime=None, plan=plan, admitted=admitted,
        plan_res=res, node_res=node_res, ctx=run_ctx, node=node, worker=worker,
        input_snapshot=input_snapshot, refusal_sink=refusal_sink)
    env.admitted_support_report = bundle.support_report
    return env


def _make_deterministic(sc):
    """Rebuild a scenario whose single worker is DETERMINISTIC (handler-driven)."""
    from guanlan_v2.orchestration.catalog import build_catalog_snapshot
    from guanlan_v2.orchestration.catalog_runtime import (
        BridgeCatalogView, CatalogRuntime, InMemoryMaterialSource, serialize_bridge_descriptor)
    from guanlan_v2.orchestration.catalog import (
        CapabilityManifestEntry, ContentManifestEntry, ResolvedCapabilityMaterial,
        ResolvedTextMaterial, catalog_material_digest)
    from guanlan_v2.orchestration.enums import DataMode
    from guanlan_v2.orchestration.refs import ContentRef
    from guanlan_v2.orchestration.spec import OrchestrationRequest, PlanDraft, PlanNode
    from guanlan_v2.orchestration.enums import ApprovalPolicy, PlanSource
    from guanlan_v2.orchestration.runtime_contracts import ExecutionBridgeDescriptor
    from tests.orchestration.test_runtime_support import (
        _capability, _context, _text, SR_CFG, _registry, _dt)

    registry = _registry()
    handler_ref, handler_mat = _text("handler.det.worker", "handler", "def run(ctx): return ctx")
    cap_ref, cap_mat = _capability("cap.data")
    cfg_ref, cfg_mat = _text("guard.cfg.det", "guardrail", '{"mode":"cache_or_invoke"}')
    prov_ref, prov_mat = _text("handler.provider.det", "handler", "def provide(ctx): return ctx")
    anal_ref, anal_mat = _text("handler.analyzer.det", "handler", "def analyze(ctx): return ctx")
    desc = ExecutionBridgeDescriptor(
        bridge_id="bridge.det", bridge_version="1", priority=10,
        provider_handler_ref=prov_ref, config_ref=cfg_ref, support_analyzer_ref=anal_ref,
        config_schema_ref=SR_CFG, activation_capability_refs=(cap_ref,),
        activation_read_categories=(), supported_execution_kinds=(ExecutionKind.DETERMINISTIC,),
        pre_input_kind="none")
    d_ref, d_mat = _text("guard.det", "guardrail", serialize_bridge_descriptor(desc).decode("utf-8"))
    worker = WorkerSpec(
        id="det.worker", catalog_role="final", selection_scope="dynamic_allowed",
        lane="quant", persona="calc", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler_ref),
        capability_allowlist=(cap_ref,), read_categories=(),
        outputs=(OutputBinding(name="primary", schema_ref=SR_OUT),),
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.OPTIONAL),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none")
    content = [
        ContentManifestEntry(ref=handler_ref, kind="handler", name="H", description="d", source_identity="gl.det"),
        ContentManifestEntry(ref=cfg_ref, kind="guardrail", name="Cfg", description="d", source_identity="gl.det"),
        ContentManifestEntry(ref=prov_ref, kind="handler", name="Prov", description="d", source_identity="gl.det"),
        ContentManifestEntry(ref=anal_ref, kind="handler", name="Anal", description="d", source_identity="gl.det"),
        ContentManifestEntry(ref=d_ref, kind="guardrail", name="bridge.det", description="d", source_identity="gl.det"),
    ]
    cap_manifest = [CapabilityManifestEntry(ref=cap_ref, capability_kind="tool", transport="in_process")]
    mats = [handler_mat, cfg_mat, prov_mat, anal_mat, d_mat, cap_mat]
    snapshot = build_catalog_snapshot(
        catalog_version="det-v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=tuple(cap_manifest), workers=(worker,), resolved_material=tuple(mats))
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats if isinstance(m, ResolvedTextMaterial)},
        capabilities={(m.ref.id, m.ref.version): m.descriptor for m in mats if isinstance(m, ResolvedCapabilityMaterial)})
    runtime = CatalogRuntime.build(snapshot, source)
    analyzers = {(anal_ref.id, anal_ref.version, anal_ref.content_digest): _Analyzer(min_calls=0, max_calls=1)}
    view = BridgeCatalogView.build(runtime, analyzers)
    context = _context()
    node = PlanNode(id="n1", worker_id="det.worker", writes_slot="s1")
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.det", run_id="run-1", request_id="req-1", phase="main", source=PlanSource.DYNAMIC,
        goal="g", as_of=_dt(), mode=DataMode.ONLINE, context_snapshot_ref=ref, nodes=(node,),
        sink_node_ids=("n1",), catalog_version=snapshot.catalog_version,
        catalog_digest=snapshot.catalog_digest, schema_registry_digest=registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED)
    request = OrchestrationRequest(request_id="req-1", goal="g", workflow="orchestrate_only",
                                   approval_policy=ApprovalPolicy.REQUIRED)
    sc.draft = draft
    sc.request = request
    sc.context = context
    sc.snapshot = snapshot
    sc.registry = registry
    sc.runtime = runtime
    sc.view = view
    sc.worker = worker
    return sc


def det_handler_factory(*, worker, resolved):
    def handler(*, node, input_snapshot, contributions, data_result_refs):
        return W.ModelResult(payload=OtherOut(note="deterministic-out"),
                             rendered_text="deterministic rendered", number_anchors=(),
                             input_tokens=0, output_tokens=0)
    return handler


# =========================================================================== #
# 1. LLM + deterministic success through trusted resolution                    #
# =========================================================================== #
def test_llm_success_completed_with_finalized_tool_call():
    e = build_env()
    node_run, artifact = e.run()
    assert isinstance(node_run, NodeRun) and isinstance(artifact, Artifact)
    assert node_run.status is NodeStatus.COMPLETED
    assert len(node_run.tool_call_records) == 1
    assert node_run.tool_call_records[0].call_ordinal == 1
    assert len(node_run.data_result_refs) == 1
    # execution evidence carries exactly the single prompt-record ref.
    assert len(node_run.execution_evidence_refs) == 1
    # Artifact provenance tuples EXACTLY equal the NodeRun tuples.
    prov = artifact.provenance
    assert prov.tool_call_records == node_run.tool_call_records
    assert prov.data_result_refs == node_run.data_result_refs
    assert prov.execution_evidence_refs == node_run.execution_evidence_refs
    assert artifact.payload == OtherOut(note="model-out")
    assert node_run.input_tokens == 11 and node_run.output_tokens == 7
    assert node_run.output_keys == ("primary",)
    assert node_run.output_artifact_ids == (artifact.artifact_id,)


def test_deterministic_success_no_prompt_no_model():
    e = build_env(deterministic=True)
    e._det_handler = True
    # register the deterministic worker handler factory in a runtime built below.
    node_run, artifact = _run_deterministic(e)
    assert node_run.status is NodeStatus.COMPLETED
    assert artifact.provenance.prompt_ref is None
    assert artifact.provenance.model_config_digest is None
    # deterministic keeps only directly consumed evidence; no prompt ref.
    assert artifact.provenance.execution_evidence_refs == node_run.execution_evidence_refs


def _run_deterministic(e, *, prompt_assembler=None, model_gateway=None):
    factories = TrustedFactoryRegistry(e.sc.runtime)
    for rb_id in e.sc.view.bridge_ids():
        rb = e.sc.view.resolve(rb_id)
        factories.register_handler(rb.provider_ref, make_provider_factory(
            do_capability=False, emit_block=False, cache_hit=True))
    factories.register_handler(e.worker.execution.handler_ref, det_handler_factory)
    e.factories = factories
    e.runtime = W.ExecutionRuntime(
        catalog=e.sc.runtime, bridge_view=e.sc.view, factories=factories,
        support_report=e.admitted_support_report, runtime_registry_digest=e.rt_digest)
    resolver, prepared, seq = W.prepare_input(
        e.plan, e.node, runtime=e.runtime, ctx=e.ctx, stores=e.stores, registry=e.sc.registry,
        context_snapshot_ref=e.input_snapshot.context_snapshot_ref, clock=e.clock)
    e._last_seq = seq
    gw = e.capability_gateway()
    return W.execute_node(
        e.plan, e.node, runtime=e.runtime, prepared_bridges=prepared,
        input_snapshot=e.input_snapshot, ctx=e.ctx, node_reservation=e.node_res,
        bridge_resolver=resolver, model_gateway=model_gateway, capability_gateway=gw,
        registry=e.sc.registry, stores=e.stores, clock=e.clock,
        prompt_assembler=prompt_assembler)


# =========================================================================== #
# 2. no injection point; required/extra/forged provider rejected               #
# =========================================================================== #
def test_no_public_handlers_injection_point():
    import inspect

    sig = inspect.signature(W.execute_node)
    assert "handlers" not in sig.parameters


def test_unregistered_provider_factory_rejected_before_execution():
    e = build_env()
    # a runtime whose factory registry has NO provider factory bound.
    empty_factories = TrustedFactoryRegistry(e.sc.runtime)
    e.runtime = W.ExecutionRuntime(
        catalog=e.sc.runtime, bridge_view=e.sc.view, factories=empty_factories,
        support_report=e.admitted_support_report, runtime_registry_digest=e.rt_digest)
    with pytest.raises(W.PreflightError):
        W.prepare_input(e.plan, e.node, runtime=e.runtime, ctx=e.ctx, stores=e.stores,
                        registry=e.sc.registry, context_snapshot_ref=e.input_snapshot.context_snapshot_ref,
                        clock=e.clock)


def test_extra_provider_or_missing_summary_rejected():
    # a catalog with TWO bridges but a support report naming only one summary set.
    e = build_env(bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",)),
                           _BridgeSpec(bridge_id="bridge.extra", priority=20, activation_caps=("cap.data",))])
    # forge a support report with the extra bridge summary stripped.
    sr = e.admitted_support_report
    trimmed = tuple(s for s in sr.bridge_support_summaries if s.bridge_id != "bridge.extra")
    e.admitted_support_report = sr.model_copy(update={"bridge_support_summaries": trimmed})
    with pytest.raises(W.PreflightError):
        e.sequencer_and_prepared()


# =========================================================================== #
# 3. RUNNING precedes bridge work; preflight has zero side effects              #
# =========================================================================== #
def test_running_precedes_bridge_open():
    e = build_env()
    obs = W.ExecutionObserver()
    node_run, _ = e.run(observer=obs)
    assert obs.events[0] == "running"
    assert any(ev.startswith("bridge_open:") for ev in obs.events[1:])


def test_capability_begin_without_running_is_rejected():
    e = build_env()
    resolver, prepared, seq = e.sequencer_and_prepared()
    gw = e.capability_gateway()
    # a fabricated begin BEFORE mark_running is rejected.
    token = prepared.handles[0].token
    with pytest.raises(W.CapabilityGatewayError):
        gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker),
                 request_schema_ref=SR_OUT, idempotency_key="x")


def test_preflight_failure_has_zero_side_effects():
    e = build_env()
    resolver, prepared, seq = e.sequencer_and_prepared()
    before = e.stores.payloads_object_count()
    gw = e.capability_gateway()
    # a terminal_partial snapshot must be rejected by preflight with no side effect.
    bad = e.input_snapshot.model_copy(update={
        "readiness": "terminal_partial", "missing_input_names": ("dep",)})
    # rebuild digest for the mutated snapshot.
    from guanlan_v2.orchestration.context import InputSnapshot
    bad = InputSnapshot.build(**{k: getattr(bad, k) for k in InputSnapshot.model_fields
                                 if k != "content_digest"})
    with pytest.raises(W.PreflightError):
        W.execute_node(e.plan, e.node, runtime=e.runtime, prepared_bridges=prepared,
                       input_snapshot=bad, ctx=e.ctx, node_reservation=e.node_res,
                       bridge_resolver=resolver, model_gateway=e.model_gateway(),
                       capability_gateway=gw, registry=e.sc.registry, stores=e.stores, clock=e.clock)
    assert e.stores.payloads_object_count() == before


# =========================================================================== #
# 4. capability gateway state machine + per-summary arithmetic                 #
# =========================================================================== #
def _running_gateway(e, *, backend=None):
    resolver, prepared, seq = e.sequencer_and_prepared()
    gw = e.capability_gateway(backend=backend)
    gw.mark_running()
    return gw, prepared.handles[0].token, seq


def test_capability_outside_worker_allowlist_is_rejected():
    e = build_env()
    gw, token, seq = _running_gateway(e)
    from guanlan_v2.orchestration.refs import CapabilityRef
    foreign = CapabilityRef(id="cap.evil", version="1", content_digest="a" * 64)
    with pytest.raises(W.CapabilityGatewayError):
        gw.begin(ordinal_token=token, capability_ref=foreign, request_schema_ref=SR_OUT,
                 idempotency_key="k")
    assert gw.finalized_records() == ()


def test_request_schema_mismatch_is_rejected():
    e = build_env()
    gw, token, seq = _running_gateway(e)
    wrong = SchemaRef(name="WrongSchema", version="9")
    with pytest.raises(W.CapabilityGatewayError):
        gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker), request_schema_ref=wrong,
                 idempotency_key="k")


def test_max_capability_invocations_plus_one_is_rejected_before_backend():
    e = build_env()
    gw, token, seq = _running_gateway(e)
    gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker), request_schema_ref=SR_OUT,
             idempotency_key="k1")
    with pytest.raises(W.CapabilityGatewayError):
        gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker), request_schema_ref=SR_OUT,
                 idempotency_key="k2")


def test_finalized_success_below_minimum_is_incomplete():
    e = build_env()
    node_run, artifact = e.run(provider_factory=make_provider_factory(
        do_capability=False, emit_block=False))
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "tool_discipline_unmet"
    assert artifact is None


def test_required_tool_calls_unmet_is_incomplete():
    e = build_env(tool_calls=ToolCallRequirement.REQUIRED)
    node_run, artifact = e.run(provider_factory=make_provider_factory(
        do_capability=False, emit_block=False))
    assert node_run.status is NodeStatus.INCOMPLETE
    assert artifact is None


def test_forbidden_tool_calls_zero_begun_completes():
    e = build_env(tool_calls=ToolCallRequirement.FORBIDDEN, worker_caps=(), bridges=[])
    node_run, artifact = e.run()
    assert node_run.status is NodeStatus.COMPLETED
    assert node_run.tool_call_records == ()


def _writer_for(e, seq):
    return W.BridgeEvidenceWriter(
        stores=e.stores, sequencer=seq, run_id=e.ctx.run_id, plan_digest=e.plan.plan_digest,
        node_id=e.node.id, data_registry_digest=e.sc.registry.registry_digest,
        runtime_registry_digest=e.rt_digest)


def test_pending_finalize_is_idempotent_and_reject_conflict_fails():
    e = build_env()
    gw, token, seq = _running_gateway(e)
    gw.bind_reader(e.stores.payloads)
    pending = gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker),
                       request_schema_ref=SR_OUT, idempotency_key="k1")
    unpub = gw.invoke(pending, OtherOut(note="req"))
    result = gw.validate_result(unpub)
    writer = _writer_for(e, seq)
    req_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                         payload=unpub.validated_request, idempotency_key="req")
    res_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                         payload=result, idempotency_key="res")
    rec1 = gw.finalize_success(pending, request_ref=req_ref, result_ref=res_ref)
    rec2 = gw.finalize_success(pending, request_ref=req_ref, result_ref=res_ref)
    assert rec1 == rec2
    with pytest.raises(W.CapabilityGatewayError):
        gw.reject(pending, detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
                  detail_payload=GenericRefusalDetails(summary="late", category="x"),
                  reason_code="late", idempotency_key="rej")


def test_pending_reject_then_finalize_fails_and_no_success_record():
    e = build_env()
    gw, token, seq = _running_gateway(e)
    pending = gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker),
                       request_schema_ref=SR_OUT, idempotency_key="k1")
    rec = gw.reject(pending, detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
                    detail_payload=GenericRefusalDetails(summary="pit_future", category="x"),
                    reason_code="pit_future", idempotency_key="rej")
    assert rec.reason_code == "pit_future"
    assert gw.finalized_records() == ()
    with pytest.raises(W.CapabilityGatewayError):
        gw.finalize_success(pending, request_ref=_fake_ref(), result_ref=_fake_ref())


def test_finalize_success_performs_no_second_payload_write():
    e = build_env()
    gw, token, seq = _running_gateway(e)
    gw.bind_reader(e.stores.payloads)
    pending = gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker),
                       request_schema_ref=SR_OUT, idempotency_key="k1")
    unpub = gw.invoke(pending, OtherOut(note="req"))
    result = gw.validate_result(unpub)
    writer = _writer_for(e, seq)
    req_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                         payload=unpub.validated_request, idempotency_key="req")
    res_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                         payload=result, idempotency_key="res")
    before = e.stores.payloads_object_count()
    gw.finalize_success(pending, request_ref=req_ref, result_ref=res_ref)
    assert e.stores.payloads_object_count() == before


def _fake_ref():
    return TypedPayloadRef(schema_ref=SR_OUT,
                           payload_ref=PayloadRef(namespace="main", object_id="x", content_digest="a" * 64))


# =========================================================================== #
# 5. cache-hit evidence classification (zero-call, request retained direct)     #
# =========================================================================== #
def test_cache_hit_produces_data_ref_no_tool_record_request_direct():
    e = build_env(bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10,
                                       activation_caps=("cap.data",), min_calls=0, max_calls=1)])
    node_run, artifact = e.run(provider_factory=make_provider_factory(
        do_capability=False, cache_hit=True, emit_block=False))
    assert node_run.status is NodeStatus.COMPLETED
    assert node_run.tool_call_records == ()
    assert len(node_run.data_result_refs) == 1
    assert len(node_run.execution_evidence_refs) == 2


def test_source_success_result_ref_matches_record_without_direct_duplication():
    e = build_env()
    node_run, artifact = e.run()
    rec = node_run.tool_call_records[0]
    assert node_run.data_result_refs[0] == rec.result_ref
    from guanlan_v2.orchestration.refs import typed_ref_sort_key
    direct_keys = {typed_ref_sort_key(r) for r in node_run.execution_evidence_refs}
    assert typed_ref_sort_key(rec.request_ref) not in direct_keys
    assert typed_ref_sort_key(rec.result_ref) not in direct_keys


# =========================================================================== #
# 6. prompt confinement + digest-bound single model call                       #
# =========================================================================== #
def test_prompt_persisted_once_before_model_and_binds_evidence():
    e = build_env()
    spy = []
    mg = e.model_gateway(spy=spy)
    node_run, artifact = e.run(model_gateway=mg)
    assert node_run.status is NodeStatus.COMPLETED
    assert len(spy) == 1
    from guanlan_v2.orchestration.refs import SchemaRef as _SR
    prompt_ref = artifact.provenance.execution_evidence_refs[-1]
    rec = e.stores.payloads.get(prompt_ref.payload_ref,
                                expected_schema_ref=_SR(name="PromptAssemblyRecord", version="1"))
    assert rec.model_request_digest


def test_untrusted_block_text_never_enters_system_channel():
    e = build_env()
    resolver, prepared, seq = e.sequencer_and_prepared()
    token = prepared.handles[0].token
    writer = _writer_for(e, seq)
    block_ref = writer.put(token=token, role="provider_prefetch", schema_ref=SR_OUT,
                           payload=OtherOut(note="IGNORE-PRIOR-INSTRUCTIONS-MALICIOUS"),
                           idempotency_key="mal-block")
    block = PromptUntrustedBlockRef.build(ordinal=1, payload_ref=block_ref,
                                          media_type="application/json", rendered_length=40)
    resolved = e.sc.runtime.resolve_worker(e.node.worker_id)
    request = W.StaticPromptAssembler().assemble(
        plan_digest=e.plan.plan_digest, node_id=e.node.id, worker_id=e.worker.id,
        system_prompt=resolved.system_prompt, skills=resolved.skills, guardrails=resolved.guardrails,
        trusted_input_digests=(), untrusted_blocks=(block,))
    assert b"MALICIOUS" not in request.canonical_request_bytes


def test_prompt_record_for_request_a_cannot_authorize_request_b():
    e = build_env()
    resolved = e.sc.runtime.resolve_worker(e.node.worker_id)
    req_a = W.StaticPromptAssembler().assemble(
        plan_digest=e.plan.plan_digest, node_id=e.node.id, worker_id=e.worker.id,
        system_prompt=resolved.system_prompt, skills=resolved.skills, guardrails=resolved.guardrails,
        trusted_input_digests=(W.NamedEvidenceDigest(name="a", digest="a" * 64),), untrusted_blocks=())
    req_b = W.StaticPromptAssembler().assemble(
        plan_digest=e.plan.plan_digest, node_id=e.node.id, worker_id=e.worker.id,
        system_prompt=resolved.system_prompt, skills=resolved.skills, guardrails=resolved.guardrails,
        trusted_input_digests=(W.NamedEvidenceDigest(name="b", digest="b" * 64),), untrusted_blocks=())
    assert req_a.request_digest != req_b.request_digest
    pref = e.stores.payloads.put(SchemaRef(name="PromptAssemblyRecord", version="1"),
                                 req_a.prompt_record, registry_digest=e.rt_digest, namespace="main",
                                 idempotency_key="prec-a")
    ref_a = TypedPayloadRef(schema_ref=SchemaRef(name="PromptAssemblyRecord", version="1"), payload_ref=pref)
    with pytest.raises(W.PromptBindingError):
        W.verify_model_request_binding(req_b, ref_a, reader=e.stores.payloads)
    assert W.verify_model_request_binding(req_a, ref_a, reader=e.stores.payloads) == req_a.prompt_record


def test_changing_trusted_order_changes_request_digest():
    e = build_env()
    resolved = e.sc.runtime.resolve_worker(e.node.worker_id)
    asm = W.StaticPromptAssembler()
    t1 = (W.NamedEvidenceDigest(name="a", digest="1" * 64), W.NamedEvidenceDigest(name="b", digest="2" * 64))
    r1 = asm.assemble(plan_digest=e.plan.plan_digest, node_id=e.node.id, worker_id=e.worker.id,
                      system_prompt=resolved.system_prompt, skills=resolved.skills,
                      guardrails=resolved.guardrails, trusted_input_digests=t1, untrusted_blocks=())
    t2 = (W.NamedEvidenceDigest(name="a", digest="9" * 64), W.NamedEvidenceDigest(name="b", digest="2" * 64))
    r2 = asm.assemble(plan_digest=e.plan.plan_digest, node_id=e.node.id, worker_id=e.worker.id,
                      system_prompt=resolved.system_prompt, skills=resolved.skills,
                      guardrails=resolved.guardrails, trusted_input_digests=t2, untrusted_blocks=())
    assert r1.request_digest != r2.request_digest


# =========================================================================== #
# 7. execution-kind matrix                                                     #
# =========================================================================== #
def test_llm_retains_exactly_one_persisted_prompt_ref():
    e = build_env()
    node_run, artifact = e.run()
    assert artifact.provenance.prompt_ref == e.worker.system_prompt_ref
    from guanlan_v2.orchestration.refs import SchemaRef as _SR
    prompt_ref = node_run.execution_evidence_refs[-1]
    rec = e.stores.payloads.get(prompt_ref.payload_ref,
                                expected_schema_ref=_SR(name="PromptAssemblyRecord", version="1"))
    assert rec.node_id == e.node.id


def test_deterministic_with_a_block_is_rejected():
    e = build_env(deterministic=True)
    factories = TrustedFactoryRegistry(e.sc.runtime)
    for rb_id in e.sc.view.bridge_ids():
        rb = e.sc.view.resolve(rb_id)
        factories.register_handler(rb.provider_ref, make_provider_factory(
            do_capability=False, force_block=True))
    factories.register_handler(e.worker.execution.handler_ref, det_handler_factory)
    e.factories = factories
    e.runtime = W.ExecutionRuntime(catalog=e.sc.runtime, bridge_view=e.sc.view, factories=factories,
                                   support_report=e.admitted_support_report,
                                   runtime_registry_digest=e.rt_digest)
    resolver, prepared, seq = W.prepare_input(
        e.plan, e.node, runtime=e.runtime, ctx=e.ctx, stores=e.stores, registry=e.sc.registry,
        context_snapshot_ref=e.input_snapshot.context_snapshot_ref, clock=e.clock)
    e._last_seq = seq
    gw = e.capability_gateway()
    with pytest.raises(W.WorkerExecutionError):
        W.execute_node(e.plan, e.node, runtime=e.runtime, prepared_bridges=prepared,
                       input_snapshot=e.input_snapshot, ctx=e.ctx, node_reservation=e.node_res,
                       bridge_resolver=resolver, model_gateway=None, capability_gateway=gw,
                       registry=e.sc.registry, stores=e.stores, clock=e.clock)


# =========================================================================== #
# 8. two providers merge by canonical key; forged token fails                  #
# =========================================================================== #
def test_two_bridges_merge_tool_records_by_call_ordinal():
    e = build_env(bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",)),
                           _BridgeSpec(bridge_id="bridge.two", priority=20, activation_caps=("cap.data",))])
    node_run, artifact = e.run(provider_factory=make_provider_factory(emit_block=False))
    ords = [r.call_ordinal for r in node_run.tool_call_records]
    assert ords == sorted(ords) and len(ords) == 2


def test_forged_issuance_token_fails_at_evidence_put():
    e = build_env()
    with pytest.raises(W.EvidenceTokenError):
        e.run(provider_factory=make_provider_factory(forge_token=True, emit_block=False))


# =========================================================================== #
# 9. journal recovery on failure / timeout / cancel                            #
# =========================================================================== #
def test_failure_after_evidence_recovers_journal_into_node_run():
    e = build_env()
    node_run, artifact = e.run(provider_factory=make_provider_factory(
        emit_block=False, raise_after_capability=RuntimeError("boom after tool")))
    assert node_run.status is NodeStatus.FAILED
    assert node_run.reason_code == "bridge_execution_error"
    assert node_run.error_type == "RuntimeError"
    assert artifact is None
    # the gateway-finalized ToolCallRecord SURVIVES the terminal interruption; its
    # result is the consumed data ref and its request stays represented by the
    # record - the drain path runs the same classifier, so nothing double-lists.
    assert len(node_run.tool_call_records) == 1
    assert node_run.data_result_refs == (node_run.tool_call_records[0].result_ref,)
    assert node_run.execution_evidence_refs == ()


def test_timeout_and_cancel_yield_terminal_status():
    e1 = build_env()
    nr1, a1 = e1.run(provider_factory=make_provider_factory(
        do_capability=False, emit_block=False, raise_in_freeze=W.TimeoutSignal("slow")))
    assert nr1.status is NodeStatus.TIMED_OUT and nr1.reason_code == "node_timeout" and a1 is None
    e2 = build_env()
    nr2, a2 = e2.run(provider_factory=make_provider_factory(
        do_capability=False, emit_block=False, raise_in_freeze=W.CancelSignal("cancelled")))
    assert nr2.status is NodeStatus.CANCELLED and nr2.reason_code == "node_cancelled" and a2 is None


def test_orphan_block_retained_when_assembly_fails_before_prompt_record():
    class _BoomAssembler:
        def assemble(self, **kw):
            raise RuntimeError("assembly boom")

    e = build_env()
    node_run, artifact = e.run(prompt_assembler=_BoomAssembler())
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "prompt_assembly_failed"
    assert artifact is None
    assert len(node_run.execution_evidence_refs) >= 1


# =========================================================================== #
# 10. number anchors / output schema / degraded                                #
# =========================================================================== #
def test_unsourced_number_anchor_is_incomplete():
    e = build_env()
    anchor = NumberAnchor(label="pe", value=1.1, payload_path="pe", is_unsourced=True)

    class _AnchorGateway(FakeModelGateway):
        def invoke(self, request, *, prompt_assembly_ref):
            W.verify_model_request_binding(request, prompt_assembly_ref, reader=self._stores.payloads)
            return W.ModelResult(payload=OtherOut(note="x"), rendered_text="r",
                                 number_anchors=(anchor,), provider="p", model="m",
                                 provider_response_id="r1")

    node_run, artifact = e.run(model_gateway=_AnchorGateway(e.stores))
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "number_anchor_policy"
    assert artifact is None


def test_output_schema_failure_is_incomplete_no_artifact():
    e = build_env()

    class _BadPayloadGateway(FakeModelGateway):
        def invoke(self, request, *, prompt_assembly_ref):
            W.verify_model_request_binding(request, prompt_assembly_ref, reader=self._stores.payloads)
            return W.ModelResult(payload={"unexpected_field": 1}, rendered_text="r",
                                 provider="p", model="m", provider_response_id="r1")

    node_run, artifact = e.run(model_gateway=_BadPayloadGateway(e.stores))
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "output_schema_invalid"
    assert artifact is None
    assert len(node_run.tool_call_records) == 1


def test_model_error_is_failed():
    e = build_env()
    mg = e.model_gateway(raise_exc=ValueError("model blew up"))
    node_run, artifact = e.run(model_gateway=mg)
    assert node_run.status is NodeStatus.FAILED
    assert node_run.reason_code == "model_error" and node_run.error_type == "ValueError"
    assert artifact is None


def test_degraded_input_propagates_to_degraded_status():
    e = build_env()
    mg = e.model_gateway(degraded=True, degradation_reasons=("optional data missing",))
    node_run, artifact = e.run(model_gateway=mg)
    assert node_run.status is NodeStatus.DEGRADED
    assert artifact is not None
    assert artifact.provenance.tool_call_records == node_run.tool_call_records


# =========================================================================== #
# 11. NodeRun identity + provenance layering                                    #
# =========================================================================== #
def test_node_run_carries_real_plan_and_snapshot_identity():
    e = build_env()
    node_run, artifact = e.run()
    assert node_run.run_id == e.plan.run_id
    assert node_run.plan_id == e.plan.plan_id
    assert node_run.plan_digest == e.plan.plan_digest
    assert node_run.node_id == e.node.id
    assert node_run.worker_id == e.worker.id
    assert node_run.attempt == 1
    assert node_run.input_snapshot_digest == e.input_snapshot.content_digest


def test_artifact_three_digest_layers_are_distinct():
    e = build_env()
    _, artifact = e.run()
    digests = {artifact.content_digest, artifact.reproducibility_digest, artifact.audit_digest}
    assert len(digests) == 3
    assert artifact.reproducibility_digest != artifact.content_digest


# =========================================================================== #
# 12. forged tool evidence can never reach sealed provenance                   #
# =========================================================================== #
def test_forged_tool_record_is_rejected_and_never_seals():
    e = build_env()
    node_run, artifact = e.run(provider_factory=make_provider_factory(
        do_capability=False, emit_block=False, forge_record=True))
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "forged_tool_evidence"
    assert artifact is None
    # the forged record is NOT retained: the gateway never finalized it.
    assert node_run.tool_call_records == ()
    # honest retention: the persisted forged request/result refs survive as
    # evidence (data + direct), never as tool-call authority.
    assert len(node_run.data_result_refs) == 1
    assert len(node_run.execution_evidence_refs) == 1


def test_legitimate_finalized_records_pass_the_forged_gate():
    e = build_env()
    node_run, artifact = e.run()
    # the happy path's record IS gateway-finalized and seals normally.
    assert node_run.status is NodeStatus.COMPLETED
    assert len(node_run.tool_call_records) == 1
    assert artifact is not None
    assert artifact.provenance.tool_call_records == node_run.tool_call_records


def test_required_cannot_be_satisfied_by_forged_record():
    e = build_env(tool_calls=ToolCallRequirement.REQUIRED)
    node_run, artifact = e.run(provider_factory=make_provider_factory(
        do_capability=False, emit_block=False, forge_record=True))
    # the forged gate fires BEFORE the REQUIRED arithmetic: a fabricated record
    # can never count toward tool_calls=REQUIRED.
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "forged_tool_evidence"
    assert node_run.tool_call_records == ()
    assert artifact is None


# =========================================================================== #
# 13. cross-summary minimum: an unrelated bridge cannot satisfy another's bound #
# =========================================================================== #
def per_bridge_factory(configs):
    def factory(*, bridge, summary):
        kw = configs.get(bridge.bridge_id, {})
        return ScriptedProvider(bridge=bridge, summary=summary, **kw)
    return factory


def test_unrelated_bridge_cannot_satisfy_another_summary_minimum():
    e = build_env(bridges=[
        _BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",),
                    min_calls=1, max_calls=2),
        _BridgeSpec(bridge_id="bridge.two", priority=20, activation_caps=("cap.data",),
                    min_calls=1, max_calls=2),
    ])
    node_run, artifact = e.run(provider_factory=per_bridge_factory({
        "bridge.data": {"n_calls": 2, "emit_block": False},
        "bridge.two": {"do_capability": False, "emit_block": False},
    }))
    # bridge.data finalized TWO successes, but bridge.two's own minimum (1) is
    # checked independently: the surplus cannot satisfy the other summary.
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "tool_discipline_unmet"
    assert "bridge.two" in (node_run.reason or "")
    assert len(node_run.tool_call_records) == 2  # the real successes retained honestly
    assert artifact is None


# =========================================================================== #
# 14. capability RESULT schema mismatch (adapter-side, fails closed)           #
# =========================================================================== #
class BadResultBackend:
    """A trusted backend whose raw result violates the descriptor output schema."""

    def invoke(self, *, capability_ref, request):
        return {"not_a_valid_field": 1}


def test_capability_result_schema_mismatch_is_rejected():
    from pydantic import ValidationError

    e = build_env()
    gw, token, seq = _running_gateway(e, backend=BadResultBackend())
    pending = gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker),
                       request_schema_ref=SR_OUT, idempotency_key="k1")
    unpub = gw.invoke(pending, OtherOut(note="req"))
    with pytest.raises(ValidationError):
        gw.validate_result(unpub)
    # no ToolCallRecord exists for the invalid result.
    assert gw.finalized_records() == ()


# =========================================================================== #
# 15. reversed completion order cannot change the canonical merge              #
# =========================================================================== #
def test_reversed_completion_merges_by_canonical_key():
    from guanlan_v2.orchestration.refs import CapabilityRef

    now = datetime(2026, 7, 17, tzinfo=UTC)
    cap = CapabilityRef(id="cap.data", version="1", content_digest="a" * 64)

    def _ref(digest):
        return TypedPayloadRef(schema_ref=SR_OUT, payload_ref=PayloadRef(
            namespace="main", object_id=f"obj-{digest[0]}", content_digest=digest))

    rec1 = ToolCallRecord(call_ordinal=1, tool_ref=cap, request_ref=_ref("b" * 64),
                          result_ref=_ref("c" * 64), call_id="c1",
                          started_at=now, finished_at=now)
    rec2 = ToolCallRecord(call_ordinal=2, tool_ref=cap, request_ref=_ref("d" * 64),
                          result_ref=_ref("e" * 64), call_id="c2",
                          started_at=now, finished_at=now)
    c_late = W.BridgeContribution(bridge_id="bridge.two", bridge_priority=20,
                                  summary_digest="f" * 64, tool_call_records=(rec2,))
    c_early = W.BridgeContribution(bridge_id="bridge.data", bridge_priority=10,
                                   summary_digest="f" * 64, tool_call_records=(rec1,))
    # contributions complete in REVERSED order; the merge is canonical regardless.
    merged = W._MergedEvidence.from_contributions((c_late, c_early))
    assert [r.call_ordinal for r in merged.tool_call_records] == [1, 2]
    # a duplicate/conflicting ordinal across bridges fails the merge.
    dup = W.BridgeContribution(bridge_id="bridge.x", bridge_priority=30,
                               summary_digest="f" * 64, tool_call_records=(rec2,))
    with pytest.raises(W.WorkerExecutionError):
        W._MergedEvidence.from_contributions((c_late, dup))


# =========================================================================== #
# 16. deterministic execution NEVER touches PromptAssembler / ModelGateway      #
# =========================================================================== #
def test_deterministic_never_invokes_prompt_assembler_or_model_gateway():
    e = build_env(deterministic=True)
    asm_calls = []
    mg_calls = []

    class SpyAssembler:
        def assemble(self, **kw):
            asm_calls.append(kw)
            raise AssertionError("PromptAssembler must never run for DETERMINISTIC")

    class SpyModelGateway:
        def invoke(self, request, *, prompt_assembly_ref):
            mg_calls.append(request)
            raise AssertionError("ModelGateway must never run for DETERMINISTIC")

    node_run, artifact = _run_deterministic(
        e, prompt_assembler=SpyAssembler(), model_gateway=SpyModelGateway())
    assert node_run.status is NodeStatus.COMPLETED
    assert asm_calls == [] and mg_calls == []  # spies prove zero invocations
    # no render-only block / prompt record was created; consumed evidence retained.
    assert artifact.provenance.prompt_ref is None
    assert len(node_run.data_result_refs) == 1  # the cache-hit DataResult it consumed


# =========================================================================== #
# 17. post-freeze seal: a late writer call is rejected                          #
# =========================================================================== #
def test_late_writer_call_after_freeze_is_rejected():
    e = build_env()
    stash = []

    def factory(*, bridge, summary):
        provider = ScriptedProvider(bridge=bridge, summary=summary, emit_block=False)
        real_open = provider.open_execution

        def stashing_open(request):
            stash.append((request.evidence_writer, request.handle.token))
            return real_open(request)

        provider.open_execution = stashing_open
        return provider

    node_run, artifact = e.run(provider_factory=factory)
    assert node_run.status is NodeStatus.COMPLETED
    writer, token = stash[0]
    # a provider that stashed the writer cannot journal late evidence post-freeze.
    with pytest.raises(W.WorkerExecutionError, match="sealed"):
        writer.put(token=token, role="provider_prefetch", schema_ref=SR_OUT,
                   payload=OtherOut(note="late"), idempotency_key="late-put")
    with pytest.raises(W.WorkerExecutionError, match="sealed"):
        writer.record_existing(token=token, role="provider_prefetch",
                               typed_ref=node_run.data_result_refs[0],
                               idempotency_key="late-rec")


def test_stage1_writer_is_sealed_after_prepare_input():
    e = build_env()
    stash = []

    def factory(*, bridge, summary):
        provider = ScriptedProvider(bridge=bridge, summary=summary, emit_block=False)
        real_prepare = provider.prepare_input

        def stashing_prepare(request):
            stash.append((request.evidence_writer, request.token))
            return real_prepare(request)

        provider.prepare_input = stashing_prepare
        return provider

    resolver, prepared, seq = e.sequencer_and_prepared(provider_factory=factory)
    writer, token = stash[0]
    with pytest.raises(W.WorkerExecutionError, match="sealed"):
        writer.put(token=token, role="memory_prefetch", schema_ref=SR_OUT,
                   payload=OtherOut(note="late-pre"), idempotency_key="late-pre")
