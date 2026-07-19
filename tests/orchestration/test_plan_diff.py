# -*- coding: utf-8 -*-
"""Phase 7 - Task 6: typed plan diff + deterministic reviewer rendering.

Covers ``guanlan_v2.orchestration.plan_diff``:

* :class:`PlanDiffEntry` / :class:`PlanDiff` change-matrix invariants;
* :func:`build_plan_diff` over :meth:`PlanDraft.executable_projection` (none /
  fallback-preset / prior-plan baselines; nodes diffed per node id; audit-only
  locators never appear);
* :func:`render_plan_diff_md` byte-deterministic markdown;
* :class:`PendingPlanApproval` construction via :func:`build_pending_plan_approval`
  (digest binding + tamper detection + AUTO unconstructible + preset-provenance
  both-direction validator).

Run: ``python -m pytest tests/orchestration/test_plan_diff.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    load_pilot_catalog,
)
from guanlan_v2.orchestration.enums import ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.spec import PlanDraft, PlanNode

from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PendingPlanApproval,
    PlanDiff,
    PlanDiffEntry,
    build_pending_plan_approval,
    build_plan_diff,
    render_plan_diff_md,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
D_A = "a" * 64
D_B = "b" * 64
RESEARCH_BASELINE = "main.research_baseline"


# --------------------------------------------------------------------------- #
# Reviewed pilot scaffolding (real Phase 1/2 builders; consumes nothing new).   #
# --------------------------------------------------------------------------- #
class _FixedClock:
    def now(self) -> datetime:
        return NOW


def _build_pieces(request_id: str = "req-plan-diff"):
    clock = _FixedClock()
    registry = default_registry()
    catalog_runtime = load_pilot_catalog()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_reg = phase2_runtime_registry(registry.registry_digest)
    rt_digest = resolver.register(rt_reg)
    stores = RuntimeStores(
        resolver=resolver, clock=clock,
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    request = P.preset_orchestration_request(request_id=request_id)
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()),
        stores=stores, registry_digest=rt_digest, built_at=clock.now())
    context = mem.context
    baseline = P.build_pilot_draft(
        catalog=catalog_runtime, registry=registry, context=context,
        request_id=request.request_id, run_id="run-plan-diff", as_of=clock.now())
    return request, context, baseline


def _reconstruct(draft: PlanDraft, **over) -> PlanDraft:
    """Reconstruct a draft through the REAL validators (never model_copy)."""
    return PlanDraft(**{**draft.model_dump(), **over})


def _candidate_with_node_change(baseline: PlanDraft, **over) -> PlanDraft:
    """Baseline pilot triad + sentiment params changed + one auxiliary node added."""
    nodes = list(baseline.nodes)
    sentiment2 = nodes[0].model_copy(update={"params": {"window": 5}})
    extra = PlanNode(
        id="extra", worker_id="text.sentiment", writes_slot="slot-extra",
        auxiliary=True)
    new_nodes = (sentiment2, nodes[1], nodes[2], extra)
    dumped = baseline.model_dump()
    dumped["nodes"] = tuple(n.model_dump() for n in new_nodes)
    dumped.update(over)
    return PlanDraft(**dumped)


@pytest.fixture(scope="module")
def pieces():
    return _build_pieces()


# =========================================================================== #
# build_plan_diff — none baseline (all-added)                                   #
# =========================================================================== #
def test_none_baseline_is_all_added(pieces):
    request, _context, baseline = pieces
    diff = build_plan_diff(
        baseline, request=request, candidate_plan_digest=D_A,
        baseline=None, baseline_kind="none")
    assert diff.baseline_kind == "none"
    assert diff.baseline_plan_digest is None
    assert diff.baseline_preset_id is None
    assert diff.candidate_plan_digest == D_A
    assert diff.request_digest == request.semantic_digest()
    # every entry is 'added'; baseline side is genuinely absent.
    assert diff.entries
    assert all(e.change == "added" for e in diff.entries)
    assert all(e.baseline_json is None and e.candidate_json is not None
               for e in diff.entries)
    # nodes are diffed per id, all added; none removed/changed.
    assert diff.nodes_added == tuple(sorted(n.id for n in baseline.nodes))
    assert diff.nodes_removed == () and diff.nodes_changed == ()
    pointers = {e.pointer for e in diff.entries}
    # a scalar executable field and every per-node pointer are present.
    assert "goal" in pointers and "source" in pointers
    for n in baseline.nodes:
        assert f"nodes.{n.id}" in pointers


def test_audit_only_fields_never_appear(pieces):
    request, _context, baseline = pieces
    diff = build_plan_diff(
        baseline, request=request, candidate_plan_digest=D_A,
        baseline=None, baseline_kind="none")
    pointers = {e.pointer for e in diff.entries}
    # plan/run identity + the context_snapshot_ref locator are audit-only.
    for banned in ("id", "run_id", "request_id", "context_snapshot_ref"):
        assert banned not in pointers


# =========================================================================== #
# build_plan_diff — preset baseline (node param change + node added)            #
# =========================================================================== #
def test_fallback_preset_baseline_node_param_change_and_add(pieces):
    request, _context, baseline = pieces
    candidate = _candidate_with_node_change(baseline)
    diff = build_plan_diff(
        candidate, request=request, candidate_plan_digest=D_A,
        baseline=baseline, baseline_kind="fallback_preset",
        baseline_preset_id=RESEARCH_BASELINE)
    assert diff.baseline_kind == "fallback_preset"
    assert diff.baseline_preset_id == RESEARCH_BASELINE
    assert diff.baseline_plan_digest is None
    # sentiment changed, extra added, research/pm unchanged.
    assert diff.nodes_changed == ("sentiment",)
    assert diff.nodes_added == ("extra",)
    assert diff.nodes_removed == ()
    by_pointer = {e.pointer: e for e in diff.entries}
    changed = by_pointer["nodes.sentiment"]
    assert changed.change == "changed"
    assert changed.baseline_json is not None and changed.candidate_json is not None
    assert changed.baseline_json != changed.candidate_json
    added = by_pointer["nodes.extra"]
    assert added.change == "added" and added.baseline_json is None
    # only node-level pointers differ (both drafts are PRESET, all else equal).
    assert set(by_pointer) == {"nodes.sentiment", "nodes.extra"}


def test_field_level_change_entry_for_scalar_field(pieces):
    request, _context, baseline = pieces
    candidate = _reconstruct(baseline, goal="a different reviewed goal")
    diff = build_plan_diff(
        candidate, request=request, candidate_plan_digest=D_A,
        baseline=baseline, baseline_kind="fallback_preset",
        baseline_preset_id=RESEARCH_BASELINE)
    goal_entry = next(e for e in diff.entries if e.pointer == "goal")
    assert goal_entry.change == "changed"
    assert goal_entry.baseline_json != goal_entry.candidate_json


# =========================================================================== #
# build_plan_diff — prior-plan baseline                                         #
# =========================================================================== #
def test_prior_plan_baseline_carries_digest(pieces):
    request, _context, baseline = pieces
    candidate = _candidate_with_node_change(baseline)
    diff = build_plan_diff(
        candidate, request=request, candidate_plan_digest=D_A,
        baseline=baseline, baseline_kind="prior_plan",
        baseline_plan_digest=D_B)
    assert diff.baseline_kind == "prior_plan"
    assert diff.baseline_plan_digest == D_B
    assert diff.baseline_preset_id is None
    assert diff.nodes_changed == ("sentiment",) and diff.nodes_added == ("extra",)


def test_build_plan_diff_rejects_baseline_kind_baseline_mismatch(pieces):
    request, _context, baseline = pieces
    # none-kind must have no baseline object.
    with pytest.raises(ValueError):
        build_plan_diff(baseline, request=request, candidate_plan_digest=D_A,
                        baseline=baseline, baseline_kind="none")
    # a comparative kind must be given a baseline.
    with pytest.raises(ValueError):
        build_plan_diff(baseline, request=request, candidate_plan_digest=D_A,
                        baseline=None, baseline_kind="prior_plan",
                        baseline_plan_digest=D_B)


def test_build_plan_diff_does_not_revalidate_or_mutate(pieces):
    request, _context, baseline = pieces
    before = baseline.semantic_digest()
    build_plan_diff(baseline, request=request, candidate_plan_digest=D_A,
                    baseline=None, baseline_kind="none")
    assert baseline.semantic_digest() == before  # frozen input untouched


# =========================================================================== #
# PlanDiffEntry / PlanDiff matrix violations                                    #
# =========================================================================== #
def test_entry_matrix_violations():
    with pytest.raises(ValidationError):  # added must have baseline None
        PlanDiffEntry(pointer="goal", change="added",
                      baseline_json="x", candidate_json="y")
    with pytest.raises(ValidationError):  # removed must have candidate None
        PlanDiffEntry(pointer="goal", change="removed",
                      baseline_json="x", candidate_json="y")
    with pytest.raises(ValidationError):  # changed must differ
        PlanDiffEntry(pointer="goal", change="changed",
                      baseline_json="x", candidate_json="x")
    with pytest.raises(ValidationError):  # changed needs both set
        PlanDiffEntry(pointer="goal", change="changed",
                      baseline_json=None, candidate_json="y")


def _added(pointer: str) -> PlanDiffEntry:
    return PlanDiffEntry(pointer=pointer, change="added",
                         baseline_json=None, candidate_json="v")


def test_plan_diff_matrix_none():
    with pytest.raises(ValidationError):  # none forbids a baseline digest
        PlanDiff(baseline_kind="none", request_digest=D_A,
                 candidate_plan_digest=D_B, baseline_plan_digest=D_A,
                 entries=(_added("goal"),))
    with pytest.raises(ValidationError):  # none requires all-added entries
        PlanDiff(baseline_kind="none", request_digest=D_A,
                 candidate_plan_digest=D_B,
                 entries=(PlanDiffEntry(pointer="goal", change="changed",
                                        baseline_json="x", candidate_json="y"),))


def test_plan_diff_matrix_fallback_and_prior():
    with pytest.raises(ValidationError):  # fallback needs a preset id
        PlanDiff(baseline_kind="fallback_preset", request_digest=D_A,
                 candidate_plan_digest=D_B)
    with pytest.raises(ValidationError):  # prior needs a baseline digest
        PlanDiff(baseline_kind="prior_plan", request_digest=D_A,
                 candidate_plan_digest=D_B)
    # a well-formed prior/fallback constructs.
    ok = PlanDiff(baseline_kind="prior_plan", request_digest=D_A,
                  candidate_plan_digest=D_B, baseline_plan_digest=D_A)
    assert ok.baseline_kind == "prior_plan"


def test_plan_diff_entries_must_be_sorted():
    with pytest.raises(ValidationError):
        PlanDiff(baseline_kind="none", request_digest=D_A,
                 candidate_plan_digest=D_B,
                 entries=(_added("zeta"), _added("alpha")))


# =========================================================================== #
# render_plan_diff_md — determinism                                            #
# =========================================================================== #
def test_render_is_byte_deterministic(pieces):
    request, _context, baseline = pieces
    candidate = _candidate_with_node_change(baseline)
    diff = build_plan_diff(
        candidate, request=request, candidate_plan_digest=D_A,
        baseline=baseline, baseline_kind="fallback_preset",
        baseline_preset_id=RESEARCH_BASELINE)
    first = render_plan_diff_md(diff)
    second = render_plan_diff_md(diff)
    assert first == second  # byte-identical
    assert "<" not in first and ">" not in first  # plain markdown, no HTML
    # full digests appear (not truncated).
    assert diff.candidate_plan_digest in first
    assert diff.request_digest in first
    assert "nodes.sentiment" in first and "nodes.extra" in first


def test_build_and_render_are_pure_functions_of_inputs(pieces):
    request, _context, baseline = pieces
    candidate = _candidate_with_node_change(baseline)
    d1 = build_plan_diff(candidate, request=request, candidate_plan_digest=D_A,
                         baseline=baseline, baseline_kind="prior_plan",
                         baseline_plan_digest=D_B)
    d2 = build_plan_diff(candidate, request=request, candidate_plan_digest=D_A,
                         baseline=baseline, baseline_kind="prior_plan",
                         baseline_plan_digest=D_B)
    assert d1.semantic_digest() == d2.semantic_digest()
    assert render_plan_diff_md(d1) == render_plan_diff_md(d2)


# =========================================================================== #
# build_pending_plan_approval — digest binding + tamper detection              #
# =========================================================================== #
def _diff_ref(diff: PlanDiff, *, schema_ref=None, content_digest=None) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=schema_ref if schema_ref is not None else PLAN_DIFF_SCHEMA_REF,
        payload_ref=PayloadRef(
            namespace="main", object_id="diff-1",
            content_digest=content_digest if content_digest is not None
            else diff.semantic_digest()))


def _dynamic_pending(pieces):
    request, _context, baseline = pieces
    candidate = _reconstruct(
        _candidate_with_node_change(baseline), source=PlanSource.DYNAMIC)
    diff = build_plan_diff(
        candidate, request=request, candidate_plan_digest=D_A,
        baseline=baseline, baseline_kind="fallback_preset",
        baseline_preset_id=RESEARCH_BASELINE)
    pending = build_pending_plan_approval(
        draft=candidate, request=request, candidate_plan_digest=D_A,
        diff=diff, plan_diff_ref=_diff_ref(diff), planner_rationale="looks fine",
        candidate_id="cand-1", requested_at=NOW)
    return candidate, diff, pending


def test_build_pending_binds_rendered_md_to_diff(pieces):
    candidate, diff, pending = _dynamic_pending(pieces)
    assert pending.source is PlanSource.DYNAMIC
    assert pending.approval_policy is ApprovalPolicy.REQUIRED
    assert pending.node_count == len(candidate.nodes)
    assert pending.worker_ids == tuple(sorted({n.worker_id for n in candidate.nodes}))
    assert pending.rendered_md == render_plan_diff_md(diff)
    assert pending.rendered_from_diff_digest == diff.semantic_digest()
    assert pending.plan_diff_ref.payload_ref.content_digest == diff.semantic_digest()
    # DYNAMIC card carries no preset provenance.
    assert pending.preset_id is None and pending.preset_record_digest is None


def test_tampered_markdown_is_detected(pieces):
    _candidate, diff, pending = _dynamic_pending(pieces)
    # the honest card re-renders exactly; a tampered copy does not.
    assert render_plan_diff_md(diff) == pending.rendered_md
    tampered = pending.model_copy(update={"rendered_md": pending.rendered_md + "\nHACK"})
    assert render_plan_diff_md(diff) != tampered.rendered_md
    # the sealed digest still binds the true diff, so the mismatch is provable.
    assert tampered.rendered_from_diff_digest == diff.semantic_digest()


def test_build_pending_rejects_wrong_schema_ref(pieces):
    request, _context, baseline = pieces
    candidate = _reconstruct(baseline, source=PlanSource.DYNAMIC)
    diff = build_plan_diff(candidate, request=request, candidate_plan_digest=D_A,
                           baseline=None, baseline_kind="none")
    bad = _diff_ref(diff, schema_ref=SchemaRef(name="WrongSchema", version="1"))
    with pytest.raises(ValueError):
        build_pending_plan_approval(
            draft=candidate, request=request, candidate_plan_digest=D_A,
            diff=diff, plan_diff_ref=bad, planner_rationale=None,
            candidate_id="cand-1", requested_at=NOW)


def test_build_pending_rejects_ref_digest_mismatch(pieces):
    request, _context, baseline = pieces
    candidate = _reconstruct(baseline, source=PlanSource.DYNAMIC)
    diff = build_plan_diff(candidate, request=request, candidate_plan_digest=D_A,
                           baseline=None, baseline_kind="none")
    bad = _diff_ref(diff, content_digest=D_B)
    with pytest.raises(ValueError):
        build_pending_plan_approval(
            draft=candidate, request=request, candidate_plan_digest=D_A,
            diff=diff, plan_diff_ref=bad, planner_rationale=None,
            candidate_id="cand-1", requested_at=NOW)


def test_build_pending_rejects_candidate_digest_mismatch(pieces):
    request, _context, baseline = pieces
    candidate = _reconstruct(baseline, source=PlanSource.DYNAMIC)
    diff = build_plan_diff(candidate, request=request, candidate_plan_digest=D_A,
                           baseline=None, baseline_kind="none")
    with pytest.raises(ValueError):  # card digest must match the diff's
        build_pending_plan_approval(
            draft=candidate, request=request, candidate_plan_digest=D_B,
            diff=diff, plan_diff_ref=_diff_ref(diff), planner_rationale=None,
            candidate_id="cand-1", requested_at=NOW)


# =========================================================================== #
# PendingPlanApproval — AUTO unconstructible + preset-provenance validator      #
# =========================================================================== #
def _pending_kwargs(diff: PlanDiff, **over):
    base = dict(
        request_id="r-1", candidate_plan_digest=D_A, goal="g",
        source=PlanSource.DYNAMIC, approval_policy=ApprovalPolicy.REQUIRED,
        node_count=1, worker_ids=("text.sentiment",),
        budget_request_tokens=0, budget_request_llm_invocations=0,
        plan_diff_ref=_diff_ref(diff), rendered_md="body",
        rendered_from_diff_digest=diff.semantic_digest(),
        planner_rationale=None, candidate_id="cand-1", requested_at=NOW)
    base.update(over)
    return base


def test_auto_pending_card_is_unconstructible(pieces):
    request, _context, baseline = pieces
    diff = build_plan_diff(baseline, request=request, candidate_plan_digest=D_A,
                           baseline=None, baseline_kind="none")
    with pytest.raises(ValidationError):
        PendingPlanApproval(**_pending_kwargs(
            diff, approval_policy=ApprovalPolicy.AUTO))


def test_pending_source_must_be_dynamic_or_preset_fallback(pieces):
    request, _context, baseline = pieces
    diff = build_plan_diff(baseline, request=request, candidate_plan_digest=D_A,
                           baseline=None, baseline_kind="none")
    with pytest.raises(ValidationError):  # PRESET (non-fallback) is not a human card
        PendingPlanApproval(**_pending_kwargs(diff, source=PlanSource.PRESET))
    with pytest.raises(ValidationError):
        PendingPlanApproval(**_pending_kwargs(diff, source=PlanSource.BOOTSTRAP))


def test_preset_provenance_both_directions(pieces):
    request, _context, baseline = pieces
    diff = build_plan_diff(baseline, request=request, candidate_plan_digest=D_A,
                           baseline=None, baseline_kind="none")
    # DYNAMIC must NOT carry preset provenance.
    with pytest.raises(ValidationError):
        PendingPlanApproval(**_pending_kwargs(
            diff, source=PlanSource.DYNAMIC, preset_id=RESEARCH_BASELINE,
            preset_record_digest=D_B))
    # PRESET_FALLBACK MUST carry both provenance fields.
    with pytest.raises(ValidationError):
        PendingPlanApproval(**_pending_kwargs(
            diff, source=PlanSource.PRESET_FALLBACK))
    with pytest.raises(ValidationError):  # half-set is rejected
        PendingPlanApproval(**_pending_kwargs(
            diff, source=PlanSource.PRESET_FALLBACK, preset_id=RESEARCH_BASELINE))
    # both set constructs.
    ok = PendingPlanApproval(**_pending_kwargs(
        diff, source=PlanSource.PRESET_FALLBACK, preset_id=RESEARCH_BASELINE,
        preset_record_digest=D_B))
    assert ok.source is PlanSource.PRESET_FALLBACK
    assert ok.preset_id == RESEARCH_BASELINE


def test_pending_preset_fallback_via_builder(pieces):
    request, _context, baseline = pieces
    candidate = _reconstruct(
        _candidate_with_node_change(baseline), source=PlanSource.PRESET_FALLBACK)
    diff = build_plan_diff(
        candidate, request=request, candidate_plan_digest=D_A,
        baseline=baseline, baseline_kind="fallback_preset",
        baseline_preset_id=RESEARCH_BASELINE)
    pending = build_pending_plan_approval(
        draft=candidate, request=request, candidate_plan_digest=D_A,
        diff=diff, plan_diff_ref=_diff_ref(diff), planner_rationale=None,
        candidate_id="cand-1", requested_at=NOW,
        preset_id=RESEARCH_BASELINE, preset_record_digest=D_B)
    assert pending.source is PlanSource.PRESET_FALLBACK
    assert pending.preset_id == RESEARCH_BASELINE
    assert pending.preset_record_digest == D_B
    # a PRESET_FALLBACK card without provenance fails through the builder too.
    with pytest.raises(ValidationError):
        build_pending_plan_approval(
            draft=candidate, request=request, candidate_plan_digest=D_A,
            diff=diff, plan_diff_ref=_diff_ref(diff), planner_rationale=None,
            candidate_id="cand-1", requested_at=NOW)


# =========================================================================== #
# Registered-contract schema identity                                          #
# =========================================================================== #
def test_semantic_exclude_and_schema_ref_constant():
    assert PendingPlanApproval.SEMANTIC_EXCLUDE == frozenset(
        {"candidate_id", "requested_at"})
    assert PLAN_DIFF_SCHEMA_REF == SchemaRef(name="PlanDiff", version="1")
    # audit-only fields do not move the semantic digest.
    kw = dict(
        request_id="r-1", candidate_plan_digest=D_A, goal="g",
        source=PlanSource.DYNAMIC, approval_policy=ApprovalPolicy.REQUIRED,
        node_count=1, worker_ids=("text.sentiment",),
        plan_diff_ref=TypedPayloadRef(
            schema_ref=PLAN_DIFF_SCHEMA_REF,
            payload_ref=PayloadRef(namespace="main", object_id="o",
                                   content_digest=D_A)),
        rendered_md="body", rendered_from_diff_digest=D_A,
        candidate_id="cand-1", requested_at=NOW)
    a = PendingPlanApproval(**kw)
    b = PendingPlanApproval(**{**kw, "candidate_id": "cand-9",
                               "requested_at": datetime(2030, 1, 1, tzinfo=UTC)})
    assert a.semantic_digest() == b.semantic_digest()
