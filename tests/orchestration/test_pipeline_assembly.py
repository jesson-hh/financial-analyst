# -*- coding: utf-8 -*-
"""Phase 10 - Task 0b: production assembly (catalog loading + worker tier gateways
+ plan-runner composition).

Offline and deterministic (zero network). Every run below drives the REAL kernel
surfaces; the only scripted seam is the ``gateway_factory`` -- the one seam the
brief declares as the test/production difference.

E1 reconciliation (the implemented composition surface, recorded before any code
was written -- file:line):

* ``build_admitted_plan_runner(*, admission, lane_bindings, run_context_factory,
  request_id, plan_executor=None, sink_artifact_resolver=None,
  freeze_idempotency_prefix="replay-freeze")`` --
  guanlan_v2/orchestration/adapters/launcher.py:313-322; the returned runner's
  seam kwargs are ``(lane, point, approval, data_context, memory_binding,
  candidate_plan_digest)`` (launcher.py:356-357).
* ``build_dag_plan_executor(*, pool, budget, runtime, registry, stores,
  runtime_limit, clock, admission, model_gateway=None, prompt_assembler=None,
  refusal_sink=None, runtime_profile=None, recorder_factory=None)`` --
  launcher.py:269-284; ``pool_sink_artifact_resolver(pool)`` launcher.py:247-266;
  ``LaneExecutionBinding(lane, candidate_plan_digest, reservation_id,
  approval_event_id)`` launcher.py:229-244.
* ``CatalogRuntime.build(snapshot, source)`` -- catalog_runtime.py:261-327;
  ``MaterialSource`` protocol :127-152; ``InMemoryMaterialSource`` :154-193.
* ``TrustedFactoryRegistry(runtime)`` -- catalog_runtime.py:437-454; handler
  registration ABI ``register_handler(ref: ContentRef, factory)`` :456-466
  (keys = catalog handler ContentRef identity; never rebindable). The registry is
  constructed FROM the built CatalogRuntime -- it cannot pre-exist it, which is
  why ``build_production_catalog_runtime`` returns the (runtime, factories) pair.
* ``ExecutionRuntime(catalog, bridge_view, factories, support_report,
  runtime_registry_digest)`` -- worker.py:252-268.
* ``ModelGateway`` port: ``invoke(request: AssembledModelRequest, *,
  prompt_assembly_ref: TypedPayloadRef) -> ModelResult`` -- worker.py:1112-1124;
  the mandatory pre-provider check ``verify_model_request_binding``
  worker.py:1127-1157; the executor invokes the gateway on a worker thread
  (``asyncio.to_thread`` dag.py:728 -> worker.py:1712) and validates
  ``ModelResult.payload`` against the output schema (worker.py:1763).
* Planner-gateway idiom (the production reference): planner_gateway.py:170-263;
  the explicit repo config path ``REPO_LLM_CONFIG_PATH`` planner_gateway.py:95.
* Tier bridge: ``TIER_ORDER`` model_tiers.py:84, ``ORCHESTRATION_TIER_ALIASES``
  :95-99, ``ModelTierUnconfigured`` :109, ``tier_map_from_llm_yaml`` :177.
* ``PlanPresetRegistry`` register/seal/get/manifest/registry_digest --
  plan_presets.py:188-260; ``PlanPresetRecord`` :102; ``load_preset_registry``
  :266-299.
* ``build_durable_runtime_stores(root, *, resolver=None, clock=None,
  allowed_cell_namespaces=())`` -- adapters/durable.py:529-552.
* ``dag.run_plan(plan_digest, ctx, *, admission, pool, budget, runtime, registry,
  stores, runtime_limit, clock, ...)`` -- dag.py:462-481; it re-verifies through
  ``admission.verify_for_dispatch`` (dag.py:500) and pins the runtime's support
  report + catalog digests (dag.py:502-505).
* ``ArtifactPool(*, stores, registry_digest, plan, catalog, clock)`` --
  pool.py:244-252; ``replay()`` :690; ``committed_output`` :563.
* ``PlanAdmissionService.freeze_and_admit_candidate(candidate_id, *,
  reservation_id, approval_event_id, idempotency_key)`` -- admission.py:547;
  ``verify_for_dispatch`` :660.

Run from repo root: ``python -m pytest tests/orchestration/test_pipeline_assembly.py -v``
"""
from __future__ import annotations

