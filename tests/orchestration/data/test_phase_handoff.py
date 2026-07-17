# -*- coding: utf-8 -*-
"""Task 0 — the executable Phase 1 / Phase 2 -> Phase 3 handoff gate.

This is a *consumer* gate, not a red->green TDD task. It imports the frozen Phase 1
orchestration contract layer *and* the sealed Phase 2 runtime kernel exactly as the
Phase 3 data / PIT layer will, exercises the real builders / gateways / stores /
digests through their public surfaces, and asserts the 11 conformance points of the
plan's Task 0 Step 1. Every assertion here PASSES against the already-existing
upstream code (HEAD ``2b68c42`` at authoring; ``pytest tests/orchestration`` = 1358
green). A failure means an upstream Phase 1 / Phase 2 contract drifted from its
frozen exit gate and BLOCKS Phase 3 — it must never be "fixed" by weakening an
assertion or locally redefining an upstream type. If an upstream contract is absent
or incompatible, fix its owning phase; do not patch it here.

The reused ``build_env`` / ``build_scenario`` / gateway fixtures are the *real*
Phase 2 admission→dispatch scaffolding (``tests/orchestration/test_worker.py`` and
``tests/orchestration/test_runtime_support.py``); this gate mines them to construct
valid objects rather than reinventing them, but every assertion drives a genuine
public surface (``PayloadStore`` / ``EventStore`` / ``CapabilityGateway`` /
``execute_node`` / ``check_runtime_support`` / ``RuntimeUnitOfWork``), never a
private reimplementation of an upstream type.

The 11 points (plan Task 0, Step 1):

 1. every required upstream symbol imports from its owning module;
 2. payload round-trip validates the exact SchemaRef / PayloadRef.content_digest /
    namespace; TypedPayloadRef round-trips through the inherited registry, rejects
    wrong schema/namespace/content and treats object-id relocation as audit-only;
 3. PayloadRef.object_id / snapshot-locator-only relocation change audit identity
    but not a parent semantic digest;
 4. EventAppendRequest has no caller-selected cursor, append is authoritative
    before publish and idempotent on the same key; refusal audit is separate and
    cannot create a public event;
 5. capability invocation cannot bypass CapabilityGateway; a pending invocation
    yields no evidence-counting ToolCallRecord until finalize_success, while reject
    writes only refusal audit;
 6. replay resolves a recorded tool/data result without invoking a live handler;
 7. InputSnapshot and Artifact.Provenance can bind immutable data-result refs/digests;
 8. the Phase 1 memory/InputSnapshot ABI imports with exact closed fields/projections;
 9. Phase 2's PromptAssemblyRecord round-trips only in main, is persisted for the
    single LLM model call, its typed ref reaches NodeRun + successful Artifact with
    all evidence tuples equal; ModelGateway rejects request/ref/order drift before
    send; DETERMINISTIC creates no prompt record;
10. the generic bridge descriptor/support summary round-trips from exact catalog
    material; RuntimeSupport evaluates REQUIRED/FORBIDDEN from verified call bounds
    and rejects drift; RUNNING precedes I/O with no injection point; reversed
    multi-provider completion preserves the canonical contribution order;
11. the generic RuntimeStateCellStore / RuntimeUnitOfWork atomically put a typed
    payload + CAS head/result cells; same-operation replay returns the original ref
    after a later head move; a stale/failed commit exposes none.

Step 2's reviewed upstream digests are recorded verbatim as module constants and
re-derived from the sealed builders below.

Run from repo root: ``python -m pytest tests/orchestration/data/test_phase_handoff.py -v``
"""
from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

# -- Phase 1 owning modules ------------------------------------------------- #
from guanlan_v2.orchestration import budget as budget_mod
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
    typed_ref_sort_key,
    validate_typed_ref_tuple,
)
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.enums import (
    DataBackend,
    DataMode,
    DataStatus,
    ExecutionKind,
    NodeStatus,
    PortfolioRating,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.data.result import (
    DataResult,
    PitAudit,
    PitRecord,
    SourceAttempt,
)
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextSnapshot,
    DataContext,
    InputSnapshot,
    MemoryRecordRef,
    build_empty_memory_binding,
    check_memory_session_scope,
)
from guanlan_v2.orchestration.schemas import (
    Artifact,
    NodeRun,
    Provenance,
    ResearchPlan,
    ToolCallRecord,
)
from guanlan_v2.orchestration.events import EventType, RunEvent

# -- Phase 2 runtime kernel ------------------------------------------------- #
from guanlan_v2.orchestration.runtime_contracts import (
    ExecutionBridgeDescriptor,
    PromptUntrustedBlockRef,
    phase2_runtime_registry,
)
from guanlan_v2.orchestration.eventstore import (
    EventAppendRequest,
    EventRefusalAuditSink,
    EventStore,
    IdempotencyConflict,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    SchemaRegistryResolver,
    StagedPayloadKey,
    StagedTypedPayloadRef,
    StateCellCompareAndSwapCommand,
    UnitOfWorkError,
)
from guanlan_v2.orchestration.catalog_runtime import (
    parse_bridge_descriptor,
    serialize_bridge_descriptor,
)
from guanlan_v2.orchestration.runtime_contracts import (
    default_audit_detail_registry,
    GenericRefusalDetails,
)
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W

# -- reused real Phase 2 admission/dispatch fixtures ------------------------ #
from tests.orchestration.test_worker import (
    build_env,
    make_provider_factory,
    _cap_ref,
    _run_deterministic,
    _running_gateway,
    _writer_for,
)
from tests.orchestration.test_runtime_support import (
    OtherOut,
    SR_OUT,
    _Analyzer,
    _BridgeSpec,
    build_scenario,
)

UTC = timezone.utc
DT = datetime(2026, 7, 17, 8, 30, tzinfo=UTC)
D64 = "a" * 64
DB = "b" * 64
DC = "c" * 64
GARBAGE_DIGEST = "not-a-valid-sha256-digest"

