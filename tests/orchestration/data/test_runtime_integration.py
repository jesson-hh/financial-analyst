# -*- coding: utf-8 -*-
"""Phase 3 · Task 8 — the DataRuntimeBridge over the REAL Phase-2 runtime.

End-to-end integration: the Task-6 ``dispatch`` / Task-7 ``DataReader`` run behind
the real Phase-2 ``ExecutionBridgeResolver`` / ``CapabilityGateway`` /
``BridgeEvidenceWriter`` / ``RuntimeStores`` (no Task-6/7 fakes on the write/
capability path), through the full admission pipeline, into ``execute_node``'s
NodeRun / Artifact provenance. The persistence registry is the cumulative Phase-3
data registry (never ``default_registry()``); refusal audit flows through the one
authoritative ``EventRefusalAuditSink`` over a data-aware audit-detail registry.

Run: ``python -m pytest tests/orchestration/data/test_runtime_integration.py -v``
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.admission import ApprovalSubmission, PlanAdmissionService
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog import (
    CapabilityManifestEntry,
    CatalogError,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    OutputBinding,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    WorkerSpec,
    build_catalog_snapshot,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
    build_text_material,
    serialize_bridge_descriptor,
)
from guanlan_v2.orchestration.context import (
    ContextSnapshot,
    InputSnapshot,
    RunBudget,
    RunContext,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.data import runtime as RT
from guanlan_v2.orchestration.data.calendar import (
    TradingCalendarResolver,
    build_trading_calendar,
)
from guanlan_v2.orchestration.data.catalog import (
    DATA_BRIDGE_ID,
    DataBridgePrefetchBinding,
    DataBridgeSupportAnalyzer,
    DataPrefetchOperation,
    ParamBinding,
    phase3_data_surface,
    serialize_prefetch_binding,
)
from guanlan_v2.orchestration.data.errors import (
    NoDataError,
    NotConfiguredError,
    RateLimitError,
)
from guanlan_v2.orchestration.data.pit import DataFetchRefusalDetails, FreshnessPolicy, RawRowCandidate
from guanlan_v2.orchestration.data.registry import (
    FALLBACK_USED_BADGE,
    REQUEST_SCHEMA_REF,
    DataSourceRegistry,
)
from guanlan_v2.orchestration.data.schema_registry import PHASE3_PUBLIC_MODELS
from guanlan_v2.orchestration.data.snapshot import (
    build_data_context,
    build_data_snapshot_manifest,
    build_data_source_config_snapshot,
)
from guanlan_v2.orchestration.data.source import (
    DataMethodSpec,
    DataPolicyResolver,
    DataSourceDescriptor,
    ResolvedMethodRoute,
    RouteEntry,
    build_raw_fetch,
)
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DataBackend,
    DataMode,
    DataStatus,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
    UnknownRegistryDigest,
)
from guanlan_v2.orchestration.refs import (
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
    typed_ref_sort_key,
)
from guanlan_v2.orchestration.runtime_contracts import (
    Phase2RuntimeRegistry,
    default_audit_detail_registry,
    phase2_public_models,
)
from guanlan_v2.orchestration.schemas import Artifact, NodeRun
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    validate_plan_draft,
)
from tests.orchestration.test_runtime_support import (
    OtherOut,
    SR_CFG,
    SR_OUT,
    _Analyzer,
    _capability,
    _text,
)

UTC = timezone.utc
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
WORKER_ID = "dec.data"
_PRIMARY = "primary.vendor"
_BACKUP = "backup.vendor"
_HANDLER = ContentRef(id="data.source.handler", version="1", content_digest="a" * 64)

NODE_PARAMS = {
    "symbol": {"code": "600519", "exchange": "SH", "board": "main"},
    "start": "2026-07-01T00:00:00+00:00",
    "end": "2026-07-15T00:00:00+00:00",
}

RENDERED_BLOCK_SR = SchemaRef(name="RenderedDataBlock", version="1")
PROMPT_SR = SchemaRef(name="PromptAssemblyRecord", version="1")
OHLCV_RESULT_SR = SchemaRef(name="OHLCVDataResult", version="1")


class FixedClock:
    def __init__(self, now: datetime = AS_OF) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _weekdays_2026() -> tuple[date, ...]:
    out: list[date] = []
    d = date(2026, 1, 1)
    while d <= date(2026, 12, 31):
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


CALENDAR = build_trading_calendar(
    calendar_id="cn_a_share", sessions=_weekdays_2026(),
    material_id="cal.cn.2026", material_version="1",
)

#: session-based freshness so the REAL DataPolicyResolver binds the exact calendar.
SESSION_FRESHNESS = FreshnessPolicy.build(
    policy_id="policy.freshness.session", policy_version="1",
    method_or_category="prices", max_trading_sessions=30,
    calendar_id=CALENDAR.calendar_id, calendar_material_ref=CALENDAR.material_ref,
)
_FRESHNESS_REF = ContentRef(
    id=SESSION_FRESHNESS.policy_id, version=SESSION_FRESHNESS.policy_version,
    content_digest=SESSION_FRESHNESS.policy_digest,
)


# --------------------------------------------------------------------------- #
# cumulative Phase-3 persistence registry (+ the test worker's output schema)   #
# --------------------------------------------------------------------------- #
def build_test_schema_registry(extra_models: tuple[type, ...] = ()) -> Phase2RuntimeRegistry:
    """Phase-2 public + Phase-3 data models + the test worker output (OtherOut)."""
    reg = Phase2RuntimeRegistry()
    for model in tuple(phase2_public_models()) + PHASE3_PUBLIC_MODELS + (OtherOut,) + extra_models:
        reg.register(model)
    reg.seal()
    return reg


# --------------------------------------------------------------------------- #
# the reviewed data method specs (session freshness) + two-vendor registry      #
# --------------------------------------------------------------------------- #
def _session_specs() -> tuple[DataMethodSpec, ...]:
    surface = phase3_data_surface()
    specs = []
    for base in surface.method_specs:
        fields = {n: getattr(base, n) for n in type(base).model_fields if n != "spec_digest"}
        fields["freshness_policy_ref"] = _FRESHNESS_REF
        specs.append(DataMethodSpec.build(**fields))
    return tuple(specs)


def _descriptor(source_id: str, specs) -> DataSourceDescriptor:
    surface = phase3_data_surface()
    return DataSourceDescriptor.build(
        source_id=source_id, source_version="1",
        method_refs=tuple(s.method_ref for s in specs),
        method_capability_refs=tuple(
            sorted(surface.capability_refs.values(), key=lambda c: (c.id, c.version))),
        supported_modes=(DataMode.ONLINE, DataMode.PIT_REPLAY),
        supported_backends=(DataBackend.CACHE, DataBackend.LIVE, DataBackend.PIT_STORE),
        handler_ref=_HANDLER,
        source_config_schema_ref=SchemaRef(name="DataSourceConfigSnapshot", version="1"),
    )


def build_data_source_registry() -> DataSourceRegistry:
    specs = _session_specs()
    primary, backup = _descriptor(_PRIMARY, specs), _descriptor(_BACKUP, specs)
    p_ref = ContentRef(id=primary.source_id, version="1", content_digest=primary.descriptor_digest)
    b_ref = ContentRef(id=backup.source_id, version="1", content_digest=backup.descriptor_digest)
    reg = DataSourceRegistry(registry_version="phase3-source-v1")
    for spec in specs:
        reg.register_method(spec)
    reg.register_descriptor(primary).register_descriptor(backup)
    for spec in specs:
        reg.register_route(ResolvedMethodRoute(
            method_ref=spec.method_ref,
            entries=(RouteEntry(source_ref=p_ref, capability_ref=spec.capability_ref),
                     RouteEntry(source_ref=b_ref, capability_ref=spec.capability_ref)),
            route_policy_ref=ContentRef(id="policy.route.default", version="1",
                                        content_digest="d" * 64)))
    reg.register_freshness(SESSION_FRESHNESS)
    return reg.seal()


# --------------------------------------------------------------------------- #
# scripted vendors behind the ONE trusted capability backend                    #
# --------------------------------------------------------------------------- #
def _good_candidate(*, available_at: datetime | None = None, close: float = 1.5) -> RawRowCandidate:
    return RawRowCandidate.build(
        raw_payload={"symbol": {"code": "600519", "exchange": "SH", "board": "main"},
                     "open": 1.0, "high": 2.0, "low": 0.5, "close": close, "volume": 100},
        effective_at=datetime(2026, 7, 15, 3, 0, tzinfo=UTC),
        available_at=available_at or datetime(2026, 7, 15, 8, 0, tzinfo=UTC),
        ingested_at=datetime(2026, 7, 15, 9, 0, tzinfo=UTC),
    )


class ScriptedVendor:
    """One data-source adapter (DataSource protocol) with call accounting."""

    def __init__(self, source_ref: ContentRef, *, candidates=None, raise_exc=None):
        self.source_ref = source_ref
        self.candidates = tuple(candidates) if candidates is not None else (_good_candidate(),)
        self.raise_exc = raise_exc
        self.calls = 0

    def fetch(self, request, *, scope):
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        cap_ref = next(e.capability_ref for e in scope.frozen_route.entries
                       if e.source_ref == self.source_ref)
        return build_raw_fetch(
            request_digest=request.request_digest, source_ref=self.source_ref,
            capability_ref=cap_ref, candidates=self.candidates,
            subsource="main", fetched_at=AS_OF, provider_request_id="p-1")


class NullCache:
    def __init__(self):
        self.probes = 0

    def get_verified(self, key, *, ctx, manifest, registry, payload_store):
        self.probes += 1
        return None


class EchoBackend:
    """Trusted backend for the generic (non-data) capability of a second bridge."""

    def invoke(self, *, capability_ref, request):
        return OtherOut(note="tool-result")


def data_audit_sink(clock) -> EventRefusalAuditSink:
    """The ONE authoritative Phase-2 refusal sink over a data-aware detail registry."""
    reg = default_audit_detail_registry()
    reg.register(DataFetchRefusalDetails)
    reg.seal()
    return EventRefusalAuditSink(detail_registry=reg, clock=clock)


# --------------------------------------------------------------------------- #
# generic fake second bridge (runs beside the real data bridge)                 #
# --------------------------------------------------------------------------- #
@dataclass
class GenericProvider:
    """A minimal scripted generic bridge provider (mirrors Phase-2's test fake)."""

    bridge: object
    summary: object
    do_capability: bool = True
    cap_ref: Any = None

    def prepare_input(self, request):
        handle = W.PreparedBridgeHandle(
            bridge_id=self.bridge.bridge_id, bridge_priority=self.bridge.priority,
            summary_digest=self.summary.summary_digest, token=request.token,
            input_contribution=W.BridgeInputContribution())
        return W.BridgeStageOutcome(status="prepared",
                                    input_contribution=W.BridgeInputContribution(),
                                    prepared_handle=handle)

    def open_execution(self, request):
        return _GenericSession(self, request)


class _GenericSession:
    def __init__(self, provider, request):
        self.p, self.req = provider, request

    def freeze_for_execution(self, *, kind):
        records, data_refs = [], []
        if self.p.do_capability:
            gw, writer, token = (self.req.capability_gateway, self.req.evidence_writer,
                                 self.req.handle.token)
            pending = gw.begin(ordinal_token=token, capability_ref=self.p.cap_ref,
                               request_schema_ref=SR_OUT,
                               idempotency_key=f"{self.p.bridge.bridge_id}:call:0")
            unpub = gw.invoke(pending, OtherOut(note="tool-request"))
            result_model = gw.validate_result(unpub)
            req_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                                 payload=unpub.validated_request,
                                 idempotency_key=f"{self.p.bridge.bridge_id}:req:0")
            res_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                                 payload=result_model,
                                 idempotency_key=f"{self.p.bridge.bridge_id}:res:0")
            records.append(gw.finalize_success(pending, request_ref=req_ref, result_ref=res_ref))
            data_refs.append(res_ref)
        contribution = W.BridgeContribution(
            bridge_id=self.p.bridge.bridge_id, bridge_priority=self.p.bridge.priority,
            summary_digest=self.p.summary.summary_digest, tool_call_records=tuple(records),
            data_result_refs=tuple(data_refs))
        return W.BridgeStageOutcome(status="completed", frozen_contribution=contribution)