import ast
import asyncio
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.admission import AdmissionRejected, PlanAdmissionService
from guanlan_v2.orchestration.adapters.durable import build_durable_runtime_stores
from guanlan_v2.orchestration.adapters.launcher import (
    LaneExecutionBinding,
    LaunchRefused,
)
from guanlan_v2.orchestration.approval import PlanApprovalCoordinator
from guanlan_v2.orchestration.catalog import build_catalog_snapshot
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
    build_text_material,
    load_pilot_catalog,
)
from guanlan_v2.orchestration.catalog import ContentManifestEntry
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.enums import ApprovalDecision, ApprovalPolicy, PortfolioRating
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.eventstore import SchemaRegistryResolver
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.model_tiers import (
    ORCHESTRATION_TIER_ALIASES,
    TIER_ORDER,
    ModelTierUnconfigured,
    tier_map_from_llm_yaml,
)
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    build_pending_plan_approval,
    build_plan_diff,
)
from guanlan_v2.orchestration.plan_presets import (
    PlanPresetError,
    PlanPresetRecord,
    PlanPresetRegistry,
    load_preset_registry,
    materialize_fallback_draft,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import (
    phase2_runtime_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.schemas import (
    Confidence,
    PortfolioDecision,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
)
from guanlan_v2.orchestration.spec import PlanNode
from guanlan_v2.orchestration.worker import PromptBindingError

from guanlan_v2.orchestration.pipeline import assembly as assembly_mod
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    PipelineAssemblyError,
    PresetBaseRefused,
    ProductionCatalogRuntime,
    WorkerSeatModelGateway,
    build_phase10_preset_registry,
    build_production_catalog_runtime,
    build_production_plan_runner,
    production_gateway_factory,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 27, 2, 0, tzinfo=UTC)
RESEARCH_BASELINE = "main.research_baseline"
GOOD_CRED = "good-cred"
BASELINE_PRESET_FILE = PRODUCTION_PRESETS_DIR / "main_research_baseline.json"


# =========================================================================== #
# deterministic doubles (transcribed from the reviewed dynamic-e2e suite)      #
# =========================================================================== #
class _FixedClock:
    def now(self) -> datetime:
        return NOW


class _Verifier:
    """The fail-closed Phase-3 verifier port double."""

    def verify(self, credential) -> AuthenticatedAdminPrincipal:
        if credential != GOOD_CRED:
            raise AssertionError(f"unverifiable credential {credential!r}")
        return AuthenticatedAdminPrincipal(actor="human-approver", verified_by="0b-verifier")


def _sentiment() -> SentimentReport:
    return SentimentReport(
        overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
        confidence=Confidence.MEDIUM, narrative="Balanced flow with mild caution.")


def _research() -> ResearchPlan:
    return ResearchPlan(
        recommendation=PortfolioRating.HOLD, rationale="Hold pending confirmation.",
        strategic_actions=("Monitor breadth", "Re-check on catalyst"))


def _decision() -> PortfolioDecision:
    return PortfolioDecision(
        rating=PortfolioRating.HOLD, executive_summary="Advisory hold.",
        investment_thesis="Risk/reward balanced; no execution authority.")


_PAYLOAD_BY_WORKER = {
    "text.sentiment": _sentiment,
    "dec.research_mgr": _research,
    "dec.pm": _decision,
}


class _ScriptedWorkerGateway:
    """A scripted trusted single-shot worker ``ModelGateway`` (zero network).

    Enforces the exact prompt binding through the reviewed
    ``verify_model_request_binding`` and returns the reviewed payload per pilot
    worker -- the scripted stand-in ONLY for the provider call, never for the
    assembly path.
    """

    def __init__(self, payload_reader, marker: str = "scripted"):
        self._reader = payload_reader
        self.marker = marker
        self.invoked: list[str] = []

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._reader)
        self.invoked.append(record.worker_id)
        factory = _PAYLOAD_BY_WORKER.get(record.worker_id)
        if factory is None:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected pilot worker {record.worker_id!r}")
        return W.ModelResult(
            payload=factory(), rendered_text=f"{self.marker}:{record.worker_id}",
            input_tokens=13, output_tokens=9, provider=self.marker, model="scripted")


class _RecordingFactory:
    """A recording ``gateway_factory`` -- THE seam under test."""

    def __init__(self, marker: str):
        self.marker = marker
        self.calls: list[dict] = []
        self.gateways: list[_ScriptedWorkerGateway] = []

    def __call__(self, *, payload_reader, catalog_runtime):
        self.calls.append(
            {"payload_reader": payload_reader, "catalog_runtime": catalog_runtime})
        gateway = _ScriptedWorkerGateway(payload_reader, marker=self.marker)
        self.gateways.append(gateway)
        return gateway


# =========================================================================== #
# environment: the pilot preset composed through the REAL production assembly  #
# =========================================================================== #
class _Env:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fresh_resolver():
    registry = default_registry()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_digest = resolver.register(phase2_runtime_registry(registry.registry_digest))
    return registry, resolver, rt_digest


def _run_context_factory(context, run_budget):
    def _factory(*, lane, point, plan, data_context, memory_binding):
        return RunContext(
            run_id=plan.run_id, data=data_context,
            context_snapshot_id=context.snapshot_id,
            memory_snapshot_hash=context.memory_snapshot_hash,
            budget=run_budget, cancellation_token_id="cancel-pipe")
    return _factory


def _compose_env(tmp_path: Path, gateway_factory, *, decide: bool = True,
                 run_id: str = "run-pipe") -> _Env:
    """The pilot preset admitted + composed through the production assembly.

    Every stage is the real one: the EXISTING ``main_research_baseline`` preset
    file, ``materialize_fallback_draft``, the real ``PlanAdmissionService``, the
    real durable coordinator ``decide`` (fixture-approved), and
    ``build_production_plan_runner`` over tmp DURABLE stores. Only the model
    gateway is scripted, through the declared ``gateway_factory`` seam.
    """
    registry, resolver, rt_digest = _fresh_resolver()
    clock = _FixedClock()
    snapshot = load_pilot_catalog().snapshot
    stores = build_durable_runtime_stores(
        tmp_path / "stores", resolver=resolver, clock=clock,
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))

    from guanlan_v2.orchestration.spec import OrchestrationRequest

    request = OrchestrationRequest(
        request_id=f"req-{run_id}", goal="pilot preset production assembly e2e",
        workflow="orchestrate_only", fallback_preset_id=RESEARCH_BASELINE,
        approval_policy=ApprovalPolicy.REQUIRED)
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()), stores=stores,
        registry_digest=rt_digest, built_at=clock.now())
    context = mem.context
    ctx_ref = PayloadRef(
        namespace="main", object_id=f"ctx-{run_id}",
        content_digest=context.content_digest)
    run_budget = RunBudget(
        ledger_id=f"led-{run_id}", max_tokens=6_000_000, max_llm_invocations=40,
        max_concurrency=8)
    stores.bind_run_budget(run_id=run_id, run_budget=run_budget)

    # -- THE existing pilot preset, from its committed production file --------- #
    presets7 = load_preset_registry(PRODUCTION_PRESETS_DIR)
    preset = presets7.get(RESEARCH_BASELINE)
    draft = materialize_fallback_draft(
        preset, request=request, context=context, context_snapshot_ref=ctx_ref,
        catalog=snapshot, schema_registry=registry, draft_id=f"plan-{run_id}",
        run_id=run_id)

    # -- the production catalog assembly (physical config/orchestration sources) #
    bundle = build_production_catalog_runtime(snapshot)
    view = BridgeCatalogView.build(bundle.runtime, {})
    approvals: dict = {}
    service = PlanAdmissionService(
        run_id=run_id, requests={request.request_id: request},
        drafts={draft.id: draft}, contexts={context.content_digest: context},
        attestations={}, approvals=approvals, catalog=bundle.runtime,
        bridge_view=view, phase1_registry=registry,
        runtime_registry_digest=rt_digest, profile=static_runtime_profile(),
        stores=stores, run_budget=run_budget, clock=clock)
    prep = service.prepare_candidate(draft.id, request_id=request.request_id)
    cand, res = service.persist_and_reserve_candidate(
        prep, idempotency_key=f"reserve-{run_id}")
    digest = cand.candidate_plan_digest

    if decide:
        diff = build_plan_diff(
            draft, request=request, candidate_plan_digest=digest,
            baseline=None, baseline_kind="none")
        diff_ref = TypedPayloadRef(
            schema_ref=PLAN_DIFF_SCHEMA_REF,
            payload_ref=PayloadRef(
                namespace="main", object_id="diff-" + digest[:8],
                content_digest=diff.semantic_digest()))
        pending = build_pending_plan_approval(
            draft=draft, request=request, candidate_plan_digest=digest, diff=diff,
            plan_diff_ref=diff_ref, planner_rationale=None,
            candidate_id=f"cand-{run_id}", requested_at=clock.now(),
            preset_id=RESEARCH_BASELINE,
            preset_record_digest=preset.semantic_digest())
        coordinator = PlanApprovalCoordinator(
            tmp_path / "plan_approvals.jsonl", admission=service, clock=clock,
            verifier=_Verifier(), console_emit=None,
            approvals_sink=lambda ap: approvals.__setitem__(
                (ap.request_id, ap.candidate_plan_digest), ap))
        coordinator.register_pending(pending, idempotency_key=f"pending-{run_id}")
        _approval, event = coordinator.decide(
            request_id=request.request_id, candidate_plan_digest=digest,
            decision=ApprovalDecision.APPROVED, actor=GOOD_CRED, reason="reviewed",
            idempotency_key=f"decide-{run_id}")
        approval_event_id = event.event_id
    else:
        approval_event_id = "ev-never-approved"

    binding = LaneExecutionBinding(
        lane="main", candidate_plan_digest=digest,
        reservation_id=res.reservation_id, approval_event_id=approval_event_id)
    runner = build_production_plan_runner(
        stores=stores, catalog_snapshot=snapshot, registry=registry,
        gateway_factory=gateway_factory, admission=service,
        lane_bindings={"main": binding},
        run_context_factory=_run_context_factory(context, run_budget),
        request_id=request.request_id, clock=clock,
        runtime_registry_digest=rt_digest, runtime_limit=3)

    return _Env(
        registry=registry, clock=clock, snapshot=snapshot, stores=stores,
        stores_root=tmp_path / "stores", request=request, context=context,
        run_budget=run_budget, run_id=run_id, digest=digest, runner=runner,
        service=service, bundle=bundle)