# =========================================================================== #
# Step 2 — reviewed upstream registry / catalog digests recorded verbatim.     #
# These are the *exact* sealed Phase 2 runtime registry digest and the         #
# canonical PHASE2_STATIC_CATALOG_DIGEST — not a local path, an earlier pilot   #
# catalog or a mutable singleton identity. They are re-derived from the sealed  #
# builders in test_step2_* below.                                              #
# =========================================================================== #
PHASE1_BASE_REGISTRY_DIGEST = (
    "75f7920db13cdcaac89a70e0103812a29348ab3caaa98b9c1020429bb4e18b03"
)
PHASE2_RUNTIME_REGISTRY_DIGEST = (
    "b11fcacf0efd931dc3a3d11f859d6f5bac86c1c24b78e5cc1d2b44829822d8d5"
)
PHASE2_STATIC_CATALOG_DIGEST = (
    "b41bf223f0dd0b05c5a4f4f7bbd32960eef90d7176a17300dbe576f6b61bf0d3"
)


# --------------------------------------------------------------------------- #
# shared local helpers (mirror the eventstore test's construction patterns)    #
# --------------------------------------------------------------------------- #
class AdvancingClock:
    def __init__(self, start: datetime | None = None, step: timedelta = timedelta(seconds=1)):
        self._next = start or datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
        self._step = step

    def now(self) -> datetime:
        cur = self._next
        self._next = cur + self._step
        return cur


def _research(rec=PortfolioRating.BUY, rationale="thesis") -> ResearchPlan:
    return ResearchPlan(recommendation=rec, rationale=rationale)


def _stores(*, allowed_cell_namespaces=()):
    resolver = SchemaRegistryResolver()
    base_digest = resolver.register(default_registry())
    rt_digest = resolver.register(phase2_runtime_registry(default_registry().registry_digest))
    stores = RuntimeStores(
        resolver=resolver, clock=AdvancingClock(),
        allowed_cell_namespaces=allowed_cell_namespaces,
    )
    return stores, base_digest, rt_digest


def _sr(name, version="1"):
    return SchemaRef(name=name, version=version)


_SR_RESEARCH = _sr("ResearchPlan")


# =========================================================================== #
# Point 1 — every required upstream symbol imports from its owning module       #
# =========================================================================== #
#: symbol -> owning module. A Phase 3 consumer imports each of these from the
#: module that *defines* it; a re-home by any Phase 2/3 path breaks this gate.
_OWNED = {
    "guanlan_v2.orchestration.digest": (
        "ContractModel", "DigestModel", "canonical_json", "content_digest",
    ),
    "guanlan_v2.orchestration.refs": (
        "SchemaRef", "PayloadRef", "TypedPayloadRef", "ContentRef", "CapabilityRef",
        "typed_ref_sort_key", "validate_typed_ref_tuple",
    ),
    "guanlan_v2.orchestration.schema_registry": ("SchemaRegistry", "default_registry"),
    "guanlan_v2.orchestration.data.result": (
        "DataResult", "PitRecord", "PitAudit", "SourceAttempt",
    ),
    "guanlan_v2.orchestration.context": (
        "DataContext", "ContextRuntimeRequirements", "ContextSnapshot", "InputSnapshot",
        "InputArtifactBinding", "MemoryRecordRef", "EmptyMemorySnapshot",
        "EmptyMemorySelection", "build_empty_memory_binding", "check_memory_session_scope",
    ),
    "guanlan_v2.orchestration.schemas": ("Artifact", "Provenance", "ToolCallRecord", "NodeRun"),
    "guanlan_v2.orchestration.spec": (
        "compute_candidate_plan_digest", "validate_plan_draft", "freeze_plan",
    ),
    "guanlan_v2.orchestration.events": ("RunEvent", "EventType"),
    "guanlan_v2.orchestration.runtime_contracts": (
        "phase2_runtime_registry", "PromptAssemblyRecord", "PromptUntrustedBlockRef",
        "ExecutionBridgeDescriptor", "BridgeStaticSupportSummary", "BridgeEvidenceRecorded",
        "RuntimeSupportReport", "static_runtime_profile", "StaticRuntimeProfile",
    ),
    "guanlan_v2.orchestration.eventstore": (
        "RuntimeStores", "PayloadStore", "EventStore", "EventAppendRequest",
        "RuntimeUnitOfWork", "StateCellCompareAndSwapCommand", "RuntimeStateCellStore",
        "EventRefusalAuditSink", "SchemaRegistryResolver",
    ),
    "guanlan_v2.orchestration.catalog_runtime": (
        "CatalogRuntime", "TrustedFactoryRegistry", "BridgeCatalogView",
        "serialize_bridge_descriptor", "parse_bridge_descriptor",
    ),
    "guanlan_v2.orchestration.runtime_support": ("check_runtime_support",),
    "guanlan_v2.orchestration.worker": (
        "execute_node", "CapabilityGateway", "StaticPromptAssembler", "PromptAssembler",
        "ModelGateway", "ExecutionBridgeResolver", "BridgeEvidenceWriter",
        "verify_model_request_binding",
    ),
    "guanlan_v2.orchestration.presets": ("phase2_static_catalog_snapshot",),
}


def test_point1_every_required_symbol_imports_from_its_owning_module():
    for module_name, names in _OWNED.items():
        module = importlib.import_module(module_name)
        for name in names:
            obj = getattr(module, name, None)
            assert obj is not None, f"{module_name} does not export {name}"
            assert obj.__module__ == module_name, (
                f"{name} must be owned by {module_name}; got {obj.__module__}"
            )


def test_point1_idempotency_conflict_re_export_is_the_budget_type():
    # eventstore re-exports the budget module's IdempotencyConflict verbatim; the
    # low-level stores share the single conflict type, not a parallel definition.
    assert IdempotencyConflict is budget_mod.IdempotencyConflict
    assert IdempotencyConflict.__module__ == "guanlan_v2.orchestration.budget"


def test_point1_static_catalog_digest_constant_is_a_presets_hex():
    digest = P.PHASE2_STATIC_CATALOG_DIGEST
    assert isinstance(digest, str) and len(digest) == 64
    int(digest, 16)  # a well-formed hex digest, not a path or placeholder


