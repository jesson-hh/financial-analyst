# -*- coding: utf-8 -*-
"""Phase 2 · Task 3 — read-only catalog material runtime + resolver.

These tests prove the runtime *consumes* one verified Phase 1
``WorkerCatalogSnapshot`` and resolves its physical prompt/skill/guardrail/
handler/capability materials without ever forking Phase 1 semantics: exact
material resolution, drift / missing / extra rejection, an immutable lookup
index with no register/mutate surface, a trusted handler/model-factory registry
keyed by catalog ref identity, the three-worker pilot's role/scope/output
schemas, compatibility-worker exclusion, and the absence of any physical path in
the snapshot / WorkerSpec.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.catalog import (
    CapabilityDescriptor,
    CapabilityManifestEntry,
    CompatibilityBinding,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    SkillBinding,
    SkillManifest,
    WorkerCatalogSnapshot,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
)
from guanlan_v2.orchestration.digest import canonical_json
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import (
    LOGICAL_ID_PATTERN,
    CapabilityRef,
    ContentRef,
    SchemaRef,
)

from guanlan_v2.orchestration.catalog_runtime import (
    CatalogMaterialError,
    CatalogRuntime,
    InMemoryMaterialSource,
    MaterialSource,
    ResolvedWorkerRuntime,
    TrustedFactoryRegistry,
    load_pilot_catalog,
)

DIGEST0 = "0" * 64
SREF_IN = SchemaRef(name="InputModel", version="1")
SREF_OUT = SchemaRef(name="OutputModel", version="1")


# --------------------------------------------------------------------------- #
# Local fixture builders (mirror the Phase 1 catalog test helpers)            #
# --------------------------------------------------------------------------- #
def make_text(id: str, kind: str, text: str, version: str = "1"):
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=id, version=version, content_digest=DIGEST0),
        kind=kind,
        raw_utf8=raw,
    )
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=id, version=version, content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def make_capability(id: str, *, capability_kind="tool", transport="in_process",
                    operation="op.run", version="1"):
    desc = CapabilityDescriptor(
        id=id, version=version, capability_kind=capability_kind, transport=transport,
        operation=operation, input_schema_ref=SREF_IN, output_schema_ref=SREF_OUT,
    )
    tmp = ResolvedCapabilityMaterial(
        ref=CapabilityRef(id=id, version=version, content_digest=DIGEST0), descriptor=desc,
    )
    digest = catalog_material_digest(tmp)
    ref = CapabilityRef(id=id, version=version, content_digest=digest)
    return ref, ResolvedCapabilityMaterial(ref=ref, descriptor=desc)


def render_skill(*, name="deep dive", summary="Multi-source A-share deep dive.",
                 perfect_for=("high-conviction names", "earnings season"),
                 not_ideal_for=("intraday scalping",),
                 sources=("get_verified_snapshot", "tushare daily")) -> str:
    line2 = "Perfect for: " + canonical_json(list(perfect_for))
    line3 = "Not ideal for: " + canonical_json(list(not_ideal_for))
    fm = (
        "---\n"
        f"name: {name}\n"
        "description: |\n"
        f"  {summary}\n"
        f"  {line2}\n"
        f"  {line3}\n"
        "---\n"
    )
    body = "## ⚠️ CRITICAL: Data Source Priority\n" + "".join(f"- {s}\n" for s in sources)
    return fm + body


def make_skill(id: str, *, source_identity="ta.deep_dive", version="1", **kw):
    text = render_skill(**kw)
    ref, mat = make_text(id, "skill", text, version=version)
    manifest = SkillManifest(
        ref=ref,
        name=kw.get("name", "deep dive"),
        summary=kw.get("summary", "Multi-source A-share deep dive."),
        format_version="skill-v1",
        perfect_for=tuple(kw.get("perfect_for", ("high-conviction names", "earnings season"))),
        not_ideal_for=tuple(kw.get("not_ideal_for", ("intraday scalping",))),
        critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
        source_identity=source_identity,
    )
    return ref, mat, manifest


def _llm_worker(id, *, prompt_ref, skill_ref, guard_ref, cap_ref=None,
                can_emit_decision=False, decision_authority="none", lane="text"):
    caps = (cap_ref,) if cap_ref is not None else ()
    tool_calls = ToolCallRequirement.OPTIONAL if cap_ref else ToolCallRequirement.FORBIDDEN
    return WorkerSpec(
        id=id, catalog_role="final", selection_scope="dynamic_allowed",
        lane=lane, persona="analyst", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=prompt_ref,
        skills=(SkillBinding(skill_ref=skill_ref),),
        guardrail_refs=(guard_ref,),
        capability_allowlist=caps,
        read_categories=("context",),
        outputs=(OutputBinding(name="primary", schema_ref=SREF_OUT),),
        evidence_policy=EvidencePolicy(tool_calls=tool_calls),
        supported_modes=(DataMode.ONLINE,),
        can_emit_decision=can_emit_decision, decision_authority=decision_authority,
    )


def _det_worker(id, *, handler_ref, lane="quant"):
    return WorkerSpec(
        id=id, catalog_role="final", selection_scope="dynamic_allowed",
        lane=lane, persona="quant handler", tier=Tier.READER,
        execution=ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler_ref),
        outputs=(OutputBinding(name="primary", schema_ref=SREF_OUT),),
        supported_modes=(DataMode.ONLINE,),
        can_emit_decision=False, decision_authority="none",
    )


def _compat_worker(id="compat.stock_deep_dive", *, prompt_ref):
    return WorkerSpec(
        id=id, catalog_role="compatibility", selection_scope="static_legacy_only",
        compatibility=CompatibilityBinding(
            legacy_source_schema=SchemaRef(name="LegacyConfig", version="1"),
            source_config_digest="a" * 64, legacy_mapping_digest="b" * 64,
        ),
        lane="decision", persona="legacy mirror", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=prompt_ref,
        outputs=(OutputBinding(name="primary", schema_ref=SREF_OUT),),
        supported_modes=(DataMode.ONLINE,),
        can_emit_decision=False, decision_authority="none",
    )


def synthetic_snapshot(*, include_det=False, include_compat=False):
    """A small but complete snapshot + the materials that back it."""
    prompt_ref, prompt_mat = make_text("text.demo.prompt", "prompt", "You are the demo analyst.")
    guard_ref, guard_mat = make_text("guard.provenance", "guardrail", "Cite every number.")
    skill_ref, skill_mat, skill_man = make_skill("skill.demo")
    cap_ref, cap_mat = make_capability("cap.markets")

    workers = [_llm_worker("text.demo", prompt_ref=prompt_ref, skill_ref=skill_ref,
                           guard_ref=guard_ref, cap_ref=cap_ref)]
    content = [
        ContentManifestEntry(ref=prompt_ref, kind="prompt", name="Prompt",
                             description="Role.", source_identity="guanlan.demo"),
        ContentManifestEntry(ref=guard_ref, kind="guardrail", name="Guard",
                             description="Anchors.", source_identity="guanlan.introspector"),
    ]
    mats = [prompt_mat, guard_mat, skill_mat, cap_mat]
    caps_manifest = [CapabilityManifestEntry(ref=cap_ref, capability_kind="tool",
                                             transport="in_process")]

    if include_det:
        handler_ref, handler_mat = make_text("handler.demo", "handler", "def run(ctx): return ctx")
        workers.append(_det_worker("quant.demo", handler_ref=handler_ref))
        content.append(ContentManifestEntry(ref=handler_ref, kind="handler", name="Handler",
                                            description="Pure fn.", source_identity="guanlan.quant"))
        mats.append(handler_mat)

    if include_compat:
        cprompt_ref, cprompt_mat = make_text("compat.demo.prompt", "prompt", "Legacy mirror.")
        workers.append(_compat_worker(prompt_ref=cprompt_ref))
        content.append(ContentManifestEntry(ref=cprompt_ref, kind="prompt", name="Legacy prompt",
                                            description="Mirror.", source_identity="guanlan.legacy"))
        mats.append(cprompt_mat)

    snap = build_catalog_snapshot(
        catalog_version="synthetic-v1",
        content_manifest=tuple(content),
        skill_manifest=(skill_man,),
        capability_manifest=tuple(caps_manifest),
        workers=tuple(workers),
        resolved_material=tuple(mats),
    )
    return snap, tuple(mats)


def source_from_materials(materials):
    text = {}
    caps = {}
    for m in materials:
        if isinstance(m, ResolvedTextMaterial):
            text[(m.ref.id, m.ref.version)] = m.raw_utf8
        else:
            caps[(m.ref.id, m.ref.version)] = m.descriptor
    return InMemoryMaterialSource(text=text, capabilities=caps)


# --------------------------------------------------------------------------- #
# 1. Exact material resolution + immutable lookup                             #
# --------------------------------------------------------------------------- #
def test_build_resolves_exact_materials_and_digest():
    snap, mats = synthetic_snapshot()
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    assert rt.catalog_digest == snap.catalog_digest
    assert rt.catalog_version == snap.catalog_version
    w = rt.worker("text.demo")
    assert w.id == "text.demo"
    # text() returns the resolved bytes for a pinned ref
    tm = rt.text(w.system_prompt_ref)
    assert isinstance(tm, ResolvedTextMaterial)
    assert tm.raw_utf8 == b"You are the demo analyst."
    # capability() returns the resolved descriptor
    cm = rt.capability(w.capability_allowlist[0])
    assert isinstance(cm, ResolvedCapabilityMaterial)


def test_resolve_worker_returns_prechecked_materials():
    snap, mats = synthetic_snapshot()
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    rw = rt.resolve_worker("text.demo")
    assert isinstance(rw, ResolvedWorkerRuntime)
    assert rw.system_prompt is not None and rw.system_prompt.kind == "prompt"
    assert len(rw.skills) == 1 and rw.skills[0].kind == "skill"
    assert len(rw.guardrails) == 1 and rw.guardrails[0].kind == "guardrail"
    assert len(rw.capabilities) == 1
    assert rw.handler is None  # LLM worker has no handler material


def test_resolved_worker_runtime_is_frozen_cannot_replace_binding():
    snap, mats = synthetic_snapshot()
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    rw = rt.resolve_worker("text.demo")
    with pytest.raises(ValidationError):
        rw.system_prompt = None  # type: ignore[misc]


def test_runtime_has_no_register_or_mutate_surface():
    snap, mats = synthetic_snapshot()
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    assert not hasattr(rt, "register")
    assert not hasattr(rt, "add_worker")
    assert not hasattr(rt, "set_version")


def test_text_lookup_confined_by_content_digest():
    """text() must reject a ref whose digest does not match the pinned material."""
    snap, mats = synthetic_snapshot()
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    w = rt.worker("text.demo")
    forged = ContentRef(id=w.system_prompt_ref.id, version=w.system_prompt_ref.version,
                        content_digest="c" * 64)
    with pytest.raises(CatalogMaterialError):
        rt.text(forged)


def test_material_source_is_a_protocol():
    snap, mats = synthetic_snapshot()
    src = source_from_materials(mats)
    assert isinstance(src, MaterialSource)


# --------------------------------------------------------------------------- #
# 2. Drift / missing / extra / duplicate rejection                            #
# --------------------------------------------------------------------------- #
def test_content_drift_rejected():
    snap, mats = synthetic_snapshot()
    src = source_from_materials(mats)
    # drift the prompt bytes for a pinned ref
    src._text[("text.demo.prompt", "1")] = b"You are a DIFFERENT analyst."
    with pytest.raises(CatalogMaterialError):
        CatalogRuntime.build(snap, src)


def test_missing_material_rejected():
    snap, mats = synthetic_snapshot()
    src = source_from_materials(mats)
    del src._text[("guard.provenance", "1")]
    with pytest.raises(CatalogMaterialError):
        CatalogRuntime.build(snap, src)


def test_extra_unreferenced_material_rejected():
    snap, mats = synthetic_snapshot()
    src = source_from_materials(mats)
    src._text[("prompt.stowaway", "1")] = b"unreferenced material"
    with pytest.raises(CatalogMaterialError):
        CatalogRuntime.build(snap, src)


def test_extra_unreferenced_capability_rejected():
    snap, mats = synthetic_snapshot()
    src = source_from_materials(mats)
    _, extra = make_capability("cap.stowaway")
    src._caps[("cap.stowaway", "1")] = extra.descriptor
    with pytest.raises(CatalogMaterialError):
        CatalogRuntime.build(snap, src)


def test_duplicate_material_rejected_via_builder_delegation():
    """CatalogRuntime.build routes coverage through build_catalog_snapshot, which
    rejects a duplicated resolved material — the runtime inherits that rejection."""
    _, mats = synthetic_snapshot()
    prompt_mat = next(m for m in mats if isinstance(m, ResolvedTextMaterial)
                      and m.ref.id == "text.demo.prompt")
    from guanlan_v2.orchestration.catalog import CatalogError
    with pytest.raises(CatalogError):
        build_catalog_snapshot(
            catalog_version="dup", content_manifest=(
                ContentManifestEntry(ref=prompt_mat.ref, kind="prompt", name="p",
                                     description="d", source_identity="s.a"),
            ),
            skill_manifest=(), capability_manifest=(),
            workers=(), resolved_material=(prompt_mat, prompt_mat),
        )


# --------------------------------------------------------------------------- #
# 3. Trusted handler / model-factory registry (keyed by catalog ref identity) #
# --------------------------------------------------------------------------- #
def test_handler_registry_keyed_by_catalog_ref():
    snap, mats = synthetic_snapshot(include_det=True)
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    reg = TrustedFactoryRegistry(rt)
    handler_ref = rt.worker("quant.demo").execution.handler_ref

    sentinel = object()
    reg.register_handler(handler_ref, lambda: sentinel)
    assert reg.handler_factory(handler_ref)() is sentinel
    # rebinding is rejected
    with pytest.raises(CatalogMaterialError):
        reg.register_handler(handler_ref, lambda: None)


def test_handler_registry_rejects_off_catalog_ref():
    snap, mats = synthetic_snapshot(include_det=True)
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    reg = TrustedFactoryRegistry(rt)
    foreign = ContentRef(id="handler.smuggled", version="1", content_digest="d" * 64)
    with pytest.raises(CatalogMaterialError):
        reg.register_handler(foreign, lambda: None)


def test_handler_registry_rejects_wrong_digest_for_catalog_id():
    """A handler id present in the catalog but with a forged digest is off-catalog."""
    snap, mats = synthetic_snapshot(include_det=True)
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    reg = TrustedFactoryRegistry(rt)
    real = rt.worker("quant.demo").execution.handler_ref
    forged = ContentRef(id=real.id, version=real.version, content_digest="e" * 64)
    with pytest.raises(CatalogMaterialError):
        reg.register_handler(forged, lambda: None)


def test_model_factory_registry_confined_to_catalog_tiers():
    snap, mats = synthetic_snapshot()
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    reg = TrustedFactoryRegistry(rt)
    gw = object()
    reg.register_model_factory("reasoner", lambda: gw)
    assert reg.model_factory("reasoner")() is gw
    # a tier no catalog worker uses is rejected
    with pytest.raises(CatalogMaterialError):
        reg.register_model_factory("reasoner_deep", lambda: None)


# --------------------------------------------------------------------------- #
# 4. Compatibility exclusion                                                  #
# --------------------------------------------------------------------------- #
def test_compat_worker_excluded_from_final_count_but_resolvable():
    snap, mats = synthetic_snapshot(include_compat=True)
    rt = CatalogRuntime.build(snap, source_from_materials(mats))
    assert rt.final_worker_count() == 1
    assert "compat.stock_deep_dive" not in rt.final_worker_ids()
    assert "text.demo" in rt.final_worker_ids()
    # resolving a compat worker yields materials but confers no authority / scope
    cw = rt.worker("compat.stock_deep_dive")
    assert cw.catalog_role == "compatibility"
    assert cw.selection_scope == "static_legacy_only"
    assert cw.decision_authority == "none"
    rw = rt.resolve_worker("compat.stock_deep_dive")
    assert rw.worker.selection_scope == "static_legacy_only"


# --------------------------------------------------------------------------- #
# 5. Three-worker pilot catalog                                               #
# --------------------------------------------------------------------------- #
PILOT_IDS = ("text.sentiment", "dec.research_mgr", "dec.pm")


@pytest.fixture(scope="module")
def pilot_runtime():
    return load_pilot_catalog()


def test_pilot_final_triad_present(pilot_runtime):
    assert set(pilot_runtime.final_worker_ids()) == set(PILOT_IDS)
    assert pilot_runtime.final_worker_count() == 3


def test_pilot_roles_and_scope(pilot_runtime):
    for wid in PILOT_IDS:
        w = pilot_runtime.worker(wid)
        assert w.catalog_role == "final"
        assert w.selection_scope == "dynamic_allowed"


def test_pilot_output_schemas(pilot_runtime):
    def primary(wid):
        outs = {o.name: o for o in pilot_runtime.worker(wid).outputs}
        return outs["primary"].schema_ref.key
    assert primary("text.sentiment") == "SentimentReport@1"
    assert primary("dec.research_mgr") == "ResearchPlan@1"
    assert primary("dec.pm") == "PortfolioDecision@1"


def test_pilot_pm_is_advisory_only_not_trading(pilot_runtime):
    from guanlan_v2.orchestration.catalog import DecisionAuthority
    # the type ceiling is advisory_only — there is no trading authority level
    assert set(DecisionAuthority.__args__) == {"none", "advisory_only"}
    pm = pilot_runtime.worker("dec.pm")
    assert pm.can_emit_decision is True
    assert pm.decision_authority == "advisory_only"
    rm = pilot_runtime.worker("dec.research_mgr")
    assert rm.decision_authority == "advisory_only"
    sent = pilot_runtime.worker("text.sentiment")
    assert sent.can_emit_decision is False
    assert sent.decision_authority == "none"


def test_pilot_chain_wiring_by_schema(pilot_runtime):
    """text.sentiment -> dec.research_mgr -> dec.pm consumes the prior primary."""
    rm_inputs = {i.name: i.schema_ref.key for i in pilot_runtime.worker("dec.research_mgr").inputs}
    assert "SentimentReport@1" in rm_inputs.values()
    pm_inputs = {i.name: i.schema_ref.key for i in pilot_runtime.worker("dec.pm").inputs}
    assert "ResearchPlan@1" in pm_inputs.values()


def test_pilot_resolve_worker_materials_are_runnable(pilot_runtime):
    from guanlan_v2.orchestration.catalog import parse_skill_v1
    rw = pilot_runtime.resolve_worker("text.sentiment")
    assert rw.system_prompt is not None
    assert len(rw.skills) == 1
    # the pilot skill parses as a valid skill-v1 envelope
    parsed = parse_skill_v1(rw.skills[0].raw_utf8.decode("utf-8"))
    assert parsed.perfect_for  # non-empty triggers
    assert parsed.source_priority  # non-empty data-source priority
    # dec.pm carries its A-share constraint-check capability
    pm = pilot_runtime.resolve_worker("dec.pm")
    assert len(pm.capabilities) >= 1


def test_pilot_catalog_digest_pinned_and_drift_detected(pilot_runtime):
    snap = pilot_runtime.snapshot
    # rebuild the pinned snapshot against a fully drifted source and require rejection
    src = InMemoryMaterialSource(
        text={(e.ref.id, e.ref.version): b"tampered" for e in snap.content_manifest}
        | {(e.ref.id, e.ref.version): b"tampered skill" for e in snap.skill_manifest},
        capabilities={(e.ref.id, e.ref.version): _cap_desc_for(snap, e.ref)
                      for e in snap.capability_manifest},
    )
    with pytest.raises(CatalogMaterialError):
        CatalogRuntime.build(snap, src)


def _cap_desc_for(snap, ref):
    # helper: pull the descriptor a capability ref would resolve to (unused digest side)
    for w in snap.workers:
        for c in w.capability_allowlist:
            if (c.id, c.version) == (ref.id, ref.version):
                return CapabilityDescriptor(
                    id=ref.id, version=ref.version, capability_kind="data_adapter",
                    transport="in_process", operation="x.y",
                    input_schema_ref=SREF_IN, output_schema_ref=SREF_OUT)
    return CapabilityDescriptor(
        id=ref.id, version=ref.version, capability_kind="data_adapter",
        transport="in_process", operation="x.y",
        input_schema_ref=SREF_IN, output_schema_ref=SREF_OUT)


# --------------------------------------------------------------------------- #
# 6. No physical path anywhere in the snapshot / WorkerSpec                    #
# --------------------------------------------------------------------------- #
def test_no_physical_path_in_snapshot(pilot_runtime):
    import re
    snap = pilot_runtime.snapshot
    blob = canonical_json(snap)
    # no filesystem separators / config path fragments leak into the snapshot
    assert "config/orchestration" not in blob
    assert ".md" not in blob
    assert "\\\\" not in blob
    # every ref id is a logical id (grammar forbids '/', '\\', ':', '@')
    pat = re.compile(LOGICAL_ID_PATTERN)
    for e in snap.content_manifest:
        assert pat.match(e.ref.id)
    for e in snap.skill_manifest:
        assert pat.match(e.ref.id)
    for e in snap.capability_manifest:
        assert pat.match(e.ref.id)
    for w in snap.workers:
        assert pat.match(w.id)
        if w.system_prompt_ref:
            assert "/" not in w.system_prompt_ref.id and "\\" not in w.system_prompt_ref.id