def _run(env: _Env):
    return env.runner(
        lane="main", point=SimpleNamespace(point_ordinal=1),
        approval=SimpleNamespace(), data_context=env.context.data_context,
        memory_binding=None, candidate_plan_digest=env.digest)


def _event_types(stores, run_id):
    return [e.event_type for e in stores.events.journal(run_id, "main")]


# =========================================================================== #
# 1. the pilot-preset e2e through the composed runner                           #
# =========================================================================== #
def test_pilot_preset_e2e_through_the_composed_runner(tmp_path):
    """The EXISTING pilot preset runs through admission + the composed runner:
    real assembly, scripted gateway, artifacts committed to tmp DURABLE stores."""
    factory = _RecordingFactory("e2e")
    env = _compose_env(tmp_path, factory)
    artifact = _run(env)

    # the sink artifact is the pm node's committed PortfolioDecision.
    assert artifact is not None
    validated = env.registry.validate_payload(artifact.payload_schema_ref, artifact.payload)
    assert isinstance(validated, PortfolioDecision)

    # the run really froze + completed on the durable journal (never a stand-in).
    kinds = _event_types(env.stores, env.run_id)
    assert EventType.PLAN_FROZEN in kinds
    assert EventType.RUN_COMPLETED in kinds

    # the scripted gateway saw exactly the pilot triad, via the real assembler.
    assert sorted(factory.gateways[0].invoked) == [
        "dec.pm", "dec.research_mgr", "text.sentiment"]

    # the factory received the composition's own payload reader + catalog runtime.
    call = factory.calls[0]
    assert call["payload_reader"] is env.stores.payloads
    assert call["catalog_runtime"].catalog_digest == env.snapshot.catalog_digest


def test_a_raw_json_sentiment_completion_completes_through_the_composed_runner(tmp_path):
    """The deep lane under the reviewed D2/D3 treatment (2026-07-31), wired at
    the ONE production seam. The live run ``deep-f1f0031d521bea3a`` recorded
    ``sentiment`` refused with ``'Neutral' is not an instance of SentimentBand``
    / ``'low' is not an instance of Confidence`` — a CORRECT answer, killed by
    python-mode decoding because ``WorkerSeatModelGateway`` never got the Lane-0
    gateway's strict-JSON normalization. ``build_production_plan_runner`` now
    wraps every gateway in ``SeatOutputNormalizingGateway``; the recorded raw
    JSON must complete the pilot chain end to end."""

    class _RawSentimentGateway(_ScriptedWorkerGateway):
        def invoke(self, request, *, prompt_assembly_ref):
            record = W.verify_model_request_binding(
                request, prompt_assembly_ref, reader=self._reader)
            self.invoked.append(record.worker_id)
            if record.worker_id == "text.sentiment":
                # verbatim: the recorded live values that python-mode refused,
                # inside the named envelope idiom the live lane0.rotation seat
                # produced — only the wired decorator can unwrap it, so this
                # test is load-bearing on the runner wiring itself (step (9)
                # alone refuses the envelope).
                return W.ModelResult(
                    payload={"sentiment_report": {
                        "schema_version": "1",
                        "overall_band": "Neutral",
                        "overall_score": 5.0,
                        "confidence": "low",
                        "narrative": "Neutral tape; flows mixed.",
                    }},
                    rendered_text="raw-json:text.sentiment",
                    input_tokens=13, output_tokens=9,
                    provider=self.marker, model="scripted")
            return super().invoke(request, prompt_assembly_ref=prompt_assembly_ref)

    class _RawFactory(_RecordingFactory):
        def __call__(self, *, payload_reader, catalog_runtime):
            self.calls.append(
                {"payload_reader": payload_reader, "catalog_runtime": catalog_runtime})
            gateway = _RawSentimentGateway(payload_reader, marker=self.marker)
            self.gateways.append(gateway)
            return gateway

    factory = _RawFactory("raw-json")
    env = _compose_env(tmp_path, factory, run_id="run-rawjson")
    artifact = _run(env)

    # the chain COMPLETED: the raw-JSON sentiment decoded into its typed report
    # instead of blocking research-mgr/pm (the live run's death).
    assert artifact is not None
    validated = env.registry.validate_payload(artifact.payload_schema_ref, artifact.payload)
    assert isinstance(validated, PortfolioDecision)
    kinds = _event_types(env.stores, "run-rawjson")
    assert EventType.RUN_COMPLETED in kinds
    assert "text.sentiment" in factory.gateways[0].invoked