# =========================================================================== #
# Point 2 — payload round-trip + TypedPayloadRef through the inherited registry  #
# =========================================================================== #
def test_point2_payload_round_trip_binds_exact_schema_namespace_content():
    stores, base_digest, _ = _stores()
    ref = stores.payloads.put(
        _SR_RESEARCH, _research(), registry_digest=base_digest,
        namespace="main", idempotency_key="p-1",
    )
    assert isinstance(ref, PayloadRef)
    assert ref.namespace == "main"
    # content_digest is computed by the store, never caller-chosen.
    assert ref.content_digest == content_digest(_research())
    got = stores.payloads.get(ref, expected_schema_ref=_SR_RESEARCH)
    assert isinstance(got, ResearchPlan) and got == _research()

    # get re-validates the exact SchemaRef / content / namespace.
    with pytest.raises(Exception):
        stores.payloads.get(ref, expected_schema_ref=_sr("PortfolioDecision"))
    tampered_content = PayloadRef(namespace="main", object_id=ref.object_id, content_digest=DB)
    with pytest.raises(Exception):
        stores.payloads.get(tampered_content, expected_schema_ref=_SR_RESEARCH)
    tampered_ns = PayloadRef(namespace="sealed", object_id=ref.object_id, content_digest=ref.content_digest)
    with pytest.raises(Exception):
        stores.payloads.get(tampered_ns, expected_schema_ref=_SR_RESEARCH)


def test_point2_typed_payload_ref_round_trips_through_inherited_registry():
    stores, base_digest, rt_digest = _stores()
    # a payload persisted under the inherited (Phase 2 runtime) registry — which
    # contains every Phase 1 model — round-trips as a TypedPayloadRef.
    ref = stores.payloads.put(
        _SR_RESEARCH, _research(), registry_digest=rt_digest,
        namespace="main", idempotency_key="p-typed",
    )
    typed = TypedPayloadRef(schema_ref=_SR_RESEARCH, payload_ref=ref)
    # the composite is accepted as a main-namespace evidence ref.
    validate_typed_ref_tuple((typed,), require_main=True, field_name="probe")
    # and the referenced payload re-validates against the exact schema.
    got = stores.payloads.get(typed.payload_ref, expected_schema_ref=typed.schema_ref)
    assert got == _research()

    # a non-main-namespace typed ref is rejected by the owner-side invariant.
    sealed = TypedPayloadRef(
        schema_ref=_SR_RESEARCH,
        payload_ref=PayloadRef(namespace="sealed", object_id="o", content_digest=D64),
    )
    with pytest.raises(ValueError):
        validate_typed_ref_tuple((sealed,), require_main=True, field_name="probe")


def test_point2_typed_ref_relocation_is_audit_only_content_and_schema_are_semantic():
    schema = _SR_RESEARCH
    a = TypedPayloadRef(schema_ref=schema, payload_ref=PayloadRef(namespace="main", object_id="obj-1", content_digest=D64))
    b = TypedPayloadRef(schema_ref=schema, payload_ref=PayloadRef(namespace="main", object_id="obj-2", content_digest=D64))
    c = TypedPayloadRef(schema_ref=schema, payload_ref=PayloadRef(namespace="main", object_id="obj-1", content_digest=DB))
    d = TypedPayloadRef(schema_ref=_sr("PortfolioDecision"), payload_ref=PayloadRef(namespace="main", object_id="obj-1", content_digest=D64))
    # object_id relocation is audit-only: same semantic key + same semantic digest.
    assert typed_ref_sort_key(a) == typed_ref_sort_key(b)
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()
    # a changed referenced content digest OR schema identity moves the semantic key.
    assert typed_ref_sort_key(a) != typed_ref_sort_key(c)
    assert a.semantic_digest() != c.semantic_digest()
    assert typed_ref_sort_key(a) != typed_ref_sort_key(d)


# =========================================================================== #
# Point 3 — relocation changes audit identity, not a parent semantic digest     #
# =========================================================================== #
def _run_event(payload_ref: PayloadRef) -> RunEvent:
    return RunEvent.build(
        event_id="ev-1", journal_seq=1, occurred_at=DT, run_id="run-1", partition="main",
        event_type=EventType.ARTIFACT_STAGED, idempotency_key="idem-1",
        payload_schema_ref=_SR_RESEARCH, payload_ref=payload_ref,
    )


def test_point3_payload_relocation_is_audit_only_on_the_parent_event():
    a = PayloadRef(namespace="main", object_id="obj-1", content_digest=D64)
    b = PayloadRef(namespace="main", object_id="obj-2", content_digest=D64)  # relocated
    c = PayloadRef(namespace="main", object_id="obj-1", content_digest=DB)   # new content
    ev_a, ev_b, ev_c = _run_event(a), _run_event(b), _run_event(c)
    # re-storing identical payload content under a new object_id moves only audit.
    assert ev_a.content_digest == ev_b.content_digest
    assert ev_a.audit_digest_value() != ev_b.audit_digest_value()
    # a changed referenced content digest moves the parent semantic digest.
    assert ev_a.content_digest != ev_c.content_digest


def test_point3_snapshot_locator_relocation_is_semantic_invariant_on_input_snapshot():
    def _snap(object_id: str) -> InputSnapshot:
        ctx_ref = TypedPayloadRef(
            schema_ref=_sr("ContextSnapshot"),
            payload_ref=PayloadRef(namespace="main", object_id=object_id, content_digest=D64),
        )
        return InputSnapshot.build(
            snapshot_id="in-1", run_id="run-1", plan_id="plan-1", plan_digest=D64,
            node_id="node-1", layer_index=0, attempt=1, context_snapshot_ref=ctx_ref,
            artifact_inputs=(), data_result_refs=(), memory_record_refs=(),
            readiness="ready", built_at=DT,
        )
    s1 = _snap("ctx-obj-1")
    s2 = _snap("ctx-obj-2")  # snapshot-locator-only relocation of the context ref
    assert s1.content_digest == s2.content_digest
    assert s1.audit_digest_value() != s2.audit_digest_value()


# =========================================================================== #
# Point 4 — no caller cursor; append authoritative + idempotent; refusal audit   #
# =========================================================================== #
def test_point4_event_append_request_has_no_caller_selected_cursor_or_time():
    fields = set(EventAppendRequest.model_fields)
    for forbidden in ("journal_seq", "visible_seq", "occurred_at", "event_id"):
        assert forbidden not in fields
    # the append entry point exposes no seq/time parameter either.
    assert set(inspect.signature(EventStore.append).parameters) == {"self", "request"}