class FakeModelGateway:
    """Single-shot model gateway enforcing the exact prompt binding (Phase-2 fake)."""

    def __init__(self, stores):
        self._stores = stores
        self.invocations = 0

    def invoke(self, request, *, prompt_assembly_ref):
        W.verify_model_request_binding(request, prompt_assembly_ref, reader=self._stores.payloads)
        self.invocations += 1
        return W.ModelResult(payload=OtherOut(note="model-out"), rendered_text="model out",
                             number_anchors=(), input_tokens=11, output_tokens=7,
                             provider="fake-provider", model="fake-model",
                             provider_response_id="resp-1")


def det_handler_factory(*, worker, resolved):
    def handler(*, node, input_snapshot, contributions, data_result_refs):
        return W.ModelResult(payload=OtherOut(note="det-out"), rendered_text="det out",
                             number_anchors=(), input_tokens=0, output_tokens=0)
    return handler


# --------------------------------------------------------------------------- #
# the environment builder                                                       #
# --------------------------------------------------------------------------- #
def default_binding_rows(data_registry: DataSourceRegistry) -> tuple[DataPrefetchOperation, ...]:
    spec = data_registry.method_spec("ohlcv")
    route = data_registry.default_route("ohlcv")
    return (DataPrefetchOperation(
        worker_id=WORKER_ID, method_ref=spec.method_ref, capability_ref=spec.capability_ref,
        frozen_route=route.entries, invocation_mode="cache_or_invoke",
        success_requires_finalized_call=False,
        param_bindings=(
            ParamBinding(target_pointer="/end", source_kind="node_param", source_pointer="/end"),
            ParamBinding(target_pointer="/start", source_kind="node_param", source_pointer="/start"),
            ParamBinding(target_pointer="/symbol", source_kind="node_param", source_pointer="/symbol"),
        )),)