def test_e2e_artifacts_survive_a_durable_store_reopen(tmp_path):
    """The committed run folds back byte-identically from the tmp durable root --
    the proof the composition really wrote through ``build_durable_runtime_stores``."""
    env = _compose_env(tmp_path, _RecordingFactory("durable"), run_id="run-dur")
    artifact = _run(env)
    assert artifact is not None

    _registry, resolver, _rt = _fresh_resolver()
    reopened = build_durable_runtime_stores(
        env.stores_root, resolver=resolver, clock=_FixedClock(),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    assert env.run_id in reopened.events.run_ids("main")
    kinds = _event_types(reopened, env.run_id)
    assert EventType.PLAN_FROZEN in kinds
    assert EventType.RUN_COMPLETED in kinds


# =========================================================================== #
# 2. the gateway-factory seam                                                  #
# =========================================================================== #
def test_gateway_factory_is_the_only_seam(tmp_path):
    """Identical assembly, different factory -> a different gateway is observed.
    The assembly code path itself is the same builder both times."""
    factory_a = _RecordingFactory("seam-a")
    factory_b = _RecordingFactory("seam-b")
    env_a = _compose_env(tmp_path / "a", factory_a, run_id="run-seam-a")
    env_b = _compose_env(tmp_path / "b", factory_b, run_id="run-seam-b")
    assert _run(env_a) is not None
    assert _run(env_b) is not None
    assert factory_a.gateways[0].marker == "seam-a"
    assert factory_b.gateways[0].marker == "seam-b"
    assert factory_a.gateways[0] is not factory_b.gateways[0]
    assert factory_a.gateways[0].invoked and factory_b.gateways[0].invoked


# =========================================================================== #
# 3. no approval/admission bypass                                               #
# =========================================================================== #
def test_an_unbound_candidate_digest_is_refused_before_any_freeze(tmp_path):
    env = _compose_env(tmp_path, _RecordingFactory("nb"), run_id="run-nb")
    with pytest.raises(LaunchRefused):
        env.runner(
            lane="main", point=SimpleNamespace(point_ordinal=1),
            approval=SimpleNamespace(), data_context=env.context.data_context,
            memory_binding=None, candidate_plan_digest="f" * 64)
    assert EventType.PLAN_FROZEN not in _event_types(env.stores, env.run_id)


def test_a_never_approved_candidate_cannot_execute(tmp_path):
    """The composed runner inherits the admission red line: a bound digest whose
    approval never happened dies in ``freeze_and_admit_candidate``."""
    factory = _RecordingFactory("na")
    env = _compose_env(tmp_path, factory, decide=False, run_id="run-na")
    with pytest.raises(AdmissionRejected):
        _run(env)
    assert EventType.PLAN_FROZEN not in _event_types(env.stores, env.run_id)
    # the freeze died before the executor: the gateway seam was never even built.
    assert factory.calls == [] and factory.gateways == []


# =========================================================================== #
# 4. build_production_catalog_runtime                                           #
# =========================================================================== #
def test_pilot_catalog_resolves_from_its_physical_sources():
    snapshot = load_pilot_catalog().snapshot
    bundle = build_production_catalog_runtime(snapshot)
    assert isinstance(bundle, ProductionCatalogRuntime)
    assert isinstance(bundle.runtime, CatalogRuntime)
    assert isinstance(bundle.factories, TrustedFactoryRegistry)
    assert bundle.runtime.catalog_digest == snapshot.catalog_digest
    resolved = bundle.runtime.resolve_worker("text.sentiment")
    assert resolved.system_prompt is not None


def test_an_unresolvable_material_is_refused_loudly():
    """A snapshot referencing a material no reviewed physical source holds is a
    typed refusal NAMING the material -- never a silent stub."""
    ref, mat = build_text_material(
        id="no.such.material", version="1", kind="prompt",
        raw=b"a prompt with no physical production source")
    snapshot = build_catalog_snapshot(
        catalog_version="phase10-unresolvable-v1",
        content_manifest=(ContentManifestEntry(
            ref=ref, kind="prompt", name="orphan", description="d",
            source_identity="gl"),),
        skill_manifest=(), capability_manifest=(), workers=(),
        resolved_material=(mat,))
    with pytest.raises(CatalogMaterialError) as excinfo:
        build_production_catalog_runtime(snapshot)
    assert "no.such.material" in str(excinfo.value)


def test_an_off_catalog_handler_registration_is_refused():
    snapshot = load_pilot_catalog().snapshot
    with pytest.raises(CatalogMaterialError) as excinfo:
        build_production_catalog_runtime(
            snapshot, handler_registry={"no.such.handler": lambda **kw: None})
    assert "no.such.handler" in str(excinfo.value)


def test_a_caller_supplied_material_source_is_honored():
    """The material_source seam: an explicit source replaces the default physical
    resolution (Tasks 2/11 bind their own loaders here) -- same verified build."""
    pilot = load_pilot_catalog()
    text, caps = pilot.resolved_materials()
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in text},
        capabilities={(c.ref.id, c.ref.version): c.descriptor for c in caps})
    bundle = build_production_catalog_runtime(pilot.snapshot, material_source=source)
    assert bundle.runtime.catalog_digest == pilot.snapshot.catalog_digest


# =========================================================================== #
# 5. WorkerSeatModelGateway -- tier resolution                                  #
# =========================================================================== #
def _pilot_runtime():
    return load_pilot_catalog()