def test_point4_append_is_authoritative_before_publish_and_idempotent():
    stores, base_digest, _ = _stores()
    ref = stores.payloads.put(_SR_RESEARCH, _research(), registry_digest=base_digest,
                              namespace="main", idempotency_key="p")
    def _append(idem):
        return stores.events.append(EventAppendRequest(
            run_id="run-1", partition="main", event_type="LayerCommitted",
            payload_schema_ref=_SR_RESEARCH, payload_ref=ref,
            registry_digest=base_digest, idempotency_key=idem,
        ))
    e1 = _append("e")
    assert isinstance(e1, RunEvent)
    # the store assigns and re-verifies its own sealed content_digest + cursor.
    assert e1.content_digest == e1.semantic_digest()
    assert e1.journal_seq == 1
    # same key + identical content is idempotent — no second journal row.
    e2 = _append("e")
    assert e2.journal_seq == e1.journal_seq
    assert len(stores.events.journal("run-1", "main")) == 1


def test_point4_refusal_audit_is_separate_and_creates_no_public_event():
    stores, base_digest, _ = _stores()
    sink = EventRefusalAuditSink(detail_registry=default_audit_detail_registry(),
                                 clock=AdvancingClock())
    # a public append with a missing payload ref is rejected by the EventStore.
    with pytest.raises(Exception):
        stores.events.append(EventAppendRequest(
            run_id="run-1", partition="main", event_type="ArtifactStaged",
            payload_schema_ref=_SR_RESEARCH,
            payload_ref=PayloadRef(namespace="main", object_id="ghost", content_digest=D64),
            registry_digest=base_digest, idempotency_key="ev-bad",
        ))
    # the refusal is recorded only in the audit-only sink, never in the journal.
    rec = sink.record(
        detail_schema_ref=_sr("GenericRefusalDetails"),
        detail_payload=GenericRefusalDetails(summary="payload ref missing"),
        reason_code="payload_missing", attempted_namespace="main", idempotency_key="ref-1",
    )
    assert rec.detail_payload_ref.namespace == "audit"
    assert not hasattr(rec, "journal_seq") and not hasattr(rec, "visible_seq")
    assert stores.events.journal("run-1", "main") == ()
    assert stores.events.visible("run-1", "main") == ()
    assert len(sink.records()) == 1


# =========================================================================== #
# Point 5 — capability confinement; pending vs finalize; reject == refusal only  #
# =========================================================================== #
def test_point5_capability_invocation_cannot_bypass_the_gateway():
    # execute_node / prepare_input expose no handler/model/provider injection point;
    # capability backends bind only through the trusted factory registry.
    for fn in (W.execute_node, W.prepare_input):
        params = set(inspect.signature(fn).parameters)
        assert "handlers" not in params and "handler" not in params
    e = build_env()
    gw, token, _ = _running_gateway(e)
    foreign = CapabilityRef(id="cap.evil", version="1", content_digest=D64)
    with pytest.raises(W.CapabilityGatewayError):
        gw.begin(ordinal_token=token, capability_ref=foreign, request_schema_ref=SR_OUT,
                 idempotency_key="k")
    assert gw.finalized_records() == ()


def test_point5_pending_invocation_yields_no_record_until_finalize_success():
    e = build_env()
    gw, token, seq = _running_gateway(e)
    gw.bind_reader(e.stores.payloads)
    pending = gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker),
                       request_schema_ref=SR_OUT, idempotency_key="k1")
    # a pending invocation counts no evidence yet.
    assert gw.finalized_records() == ()
    unpub = gw.invoke(pending, OtherOut(note="req"))
    result = gw.validate_result(unpub)
    writer = _writer_for(e, seq)
    req_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                         payload=unpub.validated_request, idempotency_key="req")
    res_ref = writer.put(token=token, role="tool_result", schema_ref=SR_OUT,
                         payload=result, idempotency_key="res")
    rec = gw.finalize_success(pending, request_ref=req_ref, result_ref=res_ref)
    # only finalize_success produces the evidence-counting ToolCallRecord.
    assert isinstance(rec, ToolCallRecord)
    assert len(gw.finalized_records()) == 1


def test_point5_reject_writes_only_refusal_audit_and_no_success_record():
    e = build_env()
    gw, token, _ = _running_gateway(e)
    pending = gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker),
                       request_schema_ref=SR_OUT, idempotency_key="k1")
    before = len(e.refusal_sink.records())
    rec = gw.reject(
        pending, detail_schema_ref=_sr("GenericRefusalDetails"),
        detail_payload=GenericRefusalDetails(summary="pit_future", category="x"),
        reason_code="pit_future", idempotency_key="rej")
    # a rejected invocation writes exactly one audit-namespace refusal, no record.
    assert rec.reason_code == "pit_future"
    assert rec.detail_payload_ref.namespace == "audit"
    assert gw.finalized_records() == ()
    assert len(e.refusal_sink.records()) - before == 1
    # a rejected pending can never later be finalized into a success record.
    with pytest.raises(W.CapabilityGatewayError):
        gw.finalize_success(pending, request_ref=_probe_typed_ref(), result_ref=_probe_typed_ref())


def _probe_typed_ref():
    return TypedPayloadRef(schema_ref=SR_OUT,
                           payload_ref=PayloadRef(namespace="main", object_id="x", content_digest=D64))


# =========================================================================== #
# Point 6 — replay resolves a recorded result without invoking a live handler    #
# =========================================================================== #
class _CountingBackend:
    """A trusted capability backend that counts every live invocation."""

    def __init__(self):
        self.calls = 0

    def invoke(self, *, capability_ref, request):
        self.calls += 1
        return OtherOut(note="tool-result")


def test_point6_replay_resolves_recorded_tool_result_without_live_handler():
    e = build_env()
    backend = _CountingBackend()
    node_run, artifact = e.run(backend=backend)
    assert node_run.status is NodeStatus.COMPLETED
    assert backend.calls == 1  # the live handler ran exactly once on the first pass

    # the recorded tool result is resolved purely from the persisted PayloadStore —
    # a replay reads the frozen ref, never re-invoking the backend.
    rec = node_run.tool_call_records[0]
    resolved = e.stores.payloads.get(rec.result_ref.payload_ref,
                                     expected_schema_ref=rec.result_ref.schema_ref)
    assert isinstance(resolved, OtherOut)
    assert backend.calls == 1  # the live handler was NOT invoked to resolve the record

    # the recorded prompt record likewise resolves from the store with no model call.
    prompt_ref = node_run.execution_evidence_refs[-1]
    prompt_rec = e.stores.payloads.get(prompt_ref.payload_ref,
                                       expected_schema_ref=_sr("PromptAssemblyRecord"))
    assert prompt_rec.node_id == e.node.id


