# -*- coding: utf-8 -*-
"""Task 9A — prompt/skill/capability refs and runnable Worker catalog ABI.

Written test-first (RED before ``catalog.py`` exists). Locks the field-level v1
ABI + invariants from ``task-9A-brief.md``:

* the LLM/deterministic execution conditional matrix and deterministic
  no-prompt/no-skill success;
* no-tool success, forbidden-tool and required-tool/empty-allowlist rejection;
* duplicate / unknown ref rejection;
* declared-digest, missing/duplicate material and descriptor/manifest mismatch
  rejection;
* manifest order independence;
* material-digest normalization (LF/CRLF + NFC-equivalent match; trailing
  newline / descriptor change differ);
* path-like refs cannot enter a WorkerSpec;
* an incomplete planned worker cannot enter a runnable snapshot;
* the exact skill-v1 three-line grammar (+ duplicate/extra key, trigger
  escaping/order, critical-opening rejection);
* separate params/input/output bindings + duplicate binding-name rejection;
* the final/compatibility role/ID/scope/binding matrix and final-24 counting
  that ignores ``compat.*``.

Run from repo root: ``pytest tests/orchestration/test_catalog.py -v``
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import canonical_json
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import CapabilityRef, ContentRef, SchemaRef
from guanlan_v2.orchestration.catalog import (
    CapabilityDescriptor,
    CapabilityManifestEntry,
    CatalogError,
    CompatibilityBinding,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    ParsedSkillV1,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    SkillBinding,
    SkillFormatError,
    SkillManifest,
    WorkerCatalogSnapshot,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
    count_final_workers,
    parse_skill_v1,
    validate_catalog_snapshot,
)

DIGEST0 = "0" * 64
SREF_IN = SchemaRef(name="InputModel", version="1")
SREF_OUT = SchemaRef(name="OutputModel", version="1")
SREF_PARAMS = SchemaRef(name="ParamsModel", version="1")


# --------------------------------------------------------------------------- #
# Fixture builders                                                            #
# --------------------------------------------------------------------------- #
def make_text(id: str, kind: str, text: str, version: str = "1"):
    """Return a (ContentRef, ResolvedTextMaterial) pair with a correct digest."""
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=id, version=version, content_digest=DIGEST0),
        kind=kind,
        raw_utf8=raw,
    )
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=id, version=version, content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def make_capability(
    id: str,
    *,
    capability_kind: str = "tool",
    transport: str = "in_process",
    operation: str = "op.run",
    version: str = "1",
):
    """Return a (CapabilityRef, ResolvedCapabilityMaterial, CapabilityDescriptor)."""
    desc = CapabilityDescriptor(
        id=id,
        version=version,
        capability_kind=capability_kind,
        transport=transport,
        operation=operation,
        input_schema_ref=SREF_IN,
        output_schema_ref=SREF_OUT,
    )
    tmp = ResolvedCapabilityMaterial(
        ref=CapabilityRef(id=id, version=version, content_digest=DIGEST0), descriptor=desc
    )
    digest = catalog_material_digest(tmp)
    ref = CapabilityRef(id=id, version=version, content_digest=digest)
    return ref, ResolvedCapabilityMaterial(ref=ref, descriptor=desc), desc


def render_skill(
    *,
    name: str = "deep dive",
    summary: str = "Multi-source A-share deep dive.",
    perfect_for=("high-conviction names", "earnings season"),
    not_ideal_for=("intraday scalping",),
    sources=("get_verified_snapshot", "tushare daily", "eastmoney fundflow"),
) -> str:
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
    body = "## ⚠️ CRITICAL: Data Source Priority\n" + "".join(
        f"- {s}\n" for s in sources
    )
    return fm + body


def make_skill(id: str, *, source_identity: str = "ta.deep_dive", version: str = "1", **kw):
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


def llm_execution(tier="reasoner", budget=None):
    return ExecutionSpec(kind=ExecutionKind.LLM, model_tier=tier, thinking_budget=budget)


def det_execution(handler_ref):
    return ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler_ref)


PRIMARY_OUT = OutputBinding(name="primary", schema_ref=SREF_OUT)


def base_llm_worker(
    id="text.macro",
    *,
    prompt_ref,
    skills=(),
    guardrail_refs=(),
    capability_allowlist=(),
    evidence_policy=None,
    supported_modes=(DataMode.ONLINE,),
    can_emit_decision=False,
    decision_authority="none",
    lane="text",
    outputs=(PRIMARY_OUT,),
    inputs=(),
    catalog_role="final",
    selection_scope="dynamic_allowed",
    compatibility=None,
    read_categories=(),
    borrowed_from=(),
    params_schema_ref=None,
):
    return WorkerSpec(
        id=id,
        catalog_role=catalog_role,
        selection_scope=selection_scope,
        compatibility=compatibility,
        lane=lane,
        persona="Macro strategist",
        tier=Tier.WRITER,
        execution=llm_execution(),
        system_prompt_ref=prompt_ref,
        skills=tuple(skills),
        guardrail_refs=tuple(guardrail_refs),
        capability_allowlist=tuple(capability_allowlist),
        read_categories=tuple(read_categories),
        params_schema_ref=params_schema_ref,
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        evidence_policy=evidence_policy or EvidencePolicy(),
        supported_modes=tuple(supported_modes),
        can_emit_decision=can_emit_decision,
        decision_authority=decision_authority,
        borrowed_from=tuple(borrowed_from),
    )


def golden_catalog():
    """A minimal but complete, valid runnable snapshot with one LLM worker."""
    prompt_ref, prompt_mat = make_text("text.macro.prompt", "prompt", "You are the macro strategist.")
    guard_ref, guard_mat = make_text("guard.provenance", "guardrail", "Cite every number.")
    skill_ref, skill_mat, skill_man = make_skill("skill.macro")
    cap_ref, cap_mat, cap_desc = make_capability("cap.get_prediction_markets")

    worker = base_llm_worker(
        prompt_ref=prompt_ref,
        skills=(SkillBinding(skill_ref=skill_ref),),
        guardrail_refs=(guard_ref,),
        capability_allowlist=(cap_ref,),
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.REQUIRED),
        read_categories=("context", "market_data"),
    )
    content_manifest = (
        ContentManifestEntry(
            ref=prompt_ref, kind="prompt", name="Macro prompt",
            description="Role + boundary.", source_identity="guanlan.macro.pulse",
        ),
        ContentManifestEntry(
            ref=guard_ref, kind="guardrail", name="Provenance guard",
            description="Number anchors.", source_identity="guanlan.introspector",
        ),
    )
    capability_manifest = (
        CapabilityManifestEntry(ref=cap_ref, capability_kind="tool", transport="in_process"),
    )
    snapshot = build_catalog_snapshot(
        catalog_version="2026.07-pilot",
        content_manifest=content_manifest,
        skill_manifest=(skill_man,),
        capability_manifest=capability_manifest,
        workers=(worker,),
        resolved_material=(prompt_mat, guard_mat, skill_mat, cap_mat),
    )
    return snapshot


# --------------------------------------------------------------------------- #
# 1. LLM / deterministic conditional matrix                                   #
# --------------------------------------------------------------------------- #
def test_llm_execution_requires_model_tier():
    with pytest.raises(ValidationError):
        ExecutionSpec(kind=ExecutionKind.LLM, model_tier=None)


def test_llm_execution_defaults_thinking_budget_to_zero():
    ex = ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner")
    assert ex.thinking_budget == 0


def test_llm_execution_forbids_handler_ref():
    handler_ref, _ = make_text("handler.x", "handler", "def run(): ...")
    with pytest.raises(ValidationError):
        ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner", handler_ref=handler_ref)


def test_deterministic_requires_handler_and_forbids_tier_and_budget():
    handler_ref, _ = make_text("handler.q", "handler", "def run(): ...")
    ok = ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler_ref)
    assert ok.model_tier is None and ok.thinking_budget is None
    with pytest.raises(ValidationError):  # no handler
        ExecutionSpec(kind=ExecutionKind.DETERMINISTIC)
    with pytest.raises(ValidationError):  # tier forbidden
        ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler_ref, model_tier="fast")
    with pytest.raises(ValidationError):  # budget forbidden
        ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler_ref, thinking_budget=5)


def test_llm_worker_requires_prompt_ref():
    with pytest.raises(ValidationError):
        base_llm_worker(prompt_ref=None)


def test_negative_thinking_budget_rejected():
    with pytest.raises(ValidationError):
        ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner", thinking_budget=-1)


# --------------------------------------------------------------------------- #
# 2. Deterministic no-prompt / no-skill success                               #
# --------------------------------------------------------------------------- #
def test_deterministic_worker_needs_no_prompt_or_skill():
    handler_ref, handler_mat = make_text("handler.quant_factor", "handler", "compute ic")
    worker = WorkerSpec(
        id="quant.factor",
        catalog_role="final",
        selection_scope="dynamic_allowed",
        lane="quant",
        persona="Deterministic factor IC",
        tier=Tier.READER,
        execution=det_execution(handler_ref),
        system_prompt_ref=None,
        skills=(),
        outputs=(PRIMARY_OUT,),
        supported_modes=(DataMode.ONLINE, DataMode.PIT_REPLAY),
        can_emit_decision=False,
        decision_authority="none",
    )
    assert worker.system_prompt_ref is None and worker.skills == ()

    content_manifest = (
        ContentManifestEntry(
            ref=handler_ref, kind="handler", name="factor handler",
            description="ic compute", source_identity="guanlan.factor_ic",
        ),
    )
    snap = build_catalog_snapshot(
        catalog_version="v",
        content_manifest=content_manifest,
        skill_manifest=(),
        capability_manifest=(),
        workers=(worker,),
        resolved_material=(handler_mat,),
    )
    validate_catalog_snapshot(snap)


# --------------------------------------------------------------------------- #
# 3. Tool-call matrix                                                         #
# --------------------------------------------------------------------------- #
def test_no_tool_worker_completes_with_empty_allowlist():
    prompt_ref, _ = make_text("p.notool", "prompt", "no tools")
    worker = base_llm_worker(
        prompt_ref=prompt_ref,
        capability_allowlist=(),
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.OPTIONAL),
    )
    assert worker.capability_allowlist == ()


def test_forbidden_tool_calls_require_empty_allowlist():
    prompt_ref, _ = make_text("p.forbid", "prompt", "x")
    cap_ref, _, _ = make_capability("cap.z")
    with pytest.raises(ValidationError):
        base_llm_worker(
            prompt_ref=prompt_ref,
            capability_allowlist=(cap_ref,),
            evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.FORBIDDEN),
        )


def test_required_tool_calls_reject_empty_allowlist():
    prompt_ref, _ = make_text("p.req", "prompt", "x")
    with pytest.raises(ValidationError):
        base_llm_worker(
            prompt_ref=prompt_ref,
            capability_allowlist=(),
            evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.REQUIRED),
        )


# --------------------------------------------------------------------------- #
# 4. Duplicate / unknown ref rejection                                        #
# --------------------------------------------------------------------------- #
def test_unknown_worker_ref_rejected():
    snap = golden_catalog()
    # A worker referencing a prompt ref that is not in any manifest.
    ghost_ref, _ = make_text("ghost.prompt", "prompt", "unknown")
    bad_worker = base_llm_worker(id="text.ghost", prompt_ref=ghost_ref)
    tampered = snap.model_copy(update={"workers": (bad_worker,)})
    with pytest.raises(CatalogError):
        validate_catalog_snapshot(tampered)


def test_duplicate_manifest_ref_rejected():
    snap = golden_catalog()
    dup = snap.content_manifest[0]
    tampered = WorkerCatalogSnapshot.model_construct(
        schema_version="1",
        catalog_version=snap.catalog_version,
        content_manifest=(dup, dup),
        skill_manifest=snap.skill_manifest,
        capability_manifest=snap.capability_manifest,
        workers=snap.workers,
        catalog_digest=snap.catalog_digest,
    )
    with pytest.raises(CatalogError):
        validate_catalog_snapshot(tampered)


def test_worker_guardrail_refs_must_be_unique():
    prompt_ref, _ = make_text("p.dupguard", "prompt", "x")
    guard_ref, _ = make_text("g.dup", "guardrail", "rule")
    with pytest.raises(ValidationError):
        base_llm_worker(prompt_ref=prompt_ref, guardrail_refs=(guard_ref, guard_ref))


# --------------------------------------------------------------------------- #
# 5. Declared digest / missing / duplicate material / descriptor mismatch     #
# --------------------------------------------------------------------------- #
def test_declared_digest_mismatch_rejected():
    prompt_ref, prompt_mat = make_text("p.dig", "prompt", "hello")
    wrong_ref = ContentRef(id="p.dig", version="1", content_digest="1" * 64)
    worker = base_llm_worker(prompt_ref=wrong_ref)
    with pytest.raises(CatalogError):
        build_catalog_snapshot(
            catalog_version="v",
            content_manifest=(
                ContentManifestEntry(
                    ref=wrong_ref, kind="prompt", name="p", description="d",
                    source_identity="s.x",
                ),
            ),
            skill_manifest=(),
            capability_manifest=(),
            workers=(worker,),
            resolved_material=(prompt_mat,),
        )


def test_missing_material_rejected():
    prompt_ref, _prompt_mat = make_text("p.missing", "prompt", "hi")
    worker = base_llm_worker(prompt_ref=prompt_ref)
    with pytest.raises(CatalogError):
        build_catalog_snapshot(
            catalog_version="v",
            content_manifest=(
                ContentManifestEntry(
                    ref=prompt_ref, kind="prompt", name="p", description="d",
                    source_identity="s.x",
                ),
            ),
            skill_manifest=(),
            capability_manifest=(),
            workers=(worker,),
            resolved_material=(),  # material missing
        )


def test_duplicate_material_rejected():
    prompt_ref, prompt_mat = make_text("p.dupmat", "prompt", "hi")
    worker = base_llm_worker(prompt_ref=prompt_ref)
    with pytest.raises(CatalogError):
        build_catalog_snapshot(
            catalog_version="v",
            content_manifest=(
                ContentManifestEntry(
                    ref=prompt_ref, kind="prompt", name="p", description="d",
                    source_identity="s.x",
                ),
            ),
            skill_manifest=(),
            capability_manifest=(),
            workers=(worker,),
            resolved_material=(prompt_mat, prompt_mat),  # duplicate
        )


def test_content_manifest_kind_must_match_material():
    prompt_ref, prompt_mat = make_text("p.kind", "prompt", "hi")
    worker = base_llm_worker(prompt_ref=prompt_ref)
    with pytest.raises(CatalogError):
        build_catalog_snapshot(
            catalog_version="v",
            content_manifest=(
                ContentManifestEntry(
                    ref=prompt_ref, kind="guardrail",  # lies about kind
                    name="p", description="d", source_identity="s.x",
                ),
            ),
            skill_manifest=(),
            capability_manifest=(),
            workers=(worker,),
            resolved_material=(prompt_mat,),
        )


def test_capability_descriptor_manifest_transport_mismatch_rejected():
    prompt_ref, prompt_mat = make_text("p.cap", "prompt", "x")
    cap_ref, cap_mat, _ = make_capability("cap.mcp", transport="mcp")
    worker = base_llm_worker(
        prompt_ref=prompt_ref,
        capability_allowlist=(cap_ref,),
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.REQUIRED),
    )
    with pytest.raises(CatalogError):
        build_catalog_snapshot(
            catalog_version="v",
            content_manifest=(
                ContentManifestEntry(
                    ref=prompt_ref, kind="prompt", name="p", description="d",
                    source_identity="s.x",
                ),
            ),
            skill_manifest=(),
            capability_manifest=(
                CapabilityManifestEntry(
                    ref=cap_ref, capability_kind="tool",
                    transport="in_process",  # descriptor says mcp
                ),
            ),
            workers=(worker,),
            resolved_material=(prompt_mat, cap_mat),
        )


# --------------------------------------------------------------------------- #
# 6. Manifest order independence                                              #
# --------------------------------------------------------------------------- #
def test_catalog_digest_independent_of_manifest_registration_order():
    p1, m1 = make_text("a.prompt", "prompt", "A")
    p2, m2 = make_text("b.prompt", "prompt", "B")
    w1 = base_llm_worker(id="w.a", prompt_ref=p1)
    w2 = base_llm_worker(id="w.b", prompt_ref=p2)
    e1 = ContentManifestEntry(ref=p1, kind="prompt", name="a", description="d", source_identity="s.a")
    e2 = ContentManifestEntry(ref=p2, kind="prompt", name="b", description="d", source_identity="s.b")

    snap_fwd = build_catalog_snapshot(
        catalog_version="v",
        content_manifest=(e1, e2),
        skill_manifest=(),
        capability_manifest=(),
        workers=(w1, w2),
        resolved_material=(m1, m2),
    )
    snap_rev = build_catalog_snapshot(
        catalog_version="v",
        content_manifest=(e2, e1),
        skill_manifest=(),
        capability_manifest=(),
        workers=(w2, w1),
        resolved_material=(m2, m1),
    )
    assert snap_fwd.catalog_digest == snap_rev.catalog_digest


# --------------------------------------------------------------------------- #
# 7. Material-digest normalization                                            #
# --------------------------------------------------------------------------- #
def _prompt_digest(text: str) -> str:
    return catalog_material_digest(
        ResolvedTextMaterial(
            ref=ContentRef(id="x.p", version="1", content_digest=DIGEST0),
            kind="prompt",
            raw_utf8=text.encode("utf-8"),
        )
    )


def test_crlf_and_cr_normalize_to_lf():
    assert _prompt_digest("line1\r\nline2") == _prompt_digest("line1\nline2")
    assert _prompt_digest("line1\rline2") == _prompt_digest("line1\nline2")


def test_nfc_equivalent_unicode_matches():
    composed = "café"          # é as U+00E9
    decomposed = "café"       # e + combining acute
    assert _prompt_digest(composed) == _prompt_digest(decomposed)


def test_trailing_newline_changes_digest():
    assert _prompt_digest("body") != _prompt_digest("body\n")


def test_bom_rejected():
    with pytest.raises(ValueError):
        catalog_material_digest(
            ResolvedTextMaterial(
                ref=ContentRef(id="x.bom", version="1", content_digest=DIGEST0),
                kind="prompt",
                raw_utf8=b"\xef\xbb\xbfhello",
            )
        )


def test_content_drift_changes_catalog_digest_but_crlf_does_not():
    def build(text, newline):
        raw = text.replace("\n", newline)
        pref, pmat = make_text("drift.prompt", "prompt", raw)
        # make_text encodes/normalizes: recompute against the *raw* variant.
        pmat = ResolvedTextMaterial(ref=pref, kind="prompt", raw_utf8=raw.encode("utf-8"))
        # rebuild ref with digest of this exact raw
        digest = catalog_material_digest(pmat)
        pref = ContentRef(id="drift.prompt", version="1", content_digest=digest)
        pmat = ResolvedTextMaterial(ref=pref, kind="prompt", raw_utf8=raw.encode("utf-8"))
        w = base_llm_worker(id="w.drift", prompt_ref=pref)
        e = ContentManifestEntry(ref=pref, kind="prompt", name="n", description="d", source_identity="s.d")
        return build_catalog_snapshot(
            catalog_version="v", content_manifest=(e,), skill_manifest=(),
            capability_manifest=(), workers=(w,), resolved_material=(pmat,),
        ).catalog_digest

    lf = build("alpha\nbeta", "\n")
    crlf = build("alpha\nbeta", "\r\n")
    drift = build("alpha\nGAMMA", "\n")
    assert lf == crlf
    assert lf != drift


def test_capability_descriptor_change_changes_material_digest():
    _, mat_a, _ = make_capability("cap.same", transport="in_process")
    _, mat_b, _ = make_capability("cap.same", transport="mcp")
    assert catalog_material_digest(mat_a) != catalog_material_digest(mat_b)


# --------------------------------------------------------------------------- #
# 8. Path-like refs cannot enter a WorkerSpec                                  #
# --------------------------------------------------------------------------- #
def test_path_like_content_ref_id_rejected():
    with pytest.raises(ValidationError):
        ContentRef(id="prompts/text_macro.md", version="1", content_digest=DIGEST0)
    with pytest.raises(ValidationError):
        ContentRef(id="C:\\prompts\\x", version="1", content_digest=DIGEST0)


def test_worker_rejects_raw_string_prompt_ref():
    with pytest.raises(ValidationError):
        base_llm_worker(prompt_ref="prompts/macro.md")


# --------------------------------------------------------------------------- #
# 9. Incomplete planned worker cannot enter a runnable snapshot               #
# --------------------------------------------------------------------------- #
def test_incomplete_planned_worker_cannot_be_constructed():
    # A Task-0 planning row (id + lane only) is not a runnable WorkerSpec.
    with pytest.raises(ValidationError):
        WorkerSpec(id="market.factor", lane="market")  # type: ignore[call-arg]


def test_worker_requires_exactly_one_primary_output():
    prompt_ref, _ = make_text("p.noprimary", "prompt", "x")
    with pytest.raises(ValidationError):
        base_llm_worker(prompt_ref=prompt_ref, outputs=(OutputBinding(name="aux", schema_ref=SREF_OUT),))


# --------------------------------------------------------------------------- #
# 10. skill-v1 grammar                                                        #
# --------------------------------------------------------------------------- #
def test_skill_v1_three_line_grammar_parses():
    parsed = parse_skill_v1(render_skill())
    assert isinstance(parsed, ParsedSkillV1)
    assert parsed.name == "deep dive"
    assert parsed.summary == "Multi-source A-share deep dive."
    assert parsed.perfect_for == ("high-conviction names", "earnings season")
    assert parsed.not_ideal_for == ("intraday scalping",)
    assert parsed.critical_data_source_heading == "⚠️ CRITICAL: Data Source Priority"
    assert parsed.source_priority[0] == "get_verified_snapshot"


def test_skill_v1_trigger_order_is_semantic():
    a = parse_skill_v1(render_skill(perfect_for=("x", "y")))
    b = parse_skill_v1(render_skill(perfect_for=("y", "x")))
    assert a.perfect_for == ("x", "y")
    assert b.perfect_for == ("y", "x")
    assert a.perfect_for != b.perfect_for


def test_skill_v1_non_canonical_trigger_json_rejected():
    bad = (
        "---\nname: n\ndescription: |\n"
        "  summary\n"
        '  Perfect for: ["a", "b"]\n'           # extra space => non-canonical
        '  Not ideal for: ["c"]\n'
        "---\n"
        "## ⚠️ CRITICAL: Data Source Priority\n- s\n"
    )
    with pytest.raises(SkillFormatError):
        parse_skill_v1(bad)


def test_skill_v1_duplicate_frontmatter_key_rejected():
    bad = (
        "---\nname: a\nname: b\ndescription: |\n"
        "  s\n  Perfect for: [\"x\"]\n  Not ideal for: [\"y\"]\n"
        "---\n## ⚠️ CRITICAL: Data Source Priority\n- s\n"
    )
    with pytest.raises(SkillFormatError):
        parse_skill_v1(bad)


def test_skill_v1_unknown_frontmatter_key_rejected():
    bad = (
        "---\nname: a\nextra: nope\ndescription: |\n"
        "  s\n  Perfect for: [\"x\"]\n  Not ideal for: [\"y\"]\n"
        "---\n## ⚠️ CRITICAL: Data Source Priority\n- s\n"
    )
    with pytest.raises(SkillFormatError):
        parse_skill_v1(bad)


def test_skill_v1_wrong_number_of_description_lines_rejected():
    bad = (
        "---\nname: a\ndescription: |\n"
        "  only-summary\n  Perfect for: [\"x\"]\n"   # missing not-ideal line
        "---\n## ⚠️ CRITICAL: Data Source Priority\n- s\n"
    )
    with pytest.raises(SkillFormatError):
        parse_skill_v1(bad)


def test_skill_v1_wrong_critical_opening_rejected():
    bad = (
        "---\nname: a\ndescription: |\n"
        "  s\n  Perfect for: [\"x\"]\n  Not ideal for: [\"y\"]\n"
        "---\n## Data Sources\n- s\n"              # wrong heading
    )
    with pytest.raises(SkillFormatError):
        parse_skill_v1(bad)


def test_skill_v1_empty_source_list_rejected():
    bad = (
        "---\nname: a\ndescription: |\n"
        "  s\n  Perfect for: [\"x\"]\n  Not ideal for: [\"y\"]\n"
        "---\n## ⚠️ CRITICAL: Data Source Priority\n"  # no list items
    )
    with pytest.raises(SkillFormatError):
        parse_skill_v1(bad)


def test_skill_manifest_mismatch_vs_document_rejected():
    ref, mat, _man = make_skill("skill.mm")
    wrong_manifest = SkillManifest(
        ref=ref, name="WRONG NAME", summary="Multi-source A-share deep dive.",
        format_version="skill-v1",
        perfect_for=("high-conviction names", "earnings season"),
        not_ideal_for=("intraday scalping",),
        critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
        source_identity="ta.deep_dive",
    )
    prompt_ref, prompt_mat = make_text("p.mm", "prompt", "x")
    worker = base_llm_worker(prompt_ref=prompt_ref, skills=(SkillBinding(skill_ref=ref),))
    with pytest.raises(CatalogError):
        build_catalog_snapshot(
            catalog_version="v",
            content_manifest=(
                ContentManifestEntry(ref=prompt_ref, kind="prompt", name="p", description="d", source_identity="s.x"),
            ),
            skill_manifest=(wrong_manifest,),
            capability_manifest=(),
            workers=(worker,),
            resolved_material=(prompt_mat, mat),
        )


# --------------------------------------------------------------------------- #
# 11. Separate params / input / output bindings + duplicate-name rejection    #
# --------------------------------------------------------------------------- #
def test_params_and_input_and_output_are_separate_schema_refs():
    prompt_ref, prompt_mat = make_text("p.sep", "prompt", "x")
    worker = base_llm_worker(
        prompt_ref=prompt_ref,
        params_schema_ref=SREF_PARAMS,
        inputs=(InputBinding(name="upstream", schema_ref=SREF_IN),),
        outputs=(PRIMARY_OUT,),
    )
    assert worker.params_schema_ref == SREF_PARAMS
    assert worker.inputs[0].schema_ref == SREF_IN
    assert worker.outputs[0].schema_ref == SREF_OUT
    # they are distinct SchemaRefs, not "Model@1" strings
    assert isinstance(worker.params_schema_ref, SchemaRef)


def test_duplicate_input_binding_name_rejected():
    prompt_ref, _ = make_text("p.din", "prompt", "x")
    with pytest.raises(ValidationError):
        base_llm_worker(
            prompt_ref=prompt_ref,
            inputs=(
                InputBinding(name="dup", schema_ref=SREF_IN),
                InputBinding(name="dup", schema_ref=SREF_OUT),
            ),
        )


def test_duplicate_output_binding_name_rejected():
    prompt_ref, _ = make_text("p.dout", "prompt", "x")
    with pytest.raises(ValidationError):
        base_llm_worker(
            prompt_ref=prompt_ref,
            outputs=(
                PRIMARY_OUT,
                OutputBinding(name="primary", schema_ref=SREF_IN),
            ),
        )


def test_input_binding_cardinality_default_and_many():
    b = InputBinding(name="upstream", schema_ref=SREF_IN)
    assert b.required is True and b.cardinality == "one"
    m = InputBinding(name="many_up", schema_ref=SREF_IN, cardinality="many", required=False)
    assert m.cardinality == "many" and m.required is False


# --------------------------------------------------------------------------- #
# 12. Final / compatibility matrix + final-24 counting ignores compat.*       #
# --------------------------------------------------------------------------- #
def _compat_binding():
    return CompatibilityBinding(
        legacy_source_schema=SchemaRef(name="LegacyConfig", version="1"),
        source_config_digest="a" * 64,
        legacy_mapping_digest="b" * 64,
    )


def compat_worker(id="compat.stock_deep_dive", *, prompt_ref):
    return WorkerSpec(
        id=id,
        catalog_role="compatibility",
        selection_scope="static_legacy_only",
        compatibility=_compat_binding(),
        lane="decision",
        persona="legacy mirror",
        tier=Tier.WRITER,
        execution=llm_execution(),
        system_prompt_ref=prompt_ref,
        outputs=(PRIMARY_OUT,),
        supported_modes=(DataMode.ONLINE,),
        can_emit_decision=False,
        decision_authority="none",
    )


def test_final_worker_rejects_compat_prefix_id():
    prompt_ref, _ = make_text("p.fp", "prompt", "x")
    with pytest.raises(ValidationError):
        base_llm_worker(id="compat.oops", prompt_ref=prompt_ref)


def test_final_worker_rejects_static_scope():
    prompt_ref, _ = make_text("p.fs", "prompt", "x")
    with pytest.raises(ValidationError):
        base_llm_worker(prompt_ref=prompt_ref, selection_scope="static_legacy_only")


def test_final_worker_rejects_compatibility_binding():
    prompt_ref, _ = make_text("p.fb", "prompt", "x")
    with pytest.raises(ValidationError):
        base_llm_worker(prompt_ref=prompt_ref, compatibility=_compat_binding())


def test_compatibility_worker_requires_binding_and_prefix_and_scope():
    prompt_ref, _ = make_text("p.cw", "prompt", "x")
    ok = compat_worker(prompt_ref=prompt_ref)
    assert ok.catalog_role == "compatibility"
    # missing binding
    with pytest.raises(ValidationError):
        WorkerSpec(
            id="compat.x", catalog_role="compatibility", selection_scope="static_legacy_only",
            compatibility=None, lane="decision", persona="p", tier=Tier.WRITER,
            execution=llm_execution(), system_prompt_ref=prompt_ref,
            outputs=(PRIMARY_OUT,), supported_modes=(DataMode.ONLINE,),
            can_emit_decision=False, decision_authority="none",
        )
    # non-compat id
    with pytest.raises(ValidationError):
        compat_worker(id="legacy.deep_dive", prompt_ref=prompt_ref)


def test_decision_authority_matrix():
    prompt_ref, _ = make_text("p.da", "prompt", "x")
    with pytest.raises(ValidationError):  # emit=True but authority none
        base_llm_worker(prompt_ref=prompt_ref, can_emit_decision=True, decision_authority="none")
    with pytest.raises(ValidationError):  # emit=False but advisory
        base_llm_worker(prompt_ref=prompt_ref, can_emit_decision=False, decision_authority="advisory_only")
    ok = base_llm_worker(prompt_ref=prompt_ref, can_emit_decision=True, decision_authority="advisory_only")
    assert ok.decision_authority == "advisory_only"


def test_final_24_count_ignores_compat_workers():
    p1, _ = make_text("p.c1", "prompt", "x")
    p2, _ = make_text("p.c2", "prompt", "y")
    final = base_llm_worker(id="dec.pm", prompt_ref=p1, can_emit_decision=True, decision_authority="advisory_only")
    compat = compat_worker(prompt_ref=p2)
    assert count_final_workers((final, compat)) == 1
    assert count_final_workers((compat,)) == 0


# --------------------------------------------------------------------------- #
# Golden snapshot sanity + round-trip validate                                #
# --------------------------------------------------------------------------- #
def test_golden_snapshot_builds_and_validates():
    snap = golden_catalog()
    assert isinstance(snap, WorkerCatalogSnapshot)
    validate_catalog_snapshot(snap)
    # digest is self-consistent and re-verified on reload
    reloaded = WorkerCatalogSnapshot.model_validate(snap.model_dump())
    assert reloaded.catalog_digest == snap.catalog_digest


def test_tampered_catalog_digest_rejected_on_validate():
    snap = golden_catalog()
    tampered = snap.model_construct(**{**snap.__dict__, "catalog_digest": "e" * 64})
    with pytest.raises(CatalogError):
        validate_catalog_snapshot(tampered)
