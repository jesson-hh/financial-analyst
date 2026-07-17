# -*- coding: utf-8 -*-
"""Phase 2 · Task 10 — attested stock-deep-dive execution equivalence (acceptance).

Proves that the admitted Phase-2 static runtime executes the attested legacy
``stock-deep-dive`` compatibility plan with the same reviewed semantics as the real
legacy engine ``Orchestrator``/``DAGNode`` — under the **user-ruled scope adjustment
(2026-07-17)**: equivalence VALUE assertions are scoped to the reviewed honest core
(``news-sentiment -> compat.news_sentiment`` and ``report-writer ->
compat.report_writer``, connected by the fixture's SOFT edge / DEGRADE semantics);
the 16 excluded data agents are documented exclusions whose real schemas arrive with
Phase 3 data/PIT; the full-18 config digest attestation and "the authoritative graph
is UNMAPPABLE" stay as negative-control anchors.

Assertion-by-assertion scope (brief numbering):

1.  node set / dependency partial order / executed set — **full-graph shape** (the
    real 18-node engine Orchestrator over the authoritative YAML) + core layers.
2.  per-node canonical terminal outcome — **core value** (reviewed outcome-mapping
    table in the fixture; DEGRADED for a legacy ok-with-omitted-soft-input, FAILED
    for a legacy error; BLOCKED is legacy-authoritative on the full graph and
    runtime-proven on an explicitly **synthetic** BLOCK graph).
3.  every core LegacyInputMapping row — **core value** (base ``code``/``asof_date``
    -> context, ``out_dir`` -> service binding, upstream -> input_binding satisfied
    AND missing). Multi-field ``model_dump`` injection is observed on the real
    legacy graph; single-field unwrap is observed on the real legacy graph via the
    non-core doubles (shape observation only — no core mapping row claims it);
    the recorded-not-applied projection row is locked in ``test_presets``
    (**synthetic-labeled** there).
4.  every core LegacyDependencyMapping row — **core value** for the SOFT/DEGRADE
    edge (both states); hard/BLOCK is **synthetic-labeled** (runtime) plus
    **legacy-authoritative full-graph** (real hard edges cascade-block).
5.  artifact slot / output key / SchemaRef per core node — **core value**.
6.  canonical normalized typed payload + content digest — **core value over the
    deterministic HARNESS artifacts**: both core planned workers are LLM per the
    Task-0 evidence, so byte-comparison applies to the typed payloads the fake
    gateways produce from fixed fixture outputs (canonically identical across
    old/new and across fresh-env/replay runs). Live LLM prose is deliberately not
    byte-compared; there are NO legacy-deterministic core nodes.
7.  sink outcomes + public LayerCommit boundaries — **core value**.
8.  no uncommitted staged artifact visible — **core value** (crash-before-barrier).
9.  Plan/Artifact/NodeRun provenance binds the attested config/mapping/catalog and
    input/evidence identity — **core value**.

Completion-order permutations: the reviewed core is a two-layer chain (one node per
layer), so intra-layer permutation is **degenerate at core scope**; the harness
instead proves cross-run/replay byte-stability of every committed artifact and
LayerCommit, and the general permutation property is locked by
``test_dag.py::test_stable_artifact_sequence_independent_of_completion_order``.

Hermetic: deterministic legacy SubAgent doubles (real node names, reviewed output
schemas) drive the REAL engine ``Orchestrator`` over the full 18-node authoritative
graph; the new side resolves compat WorkerSpecs and fake single-shot model gateways
only through ``CatalogRuntime``; no live data / LLM / memory / filesystem output —
a service-owned temporary locator stands in for legacy ``out_dir`` and a tmp
memory_root (read-only at init) for ``AgentMemory``. The engine import comes from
``tests/conftest.py``'s repo-engine pin (asserted below — no manual PYTHONPATH).

Run: ``python -m pytest tests/orchestration/test_engine_equivalence.py -v``
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import financial_analyst
from financial_analyst.agent.base import SubAgent
from financial_analyst.agent.orchestrator import DAGNode, Orchestrator
from pydantic import BaseModel

from guanlan_v2.orchestration import dag as D
from guanlan_v2.orchestration import migration as M
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.admission import (
    AdmissionRejected,
    ApprovalSubmission,
    PlanAdmissionService,
)
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
)
from guanlan_v2.orchestration.context import RunBudget, RunContext
from guanlan_v2.orchestration.digest import canonical_json, content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    Confidence,
    MappingStatus,
    NodeStatus,
    PlanSource,
    PortfolioRating,
    SentimentBand,
)
from guanlan_v2.orchestration.eventstore import (
    EventRefusalAuditSink,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.events import EventType, PlanApproval
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.refs import SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    default_audit_detail_registry,
    phase2_runtime_registry,
    static_runtime_profile,
)
from guanlan_v2.orchestration.schema_registry import default_registry
from guanlan_v2.orchestration.schemas import ResearchPlan, SentimentReport

from tests.orchestration.test_presets import (
    _synthetic_graph_catalog,
    _synthetic_graph_mapping,
)

UTC = timezone.utc

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_EQUIV_FIXTURE = _FIXTURES_DIR / "stock_deep_dive_equivalence_v1.json"
_LEGACY_FIXTURE = _FIXTURES_DIR / "legacy_contract_samples.json"

_SR_SENTIMENT = SchemaRef(name="SentimentReport", version="1")

_STATUS_BY_TOKEN = {
    "completed": NodeStatus.COMPLETED,
    "degraded": NodeStatus.DEGRADED,
    "failed": NodeStatus.FAILED,
    "blocked": NodeStatus.BLOCKED,
}


class FixedClock:
    """Deterministic aware-UTC clock (constant instant → byte-stable replay)."""

    def now(self) -> datetime:
        return datetime(2026, 7, 17, 3, 0, tzinfo=UTC)


# =========================================================================== #
# The reviewed equivalence fixture is REQUIRED (Step 1 red state without it)   #
# =========================================================================== #
@pytest.fixture(scope="module")
def FX() -> dict:
    if not _EQUIV_FIXTURE.exists():
        pytest.fail(
            "the reviewed equivalence fixture "
            "tests/orchestration/fixtures/stock_deep_dive_equivalence_v1.json is "
            "required for the Task 10 acceptance matrix (brief Step 2); it must be "
            "built and reviewed, not skipped"
        )
    return json.loads(_EQUIV_FIXTURE.read_text(encoding="utf-8"))


def _core_payload_models(fx: dict) -> dict[str, object]:
    """Build the reviewed deterministic typed payloads from fixture values."""
    ns = fx["deterministic_payloads"]["news-sentiment"]
    rw = fx["deterministic_payloads"]["report-writer"]
    return {
        "news-sentiment": SentimentReport(
            overall_band=SentimentBand(ns["overall_band"]),
            overall_score=float(ns["overall_score"]),
            confidence=Confidence(ns["confidence"]),
            narrative=ns["narrative"],
        ),
        "report-writer": ResearchPlan(
            recommendation=PortfolioRating(rw["recommendation"]),
            rationale=rw["rationale"],
            strategic_actions=tuple(rw["strategic_actions"]),
        ),
    }


# =========================================================================== #
# Legacy side: deterministic SubAgent doubles over the REAL engine graph       #
# =========================================================================== #
class _LegacyDoc(BaseModel):
    """Single-field double output → legacy `_build_inputs` unwraps to a scalar."""

    text: str


def _make_double(name, schema, factory, recorder, fail_nodes, memory_root):
    class _Double(SubAgent):
        NAME = name
        OUTPUT_SCHEMA = schema

        async def _execute(self, inputs):
            recorder[name] = dict(inputs)
            if name in fail_nodes:
                raise RuntimeError(f"scripted legacy failure at {name}")
            return factory(inputs)

    return _Double(memory_root)


class LegacyRun:
    def __init__(self, done, received_inputs, waves):
        self.done = done
        self.received_inputs = received_inputs
        self.waves = waves

    def wave_index(self, name: str) -> int:
        for i, wave in enumerate(self.waves):
            if name in wave:
                return i
        raise AssertionError(f"{name} never appeared in a wave")


def run_legacy_graph(fx, tmp_path, *, fail_nodes=(), base_overrides=None) -> LegacyRun:
    """Drive the REAL engine Orchestrator over the full authoritative 18-node graph.

    Core doubles emit the reviewed registered output schemas from fixture values
    (the fixture worker table binds the planned workers' outputs to
    SentimentReport / the ResearchPlan 5-band migration target) and require the
    reviewed base inputs (missing_behavior='error' per the core mapping rows).
    Non-core doubles emit a single-field doc (their outputs carry no reviewed
    schema — graph-shape scope only).
    """
    cfg = P._full_normalized_config()  # asserts repo == engine normalization first
    payloads = _core_payload_models(fx)
    recorder: dict[str, dict] = {}
    fail_nodes = set(fail_nodes)
    memory_root = tmp_path / "legacy-memory"

    def _require_base(inputs):
        for key in ("code", "asof_date"):
            if key not in inputs:  # reviewed base row: missing_behavior='error'
                raise KeyError(f"reviewed base input {key!r} is missing")

    def _sentiment_factory(inputs):
        _require_base(inputs)
        return payloads["news-sentiment"].model_dump()

    def _report_factory(inputs):
        _require_base(inputs)
        if "out_dir" not in inputs:  # service-owned locator must have been injected
            raise KeyError("reviewed base input 'out_dir' is missing")
        return payloads["report-writer"].model_dump()

    def _doc_factory_for(name):
        return lambda inputs: {"text": f"{name}-output"}

    nodes = []
    for name, spec in cfg["nodes"].items():
        if name == "news-sentiment":
            agent = _make_double(name, SentimentReport, _sentiment_factory,
                                 recorder, fail_nodes, memory_root)
        elif name == "report-writer":
            agent = _make_double(name, ResearchPlan, _report_factory,
                                 recorder, fail_nodes, memory_root)
        else:
            agent = _make_double(name, _LegacyDoc, _doc_factory_for(name),
                                 recorder, fail_nodes, memory_root)
        nodes.append(DAGNode(agent=agent, deps=list(spec["deps"]),
                             input_keys=list(spec["input_keys"]),
                             soft_deps=list(spec["soft_deps"])))

    waves: list[list[str]] = []

    def on_event(evt, data):
        if evt == "wave_start":
            waves.append(list(data["agents"]))

    base = {"code": fx["base_inputs"]["code"], "asof_date": fx["base_inputs"]["asof_date"],
            "out_dir": str(tmp_path / "service-owned-out")}
    if base_overrides is not None:
        base = dict(base_overrides)

    orch = Orchestrator(nodes, on_event=on_event)
    done = asyncio.run(orch.run(base))
    return LegacyRun(done=done, received_inputs=recorder, waves=waves)


# =========================================================================== #
# New side: the attested compat plan through the admitted Phase-2 runtime      #
# =========================================================================== #
class FakeCompatGateway:
    """Trusted single-shot gateway: exact prompt binding + fixed fixture payloads."""

    def __init__(self, stores, payload_by_worker, fail_nodes):
        self._stores = stores
        self._payloads = payload_by_worker
        self._fail = set(fail_nodes)

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._stores.payloads)
        if record.node_id in self._fail:
            raise RuntimeError(f"scripted failure at {record.node_id}")
        payload = self._payloads[record.worker_id]
        return W.ModelResult(
            payload=payload, rendered_text=f"rendered:{record.worker_id}",
            number_anchors=(), input_tokens=13, output_tokens=9, provider="fake",
            model="fake-compat", provider_response_id=f"resp-{record.node_id}")


class NewEnv:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def run(self, *, runtime_limit=8, recorder=None, layer_hook=None,
            runtime=None, model_gateway="__default__"):
        rec = recorder if recorder is not None else D.RunRecorder()
        mg = self.model_gateway if model_gateway == "__default__" else model_gateway
        result = asyncio.run(D.run_plan(
            self.plan.plan_digest, self.run_ctx, admission=self.service, pool=self.pool,
            budget=self.budget, runtime=runtime or self.runtime, registry=self.registry,
            stores=self.stores, runtime_limit=runtime_limit, clock=self.clock,
            refusal_sink=self.refusal_sink, model_gateway=mg, recorder=rec,
            layer_hook=layer_hook))
        return result, rec


def build_new_env(fx, *, fail_nodes=(), request_id="req-equiv", with_attestation=True,
                  admit=True, drop_context=False) -> NewEnv:
    clock = FixedClock()
    registry = default_registry()
    catalog = P.load_compat_catalog()
    view = BridgeCatalogView.build(catalog, {})
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_digest = resolver.register(phase2_runtime_registry(registry.registry_digest))
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()),
        stores=stores, registry_digest=rt_digest, built_at=clock.now())
    context = mem.context
    mapping = P.build_stock_deep_dive_compat_mapping()
    request = P.preset_orchestration_request(request_id=request_id)
    draft = P.legacy_draft_from_mapping(
        mapping, request=request, context=context, catalog=catalog,
        schema_registry=registry,
        budget_request={"tokens": 8_000_000, "llm_invocations": 64, "max_concurrency": 16},
        sink_mapping=("report-writer",))
    attestations: dict = {}
    if with_attestation:
        P.attest_and_store_legacy_plan(
            mapping, draft, request, context=context, catalog=catalog.snapshot,
            schema_registry=registry, attestations=attestations)
    run_budget = RunBudget(ledger_id="led-equiv", max_tokens=16_000_000,
                           max_llm_invocations=128, max_concurrency=32)
    approvals: dict = {}
    contexts = {} if drop_context else {context.content_digest: context}
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request},
        drafts={draft.id: draft}, contexts=contexts, attestations=attestations,
        approvals=approvals, catalog=catalog, bridge_view=view, phase1_registry=registry,
        runtime_registry_digest=rt_digest, profile=static_runtime_profile(),
        stores=stores, run_budget=run_budget, clock=clock)

    payloads = _core_payload_models(fx)
    payload_by_worker = {
        "compat.news_sentiment": payloads["news-sentiment"],
        "compat.report_writer": payloads["report-writer"],
    }
    model_gateway = FakeCompatGateway(stores, payload_by_worker, fail_nodes)
    audit_reg = default_audit_detail_registry()
    audit_reg.seal()
    refusal_sink = EventRefusalAuditSink(detail_registry=audit_reg, clock=clock)

    env = NewEnv(
        clock=clock, registry=registry, catalog=catalog, view=view, rt_digest=rt_digest,
        stores=stores, mem=mem, context=context, mapping=mapping, request=request,
        draft=draft, attestations=attestations, approvals=approvals, service=service,
        run_budget=run_budget, model_gateway=model_gateway, refusal_sink=refusal_sink,
        plan=None, admitted=None, bundle=None, pool=None, budget=None, runtime=None,
        run_ctx=None, prep=None, cand=None, res=None)
    if not admit:
        return env

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
    pool = ArtifactPool(stores=stores, registry_digest=rt_digest, plan=plan,
                        catalog=catalog.snapshot, clock=clock)
    budget = BudgetLedger(
        sink=stores.budget_event_sink(run_id=draft.run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)
    factories = TrustedFactoryRegistry(catalog)
    runtime = W.ExecutionRuntime(
        catalog=catalog, bridge_view=view, factories=factories,
        support_report=bundle.support_report, runtime_registry_digest=rt_digest)
    run_ctx = RunContext(
        run_id=draft.run_id, data=context.data_context,
        context_snapshot_id=context.snapshot_id,
        memory_snapshot_hash=context.memory_snapshot_hash, budget=run_budget,
        cancellation_token_id="cancel-equiv")
    env.plan, env.admitted, env.bundle = plan, admitted, bundle
    env.pool, env.budget, env.runtime, env.run_ctx = pool, budget, runtime, run_ctx
    env.prep, env.cand, env.res = prep, cand, res
    env.factories = factories
    return env


def _status(rec, node_id) -> NodeStatus:
    runs = [nr for nr in rec.node_runs if nr.node_id == node_id]
    assert runs, f"no NodeRun recorded for {node_id}"
    return runs[-1].status


def _budget_events(env):
    return [ev for ev in env.stores.events.journal(env.draft.run_id, "main")
            if ev.event_type in (EventType.BUDGET_RESERVED, EventType.BUDGET_SETTLED,
                                 EventType.BUDGET_RELEASED)]


def _node_reservations(env):
    st = env.budget.replay()
    return [r for r in st.reservations.values() if r.scope_type == "node"]


def _scenario(fx, scenario_id):
    return next(s for s in fx["scenarios"] if s["id"] == scenario_id)


# =========================================================================== #
# 0. hermetic identity: engine import, YAML copies, referenced digests         #
# =========================================================================== #
def test_engine_imports_from_the_repo_fork_without_manual_pythonpath():
    # tests/conftest.py pins the in-repo engine fork; the acceptance harness must
    # not depend on a manual PYTHONPATH (brief hermetic rule).
    engine_dir = Path(__file__).resolve().parents[2] / "engine"
    assert Path(financial_analyst.__file__).resolve().is_relative_to(engine_dir)


def test_yaml_copies_normalize_identically_and_match_the_attested_digest(FX):
    # _full_normalized_config() hard-fails unless repo == engine normalized bytes.
    cfg = P._full_normalized_config()
    assert content_digest(cfg) == FX["references"]["source_config_digest"]
    legacy_fx = json.loads(_LEGACY_FIXTURE.read_text(encoding="utf-8"))
    assert legacy_fx["graphs"][0]["config_digest"] == FX["references"]["source_config_digest"]
    assert set(cfg["nodes"]) == set(legacy_fx["graphs"][0]["normalized_config"]["nodes"])


def test_fixture_references_the_phase1_identities_and_documents_exclusions(FX):
    refs = FX["references"]
    mapping = P.build_stock_deep_dive_compat_mapping()
    assert refs["source_config_digest"] == mapping.source_config_digest
    assert refs["legacy_mapping_semantic_digest"] == mapping.semantic_digest()
    assert refs["phase2_static_catalog_digest"] == P.PHASE2_STATIC_CATALOG_DIGEST
    assert refs["phase1_schema_registry_digest"] == default_registry().registry_digest
    # core + exclusions partition the full authoritative node set, and every
    # exclusion carries a reason and the Phase-3 pointer (the user ruling).
    cfg = P._full_normalized_config()
    core = set(FX["core"]["nodes"])
    excluded = set(FX["excluded_nodes"])
    assert core == set(P._CORE_NODES)
    assert core | excluded == set(cfg["nodes"])
    assert not (core & excluded)
    for node, entry in FX["excluded_nodes"].items():
        assert entry["reason"], node
        assert "phase 3" in entry["arrives_with"].lower(), node


# =========================================================================== #
# 1. node set, dependency partial order, executed set (full-graph shape)       #
# =========================================================================== #
def test_full_graph_node_set_partial_order_and_executed_set(FX, tmp_path):
    legacy = run_legacy_graph(FX, tmp_path)
    cfg = P._full_normalized_config()
    # complete node set: all 18 authoritative nodes ran to a terminal result.
    assert set(legacy.done) == set(cfg["nodes"])
    assert all(r.ok for r in legacy.done.values())
    assert set(legacy.received_inputs) == set(cfg["nodes"])  # executed set == all
    # dependency partial order: every node's wave strictly follows every dep's.
    for name, spec in cfg["nodes"].items():
        for dep in spec["deps"]:
            assert legacy.wave_index(name) > legacy.wave_index(dep), (name, dep)
    # the core order embeds in the full graph: news-sentiment before report-writer.
    assert legacy.wave_index("news-sentiment") < legacy.wave_index("report-writer")
    # new side: the admitted core plan layers to the same partial order.
    env = build_new_env(FX)
    layers = D._kahn_depth_layers(env.plan.nodes)
    assert [[n.id for n in layer] for layer in layers] == [["news-sentiment"], ["report-writer"]]


# =========================================================================== #
# 2/5/6/7/9 + 3-satisfied. all-success equivalence (core value scope)          #
# =========================================================================== #
def test_all_success_core_terminal_outcomes_match_the_reviewed_mapping(FX, tmp_path):
    scenario = _scenario(fx=FX, scenario_id="all-success")
    legacy = run_legacy_graph(FX, tmp_path)
    env = build_new_env(FX)
    result, rec = env.run()
    for node, ok in scenario["expected"]["legacy_ok"].items():
        assert legacy.done[node].ok is ok, node
    for node, token in scenario["expected"]["new"].items():
        assert _status(rec, node) is _STATUS_BY_TOKEN[token], node
    assert result.terminal_status == scenario["expected"]["terminal"]
    # per-node truth comes from NodeRuns (RunResult collapses degraded/partial).
    assert result.settled_llm_invocations == 2


def test_all_success_payload_byte_equality_across_old_new_and_replay(FX, tmp_path):
    # assertion 6 at the ruled scope: both core planned workers are LLM per the
    # Task-0 evidence, so byte-comparison applies to the deterministic HARNESS
    # artifacts — the typed payloads the fake gateways produce from fixed fixture
    # outputs. LLM prose is not byte-compared; no legacy-deterministic core node
    # exists. (See the module docstring.)
    legacy = run_legacy_graph(FX, tmp_path)
    env = build_new_env(FX)
    env.run()
    for node in FX["core"]["nodes"]:
        legacy_payload = legacy.done[node].output
        artifact = env.pool.committed_output(node, "primary")
        assert artifact is not None, node
        # canonical normalized typed payload equality old vs new (content digest).
        assert content_digest(legacy_payload) == content_digest(artifact.payload), node
        assert canonical_json(legacy_payload) == canonical_json(artifact.payload), node
    # cross-run byte-stability: a second fresh env commits byte-identical payloads
    # and identical LayerCommit orderings; a replay run returns an equal RunResult.
    env2 = build_new_env(FX)
    r2a, rec2a = env2.run()
    r2b, rec2b = env2.run()
    assert r2a == r2b  # replay equality incl. settled totals
    for node in FX["core"]["nodes"]:
        a1 = env.pool.committed_output(node, "primary")
        a2 = env2.pool.committed_output(node, "primary")
        assert a1.content_digest == a2.content_digest, node
    lc1 = [(c.layer_index, tuple((a.artifact_seq, a.artifact_id) for a in c.artifacts))
           for c in rec2a.layer_commits]
    lc2 = [(c.layer_index, tuple((a.artifact_seq, a.artifact_id) for a in c.artifacts))
           for c in rec2b.layer_commits]
    assert lc1 == lc2
    # completion-order permutation is degenerate at core scope (two-layer chain,
    # one node per layer) — documented; the general intra-layer property is locked
    # by test_dag.py::test_stable_artifact_sequence_independent_of_completion_order.


def test_all_success_artifact_slot_output_key_and_schema_per_core_node(FX):
    env = build_new_env(FX)
    env.run()
    for node in FX["core"]["nodes"]:
        expected = FX["core"]["outputs"][node]
        artifact = env.pool.committed_output(node, expected["output_key"])
        assert artifact is not None, node
        assert artifact.slot == expected["slot"]
        assert artifact.output_key == expected["output_key"]
        name, version = expected["schema"].split("@")
        assert artifact.payload_schema_ref == SchemaRef(name=name, version=version)
        # validates against the registered schema (never a generic Doc).
        env.registry.validate_payload(artifact.payload_schema_ref, artifact.payload)


def test_all_success_sink_outcomes_and_layer_commit_boundaries(FX, tmp_path):
    legacy = run_legacy_graph(FX, tmp_path)
    env = build_new_env(FX)
    result, rec = env.run()
    # legacy sink (report-writer) succeeded; new sink COMPLETED and committed in
    # the LAST public LayerCommit with its real NodeRun id.
    assert legacy.done["report-writer"].ok
    assert result.terminal_status == "completed"
    assert len(rec.layer_commits) == 2
    assert [c.layer_index for c in rec.layer_commits] == [0, 1]
    sink_run = [nr for nr in rec.node_runs if nr.node_id == "report-writer"][-1]
    assert sink_run.node_run_id in rec.layer_commits[-1].node_run_ids


def test_all_success_input_mapping_rows_satisfied(FX, tmp_path):
    # assertion 3, satisfied branch — every reviewed core LegacyInputMapping row:
    legacy = run_legacy_graph(FX, tmp_path)
    env = build_new_env(FX)
    result, rec = env.run()
    payloads = _core_payload_models(FX)
    # base code/asof_date -> context: the legacy doubles actually received them,
    # the new plan nodes carry NO params (context-supplied, never a plan param).
    for node in FX["core"]["nodes"]:
        for key in ("code", "asof_date"):
            assert legacy.received_inputs[node][key] == FX["base_inputs"][key]
    for node in env.plan.nodes:
        assert node.params == {}
    # the frozen context ref binds the exact admitted ContextSnapshot in every
    # executable InputSnapshot (the new-side carrier of context-targeted bases).
    for node in FX["core"]["nodes"]:
        snap = rec.input_snapshots[node]
        assert snap.context_snapshot_ref.payload_ref.content_digest == env.context.content_digest
    # out_dir -> service_binding: legacy received the service-owned locator; the
    # frozen new plan carries NO path anywhere (service binding, never a plan path).
    assert "out_dir" in legacy.received_inputs["report-writer"]
    assert "service-owned-out" not in canonical_json(env.plan)
    # upstream -> input_binding (SOFT edge, satisfied): legacy injected the FULL
    # multi-field model_dump of the sentiment output (the reviewed injected payload
    # shape); the new sink bound the upstream artifact under 'sentiment' with the
    # full upstream output SchemaRef (projection='raw' — none applied).
    expected_dump = payloads["news-sentiment"].model_dump()
    assert legacy.received_inputs["report-writer"]["news-sentiment"] == expected_dump
    snap = rec.input_snapshots["report-writer"]
    binding = [b for b in snap.artifact_inputs if b.input_name == "sentiment"][0]
    assert [r.producer_node_id for r in binding.artifact_refs] == ["news-sentiment"]
    assert binding.artifact_refs[0].schema_ref == _SR_SENTIMENT
    dep = [d for d in env.plan.nodes[1].dependencies
           if d.upstream_node_id == "news-sentiment"][0]
    assert dep.upstream_output_key == "primary"
    # single-field unwrap is OBSERVED on the real legacy graph (shape observation
    # via a non-core double; no core mapping row claims this projection — the
    # recorded-not-applied projection row is locked synthetic in test_presets).
    assert legacy.received_inputs["report-writer"]["morning-brief-writer"] == \
        "morning-brief-writer-output"


def test_all_success_provenance_binds_the_attested_identity(FX):
    # assertion 9: Plan/Artifact/NodeRun provenance binds the same attested
    # config/mapping/catalog and input/evidence identity.
    env = build_new_env(FX)
    result, rec = env.run()
    refs = FX["references"]
    # the frozen Plan binds the attested legacy tuple + catalog/registry digests.
    assert env.plan.draft.legacy_source_config_digest == refs["source_config_digest"]
    assert env.plan.draft.legacy_mapping_digest == refs["legacy_mapping_semantic_digest"]
    assert env.plan.draft.catalog_digest == refs["phase2_static_catalog_digest"]
    assert env.plan.draft.schema_registry_digest == refs["phase1_schema_registry_digest"]
    # the admitted witness binds the exact service-owned attestation + catalog.
    attestation = env.attestations[env.plan.plan_digest]
    assert env.admitted.legacy_attestation_digest == attestation.semantic_digest()
    assert env.admitted.catalog_digest == refs["phase2_static_catalog_digest"]
    assert attestation.source_config_digest == refs["source_config_digest"]
    assert attestation.legacy_mapping_digest == refs["legacy_mapping_semantic_digest"]
    for node in FX["core"]["nodes"]:
        artifact = env.pool.committed_output(node, "primary")
        node_run = [nr for nr in rec.node_runs if nr.node_id == node][-1]
        # artifact provenance pins the admitted plan digest + the frozen read-set.
        assert artifact.provenance.plan_digest == env.plan.plan_digest
        assert artifact.provenance.input_snapshot_digest == \
            rec.input_snapshots[node].content_digest
        assert node_run.input_snapshot_digest == rec.input_snapshots[node].content_digest
        # LLM execution evidence (the persisted prompt-assembly record) survives on
        # BOTH the NodeRun and the sealed Artifact provenance, and they are equal.
        assert node_run.execution_evidence_refs != ()
        assert artifact.provenance.execution_evidence_refs == node_run.execution_evidence_refs
        assert artifact.provenance.tool_call_records == node_run.tool_call_records == ()


# =========================================================================== #
# 2/3/4-missing. THE load-bearing scenario: soft input absent, downstream runs #
# =========================================================================== #
def test_soft_input_absent_downstream_still_runs_degraded(FX, tmp_path):
    scenario = _scenario(fx=FX, scenario_id="soft-upstream-fails")
    fail = tuple(scenario["fail"])
    legacy = run_legacy_graph(FX, tmp_path, fail_nodes=fail)
    # legacy: the soft dep failed, report-writer still RAN and produced its output
    # with the 'news-sentiment' key OMITTED from its inputs (ok-gated injection).
    assert legacy.done["news-sentiment"].ok is False
    assert legacy.done["report-writer"].ok is True
    assert legacy.done["report-writer"].output is not None
    assert "news-sentiment" not in legacy.received_inputs["report-writer"]
    for omitted in scenario["expected"]["legacy_omitted_inputs"]["report-writer"]:
        assert omitted not in legacy.received_inputs["report-writer"]
    # new: news-sentiment FAILED; report-writer executed DEGRADED (the reviewed
    # canonical mapping of legacy ok-with-omitted-soft-input) with an artifact.
    env = build_new_env(FX, fail_nodes=fail)
    result, rec = env.run()
    for node, token in scenario["expected"]["new"].items():
        assert _status(rec, node) is _STATUS_BY_TOKEN[token], node
    assert result.terminal_status == scenario["expected"]["terminal"]
    sink_run = [nr for nr in rec.node_runs if nr.node_id == "report-writer"][-1]
    assert sink_run.status is NodeStatus.DEGRADED
    assert sink_run.reason_code == "degraded_input_or_result"
    # the DEGRADE row's missing behavior: the optional input is OMITTED from the
    # ready snapshot (mirror of the legacy omission), and the payload still equals
    # the legacy output byte-for-byte.
    snap = rec.input_snapshots["report-writer"]
    assert snap.readiness == "ready"
    assert all(b.input_name != "sentiment" for b in snap.artifact_inputs)
    artifact = env.pool.committed_output("report-writer", "primary")
    assert artifact is not None
    assert content_digest(legacy.done["report-writer"].output) == \
        content_digest(artifact.payload)
    # the failed upstream committed nothing on either side.
    assert legacy.done["news-sentiment"].output is None
    assert env.pool.committed_output("news-sentiment", "primary") is None


def test_core_sink_failure_maps_to_failed_and_blocks_legacy_downstream(FX, tmp_path):
    scenario = _scenario(fx=FX, scenario_id="sink-fails")
    fail = tuple(scenario["fail"])
    legacy = run_legacy_graph(FX, tmp_path, fail_nodes=fail)
    assert legacy.done["news-sentiment"].ok is True
    assert legacy.done["report-writer"].ok is False
    # legacy-authoritative hard consequence on the REAL graph: the introspector
    # hard-depends on report-writer and blocks forever.
    for blocked in scenario["expected"]["legacy_blocked_downstream"]:
        assert legacy.done[blocked].ok is False
        assert legacy.done[blocked].error == "upstream dependency failed"
        assert blocked not in legacy.received_inputs  # never executed
    # new: the sink FAILED -> terminal 'failed'; upstream unaffected.
    env = build_new_env(FX, fail_nodes=fail)
    result, rec = env.run()
    for node, token in scenario["expected"]["new"].items():
        assert _status(rec, node) is _STATUS_BY_TOKEN[token], node
    assert result.terminal_status == scenario["expected"]["terminal"]
    assert env.pool.committed_output("report-writer", "primary") is None
    # a FAILED node keeps its committed evidence identity but no output artifact.
    sink_run = [nr for nr in rec.node_runs if nr.node_id == "report-writer"][-1]
    assert sink_run.output_artifact_ids == ()


def test_legacy_hard_block_cascades_on_the_real_graph(FX, tmp_path):
    # legacy-authoritative BLOCK semantics on the authoritative 18-node graph
    # (graph-shape scope: these nodes are documented exclusions, so no new-side
    # value counterpart is claimed): a failed hard dep blocks the whole cascade.
    legacy = run_legacy_graph(FX, tmp_path, fail_nodes=("quote-fetcher",))
    cascade = ("fundamental-analyst", "technical-analyst", "whale-analyst",
               "bull-advocate", "bear-advocate", "risk-officer", "report-writer",
               "introspector")
    for node in cascade:
        assert legacy.done[node].ok is False, node
        assert legacy.done[node].error == "upstream dependency failed", node
        assert node not in legacy.received_inputs, node
    # roots unaffected by the failed hard dep still completed (incl. the core root).
    assert legacy.done["news-sentiment"].ok is True


# =========================================================================== #
# 4-BLOCK. hard/BLOCK missing-output equivalence — EXPLICITLY SYNTHETIC        #
# =========================================================================== #
def test_synthetic_block_edge_blocks_downstream_on_both_sides(FX, tmp_path):
    """SYNTHETIC (labeled): the reviewed core has no BLOCK edge, so the hard/BLOCK
    runtime consequence is demonstrated on the explicitly synthetic 3-node
    catalog+mapping from test_presets (up -> mid -> sink, both edges hard). No
    stock-deep-dive semantics are claimed by this test."""
    # -- legacy synthetic analog: same shape through the REAL engine ---------- #
    recorder: dict[str, dict] = {}
    memory_root = tmp_path / "syn-memory"
    agents = {
        name: _make_double(name, _LegacyDoc, lambda inputs, n=name: {"text": f"{n}-out"},
                           recorder, {"up"}, memory_root)
        for name in ("up", "mid", "sink")
    }
    orch = Orchestrator([
        DAGNode(agent=agents["up"]),
        DAGNode(agent=agents["mid"], deps=["up"], input_keys=["up"]),
        DAGNode(agent=agents["sink"], deps=["mid"], input_keys=["mid"]),
    ])
    done = asyncio.run(orch.run({}))
    assert done["up"].ok is False
    assert done["mid"].error == "upstream dependency failed"
    assert done["sink"].error == "upstream dependency failed"
    assert "mid" not in recorder and "sink" not in recorder  # never executed

    # -- new synthetic analog through the full admitted runtime --------------- #
    catalog, registry = _synthetic_graph_catalog()
    syn_mapping = _synthetic_graph_mapping()
    clock = FixedClock()
    view = BridgeCatalogView.build(catalog, {})
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    rt_digest = resolver.register(phase2_runtime_registry(registry.registry_digest))
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=clock.now()),
        stores=stores, registry_digest=rt_digest, built_at=clock.now())
    request = P.preset_orchestration_request(request_id="req-syn-block")
    draft = P.legacy_draft_from_mapping(
        syn_mapping, request=request, context=mem.context, catalog=catalog,
        schema_registry=registry,
        budget_request={"tokens": 100_000, "llm_invocations": 8}, sink_mapping=("sink",))
    attestations: dict = {}
    P.attest_and_store_legacy_plan(
        syn_mapping, draft, request, context=mem.context, catalog=catalog.snapshot,
        schema_registry=registry, attestations=attestations)
    run_budget = RunBudget(ledger_id="led-syn", max_tokens=200_000,
                           max_llm_invocations=16, max_concurrency=8)
    approvals: dict = {}
    service = PlanAdmissionService(
        run_id=draft.run_id, requests={request.request_id: request},
        drafts={draft.id: draft}, contexts={mem.context.content_digest: mem.context},
        attestations=attestations, approvals=approvals, catalog=catalog,
        bridge_view=view, phase1_registry=registry, runtime_registry_digest=rt_digest,
        profile=static_runtime_profile(), stores=stores, run_budget=run_budget,
        clock=clock)
    prep = service.prepare_candidate(draft.id, request_id=request.request_id)
    assert prep.phase1_report.valid, [i.code for i in prep.phase1_report.issues]
    cand, res = service.persist_and_reserve_candidate(prep, idempotency_key="r1")
    approvals[(request.request_id, cand.candidate_plan_digest)] = PlanApproval(
        request_id=request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor_id="human-1", decided_at=clock.now())
    ev = service.record_approval(
        cand.candidate_plan_digest,
        ApprovalSubmission(request_id=request.request_id,
                           candidate_plan_digest=cand.candidate_plan_digest,
                           decision=ApprovalDecision.APPROVED),
        authenticated_actor="human-1", idempotency_key="a1")
    plan, _ = service.freeze_and_admit_candidate(
        cand.candidate_plan_digest, reservation_id=res.reservation_id,
        approval_event_id=ev.event_id, idempotency_key="f1")
    bundle = service.verify_for_dispatch(plan.plan_digest)
    pool = ArtifactPool(stores=stores, registry_digest=rt_digest, plan=plan,
                        catalog=catalog.snapshot, clock=clock)
    budget = BudgetLedger(
        sink=stores.budget_event_sink(run_id=draft.run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)
    runtime = W.ExecutionRuntime(
        catalog=catalog, bridge_view=view, factories=TrustedFactoryRegistry(catalog),
        support_report=bundle.support_report, runtime_registry_digest=rt_digest)
    run_ctx = RunContext(run_id=draft.run_id, data=mem.context.data_context,
                         context_snapshot_id=mem.context.snapshot_id,
                         memory_snapshot_hash=mem.context.memory_snapshot_hash,
                         budget=run_budget, cancellation_token_id="cancel-syn")

    class _FailUpGateway:
        def invoke(self, request, *, prompt_assembly_ref):
            record = W.verify_model_request_binding(request, prompt_assembly_ref,
                                                    reader=stores.payloads)
            if record.node_id == "up":
                raise RuntimeError("scripted failure at up")
            raise AssertionError("a BLOCKED node must never invoke the model")

    audit_reg = default_audit_detail_registry()
    audit_reg.seal()
    rec = D.RunRecorder()
    result = asyncio.run(D.run_plan(
        plan.plan_digest, run_ctx, admission=service, pool=pool, budget=budget,
        runtime=runtime, registry=registry, stores=stores, runtime_limit=8,
        clock=clock, refusal_sink=EventRefusalAuditSink(detail_registry=audit_reg, clock=clock),
        model_gateway=_FailUpGateway(), recorder=rec))
    # BLOCK: legacy 'upstream dependency failed' <-> new BLOCKED terminal_partial.
    assert _status(rec, "up") is NodeStatus.FAILED
    assert _status(rec, "mid") is NodeStatus.BLOCKED
    assert _status(rec, "sink") is NodeStatus.BLOCKED
    assert result.terminal_status == "failed"
    for node_id in ("mid", "sink"):
        snap = rec.input_snapshots[node_id]
        assert snap.readiness == "terminal_partial"
        run = [nr for nr in rec.node_runs if nr.node_id == node_id][-1]
        assert run.reason_code == "blocking_dependency_unsatisfied"
        assert run.output_artifact_ids == ()
    # blocked nodes reserved no child budget (parity with the legacy never-ran).
    st = budget.replay()
    blocked_res = [r for r in st.reservations.values()
                   if r.scope_type == "node" and r.scope_id in ("mid", "sink")]
    assert blocked_res == []


# =========================================================================== #
# base input missing per the reviewed policy                                   #
# =========================================================================== #
def test_base_input_missing_fails_per_the_reviewed_policy(FX, tmp_path):
    # reviewed base rows carry missing_behavior='error'. Legacy errors inside the
    # agent at run time (ok=False); the new side is STRICTER (documented in the
    # fixture): the context that carries the base values is admission authority,
    # so a missing context fails BEFORE execution and consumes no budget.
    legacy = run_legacy_graph(FX, tmp_path, base_overrides={})  # no code/asof/out_dir
    assert legacy.done["news-sentiment"].ok is False
    assert legacy.done["report-writer"].ok is False
    env = build_new_env(FX, admit=False, drop_context=True)
    with pytest.raises(AdmissionRejected) as ei:
        env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    assert ei.value.code == "context_missing"
    assert _budget_events(env) == []  # pre-admission failure consumed no budget


# =========================================================================== #
# 8 + crash/replay: barrier visibility and idempotent resume (core scope)      #
# =========================================================================== #
def test_crash_after_layer_commit_resumes_to_the_clean_result(FX):
    clean = build_new_env(FX)
    result_clean, _ = clean.run()

    crashed = build_new_env(FX)

    class _Crash(Exception):
        pass

    def layer_hook(layer_index, *, committed):
        if layer_index == 0:
            raise _Crash("crash after layer 0 commit")

    with pytest.raises(_Crash):
        crashed.run(layer_hook=layer_hook)
    # the barrier held: layer 0's output is durably visible, the sink's is not.
    assert crashed.pool.committed_output("news-sentiment", "primary") is not None
    assert crashed.pool.committed_output("report-writer", "primary") is None
    result_resumed, rec_resumed = crashed.run()
    assert result_resumed == result_clean  # incl. settled totals
    assert _status(rec_resumed, "news-sentiment") is NodeStatus.COMPLETED
    assert _status(rec_resumed, "report-writer") is NodeStatus.COMPLETED
    # idempotent resume: exactly one child reservation per core node.
    for node in FX["core"]["nodes"]:
        assert len([r for r in _node_reservations(crashed) if r.scope_id == node]) == 1


def test_crash_before_commit_leaves_no_visible_artifact_then_replays(FX):
    # assertion 8: a staged artifact is NEVER visible before its LayerCommitted.
    env = build_new_env(FX)

    class _Crash(Exception):
        pass

    calls = {"n": 0}
    real_commit = env.pool.commit_layer

    def crashing_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Crash("crash after stage, before commit")
        return real_commit(*args, **kwargs)

    env.pool.commit_layer = crashing_commit  # type: ignore[method-assign]
    with pytest.raises(_Crash):
        env.run()
    assert env.pool.committed_output("news-sentiment", "primary") is None
    assert env.pool.committed_output("report-writer", "primary") is None
    env.pool.commit_layer = real_commit  # type: ignore[method-assign]
    result, _ = env.run()
    assert result.terminal_status == "completed"
    assert env.pool.committed_output("report-writer", "primary") is not None


# =========================================================================== #
# Negative controls — every one fails BEFORE execution; pre-admission ones     #
# consume no budget.                                                           #
# =========================================================================== #
def _assert_no_budget(env):
    assert _budget_events(env) == []


def test_nc_missing_attestation_rejected_at_the_gate_no_budget(FX):
    env = build_new_env(FX, with_attestation=False, admit=False)
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    assert not prep.phase1_report.valid
    assert any(i.code == "compat_attestation_required" for i in prep.phase1_report.issues)
    with pytest.raises(AdmissionRejected) as ei:
        env.service.persist_and_reserve_candidate(prep, idempotency_key="nc-1")
    assert ei.value.code == "unsupported"
    _assert_no_budget(env)


def test_nc_mismatched_request_attestation_rejected_no_budget(FX):
    # an attestation minted for a DIFFERENT request/candidate is not authority.
    env = build_new_env(FX, with_attestation=False, admit=False)
    other = build_new_env(FX, with_attestation=True, admit=False,
                          request_id="req-other")
    wrong = other.attestations[next(iter(other.attestations))]
    probe = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    env.attestations[probe.candidate_plan_digest] = wrong
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    assert not prep.phase1_report.valid
    assert any(i.code in ("compat_attestation_mismatch", "compat_attestation_required")
               for i in prep.phase1_report.issues)
    with pytest.raises(AdmissionRejected):
        env.service.persist_and_reserve_candidate(prep, idempotency_key="nc-2")
    _assert_no_budget(env)


def test_nc_unknown_request_rejected_no_budget(FX):
    env = build_new_env(FX, admit=False)
    with pytest.raises(AdmissionRejected) as ei:
        env.service.prepare_candidate(env.draft.id, request_id="req-nobody")
    assert ei.value.code == "unknown_request"
    _assert_no_budget(env)


@pytest.mark.parametrize("field,code", [
    ("catalog_digest", "catalog_drift"),
    ("schema_registry_digest", "registry_drift"),
])
def test_nc_catalog_or_registry_digest_drift_rejected_no_budget(FX, field, code):
    env = build_new_env(FX, admit=False)
    tampered = env.draft.model_copy(update={field: "0" * 64})
    env.service._drafts = {env.draft.id: tampered}
    with pytest.raises(AdmissionRejected) as ei:
        env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    assert ei.value.code == code
    _assert_no_budget(env)


@pytest.mark.parametrize("field", ["legacy_source_config_digest", "legacy_mapping_digest"])
def test_nc_source_config_or_mapping_digest_tamper_rejected_no_budget(FX, field):
    # the attested tuple is bound into the candidate digest AND cross-checked
    # against the attestation: tampering either digest de-authorizes the plan.
    env = build_new_env(FX, admit=False)
    tampered = env.draft.model_copy(update={field: "0" * 64})
    env.service._drafts = {env.draft.id: tampered}
    probe = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    # re-key the genuine attestation under the tampered candidate: still refused.
    genuine = env.attestations[next(iter(env.attestations))]
    env.attestations[probe.candidate_plan_digest] = genuine
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    assert not prep.phase1_report.valid
    assert any(i.code in ("compat_attestation_mismatch", "compat_attestation_required")
               for i in prep.phase1_report.issues)
    with pytest.raises(AdmissionRejected):
        env.service.persist_and_reserve_candidate(prep, idempotency_key="nc-3")
    _assert_no_budget(env)


def test_nc_one_yaml_copy_drifted_freezes_nothing(FX, tmp_path, monkeypatch):
    # a drifted engine copy breaks the repo==engine identity: the mapping cannot
    # even be BUILT, so no draft, binding or attestation exists.
    drifted = tmp_path / "stock-deep-dive.yaml"
    drifted.write_text(
        P._SWARM_YAML.read_text(encoding="utf-8").replace(
            "news-sentiment", "news-sentiment-drifted"),
        encoding="utf-8")
    monkeypatch.setattr(P, "_ENGINE_SWARM_YAML", drifted)
    with pytest.raises(M.MigrationError):
        P.build_stock_deep_dive_compat_mapping()


def test_nc_authoritative_full18_mapping_is_unmappable_and_yields_nothing(FX):
    # THE ruling's anchor: the authoritative Phase-1 migration of the real 18-node
    # graph is UNMAPPABLE and produces no draft, no binding and no attestation.
    legacy_fx = json.loads(_LEGACY_FIXTURE.read_text(encoding="utf-8"))
    unmappable = M.migrate_legacy_graph(
        legacy_fx["graphs"][0]["normalized_config"],
        source_schema=M.SRC_STOCK_DEEP_DIVE, source_format="json")
    assert unmappable.mapping_status is MappingStatus.UNMAPPABLE
    assert unmappable.source_config_digest == FX["references"]["source_config_digest"]
    env = build_new_env(FX, admit=False)
    with pytest.raises(M.MigrationError):
        P.legacy_draft_from_mapping(
            unmappable, request=env.request, context=env.context, catalog=env.catalog,
            schema_registry=env.registry,
            budget_request={"tokens": 1000, "llm_invocations": 1},
            sink_mapping=("report-writer",))
    with pytest.raises(M.MigrationError):
        M.compatibility_binding_for(unmappable)
    with pytest.raises(M.MigrationError):
        M.attest_static_legacy_plan(
            unmappable, env.draft, env.request, context=env.context,
            catalog=env.catalog.snapshot, schema_registry=env.registry)


@pytest.mark.parametrize("source", [PlanSource.DYNAMIC, PlanSource.BOOTSTRAP])
def test_nc_compat_workers_rejected_under_dynamic_and_bootstrap(FX, source):
    env = build_new_env(FX, admit=False)
    tampered = env.draft.model_copy(update={"source": source})
    env.service._drafts = {env.draft.id: tampered}
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    assert not prep.phase1_report.valid
    assert any(i.code == "compat_worker_forbidden_source"
               for i in prep.phase1_report.issues)
    if source is PlanSource.BOOTSTRAP:
        assert any(i.code == "bootstrap_unsupported" for i in prep.support_report.issues)
    with pytest.raises(AdmissionRejected):
        env.service.persist_and_reserve_candidate(prep, idempotency_key="nc-4")
    _assert_no_budget(env)


def test_nc_caller_supplied_handler_binding_rejected(FX):
    from guanlan_v2.orchestration.refs import ContentRef
    env = build_new_env(FX)
    forged = ContentRef(id="handler.rogue", version="1", content_digest="f" * 64)
    with pytest.raises(CatalogMaterialError):
        env.factories.register_handler(forged, lambda **kw: None)


def test_nc_caller_supplied_approval_rejected(FX):
    env = build_new_env(FX, admit=False)
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    cand, _ = env.service.persist_and_reserve_candidate(prep, idempotency_key="nc-5")
    forged = PlanApproval(
        request_id=env.request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor_id="impostor",
        decided_at=env.clock.now())
    # a caller-constructed authority record is not an ApprovalSubmission.
    with pytest.raises(AdmissionRejected) as ei:
        env.service.record_approval(cand.candidate_plan_digest, forged,
                                    authenticated_actor="impostor",
                                    idempotency_key="nc-5a")
    assert ei.value.code == "caller_carried_authority"
    # no approval on file -> a well-formed submission still cannot approve.
    with pytest.raises(AdmissionRejected) as ei:
        env.service.record_approval(
            cand.candidate_plan_digest,
            ApprovalSubmission(request_id=env.request.request_id,
                               candidate_plan_digest=cand.candidate_plan_digest,
                               decision=ApprovalDecision.APPROVED),
            authenticated_actor="impostor", idempotency_key="nc-5b")
    assert ei.value.code == "missing_approval"
    assert _node_reservations_via(env) == []


def _node_reservations_via(env):
    ledger = BudgetLedger(
        sink=env.stores.budget_event_sink(run_id=env.draft.run_id,
                                          ledger_id=env.run_budget.ledger_id),
        run_budget=env.run_budget)
    st = ledger.replay()
    return [r for r in st.reservations.values() if r.scope_type == "node"]


def test_nc_caller_supplied_reservation_rejected(FX):
    env = build_new_env(FX, admit=False)
    prep = env.service.prepare_candidate(env.draft.id, request_id=env.request.request_id)
    cand, _ = env.service.persist_and_reserve_candidate(prep, idempotency_key="nc-6")
    env.approvals[(env.request.request_id, cand.candidate_plan_digest)] = PlanApproval(
        request_id=env.request.request_id, candidate_plan_digest=cand.candidate_plan_digest,
        decision=ApprovalDecision.APPROVED, actor_id="human-1", decided_at=env.clock.now())
    ev = env.service.record_approval(
        cand.candidate_plan_digest,
        ApprovalSubmission(request_id=env.request.request_id,
                           candidate_plan_digest=cand.candidate_plan_digest,
                           decision=ApprovalDecision.APPROVED),
        authenticated_actor="human-1", idempotency_key="nc-6a")
    with pytest.raises(AdmissionRejected) as ei:
        env.service.freeze_and_admit_candidate(
            cand.candidate_plan_digest, reservation_id="res-forged",
            approval_event_id=ev.event_id, idempotency_key="nc-6b")
    assert ei.value.code == "unknown_reservation"
    assert _node_reservations_via(env) == []


def test_nc_caller_path_never_reaches_the_plan(FX):
    # 'out_dir' resolves ONLY to the service_binding; the frozen plan carries no
    # caller path and no node param for it (structural closure of the path row).
    env = build_new_env(FX)
    plan_json = canonical_json(env.plan)
    assert "out_dir" not in plan_json
    for node in env.plan.nodes:
        assert node.params == {}
    out_row = [i for i in env.mapping.input_mappings if i.source_key == "out_dir"][0]
    assert out_row.target_kind == "service_binding"
    assert out_row.target_service_binding == "output_locator"


def test_nc_material_byte_drift_after_snapshot_fails_rebuild(FX):
    env = build_new_env(FX, admit=False)
    snapshot = env.catalog.snapshot
    text = {}
    for entry in snapshot.content_manifest:
        raw = env.catalog.text(entry.ref).raw_utf8
        if entry.ref.id == "compat.prompt.mirror":
            raw = raw + b"\ntampered-after-snapshot"
        text[(entry.ref.id, entry.ref.version)] = raw
    for entry in snapshot.skill_manifest:
        text[(entry.ref.id, entry.ref.version)] = env.catalog.text(entry.ref).raw_utf8
    caps = {}
    for entry in snapshot.capability_manifest:
        caps[(entry.ref.id, entry.ref.version)] = env.catalog.capability(entry.ref).descriptor
    with pytest.raises(CatalogMaterialError):
        CatalogRuntime.build(snapshot, InMemoryMaterialSource(text=text, capabilities=caps))


def test_nc_profile_changed_after_reservation_rejected_before_execution(FX):
    env = build_new_env(FX)
    tampered = static_runtime_profile().model_copy(update={"profile_digest": "0" * 64})
    env.service._profile = tampered
    with pytest.raises(AdmissionRejected) as ei:
        env.service.verify_for_dispatch(env.plan.plan_digest)
    assert ei.value.code == "profile_drift"
    assert _node_reservations(env) == []  # failed before any node execution


def test_nc_support_report_changed_after_reservation_rejected_before_execution(FX):
    env = build_new_env(FX)
    other = build_new_env(FX, request_id="req-equiv-other")
    bad_runtime = W.ExecutionRuntime(
        catalog=env.catalog, bridge_view=env.view, factories=env.factories,
        support_report=other.bundle.support_report,
        runtime_registry_digest=env.rt_digest)
    with pytest.raises(D.DagRunError):
        env.run(runtime=bad_runtime)
    assert _node_reservations(env) == []  # failed before any node execution