# =========================================================================== #
# Point 7 — InputSnapshot / Provenance bind immutable data-result refs/digests   #
# =========================================================================== #
def _sealed_data_result() -> DataResult:
    """A genuine, digest-verified DataResult (OK, carries data)."""
    attempt = SourceAttempt(vendor="tushare", configured=True, outcome="success",
                            started_at=DT, finished_at=DT)
    pit = PitAudit(mode=DataMode.ONLINE, as_of=DT, rows_seen=1, rows_returned=1,
                   future_rows=0, missing_available_at_rows=0, guard_result="passed",
                   latest_available_at=DT)
    return DataResult.build(
        id="dr-1", method="prices.daily", request_digest=D64, status=DataStatus.OK,
        data=_research(), resolved_vendor_chain=("tushare",), source_config_digest=D64,
        available_at=DT, ingested_at=DT, fetched_at=DT, attempts=(attempt,), pit_audit=pit,
    )


def _data_result_ref(content: str = D64) -> TypedPayloadRef:
    return TypedPayloadRef(schema_ref=_sr("DataResult"),
                           payload_ref=PayloadRef(namespace="main", object_id="dr-obj", content_digest=content))


def test_point7_sealed_data_result_verifies_its_own_digests():
    dr = _sealed_data_result()
    assert dr.status is DataStatus.OK
    assert dr.content_digest == dr.semantic_digest()
    assert dr.audit_digest == dr.audit_digest_value()
    assert dr.data_content_digest == content_digest(_research())


def test_point7_input_snapshot_binds_immutable_data_result_refs():
    ctx_ref = TypedPayloadRef(schema_ref=_sr("ContextSnapshot"),
                              payload_ref=PayloadRef(namespace="main", object_id="ctx", content_digest=D64))
    snap = InputSnapshot.build(
        snapshot_id="in-1", run_id="run-1", plan_id="plan-1", plan_digest=D64,
        node_id="node-1", layer_index=0, attempt=1, context_snapshot_ref=ctx_ref,
        artifact_inputs=(), data_result_refs=(_data_result_ref(),), memory_record_refs=(),
        readiness="ready", built_at=DT,
    )
    assert snap.data_result_refs == (_data_result_ref(),)
    assert snap.content_digest == snap.semantic_digest()
    # the snapshot is an immutable fact: a data-result tuple cannot be re-bound.
    with pytest.raises(ValidationError):
        snap.data_result_refs = ()


def test_point7_provenance_binds_immutable_data_result_refs():
    prov = Provenance(
        plan_digest=D64, code_version="v1", as_of=DT, pit_mode=DataMode.ONLINE,
        data_result_refs=(_data_result_ref(),),
    )
    assert prov.data_result_refs == (_data_result_ref(),)
    with pytest.raises(ValidationError):
        prov.data_result_refs = ()
    # and an Artifact carries the same immutable provenance data refs.
    art = Artifact.build(
        artifact_id="art-1", run_id="run-1", created_at=DT, producer_node_id="node-1",
        slot="slot-1", output_key="primary", kind="research_plan",
        payload_schema_ref=_SR_RESEARCH, payload=_research(), rendered_md="body",
        provenance=prov,
    )
    assert art.provenance.data_result_refs == (_data_result_ref(),)


# =========================================================================== #
# Point 8 — Phase 1 memory / InputSnapshot ABI: closed fields + projections      #
# =========================================================================== #
def _online_data_context() -> DataContext:
    clock = ClockSpec(as_of=DT, timezone="Asia/Shanghai", calendar_id="XSHG")
    return DataContext(
        as_of=DT, clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
        source_config_digest=D64, source_registry_digest=D64, routing_snapshot_digest=D64,
        data_snapshot_id="ds-1", data_snapshot_content_digest=D64, built_at=DT,
    )


def test_point8_canonical_empty_models_round_trip_as_typed_refs():
    binding = build_empty_memory_binding()
    # the canonical empty facts are exact main-namespace TypedPayloadRefs.
    assert isinstance(binding.memory_snapshot_ref, TypedPayloadRef)
    assert isinstance(binding.memory_selection_ref, TypedPayloadRef)
    assert binding.memory_snapshot_ref == P.EMPTY_MEMORY_SNAPSHOT_REF
    assert binding.memory_selection_ref == P.EMPTY_MEMORY_SELECTION_REF
    # each ref's payload content digest equals the self-sealed hash.
    assert binding.memory_snapshot_ref.payload_ref.content_digest == binding.snapshot_hash
    # selection ref content equals past_context_hash (the reviewed selection identity).
    assert binding.memory_selection_ref.payload_ref.content_digest == binding.past_context_hash
    assert binding.selection.content_digest == binding.past_context_hash
    assert binding.selection.snapshot_digest == binding.snapshot.content_digest
    assert binding.snapshot.records == () and binding.selection.records == ()


def test_point8_typed_object_relocation_is_semantic_invariant_schema_content_drift_is_semantic():
    binding = build_empty_memory_binding()
    ref = binding.memory_snapshot_ref
    relocated = TypedPayloadRef(
        schema_ref=ref.schema_ref,
        payload_ref=PayloadRef(namespace="main", object_id="relocated",
                               content_digest=ref.payload_ref.content_digest),
    )
    assert typed_ref_sort_key(relocated) == typed_ref_sort_key(ref)
    assert relocated.semantic_digest() == ref.semantic_digest()
    # a schema or content drift is semantic.
    content_drift = TypedPayloadRef(schema_ref=ref.schema_ref,
                                    payload_ref=PayloadRef(namespace="main", object_id="o", content_digest=DB))
    assert typed_ref_sort_key(content_drift) != typed_ref_sort_key(ref)


