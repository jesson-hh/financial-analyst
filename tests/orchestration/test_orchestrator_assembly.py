# -*- coding: utf-8 -*-
"""Phase 7 - Task 2: runtime-stamped, shrink-only dynamic PlanDraft assembly.

Written test-first (RED until ``assemble_dynamic_draft`` / ``PlannerShapeRejected``
exist in ``guanlan_v2/orchestration/orchestrator.py``).

Covers, per the Task-2 brief:

* the happy path over the reviewed pilot triad (``text.sentiment ->
  dec.research_mgr -> dec.pm``): a plain Phase 1 ``PlanDraft`` (schema_version
  "2", no subclass/wrapper), runtime-stamped identity/authority fields, Phase 1
  ``validate_plan_draft`` acceptance, and byte-identical / same-candidate-digest
  determinism;
* each of the five generation-side shape issue codes in isolation and a
  multi-code accumulation (canonically sorted inside one ``PlannerShapeRejected``,
  never a partial construction);
* the runtime ``context_snapshot_ref`` precondition raising *before* construction
  (a plain ``ValueError``, not a ``PlannerShapeRejected``);
* stamped-field immunity END-TO-END: an envelope can never influence
  ``as_of`` / ``mode`` / digests / approval policy / source because those authored
  keys are parser-rejected upstream of assembly;
* approval-policy verbatim copy (an AUTO request yields an AUTO draft that Phase 1
  - not assembly - rejects);
* universe normalization over the Phase 3 grammar (bare / dotted / engine) with
  garbage rejected via ``planner_universe_symbol_invalid``;
* Phase 1 validator pass-through: ``unknown_worker`` on a swapped catalog and
  ``input_schema_mismatch`` on a wrong dependency reach validation unshimmed.

Run from repo root: ``pytest tests/orchestration/test_orchestrator_assembly.py -v``
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.catalog_runtime import load_pilot_catalog
from guanlan_v2.orchestration.enums import ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.refs import PayloadRef
from guanlan_v2.orchestration.runtime_contracts import phase2_runtime_registry
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.spec import (
    PlanDraft,
    _HIDDEN_AUTHORITY_KEYS,
    compute_candidate_plan_digest,
    validate_plan_draft,
)

from guanlan_v2.orchestration.orchestrator import (
    PlannerDraftEnvelope,
    PlannerOutputRejected,
    PlannerShapeRejected,
    assemble_dynamic_draft,
    parse_planner_output,
)

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Pilot-triad scaffolding (mirrors the Task-0 handoff gate's fixtures, built    #
# only from reviewed public Phase-1/2 builders).                                #
# --------------------------------------------------------------------------- #
class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 19, 2, 0, tzinfo=UTC)


class _Pieces:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    @property
    def snapshot(self):
        return self.catalog_runtime.snapshot


def _build_pieces(request_id: str = "req-task2") -> _Pieces:
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
    return _Pieces(clock=clock, registry=registry, catalog_runtime=catalog_runtime,
                   request=request, context=mem.context)


@pytest.fixture(scope="module")
def pieces() -> _Pieces:
    return _build_pieces()


def _pilot_envelope_obj() -> dict:
    """The authored envelope that mirrors the reviewed pilot triad exactly."""
    return {
        "nodes": [
            {"id": "sentiment", "worker_id": "text.sentiment", "params": {},
             "dependencies": [], "writes_slot": "slot-sentiment"},
            {"id": "research", "worker_id": "dec.research_mgr", "params": {},
             "writes_slot": "slot-research",
             "dependencies": [
                 {"upstream_node_id": "sentiment", "artifact_slot": "slot-sentiment",
                  "inject_as": "sentiment", "policy": "block"}]},
            {"id": "pm", "worker_id": "dec.pm", "params": {}, "writes_slot": "slot-pm",
             "dependencies": [
                 {"upstream_node_id": "research", "artifact_slot": "slot-research",
                  "inject_as": "research_plan", "policy": "block"},
                 {"upstream_node_id": "sentiment", "artifact_slot": "slot-sentiment",
                  "inject_as": "sentiment", "policy": "block"}]},
        ],
        "sink_node_ids": ["pm"],
        "universe": [],
        "budget_request_tokens": 2_000_000,
        "budget_request_llm_invocations": 3,
        "max_concurrency": 3,
    }


def _env(obj: dict) -> PlannerDraftEnvelope:
    """Build the envelope in JSON mode (admits JSON arrays for its tuple fields),
    exactly as ``parse_planner_output`` does — python strict mode would reject a
    list for a ``tuple`` field."""
    return PlannerDraftEnvelope.model_validate_json(json.dumps(obj))


def _ctx_ref(pieces: _Pieces) -> PayloadRef:
    return PayloadRef(namespace="main", object_id="dyn-ctx",
                      content_digest=pieces.context.content_digest)


def _assemble(pieces: _Pieces, envelope: PlannerDraftEnvelope, **over) -> PlanDraft:
    kw = dict(
        request=over.pop("request", pieces.request),
        context=pieces.context,
        context_snapshot_ref=over.pop("context_snapshot_ref", None) or _ctx_ref(pieces),
        catalog=over.pop("catalog", pieces.snapshot),
        schema_registry=pieces.registry,
        draft_id="plan-dyn-1",
        run_id="run-dyn-1",
    )
    kw.update(over)
    return assemble_dynamic_draft(envelope, **kw)


# =========================================================================== #
# Happy path over the pilot triad (invariants 1, 2, 4, 6)                       #
# =========================================================================== #
def test_happy_path_plain_plandraft_stamped_valid_and_deterministic(pieces):
    env = parse_planner_output(json.dumps(_pilot_envelope_obj()))
    d1 = _assemble(pieces, env)
    d2 = _assemble(pieces, env)

    # invariant 1: a plain Phase-1 PlanDraft on schema_version "2" (no subclass).
    assert type(d1) is PlanDraft
    assert d1.schema_version == "2"

    # runtime-stamped identity/authority.
    assert d1.source is PlanSource.DYNAMIC and d1.phase == "main"
    assert d1.id == "plan-dyn-1" and d1.run_id == "run-dyn-1"
    assert d1.request_id == pieces.request.request_id
    assert d1.goal == pieces.request.goal
    assert d1.as_of == pieces.context.data_context.as_of
    assert d1.mode == pieces.context.data_context.mode
    assert d1.catalog_version == pieces.snapshot.catalog_version
    assert d1.catalog_digest == pieces.snapshot.catalog_digest
    assert d1.schema_registry_digest == pieces.registry.registry_digest
    assert d1.approval_policy == pieces.request.approval_policy
    # empty kernel-authority collections + None legacy tuple.
    assert d1.debates == () and d1.gates == () and d1.reducers == ()
    assert d1.stop_condition_refs == ()
    assert d1.legacy_source_schema is None
    assert d1.legacy_source_config_digest is None
    assert d1.legacy_mapping_digest is None

    # invariant 4: Phase-1 validation accepts the pilot triad, zero shims.
    report = validate_plan_draft(
        d1, request=pieces.request, context=pieces.context,
        catalog=pieces.snapshot, schema_registry=pieces.registry)
    assert report.valid, [i.code for i in report.issues]
    # invariant 2: a REQUIRED request passes both DYNAMIC approval checks.
    codes = {i.code for i in report.issues}
    assert "dynamic_approval_policy_mismatch" not in codes
    assert "dynamic_approval_policy_not_required" not in codes

    # invariant 6: byte-identical projection + identical candidate digest.
    assert d1.model_dump_json() == d2.model_dump_json()
    cd1 = compute_candidate_plan_digest(
        request=pieces.request, draft=d1,
        context_content_digest=pieces.context.content_digest)
    cd2 = compute_candidate_plan_digest(
        request=pieces.request, draft=d2,
        context_content_digest=pieces.context.content_digest)
    assert cd1 == cd2


def test_authored_node_fields_pass_through_with_phase1_defaults(pieces):
    env = _env(_pilot_envelope_obj())
    d = _assemble(pieces, env)
    by_id = {n.id: n for n in d.nodes}
    # authored fields carried; every non-authored field takes its Phase-1 default.
    pm = by_id["pm"]
    assert pm.worker_id == "dec.pm" and pm.writes_slot == "slot-pm"
    assert pm.timeout_sec == 300 and pm.token_reservation == 0
    assert pm.gate_ids == () and pm.debate_id is None and pm.auxiliary is False
    assert pm.max_attempts == 1
    # dependency policy literal lifted to the Phase-1 enum with default accept set.
    dep = pm.dependencies[0]
    assert dep.policy.value == "block"
    assert set(s.value for s in dep.accept_statuses) == {"completed"}


# =========================================================================== #
# Invariant 3 - shrink-only: no hidden-authority surface anywhere               #
# =========================================================================== #
def test_shrink_only_executable_projection_has_no_hidden_authority(pieces):
    env = _env(_pilot_envelope_obj())
    d = _assemble(pieces, env)

    keys: set[str] = set()

    def _walk(obj):
        if isinstance(obj, BaseModel):
            obj = obj.model_dump(mode="python")
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys.add(k)
                _walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _walk(v)

    for value in d.executable_projection().values():
        _walk(value)
    assert _HIDDEN_AUTHORITY_KEYS.isdisjoint(keys), keys & _HIDDEN_AUTHORITY_KEYS


# =========================================================================== #
# Stamped-field immunity END-TO-END                                            #
# =========================================================================== #
def test_stamped_fields_are_immune_end_to_end(pieces):
    env = parse_planner_output(json.dumps(_pilot_envelope_obj()))
    d = _assemble(pieces, env)
    assert d.as_of == pieces.context.data_context.as_of
    assert d.mode == pieces.context.data_context.mode
    assert d.catalog_digest == pieces.snapshot.catalog_digest
    assert d.schema_registry_digest == pieces.registry.registry_digest
    assert d.approval_policy == pieces.request.approval_policy
    assert d.source is PlanSource.DYNAMIC

    # every attempt to author a stamped/authority field is parser-rejected before
    # assembly can ever see it.
    hostile = [
        ("as_of", "2000-01-01T00:00:00+00:00"),
        ("mode", "online"),
        ("catalog_digest", "0" * 64),
        ("schema_registry_digest", "0" * 64),
        ("approval_policy", "auto"),
        ("source", "dynamic"),
        ("phase", "main"),
        ("context_snapshot_ref",
         {"namespace": "main", "object_id": "x", "content_digest": "0" * 64}),
    ]
    for key, val in hostile:
        obj = {**_pilot_envelope_obj(), key: val}
        with pytest.raises(PlannerOutputRejected) as ei:
            parse_planner_output(json.dumps(obj))
        assert "planner_authored_reserved_field" in ei.value.issue_codes, key


def test_approval_policy_copied_verbatim_and_phase1_owns_auto_rejection(pieces):
    env = _env(_pilot_envelope_obj())
    req_auto = pieces.request.model_copy(update={"approval_policy": ApprovalPolicy.AUTO})
    d = _assemble(pieces, env, request=req_auto)
    # assembly copies the request policy verbatim - it never sanitizes/chooses.
    assert d.approval_policy is ApprovalPolicy.AUTO
    # Phase 1 (not assembly) rejects AUTO for a DYNAMIC draft.
    report = validate_plan_draft(
        d, request=req_auto, context=pieces.context,
        catalog=pieces.snapshot, schema_registry=pieces.registry)
    codes = {i.code for i in report.issues}
    assert not report.valid
    assert "auto_approval_rejected" in codes
    assert "dynamic_approval_policy_not_required" in codes


# =========================================================================== #
# context_snapshot_ref precondition (runtime input, raises pre-construction)    #
# =========================================================================== #
def test_context_ref_mismatch_raises_before_construction(pieces):
    env = _env(_pilot_envelope_obj())
    bad_digest = PayloadRef(namespace="main", object_id="x", content_digest="0" * 64)
    with pytest.raises(ValueError) as ei:
        _assemble(pieces, env, context_snapshot_ref=bad_digest)
    assert not isinstance(ei.value, PlannerShapeRejected)

    bad_ns = PayloadRef(namespace="sealed", object_id="x",
                        content_digest=pieces.context.content_digest)
    with pytest.raises(ValueError) as ei2:
        _assemble(pieces, env, context_snapshot_ref=bad_ns)
    assert not isinstance(ei2.value, PlannerShapeRejected)


# =========================================================================== #
# Generation-side shape checks - each code in isolation                        #
# =========================================================================== #
def test_shape_unknown_worker(pieces):
    obj = {
        "nodes": [{"id": "x", "worker_id": "no.such.worker", "params": {},
                   "dependencies": [], "writes_slot": "s"}],
        "sink_node_ids": ["x"],
        "budget_request_tokens": 0, "budget_request_llm_invocations": 0,
    }
    env = _env(obj)
    with pytest.raises(PlannerShapeRejected) as ei:
        _assemble(pieces, env)
    assert ei.value.issue_codes == ("planner_unknown_worker",)


def test_shape_worker_not_dynamic_excludes_compat(pieces):
    tpcv = importlib.import_module("tests.orchestration.test_plan_catalog_validation")
    compat = tpcv._compat_catalog()  # a single compat.legacy (static_legacy_only) worker
    obj = {
        "nodes": [{"id": "legacy", "worker_id": "compat.legacy", "params": {},
                   "dependencies": [], "writes_slot": "sl"}],
        "sink_node_ids": ["legacy"],
        "budget_request_tokens": 0, "budget_request_llm_invocations": 0,
    }
    env = _env(obj)
    with pytest.raises(PlannerShapeRejected) as ei:
        _assemble(pieces, env, catalog=compat)
    assert ei.value.issue_codes == ("planner_worker_not_dynamic",)


def test_shape_budget_exceeds_remaining_tokens(pieces):
    env = _env(_pilot_envelope_obj())
    with pytest.raises(PlannerShapeRejected) as ei:
        _assemble(pieces, env, remaining_tokens=1000)
    assert ei.value.issue_codes == ("planner_budget_exceeds_remaining",)


def test_shape_budget_exceeds_remaining_llm(pieces):
    env = _env(_pilot_envelope_obj())
    with pytest.raises(PlannerShapeRejected) as ei:
        _assemble(pieces, env, remaining_llm_invocations=1)
    assert ei.value.issue_codes == ("planner_budget_exceeds_remaining",)


def test_shape_budget_within_remaining_is_ok(pieces):
    env = _env(_pilot_envelope_obj())
    d = _assemble(pieces, env, remaining_tokens=2_000_000, remaining_llm_invocations=3)
    assert type(d) is PlanDraft  # exactly-equal remaining is admissible


def test_shape_duplicate_node_id(pieces):
    obj = {
        "nodes": [
            {"id": "n", "worker_id": "text.sentiment", "params": {},
             "dependencies": [], "writes_slot": "s1"},
            {"id": "n", "worker_id": "text.sentiment", "params": {},
             "dependencies": [], "writes_slot": "s2"},
        ],
        "sink_node_ids": ["n"],
        "budget_request_tokens": 0, "budget_request_llm_invocations": 0,
    }
    env = _env(obj)
    with pytest.raises(PlannerShapeRejected) as ei:
        _assemble(pieces, env)
    assert ei.value.issue_codes == ("planner_duplicate_node_id",)


def test_shape_multiple_codes_accumulate_canonically_sorted(pieces):
    obj = {
        "nodes": [
            {"id": "dup", "worker_id": "no.such", "params": {},
             "dependencies": [], "writes_slot": "s1"},
            {"id": "dup", "worker_id": "text.sentiment", "params": {},
             "dependencies": [], "writes_slot": "s2"},
        ],
        "sink_node_ids": ["dup"],
        "universe": ["notasymbol"],
        "budget_request_tokens": 2_000_000, "budget_request_llm_invocations": 0,
    }
    env = _env(obj)
    with pytest.raises(PlannerShapeRejected) as ei:
        _assemble(pieces, env, remaining_tokens=1000)
    expected = tuple(sorted({
        "planner_unknown_worker", "planner_duplicate_node_id",
        "planner_universe_symbol_invalid", "planner_budget_exceeds_remaining"}))
    assert ei.value.issue_codes == expected


# =========================================================================== #
# Universe normalization (Phase 3 grammar) + garbage rejection                  #
# =========================================================================== #
def test_universe_normalization_accepts_all_three_grammars(pieces):
    obj = {**_pilot_envelope_obj(),
           "universe": ["600519", "600519.SH", "SH600519", "000001", "300750", "688111"]}
    env = _env(obj)
    d = _assemble(pieces, env)
    assert [s.dotted for s in d.universe] == [
        "600519.SH", "600519.SH", "600519.SH", "000001.SZ", "300750.SZ", "688111.SH"]


@pytest.mark.parametrize("junk", ["白酒", "notasymbol", "12345", "600519.XX"])
def test_universe_garbage_is_rejected(pieces, junk):
    obj = {**_pilot_envelope_obj(), "universe": [junk]}
    env = _env(obj)
    with pytest.raises(PlannerShapeRejected) as ei:
        _assemble(pieces, env)
    assert ei.value.issue_codes == ("planner_universe_symbol_invalid",)


# =========================================================================== #
# Phase 1 validator pass-through (no assembly shim masks a real rejection)      #
# =========================================================================== #
def test_phase1_passthrough_unknown_worker_on_swapped_catalog(pieces):
    tpcv = importlib.import_module("tests.orchestration.test_plan_catalog_validation")
    env = parse_planner_output(json.dumps(_pilot_envelope_obj()))
    d = _assemble(pieces, env)  # valid against the pilot catalog
    swapped = tpcv._compat_catalog()  # has none of the three pilot workers
    report = validate_plan_draft(
        d, request=pieces.request, context=pieces.context,
        catalog=swapped, schema_registry=pieces.registry)
    assert not report.valid
    assert "unknown_worker" in {i.code for i in report.issues}


def test_phase1_passthrough_input_schema_mismatch_on_wrong_dependency(pieces):
    obj = _pilot_envelope_obj()
    # swap pm's two inject targets: the upstream output schema no longer equals the
    # target input binding schema (ResearchPlan into sentiment, SentimentReport into
    # research_plan) - assembly has no view of schema equality, Phase 1 does.
    obj["nodes"][2]["dependencies"] = [
        {"upstream_node_id": "research", "artifact_slot": "slot-research",
         "inject_as": "sentiment", "policy": "block"},
        {"upstream_node_id": "sentiment", "artifact_slot": "slot-sentiment",
         "inject_as": "research_plan", "policy": "block"},
    ]
    env = _env(obj)
    d = _assemble(pieces, env)  # assembly SUCCEEDS: no shape issue
    assert type(d) is PlanDraft
    report = validate_plan_draft(
        d, request=pieces.request, context=pieces.context,
        catalog=pieces.snapshot, schema_registry=pieces.registry)
    assert not report.valid
    assert "input_schema_mismatch" in {i.code for i in report.issues}
