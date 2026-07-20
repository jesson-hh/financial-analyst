# -*- coding: utf-8 -*-
"""Phase 8 · Task 7 — Batch 4 · 跨切 xcut (2 deterministic cross-cut workers).

Covers (per brief Step 1):
* the WorkerSpec matrix per roster row (lane literal ``"xcut"`` — never the Task-0 map's
  documentation-only ``cross`` string — tier/exec-kind/tool policy/allowlist/guardrails/
  read_categories/output/inputs/decision authority + the per-seat EvidencePolicy), where
  ``x.number_critic`` carries ``require_number_anchors=False`` (it PRODUCES the anchor
  verdicts) and ``x.quality_gate`` carries ``require_number_anchors=True``;
* the ``cross``-literal firewall: no shipped WorkerSpec (id / lane / persona / any ref id)
  contains the string ``cross`` — the lane is ``"xcut"``;
* the deterministic invariants (``handler_ref`` present, no ``model_tier``/tier, no prompt)
  for both seats + the FORBIDDEN⇔empty-allowlist / ``can_emit_decision=False`` red lines;
* the promise/supply lint clean (``lint_skill_supply == ()``) for every shipped seat (both
  FORBIDDEN⇒empty allowlist⇒zero ww_ tools in the DSP);
* skill-v1 grammar (real ``parse_skill_v1``) + mirror byte identity for both;
* the Lane 跨切 payload schema matrices (``QualityComponent`` ABCDF band, ``DataQualityGrade``
  sorted+dup-free components, weakest-link overall grade, non-empty components);
* the two deterministic handlers: ``handler.x.quality_gate`` is an import-safe stdlib
  projection (clause-h impure fallback over the wired reports' honesty channels), while
  ``handler.x.number_critic`` binds ONLY the import-safe pure honesty spine (clause-h
  branch-1, like ``pv.price_action`` binds ``compute_pa_features``);
* THE EXIT-GATE PROOF: a fabricated-number fixture (anchor value != payload leaf) flows
  through ``handler.x.number_critic`` and is CAUGHT (subject verdict ``incomplete`` with a
  ``fabricated_number`` issue) — both through ``critique_subject`` and as the ``critique``
  primary; the multi-subject output is byte-stable under input reordering;
* the attribution surface stays forward-compatible: the emitted ``HonestyReport`` tuple is
  exactly ``honesty.attribution_candidates``'s input, with NO Task-9 ``DebateTranscript``
  coupling in the handler;
* the classify_worker cross-test (a FORBIDDEN offline xcut seat with zero calls + clean
  output is NOT killed — 合法无工具 worker 不因零调用被误杀);
* the legacy adapter regression (``IntrospectionProposal@1#confidence`` via
  ``migrate_confidence``): ``med`` stays UNMAPPABLE, ``low``/``high`` MAPPED, reverses exact
  — no ``med->medium`` mapping invented.
"""
from __future__ import annotations

import ast
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from guanlan_v2.orchestration import lane_catalog as lc
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration.capability_manifest import (
    MATERIAL_PATH as CAP_MANIFEST_PATH,
    lint_skill_supply,
    parse_capability_manifest,
)
from guanlan_v2.orchestration.catalog import (
    EvidencePolicy,
    ExecutionSpec,
    OutputBinding,
    WorkerSpec,
    canonical_json,
    parse_skill_v1,
)
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    MappingStatus,
    NodeStatus,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.honesty import (
    attribution_candidates,
    classify_worker,
)
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.schemas import Artifact, NodeRun, NumberAnchor, Provenance

_REPO = Path(__file__).resolve().parents[2]
_TREE = _REPO / "guanlan_v2" / "orchestration" / "skills"
_MIRROR = _REPO / "config" / "orchestration" / "materials" / "skills"
_HANDLERS = _REPO / "config" / "orchestration" / "materials" / "handlers"
_UTC = timezone.utc
_NOW = datetime(2026, 7, 20, 1, 30, tzinfo=_UTC)
_D64 = "a" * 64
_D64B = "b" * 64


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def specs() -> dict:
    materials = lc.load_xcut_lane_materials()
    return {w.id: w for w in lc.build_xcut_worker_specs(materials=materials)}


@pytest.fixture(scope="module")
def cap_manifest():
    return parse_capability_manifest(CAP_MANIFEST_PATH.read_bytes())


