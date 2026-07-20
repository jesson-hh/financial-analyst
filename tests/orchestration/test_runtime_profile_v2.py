# -*- coding: utf-8 -*-
"""Phase 8 · Task 8 — Runtime profile v2 (reducers / multi-writer / gate metrics /
retry + bounded schema repair).

Locks the additive v2 runtime layer while proving the v1-bound and BOOTSTRAP-profile
behavior is byte-identical (regression pins):

* ``StaticRuntimeProfileV2`` identity (Option-4 new model — ``profile_id=
  "static-runtime"`` / ``profile_version="2"``, clause (f)); the Phase-2 v1 constant
  and Phase-5 BOOTSTRAP constant are untouched;
* the admission matrix — each v2 unlock (reducers / multi-writer / gates / debates /
  ``max_attempts<=2``) is ADMITTED under v2 and still REJECTED under v1 and BOOTSTRAP
  (differential); conditions and stop conditions stay rejected under v2;
* the pure v2 analyzers ``analyze_reducers`` / ``analyze_gates`` /
  ``analyze_retry_repair`` (matrices);
* the deterministic reducer fold (shuffled-completion property, byte-identical);
* the gate-metric evaluation matrix (passed / failed / unavailable×{fail,degrade,
  skip} -> GateResult + downstream disposition);
* the bounded retry + schema-repair control loop over the real ``BudgetLedger``
  (per-attempt reservation + settlement, one committed output per key, idempotent
  replay, deterministic zero-LLM retries).

Run: ``python -m pytest tests/orchestration/test_runtime_profile_v2.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pytest

from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    OutputBinding,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
)
from guanlan_v2.orchestration.digest import DigestModel, NonEmptyStr, content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    DataMode,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.schemas import Artifact, NodeRun, Provenance
from guanlan_v2.orchestration.spec import (
    GateCfg,
    PlanDraft,
    PlanNode,
    PlanStructureError,
    ReducerCfg,
    validate_plan_structure,
)

from guanlan_v2.orchestration.runtime_contracts import static_runtime_profile
from guanlan_v2.orchestration.bootstrap import bootstrap_runtime_profile
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    StaticRuntimeProfileV2,
    analyze_gates,
    analyze_reducers,
    analyze_retry_repair,
    check_runtime_support,
    static_runtime_profile_v2,
)
from guanlan_v2.orchestration.pool import fold_reducer_producers
from guanlan_v2.orchestration import dag as D
from guanlan_v2.orchestration import worker as W

from tests.orchestration.test_budget_ledger import _ledger, _reserve_plan
from tests.orchestration.test_runtime_support import build_scenario

UTC = timezone.utc
DIG = "a" * 64


def _dt() -> datetime:
    return datetime(2026, 7, 19, 1, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# tiny payload schema + registry                                              #
# --------------------------------------------------------------------------- #
class OutPayload(DigestModel):
    schema_version: Literal["1"] = "1"
    note: NonEmptyStr = "n"


SR_OUT = SchemaRef(name="OutPayload", version="1")


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(OutPayload)
    reg.seal()
    return reg


def _text(cid: str, kind: str, text: str):
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=cid, version="1", content_digest="0" * 64), kind=kind, raw_utf8=raw)
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=cid, version="1", content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


# --------------------------------------------------------------------------- #
# multi-writer + reducer + gate catalog / draft builder                        #
# --------------------------------------------------------------------------- #
def _det_worker(worker_id: str, handler_ref: ContentRef) -> WorkerSpec:
    return WorkerSpec(
        id=worker_id, catalog_role="final", selection_scope="dynamic_allowed", lane="market",
        persona="deterministic reader", tier=Tier.READER,
        execution=ExecutionSpec(kind=ExecutionKind.DETERMINISTIC, handler_ref=handler_ref),
        read_categories=(), inputs=(),
        outputs=(OutputBinding(name="primary", schema_ref=SR_OUT),),
        evidence_policy=EvidencePolicy(
            tool_calls=ToolCallRequirement.FORBIDDEN, require_input_refs=False,
            require_number_anchors=False, allow_unsourced_numbers=False,
            optional_data_may_degrade=True),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none")


def build_multiwriter_catalog(*, reducer_kind: str = "reducer"):
    """A minimal catalog: two deterministic workers, a reducer material and a
    gate-metric material. ``reducer_kind`` lets a test forge a wrong-kind reducer_ref.
    """
    h1_ref, h1 = _text("h.w1", "handler", "def w1(): return 1")
    h2_ref, h2 = _text("h.w2", "handler", "def w2(): return 2")
    red_ref, red_mat = _text("m.reducer", reducer_kind, "deterministic reducer material")
    gm_ref, gm_mat = _text("m.gatemetric", "gate_metric",
                           '{"material_kind":"gate_metric","value_pointer":"/rank_ic"}')
    w1 = _det_worker("w1", h1_ref)
    w2 = _det_worker("w2", h2_ref)
    content = [
        ContentManifestEntry(ref=h1_ref, kind="handler", name="h.w1", description="d", source_identity="gl"),
        ContentManifestEntry(ref=h2_ref, kind="handler", name="h.w2", description="d", source_identity="gl"),
        ContentManifestEntry(ref=red_ref, kind=reducer_kind, name="m.reducer", description="d", source_identity="gl"),
        ContentManifestEntry(ref=gm_ref, kind="gate_metric", name="m.gatemetric", description="d", source_identity="gl"),
    ]
    mats = [h1, h2, red_mat, gm_mat]
    snapshot = build_catalog_snapshot(
        catalog_version="mw-v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=(), workers=(w1, w2), resolved_material=tuple(mats))
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats}, capabilities={})
    runtime = CatalogRuntime.build(snapshot, source)
    view = BridgeCatalogView.build(runtime, {})
    return runtime, view, snapshot, red_ref, gm_ref


def _mw_nodes():
    n1 = PlanNode(id="n1", worker_id="w1", writes_slot="s")
    n2 = PlanNode(id="n2", worker_id="w2", writes_slot="s")
    return n1, n2


def _reducer(red_ref: ContentRef, producers=("n1", "n2")) -> ReducerCfg:
    return ReducerCfg(id="r1", slot="s", reducer_ref=red_ref,
                      producer_node_ids=tuple(producers), output_schema_ref=SR_OUT)


def build_mw_draft(*, red_ref, gates=(), reducers=None, nodes=None, source=PlanSource.DYNAMIC,
                   snapshot=None, registry_digest=DIG, max_attempts=1):
    n1, n2 = nodes if nodes is not None else _mw_nodes()
    if max_attempts != 1:
        n1 = n1.model_copy(update={"max_attempts": max_attempts})
    reds = reducers if reducers is not None else (_reducer(red_ref),)
    ctx_ref = PayloadRef(namespace="main", object_id="ctx", content_digest=DIG)
    return PlanDraft(
        id="plan.mw", run_id="run-1", request_id="req-1", phase="main", source=source,
        goal="g", as_of=_dt(), mode=DataMode.ONLINE, context_snapshot_ref=ctx_ref,
        nodes=(n1, n2), sink_node_ids=("n1", "n2"),
        reducers=reds, gates=tuple(gates),
        catalog_version=(snapshot.catalog_version if snapshot else "mw-v1"),
        catalog_digest=(snapshot.catalog_digest if snapshot else DIG),
        schema_registry_digest=registry_digest, approval_policy=ApprovalPolicy.REQUIRED)


# =========================================================================== #
# 1. StaticRuntimeProfileV2 identity (Option-4)                                #
# =========================================================================== #
def test_v2_profile_identity_and_unlocks():
    p = STATIC_RUNTIME_PROFILE_V2
    assert p.profile_id == "static-runtime"
    assert p.profile_version == "2"
    assert (p.supports_reducers, p.supports_multi_writer, p.supports_gates,
            p.supports_debates, p.supports_retries) == (True, True, True, True, True)
    # conditions + stop conditions stay LOCKED under v2.
    assert p.supports_conditions is False
    assert p.supports_stop_conditions is False
    assert p.supports_bootstrap is False
    assert p.max_attempts_limit == 2 and p.max_attempts_supported == 2
    assert p.schema_repairs_per_attempt == 1
    # self-sealed + canonical (a fresh instance is equal + same digest).
    assert static_runtime_profile_v2() == p
    assert static_runtime_profile_v2().profile_digest == p.profile_digest


def test_v2_profile_is_a_distinct_model_from_v1_and_leaves_v1_untouched():
    v1 = static_runtime_profile()
    assert v1.profile_version == "1"
    assert v1.supports_reducers is False and v1.max_attempts_supported == 1
    # distinct identity + digest (Option-4: a new model, not a v1 instance).
    assert STATIC_RUNTIME_PROFILE_V2.profile_digest != v1.profile_digest
    assert not isinstance(STATIC_RUNTIME_PROFILE_V2, type(v1))
    # a v2 profile cannot be forged with unlocked conditions/stop.
    with pytest.raises(Exception):
        StaticRuntimeProfileV2(profile_digest="0" * 64)  # unsealed digest rejected


# =========================================================================== #
# 2/1. admission matrix — differential (v1/BOOTSTRAP reject, v2 admits)         #
# =========================================================================== #
def _report(draft, *, snapshot, runtime, view, registry, profile):
    from guanlan_v2.orchestration.spec import OrchestrationRequest, validate_plan_draft
    request = OrchestrationRequest(request_id="req-1", goal="g", workflow="orchestrate_only",
                                   approval_policy=ApprovalPolicy.REQUIRED)
    phase1 = validate_plan_draft(draft, request=request, context=None, catalog=snapshot,
                                 schema_registry=registry)
    return check_runtime_support(
        draft, phase1_report=phase1, context=None, context_requirements=None, catalog=runtime,
        bridge_view=view, schema_registry=registry, profile=profile)


def _codes(report):
    return {i.code for i in report.issues}


def test_v1_rejects_reducers_multiwriter_gates_before_reservation():
    """Row 1: a v1 profile rejects reducers / multi-writer / gates (Phase-2 pin)."""
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    reg = _registry()
    gate = GateCfg(id="g1", metric=gm_ref, operator=">=", threshold=0.02, scope="oos")
    n1 = PlanNode(id="n1", worker_id="w1", writes_slot="s", gate_ids=("g1",))
    n2 = PlanNode(id="n2", worker_id="w2", writes_slot="s")
    draft = build_mw_draft(red_ref=red_ref, gates=(gate,), nodes=(n1, n2), snapshot=snap,
                           registry_digest=reg.registry_digest)
    rep = _report(draft, snapshot=snap, runtime=runtime, view=view, registry=reg,
                  profile=static_runtime_profile())
    assert not rep.supported
    codes = _codes(rep)
    assert "reducers_unsupported" in codes
    assert "multi_writer_unsupported" in codes
    assert "gates_unsupported" in codes


def test_v2_admits_reducers_multiwriter_gates():
    """Row 1 mirror: under v2 the reducer/multi-writer/gate unlock codes are ABSENT
    (the well-formed plan is admitted at those seams; the no-context draft still
    carries the profile-independent bootstrap code, which is constant and not part
    of this differential)."""
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    reg = _registry()
    gate = GateCfg(id="g1", metric=gm_ref, operator=">=", threshold=0.02, scope="oos")
    n1 = PlanNode(id="n1", worker_id="w1", writes_slot="s", gate_ids=("g1",))
    n2 = PlanNode(id="n2", worker_id="w2", writes_slot="s")
    draft = build_mw_draft(red_ref=red_ref, gates=(gate,), nodes=(n1, n2), snapshot=snap,
                           registry_digest=reg.registry_digest)
    rep = _report(draft, snapshot=snap, runtime=runtime, view=view, registry=reg,
                  profile=STATIC_RUNTIME_PROFILE_V2)
    codes = _codes(rep)
    assert "reducers_unsupported" not in codes
    assert "multi_writer_unsupported" not in codes
    assert "gates_unsupported" not in codes


def test_v2_supported_end_to_end_with_context_and_retry():
    """A fully-contexted single-node plan with max_attempts=2 is runtime-SUPPORTED
    under v2 but REJECTED under v1 (retries_unsupported) — the retry unlock proved on
    the real admission fixture."""
    sc = build_scenario(node_over={"max_attempts": 2})
    v1 = sc.check(profile=static_runtime_profile())
    v2 = sc.check(profile=STATIC_RUNTIME_PROFILE_V2)
    assert not v1.supported and "retries_unsupported" in _codes(v1)
    assert v2.supported, [i.code for i in v2.issues]


def test_bootstrap_profile_still_rejects_v2_features():
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    reg = _registry()
    draft = build_mw_draft(red_ref=red_ref, snapshot=snap, registry_digest=reg.registry_digest)
    rep = _report(draft, snapshot=snap, runtime=runtime, view=view, registry=reg,
                  profile=bootstrap_runtime_profile())
    assert not rep.supported
    codes = _codes(rep)
    assert "reducers_unsupported" in codes and "multi_writer_unsupported" in codes


def test_conditions_and_stop_conditions_still_rejected_under_v2():
    """Conditions + stop conditions remain rejected before reservation under v2."""
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    reg = _registry()
    cond_ref, _cm = _text("m.cond", "condition", "cond")
    n1 = PlanNode(id="n1", worker_id="w1", writes_slot="s", condition_ref=cond_ref)
    n2 = PlanNode(id="n2", worker_id="w2", writes_slot="s")
    ctx_ref = PayloadRef(namespace="main", object_id="ctx", content_digest=DIG)
    draft = PlanDraft(
        id="plan.c", run_id="run-1", request_id="req-1", phase="main", source=PlanSource.DYNAMIC,
        goal="g", as_of=_dt(), mode=DataMode.ONLINE, context_snapshot_ref=ctx_ref,
        nodes=(n1, n2), sink_node_ids=("n1", "n2"),
        reducers=(_reducer(red_ref),), stop_condition_refs=(cond_ref,),
        catalog_version=snap.catalog_version, catalog_digest=snap.catalog_digest,
        schema_registry_digest=reg.registry_digest, approval_policy=ApprovalPolicy.REQUIRED)
    rep = _report(draft, snapshot=snap, runtime=runtime, view=view, registry=reg,
                  profile=STATIC_RUNTIME_PROFILE_V2)
    assert not rep.supported
    codes = _codes(rep)
    assert "conditions_unsupported" in codes
    assert "stop_conditions_unsupported" in codes


def test_max_attempts_two_admitted_under_v2_rejected_under_v1_and_bootstrap():
    """The retry unlock differential expressed on a single-node slot."""
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    reg = _registry()
    draft = build_mw_draft(red_ref=red_ref, snapshot=snap, registry_digest=reg.registry_digest,
                           max_attempts=2)
    v1 = _report(draft, snapshot=snap, runtime=runtime, view=view, registry=reg,
                 profile=static_runtime_profile())
    boot = _report(draft, snapshot=snap, runtime=runtime, view=view, registry=reg,
                   profile=bootstrap_runtime_profile())
    v2 = _report(draft, snapshot=snap, runtime=runtime, view=view, registry=reg,
                 profile=STATIC_RUNTIME_PROFILE_V2)
    assert "retries_unsupported" in _codes(v1)
    assert "retries_unsupported" in _codes(boot)
    assert "retries_unsupported" not in _codes(v2)
    assert "max_attempts_exceeds_limit" not in _codes(v2)


def test_v1_v2_byte_identical_on_a_plain_plan_regression_pin():
    """A plain (no-v2-feature) fully-contexted plan is SUPPORTED and yields the SAME
    issue set under v1 and v2 — v2 changes nothing for plans that use no unlock (only
    the profile digest differs)."""
    sc = build_scenario()
    v1 = sc.check(profile=static_runtime_profile())
    v2 = sc.check(profile=STATIC_RUNTIME_PROFILE_V2)
    assert v1.supported and v2.supported
    assert _codes(v1) == _codes(v2) == set()
    # only the sealed profile digest differs; everything else the checker bound is equal.
    assert v1.runtime_profile_digest != v2.runtime_profile_digest
    assert v1.candidate_plan_digest == v2.candidate_plan_digest


# =========================================================================== #
# 2. Phase-1 rejects an unreduced multi-writer slot (v2 does NOT bypass)        #
# =========================================================================== #
def test_multiwriter_without_reducer_rejected_at_phase1():
    """Row 2: a multi-writer slot with no ReducerCfg is a Phase-1 structural error;
    v2 does not bypass Phase-1 validation."""
    # a valid reduced draft, then strip the reducer via model_construct (bypassing the
    # constructor) and re-run the spec.py structural check directly — it must reject.
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    good = build_mw_draft(red_ref=red_ref, snapshot=snap)
    unreduced = good.model_construct(**{**good.__dict__, "reducers": ()})
    with pytest.raises(PlanStructureError) as exc:
        validate_plan_structure(unreduced)
    assert "reducer" in str(exc.value).lower()


# =========================================================================== #
# 3. analyze_reducers matrix                                                   #
# =========================================================================== #
def test_analyze_reducers_wellformed_is_empty():
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    draft = build_mw_draft(red_ref=red_ref, snapshot=snap)
    assert analyze_reducers(draft, catalog=runtime) == ()


def test_analyze_reducers_wrong_kind_reducer_ref():
    """Row 3: a reducer_ref resolving to a 'prompt' material yields an analyzer issue."""
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog(reducer_kind="prompt")
    draft = build_mw_draft(red_ref=red_ref, snapshot=snap)
    issues = analyze_reducers(draft, catalog=runtime)
    assert any(i.code == "reducer_ref_wrong_kind" for i in issues), [i.code for i in issues]


def test_analyze_reducers_producers_mismatch():
    # producers != writers is ALSO a Phase-1 structural error, so a normal PlanDraft
    # cannot carry it; build via model_construct to exercise the analyzer's defensive
    # runtime-support mirror directly.
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    good = build_mw_draft(red_ref=red_ref, snapshot=snap)
    draft = good.model_construct(**{**good.__dict__, "reducers": (_reducer(red_ref, producers=("n1",)),)})
    issues = analyze_reducers(draft, catalog=runtime)
    assert any(i.code == "reducer_producers_mismatch" for i in issues)


def test_analyze_reducers_unresolved_ref():
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    bogus = ContentRef(id="m.reducer", version="1", content_digest="9" * 64)
    draft = build_mw_draft(red_ref=bogus, snapshot=snap)
    issues = analyze_reducers(draft, catalog=runtime)
    assert any(i.code == "reducer_ref_unresolved" for i in issues)


# =========================================================================== #
# 4. analyze_gates matrix                                                      #
# =========================================================================== #
def test_analyze_gates_wellformed_is_empty():
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    gate = GateCfg(id="g1", metric=gm_ref, operator=">=", threshold=0.02, scope="oos")
    draft = build_mw_draft(red_ref=red_ref, gates=(gate,), snapshot=snap)
    assert analyze_gates(draft, catalog=runtime) == ()


def test_analyze_gates_wrong_kind_and_unresolved():
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    # a metric ref that points at the reducer material (kind='reducer', not 'gate_metric').
    wrong = GateCfg(id="g1", metric=red_ref, operator=">=", threshold=0.02, scope="oos")
    draft = build_mw_draft(red_ref=red_ref, gates=(wrong,), snapshot=snap)
    issues = analyze_gates(draft, catalog=runtime)
    assert any(i.code == "gate_metric_wrong_kind" for i in issues)

    bogus = ContentRef(id="m.gatemetric", version="1", content_digest="9" * 64)
    g2 = GateCfg(id="g2", metric=bogus, operator=">=", threshold=0.02, scope="oos")
    draft2 = build_mw_draft(red_ref=red_ref, gates=(g2,), snapshot=snap)
    issues2 = analyze_gates(draft2, catalog=runtime)
    assert any(i.code == "gate_metric_unresolved" for i in issues2)


# =========================================================================== #
# 5/6. analyze_retry_repair (cap = max_attempts_limit)                          #
# =========================================================================== #
def test_analyze_retry_repair_over_limit():
    """Row 6: max_attempts=3 under v2 (limit 2) yields an analyzer issue."""
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    draft = build_mw_draft(red_ref=red_ref, snapshot=snap, max_attempts=3)
    issues = analyze_retry_repair(draft, profile=STATIC_RUNTIME_PROFILE_V2)
    assert any(i.code == "max_attempts_exceeds_limit" and i.node_id == "n1" for i in issues)


def test_analyze_retry_repair_at_limit_is_ok():
    runtime, view, snap, red_ref, gm_ref = build_multiwriter_catalog()
    draft = build_mw_draft(red_ref=red_ref, snapshot=snap, max_attempts=2)
    assert analyze_retry_repair(draft, profile=STATIC_RUNTIME_PROFILE_V2) == ()


def test_retry_llm_upper_bound_formula():
    # LLM: max_attempts x (1 + repairs); deterministic: 0.
    assert W.retry_llm_invocation_upper_bound(is_llm=True, max_attempts=2, schema_repairs_per_attempt=1) == 4
    assert W.retry_llm_invocation_upper_bound(is_llm=True, max_attempts=1, schema_repairs_per_attempt=1) == 2
    assert W.retry_llm_invocation_upper_bound(is_llm=False, max_attempts=2, schema_repairs_per_attempt=1) == 0


# =========================================================================== #
# 4 (matrix). reducer fold determinism — shuffled completion                    #
# =========================================================================== #
def _artifact(node_id: str, note: str) -> Artifact:
    prov = Provenance(plan_digest=DIG, code_version="v1", as_of=_dt(), pit_mode=DataMode.ONLINE)
    return Artifact.build(
        artifact_id=f"art-{node_id}", run_id="run-1", created_at=_dt(),
        producer_node_id=node_id, slot="s", output_key="primary", kind="reduced",
        payload_schema_ref=SR_OUT, payload=OutPayload(note=note), rendered_md=f"# {note}",
        provenance=prov)


def _merge_handler(ordered):
    """A pure deterministic reducer: concatenate producer notes IN GIVEN ORDER."""
    joined = "|".join(a.payload.note for a in ordered)
    return OutPayload(note=joined)


def test_reducer_fold_is_shuffle_invariant_over_20_seeds():
    """Row 4: reduced payload is a function of (Plan node order, payloads) only —
    shuffled completion order yields a byte-identical reduced payload digest."""
    import random
    producers = [_artifact("n2", "beta"), _artifact("n1", "alpha")]
    # the canonical (node-id sorted) fold: n1 then n2 -> "alpha|beta".
    canonical = fold_reducer_producers(producers, handler=_merge_handler)
    assert canonical.note == "alpha|beta"
    baseline = content_digest(dict(canonical))
    for seed in range(20):
        shuffled = list(producers)
        random.Random(seed).shuffle(shuffled)
        reduced = fold_reducer_producers(shuffled, handler=_merge_handler)
        assert content_digest(dict(reduced)) == baseline, seed


# =========================================================================== #
# 5 (matrix). gate-metric evaluation matrix                                     #
# =========================================================================== #
_GATE_MATERIAL = {
    "material_kind": "gate_metric",
    "value_pointer": "/rank_ic",
    "applies_when": [{"pointer": "/oos", "equals": True}],
}


def _gate(**over):
    base = dict(id="g", metric=ContentRef(id="m.gatemetric", version="1", content_digest="0" * 64),
                operator=">=", threshold=0.02, scope="oos", blocking=True, unavailable_policy="fail")
    base.update(over)
    return GateCfg(**base)


def test_gate_material_parses_and_projects():
    from pathlib import Path
    import guanlan_v2.orchestration as orch
    p = Path(orch.__file__).resolve().parents[2] / "config" / "orchestration" / "materials" / "gate_metrics" / "oos-rank-ic.json"
    material = D.parse_gate_metric_material(p.read_bytes())
    assert material["metric_id"] == "gate_metric.oos_rank_ic"
    value, applicable = D.project_gate_metric(material, {"rank_ic": 0.05, "oos": True})
    assert applicable and value == 0.05
    # not applicable when the applies_when condition is unmet.
    _v, ap = D.project_gate_metric(material, {"rank_ic": 0.05, "oos": False})
    assert ap is False


def test_gate_passed_and_failed():
    passed = D.evaluate_gate(_gate(), observed=0.05, applicable=True, metrics_artifact_id="art-1")
    assert passed.status == "passed"
    assert D.gate_downstream_disposition(passed, _gate()) == "pass"

    failed = D.evaluate_gate(_gate(), observed=0.01, applicable=True, metrics_artifact_id="art-1")
    assert failed.status == "failed"
    # a blocking failed gate blocks dependents (run terminal partial).
    assert D.gate_downstream_disposition(failed, _gate(blocking=True)) == "blocked"
    # a non-blocking failed gate is advisory.
    assert D.gate_downstream_disposition(failed, _gate(blocking=False)) == "pass"


@pytest.mark.parametrize("policy,expected", [("fail", "blocked"), ("degrade", "degraded"), ("skip", "skipped")])
def test_gate_unavailable_follows_policy(policy, expected):
    gate = _gate(unavailable_policy=policy)
    result = D.evaluate_gate(gate, observed=None, applicable=False, metrics_artifact_id="art-1")
    assert result.status == "unavailable"
    assert D.gate_downstream_disposition(result, gate) == expected


# =========================================================================== #
# 7/8/9/10. bounded retry + schema repair control loop                          #
# =========================================================================== #
def _node_run(status, *, reason_code=None, attempt=1, itok=0, otok=0) -> NodeRun:
    now = _dt()
    success = status in (NodeStatus.COMPLETED, NodeStatus.DEGRADED)
    okeys = ("primary",) if success else ()
    oids = (f"art-{attempt}",) if success else ()
    return NodeRun(
        node_run_id=f"nr-{attempt}", run_id="run-1", plan_id="plan.mw", plan_digest=DIG,
        node_id="n1", worker_id="w1", status=status, reason_code=reason_code, reason=None,
        attempt_id=f"att-{attempt}", attempt=attempt, input_snapshot_digest=DIG,
        started_at=now, finished_at=now, output_keys=okeys, output_artifact_ids=oids,
        tool_call_records=(), data_result_refs=(), execution_evidence_refs=(),
        input_tokens=itok, output_tokens=otok)


class _ScriptedAttempts:
    """Returns a scripted (status, reason_code, otok) per invocation ordinal; records
    how many times it was called and which ordinals were repairs."""

    def __init__(self, script):
        self.script = script          # list of (status, reason_code, otok)
        self.calls = []               # (ordinal, is_repair)

    def __call__(self, ordinal, *, is_repair):
        self.calls.append((ordinal, is_repair))
        status, reason, otok = self.script[len(self.calls) - 1]
        nr = _node_run(status, reason_code=reason, attempt=ordinal, itok=5, otok=otok)
        art = object() if status in (NodeStatus.COMPLETED, NodeStatus.DEGRADED) else None
        return nr, art


def _fresh_ledger_with_plan():
    ledger, sink = _ledger()
    plan_res = _reserve_plan(ledger, tokens=4000, llm=30, concurrency=4)
    return ledger, plan_res


def test_schema_repair_invalid_then_valid_completes():
    """Row 7: an LLM node invalid once then valid — a schema_repair invocation runs,
    a second attempt (second PromptAssemblyRecord in production) and COMPLETED."""
    ledger, plan_res = _fresh_ledger_with_plan()
    script = _ScriptedAttempts([
        (NodeStatus.INCOMPLETE, "output_schema_invalid", 3),   # primary invalid
        (NodeStatus.COMPLETED, None, 7),                        # repair valid
    ])
    out = W.execute_with_bounded_retry(
        node_id="n1", run_id="run-1", is_llm=True, max_attempts=1, schema_repairs_per_attempt=1,
        budget=ledger, plan_reservation_id=plan_res.reservation_id, node_token_reservation=1024,
        attempt_fn=script)
    assert out.succeeded and out.node_run.status is NodeStatus.COMPLETED
    assert out.artifact is not None
    # exactly two invocations: one primary + one schema-repair.
    assert out.invocations == 2
    roles = [r.role for r in out.reservations]
    assert roles == ["primary", "schema_repair"]
    # both child reservations settled (fold-derived).
    state = ledger.replay()
    for r in out.reservations:
        assert state.reservations[r.reservation_id].status == "settled"


def test_schema_repair_invalid_twice_is_incomplete_no_artifact():
    """Row 8: invalid twice (schema_repairs_per_attempt=1) — INCOMPLETE, no Artifact,
    both reservations settled."""
    ledger, plan_res = _fresh_ledger_with_plan()
    script = _ScriptedAttempts([
        (NodeStatus.INCOMPLETE, "output_schema_invalid", 3),
        (NodeStatus.INCOMPLETE, "output_schema_invalid", 3),
    ])
    out = W.execute_with_bounded_retry(
        node_id="n1", run_id="run-1", is_llm=True, max_attempts=1, schema_repairs_per_attempt=1,
        budget=ledger, plan_reservation_id=plan_res.reservation_id, node_token_reservation=1024,
        attempt_fn=script)
    assert not out.succeeded
    assert out.node_run.status is NodeStatus.INCOMPLETE and out.artifact is None
    assert out.invocations == 2  # one primary + one (exhausted) repair
    state = ledger.replay()
    for r in out.reservations:
        assert state.reservations[r.reservation_id].status == "settled"


def test_crash_between_repair_and_settle_replays_idempotently():
    """Row 9: re-running the loop with the SAME scripted outcomes + ledger is
    idempotent — no duplicate reservations, no duplicated invocation billed."""
    ledger, plan_res = _fresh_ledger_with_plan()

    def run_once():
        script = _ScriptedAttempts([
            (NodeStatus.INCOMPLETE, "output_schema_invalid", 3),
            (NodeStatus.COMPLETED, None, 7),
        ])
        return W.execute_with_bounded_retry(
            node_id="n1", run_id="run-1", is_llm=True, max_attempts=1,
            schema_repairs_per_attempt=1, budget=ledger,
            plan_reservation_id=plan_res.reservation_id, node_token_reservation=1024,
            attempt_fn=script)

    first = run_once()
    n_events_after_first = len(ledger.replay().reservations)
    second = run_once()  # "replay" after a crash: same idempotency keys
    assert [r.reservation_id for r in first.reservations] == [r.reservation_id for r in second.reservations]
    # no NEW reservations minted on replay (idempotent).
    assert len(ledger.replay().reservations) == n_events_after_first


def test_deterministic_retry_reserves_zero_llm_and_one_output():
    """Row 10: a deterministic node with max_attempts=2 whose first attempt FAILS —
    a retry runs with zero LLM reservations and exactly one committed output."""
    ledger, plan_res = _fresh_ledger_with_plan()
    script = _ScriptedAttempts([
        (NodeStatus.FAILED, "handler_error", 0),   # first attempt fails
        (NodeStatus.COMPLETED, None, 0),            # retry succeeds
    ])
    out = W.execute_with_bounded_retry(
        node_id="n1", run_id="run-1", is_llm=False, max_attempts=2, schema_repairs_per_attempt=1,
        budget=ledger, plan_reservation_id=plan_res.reservation_id, node_token_reservation=1024,
        attempt_fn=script)
    assert out.succeeded and out.node_run.status is NodeStatus.COMPLETED
    assert out.artifact is not None
    roles = [r.role for r in out.reservations]
    assert roles == ["primary", "retry"]
    # deterministic node reserved ZERO llm invocations on every child.
    state = ledger.replay()
    for r in out.reservations:
        res = state.reservations[r.reservation_id]
        assert res.reserved_llm_invocations == 0
        assert res.status == "settled"
    # exactly one committed output (the winning artifact); no schema_repair role.
    assert "schema_repair" not in roles