@dataclass
class DataEnv:
    clock: FixedClock
    stores: RuntimeStores
    schema_registry: Phase2RuntimeRegistry
    rt_digest: str
    data_registry: DataSourceRegistry
    routing: Any
    manifest: Any
    source_config: Any
    dc: Any
    world: RT.DataRuntimeWorld
    backend: RT.DataSourceCapabilityBackend
    vendors: dict
    cache: Any
    refusal_sink: EventRefusalAuditSink
    catalog_runtime: CatalogRuntime
    view: BridgeCatalogView
    registry1: Any  # phase-1-facing registry surface used at admission (== schema_registry)
    plan: Any
    node: Any
    worker: Any
    ctx: RunContext
    node_res: Any
    input_snapshot: Any
    support_report: Any
    context_snapshot: Any
    data_bridge_provider_ref: ContentRef
    generic_bridge: bool
    generic_cap: Any
    det_handler_ref: Any
    binding: DataBridgePrefetchBinding
    run_budget: RunBudget
    plan_res: Any
    _attempt: int = 1
    factories: TrustedFactoryRegistry | None = None
    runtime: W.ExecutionRuntime | None = None
    _draft: Any = None
    request: Any = None

    # -- runtime wiring ------------------------------------------------------ #
    def build_runtime(self, *, generic_do_capability: bool = True) -> None:
        factories = TrustedFactoryRegistry(self.catalog_runtime)
        factories.register_handler(
            self.data_bridge_provider_ref, RT.data_runtime_provider_factory(self.world))
        if self.generic_bridge:
            rb = self.view.resolve("bridge.generic")
            factories.register_handler(
                rb.provider_ref,
                lambda *, bridge, summary: GenericProvider(
                    bridge=bridge, summary=summary, do_capability=generic_do_capability,
                    cap_ref=self.generic_cap))
        if self.det_handler_ref is not None:
            factories.register_handler(self.det_handler_ref, det_handler_factory)
        for cap in self.worker.capability_allowlist:
            if cap.id.startswith("cap.data."):
                factories.register_capability_backend(cap, lambda **kw: self.backend)
            else:
                factories.register_capability_backend(cap, lambda **kw: EchoBackend())
        self.factories = factories
        self.runtime = W.ExecutionRuntime(
            catalog=self.catalog_runtime, bridge_view=self.view, factories=factories,
            support_report=self.support_report, runtime_registry_digest=self.rt_digest)

    def prepare(self, *, observer=None):
        ctx_ref = self.input_snapshot.context_snapshot_ref
        resolver, prepared, seq = W.prepare_input(
            self.plan, self.node, runtime=self.runtime, ctx=self.ctx, stores=self.stores,
            registry=self.schema_registry, context_snapshot_ref=ctx_ref, clock=self.clock,
            attempt=self._attempt, observer=observer)
        self._last_seq = seq
        return resolver, prepared, seq

    def capability_gateway(self):
        summaries = {s.summary_digest: s for s in self.runtime.summaries_for(self.node.id)}
        return W.CapabilityGateway(
            plan_digest=self.plan.plan_digest, worker=self.worker, summaries=summaries,
            catalog=self.catalog_runtime, factories=self.factories,
            phase1_registry=self.schema_registry, refusal_sink=self.refusal_sink,
            clock=self.clock, sequencer=self._last_seq)

    def run(self, *, observer=None, generic_do_capability: bool = True,
            attempt: int | None = None):
        if attempt is not None:
            self._attempt = attempt
        self.build_runtime(generic_do_capability=generic_do_capability)
        resolver, prepared, seq = self.prepare(observer=observer)
        gw = self.capability_gateway()
        mg = (FakeModelGateway(self.stores)
              if self.worker.execution.kind is ExecutionKind.LLM else None)
        self.model_gateway = mg
        return W.execute_node(
            self.plan, self.node, runtime=self.runtime, prepared_bridges=prepared,
            input_snapshot=self.input_snapshot, ctx=self.ctx, node_reservation=self.node_res,
            bridge_resolver=resolver, model_gateway=mg, capability_gateway=gw,
            registry=self.schema_registry, stores=self.stores, clock=self.clock,
            observer=observer, attempt=self._attempt)

    def data_summary(self):
        return next(s for s in self.support_report.bridge_support_summaries
                    if s.bridge_id == DATA_BRIDGE_ID and s.node_id == self.node.id)