def test_all_three_tier_seats_resolve_from_the_repo_llm_yaml_by_explicit_path():
    gateway = WorkerSeatModelGateway(
        payload_reader=object(), catalog_runtime=_pilot_runtime())
    # the explicit repo path -- never find_config.
    assert gateway.config_path.name == "llm.yaml"
    assert gateway.config_path.parent.name == "config"
    expected = tier_map_from_llm_yaml(
        gateway.config_path.read_text(encoding="utf-8"))
    for tier in TIER_ORDER:
        assert gateway.seat_for_tier(tier) == ORCHESTRATION_TIER_ALIASES[tier]
        assert gateway.binding_for_tier(tier) == expected.binding_for(tier)


def test_an_unconfigured_tier_raises_a_typed_error_naming_the_seat(tmp_path):
    partial = tmp_path / "llm.yaml"
    partial.write_text(
        "providers:\n"
        "  deepseek: {api_key_env: DEEPSEEK_API_KEY}\n"
        "agent_overrides:\n"
        "  orchestration-fast: {provider: deepseek, model: deepseek-chat}\n"
        "  orchestration-reasoner-deep: {provider: deepseek, model: deepseek-reasoner}\n",
        encoding="utf-8")
    with pytest.raises(ModelTierUnconfigured) as excinfo:
        WorkerSeatModelGateway(
            payload_reader=object(), catalog_runtime=_pilot_runtime(),
            config_path=partial)
    assert "orchestration-reasoner" in str(excinfo.value)


def test_an_unknown_tier_is_refused_by_name():
    gateway = WorkerSeatModelGateway(
        payload_reader=object(), catalog_runtime=_pilot_runtime())
    with pytest.raises(ModelTierUnconfigured) as excinfo:
        gateway.seat_for_tier("galaxy_brain")
    assert "galaxy_brain" in str(excinfo.value)


def test_invoke_refuses_a_forged_binding_before_any_provider_work(monkeypatch):
    """The rehash-refusal runs BEFORE the engine client is even imported: a
    booby-trapped engine module proves no provider work is reachable."""
    runtime = _pilot_runtime()
    resolved = runtime.resolve_worker("text.sentiment")
    request = W.StaticPromptAssembler().assemble(
        plan_digest="0" * 64, node_id="n1", worker_id="text.sentiment",
        system_prompt=resolved.system_prompt, skills=resolved.skills,
        guardrails=resolved.guardrails, trusted_input_digests=(),
        untrusted_blocks=())

    class _Trap(types.ModuleType):
        def __getattr__(self, name):  # pragma: no cover - reaching this IS the failure
            raise AssertionError("the engine client was touched before binding verification")

    monkeypatch.setitem(
        sys.modules, "financial_analyst.llm.client", _Trap("financial_analyst.llm.client"))
    gateway = WorkerSeatModelGateway(payload_reader=object(), catalog_runtime=runtime)
    forged = TypedPayloadRef(
        schema_ref=SchemaRef(name="NotAPromptRecord", version="1"),
        payload_ref=PayloadRef(
            namespace="main", object_id="obj-x", content_digest="0" * 64))
    with pytest.raises(PromptBindingError):
        gateway.invoke(request, prompt_assembly_ref=forged)


def test_production_gateway_factory_builds_the_worker_seat_gateway():
    gateway = production_gateway_factory(
        payload_reader=object(), catalog_runtime=_pilot_runtime())
    assert isinstance(gateway, WorkerSeatModelGateway)
    gateway.close()


# =========================================================================== #
# 5b. WorkerSeatModelGateway -- the provider path itself (fake client, no net)  #
# =========================================================================== #
_SEAT_YAML = (
    "providers:\n"
    "  prov-x: {api_key_env: PX_API_KEY}\n"
    "agent_overrides:\n"
    "  orchestration-fast: {provider: prov-x, model: model-fast, timeout: 77}\n"
    "  orchestration-reasoner: {provider: prov-x, model: model-reasoner, timeout: 77}\n"
    "  orchestration-reasoner-deep: {provider: prov-x, model: model-deep, timeout: 77}\n"
)


class _FakeEngineClient:
    """An engine-client double: OpenAI-shaped response, planner-idiom attributes."""

    def __init__(self, provider: str, model: str, content: str):
        self.provider = provider
        self.model = model
        self.total_prompt_tokens = 42
        self.total_completion_tokens = 17
        self._content = content
        self.chat_kwargs = None

    async def chat(self, messages, **kwargs):
        self.chat_kwargs = kwargs
        return {"choices": [{"message": {"content": self._content}}]}


def _install_fake_engine(monkeypatch, client, captured: list):
    module = types.ModuleType("financial_analyst.llm.client")

    class _FakeLLMClient:
        @classmethod
        def for_agent(cls, seat, *, config_path):
            captured.append({"seat": seat, "config_path": config_path})
            return client

    module.LLMClient = _FakeLLMClient
    monkeypatch.setitem(sys.modules, "financial_analyst.llm.client", module)


def _provider_path_fixture(tmp_path, content: str, *, provider=None, model=None):
    """A gateway on a synthetic seat yaml + a valid bound request + fake client."""
    runtime = _pilot_runtime()
    resolved = runtime.resolve_worker("text.sentiment")
    request = W.StaticPromptAssembler().assemble(
        plan_digest="0" * 64, node_id="n1", worker_id="text.sentiment",
        system_prompt=resolved.system_prompt, skills=resolved.skills,
        guardrails=resolved.guardrails, trusted_input_digests=(),
        untrusted_blocks=())
    ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="PromptAssemblyRecord", version="1"),
        payload_ref=PayloadRef(
            namespace="main", object_id="obj-rec", content_digest="0" * 64))

    class _Reader:
        def get(self, r, *, expected_schema_ref):
            return request.prompt_record

    config = tmp_path / "llm.yaml"
    config.write_text(_SEAT_YAML, encoding="utf-8")
    gateway = WorkerSeatModelGateway(
        payload_reader=_Reader(), catalog_runtime=runtime, config_path=config)
    tier = runtime.worker("text.sentiment").execution.model_tier
    binding = gateway.binding_for_tier(tier)
    client = _FakeEngineClient(
        provider if provider is not None else binding.provider,
        model if model is not None else binding.model,
        content)
    return SimpleNamespace(
        gateway=gateway, request=request, ref=ref, tier=tier,
        binding=binding, client=client)