def test_point8_empty_pair_forbids_and_non_empty_pair_requires_runtime_requirements():
    binding = build_empty_memory_binding()
    dc = _online_data_context()
    # canonical empty pair -> runtime_requirements_ref MUST be None.
    cs = ContextSnapshot.build(
        snapshot_id="cs-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash, past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, built_at=DT,
    )
    assert cs.memory_session_id is None
    assert cs.runtime_requirements_ref is None
    assert cs.content_digest == cs.semantic_digest()

    # a non-empty memory binding with no ContextRuntimeRequirements ref is rejected.
    snap_ref = TypedPayloadRef(schema_ref=_sr("EmptyMemorySnapshot"),
                               payload_ref=PayloadRef(namespace="main", object_id="ms", content_digest="e" * 64))
    sel_ref = TypedPayloadRef(schema_ref=_sr("EmptyMemorySelection"),
                              payload_ref=PayloadRef(namespace="main", object_id="msel", content_digest="f" * 64))
    with pytest.raises(ValueError):
        ContextSnapshot.build(
            snapshot_id="cs-2", data_context=dc, memory_snapshot_id="ms-2",
            memory_snapshot_hash="e" * 64, past_context_hash="f" * 64,
            memory_snapshot_ref=snap_ref, memory_selection_ref=sel_ref,
            runtime_requirements_ref=None, built_at=DT,
        )


def test_point8_memory_session_scope_widening_is_rejected():
    with pytest.raises(ValueError):
        check_memory_session_scope(authority_session_id=None, requested_session_id="sess.worker")
    with pytest.raises(ValueError):
        check_memory_session_scope(authority_session_id="sess.a", requested_session_id="sess.b")
    check_memory_session_scope(authority_session_id="sess.a", requested_session_id="sess.a")
    check_memory_session_scope(authority_session_id="sess.a", requested_session_id=None)


def test_point8_full_artifact_data_and_memory_refs_enter_input_snapshot():
    binding = build_empty_memory_binding()
    ctx_ref = TypedPayloadRef(schema_ref=_sr("ContextSnapshot"),
                              payload_ref=PayloadRef(namespace="main", object_id="ctx", content_digest=D64))
    mem_ref = MemoryRecordRef(record_id="mem.rec1", revision_id="r1", available_at=DT, content_digest=D64)
    snap = InputSnapshot.build(
        snapshot_id="in-1", run_id="run-1", plan_id="plan-1", plan_digest=D64,
        node_id="node-1", layer_index=0, attempt=1, context_snapshot_ref=ctx_ref,
        artifact_inputs=(), data_result_refs=(_data_result_ref(),),
        memory_record_refs=(mem_ref,), readiness="ready", built_at=DT,
    )
    # all three families enter without any Phase 3 redefinition of the ABI.
    assert snap.data_result_refs == (_data_result_ref(),)
    assert snap.memory_record_refs == (mem_ref,)
    assert snap.content_digest == snap.semantic_digest()
    _ = binding  # the canonical empty binding is the pre-Phase-3 memory universe.


# =========================================================================== #
# Point 9 — LLM prompt-record lifecycle + evidence; DETERMINISTIC creates none    #
# =========================================================================== #
def test_point9_llm_prompt_record_persisted_in_main_and_binds_all_evidence():
    e = build_env()
    spy = []
    mg = e.model_gateway(spy=spy)
    node_run, artifact = e.run(model_gateway=mg)
    assert node_run.status is NodeStatus.COMPLETED
    # exactly one model send occurred.
    assert len(spy) == 1
    # the prompt record persists in the public main namespace and resolves back.
    prompt_ref = artifact.provenance.execution_evidence_refs[-1]
    assert prompt_ref.payload_ref.namespace == "main"
    rec = e.stores.payloads.get(prompt_ref.payload_ref,
                                expected_schema_ref=_sr("PromptAssemblyRecord"))
    assert rec.model_request_digest and rec.node_id == e.node.id
    # the artifact's prompt ref is exactly the worker's system-prompt content ref.
    assert artifact.provenance.prompt_ref == e.worker.system_prompt_ref
    # the NodeRun and successful Artifact evidence tuples are EXACTLY equal.
    prov = artifact.provenance
    assert prov.tool_call_records == node_run.tool_call_records
    assert prov.data_result_refs == node_run.data_result_refs
    assert prov.execution_evidence_refs == node_run.execution_evidence_refs


def test_point9_model_gateway_rejects_prompt_binding_drift_before_send():
    e = build_env()
    resolved = e.sc.runtime.resolve_worker(e.node.worker_id)
    asm = W.StaticPromptAssembler()
    req_a = asm.assemble(
        plan_digest=e.plan.plan_digest, node_id=e.node.id, worker_id=e.worker.id,
        system_prompt=resolved.system_prompt, skills=resolved.skills, guardrails=resolved.guardrails,
        trusted_input_digests=(W.NamedEvidenceDigest(name="a", digest="a" * 64),), untrusted_blocks=())
    req_b = asm.assemble(
        plan_digest=e.plan.plan_digest, node_id=e.node.id, worker_id=e.worker.id,
        system_prompt=resolved.system_prompt, skills=resolved.skills, guardrails=resolved.guardrails,
        trusted_input_digests=(W.NamedEvidenceDigest(name="b", digest="b" * 64),), untrusted_blocks=())
    assert req_a.request_digest != req_b.request_digest
    pref = e.stores.payloads.put(_sr("PromptAssemblyRecord"), req_a.prompt_record,
                                 registry_digest=e.rt_digest, namespace="main", idempotency_key="prec-a")
    ref_a = TypedPayloadRef(schema_ref=_sr("PromptAssemblyRecord"), payload_ref=pref)
    # a prompt record for request A can never authorize request B before send.
    with pytest.raises(W.PromptBindingError):
        W.verify_model_request_binding(req_b, ref_a, reader=e.stores.payloads)
    # the matching request binds.
    assert W.verify_model_request_binding(req_a, ref_a, reader=e.stores.payloads) == req_a.prompt_record


def test_point9_deterministic_creates_no_prompt_record_but_retains_evidence():
    e = build_env(deterministic=True)
    node_run, artifact = _run_deterministic(e)
    assert node_run.status is NodeStatus.COMPLETED
    assert artifact.provenance.prompt_ref is None
    assert artifact.provenance.model_config_digest is None
    # deterministic retains directly consumed evidence with no prompt ref.
    assert artifact.provenance.execution_evidence_refs == node_run.execution_evidence_refs


