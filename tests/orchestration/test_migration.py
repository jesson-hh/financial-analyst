# -*- coding: utf-8 -*-
"""Task 12 — source-versioned reversible legacy-schema migration adapters.

Written test-first (RED before ``migration.py`` exists). Drives, from the REAL
Task-0 evidence (``tests/orchestration/fixtures/legacy_contract_samples.json`` +
``docs/superpowers/migrations/2026-07-15-orchestration-legacy-contract-map.md``):

* two SEPARATE action domains — report ``ResearchAction`` vs position
  ``PositionAction`` (no generic ``migrate_action`` / ``MigratedAction``);
* the MAPPED/UNMAPPABLE model matrix + exact reverse for every scalar adapter;
* rating / confidence / rotation / sentiment UNMAPPABLE-by-evidence behaviour;
* the closed deterministic legacy-YAML normalizer + its rejection rules;
* the legacy graph-mapping ABI, its nested status matrices, and the pure
  binding / attestation builders that refuse a partial/unmappable graph.

Run from repo root: ``pytest tests/orchestration/test_migration.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration.catalog import (
    CompatibilityBinding,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    ResolvedTextMaterial,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
)
from guanlan_v2.orchestration.context import (
    ClockSpec,
    ContextSnapshot,
    DataContext,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    content_digest,
)
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    Confidence,
    DataBackend,
    DataMode,
    DependencyPolicy,
    ExecutionKind,
    LegacyMarketCycleStage,
    MappingStatus,
    NodeStatus,
    PlanSource,
    PortfolioRating,
    PositionAction,
    ResearchAction,
    RotationStage,
    SentimentBand,
    Tier,
)
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    StaticLegacyPlanAttestation,
    compute_candidate_plan_digest,
    validate_plan_draft,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "orchestration" / "fixtures" / "legacy_contract_samples.json"
UTC = timezone.utc


@pytest.fixture(scope="module")
def fx() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# =========================================================================== #
# 1. report avoid/accumulate preserved                                        #
# =========================================================================== #
def test_report_avoid_and_accumulate_preserved():
    a = M.migrate_research_action("avoid", source_schema=M.SRC_REPORT_OUTPUT)
    assert a.normalized is ResearchAction.AVOID
    assert a.mapping_status is MappingStatus.MAPPED
    assert a.mapping_basis == "authoritative_code"
    assert a.reason is None
    assert M.research_action_to_legacy(a) == "avoid"

    b = M.migrate_research_action("accumulate", source_schema=M.SRC_ETF_REPORT_OUTPUT)
    assert b.normalized is ResearchAction.ACCUMULATE
    assert M.research_action_to_legacy(b) == "accumulate"


# =========================================================================== #
# 2. position add/reduce preserved                                            #
# =========================================================================== #
def test_position_add_and_reduce_preserved():
    a = M.migrate_position_action("add", source_schema=M.SRC_DECISION_LEG)
    assert a.normalized is PositionAction.ADD
    assert a.mapping_status is MappingStatus.MAPPED
    assert a.mapping_basis == "authoritative_code"
    assert M.position_action_to_legacy(a) == "add"

    b = M.migrate_position_action("reduce", source_schema=M.SRC_WATCH_REC)
    assert b.normalized is PositionAction.REDUCE
    assert M.position_action_to_legacy(b) == "reduce"


# =========================================================================== #
# 3. raw 'hold' -> different result classes under report vs position          #
# =========================================================================== #
def test_hold_is_a_different_result_class_per_domain():
    r = M.migrate_research_action("hold", source_schema=M.SRC_REPORT_OUTPUT)
    p = M.migrate_position_action("hold", source_schema=M.SRC_DECISION_LEG)
    assert type(r) is M.MigratedResearchAction
    assert type(p) is M.MigratedPositionAction
    assert type(r) is not type(p)
    assert isinstance(r.normalized, ResearchAction)
    assert isinstance(p.normalized, PositionAction)


# =========================================================================== #
# 4. canonical lowercase maps exact; case/whitespace raises w/o alias policy   #
# =========================================================================== #
def test_canonical_lowercase_exact_maps_authoritative():
    r = M.migrate_research_action("hold", source_schema=M.SRC_REPORT_OUTPUT)
    assert r.mapping_basis == "authoritative_code"
    assert r.mapping_policy_id is None
    assert r.raw == "hold"


def test_case_or_whitespace_variant_raises_without_policy():
    for bad in (" hold ", "HOLD", "Hold", "hold "):
        with pytest.raises(ValueError):
            M.migrate_research_action(bad, source_schema=M.SRC_REPORT_OUTPUT)
    with pytest.raises(ValueError):
        M.migrate_position_action("ADD", source_schema=M.SRC_DECISION_LEG)


def test_alias_policy_maps_variant_and_reverse_returns_exact_raw():
    r = M.migrate_research_action(
        " HOLD ", source_schema=M.SRC_REPORT_OUTPUT,
        mapping_policy_id=M.POLICY_ACTION_SURFACE_ALIAS_V1,
    )
    assert r.mapping_status is MappingStatus.MAPPED
    assert r.normalized is ResearchAction.HOLD
    assert r.mapping_basis == "approved_policy"
    assert r.mapping_policy_id == M.POLICY_ACTION_SURFACE_ALIAS_V1
    # reverse still returns the EXACT raw, not the normalized lookup key
    assert M.research_action_to_legacy(r) == " HOLD "

    p = M.migrate_position_action(
        "ADD", source_schema=M.SRC_WATCH_REC,
        mapping_policy_id=M.POLICY_ACTION_SURFACE_ALIAS_V1,
    )
    assert p.normalized is PositionAction.ADD
    assert p.mapping_basis == "approved_policy"
    assert M.position_action_to_legacy(p) == "ADD"


def test_unknown_alias_policy_raises():
    with pytest.raises(ValueError):
        M.migrate_research_action("hold", source_schema=M.SRC_REPORT_OUTPUT,
                                  mapping_policy_id="policy.nope.v1")


# =========================================================================== #
# 5. wrong semantic-domain / source passed to an adapter fails                #
# =========================================================================== #
def test_wrong_source_for_adapter_raises():
    # a position source cannot drive the research adapter
    with pytest.raises(ValueError):
        M.migrate_research_action("hold", source_schema=M.SRC_DECISION_LEG)
    # a report source cannot drive the position adapter
    with pytest.raises(ValueError):
        M.migrate_position_action("hold", source_schema=M.SRC_REPORT_OUTPUT)


def test_out_of_domain_value_raises():
    # 'add' is a position verb, not a research verb
    with pytest.raises(ValueError):
        M.migrate_research_action("add", source_schema=M.SRC_REPORT_OUTPUT)
    # 'accumulate' is a research verb, not a position verb
    with pytest.raises(ValueError):
        M.migrate_position_action("accumulate", source_schema=M.SRC_DECISION_LEG)


# =========================================================================== #
# 6. no generic action adapter / model is exported                            #
# =========================================================================== #
def test_no_generic_action_adapter_or_model():
    assert not hasattr(M, "migrate_action")
    assert not hasattr(M, "MigratedAction")
    assert "migrate_action" not in getattr(M, "__all__", ())
    assert "MigratedAction" not in getattr(M, "__all__", ())


# =========================================================================== #
# 7. rating bounds / bool / finite                                            #
# =========================================================================== #
def test_rating_valid_int_is_unmappable_preserving_raw():
    for v in (-10, -3, 0, 5, 10):
        r = M.migrate_rating(v, source_schema=M.SRC_REPORT_OUTPUT)
        assert r.mapping_status is MappingStatus.UNMAPPABLE
        assert r.normalized is None
        assert r.mapping_basis == "none"
        assert r.reason
        assert r.raw == v and type(r.raw) is int
        assert M.rating_to_legacy(r) == v and type(M.rating_to_legacy(r)) is int


def test_rating_out_of_bounds_raises():
    for bad in (-11, 11, 100, -100):
        with pytest.raises(ValueError):
            M.migrate_rating(bad, source_schema=M.SRC_REPORT_OUTPUT)


def test_rating_rejects_bool_and_float():
    with pytest.raises(ValueError):
        M.migrate_rating(True, source_schema=M.SRC_REPORT_OUTPUT)
    with pytest.raises(ValueError):
        M.migrate_rating(5.0, source_schema=M.SRC_REPORT_OUTPUT)


def test_rating_unknown_source_and_unknown_policy_raise():
    with pytest.raises(ValueError):
        M.migrate_rating(5, source_schema=SchemaRef(name="Nope", version="1"))
    # no approved band policy exists in Task 0
    with pytest.raises(ValueError):
        M.migrate_rating(5, source_schema=M.SRC_REPORT_OUTPUT, mapping_policy_id="band.v1")


def test_no_five_band_rating_is_fabricated():
    r = M.migrate_rating(10, source_schema=M.SRC_REPORT_OUTPUT)
    assert r.normalized is None  # never a guessed PortfolioRating
    assert PortfolioRating  # imported, but never produced by the adapter


# =========================================================================== #
# 8. confidence 'med' per Task-0 evidence row                                 #
# =========================================================================== #
def test_confidence_low_high_authoritative_identity():
    lo = M.migrate_confidence("low", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert lo.normalized is Confidence.LOW
    assert lo.mapping_basis == "authoritative_code"
    assert M.confidence_to_legacy(lo) == "low"

    hi = M.migrate_confidence("high", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert hi.normalized is Confidence.HIGH
    assert M.confidence_to_legacy(hi) == "high"


def test_confidence_med_is_unmappable_preserving_raw():
    m = M.migrate_confidence("med", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert m.mapping_status is MappingStatus.UNMAPPABLE
    assert m.normalized is None
    assert m.mapping_basis == "none"
    assert m.reason
    assert M.confidence_to_legacy(m) == "med"


def test_confidence_rejects_bool_numeric_and_unknown_source():
    with pytest.raises(ValueError):
        M.migrate_confidence(True, source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    with pytest.raises(ValueError):
        M.migrate_confidence(3, source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    with pytest.raises(ValueError):
        M.migrate_confidence("low", source_schema=SchemaRef(name="Nope", version="1"))


# =========================================================================== #
# 9. numeric sentiment requires the source's fixed scale                      #
# =========================================================================== #
def test_numeric_sentiment_requires_fixed_scale():
    ok = M.migrate_sentiment(1.0, source_schema=M.SRC_TAG_SCORE, scale="pm1")
    assert ok.mapping_status is MappingStatus.UNMAPPABLE
    assert ok.source_scale == "pm1"
    assert M.sentiment_to_legacy(ok) == 1.0

    # wrong scale for this source is rejected (caller cannot reinterpret producer)
    with pytest.raises(ValueError):
        M.migrate_sentiment(1.0, source_schema=M.SRC_TAG_SCORE, scale="zero_ten")
    # a numeric source with a reviewed scale requires the scale explicitly
    with pytest.raises(ValueError):
        M.migrate_sentiment(1.0, source_schema=M.SRC_TAG_SCORE, scale=None)
    # out of range for pm1
    with pytest.raises(ValueError):
        M.migrate_sentiment(5.0, source_schema=M.SRC_TAG_SCORE, scale="pm1")


def test_sentiment_categorical_unmappable_preserves_raw():
    s = M.migrate_sentiment("利好", source_schema=M.SRC_NEWS_SENTIMENT)
    assert s.mapping_status is MappingStatus.UNMAPPABLE
    assert s.normalized is None
    assert s.source_scale is None
    assert M.sentiment_to_legacy(s) == "利好"
    assert SentimentBand  # imported, never produced


def test_sentiment_rejects_bool_and_spurious_scale():
    with pytest.raises(ValueError):
        M.migrate_sentiment(True, source_schema=M.SRC_TAG_SCORE, scale="pm1")
    # a temperature source has no reviewed pm1/zero_ten scale; supplying one raises
    with pytest.raises(ValueError):
        M.migrate_sentiment(50.0, source_schema=M.SRC_ASTOCK_TEMP, scale="pm1")


# =========================================================================== #
# 10. known non-equivalent rotation stages -> UNMAPPABLE                       #
# =========================================================================== #
def test_rotation_stages_are_unmappable():
    for stage in ("冰点", "分化", "逼空", "发酵", "回踩/启动"):
        r = M.migrate_rotation_stage(stage, source_schema=M.SRC_MARKET_CYCLE)
        assert r.mapping_status is MappingStatus.UNMAPPABLE
        assert r.normalized is None
        assert r.reason
        assert M.rotation_stage_to_legacy(r) == stage
    # 分化 is NOT collapsed into RotationStage.DIVERGENCE by string intuition
    r = M.migrate_rotation_stage("分化", source_schema=M.SRC_MARKET_CYCLE)
    assert r.normalized is not RotationStage.DIVERGENCE
    assert LegacyMarketCycleStage("分化")  # legacy enum still recognises the value


# =========================================================================== #
# 11. unknown source / raw raises                                             #
# =========================================================================== #
def test_unknown_source_or_raw_raises():
    with pytest.raises(ValueError):
        M.migrate_rotation_stage("冰点", source_schema=SchemaRef(name="Nope", version="1"))
    with pytest.raises(ValueError):
        M.migrate_rotation_stage("bull-market", source_schema=M.SRC_MARKET_CYCLE)
    with pytest.raises(ValueError):
        M.migrate_research_action("hodl", source_schema=M.SRC_REPORT_OUTPUT)


# =========================================================================== #
# 12. every exact scalar fixture round-trips identical raw type + value       #
# =========================================================================== #
def test_exact_scalar_fixtures_round_trip_identically(fx):
    for s in fx["scalars"]:
        if s["roundtrip_policy"] != "exact":
            continue
        ss = s["source_schema"]
        if s["kind"] == "action" and s["semantic_domain"] == "research_recommendation":
            src = M.SRC_REPORT_OUTPUT if "tier3" in ss else M.SRC_ETF_REPORT_OUTPUT
            for v in s["samples"]:
                got = M.research_action_to_legacy(
                    M.migrate_research_action(v, source_schema=src))
                assert got == v and type(got) is str
        elif s["kind"] == "action" and s["semantic_domain"] == "position_adjustment":
            src = M.SRC_DECISION_LEG if "backtest" in ss else M.SRC_WATCH_REC
            for v in s["samples"]:
                got = M.position_action_to_legacy(
                    M.migrate_position_action(v, source_schema=src))
                assert got == v and type(got) is str
        elif s["kind"] == "stage":
            for v in s["samples"]:
                got = M.rotation_stage_to_legacy(
                    M.migrate_rotation_stage(v, source_schema=M.SRC_MARKET_CYCLE))
                assert got == v and type(got) is str


def test_int_vs_float_preserved_distinctly_and_round_trip():
    # 50 (int) and 50.0 (float) are distinct raws and each reverses to its type
    i = M.migrate_sentiment(50, source_schema=M.SRC_ASTOCK_TEMP)
    f = M.migrate_sentiment(50.0, source_schema=M.SRC_ASTOCK_TEMP)
    assert i.raw_kind == "int" and type(i.raw) is int
    assert f.raw_kind == "float" and type(f.raw) is float
    assert M.sentiment_to_legacy(i) == 50 and type(M.sentiment_to_legacy(i)) is int
    assert M.sentiment_to_legacy(f) == 50.0 and type(M.sentiment_to_legacy(f)) is float


def test_unmappable_values_round_trip_exactly():
    r = M.migrate_rating(0, source_schema=M.SRC_REPORT_OUTPUT)
    assert M.rating_to_legacy(r) == 0
    c = M.migrate_confidence("med", source_schema=M.SRC_INTROSPECTION_PROPOSAL)
    assert M.confidence_to_legacy(c) == "med"


# =========================================================================== #
# 13. module-qualified source keys avoid stock/ETF collision                  #
# =========================================================================== #
def test_source_keys_are_module_qualified_and_distinct():
    assert M.SRC_REPORT_OUTPUT != M.SRC_ETF_REPORT_OUTPUT
    assert M.SRC_REPORT_OUTPUT.name != M.SRC_ETF_REPORT_OUTPUT.name
    # full module path (not an anonymous short 'ReportOutput')
    assert "report_writer" in M.SRC_REPORT_OUTPUT.name
    assert "etf" in M.SRC_ETF_REPORT_OUTPUT.name
    # the two report sources drive the SAME adapter but record distinct provenance
    a = M.migrate_research_action("buy", source_schema=M.SRC_REPORT_OUTPUT)
    b = M.migrate_research_action("buy", source_schema=M.SRC_ETF_REPORT_OUTPUT)
    assert a.source_schema != b.source_schema


# =========================================================================== #
# 14. graph config round-trips to exact normalized object + digest            #
# =========================================================================== #
def test_graph_config_round_trips_to_normalized_object_and_digest(fx):
    g = fx["graphs"][0]
    norm = g["normalized_config"]
    out = M.normalize_legacy_graph_config(norm, source_format="json")
    assert out == norm
    assert content_digest(out) == g["config_digest"]


def test_yaml_normalizer_preserves_json_scalar_types_and_list_order():
    yaml_text = "n: 3\nx: 1.5\nb: true\ns: hi\nz: null\nlst:\n  - c\n  - a\n  - b\n"
    out = M.normalize_legacy_graph_config(yaml_text, source_format="yaml")
    assert out == {"n": 3, "x": 1.5, "b": True, "s": "hi", "z": None,
                   "lst": ["c", "a", "b"]}
    assert type(out["n"]) is int and type(out["x"]) is float


# =========================================================================== #
# 15. YAML dup/non-string-key/tag/timestamp/merge/anchor/non-finite rejected  #
# =========================================================================== #
@pytest.mark.parametrize("bad", [
    "a: 1\na: 2\n",                       # duplicate key
    "1: a\n",                             # non-string key
    "d: 2020-01-01\n",                    # timestamp / date
    "x: !!binary aGk=\n",                 # binary
    "x: !CustomTag 5\n",                  # custom tag
    "base: &a {k: 1}\nuse:\n  <<: *a\n",  # merge key
    "a: &x 1\nb: *x\n",                   # anchor / alias
    "f: .inf\n",                          # non-finite float
    "f: .nan\n",                          # NaN
])
def test_yaml_rejections(bad):
    with pytest.raises(ValueError):
        M.normalize_legacy_graph_config(bad, source_format="yaml")


def test_non_object_top_level_rejected():
    with pytest.raises(ValueError):
        M.normalize_legacy_graph_config("- 1\n- 2\n", source_format="yaml")


# =========================================================================== #
# 16. reviewed hard/soft semantics retained; unknown nodes/edges UNMAPPABLE   #
# =========================================================================== #
def test_stock_deep_dive_graph_is_unmappable_but_retains_dep_semantics(fx):
    g = fx["graphs"][0]
    mapping = M.migrate_legacy_graph(
        g["normalized_config"], source_schema=M.SRC_STOCK_DEEP_DIVE, source_format="json")
    # workers are all planned/unresolved in Phase 1 -> whole graph UNMAPPABLE
    assert mapping.mapping_status is MappingStatus.UNMAPPABLE
    assert mapping.reason
    assert all(w.mapping_status is MappingStatus.UNMAPPABLE for w in mapping.worker_mappings)
    assert all(i.mapping_status is MappingStatus.UNMAPPABLE for i in mapping.input_mappings)
    # dependency mappings DO retain the reviewed hard/soft semantics
    hard = next(d for d in mapping.dependency_mappings if d.source_strength == "hard")
    assert hard.target_policy is DependencyPolicy.BLOCK
    assert tuple(hard.accepted_statuses) == (NodeStatus.COMPLETED,)
    assert hard.missing_output_behavior == "block"
    soft = next(d for d in mapping.dependency_mappings if d.source_strength == "soft")
    assert soft.target_policy is DependencyPolicy.DEGRADE
    assert soft.missing_output_behavior == "degrade"
    assert NodeStatus.COMPLETED in soft.accepted_statuses


def test_graph_config_digest_matches_recorded(fx):
    g = fx["graphs"][0]
    mapping = M.migrate_legacy_graph(
        g["normalized_config"], source_schema=M.SRC_STOCK_DEEP_DIVE, source_format="json")
    assert mapping.source_config_digest == g["config_digest"]
    assert M.legacy_graph_to_normalized_config(mapping) == g["normalized_config"]


def test_unknown_graph_source_raises(fx):
    g = fx["graphs"][0]
    with pytest.raises(ValueError):
        M.migrate_legacy_graph(g["normalized_config"],
                               source_schema=SchemaRef(name="Nope", version="1"),
                               source_format="json")


# =========================================================================== #
# 17. incomplete graph cannot produce binding / attestation / Plan            #
# =========================================================================== #
def test_unmappable_graph_refuses_binding_and_attestation(fx):
    g = fx["graphs"][0]
    mapping = M.migrate_legacy_graph(
        g["normalized_config"], source_schema=M.SRC_STOCK_DEEP_DIVE, source_format="json")
    with pytest.raises(ValueError):
        M.compatibility_binding_for(mapping)


# --------------------------------------------------------------------------- #
# helpers to build a synthetic FULLY-MAPPED legacy graph                       #
# --------------------------------------------------------------------------- #
SR_UP = SchemaRef(name="QuoteArtifact", version="1")


def _worker_map(node="consumer", target="quant.fundamentals", status=MappingStatus.MAPPED):
    if status is MappingStatus.MAPPED:
        return M.LegacyWorkerMapping(
            source_node_id=node, raw_node={"deps": []}, target_worker_id=target,
            mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
            mapping_policy_id=None, reason=None)
    return M.LegacyWorkerMapping(
        source_node_id=node, raw_node={"deps": []}, target_worker_id=None,
        mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
        mapping_policy_id=None, reason="worker unresolved")


def _dep_map(up="quote", down="consumer", strength="hard"):
    if strength == "hard":
        return M.LegacyDependencyMapping(
            source_upstream_node_id=up, source_downstream_node_id=down,
            raw_edge={"kind": "hard"}, source_strength="hard",
            accepted_statuses=(NodeStatus.COMPLETED,), missing_output_behavior="block",
            target_policy=DependencyPolicy.BLOCK, mapping_status=MappingStatus.MAPPED,
            mapping_basis="authoritative_code", mapping_policy_id=None, reason=None)
    return M.LegacyDependencyMapping(
        source_upstream_node_id=up, source_downstream_node_id=down,
        raw_edge={"kind": "soft"}, source_strength="soft",
        accepted_statuses=(NodeStatus.COMPLETED, NodeStatus.DEGRADED),
        missing_output_behavior="degrade", target_policy=DependencyPolicy.DEGRADE,
        mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
        mapping_policy_id=None, reason=None)


def _base_input(consumer="consumer", key="code", target_kind="param",
                param_key="code", context_field=None, service=None):
    return M.LegacyInputMapping(
        source_consumer_node_id=consumer, source_key=key, source_kind="base",
        source_upstream_node_id=None, target_kind=target_kind,
        target_input_binding=None, target_param_key=param_key,
        target_context_field=context_field, target_service_binding=service,
        upstream_output_schema_ref=None, projection="raw", projection_field=None,
        missing_behavior="error", mapping_status=MappingStatus.MAPPED,
        mapping_basis="authoritative_code", mapping_policy_id=None,
        mapping_evidence="reviewed base param", reason=None)


def _upstream_input(consumer="consumer", key="quote", up="quote",
                    binding="quote_in", missing="error", projection="raw",
                    projection_field=None):
    return M.LegacyInputMapping(
        source_consumer_node_id=consumer, source_key=key, source_kind="upstream",
        source_upstream_node_id=up, target_kind="input_binding",
        target_input_binding=binding, target_param_key=None, target_context_field=None,
        target_service_binding=None, upstream_output_schema_ref=SR_UP,
        projection=projection, projection_field=projection_field,
        missing_behavior=missing, mapping_status=MappingStatus.MAPPED,
        mapping_basis="authoritative_code", mapping_policy_id=None,
        mapping_evidence="reviewed upstream edge", reason=None)


def _mapped_graph(*, worker_mappings=None, dependency_mappings=None,
                  input_mappings=None, config=None, source_format="json"):
    config = config if config is not None else {"nodes": {"consumer": {}, "quote": {}}}
    wm = worker_mappings if worker_mappings is not None else (
        _worker_map("quote", "compat.quote_fetcher"), _worker_map("consumer"))
    dm = dependency_mappings if dependency_mappings is not None else (_dep_map(),)
    im = input_mappings if input_mappings is not None else (
        _base_input(), _upstream_input())
    norm = M.normalize_legacy_graph_config(config, source_format=source_format)
    return M.LegacyGraphMapping(
        adapter_version="v1", source_schema=M.SRC_STOCK_DEEP_DIVE,
        source_format=source_format, normalized_raw_config=norm,
        source_config_digest=content_digest(norm),
        worker_mappings=tuple(wm), dependency_mappings=tuple(dm),
        input_mappings=tuple(im), mapping_status=MappingStatus.MAPPED, reason=None)


# =========================================================================== #
# 18. input source-kind matrix                                                #
# =========================================================================== #
def test_base_input_forbids_upstream_fields():
    with pytest.raises(ValidationError):
        M.LegacyInputMapping(
            source_consumer_node_id="c", source_key="code", source_kind="base",
            source_upstream_node_id="quote",  # forbidden for base
            target_kind="param", target_param_key="code",
            projection="raw", missing_behavior="error",
            mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
            mapping_evidence="e", reason=None)


def test_upstream_mapped_requires_node_and_output_schema():
    # mapped upstream WITHOUT upstream_output_schema_ref is invalid
    with pytest.raises(ValidationError):
        M.LegacyInputMapping(
            source_consumer_node_id="c", source_key="quote", source_kind="upstream",
            source_upstream_node_id="quote", target_kind="input_binding",
            target_input_binding="quote_in", upstream_output_schema_ref=None,
            projection="raw", missing_behavior="error",
            mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
            mapping_evidence="e", reason=None)


def test_unmappable_upstream_permits_missing_schema():
    ok = M.LegacyInputMapping(
        source_consumer_node_id="c", source_key="quote", source_kind="upstream",
        source_upstream_node_id="quote", target_kind=None, upstream_output_schema_ref=None,
        projection="raw", missing_behavior="unknown",
        mapping_status=MappingStatus.UNMAPPABLE, mapping_basis="none",
        mapping_evidence=None, reason="phase-1 unresolved")
    assert ok.mapping_status is MappingStatus.UNMAPPABLE
    assert ok.upstream_output_schema_ref is None


# =========================================================================== #
# 19. target-kind matrix: exactly one matching target; reject caller paths     #
# =========================================================================== #
def test_target_kind_requires_exactly_one_matching_field():
    # param target must not also carry an input-binding target
    with pytest.raises(ValidationError):
        M.LegacyInputMapping(
            source_consumer_node_id="c", source_key="code", source_kind="base",
            target_kind="param", target_param_key="code",
            target_input_binding="quote_in",  # extra, non-matching
            projection="raw", missing_behavior="error",
            mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
            mapping_evidence="e", reason=None)


def test_service_binding_rejects_caller_path():
    with pytest.raises(ValidationError):
        M.LegacyInputMapping(
            source_consumer_node_id="c", source_key="out", source_kind="base",
            target_kind="service_binding", target_service_binding="/tmp/out.json",
            projection="raw", missing_behavior="error",
            mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
            mapping_evidence="e", reason=None)


def test_service_binding_allowlisted_name_ok():
    ok = M.LegacyInputMapping(
        source_consumer_node_id="c", source_key="out", source_kind="base",
        target_kind="service_binding", target_service_binding="output_locator",
        projection="raw", missing_behavior="omit",
        mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
        mapping_evidence="reviewed service", reason=None)
    assert ok.target_service_binding == "output_locator"


# =========================================================================== #
# 20. projection matrix                                                       #
# =========================================================================== #
def test_single_field_unwrap_requires_projection_field():
    with pytest.raises(ValidationError):
        _upstream_input(projection="single_field_unwrap", projection_field=None)
    ok = _upstream_input(projection="single_field_unwrap", projection_field="value")
    assert ok.projection_field == "value"


def test_non_raw_projection_forbidden_for_base_input():
    with pytest.raises(ValidationError):
        M.LegacyInputMapping(
            source_consumer_node_id="c", source_key="code", source_kind="base",
            target_kind="param", target_param_key="code",
            projection="model_dump",  # non-raw not allowed for base
            missing_behavior="error", mapping_status=MappingStatus.MAPPED,
            mapping_basis="authoritative_code", mapping_evidence="e", reason=None)


def test_mapped_input_requires_evidence_and_known_missing_behavior():
    assert _base_input()  # baseline constructs
    with pytest.raises(ValidationError):
        M.LegacyInputMapping(
            source_consumer_node_id="c", source_key="code", source_kind="base",
            target_kind="param", target_param_key="code", projection="raw",
            missing_behavior="unknown",  # mapped may not be 'unknown'
            mapping_status=MappingStatus.MAPPED, mapping_basis="authoritative_code",
            mapping_evidence="e", reason=None)


# =========================================================================== #
# 21. BLOCK/DEGRADE/SKIP vs error/omit/inject_none/skip_consumer consistency   #
# =========================================================================== #
def test_direct_dependency_block_requires_error_behavior():
    # a direct BLOCK dep whose consumer input uses 'omit' is inconsistent
    bad_input = _upstream_input(missing="omit")
    with pytest.raises(ValueError):
        M.LegacyGraphMapping(
            adapter_version="v1", source_schema=M.SRC_STOCK_DEEP_DIVE,
            source_format="json",
            normalized_raw_config={"nodes": {"consumer": {}, "quote": {}}},
            source_config_digest=content_digest({"nodes": {"consumer": {}, "quote": {}}}),
            worker_mappings=(_worker_map("quote", "compat.quote_fetcher"),
                             _worker_map("consumer")),
            dependency_mappings=(_dep_map(strength="hard"),),
            input_mappings=(_base_input(), bad_input),
            mapping_status=MappingStatus.MAPPED, reason=None)


def test_consistent_block_dependency_graph_is_valid():
    g = _mapped_graph()  # hard dep + upstream input missing='error'
    assert g.mapping_status is MappingStatus.MAPPED


# =========================================================================== #
# 22. duplicate keys / status matrices / no name-guessing                     #
# =========================================================================== #
def test_duplicate_consumer_key_is_rejected():
    with pytest.raises(ValueError):
        M.LegacyGraphMapping(
            adapter_version="v1", source_schema=M.SRC_STOCK_DEEP_DIVE,
            source_format="json",
            normalized_raw_config={"nodes": {"consumer": {}, "quote": {}}},
            source_config_digest=content_digest({"nodes": {"consumer": {}, "quote": {}}}),
            worker_mappings=(_worker_map("quote", "compat.quote_fetcher"),
                             _worker_map("consumer")),
            dependency_mappings=(_dep_map(),),
            input_mappings=(_base_input(key="code"), _base_input(key="code")),
            mapping_status=MappingStatus.MAPPED, reason=None)


def test_graph_status_matrix_mapped_requires_all_nested_mapped():
    # one unmappable worker forces the graph to be UNMAPPABLE (not MAPPED)
    with pytest.raises(ValueError):
        M.LegacyGraphMapping(
            adapter_version="v1", source_schema=M.SRC_STOCK_DEEP_DIVE,
            source_format="json",
            normalized_raw_config={"nodes": {"consumer": {}, "quote": {}}},
            source_config_digest=content_digest({"nodes": {"consumer": {}, "quote": {}}}),
            worker_mappings=(_worker_map("quote", status=MappingStatus.UNMAPPABLE),
                             _worker_map("consumer")),
            dependency_mappings=(_dep_map(),),
            input_mappings=(_base_input(), _upstream_input()),
            mapping_status=MappingStatus.MAPPED, reason=None)  # lies about MAPPED


# =========================================================================== #
# heavy scaffolding for attestation (mirrors test_spec.py)                     #
# =========================================================================== #
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64


def _dt(minute: int = 30) -> datetime:
    return datetime(2026, 7, 15, 1, minute, tzinfo=UTC)


class QuoteArtifact(DigestModel):
    schema_version: Literal["1"] = "1"
    value: FiniteFloat = 1.0


class ConsumerOut(DigestModel):
    schema_version: Literal["1"] = "1"
    note: NonEmptyStr = "n"


class ConsumerParams(DigestModel):
    schema_version: Literal["1"] = "1"
    code: NonEmptyStr = "600519"


SR_CONS = SchemaRef(name="ConsumerOut", version="1")
SR_CPARAMS = SchemaRef(name="ConsumerParams", version="1")


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(QuoteArtifact)
    reg.register(ConsumerOut)
    reg.register(ConsumerParams)
    reg.seal()
    return reg


def _make_text(id: str, kind: str, text: str):
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=id, version="1", content_digest="0" * 64), kind=kind, raw_utf8=raw)
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=id, version="1", content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def _llm_worker(id: str, *, outputs, inputs=(), params_schema_ref=None):
    pref, pmat = _make_text(f"{id}.prompt", "prompt", f"You are {id}.")
    pentry = ContentManifestEntry(
        ref=pref, kind="prompt", name=id, description="d", source_identity="gl.src")
    w = WorkerSpec(
        id=id, catalog_role="final", selection_scope="dynamic_allowed", lane="quant",
        persona="p", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=pref, inputs=tuple(inputs), outputs=tuple(outputs),
        params_schema_ref=params_schema_ref, evidence_policy=EvidencePolicy(),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none")
    return w, [pmat], [pentry]


def _catalog():
    up = _llm_worker("up.worker", outputs=(OutputBinding(name="primary", schema_ref=SR_UP),))
    sink = _llm_worker(
        "sink.worker",
        inputs=(InputBinding(name="feed", schema_ref=SR_UP, required=True),),
        outputs=(OutputBinding(name="primary", schema_ref=SR_CONS),),
        params_schema_ref=SR_CPARAMS)
    workers, mats, content = [], [], []
    for w, wmats, wentries in (up, sink):
        workers.append(w)
        mats += wmats
        content += wentries
    return build_catalog_snapshot(
        catalog_version="cat.v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=(), workers=tuple(workers), resolved_material=tuple(mats))


def _context(*, config_digest: str = DA) -> ContextSnapshot:
    binding = build_empty_memory_binding()
    clock = ClockSpec(as_of=_dt(), timezone="Asia/Shanghai", calendar_id="cn_a_share")
    dc = DataContext(
        as_of=_dt(), clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="cn_a_share", resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest=config_digest, source_registry_digest=DB, routing_snapshot_digest=DC,
        data_snapshot_id="snap-1", data_snapshot_content_digest=DD, built_at=_dt())
    sel = PayloadRef(namespace="main", object_id="sel-1", content_digest=binding.past_context_hash)
    return ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash, past_context_hash=binding.past_context_hash,
        memory_selection_ref=sel, built_at=_dt())


def _preset_request() -> OrchestrationRequest:
    return OrchestrationRequest(request_id="req-1", goal="g", workflow="orchestrate_only",
                                approval_policy=ApprovalPolicy.REQUIRED)


def _preset_draft(catalog, registry, context, mapping):
    up_node = PlanNode(id="up", worker_id="up.worker", writes_slot="su")
    from guanlan_v2.orchestration.spec import Dependency
    sink_node = PlanNode(
        id="sink", worker_id="sink.worker", writes_slot="ss",
        params={"code": "600519"},
        dependencies=(Dependency(upstream_node_id="up", artifact_slot="su", inject_as="feed"),))
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    return PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.PRESET, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=(up_node, sink_node), sink_node_ids=("sink",),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=1000, budget_request_llm_invocations=2, max_concurrency=2,
        legacy_source_schema=mapping.source_schema,
        legacy_source_config_digest=mapping.source_config_digest,
        legacy_mapping_digest=mapping.semantic_digest())


# =========================================================================== #
# 23. binding / attestation digests bind graph + config; move on change        #
# =========================================================================== #
def test_binding_binds_config_and_mapping_digests():
    g = _mapped_graph()
    binding = M.compatibility_binding_for(g)
    assert isinstance(binding, CompatibilityBinding)
    assert binding.source_config_digest == g.source_config_digest
    assert binding.legacy_mapping_digest == g.semantic_digest()
    assert binding.legacy_source_schema == g.source_schema


def test_binding_digest_moves_when_a_worker_mapping_changes():
    g1 = _mapped_graph()
    g2 = _mapped_graph(worker_mappings=(
        _worker_map("quote", "compat.quote_fetcher"),
        _worker_map("consumer", target="quant.model")))  # different target
    assert g1.semantic_digest() != g2.semantic_digest()
    assert (M.compatibility_binding_for(g1).legacy_mapping_digest
            != M.compatibility_binding_for(g2).legacy_mapping_digest)


def test_binding_digest_moves_when_config_changes():
    g1 = _mapped_graph(config={"nodes": {"consumer": {}, "quote": {}}})
    g2 = _mapped_graph(config={"nodes": {"consumer": {}, "quote": {}}, "v": 2})
    assert g1.source_config_digest != g2.source_config_digest
    assert (M.compatibility_binding_for(g1).source_config_digest
            != M.compatibility_binding_for(g2).source_config_digest)


def test_input_mapping_is_part_of_semantic_digest():
    g1 = _mapped_graph()
    g2 = _mapped_graph(input_mappings=(
        _base_input(), _upstream_input(projection="single_field_unwrap",
                                       projection_field="value")))
    assert g1.semantic_digest() != g2.semantic_digest()


# =========================================================================== #
# 24. attestation candidate digest == validator/freeze output                  #
# =========================================================================== #
def test_attestation_candidate_digest_equals_validator_output():
    catalog, registry, context = _catalog(), _registry(), _context()
    g = _mapped_graph()
    request = _preset_request()
    draft = _preset_draft(catalog, registry, context, g)
    att = M.attest_static_legacy_plan(
        g, draft, request, context=context, catalog=catalog, schema_registry=registry)
    assert isinstance(att, StaticLegacyPlanAttestation)
    expected = compute_candidate_plan_digest(
        request=request, draft=draft, context_content_digest=context.content_digest)
    assert att.candidate_plan_digest == expected
    # the validator computes the same candidate digest for the same inputs
    report = validate_plan_draft(
        draft, request=request, context=context, catalog=catalog,
        schema_registry=registry, legacy_attestation=att)
    assert report.candidate_plan_digest == att.candidate_plan_digest
    # attestation binds the mapping + config digests
    assert att.legacy_mapping_digest == g.semantic_digest()
    assert att.source_config_digest == g.source_config_digest


def test_attestation_candidate_digest_moves_with_context():
    catalog, registry = _catalog(), _registry()
    g = _mapped_graph()
    request = _preset_request()
    ca, cb = _context(config_digest=DA), _context(config_digest=DB)
    da = M.attest_static_legacy_plan(
        g, _preset_draft(catalog, registry, ca, g), request,
        context=ca, catalog=catalog, schema_registry=registry).candidate_plan_digest
    db = M.attest_static_legacy_plan(
        g, _preset_draft(catalog, registry, cb, g), request,
        context=cb, catalog=catalog, schema_registry=registry).candidate_plan_digest
    assert da != db


def test_attestation_refuses_unmappable_graph(fx):
    catalog, registry, context = _catalog(), _registry(), _context()
    gm = M.migrate_legacy_graph(
        fx["graphs"][0]["normalized_config"], source_schema=M.SRC_STOCK_DEEP_DIVE,
        source_format="json")
    request = _preset_request()
    # the builder must refuse an unmappable mapping regardless of the draft
    with pytest.raises(ValueError):
        M.attest_static_legacy_plan(
            gm, _preset_draft(catalog, registry, context, gm), request,
            context=context, catalog=catalog, schema_registry=registry)


# =========================================================================== #
# 25. full stock-deep-dive fixture records phase-2 equivalence facts           #
# =========================================================================== #
def test_full_stock_deep_dive_records_node_and_edge_facts(fx):
    g = fx["graphs"][0]
    mapping = M.migrate_legacy_graph(
        g["normalized_config"], source_schema=M.SRC_STOCK_DEEP_DIVE, source_format="json")
    # every reviewed node has a worker mapping in normalized declaration order
    node_ids = [w.source_node_id for w in mapping.worker_mappings]
    assert node_ids == list(g["normalized_config"]["nodes"].keys())
    # every hard/soft edge is represented with retained semantics
    n_hard = sum(1 for d in mapping.dependency_mappings if d.source_strength == "hard")
    n_soft = sum(1 for d in mapping.dependency_mappings if d.source_strength == "soft")
    assert n_hard > 0 and n_soft > 0
    # base vs upstream input origins are recorded (not guessed): code/asof_date are base
    base_keys = {i.source_key for i in mapping.input_mappings if i.source_kind == "base"}
    assert {"code", "asof_date"} <= base_keys
    # phase-1: no input resolves to a runnable binding yet
    assert all(i.mapping_status is MappingStatus.UNMAPPABLE for i in mapping.input_mappings)