def _load_handler_module(name: str):
    """Load a handler material .py (dotted stem) by file path, as a fresh module."""
    path = _HANDLERS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace(".", "_") + "_handler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# --------------------------------------------------------------------------- #
# roster expectation (the reviewed field-for-field matrix)                     #
# --------------------------------------------------------------------------- #
EXPECTED = {
    "x.quality_gate": dict(
        kind="deterministic", tier="critic", model_tier=None, tool_calls="forbidden",
        caps=(),
        guardrails=("guardrail.number_provenance",),
        handler="handler.x.quality_gate", skill="skill.x.quality_gate", prompt=None,
        output="DataQualityGrade", read_categories=("upstream_artifacts", "market_data"),
        inputs=(("news_digest", "NewsDigestReport", "1", False, "one"),
                ("macro_pulse", "MacroPulseReport", "1", False, "one"),
                ("microstructure", "MicrostructureReport", "1", False, "one"),
                ("model_predictions", "ModelPredictionReport", "1", False, "one")),
        require_number_anchors=True,
        persona="Cross-cut data-quality ABCDF gate (xcut) — weakest-link, zero trading authority"),
    "x.number_critic": dict(
        kind="deterministic", tier="critic", model_tier=None, tool_calls="forbidden",
        caps=(),
        guardrails=("guardrail.number_provenance", "guardrail.untrusted_input_isolation"),
        handler="handler.x.number_critic", skill="skill.x.number_critic", prompt=None,
        output="HonestyReport", read_categories=("upstream_artifacts",),
        inputs=(("bull_case", "BullCase", "1", False, "one"),
                ("bear_case", "BearCase", "1", False, "one"),
                ("research_plan", "ResearchPlan", "1", False, "one"),
                ("portfolio_decision", "PortfolioDecision", "1", False, "one"),
                ("technical", "TechnicalReport", "1", False, "one"),
                ("fundamentals", "FundamentalsReport", "1", False, "one")),
        require_number_anchors=False,
        persona="Cross-cut number-provenance and honesty critic (xcut) — zero trading authority"),
}


def test_both_seats_present(specs):
    assert set(specs) == set(EXPECTED) == set(lc.XCUT_LANE_WORKER_IDS)


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_worker_spec_matrix(specs, wid):
    w = specs[wid]
    exp = EXPECTED[wid]
    # identity / role — the lane literal is "xcut", never "cross"
    assert w.catalog_role == "final"
    assert w.selection_scope == "dynamic_allowed"
    assert w.compatibility is None
    assert w.lane == "xcut"
    assert w.persona == exp["persona"]
    assert w.tier.value == exp["tier"]
    # execution matrix (deterministic ⇒ handler_ref, no tier, no prompt)
    assert w.execution.kind.value == exp["kind"]
    assert w.execution.handler_ref is not None
    assert w.execution.handler_ref.id == exp["handler"]
    assert w.execution.model_tier is None
    assert w.execution.thinking_budget is None
    assert w.system_prompt_ref is None
    # skills / guardrails / caps
    assert [sb.skill_ref.id for sb in w.skills] == [exp["skill"]]
    assert tuple(g.id for g in w.guardrail_refs) == exp["guardrails"]
    assert tuple(c.id for c in w.capability_allowlist) == exp["caps"]
    # read categories / output / inputs
    assert w.read_categories == exp["read_categories"]
    assert [o.name for o in w.outputs] == ["primary"]
    assert w.outputs[0].schema_ref.name == exp["output"]
    got_inputs = tuple(
        (i.name, i.schema_ref.name, i.schema_ref.version, i.required, i.cardinality)
        for i in w.inputs)
    assert got_inputs == exp["inputs"]
    # tool policy vs allowlist invariants — both FORBIDDEN ⇔ empty allowlist
    assert w.evidence_policy.tool_calls.value == exp["tool_calls"]
    assert w.capability_allowlist == ()
    # no cross-cut seat may emit a decision
    assert w.can_emit_decision is False
    assert w.decision_authority == "none"
    assert [m.value for m in w.supported_modes] == ["online", "pit_replay"]
    # evidence policy: per-seat require_number_anchors; unsourced OFF (never the
    # contradictory combo require_number_anchors=True + allow_unsourced_numbers=True)
    assert w.evidence_policy.require_number_anchors is exp["require_number_anchors"]
    assert w.evidence_policy.allow_unsourced_numbers is False
    assert w.evidence_policy.require_input_refs is True