def build_data_env(
    *,
    binding_rows=None,
    vendors: dict | None = None,
    cache=None,
    generic_bridge: bool = False,
    grant_data: bool = True,
    allowlist_data: bool = True,
    activation_by_category: bool = False,
    deterministic: bool = False,
    tool_calls=ToolCallRequirement.OPTIONAL,
) -> DataEnv:
    clock = FixedClock()
    schema_registry = build_test_schema_registry()

    # -- the frozen data world ---------------------------------------------- #
    data_registry = build_data_source_registry()
    source_config = build_data_source_config_snapshot(
        config_version="cfg-1", method_selections={}, source_options={})
    routing = data_registry.build_routing_snapshot(
        audit_id="route-1", schema_registry_digest=schema_registry.registry_digest,
        source_config=source_config)
    manifest = build_data_snapshot_manifest(
        data_snapshot_id="snap-1", manifest_kind="online_capture_root", as_of=AS_OF,
        mode=DataMode.ONLINE, timezone="Asia/Shanghai", calendar_id="cn_a_share",
        routing_snapshot_digest=routing.routing_digest,
        schema_registry_digest=schema_registry.registry_digest, entries=())
    dc = build_data_context(
        clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        source_config=source_config, source_registry=data_registry.snapshot(),
        routing=routing, manifest=manifest)

    if binding_rows is None:
        binding_rows = default_binding_rows(data_registry)
    binding = DataBridgePrefetchBinding.build(
        bridge_id=DATA_BRIDGE_ID, bridge_version="1", operations=tuple(binding_rows))

    # -- vendors + backend ---------------------------------------------------- #
    route = data_registry.default_route("ohlcv")
    p_ref, b_ref = route.entries[0].source_ref, route.entries[1].source_ref
    if vendors is None:
        vendors = {_PRIMARY: ScriptedVendor(p_ref), _BACKUP: ScriptedVendor(b_ref)}
    backend = RT.DataSourceCapabilityBackend({sid: v for sid, v in vendors.items()})

    # -- catalog: prompt + data bridge material (+ optional generic bridge) --- #
    surface = phase3_data_surface()
    prompt_ref, prompt_mat = _text("text.data.prompt", "prompt", "You are the data worker.")
    granted_caps: list = []
    granted_methods = sorted({op.method_ref.id for op in binding.operations})
    for mid in granted_methods:
        granted_caps.append(data_registry.method_spec(mid).capability_ref)
    config_bytes = serialize_prefetch_binding(binding)
    config_ref, config_mat = build_text_material(
        id="bridge.data_runtime.prefetch.test", version="1", kind="guardrail", raw=config_bytes)
    bridge_descriptor = None
    from guanlan_v2.orchestration.runtime_contracts import ExecutionBridgeDescriptor

    bridge_descriptor = ExecutionBridgeDescriptor(
        bridge_id=DATA_BRIDGE_ID, bridge_version="1", priority=100,
        provider_handler_ref=surface.provider_ref, config_ref=config_ref,
        support_analyzer_ref=surface.analyzer_ref,
        config_schema_ref=SchemaRef(name="DataBridgePrefetchBinding", version="1"),
        activation_capability_refs=tuple(sorted(granted_caps, key=lambda c: (c.id, c.version))),
        activation_read_categories=("context",) if activation_by_category else (),
        supported_execution_kinds=(ExecutionKind.DETERMINISTIC, ExecutionKind.LLM),
        pre_input_kind="none", lifecycle="static_prefetch_v1")
    d_ref, d_mat = build_text_material(
        id="bridge.data_runtime.descriptor.test", version="1", kind="guardrail",
        raw=serialize_bridge_descriptor(bridge_descriptor))

    content = [
        ContentManifestEntry(ref=prompt_ref, kind="prompt", name="P", description="d",
                             source_identity="gl.data"),
        ContentManifestEntry(ref=surface.renderer_ref, kind="handler", name="Renderer",
                             description="d", source_identity="gl.data"),
        ContentManifestEntry(ref=surface.provider_ref, kind="handler", name="Provider",
                             description="d", source_identity="gl.data"),
        ContentManifestEntry(ref=surface.analyzer_ref, kind="handler", name="Analyzer",
                             description="d", source_identity="gl.data"),
    ]
    mats: list = [prompt_mat, surface.renderer_material, surface.provider_material,
                  surface.analyzer_material]
    cap_manifest: list = []
    cap_by_id = {m.ref.id: m for m in surface.capability_materials}
    worker_caps: list = []
    if grant_data:
        for cap in granted_caps:
            mat = cap_by_id[cap.id]
            mats.append(mat)
            cap_manifest.append(CapabilityManifestEntry(
                ref=mat.ref, capability_kind="data_adapter", transport="in_process"))
            if allowlist_data:
                worker_caps.append(mat.ref)
        content.append(ContentManifestEntry(ref=config_ref, kind="guardrail",
                                            name="DataCfg", description="d",
                                            source_identity="gl.data"))
        content.append(ContentManifestEntry(ref=d_ref, kind="guardrail",
                                            name=DATA_BRIDGE_ID, description="d",
                                            source_identity="gl.data"))
        mats += [config_mat, d_mat]

    analyzers = {(surface.analyzer_ref.id, surface.analyzer_ref.version,
                  surface.analyzer_ref.content_digest): DataBridgeSupportAnalyzer()}

    generic_cap = None
    if generic_bridge:
        g_cap_ref, g_cap_mat = _capability("cap.tool")
        generic_cap = g_cap_ref
        worker_caps.append(g_cap_ref)
        mats.append(g_cap_mat)
        cap_manifest.append(CapabilityManifestEntry(ref=g_cap_ref, capability_kind="tool",
                                                    transport="in_process"))
        g_cfg_ref, g_cfg_mat = _text("guard.cfg.generic", "guardrail", '{"mode":"x"}')
        g_prov_ref, g_prov_mat = _text("handler.provider.generic", "handler", "def p(c): return c")
        g_anal_ref, g_anal_mat = _text("handler.analyzer.generic", "handler", "def a(c): return c")
        g_desc = ExecutionBridgeDescriptor(
            bridge_id="bridge.generic", bridge_version="1", priority=10,
            provider_handler_ref=g_prov_ref, config_ref=g_cfg_ref,
            support_analyzer_ref=g_anal_ref, config_schema_ref=SR_CFG,
            activation_capability_refs=(g_cap_ref,), activation_read_categories=(),
            supported_execution_kinds=(ExecutionKind.LLM,), pre_input_kind="memory_refs_v1")
        g_d_ref, g_d_mat = _text("guard.generic", "guardrail",
                                 serialize_bridge_descriptor(g_desc).decode("utf-8"))
        content += [
            ContentManifestEntry(ref=g_cfg_ref, kind="guardrail", name="GCfg", description="d",
                                 source_identity="gl.data"),
            ContentManifestEntry(ref=g_prov_ref, kind="handler", name="GProv", description="d",
                                 source_identity="gl.data"),
            ContentManifestEntry(ref=g_anal_ref, kind="handler", name="GAnal", description="d",
                                 source_identity="gl.data"),
            ContentManifestEntry(ref=g_d_ref, kind="guardrail", name="bridge.generic",
                                 description="d", source_identity="gl.data"),
        ]
        mats += [g_cfg_mat, g_prov_mat, g_anal_mat, g_d_mat]
        analyzers[(g_anal_ref.id, g_anal_ref.version, g_anal_ref.content_digest)] = _Analyzer(
            min_calls=1, max_calls=2)

    det_handler_ref = None
    if deterministic:
        det_handler_ref, det_mat = _text("handler.det.data", "handler", "def run(c): return c")
        content.append(ContentManifestEntry(ref=det_handler_ref, kind="handler", name="Det",
                                            description="d", source_identity="gl.data"))
        mats.append(det_mat)
        execution = ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=det_handler_ref)
    else:
        execution = ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner")

    worker = WorkerSpec(
        id=WORKER_ID, catalog_role="final", selection_scope="dynamic_allowed",
        lane="quant", persona="analyst", tier=Tier.WRITER,
        execution=execution,
        system_prompt_ref=None if deterministic else prompt_ref,
        params_schema_ref=SchemaRef(name="InstrumentSeriesParams", version="1"),
        capability_allowlist=tuple(sorted(worker_caps, key=lambda c: (c.id, c.version))),
        read_categories=("context",), inputs=(),
        outputs=(OutputBinding(name="primary", schema_ref=SR_OUT),),
        evidence_policy=EvidencePolicy(tool_calls=tool_calls),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none")

    snapshot = build_catalog_snapshot(
        catalog_version="data-int-v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=tuple(cap_manifest), workers=(worker,),
        resolved_material=tuple(mats))
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats
              if isinstance(m, ResolvedTextMaterial)},
        capabilities={(m.ref.id, m.ref.version): m.descriptor for m in mats
                      if isinstance(m, ResolvedCapabilityMaterial)})
    catalog_runtime = CatalogRuntime.build(snapshot, source)
    view = BridgeCatalogView.build(catalog_runtime, analyzers)

    # -- context snapshot carrying the REAL data context ---------------------- #
    binding_mem = build_empty_memory_binding()
    context = ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding_mem.snapshot_hash,
        past_context_hash=binding_mem.past_context_hash,
        memory_snapshot_ref=binding_mem.memory_snapshot_ref,
        memory_selection_ref=binding_mem.memory_selection_ref,
        runtime_requirements_ref=None, built_at=AS_OF)

    node = PlanNode(id="n1", worker_id=WORKER_ID, writes_slot="s1", params=dict(NODE_PARAMS))
    ctx_pref = PayloadRef(namespace="main", object_id="ctx-obj",
                          content_digest=context.content_digest)
    draft = PlanDraft(
        id="plan.data", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=AS_OF, mode=DataMode.ONLINE,
        context_snapshot_ref=ctx_pref, nodes=(node,), sink_node_ids=("n1",),
        catalog_version=snapshot.catalog_version, catalog_digest=snapshot.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest,
        approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=500_000, budget_request_llm_invocations=100,
        max_concurrency=8)
    request = OrchestrationRequest(request_id="req-1", goal="g", workflow="orchestrate_only",
                                   approval_policy=ApprovalPolicy.REQUIRED)

    # -- stores + registries --------------------------------------------------- #
    resolver = SchemaRegistryResolver()
    resolver.register(schema_registry)
    from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
    from guanlan_v2.orchestration.schema_registry import default_registry

    rt_reg = phase2_runtime_registry(default_registry().registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    refusal_sink = data_audit_sink(clock)
    run_budget = RunBudget(ledger_id="led-1", max_tokens=2_000_000,
                           max_llm_invocations=1000, max_concurrency=16)

    from guanlan_v2.orchestration.runtime_contracts import static_runtime_profile

    approvals: dict = {}
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request},
        drafts={draft.id: draft}, contexts={context.content_digest: context},
        attestations={}, approvals=approvals,
        catalog=catalog_runtime, bridge_view=view, phase1_registry=schema_registry,
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
        cand.candidate_plan_digest, reservation_id=res.reservation_id,
        approval_event_id=ev.event_id, idempotency_key="freeze-1")
    bundle = service.verify_for_dispatch(plan.plan_digest)

    ledger = BudgetLedger(
        sink=stores.budget_event_sink(run_id=draft.run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)
    node_res = ledger.reserve_node(
        plan_reservation_id=res.reservation_id, node_id=node.id, attempt=1,
        tokens=1000, llm_invocations=1, concurrency=1, idempotency_key="node-res-1")

    ctx_payload_ref = stores.payloads.put(
        SchemaRef(name="ContextSnapshot", version="1"), context,
        registry_digest=rt_digest, namespace="main", idempotency_key="ctx-snap")
    ctx_typed = TypedPayloadRef(schema_ref=SchemaRef(name="ContextSnapshot", version="1"),
                                payload_ref=ctx_payload_ref)
    input_snapshot = InputSnapshot.build(
        snapshot_id="isnap-1", run_id=draft.run_id, plan_id=plan.plan_id,
        plan_digest=plan.plan_digest, node_id=node.id, layer_index=0, attempt=1,
        context_snapshot_ref=ctx_typed, artifact_inputs=(), data_result_refs=(),
        memory_record_refs=(), readiness="ready", built_at=clock.now())

    run_ctx = RunContext(
        run_id=draft.run_id, data=context.data_context,
        context_snapshot_id=context.snapshot_id,
        memory_snapshot_hash=context.memory_snapshot_hash, budget=run_budget,
        cancellation_token_id="cancel-1")

    the_cache = cache if cache is not None else NullCache()
    world = RT.DataRuntimeWorld(
        source_registry=data_registry, routing=routing, manifest=manifest,
        source_config=source_config, ctx=dc, schema_resolver=resolver,
        policy_resolver=DataPolicyResolver(
            data_registry.snapshot(), calendar_resolver=TradingCalendarResolver([CALENDAR])),
        calendar=CALENDAR, clock=clock, cache=the_cache,
        catalog_runtime=catalog_runtime, refusal_audit_sink=refusal_sink, backend=backend)

    ledger_res = res
    return DataEnv(
        clock=clock, stores=stores, schema_registry=schema_registry, rt_digest=rt_digest,
        data_registry=data_registry, routing=routing, manifest=manifest,
        source_config=source_config, dc=dc, world=world, backend=backend, vendors=vendors,
        cache=the_cache, refusal_sink=refusal_sink, catalog_runtime=catalog_runtime,
        view=view, registry1=schema_registry, plan=plan, node=node, worker=worker,
        ctx=run_ctx, node_res=node_res, input_snapshot=input_snapshot,
        support_report=bundle.support_report, context_snapshot=context,
        data_bridge_provider_ref=surface.provider_ref, generic_bridge=generic_bridge,
        generic_cap=generic_cap, det_handler_ref=det_handler_ref, binding=binding,
        run_budget=run_budget, plan_res=ledger_res, _draft=draft, request=request)


# =========================================================================== #
# 1. one authorized read: atomic evidence, four-argument finalize, provenance   #
# =========================================================================== #
def test_authorized_read_end_to_end_provenance():
    env = build_data_env()
    node_run, artifact = env.run()
    assert isinstance(node_run, NodeRun) and isinstance(artifact, Artifact)
    assert node_run.status is NodeStatus.COMPLETED

    # exactly one Phase-1 ToolCallRecord, minted only through finalize_success.
    assert len(node_run.tool_call_records) == 1
    record = node_run.tool_call_records[0]
    assert record.tool_ref.id == "cap.data.ohlcv"

    # the consumed typed DataResult ref enters data_result_refs.
    assert len(node_run.data_result_refs) == 1
    result_ref = node_run.data_result_refs[0]
    assert result_ref.schema_ref == OHLCV_RESULT_SR
    assert result_ref == record.result_ref  # cross-match: tool result == DataResult

    # the finalized call's request ref is represented BY the record (never
    # double-listed); the single prompt ref is the only direct execution evidence.
    assert record.request_ref.schema_ref.name == "DataRequest"
    schemas = {r.schema_ref.name for r in node_run.execution_evidence_refs}
    assert "PromptAssemblyRecord" in schemas
    assert "DataRequest" not in schemas

    # provenance tuples EXACTLY equal the NodeRun tuples.
    prov = artifact.provenance
    assert prov.tool_call_records == node_run.tool_call_records
    assert prov.data_result_refs == node_run.data_result_refs
    assert prov.execution_evidence_refs == node_run.execution_evidence_refs

    # the persisted named result resolves and passed the PIT guard.
    result = env.stores.payloads.get(result_ref.payload_ref, expected_schema_ref=OHLCV_RESULT_SR)
    assert result.status is DataStatus.OK
    assert result.pit_audit.guard_result == "passed"
    assert result.resolved_vendor_chain == (_PRIMARY, _BACKUP)
    # finalize consumed the EXACT persisted refs — the record's digests match.
    assert record.result_ref.payload_ref.content_digest == result.content_digest

    # only the primary vendor was invoked.
    assert env.vendors[_PRIMARY].calls == 1
    assert env.vendors[_BACKUP].calls == 0


def test_running_precedes_every_source_call():
    env = build_data_env()
    observer = W.ExecutionObserver()
    node_run, artifact = env.run(observer=observer)
    assert node_run.status is NodeStatus.COMPLETED
    assert observer.events.index("running") < observer.events.index(
        f"bridge_open:{DATA_BRIDGE_ID}")


def test_prepare_input_is_io_free_and_empty():
    env = build_data_env()
    env.build_runtime()
    resolver, prepared, seq = env.prepare()
    # prepare stage performed no vendor call and added no input-snapshot entry.
    assert env.vendors[_PRIMARY].calls == 0 and env.vendors[_BACKUP].calls == 0
    handle = prepared.handles[0]
    assert handle.bridge_id == DATA_BRIDGE_ID
    assert handle.input_contribution.memory_refs == ()
    assert handle.input_contribution.memory_evidence_refs == ()


# =========================================================================== #
# 2. capability outside the worker allowlist is denied before invocation       #
# =========================================================================== #
def test_grant_outside_worker_allowlist_denied_before_any_handler():
    reg = build_data_source_registry()
    rows = default_binding_rows(reg)
    vendors = {
        _PRIMARY: ScriptedVendor(reg.default_route("ohlcv").entries[0].source_ref),
        _BACKUP: ScriptedVendor(reg.default_route("ohlcv").entries[1].source_ref),
    }
    # the worker allowlist gets NO data capability while the binding grants one
    # (the bridge stays active via its read-category predicate): the reviewed
    # analyzer refuses at support/admission time — before ANY handler invocation.
    with pytest.raises(CatalogError, match="outside worker"):
        build_data_env(binding_rows=rows, vendors=vendors, allowlist_data=False,
                       activation_by_category=True, generic_bridge=True)
    assert vendors[_PRIMARY].calls == 0 and vendors[_BACKUP].calls == 0


def test_runtime_wrong_capability_and_max_plus_one_fail_before_source_io():
    env = build_data_env()
    env.build_runtime()
    resolver, prepared, seq = env.prepare()
    gw = env.capability_gateway()
    gw.bind_reader(env.stores.payloads)
    gw.mark_running()
    summary = env.data_summary()
    spec = env.data_registry.method_spec("ohlcv")

    # (a) a capability whose ref is NOT in the worker allowlist fails at begin.
    foreign = env.data_registry.method_spec("indicators").capability_ref
    t1 = seq.issue_call_token(bridge_priority=100, bridge_id=DATA_BRIDGE_ID,
                              summary_digest=summary.summary_digest)
    with pytest.raises(W.CapabilityGatewayError, match="allowlist"):
        gw.begin(ordinal_token=t1, capability_ref=foreign,
                 request_schema_ref=REQUEST_SCHEMA_REF, idempotency_key="deny-1")

    # (b) max+1 under the data summary fails BEFORE backend I/O.
    assert summary.max_capability_invocations == 2
    for i in range(2):
        tok = seq.issue_call_token(bridge_priority=100, bridge_id=DATA_BRIDGE_ID,
                                   summary_digest=summary.summary_digest)
        gw.begin(ordinal_token=tok, capability_ref=spec.capability_ref,
                 request_schema_ref=REQUEST_SCHEMA_REF, idempotency_key=f"ok-{i}")
    t4 = seq.issue_call_token(bridge_priority=100, bridge_id=DATA_BRIDGE_ID,
                              summary_digest=summary.summary_digest)
    with pytest.raises(W.CapabilityGatewayError, match="max_capability_invocations"):
        gw.begin(ordinal_token=t4, capability_ref=spec.capability_ref,
                 request_schema_ref=REQUEST_SCHEMA_REF, idempotency_key="over-max")
    assert env.vendors[_PRIMARY].calls == 0 and env.vendors[_BACKUP].calls == 0
    reasons = [r.reason_code for r in env.refusal_sink.records()]
    assert "capability_not_in_worker_allowlist" in reasons
    assert "max_capability_invocations_exceeded" in reasons


# =========================================================================== #
# 3. catalog / registry digest drift requires a new candidate                   #
# =========================================================================== #
def test_catalog_digest_drift_requires_new_candidate_and_fails_preflight():
    env_a = build_data_env(generic_bridge=True, grant_data=False,
                           binding_rows=None, vendors={})
    env_b = build_data_env(generic_bridge=True)
    # a data grant is a catalog change: the candidate identity MUST change.
    assert env_a.plan.catalog_digest != env_b.plan.catalog_digest
    assert env_a.plan.plan_digest != env_b.plan.plan_digest

    # driving env_a's admitted plan against env_b's (data-granting) catalog fails
    # before any provider/handler execution.
    env_b.build_runtime()
    mixed = W.ExecutionRuntime(
        catalog=env_b.catalog_runtime, bridge_view=env_b.view, factories=env_b.factories,
        support_report=env_a.support_report, runtime_registry_digest=env_b.rt_digest)
    with pytest.raises(W.PreflightError):
        W.prepare_input(env_a.plan, env_a.node, runtime=mixed, ctx=env_a.ctx,
                        stores=env_a.stores, registry=env_a.schema_registry,
                        context_snapshot_ref=env_a.input_snapshot.context_snapshot_ref,
                        clock=env_a.clock)


def test_schema_registry_digest_drift_is_rejected_at_validation():
    env = build_data_env()
    drifted = env._draft.model_copy(update={"schema_registry_digest": "9" * 64})
    report = validate_plan_draft(
        drifted, request=env.request, context=env.context_snapshot,
        catalog=env.catalog_runtime.snapshot, schema_registry=env.schema_registry)
    assert report.valid is False


# =========================================================================== #
# 4. wrong schema / namespace / registry staging is refused                     #
# =========================================================================== #
def test_payload_cannot_stage_under_wrong_schema_namespace_or_registry():
    env = build_data_env()
    seq = W.ExecutionEvidenceSequencer(node_id="n1", attempt=1)
    writer = W.BridgeEvidenceWriter(
        stores=env.stores, sequencer=seq, run_id="run-1", plan_digest=env.plan.plan_digest,
        node_id="n1", data_registry_digest=env.schema_registry.registry_digest,
        runtime_registry_digest=env.rt_digest)
    token = seq.issue_call_token(bridge_priority=100, bridge_id=DATA_BRIDGE_ID,
                                 summary_digest="0" * 64)
    # wrong schema for the payload → the registry validation refuses the write.
    with pytest.raises(Exception) as exc_info:
        writer.put(token=token, role="provider_prefetch", schema_ref=OHLCV_RESULT_SR,
                   payload=OtherOut(note="not-a-result"), idempotency_key="bad-schema")
    assert isinstance(exc_info.value, (ValidationError, ValueError, Exception))

    # a non-main typed ref cannot be recorded as existing evidence.
    bad_ref = TypedPayloadRef(
        schema_ref=OHLCV_RESULT_SR,
        payload_ref=PayloadRef(namespace="audit", object_id="x", content_digest="1" * 64))
    with pytest.raises(W.EvidenceGatewayNamespace):
        writer.record_existing(token=token, role="tool_result", typed_ref=bad_ref,
                               idempotency_key="bad-ns")

    # an unknown registry digest refuses the write (no global 'latest' registry).
    writer2 = W.BridgeEvidenceWriter(
        stores=env.stores, sequencer=seq, run_id="run-1", plan_digest=env.plan.plan_digest,
        node_id="n1", data_registry_digest="f" * 64, runtime_registry_digest=env.rt_digest)
    with pytest.raises(UnknownRegistryDigest):
        writer2.put(token=token, role="provider_prefetch", schema_ref=SR_OUT,
                    payload=OtherOut(note="x"), idempotency_key="bad-registry")


def test_bridge_resolves_schema_registry_only_from_plan_digest():
    env = build_data_env()
    # a world whose resolver does NOT know the plan's registry digest fails closed.
    world2 = RT.DataRuntimeWorld(
        source_registry=env.world.source_registry, routing=env.world.routing,
        manifest=env.world.manifest, source_config=env.world.source_config,
        ctx=env.world.ctx, schema_resolver=SchemaRegistryResolver(),
        policy_resolver=env.world.policy_resolver, calendar=env.world.calendar,
        clock=env.world.clock, cache=env.world.cache,
        catalog_runtime=env.world.catalog_runtime,
        refusal_audit_sink=env.world.refusal_audit_sink, backend=env.world.backend)
    env.world = world2
    node_run, artifact = env.run()
    assert artifact is None
    assert node_run.status is NodeStatus.FAILED
    assert env.vendors[_PRIMARY].calls == 0


# =========================================================================== #
# 5. relocation is audit-only; content changes semantics                        #
# =========================================================================== #
def test_object_id_relocation_is_audit_only_for_typed_data_refs():
    env = build_data_env()
    node_run, artifact = env.run()
    result_ref = node_run.data_result_refs[0]
    result = env.stores.payloads.get(result_ref.payload_ref,
                                     expected_schema_ref=OHLCV_RESULT_SR)
    # persist the byte-identical result under a different idempotency key → a NEW
    # object id, but the SAME semantic identity.
    seq = W.ExecutionEvidenceSequencer(node_id="n1", attempt=7)
    writer = W.BridgeEvidenceWriter(
        stores=env.stores, sequencer=seq, run_id="run-1", plan_digest=env.plan.plan_digest,
        node_id="n1", data_registry_digest=env.schema_registry.registry_digest,
        runtime_registry_digest=env.rt_digest)
    tok = seq.issue_call_token(bridge_priority=100, bridge_id=DATA_BRIDGE_ID,
                               summary_digest="0" * 64)
    relocated = writer.put(token=tok, role="tool_result", schema_ref=OHLCV_RESULT_SR,
                           payload=result, idempotency_key="relocate-1")
    assert relocated.payload_ref.object_id != result_ref.payload_ref.object_id
    assert typed_ref_sort_key(relocated) == typed_ref_sort_key(result_ref)


# =========================================================================== #
# 6. two-source fallback: distinct attempt tokens, deterministic ordering       #
# =========================================================================== #
def test_two_source_fallback_uses_distinct_attempt_tokens_and_badges():
    reg = build_data_source_registry()
    route = reg.default_route("ohlcv")
    vendors = {
        _PRIMARY: ScriptedVendor(route.entries[0].source_ref,
                                 raise_exc=RateLimitError("throttled")),
        _BACKUP: ScriptedVendor(route.entries[1].source_ref),
    }
    env = build_data_env(vendors=vendors)
    node_run, artifact = env.run()
    assert node_run.status is NodeStatus.COMPLETED
    assert vendors[_PRIMARY].calls == 1 and vendors[_BACKUP].calls == 1

    # exactly one finalized record — at the BACKUP's own attempt ordinal.
    assert len(node_run.tool_call_records) == 1
    record = node_run.tool_call_records[0]
    result = env.stores.payloads.get(node_run.data_result_refs[0].payload_ref,
                                     expected_schema_ref=OHLCV_RESULT_SR)
    assert FALLBACK_USED_BADGE in result.badges
    assert [a.outcome for a in result.attempts] == ["rate_limited", "success"]
    assert result.attempts[0].vendor == _PRIMARY
    assert result.attempts[1].vendor == _BACKUP

    # the rate-limited attempt was rejected exactly once through the ONE sink.
    reasons = [r.reason_code for r in env.refusal_sink.records()]
    assert reasons.count("rate_limited") == 1

    # ordering is canonical: refs sorted by semantic key, records by ordinal.
    assert list(node_run.data_result_refs) == sorted(
        node_run.data_result_refs, key=typed_ref_sort_key)
    prov = artifact.provenance
    assert prov.tool_call_records == node_run.tool_call_records


def test_broken_source_premature_terminal_rejected_once_and_raised():
    reg = build_data_source_registry()
    route = reg.default_route("ohlcv")
    vendors = {
        _PRIMARY: ScriptedVendor(
            route.entries[0].source_ref,
            raise_exc=NoDataError(symbol="600519", canonical="600519.SH", detail="x")),
        _BACKUP: ScriptedVendor(route.entries[1].source_ref),
    }
    env = build_data_env(vendors=vendors)
    node_run, artifact = env.run()
    assert artifact is None
    assert node_run.status is NodeStatus.FAILED
    assert node_run.error_type == "SourceBrokenError"
    assert vendors[_BACKUP].calls == 0  # a broken source NEVER advances the chain
    reasons = [r.reason_code for r in env.refusal_sink.records()]
    assert reasons.count("broken_source") == 1
    assert node_run.tool_call_records == ()


# =========================================================================== #
# 7. idempotent replay: no duplicate payload; conflicting reuse fails           #
# =========================================================================== #
def test_same_semantics_same_key_is_idempotent_and_conflict_fails():
    env = build_data_env()
    node_run, _ = env.run()
    result_ref = node_run.data_result_refs[0]
    result = env.stores.payloads.get(result_ref.payload_ref,
                                     expected_schema_ref=OHLCV_RESULT_SR)

    # same key + same semantics: the ORIGINAL payload ref, no duplicate object.
    before = env.stores.payloads_object_count()
    ref1 = env.stores.payloads.put(
        OHLCV_RESULT_SR, result, registry_digest=env.schema_registry.registry_digest,
        namespace="main", idempotency_key="data-replay-key")
    ref2 = env.stores.payloads.put(
        OHLCV_RESULT_SR, result, registry_digest=env.schema_registry.registry_digest,
        namespace="main", idempotency_key="data-replay-key")
    assert ref1 == ref2
    assert env.stores.payloads_object_count() == before + 1

    # the same key with DIFFERENT semantics conflicts loudly.
    from guanlan_v2.orchestration.eventstore import IdempotencyConflict

    with pytest.raises(IdempotencyConflict):
        env.stores.payloads.put(
            SR_OUT, OtherOut(note="different"),
            registry_digest=env.schema_registry.registry_digest,
            namespace="main", idempotency_key="data-replay-key")


# =========================================================================== #
# 9. future data: pending → rejected, ONE refusal audit, no second source       #
# =========================================================================== #
def test_future_data_refusal_audited_once_no_fallback_no_artifact():
    reg = build_data_source_registry()
    route = reg.default_route("ohlcv")
    future_cand = _good_candidate(available_at=datetime(2026, 7, 17, 8, 0, tzinfo=UTC))
    vendors = {
        _PRIMARY: ScriptedVendor(route.entries[0].source_ref, candidates=(future_cand,)),
        _BACKUP: ScriptedVendor(route.entries[1].source_ref),
    }
    env = build_data_env(vendors=vendors)
    baseline = env.stores.payloads_object_count()
    node_run, artifact = env.run()

    # the node terminates with the reviewed PIT refusal — no degrade, no fallback.
    assert artifact is None
    assert node_run.status is NodeStatus.FAILED
    assert node_run.error_type == "FutureDataRefused"
    assert vendors[_PRIMARY].calls == 1
    assert vendors[_BACKUP].calls == 0  # NEVER a second source

    # exactly one typed refusal audit through the authoritative Phase-2 sink.
    futures = [r for r in env.refusal_sink.records() if r.reason_code == "future_data"]
    assert len(futures) == 1
    assert futures[0].detail_schema_ref == SchemaRef(name="DataFetchRefusalDetails", version="1")
    assert futures[0].detail_payload_ref.namespace == "audit"

    # no evidence-counting ToolCallRecord, no DataResult, no render, no success.
    assert node_run.tool_call_records == ()
    assert node_run.data_result_refs == ()
    names = {r.schema_ref.name for r in node_run.execution_evidence_refs}
    assert "OHLCVDataResult" not in names and "RenderedDataBlock" not in names
    assert "PromptAssemblyRecord" not in names  # failed before any model call

    # the already-committed main request ref IS retained as failed-run evidence
    # (journal recovery: payload + BridgeEvidenceRecorded control + RunEvent).
    assert "DataRequest" in names
    req_ref = next(r for r in node_run.execution_evidence_refs
                   if r.schema_ref.name == "DataRequest")
    request = env.stores.payloads.get(req_ref.payload_ref,
                                      expected_schema_ref=req_ref.schema_ref)
    assert request.method_spec_ref.id == "ohlcv"


# =========================================================================== #
# 10. data reads are not retroactively inserted into the frozen snapshot        #
# =========================================================================== #
def test_node_data_read_never_mutates_the_frozen_input_snapshot():
    env = build_data_env()
    frozen_digest = env.input_snapshot.content_digest
    assert env.input_snapshot.data_result_refs == ()
    node_run, artifact = env.run()
    assert node_run.status is NodeStatus.COMPLETED
    # the read landed in NodeRun evidence, NOT in the pre-node snapshot.
    assert len(node_run.data_result_refs) == 1
    assert env.input_snapshot.data_result_refs == ()
    assert node_run.input_snapshot_digest == frozen_digest
    assert artifact.provenance.input_snapshot_digest == frozen_digest


# =========================================================================== #
# 11. resolver-constructed bridge; a second bridge shares the ONE sequencer     #
# =========================================================================== #
def test_unregistered_data_provider_factory_fails_before_execution():
    env = build_data_env()
    factories = TrustedFactoryRegistry(env.catalog_runtime)
    env.factories = factories
    env.runtime = W.ExecutionRuntime(
        catalog=env.catalog_runtime, bridge_view=env.view, factories=factories,
        support_report=env.support_report, runtime_registry_digest=env.rt_digest)
    with pytest.raises(W.PreflightError, match="no trusted provider factory"):
        env.prepare()
    assert env.vendors[_PRIMARY].calls == 0


def test_two_bridges_share_one_sequencer_with_canonical_order():
    env = build_data_env(generic_bridge=True)
    node_run, artifact = env.run()
    assert node_run.status is NodeStatus.COMPLETED
    # two finalized records: the generic bridge's + the data bridge's, in strictly
    # increasing call ordinal issued by the ONE shared node sequencer.
    assert len(node_run.tool_call_records) == 2
    ordinals = [r.call_ordinal for r in node_run.tool_call_records]
    assert ordinals == sorted(ordinals) and len(set(ordinals)) == 2
    tool_ids = {r.tool_ref.id for r in node_run.tool_call_records}
    assert tool_ids == {"cap.tool", "cap.data.ohlcv"}
    # both bridges' refs merged canonically; provenance equals NodeRun exactly.
    prov = artifact.provenance
    assert prov.tool_call_records == node_run.tool_call_records
    assert prov.data_result_refs == node_run.data_result_refs


def test_fake_second_bridge_cannot_satisfy_the_data_summary():
    # the data row REQUIRES a finalized call (always_invoke + required); both
    # vendors are down, so the data summary's minimum is unmet even though the
    # generic bridge finalized ITS OWN call.
    reg = build_data_source_registry()
    spec = reg.method_spec("indicators")
    route = reg.default_route("indicators")
    rows = (DataPrefetchOperation(
        worker_id=WORKER_ID, method_ref=spec.method_ref, capability_ref=spec.capability_ref,
        frozen_route=route.entries, invocation_mode="always_invoke",
        success_requires_finalized_call=True,
        param_bindings=(
            ParamBinding(target_pointer="/end", source_kind="node_param", source_pointer="/end"),
            ParamBinding(target_pointer="/start", source_kind="node_param", source_pointer="/start"),
            ParamBinding(target_pointer="/symbol", source_kind="node_param", source_pointer="/symbol"),
        )),)
    vendors = {
        _PRIMARY: ScriptedVendor(route.entries[0].source_ref,
                                 raise_exc=RateLimitError("down")),
        _BACKUP: ScriptedVendor(route.entries[1].source_ref,
                                raise_exc=NotConfiguredError("no key")),
    }
    env = build_data_env(binding_rows=rows, vendors=vendors, generic_bridge=True)
    node_run, artifact = env.run()
    assert artifact is None
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "tool_discipline_unmet"
    assert DATA_BRIDGE_ID in (node_run.reason or "")
    # the optional method's honest UNAVAILABLE result was still persisted + kept.
    ind_sr = SchemaRef(name="IndicatorDataResult", version="1")
    assert any(r.schema_ref == ind_sr for r in node_run.data_result_refs)
    result_ref = next(r for r in node_run.data_result_refs if r.schema_ref == ind_sr)
    result = env.stores.payloads.get(result_ref.payload_ref, expected_schema_ref=ind_sr)
    assert result.status is DataStatus.UNAVAILABLE
    # the generic bridge DID finalize its own record — it cannot stand in for data.
    assert any(r.tool_ref.id == "cap.tool" for r in node_run.tool_call_records)
    assert not any(r.tool_ref.id.startswith("cap.data.")
                   for r in node_run.tool_call_records)


# =========================================================================== #
# 12. untrusted block DTO; malicious row text stays inert                       #
# =========================================================================== #
def test_rendered_block_is_untrusted_dto_and_prompt_record_is_single():
    env = build_data_env()
    node_run, artifact = env.run()
    prompt_refs = [r for r in node_run.execution_evidence_refs
                   if r.schema_ref == PROMPT_SR]
    assert len(prompt_refs) == 1
    record = env.stores.payloads.get(prompt_refs[0].payload_ref,
                                     expected_schema_ref=PROMPT_SR)
    # exactly one ordered untrusted block, referencing the persisted RenderedDataBlock.
    assert len(record.untrusted_blocks) == 1
    block_ref = record.untrusted_blocks[0]
    assert block_ref.ordinal == 1
    assert block_ref.payload_ref.schema_ref == RENDERED_BLOCK_SR
    block = env.stores.payloads.get(block_ref.payload_ref.payload_ref,
                                    expected_schema_ref=RENDERED_BLOCK_SR)
    assert block.trust == "untrusted_data"
    assert block.result_ref == node_run.data_result_refs[0]
    # row text is embedded ONLY as length-delimited canonical JSON inside the block.
    assert '"payload_canonical_json"' in block.rendered_text
    # the system prompt / guardrail identities come only from the catalog.
    assert record.system_prompt_ref == env.worker.system_prompt_ref
    assert record.guardrail_refs == ()
    # the block ref never leaks into direct execution evidence (it is transitively
    # ordered inside the prompt record).
    assert not any(r.schema_ref == RENDERED_BLOCK_SR
                   for r in node_run.execution_evidence_refs)


# =========================================================================== #
# 13. deterministic execution: typed refs only, no render, no prompt record     #
# =========================================================================== #
def test_deterministic_node_gets_typed_refs_but_no_block_or_prompt():
    env = build_data_env(deterministic=True)
    node_run, artifact = env.run()
    assert node_run.status is NodeStatus.COMPLETED
    assert len(node_run.data_result_refs) == 1
    assert len(node_run.tool_call_records) == 1
    names = {r.schema_ref.name for r in node_run.execution_evidence_refs}
    assert "PromptAssemblyRecord" not in names
    assert "RenderedDataBlock" not in names
    assert artifact.provenance.prompt_ref is None
    # the request evidence is represented by the finalized record — never
    # double-listed as direct evidence.
    assert node_run.tool_call_records[0].request_ref.schema_ref.name == "DataRequest"
    assert names == set()


# =========================================================================== #
# 14. journal recovery; a raw provider PayloadStore write is impossible         #
# =========================================================================== #
def test_provider_reader_has_no_write_surface():
    env = build_data_env()
    env.build_runtime()
    reader = W._ReadOnlyPayloadView(env.stores.payloads)
    assert not hasattr(reader, "put")
    assert hasattr(reader, "get")


def test_failed_run_recovers_all_committed_refs_from_the_journal():
    # (the future-data test asserts the failed path retains the committed request
    # ref; here we additionally check the control-fact journal drains it exactly
    # once — no duplicate classification.)
    reg = build_data_source_registry()
    route = reg.default_route("ohlcv")
    future_cand = _good_candidate(available_at=datetime(2026, 7, 18, tzinfo=UTC))
    vendors = {
        _PRIMARY: ScriptedVendor(route.entries[0].source_ref, candidates=(future_cand,)),
        _BACKUP: ScriptedVendor(route.entries[1].source_ref),
    }
    env = build_data_env(vendors=vendors)
    node_run, _ = env.run()
    req_refs = [r for r in node_run.execution_evidence_refs
                if r.schema_ref.name == "DataRequest"]
    assert len(req_refs) == 1  # journal-drained once, never double-listed
    # and it does not ALSO appear in data_result_refs.
    assert not any(typed_ref_sort_key(r) == typed_ref_sort_key(req_refs[0])
                   for r in node_run.data_result_refs)


# =========================================================================== #
# 16b. successful terminal below the data summary minimum fails                 #
# =========================================================================== #
def test_min_finalized_calls_enforced_per_data_summary():
    # covered end-to-end by test_fake_second_bridge_cannot_satisfy_the_data_summary;
    # here the single-bridge flavor: below-minimum ⇒ INCOMPLETE even with a valid
    # honest UNAVAILABLE result.
    reg = build_data_source_registry()
    spec = reg.method_spec("indicators")
    route = reg.default_route("indicators")
    rows = (DataPrefetchOperation(
        worker_id=WORKER_ID, method_ref=spec.method_ref, capability_ref=spec.capability_ref,
        frozen_route=route.entries, invocation_mode="always_invoke",
        success_requires_finalized_call=True,
        param_bindings=(
            ParamBinding(target_pointer="/end", source_kind="node_param", source_pointer="/end"),
            ParamBinding(target_pointer="/start", source_kind="node_param", source_pointer="/start"),
            ParamBinding(target_pointer="/symbol", source_kind="node_param", source_pointer="/symbol"),
        )),)
    vendors = {
        _PRIMARY: ScriptedVendor(route.entries[0].source_ref, raise_exc=RateLimitError("x")),
        _BACKUP: ScriptedVendor(route.entries[1].source_ref, raise_exc=RateLimitError("y")),
    }
    env = build_data_env(binding_rows=rows, vendors=vendors)
    node_run, artifact = env.run()
    assert artifact is None
    assert node_run.status is NodeStatus.INCOMPLETE
    assert node_run.reason_code == "tool_discipline_unmet"
    # every begun invocation reached exactly one terminal (two rejects, no record).
    reasons = [r.reason_code for r in env.refusal_sink.records()]
    assert reasons.count("rate_limited") == 2
    assert node_run.tool_call_records == ()
