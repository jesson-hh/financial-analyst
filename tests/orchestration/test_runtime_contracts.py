# -*- coding: utf-8 -*-
"""Phase 2 · Task 5 — static runtime profile / control / prompt-evidence facts.

Locks the reviewed Task-5 model surface and the cumulative Phase-2 runtime
registry:

* the closed :class:`StaticRuntimeProfile` v1 feature matrix (BOOTSTRAP never
  supported; self-sealed ``profile_digest``);
* strict :class:`RuntimeSupportIssue` / :class:`RuntimeSupportReport` (supported
  iff no issue, canonical ordering, self-sealed ``report_digest``, both-set
  context-requirements ref/digest);
* :class:`ExecutionBridgeDescriptor` (non-empty activation, canonical order,
  ``required=True`` / ``static_prefetch_v1``, no caller override predicate);
* :class:`BridgeStaticSupportSummary` (self-sealed, ``dynamic_or_model_selected_calls``
  closed ``False``);
* :class:`PromptUntrustedBlockRef` / :class:`PromptAssemblyRecord` — main-only,
  order-sensitive, object-id relocation moves audit identity only, block
  SchemaRef/content/order moves the assembly digest;
* :class:`BridgeEvidenceRecorded` (main-only) + control facts
  (Admission/PlanAdmitted/RunResult) reject extra fields + mutation;
* the cumulative registry adds the Task-2 refusal facts **without redefinition**,
  requires the exact Phase-1 base digest, keeps the inherited Phase-1 JSON-schema
  subset byte-identical, matches its frozen golden (never auto-regenerated), and is
  accepted by a later reviewed registry snapshot by exact digest (no "latest").

Run: ``python -m pytest tests/orchestration/test_runtime_contracts.py -v``
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import CJSON_VERSION, content_digest
from guanlan_v2.orchestration.enums import ExecutionKind, PlanSource
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration import runtime_contracts as rc
from guanlan_v2.orchestration.runtime_contracts import (
    AdmissionCandidate,
    AdmissionInvalidated,
    BridgeEvidenceRecorded,
    BridgeStaticSupportSummary,
    EventRefusalRecord,
    ExecutionBridgeDescriptor,
    ExecutionEvidenceOrdinalToken,
    GenericRefusalDetails,
    NamedEvidenceDigest,
    Phase2RuntimeRegistry,
    Phase2RuntimeRegistryError,
    PlanAdmitted,
    PromptAssemblyRecord,
    PromptUntrustedBlockRef,
    ResolvedContextRuntimeRequirements,
    RunResult,
    RuntimeSupportIssue,
    RuntimeSupportReport,
    StaticRuntimeProfile,
    phase2_runtime_registry,
    static_runtime_profile,
)

DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "runtime_schema_manifest_v1.json"
PHASE1_GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "schema_manifest_v1.json"


def _content_ref(id: str, digest: str = DA) -> ContentRef:
    return ContentRef(id=id, version="1", content_digest=digest)


def _cap_ref(id: str, digest: str = DA) -> CapabilityRef:
    return CapabilityRef(id=id, version="1", content_digest=digest)


def _typed_main(schema="Blob", digest=DA, object_id="obj-1") -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=schema, version="1"),
        payload_ref=PayloadRef(namespace="main", object_id=object_id, content_digest=digest),
    )


# =========================================================================== #
# StaticRuntimeProfile                                                        #
# =========================================================================== #
def test_static_runtime_profile_is_the_closed_matrix():
    p = static_runtime_profile()
    assert p.profile_id == "static-runtime" and p.profile_version == "1"
    assert set(p.supported_plan_sources) == {
        PlanSource.PRESET, PlanSource.PRESET_FALLBACK, PlanSource.DYNAMIC,
    }
    assert PlanSource.BOOTSTRAP not in p.supported_plan_sources
    assert set(p.supported_execution_kinds) == {ExecutionKind.LLM, ExecutionKind.DETERMINISTIC}
    assert p.supports_conditions is False and p.supports_reducers is False
    assert p.supports_debates is False and p.supports_gates is False
    assert p.supports_stop_conditions is False and p.supports_bootstrap is False
    assert p.supports_retries is False and p.max_attempts_supported == 1
    assert set(p.bridge_pre_input_modes) == {"none", "memory_refs_v1"}
    assert p.bridge_lifecycle == "static_prefetch_v1"
    assert p.max_prompt_assemblies_per_llm_node == 1
    assert p.max_model_invocations_per_llm_node == 1


def test_static_runtime_profile_self_seal_and_equality():
    a = static_runtime_profile()
    b = static_runtime_profile()
    assert a == b
    assert a.profile_digest == content_digest(a._semantic_payload())
    bad = a.model_dump()
    bad["profile_digest"] = DB
    with pytest.raises(ValidationError):
        StaticRuntimeProfile.model_validate(bad)


def test_static_runtime_profile_cannot_claim_unsupported_feature():
    with pytest.raises(ValidationError):
        StaticRuntimeProfile.build(supports_conditions=True)  # Literal[False]
    with pytest.raises(ValidationError):
        StaticRuntimeProfile.build(max_attempts_supported=2)  # Literal[1]
    with pytest.raises(ValidationError):
        StaticRuntimeProfile.build(supported_plan_sources=(PlanSource.BOOTSTRAP,))


# =========================================================================== #
# RuntimeSupportIssue + RuntimeSupportReport                                  #
# =========================================================================== #
def test_runtime_support_issue_is_strict_and_sortable():
    i = RuntimeSupportIssue(code="x", model_path="draft.nodes", explanation="why")
    assert i.sort_key == ("x", "", "draft.nodes", "why")
    with pytest.raises(ValidationError):
        RuntimeSupportIssue(code="x", model_path="p", explanation="w", extra="no")


def _report(**over):
    base = dict(
        supported=True,
        issues=(),
        candidate_plan_digest=DA,
        phase1_report_digest=DB,
        runtime_profile_digest=DC,
        catalog_digest=DD,
        schema_registry_digest="e" * 64,
    )
    base.update(over)
    return RuntimeSupportReport.build(**base)


def test_support_report_supported_iff_no_issue_and_self_seals():
    r = _report()
    assert r.supported is True and r.issues == ()
    assert r.report_digest == content_digest(r._semantic_payload())
    bad = r.model_dump()
    bad["report_digest"] = "f" * 64
    with pytest.raises(ValidationError):
        RuntimeSupportReport.model_validate(bad)


def test_support_report_supported_true_with_issues_rejected():
    with pytest.raises(ValidationError):
        RuntimeSupportReport.build(
            supported=True,
            issues=(RuntimeSupportIssue(code="x", model_path="p", explanation="w"),),
            candidate_plan_digest=DA, phase1_report_digest=DB, runtime_profile_digest=DC,
            catalog_digest=DD, schema_registry_digest="e" * 64,
        )


def test_support_report_issue_ordering_enforced():
    i1 = RuntimeSupportIssue(code="a", model_path="p", explanation="w")
    i2 = RuntimeSupportIssue(code="z", model_path="p", explanation="w")
    # out-of-order issues are rejected.
    with pytest.raises(ValidationError):
        RuntimeSupportReport.build(
            supported=False, issues=(i2, i1),
            candidate_plan_digest=DA, phase1_report_digest=DB, runtime_profile_digest=DC,
            catalog_digest=DD, schema_registry_digest="e" * 64,
        )


def test_support_report_context_requirements_both_set_or_both_none():
    ref = _typed_main(schema="ContextRuntimeRequirements", digest=DA)
    # ref present but digest none -> rejected
    with pytest.raises(ValidationError):
        _report(context_requirements_ref=ref, context_requirements_digest=None)
    # both present + matching digest -> ok
    r = _report(context_requirements_ref=ref, context_requirements_digest=DA)
    assert r.context_requirements_ref == ref
    # digest must equal the ref content digest
    with pytest.raises(ValidationError):
        _report(context_requirements_ref=ref, context_requirements_digest=DB)


def test_support_report_ref_tuples_must_be_ordered_and_unique():
    r1 = _content_ref("bridge.a")
    r2 = _content_ref("bridge.b")
    with pytest.raises(ValidationError):
        _report(active_bridge_descriptor_refs=(r2, r1))
    with pytest.raises(ValidationError):
        _report(active_bridge_descriptor_refs=(r1, r1))


# =========================================================================== #
# ExecutionBridgeDescriptor                                                   #
# =========================================================================== #
def _descriptor(**over) -> ExecutionBridgeDescriptor:
    base = dict(
        bridge_id="bridge.data", bridge_version="1", priority=10,
        provider_handler_ref=_content_ref("handler.provider"),
        config_ref=_content_ref("guard.cfg"),
        support_analyzer_ref=_content_ref("handler.analyzer"),
        config_schema_ref=SchemaRef(name="BridgeConfig", version="1"),
        activation_capability_refs=(_cap_ref("cap.data"),),
        activation_read_categories=(),
        supported_execution_kinds=(ExecutionKind.LLM,),
        pre_input_kind="memory_refs_v1",
    )
    base.update(over)
    return ExecutionBridgeDescriptor(**base)


def test_bridge_descriptor_closed_markers_and_required():
    d = _descriptor()
    assert d.descriptor_kind == "execution_bridge"
    assert d.required is True and d.lifecycle == "static_prefetch_v1"
    with pytest.raises(ValidationError):
        _descriptor(required=False)  # Literal[True]
    with pytest.raises(ValidationError):
        ExecutionBridgeDescriptor(
            descriptor_kind="other", bridge_id="b", bridge_version="1", priority=1,
            provider_handler_ref=_content_ref("h"), config_ref=_content_ref("c"),
            support_analyzer_ref=_content_ref("a"),
            config_schema_ref=SchemaRef(name="C", version="1"),
            activation_read_categories=("context",),
            supported_execution_kinds=(ExecutionKind.LLM,), pre_input_kind="none",
        )


def test_bridge_descriptor_requires_a_non_empty_activation_tuple():
    with pytest.raises(ValidationError):
        _descriptor(activation_capability_refs=(), activation_read_categories=())


def test_bridge_descriptor_activation_ordering_and_activation_predicate():
    d = _descriptor(
        activation_capability_refs=(_cap_ref("cap.data"),),
        activation_read_categories=("market_data",),
    )
    assert d.activates_for(capability_ids=frozenset({"cap.data"}), read_categories=frozenset())
    assert d.activates_for(capability_ids=frozenset(), read_categories=frozenset({"market_data"}))
    assert not d.activates_for(capability_ids=frozenset({"cap.other"}), read_categories=frozenset({"context"}))
    # there is no caller override / negative predicate field.
    assert "caller_override" not in ExecutionBridgeDescriptor.model_fields
    assert "negative_predicate" not in ExecutionBridgeDescriptor.model_fields


# =========================================================================== #
# BridgeStaticSupportSummary                                                  #
# =========================================================================== #
def _summary(**over) -> BridgeStaticSupportSummary:
    base = dict(
        candidate_plan_digest=DA, node_id="n1", node_params_digest=DB,
        worker_id="w1", worker_digest=DC, bridge_id="bridge.data",
        descriptor_ref=_content_ref("guard.bridge"),
        config_ref=_content_ref("guard.cfg"),
        provider_ref=_content_ref("handler.provider"),
        analyzer_ref=_content_ref("handler.analyzer"),
        allowed_capability_refs=(_cap_ref("cap.data"),),
        min_finalized_tool_calls_on_success=1,
        max_capability_invocations=1,
        pre_input_kind="memory_refs_v1",
    )
    base.update(over)
    return BridgeStaticSupportSummary.build(**base)


def test_bridge_summary_self_seals_and_closes_dynamic_calls():
    s = _summary()
    assert s.dynamic_or_model_selected_calls is False
    assert s.summary_digest == content_digest(s._semantic_payload())
    bad = s.model_dump()
    bad["summary_digest"] = DD
    with pytest.raises(ValidationError):
        BridgeStaticSupportSummary.model_validate(bad)
    with pytest.raises(ValidationError):
        _summary(dynamic_or_model_selected_calls=True)  # Literal[False]


def test_bridge_summary_min_cannot_exceed_max():
    # memory-only zero/zero is legal; guaranteed one/one legal; min>max illegal.
    assert _summary(min_finalized_tool_calls_on_success=0, max_capability_invocations=0)
    with pytest.raises(ValidationError):
        _summary(min_finalized_tool_calls_on_success=2, max_capability_invocations=1)


# =========================================================================== #
# ResolvedContextRuntimeRequirements                                          #
# =========================================================================== #
def test_resolved_context_requirements_binds_typed_ref_to_fact():
    from guanlan_v2.orchestration.context import ContextRuntimeRequirements

    fact = ContextRuntimeRequirements.build(
        context_subject_digest=DA, required_schema_registry_digest=DB,
        required_catalog_digest=DC,
    )
    ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="ContextRuntimeRequirements", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="o", content_digest=fact.requirements_digest),
    )
    rcr = ResolvedContextRuntimeRequirements(typed_ref=ref, fact=fact)
    assert rcr.fact is fact
    # a non-main typed ref is rejected.
    bad_ref = TypedPayloadRef(
        schema_ref=SchemaRef(name="ContextRuntimeRequirements", version="1"),
        payload_ref=PayloadRef(namespace="sealed", object_id="o", content_digest=fact.requirements_digest),
    )
    with pytest.raises(ValidationError):
        ResolvedContextRuntimeRequirements(typed_ref=bad_ref, fact=fact)


# =========================================================================== #
# Prompt-evidence facts                                                       #
# =========================================================================== #
def test_prompt_untrusted_block_ref_main_only_and_relocation_invariant():
    b1 = PromptUntrustedBlockRef.build(
        ordinal=1, payload_ref=_typed_main(digest=DA, object_id="obj-1"),
        media_type="text/plain", rendered_length=42,
    )
    # object-id relocation changes audit/dereference identity only, not block_digest.
    b2 = PromptUntrustedBlockRef.build(
        ordinal=1, payload_ref=_typed_main(digest=DA, object_id="obj-99"),
        media_type="text/plain", rendered_length=42,
    )
    assert b1.block_digest == b2.block_digest
    assert b1.payload_ref != b2.payload_ref  # object id differs
    # changing the content digest changes the block digest.
    b3 = PromptUntrustedBlockRef.build(
        ordinal=1, payload_ref=_typed_main(digest=DB, object_id="obj-1"),
        media_type="text/plain", rendered_length=42,
    )
    assert b3.block_digest != b1.block_digest
    # a non-main typed ref is rejected.
    with pytest.raises(ValidationError):
        PromptUntrustedBlockRef.build(
            ordinal=1,
            payload_ref=TypedPayloadRef(
                schema_ref=SchemaRef(name="Blob", version="1"),
                payload_ref=PayloadRef(namespace="sealed", object_id="o", content_digest=DA),
            ),
            media_type="text/plain", rendered_length=1,
        )


def _block(ordinal, digest=DA, object_id="o", length=10):
    return PromptUntrustedBlockRef.build(
        ordinal=ordinal, payload_ref=_typed_main(digest=digest, object_id=object_id),
        media_type="text/plain", rendered_length=length,
    )


def _assembly(**over) -> PromptAssemblyRecord:
    base = dict(
        plan_digest=DA, node_id="n1", worker_id="w1",
        assembler_id="assembler.static", assembler_version="1",
        system_prompt_ref=_content_ref("prompt.sys"),
        skill_refs=(_content_ref("skill.a"),),
        guardrail_refs=(_content_ref("guard.a"),),
        trusted_input_digests=(NamedEvidenceDigest(name="ctx", digest=DB),),
        untrusted_blocks=(_block(1, digest=DA), _block(2, digest=DB)),
        model_request_digest=DC,
    )
    base.update(over)
    return PromptAssemblyRecord.build(**base)


def test_prompt_assembly_order_sensitive_and_object_id_relocation_invariant():
    a = _assembly()
    assert a.assembly_digest == content_digest(a._semantic_payload())
    # relocating a block's payload object id does not change assembly digest.
    relocated = _assembly(untrusted_blocks=(_block(1, digest=DA, object_id="zzz"), _block(2, digest=DB)))
    assert relocated.assembly_digest == a.assembly_digest
    # reordering block content changes the assembly digest.
    reordered = _assembly(untrusted_blocks=(_block(1, digest=DB), _block(2, digest=DA)))
    assert reordered.assembly_digest != a.assembly_digest


def test_prompt_assembly_ordinals_must_be_contiguous_and_names_unique():
    with pytest.raises(ValidationError):
        _assembly(untrusted_blocks=(_block(1), _block(3)))  # gap
    with pytest.raises(ValidationError):
        _assembly(trusted_input_digests=(
            NamedEvidenceDigest(name="z", digest=DA),
            NamedEvidenceDigest(name="a", digest=DB),
        ))  # unordered


def test_bridge_evidence_recorded_is_main_only():
    tok = ExecutionEvidenceOrdinalToken(attempt=1, call_ordinal=1, evidence_ordinal=1)
    ev = BridgeEvidenceRecorded(
        run_id="run-1", plan_digest=DA, node_id="n1", ordinal_token=tok,
        within_call_role="provider_prefetch", evidence_ref=_typed_main(),
    )
    assert ev.ordinal_token.attempt == 1
    with pytest.raises(ValidationError):
        BridgeEvidenceRecorded(
            run_id="run-1", plan_digest=DA, node_id="n1", ordinal_token=tok,
            within_call_role="provider_prefetch",
            evidence_ref=TypedPayloadRef(
                schema_ref=SchemaRef(name="Blob", version="1"),
                payload_ref=PayloadRef(namespace="sealed", object_id="o", content_digest=DA),
            ),
        )


# =========================================================================== #
# Control facts strictness + mutation                                         #
# =========================================================================== #
def test_admission_candidate_is_strict_and_frozen():
    c = AdmissionCandidate(
        run_id="run-1", request_id="req-1", candidate_plan_digest=DA,
        support_report_digest=DB, phase1_report_digest=DC, runtime_profile_digest=DD,
        catalog_digest="e" * 64, schema_registry_digest="f" * 64,
    )
    with pytest.raises(ValidationError):
        c.run_id = "other"  # frozen
    with pytest.raises(ValidationError):
        AdmissionCandidate(
            run_id="r", request_id="q", candidate_plan_digest=DA, support_report_digest=DB,
            phase1_report_digest=DC, runtime_profile_digest=DD, catalog_digest="e" * 64,
            schema_registry_digest="f" * 64, extra="no",
        )


def test_admission_invalidated_binds_drift_without_dispatch_authority():
    inv = AdmissionInvalidated(
        run_id="run-1", candidate_plan_digest=DA, reservation_id="res-1",
        drifted_inputs=(NamedEvidenceDigest(name="catalog", digest=DB),),
        reason_code="catalog_drift",
    )
    assert "dispatch_authority" not in AdmissionInvalidated.model_fields
    assert "grant" not in AdmissionInvalidated.model_fields
    # drifted inputs must be canonically ordered + non-empty.
    with pytest.raises(ValidationError):
        AdmissionInvalidated(run_id="r", candidate_plan_digest=DA, reservation_id="res",
                             drifted_inputs=(), reason_code="x")
    with pytest.raises(ValidationError):
        AdmissionInvalidated(run_id="r", candidate_plan_digest=DA, reservation_id="res",
                             drifted_inputs=(NamedEvidenceDigest(name="z", digest=DA),
                                             NamedEvidenceDigest(name="a", digest=DB)),
                             reason_code="x")


def test_plan_admitted_and_run_result_are_strict():
    # Task 6 review extension: PlanAdmitted binds the full nine-item admission
    # authority chain (both report digests, reservation id + semantic digest,
    # approval event id/digest, optional attestation digest and the frozen Plan
    # PayloadRef). plan_digest must equal candidate_plan_digest.
    plan_ref = PayloadRef(namespace="main", object_id="plan-1", content_digest="9" * 64)
    pa = PlanAdmitted(
        run_id="run-1", request_id="req-1", plan_digest=DA, candidate_plan_digest=DA,
        phase1_report_digest=DB, support_report_digest=DC, runtime_profile_digest=DD,
        catalog_digest="e" * 64, schema_registry_digest="f" * 64,
        reservation_id="res-1", reservation_semantic_digest="1" * 64,
        approval_event_id="ev-1", approval_digest="2" * 64, plan_payload_ref=plan_ref,
    )
    assert pa.plan_digest == pa.candidate_plan_digest
    with pytest.raises(ValidationError):
        pa.plan_digest = DB  # frozen
    # plan_digest != candidate_plan_digest is rejected (no second plan digest).
    with pytest.raises(ValidationError):
        PlanAdmitted(
            run_id="run-1", request_id="req-1", plan_digest=DA, candidate_plan_digest=DB,
            phase1_report_digest=DB, support_report_digest=DC, runtime_profile_digest=DD,
            catalog_digest="e" * 64, schema_registry_digest="f" * 64,
            reservation_id="res-1", reservation_semantic_digest="1" * 64,
            approval_event_id="ev-1", approval_digest="2" * 64, plan_payload_ref=plan_ref,
        )
    # a non-main frozen Plan payload ref is rejected.
    with pytest.raises(ValidationError):
        PlanAdmitted(
            run_id="run-1", request_id="req-1", plan_digest=DA, candidate_plan_digest=DA,
            phase1_report_digest=DB, support_report_digest=DC, runtime_profile_digest=DD,
            catalog_digest="e" * 64, schema_registry_digest="f" * 64,
            reservation_id="res-1", reservation_semantic_digest="1" * 64,
            approval_event_id="ev-1", approval_digest="2" * 64,
            plan_payload_ref=PayloadRef(namespace="sealed", object_id="p", content_digest="9" * 64),
        )
    rr = RunResult(run_id="run-1", plan_digest=DA, terminal_status="completed")
    assert rr.settled_tokens == 0
    with pytest.raises(ValidationError):
        RunResult(run_id="r", plan_digest=DA, terminal_status="exploded")  # not a closed status


def test_admission_candidate_optional_context_requirements_ref_coherence():
    # Task 6 review extension: the optional ContextRuntimeRequirements typed ref +
    # digest are both-set-or-both-none and the digest must equal the ref content.
    ref = _typed_main(schema="ContextRuntimeRequirements", digest=DA)
    ok = AdmissionCandidate(
        run_id="run-1", request_id="req-1", candidate_plan_digest=DA, support_report_digest=DB,
        phase1_report_digest=DC, runtime_profile_digest=DD, catalog_digest="e" * 64,
        schema_registry_digest="f" * 64, context_content_digest="7" * 64,
        context_requirements_ref=ref, context_requirements_digest=DA,
        legacy_attestation_digest="8" * 64,
    )
    assert ok.context_requirements_ref == ref
    with pytest.raises(ValidationError):
        AdmissionCandidate(
            run_id="run-1", request_id="req-1", candidate_plan_digest=DA, support_report_digest=DB,
            phase1_report_digest=DC, runtime_profile_digest=DD, catalog_digest="e" * 64,
            schema_registry_digest="f" * 64, context_requirements_ref=ref,
            context_requirements_digest=DB,  # != ref content digest
        )


# =========================================================================== #
# Cumulative Phase-2 runtime registry                                         #
# =========================================================================== #
def test_phase2_registry_requires_exact_phase1_digest():
    p1 = default_registry().registry_digest
    reg = phase2_runtime_registry(p1)
    assert reg.sealed is True
    with pytest.raises(Phase2RuntimeRegistryError):
        phase2_runtime_registry("0" * 64)  # wrong base digest


def test_phase2_base_digest_equals_phase1():
    assert rc.PHASE2_BASE_REGISTRY_DIGEST == default_registry().registry_digest


def test_phase2_registry_superset_and_distinct_digest():
    p1 = default_registry().registry_digest
    reg = phase2_runtime_registry(p1)
    assert reg.registry_digest != p1  # a strict superset has a distinct digest
    keys = {e.key for e in reg.manifest()}
    # every Phase-1 public model key is present.
    from guanlan_v2.orchestration.schema_registry import PHASE1_PUBLIC_MODELS
    for m in PHASE1_PUBLIC_MODELS:
        assert f"{m.__name__}@1" in keys
    # every Phase-2 runtime model key is present.
    for m in rc.PHASE2_RUNTIME_MODELS:
        v = m.model_fields["schema_version"].default
        assert f"{m.__name__}@{v}" in keys


def test_phase2_registry_inherited_phase1_subset_is_byte_identical():
    p1 = default_registry().registry_digest
    reg = phase2_runtime_registry(p1)
    p2_manifest = {e.key: e.json_schema_digest for e in reg.manifest()}
    p1_manifest = {e.key: e.json_schema_digest for e in default_registry().manifest()}
    for key, digest in p1_manifest.items():
        assert p2_manifest[key] == digest, f"inherited Phase-1 schema {key} drifted"


def test_phase2_registry_resolves_and_validates_both_phase1_and_phase2():
    p1 = default_registry().registry_digest
    reg = phase2_runtime_registry(p1)
    # a Phase-1 payload resolves.
    assert reg.resolve(SchemaRef(name="ResearchPlan", version="1")).__name__ == "ResearchPlan"
    # a Phase-2 fact validates through the cumulative registry.
    issue = RuntimeSupportIssue(code="x", model_path="p", explanation="w")
    restored = reg.validate_payload(SchemaRef(name="RuntimeSupportIssue", version="1"), issue.model_dump())
    assert isinstance(restored, RuntimeSupportIssue) and restored == issue


# -- invariant 7: refusal facts added WITHOUT redefinition ------------------ #
def test_refusal_facts_added_by_identity_no_redefinition():
    # the registered refusal-fact classes are the SAME Task-2 objects (no re-def).
    assert NamedEvidenceDigest in rc.PHASE2_RUNTIME_MODELS
    assert GenericRefusalDetails in rc.PHASE2_RUNTIME_MODELS
    assert EventRefusalRecord in rc.PHASE2_RUNTIME_MODELS
    p1 = default_registry().registry_digest
    reg = phase2_runtime_registry(p1)
    assert reg.resolve(SchemaRef(name="EventRefusalRecord", version="1")) is EventRefusalRecord
    assert reg.resolve(SchemaRef(name="GenericRefusalDetails", version="1")) is GenericRefusalDetails
    # the Task-2 ABI is intact: EventRefusalRecord still self-seals in the audit ns.
    from datetime import datetime, timezone
    rec = EventRefusalRecord.build(
        record_id="rr", occurred_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
        reason_code="r", detail_schema_ref=SchemaRef(name="GenericRefusalDetails", version="1"),
        detail_payload_ref=PayloadRef(namespace="audit", object_id="o", content_digest=DA),
        idempotency_key="k",
    )
    assert rec.detail_payload_ref.namespace == "audit"


# -- Task 4 pool payload: LayerCommit promoted into the cumulative registry - #
def test_layer_commit_registered_by_identity_for_the_pool():
    """Phase 1 deliberately keeps LayerCommit out of its payload registry
    (_R_EVENT_RECORD); the cumulative Phase-2 registry promotes it because the
    Task-4 pool persists it as a registry-validated PayloadStore payload behind
    the public LayerCommitted event. CommittedArtifactRef rides nested and is
    NOT independently resolvable."""
    from guanlan_v2.orchestration import events

    p1 = default_registry()
    reg = phase2_runtime_registry(p1.registry_digest)
    assert reg.resolve(SchemaRef(name="LayerCommit", version="1")) is events.LayerCommit
    # the Phase-1 default registry stays blind to it (no Phase-1 mutation).
    from guanlan_v2.orchestration.schema_registry import UnknownSchemaError
    with pytest.raises(UnknownSchemaError):
        p1.resolve(SchemaRef(name="LayerCommit", version="1"))
    # the nested component is not registered separately.
    with pytest.raises(Phase2RuntimeRegistryError):
        reg.resolve(SchemaRef(name="CommittedArtifactRef", version="1"))


# -- invariant 10: a later reviewed registry is accepted by exact digest ---- #
def test_resolver_accepts_phase2_registry_by_exact_digest_no_latest():
    from guanlan_v2.orchestration.eventstore import SchemaRegistryResolver

    resolver = SchemaRegistryResolver()
    p1_reg = default_registry()
    p1_digest = resolver.register(p1_reg)
    p2_reg = phase2_runtime_registry(p1_reg.registry_digest)
    p2_digest = resolver.register(p2_reg)
    assert p1_digest != p2_digest
    # each resolves by its exact digest; Phase-1 identity is unchanged.
    assert resolver.resolve(p1_digest) is p1_reg
    assert resolver.resolve(p2_digest) is p2_reg
    # there is no "latest" alias.
    assert not hasattr(resolver, "latest")


def test_phase2_registry_rejects_registration_when_sealed():
    p1 = default_registry().registry_digest
    reg = phase2_runtime_registry(p1)
    with pytest.raises(Phase2RuntimeRegistryError):
        reg.register(RuntimeSupportIssue)


def test_phase2_registry_registration_order_independent_digest():
    p1 = default_registry().registry_digest
    a = phase2_runtime_registry(p1)
    b = Phase2RuntimeRegistry()
    for m in reversed(rc.phase2_public_models()):
        b.register(m)
    b.seal()
    assert b.registry_digest == a.registry_digest


# =========================================================================== #
# Frozen golden manifest — NEVER auto-regenerated                             #
# =========================================================================== #
def test_runtime_golden_exists_and_pins_base_and_registry_digest():
    assert GOLDEN_PATH.exists(), f"missing runtime golden: {GOLDEN_PATH}"
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert doc["algorithm"] == CJSON_VERSION == "sha256+cjson-v1"
    assert doc["base_registry_digest"] == default_registry().registry_digest
    reg = phase2_runtime_registry(default_registry().registry_digest)
    assert doc["registry_digest"] == reg.registry_digest


def test_runtime_golden_entries_match_registry_manifest():
    """The golden per-model digests + registry digest must match; NEVER regenerated
    by this test — a changed manifest is a reviewed Phase-2 contract change."""
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    golden = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    reg = phase2_runtime_registry(default_registry().registry_digest)
    manifest = {e.key: e.json_schema_digest for e in reg.manifest()}
    assert set(manifest) == set(golden), "runtime registry key set drifted from golden"
    for key in sorted(golden):
        assert manifest[key] == golden[key], f"JSON-schema digest drift for {key}"


def test_runtime_golden_inherited_subset_matches_phase1_golden_byte_for_byte():
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    p1_doc = json.loads(PHASE1_GOLDEN_PATH.read_text(encoding="utf-8"))
    runtime_entries = {e["key"]: e["json_schema_digest"] for e in doc["entries"]}
    for e in p1_doc["entries"]:
        assert runtime_entries[e["key"]] == e["json_schema_digest"], (
            f"inherited Phase-1 schema {e['key']} is not byte-identical in the runtime golden"
        )
    assert doc["base_registry_digest"] == p1_doc["registry_digest"]