def test_no_cross_literal_in_any_workerspec(specs):
    # exit-gate firewall: the Task-0 map's `cross` is documentation only; every shipped
    # WorkerSpec uses the `xcut` lane literal and never `cross` in any STRUCTURAL id (lane /
    # worker id / handler|skill|guardrail ref ids / schema names). (Persona prose may say
    # "Cross-cut" — the concern is the lane/id token, not English description.)
    for w in specs.values():
        assert w.lane == "xcut"
        structural = [w.id, w.execution.handler_ref.id]
        structural += [sb.skill_ref.id for sb in w.skills]
        structural += [g.id for g in w.guardrail_refs]
        structural += [c.id for c in w.capability_allowlist]
        structural += [o.schema_ref.name for o in w.outputs]
        structural += [i.schema_ref.name for i in w.inputs]
        for token in structural:
            assert "cross" not in token.lower(), f"{w.id}: `cross` leaked into {token!r}"
        # and the serialized spec carries the "xcut" lane literal
        assert '"xcut"' in canonical_json(w)


def test_both_deterministic_carry_handler_and_no_tier(specs):
    for wid in ("x.quality_gate", "x.number_critic"):
        w = specs[wid]
        assert w.execution.kind is ExecutionKind.DETERMINISTIC
        assert w.execution.handler_ref is not None and w.execution.model_tier is None
        assert w.system_prompt_ref is None


def test_both_forbidden_seats_have_empty_allowlist(specs):
    for wid in ("x.quality_gate", "x.number_critic"):
        w = specs[wid]
        assert w.evidence_policy.tool_calls is ToolCallRequirement.FORBIDDEN
        assert w.capability_allowlist == ()


def test_number_critic_produces_honesty_report_and_anchors_off(specs):
    # x.number_critic emits Task 2's HonestyReport and PRODUCES the anchor verdicts, so
    # its own EvidencePolicy sets require_number_anchors=False (it is not itself anchored).
    w = specs["x.number_critic"]
    assert w.outputs[0].schema_ref.name == "HonestyReport"
    assert w.outputs[0].schema_ref.version == "1"
    assert w.evidence_policy.require_number_anchors is False


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_promise_supply_lint_clean(specs, cap_manifest, wid):
    skill_text = (_TREE / wid / "SKILL.md").read_text(encoding="utf-8")
    issues = lint_skill_supply(
        manifest=cap_manifest, skill_text=skill_text,
        capability_allowlist=specs[wid].capability_allowlist)
    assert issues == (), issues


# --------------------------------------------------------------------------- #
# skill grammar + mirror byte identity                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_skill_grammar_and_mirror_identity(wid):
    tree_bytes = (_TREE / wid / "SKILL.md").read_bytes()
    parse_skill_v1(tree_bytes.decode("utf-8"))            # raises on any deviation
    assert not tree_bytes.startswith(b"\xef\xbb\xbf")      # no BOM
    mirror = _MIRROR / f"{wid}.md"
    assert mirror.read_bytes() == tree_bytes               # mirror is a verbatim build product


@pytest.mark.parametrize("wid", sorted(EXPECTED))
def test_forbidden_seats_name_no_ww_tool_in_dsp(wid):
    # FORBIDDEN ⇒ empty allowlist ⇒ the Data Source Priority must name zero ww_ tools.
    text = (_TREE / wid / "SKILL.md").read_text(encoding="utf-8")
    dsp = text.split("## ⚠️", 1)[1].split("## ", 1)[0]
    assert "ww_" not in dsp, wid


# --------------------------------------------------------------------------- #
# payload schema matrices                                                      #
# --------------------------------------------------------------------------- #
def test_quality_component_matrix():
    c = lp.QualityComponent(source_id="news_digest", grade="B", reason="single degradation")
    assert c.schema_version == "1" and c.grade == "B"
    # closed ABCDF band
    with pytest.raises(Exception):
        lp.QualityComponent(source_id="x", grade="E", reason="bad band")
    # non-blank reason + valid LogicalId source_id
    with pytest.raises(Exception):
        lp.QualityComponent(source_id="news_digest", grade="A", reason="  ")
    with pytest.raises(Exception):
        lp.QualityComponent(source_id="Bad Id", grade="A", reason="ok")


