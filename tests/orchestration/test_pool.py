# -*- coding: utf-8 -*-
"""Phase 2 · Task 4 — ArtifactPool staged→barrier commit + input-snapshot freeze.

Written test-first (RED until ``pool.py`` exists). Locks the reviewed invariants:

* ``stage`` verifies run/plan/node/output binding, payload SchemaRef and the three
  Artifact digests through the Phase 1 builder + registry validator, persists the
  typed payload + a journal-only ``ArtifactStaged`` event, and returns an
  ``ArtifactRef`` that is **not** yet readable as committed;
* ``commit_layer`` is the atomic barrier: it validates every expected output
  (derived from the frozen Worker ``OutputBinding``, never a caller-written slot
  set), assigns a canonical ``artifact_seq`` ordered by ``(node_id, output_key)``
  independent of stage/completion order, stores one typed ``LayerCommit`` payload +
  one public ``LayerCommitted`` event in a single Task 2 unit of work, and only then
  advances the committed index;
* a crash before the barrier exposes none of the layer; replay after it derives the
  committed visibility purely from the ``LayerCommitted`` events + their typed refs;
* identical retries are idempotent, conflicting ones raise ``IdempotencyConflict``;
* ``freeze_input_snapshot`` binds a per-node read-set to the real
  run/Plan/node/layer/attempt identity with exact one/many binding order and the
  ready / terminal_partial matrix; a terminal_partial snapshot can never be
  executed.

Run from repo root: ``python -m pytest tests/orchestration/test_pool.py -v``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.catalog import (
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
    BudgetReservation,
    ClockSpec,
    ContextSnapshot,
    DataContext,
    InputArtifactBinding,
    InputSnapshot,
    MemoryRecordRef,
    build_empty_memory_binding,
)
from guanlan_v2.orchestration.digest import (
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
)
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DataBackend,
    DataMode,
    ExecutionKind,
    NodeStatus,
    PlanSource,
    Tier,
)
from guanlan_v2.orchestration.events import (
    EventType,
    LayerCommit,
    PlanApproval,
)
from guanlan_v2.orchestration.eventstore import (
    IdempotencyConflict,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.refs import (
    ContentRef,
    PayloadRef,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.schemas import (
    Artifact,
    ArtifactRef,
    NodeRun,
    Provenance,
)
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
    freeze_plan,
    validate_plan_draft,
)

from guanlan_v2.orchestration.pool import (
    ArtifactPool,
    ArtifactNotCommitted,
    ArtifactStageError,
    ExpectedOutput,
    InputSnapshotError,
    LateStageError,
    LayerCommitError,
    PoolError,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64
DC = "c" * 64
DD = "d" * 64


def _dt(minute: int = 30) -> datetime:
    return datetime(2026, 7, 16, 1, minute, tzinfo=UTC)


class AdvancingClock:
    def __init__(self, start: datetime | None = None, step: timedelta = timedelta(seconds=1)):
        self._next = start or datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
        self._step = step

    def now(self) -> datetime:
        cur = self._next
        self._next = cur + self._step
        return cur


# --------------------------------------------------------------------------- #
# Registered payload schemas                                                  #
# --------------------------------------------------------------------------- #
class UpstreamOut(DigestModel):
    schema_version: Literal["1"] = "1"
    ticker: NonEmptyStr
    score: FiniteFloat


class OtherOut(DigestModel):
    schema_version: Literal["1"] = "1"
    headline: NonEmptyStr


class SecondaryOut(DigestModel):
    schema_version: Literal["1"] = "1"
    note: NonEmptyStr


SR_UP = SchemaRef(name="UpstreamOut", version="1")
SR_OTHER = SchemaRef(name="OtherOut", version="1")
SR_SEC = SchemaRef(name="SecondaryOut", version="1")


def _registry() -> SchemaRegistry:
    """A sealed runtime registry: the payload schemas + the runtime-control facts
    the pool persists (LayerCommit + InputSnapshot)."""
    reg = SchemaRegistry()
    for m in (UpstreamOut, OtherOut, SecondaryOut, LayerCommit, InputSnapshot):
        reg.register(m)
    reg.seal()
    return reg


# --------------------------------------------------------------------------- #
# Catalog / plan builders (a 4-node all-sink plan; layers assigned by caller)  #
# --------------------------------------------------------------------------- #
def _make_text(id: str, kind: str, text: str):
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=id, version="1", content_digest="0" * 64), kind=kind, raw_utf8=raw
    )
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=id, version="1", content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def _worker(id: str, *, outputs, inputs=()):
    pref, pmat = _make_text(f"{id}.prompt", "prompt", f"You are {id}.")
    pentry = ContentManifestEntry(
        ref=pref, kind="prompt", name=id, description="d", source_identity="gl.src"
    )
    w = WorkerSpec(
        id=id, catalog_role="final", selection_scope="dynamic_allowed", lane="text",
        persona="p", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=pref, inputs=tuple(inputs), outputs=tuple(outputs),
        params_schema_ref=None, evidence_policy=EvidencePolicy(),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none",
    )
    return w, [pmat], [pentry]


def _catalog():
    specs = [
        _worker("pa.worker", outputs=(OutputBinding(name="primary", schema_ref=SR_UP),)),
        _worker("pb.worker", outputs=(OutputBinding(name="primary", schema_ref=SR_UP),)),
        _worker(
            "multi.worker",
            outputs=(
                OutputBinding(name="primary", schema_ref=SR_UP),
                OutputBinding(name="secondary", schema_ref=SR_SEC),
            ),
        ),
        _worker(
            "consumer.worker",
            inputs=(
                InputBinding(name="a", schema_ref=SR_UP, required=False, cardinality="one"),
                InputBinding(name="b", schema_ref=SR_UP, required=False, cardinality="many"),
            ),
            outputs=(OutputBinding(name="primary", schema_ref=SR_OTHER),),
        ),
    ]
    workers, mats, content = [], [], []
    for w, wmats, wentries in specs:
        workers.append(w)
        mats += wmats
        content += wentries
    return build_catalog_snapshot(
        catalog_version="cat.v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=(), workers=tuple(workers), resolved_material=tuple(mats),
    )


_WORKER_OF = {
    "pa": "pa.worker", "pb": "pb.worker", "multi": "multi.worker", "consumer": "consumer.worker",
}
_SLOT_OF = {"pa": "sa", "pb": "sb", "multi": "sm", "consumer": "sc"}


def _context() -> ContextSnapshot:
    binding = build_empty_memory_binding()
    clock = ClockSpec(as_of=_dt(), timezone="Asia/Shanghai", calendar_id="cn_a_share")
    dc = DataContext(
        as_of=_dt(), clock=clock, mode=DataMode.ONLINE, backend=DataBackend.LIVE,
        strict_pit=False, calendar_id="cn_a_share", resolved_vendor_chains={"prices": ("tushare",)},
        source_config_digest=DA, source_registry_digest=DB, routing_snapshot_digest=DC,
        data_snapshot_id="snap-1", data_snapshot_content_digest=DD, built_at=_dt(),
    )
    return ContextSnapshot.build(
        snapshot_id="ctx-1", data_context=dc, memory_snapshot_id="ms-1",
        memory_snapshot_hash=binding.snapshot_hash, past_context_hash=binding.past_context_hash,
        memory_snapshot_ref=binding.memory_snapshot_ref,
        memory_selection_ref=binding.memory_selection_ref,
        runtime_requirements_ref=None, built_at=_dt(),
    )


def _draft(catalog, registry, context):
    nodes = (
        PlanNode(id="pa", worker_id="pa.worker", writes_slot="sa"),
        PlanNode(id="pb", worker_id="pb.worker", writes_slot="sb"),
        PlanNode(id="multi", worker_id="multi.worker", writes_slot="sm"),
        PlanNode(id="consumer", worker_id="consumer.worker", writes_slot="sc"),
    )
    ref = PayloadRef(namespace="main", object_id="ctx-obj", content_digest=context.content_digest)
    return PlanDraft(
        id="plan.x", run_id="run-1", request_id="req-1", phase="main",
        source=PlanSource.DYNAMIC, goal="g", as_of=_dt(), mode=DataMode.ONLINE,
        context_snapshot_ref=ref, nodes=nodes,
        sink_node_ids=("pa", "pb", "multi", "consumer"),
        catalog_version=catalog.catalog_version, catalog_digest=catalog.catalog_digest,
        schema_registry_digest=registry.registry_digest, approval_policy=ApprovalPolicy.REQUIRED,
        budget_request_tokens=1000, budget_request_llm_invocations=2, max_concurrency=4,
    )


def _frozen_plan():
    catalog, registry, context = _catalog(), _registry(), _context()
    request = OrchestrationRequest(
        request_id="req-1", goal="g", workflow="orchestrate_only",
        approval_policy=ApprovalPolicy.REQUIRED,
    )
    draft = _draft(catalog, registry, context)
    report = validate_plan_draft(
        draft, request=request, context=context, catalog=catalog,
        schema_registry=registry, legacy_attestation=None,
    )
    assert report.valid, report.issues
    cand = report.candidate_plan_digest
    reservation = BudgetReservation(
        reservation_id="res-1", ledger_id="ledger-1", run_id="run-1", request_id="req-1",
        candidate_plan_digest=cand, scope_type="plan", scope_id="plan.x", reserved_tokens=1000,
        reserved_llm_invocations=2, reserved_concurrency=2, status="reserved", reserved_at=_dt(),
    )
    approval = PlanApproval(
        request_id="req-1", candidate_plan_digest=cand, decision=ApprovalDecision.APPROVED,
        actor_id="rev-1", decided_at=_dt(),
    )
    plan = freeze_plan(
        draft, request=request, context=context, catalog=catalog, schema_registry=registry,
        legacy_attestation=None, report=report, reservation=reservation, approval=approval,
    )
    return plan, catalog, registry, context


def _pool(clock=None):
    plan, catalog, registry, context = _frozen_plan()
    resolver = SchemaRegistryResolver()
    digest = resolver.register(registry)
    clk = clock or AdvancingClock()
    stores = RuntimeStores(resolver=resolver, clock=clk)
    pool = ArtifactPool(stores=stores, registry_digest=digest, plan=plan, catalog=catalog, clock=clk)
    return pool, stores, plan, context


# --------------------------------------------------------------------------- #
# Artifact / NodeRun builders                                                 #
# --------------------------------------------------------------------------- #
def _provenance(plan_digest: str, *, input_snapshot_digest=None) -> Provenance:
    return Provenance(
        plan_digest=plan_digest, code_version="git:abc", as_of=_dt(), pit_mode=DataMode.ONLINE,
        input_snapshot_digest=input_snapshot_digest,
    )


def _artifact(
    plan, *, node_id, output_key="primary", payload=None, schema_ref=None,
    artifact_id="art-1", run_id="run-1", slot=None, provenance=None, rendered="# r",
):
    return Artifact.build(
        artifact_id=artifact_id, run_id=run_id, producer_node_id=node_id,
        slot=slot if slot is not None else _SLOT_OF[node_id], output_key=output_key,
        kind="k", payload_schema_ref=schema_ref or SR_UP,
        payload=payload if payload is not None else UpstreamOut(ticker="AAA", score=0.5),
        rendered_md=rendered, input_refs=(),
        provenance=provenance or _provenance(plan.plan_digest), numbers=(), badges=(),
        created_at=_dt(45),
    )


def _node_run(
    plan, *, node_id, status=NodeStatus.COMPLETED, node_run_id=None,
    output_keys=None, output_artifact_ids=None,
):
    kw = dict(
        node_run_id=node_run_id or f"nr-{node_id}", run_id="run-1", plan_id=plan.plan_id,
        plan_digest=plan.plan_digest, node_id=node_id, worker_id=_WORKER_OF[node_id],
        status=status, attempt_id=f"att-{node_id}", attempt=1, input_snapshot_digest="1" * 64,
    )
    if status is NodeStatus.COMPLETED:
        kw["output_keys"] = output_keys or ("primary",)
        kw["output_artifact_ids"] = output_artifact_ids or ("art-1",)
    elif status in (NodeStatus.FAILED, NodeStatus.TIMED_OUT, NodeStatus.CANCELLED):
        kw["reason_code"] = "boom"
    return NodeRun(**kw)


def _ctx_ref(content=None) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name="ContextSnapshot", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="ctx-obj", content_digest=content or DA),
    )


def _in_ref(*, content=DA, artifact_id="in-1", node="pa") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id, schema_ref=SR_UP, producer_node_id=node,
        slot=_SLOT_OF[node], output_key="primary", content_digest=content,
    )


# =========================================================================== #
# stage — binding / digest / provenance verification                          #
# =========================================================================== #
def test_stage_returns_an_artifact_ref_bound_to_the_artifact():
    pool, _stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa")
    ref = pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")
    assert isinstance(ref, ArtifactRef)
    assert ref.artifact_id == art.artifact_id
    assert ref.content_digest == art.content_digest
    assert ref.producer_node_id == "pa" and ref.output_key == "primary" and ref.slot == "sa"


def test_stage_rejects_a_foreign_run_id():
    pool, _stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa", run_id="run-OTHER")
    with pytest.raises(PoolError):
        pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")


def test_stage_rejects_an_unknown_producer_node():
    pool, _stores, plan, _ = _pool()
    art = _artifact(plan, node_id="ghost", slot="sa")
    with pytest.raises(ArtifactStageError):
        pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")


def test_stage_rejects_an_output_key_the_worker_does_not_declare():
    pool, _stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa", output_key="nope")
    with pytest.raises(ArtifactStageError):
        pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")


def test_stage_rejects_a_payload_schema_that_disagrees_with_the_output_binding():
    pool, _stores, plan, _ = _pool()
    # multi.secondary is SecondaryOut; declaring it as UpstreamOut is a mismatch.
    art = _artifact(
        plan, node_id="multi", output_key="secondary", schema_ref=SR_UP,
        payload=UpstreamOut(ticker="X", score=1.0),
    )
    with pytest.raises(ArtifactStageError):
        pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="multi"), idempotency_key="s1")


def test_stage_rejects_a_slot_that_is_not_the_nodes_writes_slot():
    pool, _stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa", slot="WRONG")
    with pytest.raises(ArtifactStageError):
        pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")


def test_stage_rejects_provenance_not_bound_to_the_pool_plan():
    pool, _stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa", provenance=_provenance(DB))  # a valid but foreign digest
    with pytest.raises(ArtifactStageError):
        pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")


def test_stage_rejects_a_tampered_digest_artifact():
    pool, _stores, plan, _ = _pool()
    good = _artifact(plan, node_id="pa")
    # bypass the Artifact validator with model_construct, then tamper the content digest.
    bad = good.model_copy(update={"content_digest": "e" * 64})
    with pytest.raises((ArtifactStageError, ValidationError)):
        pool.stage(bad, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")


def test_stage_rejects_a_node_run_that_does_not_bind_the_plan():
    pool, _stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa")
    bad_nr = _node_run(plan, node_id="pa").model_copy(update={"plan_digest": "e" * 64})
    with pytest.raises(PoolError):
        pool.stage(art, layer_index=0, node_run=bad_nr, idempotency_key="s1")


# =========================================================================== #
# staged artifacts are invisible before the barrier                           #
# =========================================================================== #
def test_staged_artifact_is_not_readable_as_committed():
    pool, _stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa")
    ref = pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")
    # possessing the ref does not authorize a committed read before the barrier.
    with pytest.raises(ArtifactNotCommitted):
        pool.committed(ref)
    assert pool.committed_output("pa", "primary") is None


def test_a_staged_low_level_payload_does_not_authorize_an_input_read():
    # invariant 3: the payload sits in the low-level PayloadStore, but the pool's
    # committed index — the only dataflow read path — does not expose it.
    pool, stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa")
    pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")
    assert stores.payloads_object_count() == 1  # the payload IS persisted
    assert pool.committed_output("pa", "primary") is None  # yet not readable


def test_artifact_staged_event_never_gains_a_visible_sequence():
    pool, stores, plan, _ = _pool()
    art = _artifact(plan, node_id="pa")
    pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")
    journal = stores.events.journal("run-1", "main")
    assert [e.event_type for e in journal] == [EventType.ARTIFACT_STAGED]
    assert journal[0].visible_seq is None
    assert stores.events.visible("run-1", "main") == ()


# =========================================================================== #
# commit_layer — the atomic barrier                                           #
# =========================================================================== #
def _stage_layer0_pa_pb(pool, plan):
    a = _artifact(plan, node_id="pa", artifact_id="art-pa", payload=UpstreamOut(ticker="AAA", score=0.1))
    b = _artifact(plan, node_id="pb", artifact_id="art-pb", payload=UpstreamOut(ticker="BBB", score=0.2))
    ra = pool.stage(a, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s-pa")
    rb = pool.stage(b, layer_index=0, node_run=_node_run(plan, node_id="pb"), idempotency_key="s-pb")
    return ra, rb, a, b


def _expected(pool, node_runs):
    return pool.derive_expected_outputs(node_runs)


def test_commit_layer_makes_the_whole_layer_readable():
    pool, stores, plan, _ = _pool()
    ra, rb, a, b = _stage_layer0_pa_pb(pool, plan)
    runs = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
    lc = pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    assert isinstance(lc, LayerCommit)
    assert pool.committed(ra) == a
    assert pool.committed(rb) == b
    assert pool.committed_output("pa", "primary") == a
    assert pool.committed_output("pb", "primary") == b
    # exactly one public LayerCommitted event was appended.
    visible = stores.events.visible("run-1", "main")
    assert [e.event_type for e in visible] == [EventType.LAYER_COMMITTED]


def test_committed_output_resolves_the_exact_committed_ref_not_latest_by_slot():
    pool, _stores, plan, _ = _pool()
    ra, _rb, a, _b = _stage_layer0_pa_pb(pool, plan)
    runs = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
    pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    # a ref carrying a different content digest is rejected (exact digest, not slot).
    tampered = ra.model_copy(update={"content_digest": "e" * 64})
    with pytest.raises(ArtifactNotCommitted):
        pool.committed(tampered)
    assert pool.committed(ra) == a


def test_canonical_artifact_seq_is_independent_of_stage_order():
    def seqs(order):
        pool, _stores, plan, _ = _pool()
        a = _artifact(plan, node_id="pa", artifact_id="art-pa", payload=UpstreamOut(ticker="AAA", score=0.1))
        b = _artifact(plan, node_id="pb", artifact_id="art-pb", payload=UpstreamOut(ticker="BBB", score=0.2))
        pairs = {"pa": (a, "s-pa"), "pb": (b, "s-pb")}
        for nid in order:
            art, key = pairs[nid]
            pool.stage(art, layer_index=0, node_run=_node_run(plan, node_id=nid), idempotency_key=key)
        runs = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
        lc = pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
        return {c.artifact_id: c.artifact_seq for c in lc.artifacts}

    forward = seqs(["pa", "pb"])
    reverse = seqs(["pb", "pa"])
    assert forward == reverse
    # ordered by (node_id, output_key): pa < pb.
    assert forward == {"art-pa": 1, "art-pb": 2}


def test_commit_rejects_a_caller_written_expected_output_set():
    pool, _stores, plan, _ = _pool()
    _stage_layer0_pa_pb(pool, plan)
    runs = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
    # a fabricated expected set that is NOT the frozen Worker OutputBinding derivation.
    forged = (ExpectedOutput(node_id="pa", output_key="primary", schema_ref=SR_UP),)
    with pytest.raises(LayerCommitError):
        pool.commit_layer(0, node_runs=runs, expected_outputs=forged, idempotency_key="c0")
    assert pool.committed_output("pa", "primary") is None  # nothing committed


def test_missing_output_prevents_the_whole_commit():
    pool, stores, plan, _ = _pool()
    # multi declares two outputs; stage only the primary.
    prim = _artifact(plan, node_id="multi", output_key="primary", schema_ref=SR_UP,
                     artifact_id="art-mp", payload=UpstreamOut(ticker="M", score=1.0))
    pool.stage(prim, layer_index=0, node_run=_node_run(plan, node_id="multi"), idempotency_key="s-mp")
    runs = (_node_run(plan, node_id="multi", output_keys=("primary", "secondary"),
                      output_artifact_ids=("art-mp", "art-ms")),)
    with pytest.raises(LayerCommitError):
        pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    # atomic: no LayerCommitted event, primary still only staged.
    assert stores.events.visible("run-1", "main") == ()
    assert pool.committed_output("multi", "primary") is None


def test_extra_staged_output_prevents_the_commit():
    pool, _stores, plan, _ = _pool()
    _stage_layer0_pa_pb(pool, plan)
    # commit with node_runs that only cover pa — pb's staged artifact is now "extra".
    runs = (_node_run(plan, node_id="pa"),)
    with pytest.raises(LayerCommitError):
        pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    assert pool.committed_output("pa", "primary") is None


def test_duplicate_output_with_different_content_fails_at_stage():
    pool, _stores, plan, _ = _pool()
    a1 = _artifact(plan, node_id="pa", artifact_id="art-1", payload=UpstreamOut(ticker="AAA", score=0.1))
    a2 = _artifact(plan, node_id="pa", artifact_id="art-2", payload=UpstreamOut(ticker="AAA", score=0.9))
    pool.stage(a1, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")
    with pytest.raises(PoolError):
        pool.stage(a2, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s2")


def test_late_stage_after_the_layer_commits_fails():
    pool, _stores, plan, _ = _pool()
    a = _artifact(plan, node_id="pa", artifact_id="art-pa")
    pool.stage(a, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s-pa")
    b = _artifact(plan, node_id="pb", artifact_id="art-pb", payload=UpstreamOut(ticker="B", score=0.3))
    pool.stage(b, layer_index=0, node_run=_node_run(plan, node_id="pb"), idempotency_key="s-pb")
    runs = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
    pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    # staging a new artifact into the already-committed layer is rejected.
    late = _artifact(plan, node_id="multi", artifact_id="art-late",
                     payload=UpstreamOut(ticker="L", score=0.4))
    with pytest.raises(LateStageError):
        pool.stage(late, layer_index=0, node_run=_node_run(plan, node_id="multi"), idempotency_key="s-late")


def test_non_success_node_runs_require_no_artifact():
    pool, _stores, plan, _ = _pool()
    a = _artifact(plan, node_id="pa", artifact_id="art-pa")
    pool.stage(a, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s-pa")
    # pb was SKIPPED and produced nothing; the commit must still succeed.
    runs = (
        _node_run(plan, node_id="pa"),
        _node_run(plan, node_id="pb", status=NodeStatus.SKIPPED),
    )
    lc = pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    assert [c.artifact_id for c in lc.artifacts] == ["art-pa"]
    assert pool.committed_output("pa", "primary") == a
    assert pool.committed_output("pb", "primary") is None


# =========================================================================== #
# Idempotency                                                                 #
# =========================================================================== #
def test_identical_stage_retry_is_idempotent_and_conflicting_retry_raises():
    pool, stores, plan, _ = _pool()
    a = _artifact(plan, node_id="pa")
    r1 = pool.stage(a, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")
    r2 = pool.stage(a, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")
    assert r1 == r2
    assert len(stores.events.journal("run-1", "main")) == 1  # no duplicate staged event
    other = _artifact(plan, node_id="pa", artifact_id="art-2", payload=UpstreamOut(ticker="Z", score=0.9))
    with pytest.raises(IdempotencyConflict):
        pool.stage(other, layer_index=0, node_run=_node_run(plan, node_id="pa"), idempotency_key="s1")


def test_identical_commit_retry_returns_the_stored_layer_commit():
    pool, stores, plan, _ = _pool()
    _stage_layer0_pa_pb(pool, plan)
    runs = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
    lc1 = pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    lc2 = pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    assert lc1.semantic_digest() == lc2.semantic_digest()
    assert len(stores.events.visible("run-1", "main")) == 1  # no second barrier event


def test_conflicting_commit_same_key_raises_idempotency_conflict():
    pool, _stores, plan, _ = _pool()
    _stage_layer0_pa_pb(pool, plan)
    runs_full = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
    pool.commit_layer(0, node_runs=runs_full, expected_outputs=_expected(pool, runs_full), idempotency_key="c0")
    # same idempotency key, different layer content → conflict.
    runs_pa = (_node_run(plan, node_id="pa"),)
    with pytest.raises(IdempotencyConflict):
        pool.commit_layer(1, node_runs=runs_pa, expected_outputs=_expected(pool, runs_pa), idempotency_key="c0")


def test_recommitting_a_layer_under_a_different_key_conflicts():
    pool, _stores, plan, _ = _pool()
    _stage_layer0_pa_pb(pool, plan)
    runs = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
    pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    with pytest.raises(IdempotencyConflict):
        pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0-other")


# =========================================================================== #
# Crash-before / replay-after matrix                                          #
# =========================================================================== #
class _RaisingUoW:
    def commit(self, batch):
        raise RuntimeError("simulated crash before the barrier commits")


def test_crash_before_barrier_exposes_none_then_replay_after_commit_exposes_all():
    pool, stores, plan, _ = _pool()
    ra, rb, a, b = _stage_layer0_pa_pb(pool, plan)
    runs = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))

    # crash BEFORE the atomic barrier: the unit of work raises mid-commit.
    real_uow = stores.unit_of_work
    stores.unit_of_work = _RaisingUoW()
    with pytest.raises(RuntimeError):
        pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    stores.unit_of_work = real_uow
    # nothing was exposed — no visible event, no committed artifact.
    assert stores.events.visible("run-1", "main") == ()
    assert pool.committed_output("pa", "primary") is None

    # the same commit, now succeeding, makes the layer visible.
    lc = pool.commit_layer(0, node_runs=runs, expected_outputs=_expected(pool, runs), idempotency_key="c0")
    assert pool.committed_output("pa", "primary") == a

    # replay after the barrier reproduces the whole committed layer.
    replayed = pool.replay()
    assert replayed.committed(ra) == a
    assert replayed.committed(rb) == b
    assert replayed.committed_output("pa", "primary") == a
    assert replayed.committed_output("pb", "primary") == b


def test_replay_derives_committed_visibility_only_from_layer_committed_events():
    pool, stores, plan, _ = _pool()
    # layer 0 committed; a second artifact staged for layer 1 but NEVER committed.
    ra, rb, a, b = _stage_layer0_pa_pb(pool, plan)
    runs0 = (_node_run(plan, node_id="pa"), _node_run(plan, node_id="pb"))
    pool.commit_layer(0, node_runs=runs0, expected_outputs=_expected(pool, runs0), idempotency_key="c0")
    uncommitted = _artifact(plan, node_id="consumer", artifact_id="art-un",
                            schema_ref=SR_OTHER, payload=OtherOut(headline="draft"))
    r_un = pool.stage(uncommitted, layer_index=1, node_run=_node_run(plan, node_id="consumer"),
                      idempotency_key="s-un")

    replayed = pool.replay()
    # committed layer-0 artifacts are exposed…
    assert replayed.committed_output("pa", "primary") == a
    assert replayed.committed_output("pb", "primary") == b
    # …but the staged-only layer-1 artifact is NOT (no LayerCommitted event for it).
    assert replayed.committed_output("consumer", "primary") is None
    with pytest.raises(ArtifactNotCommitted):
        replayed.committed(r_un)


# =========================================================================== #
# freeze_input_snapshot — identity, ordering, readiness matrix                #
# =========================================================================== #
def _consumer_node(plan):
    return next(n for n in plan.nodes if n.id == "consumer")


def _freeze(pool, plan, **over):
    node = _consumer_node(plan)
    base = dict(
        node=node, run_id="run-1", plan=plan, layer_index=1, attempt=1,
        context_snapshot_ref=_ctx_ref(), bound_artifact_inputs=(), data_result_refs=(),
        memory_record_refs=(), readiness="ready", missing_input_names=(),
    )
    base.update(over)
    return pool.freeze_input_snapshot(base.pop("node"), **base)


def test_freeze_ready_snapshot_binds_the_real_identity():
    pool, _stores, plan, _ = _pool()
    snap = _freeze(pool, plan)
    assert isinstance(snap, InputSnapshot)
    assert snap.run_id == "run-1"
    assert snap.plan_id == plan.plan_id
    assert snap.plan_digest == plan.plan_digest
    assert snap.node_id == "consumer"
    assert snap.layer_index == 1 and snap.attempt == 1
    assert snap.readiness == "ready"
    assert snap.content_digest and snap.content_digest != "0" * 64


def test_freeze_ready_one_and_many_bindings_follow_worker_declaration_order():
    pool, _stores, plan, _ = _pool()
    one = InputArtifactBinding(input_name="a", cardinality="one", artifact_refs=(_in_ref(content=DA),))
    many = InputArtifactBinding(
        input_name="b", cardinality="many",
        artifact_refs=(_in_ref(content=DB, artifact_id="m1"), _in_ref(content=DC, artifact_id="m2")),
    )
    snap = _freeze(pool, plan, bound_artifact_inputs=(one, many))
    assert [b.input_name for b in snap.artifact_inputs] == ["a", "b"]
    # outer order must follow the WorkerSpec declaration order (a before b).
    with pytest.raises(InputSnapshotError):
        _freeze(pool, plan, bound_artifact_inputs=(many, one))


def test_freeze_many_binding_preserves_declaration_order_in_the_digest():
    pool, _stores, plan, _ = _pool()
    r1, r2 = _in_ref(content=DB, artifact_id="m1"), _in_ref(content=DC, artifact_id="m2")
    fwd = _freeze(pool, plan, bound_artifact_inputs=(
        InputArtifactBinding(input_name="b", cardinality="many", artifact_refs=(r1, r2)),))
    rev = _freeze(pool, plan, bound_artifact_inputs=(
        InputArtifactBinding(input_name="b", cardinality="many", artifact_refs=(r2, r1)),))
    assert fwd.content_digest != rev.content_digest


def test_freeze_rejects_an_input_the_worker_does_not_declare():
    pool, _stores, plan, _ = _pool()
    bogus = InputArtifactBinding(input_name="ghost", cardinality="one", artifact_refs=(_in_ref(),))
    with pytest.raises(InputSnapshotError):
        _freeze(pool, plan, bound_artifact_inputs=(bogus,))


def test_freeze_rejects_a_cardinality_that_disagrees_with_the_worker():
    pool, _stores, plan, _ = _pool()
    # input "a" is declared cardinality-one; presenting it as many is rejected.
    wrong = InputArtifactBinding(input_name="a", cardinality="many", artifact_refs=())
    with pytest.raises(InputSnapshotError):
        _freeze(pool, plan, bound_artifact_inputs=(wrong,))


def test_freeze_rejects_a_foreign_run_or_plan():
    pool, _stores, plan, _ = _pool()
    with pytest.raises(PoolError):
        _freeze(pool, plan, run_id="run-OTHER")


def test_terminal_partial_records_missing_inputs_and_cannot_be_executed():
    pool, _stores, plan, _ = _pool()
    partial = pool.freeze_input_snapshot(
        _consumer_node(plan), run_id="run-1", plan=plan, layer_index=1, attempt=1,
        context_snapshot_ref=_ctx_ref(),
        bound_artifact_inputs=(InputArtifactBinding(input_name="a", cardinality="one",
                                                    artifact_refs=(_in_ref(),)),),
        data_result_refs=(), memory_record_refs=(),
        readiness="terminal_partial", missing_input_names=("b",),
    )
    assert partial.readiness == "terminal_partial"
    assert partial.missing_input_names == ("b",)
    # a terminal_partial snapshot can never be handed to execution.
    with pytest.raises(InputSnapshotError):
        pool.assert_executable(partial)
    # a ready snapshot passes the same gate.
    ready = _freeze(pool, plan)
    assert pool.assert_executable(ready) is ready


def test_context_data_memory_and_artifact_changes_each_move_the_snapshot_digest():
    pool, _stores, plan, _ = _pool()
    base = _freeze(pool, plan)

    ctx_changed = _freeze(pool, plan, context_snapshot_ref=_ctx_ref(content=DB))
    assert ctx_changed.content_digest != base.content_digest

    data_changed = _freeze(pool, plan, data_result_refs=(
        TypedPayloadRef(schema_ref=SR_UP,
                        payload_ref=PayloadRef(namespace="main", object_id="dr", content_digest=DB)),))
    assert data_changed.content_digest != base.content_digest

    mem_changed = _freeze(pool, plan, memory_record_refs=(
        MemoryRecordRef(record_id="rec.1", revision_id="v1", available_at=_dt(), content_digest=DC),))
    assert mem_changed.content_digest != base.content_digest

    art_changed = _freeze(pool, plan, bound_artifact_inputs=(
        InputArtifactBinding(input_name="a", cardinality="one", artifact_refs=(_in_ref(content=DB),)),))
    assert art_changed.content_digest != base.content_digest


# =========================================================================== #
# No blank / short digest can ever be persisted (Phase 1 models enforce it)    #
# =========================================================================== #
def test_no_blank_or_short_digest_survives_the_phase1_models():
    with pytest.raises(ValidationError):
        Provenance(plan_digest="", code_version="c", as_of=_dt(), pit_mode=DataMode.ONLINE)
    with pytest.raises(ValidationError):
        LayerCommit(plan_digest="short", layer_index=0, artifacts=(), committed_at=_dt())
    with pytest.raises(ValidationError):
        InputSnapshot.build(
            snapshot_id="s", run_id="r", plan_id="p", plan_digest="", node_id="n",
            layer_index=0, attempt=1, context_snapshot_ref=_ctx_ref(), artifact_inputs=(),
            data_result_refs=(), memory_record_refs=(), readiness="ready",
            missing_input_names=(), built_at=_dt(),
        )


def test_derive_expected_outputs_comes_from_the_frozen_worker_binding():
    pool, _stores, plan, _ = _pool()
    runs = (
        _node_run(plan, node_id="multi", output_keys=("primary", "secondary"),
                  output_artifact_ids=("x", "y")),
        _node_run(plan, node_id="pb", status=NodeStatus.SKIPPED),  # non-success: no output
    )
    derived = set(pool.derive_expected_outputs(runs))
    assert derived == {
        ExpectedOutput(node_id="multi", output_key="primary", schema_ref=SR_UP),
        ExpectedOutput(node_id="multi", output_key="secondary", schema_ref=SR_SEC),
    }
