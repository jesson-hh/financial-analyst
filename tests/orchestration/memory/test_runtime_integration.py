# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — memory runtime bridge through the REAL Phase-2 pipeline.

Builds a fully-admitted single-node env (real ``PlanAdmissionService`` /
``BudgetLedger`` / ``RuntimeStores`` / ``CapabilityGateway`` /
``BridgeEvidenceWriter`` / sequencer) whose worker carries the ``memory`` read
category, whose active bridge is a ``memory_refs_v1`` ``memory.runtime``
descriptor with the reviewed ``0/0`` bounds, and whose ContextSnapshot binds a
REAL captured MemorySnapshot / MemorySelection / ContextRuntimeRequirements
from the Task-9 preparation service — then proves the FLAGGED worker.py
obligation-1 seam end-to-end: ``InputSnapshot.memory_record_refs`` must equal
the Phase-1-canonical union of a REAL non-empty authorized base plus every
completed PreparedBridgeSet memory addition.

Run: ``pytest tests/orchestration/memory/test_runtime_integration.py -v``
"""
from __future__ import annotations

import pytest

import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration.context import InputSnapshot
from guanlan_v2.orchestration.enums import ExecutionKind, NodeStatus
from guanlan_v2.orchestration.memory import models as M
from guanlan_v2.orchestration.memory.runtime import (
    make_memory_bridge_provider_factory,
    render_memory,
)
from guanlan_v2.orchestration.memory.catalog import serialize_memory_prefetch_binding
from guanlan_v2.orchestration.refs import SchemaRef, TypedPayloadRef
from tests.orchestration.memory._env import SteppingClock, build_memory_world

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------- #
# the fully-admitted memory env                                                #
# --------------------------------------------------------------------------- #
class MemoryEnv:
    def __init__(self, tmp_path):
        from datetime import datetime, timezone

        from guanlan_v2.orchestration.budget import BudgetLedger
        from guanlan_v2.orchestration.admission import PlanAdmissionService
        from guanlan_v2.orchestration.context import ContextSnapshot, RunContext
        from guanlan_v2.orchestration.enums import ApprovalDecision
        from guanlan_v2.orchestration.events import PlanApproval
        from guanlan_v2.orchestration.eventstore import (
            EventRefusalAuditSink,
            RuntimeStores,
            SchemaRegistryResolver,
        )
        _ = ApprovalDecision  # imported for approvals below
        from guanlan_v2.orchestration.admission import ApprovalSubmission
        from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
        from guanlan_v2.orchestration.context import RunBudget
        from guanlan_v2.orchestration.runtime_contracts import (
            default_audit_detail_registry,
            phase2_runtime_registry,
        )
        from guanlan_v2.orchestration.schema_registry import default_registry
        from tests.orchestration.test_runtime_support import (
            SR_OUT,
            _BridgeSpec,
            build_scenario,
        )

        UTC = timezone.utc
        # -- (1) scenario: one LLM worker with the memory read category ------- #
        sc = build_scenario(
            bridges=[_BridgeSpec(bridge_id="memory.runtime", priority=200,
                                 activation_cats=("memory",),
                                 min_calls=0, max_calls=0)],
            worker_read_categories=("context", "memory"),
            worker_params_schema=SR_OUT,
            node_over={"params": {"note": "momentum liquidity"}},
        )
        self.sc = sc
        clock = SteppingClock(start=datetime(2026, 7, 14, 1, 30, tzinfo=UTC))
        resolver = SchemaRegistryResolver()
        resolver.register(sc.registry)
        rt_digest = resolver.register(
            phase2_runtime_registry(default_registry().registry_digest))
        # the persist registry is the CUMULATIVE one (Task 8 finding): the
        # writer stores provider payloads under this digest, so it must resolve
        # the memory schemas + the scenario's OtherOut (test-local composition).
        from guanlan_v2.orchestration.data.schema_registry import (
            PHASE3_PUBLIC_MODELS as DATA_MODELS,
        )
        from guanlan_v2.orchestration.runtime_contracts import (
            Phase2RuntimeRegistry,
            phase2_public_models,
        )
        from tests.orchestration.test_runtime_support import OtherOut

        combined = Phase2RuntimeRegistry()
        for m in (tuple(phase2_public_models()) + tuple(DATA_MODELS)
                  + tuple(M.MEMORY_PUBLIC_MODELS) + (OtherOut,)):
            combined.register(m)
        combined.seal()
        resolver.register(combined)
        self.persist_registry = combined
        stores = RuntimeStores(
            resolver=resolver, clock=clock,
            allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,)
            + tuple(M.PHASE3_MEMORY_STATE_CELL_NAMESPACES))

        # -- (2) the real memory world over the SAME stores ------------------- #
        as_of = sc.context.data_context.as_of  # 2026-07-15 01:30 UTC
        world = build_memory_world(
            tmp_path, stores=stores, resolver=resolver, clock=clock,
            required_registry=sc.registry.registry_digest,
            required_catalog=sc.snapshot.catalog_digest,
            post_cutover_time=None)  # attested 07-14 01:30:0x < as_of 07-15
        self.world = world
        authority = M.MemoryContextAuthority(
            memory_session_id="cs.demo",
            granted_scopes=("console_global", "console_session"),
            authenticated_by="orchestrator-entry")
        binding = world.service.prepare_online(
            sc.context.data_context, authority,
            query_text="brief tables", top_k=2, operation_key="ctx-cap-1")
        assert binding.selected_record_refs, "the E2E base must be REAL and non-empty"
        self.binding = binding

        # -- (3) a REAL memory-bound ContextSnapshot replaces the empty one --- #
        context = ContextSnapshot.build(
            snapshot_id="ctx-1", data_context=sc.context.data_context,
            memory_snapshot_id="ms-1",
            memory_snapshot_hash=binding.memory_snapshot_hash,
            past_context_hash=binding.past_context_hash,
            memory_snapshot_ref=binding.memory_snapshot_ref,
            memory_selection_ref=binding.memory_selection_ref,
            runtime_requirements_ref=binding.runtime_requirements_ref,
            memory_session_id="cs.demo", built_at=as_of)
        from guanlan_v2.orchestration.refs import PayloadRef

        ctx_locator = PayloadRef(namespace="main", object_id="ctx-obj",
                                 content_digest=context.content_digest)
        draft = sc.draft.model_copy(update={
            "context_snapshot_ref": ctx_locator,
            "budget_request_tokens": 500_000,
            "budget_request_llm_invocations": 100,
            "max_concurrency": 8})
        node = draft.nodes[0]
        worker = sc.runtime.worker(node.worker_id)

        # -- (4) real admission over the memory-bound context ------------------ #
        audit_reg = default_audit_detail_registry()
        audit_reg.seal()
        self.refusal_sink = EventRefusalAuditSink(detail_registry=audit_reg, clock=clock)
        run_budget = RunBudget(ledger_id="led-1", max_tokens=2_000_000,
                               max_llm_invocations=1000, max_concurrency=16)
        approvals = {}
        service = PlanAdmissionService(
            run_id=draft.run_id, requests={sc.request.request_id: sc.request},
            drafts={draft.id: draft},
            contexts={context.content_digest: context},
            attestations={}, approvals=approvals,
            catalog=sc.runtime, bridge_view=sc.view, phase1_registry=sc.registry,
            runtime_registry_digest=rt_digest, profile=sc.profile, stores=stores,
            run_budget=run_budget, clock=clock)
        prep = service.prepare_candidate(draft.id, request_id=sc.request.request_id)
        cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="reserve-1")
        approvals[(sc.request.request_id, cand.candidate_plan_digest)] = PlanApproval(
            request_id=sc.request.request_id,
            candidate_plan_digest=cand.candidate_plan_digest,
            decision=ApprovalDecision.APPROVED, actor_id="human-1",
            decided_at=clock.now())
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
        self.support_report = bundle.support_report
        assert self.support_report.supported, self.support_report.issues
        # the requirements were resolved + embedded before ANY reservation.
        assert self.support_report.context_requirements_ref == (
            binding.runtime_requirements_ref)

        ledger = BudgetLedger(
            sink=stores.budget_event_sink(run_id=draft.run_id,
                                          ledger_id=run_budget.ledger_id),
            run_budget=run_budget)
        node_res = ledger.reserve_node(
            plan_reservation_id=res.reservation_id, node_id=node.id, attempt=1,
            tokens=1000, llm_invocations=1, concurrency=1, idempotency_key="node-res-1")

        # -- (5) persist the context; register the REAL provider --------------- #
        ctx_pref = stores.payloads.put(
            SchemaRef(name="ContextSnapshot", version="1"), context,
            registry_digest=world.registry_digest, namespace="main",
            idempotency_key="ctx-snap")
        self.context_ref = TypedPayloadRef(
            schema_ref=SchemaRef(name="ContextSnapshot", version="1"),
            payload_ref=ctx_pref)
        prefetch = M.MemoryBridgePrefetchBinding.build(
            bridge_id="memory.runtime", bridge_version="1",
            rows=(M.MemoryQueryProjection(
                worker_id=worker.id, query_text_param_pointer="/note",
                top_k=2),))
        from guanlan_v2.orchestration.refs import ContentRef

        factory = make_memory_bridge_provider_factory(
            stores=stores, policy=world.policy,
            renderer_ref=ContentRef(id="memory.render.deterministic", version="1",
                                    content_digest="1" * 64),
            config_bytes=serialize_memory_prefetch_binding(prefetch))
        factories = TrustedFactoryRegistry(sc.runtime)
        for rb_id in sc.view.bridge_ids():
            rb = sc.view.resolve(rb_id)
            factories.register_handler(rb.provider_ref, factory)
        self.factories = factories
        self.runtime = W.ExecutionRuntime(
            catalog=sc.runtime, bridge_view=sc.view, factories=factories,
            support_report=self.support_report, runtime_registry_digest=rt_digest)

        self.stores, self.clock, self.plan = stores, clock, plan
        self.node, self.worker, self.node_res = node, worker, node_res
        self.run_budget = run_budget
        self.ctx = RunContext(
            run_id=draft.run_id, data=context.data_context,
            context_snapshot_id=context.snapshot_id,
            memory_snapshot_hash=context.memory_snapshot_hash, budget=run_budget,
            cancellation_token_id="cancel-1")
        self.base_refs = binding.selected_record_refs

    # -- helpers --------------------------------------------------------------- #
    def prepare(self):
        resolver, prepared, seq = W.prepare_input(
            self.plan, self.node, runtime=self.runtime, ctx=self.ctx,
            stores=self.stores, registry=self.persist_registry,
            context_snapshot_ref=self.context_ref, clock=self.clock)
        self._seq = seq
        return resolver, prepared

    def input_snapshot(self, prepared, *, refs=None):
        refs = refs if refs is not None else W._expected_memory_refs(
            prepared, base=self.base_refs)
        return InputSnapshot.build(
            snapshot_id="isnap-1", run_id=self.plan.run_id, plan_id=self.plan.plan_id,
            plan_digest=self.plan.plan_digest, node_id=self.node.id, layer_index=0,
            attempt=1, context_snapshot_ref=self.context_ref, artifact_inputs=(),
            data_result_refs=(), memory_record_refs=refs, readiness="ready",
            built_at=self.clock.now())

    def gateway(self):
        summaries = {s.summary_digest: s for s in self.runtime.summaries_for(self.node.id)}
        return W.CapabilityGateway(
            plan_digest=self.plan.plan_digest, worker=self.worker,
            summaries=summaries, catalog=self.sc.runtime, factories=self.factories,
            phase1_registry=self.sc.registry, refusal_sink=self.refusal_sink,
            clock=self.clock, sequencer=self._seq)

    def run(self, *, refs=None):
        from tests.orchestration.test_worker import FakeModelGateway

        resolver, prepared = self.prepare()
        snap = self.input_snapshot(prepared, refs=refs)
        return W.execute_node(
            self.plan, self.node, runtime=self.runtime, prepared_bridges=prepared,
            input_snapshot=snap, ctx=self.ctx, node_reservation=self.node_res,
            bridge_resolver=resolver, model_gateway=FakeModelGateway(self.stores),
            capability_gateway=self.gateway(), registry=self.persist_registry,
            stores=self.stores, clock=self.clock,
            base_authorized_memory_refs=self.base_refs), prepared


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    return MemoryEnv(tmp_path_factory.mktemp("memenv"))


# --------------------------------------------------------------------------- #
# pre-admission → requirements → admission (context path)                      #
# --------------------------------------------------------------------------- #
def test_admission_resolves_and_embeds_the_memory_requirements(env):
    """The full closure (registry/catalog/bridge requirements) is resolved and
    enforced BEFORE any budget reservation (the support report embeds it)."""
    assert env.support_report.context_requirements_digest is not None
    req = env.stores.payloads.get(
        env.binding.runtime_requirements_ref.payload_ref,
        expected_schema_ref=env.binding.runtime_requirements_ref.schema_ref)
    assert req.required_bridge_ids == ("memory.runtime",)
    assert req.required_schema_registry_digest == env.sc.registry.registry_digest
    assert req.required_catalog_digest == env.sc.snapshot.catalog_digest


def test_non_empty_memory_context_requires_the_requirements_ref(env):
    from guanlan_v2.orchestration.context import ContextSnapshot

    with pytest.raises(ValueError, match="runtime_requirements_ref"):
        ContextSnapshot.build(
            snapshot_id="ctx-x", data_context=env.sc.context.data_context,
            memory_snapshot_id="ms-1",
            memory_snapshot_hash=env.binding.memory_snapshot_hash,
            past_context_hash=env.binding.past_context_hash,
            memory_snapshot_ref=env.binding.memory_snapshot_ref,
            memory_selection_ref=env.binding.memory_selection_ref,
            runtime_requirements_ref=None,
            memory_session_id="cs.demo", built_at=env.clock.now())


# --------------------------------------------------------------------------- #
# obligation 1 — the InputSnapshot canonical-union equality, END TO END        #
# --------------------------------------------------------------------------- #
def test_prepare_input_persists_query_and_selection_before_the_freeze(env):
    _resolver, prepared = env.prepare()
    handle = prepared.handles[0]
    assert handle.bridge_id == "memory.runtime"
    additions = prepared.memory_record_refs()
    assert additions, "the node read must select real records"
    # the query/selection evidence refs are DIRECT typed refs persisted in main.
    q_ref, s_ref = handle.input_contribution.memory_evidence_refs
    assert q_ref.schema_ref.name == "MemoryQuery"
    assert s_ref.schema_ref.name == "MemorySelection"
    sel = env.stores.payloads.get(s_ref.payload_ref, expected_schema_ref=s_ref.schema_ref)
    assert sel.snapshot_digest == env.binding.snapshot.content_digest


def test_execute_node_succeeds_with_the_real_base_union(env):
    (node_run, artifact), prepared = env.run()
    assert node_run.status is NodeStatus.COMPLETED
    assert artifact is not None
    expected = W._expected_memory_refs(prepared, base=env.base_refs)
    assert len(expected) >= len(env.base_refs)
    base_ids = {r.record_id for r in env.base_refs}
    addition_ids = {r.record_id for r in prepared.memory_record_refs()}
    assert base_ids and addition_ids
    assert base_ids | addition_ids == {r.record_id for r in expected}
    # the frozen union is canonically ordered + duplicate-free.
    keys = [(r.record_id, r.revision_id, r.content_digest) for r in expected]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


def test_missing_base_ref_fails_the_equality_preflight(env):
    resolver, prepared = env.prepare()
    # snapshot freezes ONLY the bridge additions (the base is dropped).
    dropped = W._expected_memory_refs(prepared, base=())
    snap = env.input_snapshot(prepared, refs=dropped)
    from tests.orchestration.test_worker import FakeModelGateway

    with pytest.raises(W.PreflightError, match="canonical union"):
        W.execute_node(
            env.plan, env.node, runtime=env.runtime, prepared_bridges=prepared,
            input_snapshot=snap, ctx=env.ctx, node_reservation=env.node_res,
            bridge_resolver=resolver, model_gateway=FakeModelGateway(env.stores),
            capability_gateway=env.gateway(), registry=env.persist_registry,
            stores=env.stores, clock=env.clock,
            base_authorized_memory_refs=env.base_refs)


def test_extra_foreign_ref_fails_the_equality_preflight(env):
    resolver, prepared = env.prepare()
    foreign = M.MemoryRecordRef(
        record_id="mem." + "e" * 64, revision_id="r-foreign",
        available_at=env.clock.now(), content_digest="e" * 64)
    padded = tuple(sorted(
        W._expected_memory_refs(prepared, base=env.base_refs) + (foreign,),
        key=lambda r: (r.record_id, r.revision_id, r.content_digest)))
    snap = env.input_snapshot(prepared, refs=padded)
    from tests.orchestration.test_worker import FakeModelGateway

    with pytest.raises(W.PreflightError, match="canonical union"):
        W.execute_node(
            env.plan, env.node, runtime=env.runtime, prepared_bridges=prepared,
            input_snapshot=snap, ctx=env.ctx, node_reservation=env.node_res,
            bridge_resolver=resolver, model_gateway=FakeModelGateway(env.stores),
            capability_gateway=env.gateway(), registry=env.persist_registry,
            stores=env.stores, clock=env.clock,
            base_authorized_memory_refs=env.base_refs)


def test_an_exactly_shared_ref_is_one_piece_of_evidence():
    from datetime import datetime, timezone

    ref = M.MemoryRecordRef(
        record_id="mem." + "a" * 64, revision_id="r1",
        available_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        content_digest="b" * 64)
    prepared = W.PreparedBridgeSet(node_id="n1", handles=(
        W.PreparedBridgeHandle(
            bridge_id="memory.runtime", bridge_priority=200, summary_digest="s",
            token=None, input_contribution=W.BridgeInputContribution(
                memory_refs=(ref,))),))
    union = W._expected_memory_refs(prepared, base=(ref,))
    assert union == (ref,)  # deduplicated by full semantic identity


# --------------------------------------------------------------------------- #
# execution-kind matrix + evidence                                             #
# --------------------------------------------------------------------------- #
def test_llm_run_renders_one_untrusted_block_only_inside_the_prompt_record(env):
    """The render stays inside the ONE executor-owned PromptAssemblyRecord; the
    query/selection typed refs remain direct evidence; memory made zero calls."""
    (node_run, artifact), _prepared = env.run()
    evid = node_run.execution_evidence_refs
    names = sorted(r.schema_ref.name for r in evid)
    assert "MemoryQuery" in names and "MemorySelection" in names
    assert "PromptAssemblyRecord" in names
    prompt_ref = next(r for r in evid if r.schema_ref.name == "PromptAssemblyRecord")
    prompt = env.stores.payloads.get(
        prompt_ref.payload_ref, expected_schema_ref=prompt_ref.schema_ref)
    # the RENDERED memory block is bound (by digest/ref) inside the prompt record.
    block_refs = [b for b in prompt.untrusted_blocks
                  if b.payload_ref.schema_ref.name == "RenderedMemoryBlock"]
    assert len(block_refs) == 1
    block = env.stores.payloads.get(
        block_refs[0].payload_ref.payload_ref,
        expected_schema_ref=block_refs[0].payload_ref.schema_ref)
    assert block.trust == "untrusted_data"
    assert node_run.tool_call_records == ()  # memory made ZERO capability calls
    assert node_run.data_result_refs == ()
    assert artifact.provenance.execution_evidence_refs == evid


def test_renderer_fails_closed_on_byte_overflow(env):
    small = M.MemoryCapturePolicy.build(**{
        **{f: getattr(env.world.policy.policy, f)
           for f in type(env.world.policy.policy).model_fields
           if f != "policy_digest"},
        "max_total_rendered_bytes": 8})
    from guanlan_v2.orchestration.refs import ContentRef

    tight = M.ResolvedMemoryPolicy(
        policy=small,
        material_ref=ContentRef(id="memory.facade.policy", version="1",
                                content_digest=small.policy_digest))
    sel = env.binding.selection
    records = {
        (r.record_id, r.revision_id): r for r, _ in env.binding.records
    }
    with pytest.raises(M.MemoryContractError, match="byte"):
        render_memory(sel, records, policy=tight,
                      renderer_ref=tight.material_ref)


def test_render_is_deterministic_and_untrusted(env):
    records = {(r.record_id, r.revision_id): r for r, _ in env.binding.records}
    a = render_memory(env.binding.selection, records, policy=env.world.policy,
                      renderer_ref=env.world.policy.material_ref)
    b = render_memory(env.binding.selection, records, policy=env.world.policy,
                      renderer_ref=env.world.policy.material_ref)
    assert a.content_digest == b.content_digest
    assert a.trust == "untrusted_data"
    assert 'trust="untrusted_data"' in a.text


def _direct_session(env, handle, snap):
    """Drive stage 2 directly on the REAL provider with the frozen stage-1 handle."""
    provider = env.factories.handler_factory(
        env.sc.view.resolve("memory.runtime").provider_ref)(
        bridge=env.sc.view.resolve("memory.runtime"),
        summary=env.runtime.summaries_for(env.node.id)[0])
    return provider.open_execution(W.BridgeOpenRequest(
        plan=env.plan, node=env.node, worker=env.worker,
        bridge=env.sc.view.resolve("memory.runtime"),
        summary=env.runtime.summaries_for(env.node.id)[0],
        handle=handle, input_snapshot=snap, capability_gateway=None,
        evidence_writer=W.BridgeEvidenceWriter(
            stores=env.stores, sequencer=env._seq, run_id=env.ctx.run_id,
            plan_digest=env.plan.plan_digest, node_id=env.node.id,
            data_registry_digest=env.persist_registry.registry_digest,
            runtime_registry_digest=env.runtime.runtime_registry_digest),
        reader=None, sequencer=env._seq))


def test_deterministic_kind_creates_no_render(env):
    """DETERMINISTIC execution renders nothing (the session's closed matrix)."""
    _resolver, prepared = env.prepare()
    handle = prepared.handles[0]
    snap = env.input_snapshot(prepared)
    session = _direct_session(env, handle, snap)
    det = session.freeze_for_execution(kind=ExecutionKind.DETERMINISTIC)
    assert det.status == "completed"
    assert det.frozen_contribution.untrusted_blocks == ()
    names = {r.schema_ref.name for r in det.frozen_contribution.direct_evidence_refs}
    assert names == {"MemoryQuery", "MemorySelection"}


def test_late_selection_drift_fails_before_execution(env):
    """A frozen InputSnapshot missing a prepared record ref fails the session's
    own verification (defense in depth under the resolver's whole-tuple check)."""
    _resolver, prepared = env.prepare()
    handle = prepared.handles[0]
    # freeze a snapshot WITHOUT the bridge additions but pretend they exist.
    snap = env.input_snapshot(prepared, refs=W._expected_memory_refs(
        W.PreparedBridgeSet(node_id=env.node.id, handles=()), base=env.base_refs))
    session = _direct_session(env, handle, snap)
    with pytest.raises(M.MemoryContractError, match="drifted"):
        session.freeze_for_execution(kind=ExecutionKind.DETERMINISTIC)