def test_invoke_drives_one_single_shot_completion_on_the_resolved_seat(
        tmp_path, monkeypatch):
    """Steps 3-5 of ``invoke`` under a fake engine client (zero network): the
    explicit config path reaches ``for_agent`` literally, the seat is the tier's
    alias, the binding timeout bounds the outer wait, and the result carries the
    provider/model/token truth."""
    fx = _provider_path_fixture(tmp_path, '{"overall_band": "neutral"}')
    captured: list = []
    _install_fake_engine(monkeypatch, fx.client, captured)

    timeouts: list = []
    real_wait_for = asyncio.wait_for

    def _spy_wait_for(awaitable, timeout=None):
        timeouts.append(timeout)
        return real_wait_for(awaitable, timeout)

    monkeypatch.setattr("asyncio.wait_for", _spy_wait_for)
    result = fx.gateway.invoke(fx.request, prompt_assembly_ref=fx.ref)
    fx.gateway.close()

    assert captured == [{
        "seat": ORCHESTRATION_TIER_ALIASES[fx.tier],
        "config_path": fx.gateway.config_path}]
    assert captured[0]["config_path"] is fx.gateway.config_path  # literally self._config_path
    assert fx.binding.timeout_sec == 77  # derived from the binding, not a default
    assert timeouts == [77 + assembly_mod._OUTER_WAIT_BUFFER_SEC]
    assert fx.client.chat_kwargs == {
        "response_format": {"type": "json_object"},
        "temperature": assembly_mod._WORKER_TEMPERATURE}
    assert result.payload == {"overall_band": "neutral"}
    assert result.rendered_text == '{"overall_band": "neutral"}'
    assert result.provider == fx.binding.provider
    assert result.model == fx.binding.model
    assert result.input_tokens == 42 and result.output_tokens == 17


def test_a_non_json_completion_yields_payload_none_never_fabricated(
        tmp_path, monkeypatch):
    """The honesty claim, pinned: a non-JSON completion returns ``payload=None``
    (the executor's ``output_schema_invalid`` refusal path), with the rendered
    text preserved verbatim -- never a fabricated payload."""
    fx = _provider_path_fixture(tmp_path, "not json at all")
    _install_fake_engine(monkeypatch, fx.client, [])
    result = fx.gateway.invoke(fx.request, prompt_assembly_ref=fx.ref)
    fx.gateway.close()
    assert result.payload is None
    assert result.rendered_text == "not json at all"


def test_an_engine_client_off_the_reviewed_binding_is_refused_by_seat(
        tmp_path, monkeypatch):
    """The self-enforcing seat red line: a client the engine resolved onto a
    different provider/model (default-vendor fallback, shadowed config) is a
    typed refusal NAMING the seat, before any completion is attempted."""
    fx = _provider_path_fixture(
        tmp_path, "{}", provider="default-vendor", model="default-model")
    _install_fake_engine(monkeypatch, fx.client, [])
    with pytest.raises(ModelTierUnconfigured) as excinfo:
        fx.gateway.invoke(fx.request, prompt_assembly_ref=fx.ref)
    fx.gateway.close()
    assert ORCHESTRATION_TIER_ALIASES[fx.tier] in str(excinfo.value)
    assert fx.client.chat_kwargs is None  # the completion was never attempted


# =========================================================================== #
# 6. build_phase10_preset_registry -- the sealed extension mechanism            #
# =========================================================================== #
def _screen_record() -> PlanPresetRecord:
    return PlanPresetRecord(
        preset_id="screen.lane_smoke", version="1",
        description="phase10 extension smoke record (test-only)",
        nodes=(PlanNode(id="s1", worker_id="text.sentiment", writes_slot="slot-s"),),
        sink_node_ids=("s1",), budget_request_tokens=1000,
        budget_request_llm_invocations=1, max_concurrency=1)


def test_extension_keeps_the_phase7_preset_golden_byte_identical():
    before = BASELINE_PRESET_FILE.read_bytes()
    base = load_preset_registry(PRODUCTION_PRESETS_DIR)
    extended = build_phase10_preset_registry(base, (_screen_record(),))
    assert BASELINE_PRESET_FILE.read_bytes() == before  # golden untouched
    assert extended.sealed
    file_record = PlanPresetRecord.model_validate_json(
        BASELINE_PRESET_FILE.read_text(encoding="utf-8"))
    assert (extended.get(RESEARCH_BASELINE).semantic_digest()
            == file_record.semantic_digest())
    assert extended.get("screen.lane_smoke").semantic_digest() == \
        _screen_record().semantic_digest()
    # the base registry object itself was never mutated (typed lookup refusal).
    with pytest.raises(PlanPresetError):
        base.get("screen.lane_smoke")


def test_extension_refuses_a_non_phase7_base():
    stranger = PlanPresetRegistry()
    stranger.register(_screen_record())
    stranger.seal()
    with pytest.raises(PresetBaseRefused) as excinfo:
        build_phase10_preset_registry(stranger, ())
    assert RESEARCH_BASELINE in str(excinfo.value)


def test_extension_refuses_an_unsealed_base():
    unsealed = PlanPresetRegistry()
    unsealed.register(load_preset_registry(PRODUCTION_PRESETS_DIR).get(RESEARCH_BASELINE))
    with pytest.raises(PresetBaseRefused):
        build_phase10_preset_registry(unsealed, ())


def test_extension_refuses_a_mutated_baseline_record():
    """A base carrying a DIFFERENT record under the Phase-7 preset id is not the
    Phase-7 base -- refused, never silently substituted."""
    real = load_preset_registry(PRODUCTION_PRESETS_DIR).get(RESEARCH_BASELINE)
    mutated = real.model_copy(update={"budget_request_tokens": 1})
    forged = PlanPresetRegistry()
    forged.register(mutated)
    forged.seal()
    with pytest.raises(PresetBaseRefused):
        build_phase10_preset_registry(forged, ())


def test_extension_refuses_a_smuggled_base_record():
    """The base-subset seal: a base = the true Phase-7 records PLUS an injected
    extra passes the baseline check but must NOT have the extra copied into the
    sealed production registry -- refused by NAME (base must be a subset of the
    committed presets directory, record for record by semantic digest)."""
    committed = load_preset_registry(PRODUCTION_PRESETS_DIR)
    forged = PlanPresetRegistry()
    for entry in committed.manifest():
        forged.register(committed.get(entry.preset_id))
    smuggled = _screen_record().model_copy(update={"preset_id": "smuggle.extra"})
    forged.register(smuggled)
    forged.seal()
    with pytest.raises(PresetBaseRefused) as excinfo:
        build_phase10_preset_registry(forged, ())
    assert "smuggle.extra" in str(excinfo.value)