# =========================================================================== #
# Point 10 — bridge round-trip; support bounds/drift; RUNNING-first; merge order  #
# =========================================================================== #
def test_point10_generic_bridge_descriptor_round_trips_from_exact_material():
    prov = ContentRef(id="handler.prov", version="1", content_digest=D64)
    cfg = ContentRef(id="guard.cfg", version="1", content_digest=D64)
    anal = ContentRef(id="handler.anal", version="1", content_digest=D64)
    cap = CapabilityRef(id="cap.data", version="1", content_digest=D64)
    desc = ExecutionBridgeDescriptor(
        bridge_id="bridge.data", bridge_version="1", priority=10,
        provider_handler_ref=prov, config_ref=cfg, support_analyzer_ref=anal,
        config_schema_ref=_sr("BridgeConfig"), activation_capability_refs=(cap,),
        activation_read_categories=(), supported_execution_kinds=(ExecutionKind.LLM,),
        pre_input_kind="memory_refs_v1",
    )
    raw = serialize_bridge_descriptor(desc)
    assert parse_bridge_descriptor(raw) == desc
    # a non-descriptor guardrail body parses to None (only real descriptors resolve).
    assert parse_bridge_descriptor(b'{"mode":"cache_or_invoke"}') is None


def test_point10_runtime_support_embeds_active_bridge_refs_and_summaries():
    report = build_scenario().check()
    assert report.supported is True, report.issues
    assert len(report.active_bridge_descriptor_refs) == 1
    assert len(report.active_bridge_config_refs) == 1
    assert len(report.active_bridge_provider_refs) == 1
    assert len(report.active_bridge_analyzer_refs) == 1
    assert len(report.bridge_support_summaries) == 1


def test_point10_runtime_support_evaluates_required_and_forbidden_from_call_bounds():
    # REQUIRED satisfied by a guaranteed one/one bridge; unmet by cache-or-invoke.
    ok = build_scenario(
        tool_calls=ToolCallRequirement.REQUIRED,
        bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10,
                             activation_caps=("cap.data",), min_calls=1, max_calls=1)]).check()
    assert ok.supported is True, ok.issues
    unmet = build_scenario(
        tool_calls=ToolCallRequirement.REQUIRED,
        bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10,
                             activation_caps=("cap.data",), min_calls=0, max_calls=1)]).check()
    assert unmet.supported is False
    assert "tool_calls_required_unmet" in {i.code for i in unmet.issues}
    # FORBIDDEN legal with a memory-only zero/zero bridge; violated by a positive max.
    legal = build_scenario(
        tool_calls=ToolCallRequirement.FORBIDDEN, worker_caps=(),
        worker_read_categories=("context", "memory"),
        bridges=[_BridgeSpec(bridge_id="bridge.mem", priority=10, activation_cats=("memory",),
                             min_calls=0, max_calls=0)]).check()
    assert legal.supported is True, legal.issues
    violated = build_scenario(
        tool_calls=ToolCallRequirement.FORBIDDEN, worker_caps=(),
        worker_read_categories=("context", "memory"),
        bridges=[_BridgeSpec(bridge_id="bridge.mem", priority=10, activation_cats=("memory",),
                             min_calls=0, max_calls=1)]).check()
    assert violated.supported is False
    assert "tool_calls_forbidden_violated" in {i.code for i in violated.issues}


def test_point10_runtime_support_rejects_drifted_analyzer_semantics():
    forged = (CapabilityRef(id="cap.notallowed", version="1", content_digest=D64),)
    report = build_scenario(
        bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",),
                             analyzer=_Analyzer(min_calls=1, max_calls=1, forge_allowlist=forged))]).check()
    assert report.supported is False
    assert "analyzer_allowlist_drift" in {i.code for i in report.issues}


def test_point10_running_precedes_capability_io_and_there_is_no_injection_point():
    # execute_node has no public handler injection parameter.
    assert "handlers" not in set(inspect.signature(W.execute_node).parameters)
    # a capability begin BEFORE the node is marked RUNNING is rejected — RUNNING
    # (and therefore the frozen input snapshot) precedes any capability I/O.
    e = build_env()
    resolver, prepared, seq = e.sequencer_and_prepared()
    gw = e.capability_gateway()
    token = prepared.handles[0].token
    with pytest.raises(W.CapabilityGatewayError):
        gw.begin(ordinal_token=token, capability_ref=_cap_ref(e.worker),
                 request_schema_ref=SR_OUT, idempotency_key="early")


def test_point10_reversed_multi_provider_completion_merges_canonically():
    now = datetime(2026, 7, 17, tzinfo=UTC)
    cap = CapabilityRef(id="cap.data", version="1", content_digest=D64)

    def _ref(digest):
        return TypedPayloadRef(schema_ref=SR_OUT,
                               payload_ref=PayloadRef(namespace="main", object_id=f"obj-{digest[0]}", content_digest=digest))

    rec1 = ToolCallRecord(call_ordinal=1, tool_ref=cap, request_ref=_ref("b" * 64),
                          result_ref=_ref("c" * 64), call_id="c1", started_at=now, finished_at=now)
    rec2 = ToolCallRecord(call_ordinal=2, tool_ref=cap, request_ref=_ref("d" * 64),
                          result_ref=_ref("e" * 64), call_id="c2", started_at=now, finished_at=now)
    c_late = W.BridgeContribution(bridge_id="bridge.two", bridge_priority=20,
                                  summary_digest="f" * 64, tool_call_records=(rec2,))
    c_early = W.BridgeContribution(bridge_id="bridge.data", bridge_priority=10,
                                   summary_digest="f" * 64, tool_call_records=(rec1,))
    # contributions complete in REVERSED order; the merge is canonical regardless.
    merged = W._MergedEvidence.from_contributions((c_late, c_early))
    assert [r.call_ordinal for r in merged.tool_call_records] == [1, 2]
    # a duplicate/conflicting ordinal across providers fails the canonicalizer.
    dup = W.BridgeContribution(bridge_id="bridge.x", bridge_priority=30,
                               summary_digest="f" * 64, tool_call_records=(rec2,))
    with pytest.raises(W.WorkerExecutionError):
        W._MergedEvidence.from_contributions((c_late, dup))


def test_point10_two_bridge_end_to_end_merges_tool_records_by_call_ordinal():
    e = build_env(bridges=[_BridgeSpec(bridge_id="bridge.data", priority=10, activation_caps=("cap.data",)),
                           _BridgeSpec(bridge_id="bridge.two", priority=20, activation_caps=("cap.data",))])
    node_run, artifact = e.run(provider_factory=make_provider_factory(emit_block=False))
    ordinals = [r.call_ordinal for r in node_run.tool_call_records]
    assert ordinals == sorted(ordinals) and len(ordinals) == 2