def test_data_quality_grade_matrix():
    a = lp.QualityComponent(source_id="macro_pulse", grade="C", reason="stale")
    b = lp.QualityComponent(source_id="news_digest", grade="A", reason="fresh")
    # sorted-by-source_id acceptance (macro_pulse < news_digest); overall = worst (C)
    rep = lp.DataQualityGrade(as_of=_NOW, grade="C", components=(a, b))
    assert rep.schema_version == "1"
    assert [c.source_id for c in rep.components] == ["macro_pulse", "news_digest"]
    # components must be sorted by source_id ...
    with pytest.raises(Exception):
        lp.DataQualityGrade(as_of=_NOW, grade="C", components=(b, a))   # unsorted
    # ... and duplicate-free by source_id
    dup = lp.QualityComponent(source_id="macro_pulse", grade="A", reason="x")
    with pytest.raises(Exception):
        lp.DataQualityGrade(as_of=_NOW, grade="A", components=(a, dup))
    # honest weakest-link: an overall BETTER than the worst component is rejected
    with pytest.raises(Exception):
        lp.DataQualityGrade(as_of=_NOW, grade="A", components=(a, b))   # worst is C, A is dishonest
    # a MORE conservative overall (worse than the worst component) is allowed
    conservative = lp.DataQualityGrade(as_of=_NOW, grade="F", components=(a, b))
    assert conservative.grade == "F"
    # at least one component (an ungrounded grade is meaningless)
    with pytest.raises(Exception):
        lp.DataQualityGrade(as_of=_NOW, grade="A", components=())


def test_lane_x_public_models_partition():
    # HonestyReport is Task 2's (owned by honesty.py), deliberately NOT a lane payload.
    assert set(lp.LANE_X_PUBLIC_MODELS) == {lp.QualityComponent, lp.DataQualityGrade}