# =========================================================================== #
# 7. red lines                                                                  #
# =========================================================================== #
def test_assembly_imports_stay_inside_orchestration_plus_engine_llm_client():
    """Invariant 4: no engine file dependency beyond the LLM client; every
    guanlan_v2 import stays inside the orchestration package."""
    from guanlan_v2.orchestration.pipeline import assembly as assembly_mod

    src = Path(assembly_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    for module in modules:
        if module.startswith("guanlan_v2"):
            assert module.startswith("guanlan_v2.orchestration"), module
        if module.startswith("financial_analyst"):
            assert module == "financial_analyst.llm.client", module
        assert not module.startswith("engine"), module


def test_the_composed_runner_refuses_the_event_loop_thread(tmp_path):
    """The launcher's 9999 red line is inherited intact by the composition."""
    env = _compose_env(tmp_path, _RecordingFactory("loop"), run_id="run-loop")

    async def _inside():
        with pytest.raises(LaunchRefused):
            _run(env)

    asyncio.run(_inside())
    assert EventType.PLAN_FROZEN not in _event_types(env.stores, env.run_id)


def test_catalog_snapshot_and_prebuilt_runtime_must_agree(tmp_path):
    """A pre-assembled catalog bundle whose digest is not the snapshot's is a
    typed assembly refusal (no split-brain catalog identity)."""
    pilot = load_pilot_catalog()
    bundle = build_production_catalog_runtime(pilot.snapshot)
    ref, mat = build_text_material(
        id="tiny.prompt", version="1", kind="prompt", raw=b"tiny")
    other = build_catalog_snapshot(
        catalog_version="phase10-other-v1",
        content_manifest=(ContentManifestEntry(
            ref=ref, kind="prompt", name="t", description="d",
            source_identity="gl"),),
        skill_manifest=(), capability_manifest=(), workers=(),
        resolved_material=(mat,))
    with pytest.raises(PipelineAssemblyError):
        build_production_plan_runner(
            stores=object(), catalog_snapshot=other, registry=object(),
            gateway_factory=_RecordingFactory("x"), admission=object(),
            lane_bindings={}, run_context_factory=lambda **kw: None,
            request_id="req-x", clock=_FixedClock(),
            runtime_registry_digest="0" * 64, runtime_limit=1,
            catalog=bundle)


# =========================================================================== #
# 12. Task 11 — the promoted production material universe + planner seam        #
# =========================================================================== #
class TestTask11MaterialUniverse:
    """The nine-loader promotion (Task-0b concern 2 closed): the production
    material universe now resolves the FULL Phase-9 (and Phase-10) cumulative
    catalogs with ``material_source=None`` — the exact recipe the deep-preset
    suite proved, lifted out of test code into ``_reviewed_material_universe``."""

    def test_the_full_phase9_catalog_builds_with_no_explicit_source(self):
        from guanlan_v2.orchestration.adapters import chain as p9

        bundle = build_production_catalog_runtime(p9.phase9_catalog_snapshot())
        assert bundle.catalog_digest == p9.PHASE9_CATALOG_DIGEST
        # a real Phase-8 lane worker resolves with its prompt material bytes.
        resolved = bundle.runtime.resolve_worker("dec.pm")
        assert resolved.system_prompt is not None

    def test_the_phase10_catalog_builds_and_registers_the_cand_handlers(self):
        """The plan's Task-11 clause: the ``cand.*`` handler callables register
        as DETERMINISTIC catalog handlers through ``handler_registry`` against
        the Phase-10 catalog's own handler material ids."""
        from guanlan_v2.orchestration.pipeline import chain as p10
        from guanlan_v2.orchestration.pipeline.candidates import (
            CandLane0Params,
            CandModelParams,
            CandV4Params,
            CandidatePorts,
            as_handler_factory,
            cand_lane0_handler,
            cand_model_handler,
            cand_v4_handler,
        )

        ports = CandidatePorts(
            as_of=datetime(2026, 7, 28, 2, 30, tzinfo=timezone.utc),
            ranking_reader=SimpleNamespace())
        registry = {
            p10.CAND_HANDLER_IDS["cand.v4"]: as_handler_factory(
                cand_v4_handler(params=CandV4Params(top_n=5), ports=ports)),
            p10.CAND_HANDLER_IDS["cand.lane0"]: as_handler_factory(
                cand_lane0_handler(
                    params=CandLane0Params(top_n=5, mainline_limit=3),
                    ports=ports)),
            p10.CAND_HANDLER_IDS["cand.model"]: as_handler_factory(
                cand_model_handler(
                    params=CandModelParams(top_n=5, variant_id="m_test"),
                    ports=ports)),
        }
        snap = p10.phase10_catalog_snapshot()
        bundle = build_production_catalog_runtime(snap, handler_registry=registry)
        assert bundle.catalog_digest == p10.PHASE10_CATALOG_DIGEST
        # each cand.* worker's exact handler ref resolves its registered factory.
        for worker in snap.workers:
            if worker.id not in p10.CAND_WORKER_IDS:
                continue
            factory = bundle.factories.handler_factory(
                worker.execution.handler_ref)
            assert factory is registry[worker.execution.handler_ref.id]

    def test_a_shapeless_material_is_refused_loudly_naming_the_type(
            self, monkeypatch):
        """FINAL-REVIEW minor 1: the ``_add`` material-shape guard (abd15de) —
        a reviewed loader yielding a material with neither ``raw_utf8`` nor
        ``descriptor`` is a loud typed refusal naming the offending type and
        ref, never a silent drop."""
        import guanlan_v2.orchestration.lane_catalog as lane_catalog
        from guanlan_v2.orchestration.pipeline import assembly

        bad = SimpleNamespace(ref=SimpleNamespace(id="bogus.material", version="1"))
        monkeypatch.setattr(
            lane_catalog, "load_phase8_lane_materials", lambda: [bad])
        with pytest.raises(CatalogMaterialError, match="SimpleNamespace") as exc:
            assembly._reviewed_material_universe()
        assert "bogus.material@1" in str(exc.value)

    def test_production_bridge_view_binds_the_three_real_analyzers(self):
        import guanlan_v2.orchestration.bootstrap as bs
        import guanlan_v2.orchestration.data.catalog as dcat
        import guanlan_v2.orchestration.memory.catalog as mcat
        from guanlan_v2.orchestration.adapters import chain as p9
        from guanlan_v2.orchestration.pipeline.assembly import (
            production_bridge_view,
        )

        bundle = build_production_catalog_runtime(p9.phase9_catalog_snapshot())
        view = production_bridge_view(bundle.runtime)
        assert sorted(view.bridge_ids()) == [
            "data.runtime", "experience.bridge", "memory.runtime"]
        assert isinstance(view.resolve("data.runtime").analyzer,
                          dcat.DataBridgeSupportAnalyzer)
        assert isinstance(view.resolve("memory.runtime").analyzer,
                          mcat.MemoryBridgeSupportAnalyzer)
        assert isinstance(view.resolve("experience.bridge").analyzer,
                          bs.ExperienceBridgeSupportAnalyzer)

    def test_the_analyzer_map_is_one_recipe_shared_by_view_and_runner_halves(self):
        """The 2026-07-31 burned-lease defect's structural cure: the three
        reviewed analyzer bindings come from ONE function —
        ``production_bridge_analyzers`` — which ``production_bridge_view``
        consumes by default and which the live seam hands to
        ``build_production_plan_runner`` as ``bridge_analyzers``. A caller that
        builds the map once may inject it into the view, and the view binds
        EXACTLY those instances (no silent second build)."""
        import guanlan_v2.orchestration.bootstrap as bs
        import guanlan_v2.orchestration.data.catalog as dcat
        import guanlan_v2.orchestration.memory.catalog as mcat
        from guanlan_v2.orchestration.adapters import chain as p9
        from guanlan_v2.orchestration.pipeline.assembly import (
            production_bridge_analyzers,
            production_bridge_view,
        )

        analyzers = production_bridge_analyzers()
        assert len(analyzers) == 3
        by_type = {type(a) for a in analyzers.values()}
        assert by_type == {dcat.DataBridgeSupportAnalyzer,
                           mcat.MemoryBridgeSupportAnalyzer,
                           bs.ExperienceBridgeSupportAnalyzer}
        # every key is a sealed (id, version, content_digest) analyzer ref.
        for key in analyzers:
            assert len(key) == 3
            assert all(isinstance(part, str) and part for part in key)

        bundle = build_production_catalog_runtime(p9.phase9_catalog_snapshot())
        view = production_bridge_view(bundle.runtime, analyzers)
        bound = {view.resolve(bid).analyzer for bid in view.bridge_ids()}
        # the injected instances themselves are bound — identity, not a rebuild.
        assert bound == set(analyzers.values())


class _ExplodingPlannerGateway:
    """A scripted provider outage: every invoke raises AFTER the real prompt
    record was persisted (run_planner step 2 precedes step 3)."""

    def __init__(self):
        self.calls = 0
        self.closed = False

    def invoke(self, request, *, prompt_assembly_ref):
        self.calls += 1
        raise RuntimeError("scripted provider outage")

    def close(self):
        self.closed = True


class TestTask11PlannerRunner:
    def test_the_seam_runs_the_real_planner_loop_and_halts_honestly(self):
        """``build_production_planner_runner`` drives the REAL ``run_planner``
        over the verified production catalog runtime + the catalog-derived
        ``PlannerSpec`` + the real ``StaticPromptAssembler`` + a planner-scoped
        budget ledger. With a scripted always-failing gateway the loop exhausts
        its reviewed attempts (each an honest ``model_error``) and terminates
        ``halted_no_fallback`` (the request carries no fallback preset) — no
        draft is ever fabricated, and the gateway is closed."""
        from guanlan_v2.orchestration.adapters import chain as p9
        from guanlan_v2.orchestration.eventstore import RuntimeStores
        from guanlan_v2.orchestration.phase7_registry import (
            build_phase7_planner_spec,
        )
        from guanlan_v2.orchestration.pipeline.assembly import (
            build_production_planner_runner,
            load_phase10_preset_registry,
        )
        from guanlan_v2.orchestration.spec import OrchestrationRequest

        now = datetime(2026, 7, 28, 2, 30, tzinfo=timezone.utc)

        class _Clock:
            def now(self):
                return now

        registry = p9.build_phase9_registry(p9.PHASE9_BASE_REGISTRY_DIGEST)
        resolver = SchemaRegistryResolver()
        resolver.register(registry)
        stores = RuntimeStores(
            resolver=resolver, clock=_Clock(),
            allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
        mem = P.build_empty_memory_context(
            data_context=P.pilot_data_context(as_of=now), stores=stores,
            registry_digest=registry.registry_digest, built_at=now)
        context = mem.context
        ctx_ref = PayloadRef(
            namespace="main", object_id="ctx-planner-t11",
            content_digest=context.content_digest)
        request = OrchestrationRequest(
            request_id="req-planner-t11", goal="a goal for the seam test",
            workflow="orchestrate_only", fallback_preset_id=None,
            approval_policy=ApprovalPolicy.REQUIRED)

        bundle = build_production_catalog_runtime(p9.phase9_catalog_snapshot())
        gateway = _ExplodingPlannerGateway()
        runner = build_production_planner_runner(
            stores=stores, catalog=bundle, schema_registry=registry,
            preset_registry=load_phase10_preset_registry(PRODUCTION_PRESETS_DIR),
            clock=_Clock(), model_gateway_factory=lambda: gateway)
        result = runner(
            request=request, context=context, context_snapshot_ref=ctx_ref,
            run_id="run-planner-t11", draft_id="plan-planner-t11")

        spec = build_phase7_planner_spec(bundle.snapshot)
        assert result.draft is None
        assert result.record.terminal_outcome == "halted_no_fallback"
        assert len(result.record.attempts) == spec.max_generation_attempts
        assert all(a.outcome == "model_error" for a in result.record.attempts)
        assert gateway.calls == spec.max_generation_attempts
        assert gateway.closed is True  # the finally closes the gateway