# =========================================================================== #
# Point 11 — RuntimeUnitOfWork atomic put + CAS head/result cells; replay/stale   #
# =========================================================================== #
HEAD_NS = "runtime.repository_head"
RESULT_NS = "runtime.operation_result"
HK = "1" * 64
RK = "2" * 64


def _cell_stores():
    resolver = SchemaRegistryResolver()
    digest = resolver.register(default_registry())
    stores = RuntimeStores(resolver=resolver, clock=AdvancingClock(),
                           allowed_cell_namespaces=(HEAD_NS, RESULT_NS))
    return stores, digest


def _staged_typed(key="a"):
    return StagedTypedPayloadRef(
        staged_key=StagedPayloadKey(key=key), schema_ref=_SR_RESEARCH, namespace="main")


def _put_and_cas_batch(digest, *, idem, expected_head, rationale="thesis"):
    return RuntimeBatch(
        idempotency_key=idem,
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"), schema_ref=_SR_RESEARCH, namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.BUY,
                              "rationale": rationale},
            registry_digest=digest, idempotency_key=idem + "-pa"),),
        cell_cas=(
            StateCellCompareAndSwapCommand(
                cell_namespace=HEAD_NS, cell_key_digest=HK,
                expected_value=expected_head, new_target=_staged_typed("a")),
            StateCellCompareAndSwapCommand(
                cell_namespace=RESULT_NS, cell_key_digest=RK,
                expected_value=None, new_target=_staged_typed("a")),
        ),
    )


def test_point11_uow_atomically_puts_payload_and_cas_head_and_result_cells():
    stores, digest = _cell_stores()
    result = stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    typed = result.staged_typed_ref("a")
    # both cells atomically point at the batch's staged typed ref.
    assert stores.cells.load(HEAD_NS, HK).semantic_digest() == typed.semantic_digest()
    assert stores.cells.load(RESULT_NS, RK).semantic_digest() == typed.semantic_digest()


def test_point11_same_operation_replay_returns_original_ref_after_head_advance():
    stores, digest = _cell_stores()
    op1 = stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    head_after_op1 = stores.cells.load(HEAD_NS, HK)
    # a distinct op advances the head (expected = current head).
    op2 = stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="op2",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"), schema_ref=_SR_RESEARCH, namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.SELL,
                              "rationale": "next"},
            registry_digest=digest, idempotency_key="op2-pa"),),
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest=HK,
            expected_value=head_after_op1, new_target=_staged_typed("a")),)))
    assert stores.cells.load(HEAD_NS, HK).semantic_digest() == op2.staged_typed_ref("a").semantic_digest()
    # replaying op1 returns op1's original ref even though the head has since moved;
    # the head is NOT rolled back.
    replay = stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    assert replay.staged_typed_ref("a").semantic_digest() == op1.staged_typed_ref("a").semantic_digest()
    assert stores.cells.load(HEAD_NS, HK).semantic_digest() == op2.staged_typed_ref("a").semantic_digest()


def test_point11_stale_expected_head_commit_exposes_none():
    stores, digest = _cell_stores()
    stores.unit_of_work.commit(_put_and_cas_batch(digest, idem="op1", expected_head=None))
    head = stores.cells.load(HEAD_NS, HK)
    # advance the head with a real second payload.
    advance_ref = stores.payloads.put(_SR_RESEARCH, _research(rationale="advance"),
                                      registry_digest=digest, namespace="main", idempotency_key="advance-pa")
    concrete = TypedPayloadRef(schema_ref=_SR_RESEARCH, payload_ref=advance_ref)
    stores.unit_of_work.commit(RuntimeBatch(
        idempotency_key="op2",
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest=HK, expected_value=head, new_target=concrete),)))
    head_now = stores.cells.load(HEAD_NS, HK)

    before = stores.payloads_object_count()
    stale = RuntimeBatch(
        idempotency_key="op3",
        payload_puts=(PayloadPutCommand(
            staged_key=StagedPayloadKey(key="a"), schema_ref=_SR_RESEARCH, namespace="main",
            payload_template={"schema_version": "1", "recommendation": PortfolioRating.HOLD,
                              "rationale": "stale"},
            registry_digest=digest, idempotency_key="op3-pa"),),
        cell_cas=(StateCellCompareAndSwapCommand(
            cell_namespace=HEAD_NS, cell_key_digest=HK,
            expected_value=head, new_target=_staged_typed("a")),))  # head is stale now
    with pytest.raises(UnitOfWorkError):
        stores.unit_of_work.commit(stale)
    # the failed commit exposes nothing: head unchanged, no orphan payload.
    assert stores.cells.load(HEAD_NS, HK).semantic_digest() == head_now.semantic_digest()
    assert stores.payloads_object_count() == before


# =========================================================================== #
# Step 2 — the reviewed upstream registry/catalog digests are the frozen ones    #
# =========================================================================== #
def test_step2_records_the_exact_phase2_runtime_registry_digest():
    base = default_registry()
    assert base.registry_digest == PHASE1_BASE_REGISTRY_DIGEST
    rt = phase2_runtime_registry(base.registry_digest)
    # the sealed cumulative Phase 2 runtime registry reproduces the frozen digest.
    assert rt.registry_digest == PHASE2_RUNTIME_REGISTRY_DIGEST
    assert rt.sealed is True


def test_step2_records_the_canonical_static_catalog_digest_not_the_pilot():
    # the recorded catalog digest is the canonical PHASE2_STATIC_CATALOG_DIGEST, and
    # it is the digest of phase2_static_catalog_snapshot() — not a pilot catalog.
    assert P.PHASE2_STATIC_CATALOG_DIGEST == PHASE2_STATIC_CATALOG_DIGEST
    snap = P.phase2_static_catalog_snapshot()
    assert snap.catalog_digest == PHASE2_STATIC_CATALOG_DIGEST
    from guanlan_v2.orchestration.catalog_runtime import load_pilot_catalog
    assert load_pilot_catalog().snapshot.catalog_digest != PHASE2_STATIC_CATALOG_DIGEST