# --------------------------------------------------------------------------- #
# handlers: import-safety (per clause-h branch)                                #
# --------------------------------------------------------------------------- #
def _top_imports(name: str) -> set[str]:
    tree = ast.parse((_HANDLERS / f"{name}.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:            # module top-level only (ignores docstring prose)
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    return imported


def test_quality_gate_handler_is_import_safe_stdlib_only():
    # clause (h) impure fallback: the datafeed.health collector is NOT import-safe (it reads
    # live freshness snapshots), so the grader is a self-contained stdlib projection.
    assert _top_imports("x.quality_gate") <= {"__future__", "math"}


def test_number_critic_handler_binds_only_the_pure_honesty_spine():
    # clause (h) branch-1: honesty.py IS import-safe/pure, so the critic binds it DIRECTLY
    # (like pv.price_action binds compute_pa_features) — nothing else.
    assert _top_imports("x.number_critic") <= {"__future__", "guanlan_v2.orchestration.honesty"}


# --------------------------------------------------------------------------- #
# handler.x.quality_gate — ABCDF weakest-link grading                          #
# --------------------------------------------------------------------------- #
def test_quality_gate_handler_grades_abcdf_weakest_link():
    h = _load_handler_module("x.quality_gate")
    # a clean fresh source ⇒ A; a single degradation ⇒ B; ≥2 degradations ⇒ C;
    # stale ⇒ C; a missing source ⇒ F
    clean = h.grade_source(source_id="news_digest")
    minor = h.grade_source(source_id="macro_pulse", degradation=("overseas feed down",))
    multi = h.grade_source(source_id="microstructure",
                           degradation=("orderbook down", "tape down"))
    stale = h.grade_source(source_id="model_predictions", stale_days=9)
    absent = h.grade_source(source_id="news_digest", missing=True)
    assert clean["grade"] == "A"
    assert minor["grade"] == "B"
    assert multi["grade"] == "C"
    assert stale["grade"] == "C"
    assert absent["grade"] == "F"
    # every grade carries a stated reason (never a band without evidence)
    for comp in (clean, minor, multi, stale, absent):
        assert comp["reason"].strip()
    # project: sorted + dup-free + overall = worst component (weakest-link)
    out = h.project([minor, stale, clean])    # macro_pulse B, model_predictions C, news_digest A
    assert out["grade"] == "C"                # worst of {B, C, A}
    assert [c["source_id"] for c in out["components"]] == \
        ["macro_pulse", "model_predictions", "news_digest"]   # sorted by source_id
    rep = lp.DataQualityGrade(
        as_of=_NOW, grade=out["grade"],
        components=tuple(lp.QualityComponent(**c) for c in out["components"]))
    assert rep.grade == "C"
    with pytest.raises(Exception):            # duplicate source_id is a caller bug
        h.project([clean, absent])            # both news_digest


# --------------------------------------------------------------------------- #
# EXIT GATE — the fabricated-number fixture flows through the handler + CAUGHT  #
# --------------------------------------------------------------------------- #
def _subject_worker(*, tool_calls=ToolCallRequirement.OPTIONAL) -> WorkerSpec:
    """A minimal deterministic subject WorkerSpec for the number critic to grade."""
    return WorkerSpec(
        id="pv.technical", catalog_role="final", selection_scope="dynamic_allowed",
        lane="pv", persona="subject under critique", tier=Tier.READER,
        execution=ExecutionSpec(
            kind=ExecutionKind.DETERMINISTIC,
            handler_ref=ContentRef(id="handler.pv.x", version="1", content_digest=_D64)),
        capability_allowlist=(),
        outputs=(OutputBinding(name="primary",
                               schema_ref=SchemaRef(name="TechnicalReport", version="1")),),
        evidence_policy=EvidencePolicy(
            tool_calls=tool_calls, require_input_refs=True, require_number_anchors=True,
            allow_unsourced_numbers=False, optional_data_may_degrade=True),
        supported_modes=(DataMode.ONLINE,),
        can_emit_decision=False, decision_authority="none")


def _subject_node_run() -> NodeRun:
    return NodeRun(
        node_run_id="nr-sub", run_id="run-1", plan_id="plan-1", plan_digest=_D64,
        node_id="pv", worker_id="pv.technical", status=NodeStatus.COMPLETED,
        attempt_id="att-1", input_snapshot_digest="1" * 64, tool_call_records=(),
        output_keys=("primary",), output_artifact_ids=("art-sub",))


def _subject_artifact(*, payload, numbers) -> Artifact:
    return Artifact.build(
        artifact_id="art-sub", run_id="run-1", created_at=_NOW,
        producer_node_id="pv", slot="pv", output_key="primary", kind="technical_report",
        payload_schema_ref=SchemaRef(name="TechnicalReport", version="1"),
        payload=payload, rendered_md="# subject", input_refs=(),
        provenance=Provenance(plan_digest=_D64, code_version="git:abc",
                              as_of=_NOW, pit_mode=DataMode.ONLINE),
        numbers=numbers, badges=())


def _fabricated_subject():
    # anchor DECLARES 9.99 but the payload leaf `score` is 0.74 ⇒ 编数 (fabrication).
    art = _subject_artifact(
        payload={"score": 0.74},
        numbers=(NumberAnchor(label="score", value=9.99, payload_path="score",
                              source_artifact_id="in-1", is_unsourced=False),))
    return (_subject_worker(), _subject_node_run(), art)


def _clean_subject():
    art = _subject_artifact(
        payload={"score": 0.5},
        numbers=(NumberAnchor(label="score", value=0.5, payload_path="score",
                              source_artifact_id="in-1", is_unsourced=False),))
    return (_subject_worker(), _subject_node_run(), art)


def test_number_critic_handler_catches_fabricated_number():
    """EXIT GATE: a fabricated-number fixture flows through the handler and is CAUGHT."""
    h = _load_handler_module("x.number_critic")
    w, nr, art = _fabricated_subject()
    rep = h.critique_subject(worker=w, node_run=nr, artifact=art)
    assert rep.verdict == "incomplete"
    assert "fabricated_number" in {i.code for i in rep.issues}
    # and it surfaces as the multi-subject critique PRIMARY (first violating subject)
    subjects = [("bear_case", *_clean_subject()), ("bull_case", *_fabricated_subject())]
    primary, reports = h.critique(subjects)
    assert primary.verdict == "incomplete"
    assert "fabricated_number" in {i.code for i in primary.issues}
    assert len(reports) == 2


def test_number_critic_handler_is_byte_stable_under_input_reorder():
    h = _load_handler_module("x.number_critic")
    subjects = [("bear_case", *_clean_subject()), ("bull_case", *_fabricated_subject())]
    p1, r1 = h.critique(subjects)
    p2, r2 = h.critique(list(reversed(subjects)))
    assert p1.semantic_digest() == p2.semantic_digest()
    assert tuple(r.semantic_digest() for r in r1) == tuple(r.semantic_digest() for r in r2)


def test_number_critic_attribution_surface_is_forward_compatible():
    # the emitted HonestyReport tuple IS honesty.attribution_candidates's input (spec §6.3):
    # the non-ok subset, node_id-sorted — with NO Task-9 DebateTranscript coupling.
    h = _load_handler_module("x.number_critic")
    subjects = [("bear_case", *_clean_subject()), ("bull_case", *_fabricated_subject())]
    _primary, reports = h.critique(subjects)
    cands = h.attribution(reports)
    assert cands == attribution_candidates(reports)            # exactly the §6.3 hook
    assert all(r.verdict != "ok" for r in cands) and len(cands) == 1
    # forward-compatibility: the EXECUTABLE handler code (imports + identifiers, docstring
    # prose excluded) references NO Task-9 DebateTranscript / debate type — no premature
    # coupling. (Docstrings legitimately explain the later, additive join.)
    tree = ast.parse((_HANDLERS / "x.number_critic.py").read_text(encoding="utf-8"))
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.add(node.name)
            if node.asname:
                identifiers.add(node.asname)
        elif isinstance(node, ast.ImportFrom):
            identifiers.add(node.module or "")
    assert not any("debate" in ident.lower() for ident in identifiers), identifiers


# --------------------------------------------------------------------------- #
# classify_worker cross-test: a FORBIDDEN offline xcut seat with zero calls OK  #
# --------------------------------------------------------------------------- #
def _min_node_run(worker_id: str) -> NodeRun:
    return NodeRun(
        node_run_id="nr-1", run_id="run-1", plan_id="plan-1", plan_digest=_D64,
        node_id="xcut", worker_id=worker_id, status=NodeStatus.COMPLETED,
        attempt_id="att-1", input_snapshot_digest="1" * 64, tool_call_records=(),
        output_keys=("primary",), output_artifact_ids=("art-1",))


def _min_artifact(schema_name: str) -> Artifact:
    return Artifact.build(
        artifact_id="art-1", run_id="run-1", created_at=_NOW,
        producer_node_id="xcut", slot="xcut", output_key="primary", kind="quality_grade",
        payload_schema_ref=SchemaRef(name=schema_name, version="1"),
        payload={"note": "ok"}, rendered_md="# out", input_refs=(),
        provenance=Provenance(plan_digest=_D64, code_version="git:abc",
                              as_of=_NOW, pit_mode=DataMode.ONLINE),
        numbers=(), badges=())


def test_forbidden_offline_quality_gate_zero_calls_is_not_killed(specs):
    # x.quality_gate is FORBIDDEN with no REQUIRED input; a zero-tool-call run that
    # produced a non-empty payload is a legitimate offline run — never incomplete.
    rep = classify_worker(
        worker=specs["x.quality_gate"], node_run=_min_node_run("x.quality_gate"),
        artifact=_min_artifact("DataQualityGrade"))
    assert rep.verdict == "ok"
    assert "required_tools_zero_calls" not in {i.code for i in rep.issues}


# --------------------------------------------------------------------------- #
# legacy adapter regression — IntrospectionProposal confidence, frozen verdicts  #
# --------------------------------------------------------------------------- #
def test_introspection_confidence_scalars_replay_frozen_and_reverse_exact():
    # low / high are authoritative identity (MAPPED); 'med' stays UNMAPPABLE (frozen, not
    # re-mapped — no med->medium alias invented); every reverse returns the exact raw.
    lo = M.migrate_confidence("low", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    hi = M.migrate_confidence("high", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert lo.mapping_status is MappingStatus.MAPPED
    assert hi.mapping_status is MappingStatus.MAPPED
    assert M.confidence_to_legacy(lo) == "low" and M.confidence_to_legacy(hi) == "high"
    med = M.migrate_confidence("med", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert med.mapping_status is MappingStatus.UNMAPPABLE and med.normalized is None
    assert M.confidence_to_legacy(med) == "med"
