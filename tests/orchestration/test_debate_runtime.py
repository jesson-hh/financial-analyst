# -*- coding: utf-8 -*-
"""Phase 8 * Task 9 — Lane-D bounded-debate runtime.

Locks the debate runtime layer that sits on top of the FROZEN Phase-1 debate
contracts (``DebateCfg`` / the debate ``PlanNode`` fields / the six Phase-1 debate
issue codes) and the Task-8 deterministic reducer-fold seam
(``pool.fold_reducer_producers``):

* ``DebateMessage`` / ``DebateTranscript`` immutable payloads (identity, frozen,
  extra-forbid, ``created_at`` audit-only, ordering / uniqueness validators);
* the ``verify_debate_expansion`` "wan quan zhan kai" verifier (complete rounds
  ``1..R`` covering ``turn_order`` in order) with its three Task-9 issue codes,
  deferring to Phase 1 for ``debate_round_out_of_range`` / ``duplicate_debate_turn``;
* ``count_debate_invocations`` (every seat x round = one LLM invocation);
* ``analyze_debates`` (seats LLM, judge not a seat, ``max_rounds <= 2``, budget
  >= expanded invocation count) run BEFORE any reservation;
* the deterministic Plan-order fold (``fold_debate_messages`` +
  ``debate.transcript_reducer`` handler) — shuffled completion yields a
  byte-identical transcript, and event replay equals the pool fold;
* the additive ``EventType.DEBATE_MESSAGE_PUBLISHED`` member + public visibility
  rule + persist-then-publish idempotency through the REAL Phase-2 EventStore.

Run: ``python -m pytest tests/orchestration/test_debate_runtime.py -v``
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    OutputBinding,
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
    PlanSource,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.eventstore import (
    IdempotencyConflict,
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.schemas import Artifact, Provenance
from guanlan_v2.orchestration.spec import (
    DebateCfg,
    PlanDraft,
    PlanNode,
    PlanStructureError,
    ReducerCfg,
    validate_plan_structure,
)
from guanlan_v2.orchestration.runtime_support import STATIC_RUNTIME_PROFILE_V2

from guanlan_v2.orchestration.debate import (
    DEBATE_MAX_ROUNDS,
    DEBATE_MESSAGE_SCHEMA_REF,
    DEBATE_TRANSCRIPT_REDUCER_ID,
    DebateMessage,
    DebateTranscript,
    analyze_debates,
    count_debate_invocations,
    debate_message_key,
    debate_transcript_reducer_handler,
    fold_debate_messages,
    publish_debate_message,
    replay_debate_transcript,
    verify_debate_expansion,
)

from tests.orchestration.test_budget_ledger import AdvancingClock

UTC = timezone.utc
DIG = "a" * 64
SR_OUT = SchemaRef(name="OutPayload", version="1")


def _dt(hour: int = 1) -> datetime:
    return datetime(2026, 7, 20, hour, 30, tzinfo=UTC)


class OutPayload(DigestModel):
    schema_version: Literal["1"] = "1"
    note: NonEmptyStr = "n"


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(OutPayload)
    reg.register(DebateMessage)
    reg.register(DebateTranscript)
    reg.seal()
    return reg


def _text(cid: str, kind: str, text: str):
    raw = text.encode("utf-8")
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=cid, version="1", content_digest="0" * 64), kind=kind, raw_utf8=raw)
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=cid, version="1", content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def _llm_worker(worker_id: str, prompt_ref: ContentRef) -> WorkerSpec:
    return WorkerSpec(
        id=worker_id, catalog_role="final", selection_scope="dynamic_allowed", lane="text",
        persona="debater", tier=Tier.WRITER,
        execution=ExecutionSpec(kind=ExecutionKind.LLM, model_tier="reasoner"),
        system_prompt_ref=prompt_ref, read_categories=(), inputs=(),
        outputs=(OutputBinding(name="primary", schema_ref=SR_OUT),),
        evidence_policy=EvidencePolicy(tool_calls=ToolCallRequirement.OPTIONAL),
        supported_modes=(DataMode.ONLINE,), can_emit_decision=False, decision_authority="none")


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


def build_debate_catalog(llm_ids=("bull", "bear", "judge"), det_ids=()):
    """A minimal catalog: an LLM worker per ``llm_ids``, a deterministic worker per
    ``det_ids``, one shared prompt + one ``kind='reducer'`` material."""
    prompt_ref, prompt_mat = _text("text.debate.prompt", "prompt", "You are a debate seat.")
    red_ref, red_mat = _text("m.debate_reducer", "reducer", "debate.transcript_reducer material")
    content = [
        ContentManifestEntry(ref=prompt_ref, kind="prompt", name="P", description="d", source_identity="gl"),
        ContentManifestEntry(ref=red_ref, kind="reducer", name="R", description="d", source_identity="gl"),
    ]
    mats = [prompt_mat, red_mat]
    workers = [_llm_worker(f"w.{wid}", prompt_ref) for wid in llm_ids]
    for i, did in enumerate(det_ids):
        h_ref, h_mat = _text(f"h.{did}", "handler", f"def {did}(): return {i}")
        content.append(ContentManifestEntry(ref=h_ref, kind="handler", name=did, description="d", source_identity="gl"))
        mats.append(h_mat)
        workers.append(_det_worker(f"w.{did}", h_ref))
    snapshot = build_catalog_snapshot(
        catalog_version="deb-v1", content_manifest=tuple(content), skill_manifest=(),
        capability_manifest=(), workers=tuple(workers), resolved_material=tuple(mats))
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in mats}, capabilities={})
    runtime = CatalogRuntime.build(snapshot, source)
    return runtime, snapshot, red_ref


def _seat_node(node_id, worker_id, debate_id, round_, turn, role, slot=None):
    # each seat writes a unique slot and is auxiliary, so a debate draft is a valid
    # Phase-1 graph (no multi-writer / reachability concern) without wiring the real
    # reducer+judge dependency edges the analyzer under test does not read.
    return PlanNode(
        id=node_id, worker_id=worker_id, writes_slot=(slot or f"seat.{node_id}"),
        debate_id=debate_id, round_role=role, debate_round=round_, debate_turn=turn,
        auxiliary=True)


def _debate_cfg(debate_id="deb.bullbear", seats=("bull", "bear"),
                turn_order=("bull", "bear"), max_rounds=2, judge="n.judge"):
    return DebateCfg(id=debate_id, seats=tuple(seats), turn_order=tuple(turn_order),
                     max_rounds=max_rounds, judge_node_id=judge)


def _bullbear_nodes(debate_id="deb.bullbear", rounds=2):
    """Seat nodes for a bull/bear debate over ``rounds`` rounds (turn_order bull,bear)."""
    nodes = []
    for r in range(1, rounds + 1):
        nodes.append(_seat_node(f"n.bull.r{r}", "w.bull", debate_id, r, 1, "bull"))
        nodes.append(_seat_node(f"n.bear.r{r}", "w.bear", debate_id, r, 2, "bear"))
    return nodes


def _draft(nodes, *, sinks, debates=(), reducers=(), budget_llm=0, snapshot=None,
           registry_digest=DIG, source=PlanSource.DYNAMIC):
    ctx_ref = PayloadRef(namespace="main", object_id="ctx", content_digest=DIG)
    return PlanDraft(
        id="plan.deb", run_id="run-1", request_id="req-1", phase="main", source=source,
        goal="g", as_of=_dt(), mode=DataMode.ONLINE, context_snapshot_ref=ctx_ref,
        nodes=tuple(nodes), sink_node_ids=tuple(sinks), debates=tuple(debates),
        reducers=tuple(reducers), budget_request_llm_invocations=budget_llm,
        catalog_version=(snapshot.catalog_version if snapshot else "deb-v1"),
        catalog_digest=(snapshot.catalog_digest if snapshot else DIG),
        schema_registry_digest=registry_digest, approval_policy=ApprovalPolicy.REQUIRED)


def _judge_node(node_id="n.judge", worker_id="w.judge", slot="judge.slot"):
    # a non-auxiliary sink node (the judge / collector) the seats' debate feeds.
    return PlanNode(id=node_id, worker_id=worker_id, writes_slot=slot)


def _msg(debate_id="deb.bullbear", round_=1, turn=1, role="bull", art="art-1", hour=1):
    return DebateMessage(debate_id=debate_id, round=round_, turn=turn, role=role,
                         artifact_id=art, created_at=_dt(hour))


def _artifact(node_id: str, role: str, hour: int = 1) -> Artifact:
    prov = Provenance(plan_digest=DIG, code_version="v1", as_of=_dt(), pit_mode=DataMode.ONLINE)
    return Artifact.build(
        artifact_id=f"art-{node_id}", run_id="run-1", created_at=_dt(hour),
        producer_node_id=node_id, slot="debate.slot", output_key="primary", kind="debate_turn",
        payload_schema_ref=SR_OUT, payload=OutPayload(note=role), rendered_md=f"# {role}",
        provenance=prov)


# =========================================================================== #
# Row 1 — DebateMessage construction / frozen / extra-forbid / created_at audit  #
# =========================================================================== #
def test_debate_message_is_digest_model_frozen_and_extra_forbid():
    m = _msg()
    assert isinstance(m, DigestModel)
    with pytest.raises(ValidationError):        # frozen
        m.round = 2
    with pytest.raises(ValidationError):        # extra forbid
        DebateMessage(debate_id="d", round=1, turn=1, role="bull", artifact_id="a",
                      created_at=_dt(), surprise="x")


def test_debate_message_created_at_is_audit_only():
    a = _msg(hour=1)
    b = _msg(hour=17)
    assert a.created_at != b.created_at
    assert a.semantic_digest() == b.semantic_digest()      # created_at excluded
    assert a.audit_digest_value() != b.audit_digest_value()


def test_debate_message_identity_participates_in_semantic_digest():
    base = _msg()
    for over in (dict(round_=2), dict(turn=2), dict(role="bear"), dict(art="art-2")):
        assert _msg(**over).semantic_digest() != base.semantic_digest()


# =========================================================================== #
# Row 2 — DebateTranscript ordering / uniqueness / round<=max validators         #
# =========================================================================== #
def test_transcript_wellformed():
    t = DebateTranscript(debate_id="deb.bullbear", max_rounds=2,
                         messages=(_msg(round_=1, turn=1, role="bull", art="a1"),
                                   _msg(round_=1, turn=2, role="bear", art="a2"),
                                   _msg(round_=2, turn=1, role="bull", art="a3"),
                                   _msg(round_=2, turn=2, role="bear", art="a4")))
    assert len(t.messages) == 4


def test_transcript_rejects_out_of_order():
    with pytest.raises(ValidationError):
        DebateTranscript(debate_id="deb.bullbear", max_rounds=2,
                         messages=(_msg(round_=1, turn=2, role="bear", art="a2"),
                                   _msg(round_=1, turn=1, role="bull", art="a1")))


def test_transcript_rejects_duplicate_round_turn():
    with pytest.raises(ValidationError):
        DebateTranscript(debate_id="deb.bullbear", max_rounds=2,
                         messages=(_msg(round_=1, turn=1, role="bull", art="a1"),
                                   _msg(round_=1, turn=1, role="bear", art="a2")))


def test_transcript_rejects_duplicate_round_role():
    with pytest.raises(ValidationError):
        DebateTranscript(debate_id="deb.bullbear", max_rounds=2,
                         messages=(_msg(round_=1, turn=1, role="bull", art="a1"),
                                   _msg(round_=1, turn=2, role="bull", art="a2")))


def test_transcript_rejects_round_over_max():
    with pytest.raises(ValidationError):
        DebateTranscript(debate_id="deb.bullbear", max_rounds=2,
                         messages=(_msg(round_=3, turn=1, role="bull", art="a1"),))


def test_transcript_rejects_foreign_debate_id():
    with pytest.raises(ValidationError):
        DebateTranscript(debate_id="deb.bullbear", max_rounds=2,
                         messages=(_msg(debate_id="deb.other", round_=1, turn=1, role="bull"),))


# =========================================================================== #
# Row 3/4/5 — verify_debate_expansion (Task-9 codes) + Phase-1 deferral          #
# =========================================================================== #
def _codes(issues):
    return {i.code for i in issues}


def test_expansion_wellformed_2x2_is_empty():
    debate = _debate_cfg()
    draft = _draft(_bullbear_nodes(rounds=2) + [_judge_node()],
                   sinks=("n.judge",), debates=(debate,))
    assert verify_debate_expansion(draft, debate) == ()


def test_expansion_missing_bear_round2_is_incomplete_round():
    """Row 3: 2x2 bull/bear missing bear r2 -> debate_incomplete_round."""
    debate = _debate_cfg()
    nodes = _bullbear_nodes(rounds=2)
    nodes = [n for n in nodes if n.id != "n.bear.r2"]        # drop round-2 bear
    draft = _draft(nodes + [_judge_node()], sinks=("n.judge",), debates=(debate,))
    issues = verify_debate_expansion(draft, debate)
    assert "debate_incomplete_round" in _codes(issues)


def test_expansion_turn_order_swapped_is_mismatch():
    """Row 4: round-1 turns are (bear, bull) but turn_order=(bull, bear)."""
    debate = _debate_cfg(turn_order=("bull", "bear"))
    # turn 1 carries role 'bear', turn 2 carries role 'bull' — a legal Phase-1 draft
    # (unique (round,turn) + (round,role), roles are seats) but a turn-order violation.
    nodes = [
        _seat_node("n.a.r1", "w.bear", "deb.bullbear", 1, 1, "bear"),
        _seat_node("n.b.r1", "w.bull", "deb.bullbear", 1, 2, "bull"),
    ]
    draft = _draft(nodes + [_judge_node()], sinks=("n.judge",), debates=(debate,))
    issues = verify_debate_expansion(draft, debate)
    assert "debate_turn_order_mismatch" in _codes(issues)


def test_round_over_max_is_phase1_not_task9():
    """Row 5: a round beyond max_rounds is a Phase-1 structural error at construction
    (debate_round_out_of_range) — Task 9 does not duplicate it on the normal path."""
    from guanlan_v2.orchestration.spec import _structural_issues

    debate = _debate_cfg(max_rounds=2)
    nodes = _bullbear_nodes(rounds=2)
    nodes.append(_seat_node("n.bull.r3", "w.bull", "deb.bullbear", 3, 1, "bull"))
    # Phase 1 rejects such a draft at construction (its model_validator wraps the
    # PlanStructureError into a pydantic ValidationError) ...
    with pytest.raises(ValidationError):
        _draft(nodes + [_judge_node()], sinks=("n.judge",), debates=(debate,))
    # ... and the exact Phase-1 code is ``debate_round_out_of_range`` (Task 9 never
    #     re-implements it — its own ``debate_rounds_exceed_max`` is the defensive
    #     mirror reached only via model_construct, tested separately below).
    good = _draft(_bullbear_nodes(rounds=2) + [_judge_node()], sinks=("n.judge",), debates=(debate,))
    over = good.model_construct(**{**good.__dict__, "nodes": tuple(good.nodes) + (
        PlanNode.model_construct(
            id="n.bull.r3", worker_id="w.bull", writes_slot="seat.n.bull.r3",
            debate_id="deb.bullbear", round_role="bull", debate_round=3, debate_turn=1,
            params={}, dependencies=(), gate_ids=(), condition_ref=None, auxiliary=True,
            timeout_sec=300, max_attempts=1, token_reservation=0, schema_version="1"),)})
    assert "debate_round_out_of_range" in {i.code for i in _structural_issues(over)}


def test_expansion_rounds_exceed_max_defensive_via_model_construct():
    """The Task-9 defensive ``debate_rounds_exceed_max`` mirror fires only when Phase 1
    is bypassed (model_construct) — a round beyond max_rounds."""
    debate = _debate_cfg(max_rounds=2)
    good = _draft(_bullbear_nodes(rounds=2) + [_judge_node()], sinks=("n.judge",), debates=(debate,))
    over = good.model_construct(**{**good.__dict__, "nodes": tuple(good.nodes) + (
        PlanNode.model_construct(
            id="n.bull.r3", worker_id="w.bull", writes_slot="debate.slot",
            debate_id="deb.bullbear", round_role="bull", debate_round=3, debate_turn=1,
            params={}, dependencies=(), gate_ids=(), condition_ref=None, auxiliary=False,
            timeout_sec=300, max_attempts=1, token_reservation=0, schema_version="1"),)})
    issues = verify_debate_expansion(over, debate)
    assert "debate_rounds_exceed_max" in _codes(issues)


# =========================================================================== #
# Row 6 — count_debate_invocations + budget gate                                #
# =========================================================================== #
def test_count_debate_invocations_2x2_plus_3x2_is_10():
    d1 = _debate_cfg(debate_id="deb.bb", seats=("bull", "bear"),
                     turn_order=("bull", "bear"), max_rounds=2, judge="n.judge1")
    d2 = DebateCfg(id="deb.risk", seats=("ra", "rb", "rc"),
                   turn_order=("ra", "rb", "rc"), max_rounds=2, judge_node_id="n.judge2")
    nodes = _bullbear_nodes(debate_id="deb.bb", rounds=2)
    for r in (1, 2):
        for turn, role in ((1, "ra"), (2, "rb"), (3, "rc")):
            nodes.append(_seat_node(f"n.{role}.r{r}", f"w.{role}", "deb.risk", r, turn, role))
    nodes += [_judge_node("n.judge1", "w.judge", "judge1.slot"),
              _judge_node("n.judge2", "w.judge2", "judge2.slot")]
    draft = _draft(nodes, sinks=("n.judge1", "n.judge2"), debates=(d1, d2))
    assert count_debate_invocations(draft) == 10


def test_analyze_debates_under_requested_budget_rejected():
    """Row 6: budget_request_llm_invocations < expanded invocation count is rejected
    BEFORE reservation."""
    runtime, snap, _red = build_debate_catalog(llm_ids=("bull", "bear", "judge"))
    debate = _debate_cfg()
    nodes = _bullbear_nodes(rounds=2) + [_judge_node()]
    # 4 debate invocations + 1 non-debate LLM judge = 5 needed; request only 3.
    draft = _draft(nodes, sinks=("n.judge",), debates=(debate,), budget_llm=3, snapshot=snap)
    issues = analyze_debates(draft, catalog=runtime, profile=STATIC_RUNTIME_PROFILE_V2)
    assert "debate_budget_under_requested" in _codes(issues)


def test_analyze_debates_sufficient_budget_is_clean():
    runtime, snap, _red = build_debate_catalog(llm_ids=("bull", "bear", "judge"))
    debate = _debate_cfg()
    nodes = _bullbear_nodes(rounds=2) + [_judge_node()]
    draft = _draft(nodes, sinks=("n.judge",), debates=(debate,), budget_llm=5, snapshot=snap)
    assert analyze_debates(draft, catalog=runtime, profile=STATIC_RUNTIME_PROFILE_V2) == ()


# =========================================================================== #
# Row 7 — judge is a seat node of the same debate                               #
# =========================================================================== #
def test_analyze_debates_judge_is_seat_rejected():
    runtime, snap, _red = build_debate_catalog(llm_ids=("bull", "bear", "judge"))
    # judge points at a seat node of the same debate (there is no distinct judge node).
    debate = _debate_cfg(judge="n.bull.r1")
    nodes = _bullbear_nodes(rounds=2) + [_judge_node("n.sink", "w.judge", "sink.slot")]
    draft = _draft(nodes, sinks=("n.sink",), debates=(debate,), budget_llm=10, snapshot=snap)
    issues = analyze_debates(draft, catalog=runtime, profile=STATIC_RUNTIME_PROFILE_V2)
    assert "debate_judge_is_seat" in _codes(issues)


# =========================================================================== #
# Row 8 — deterministic worker as a seat                                        #
# =========================================================================== #
def test_analyze_debates_deterministic_seat_rejected():
    runtime, snap, _red = build_debate_catalog(llm_ids=("bull", "judge"), det_ids=("bear",))
    debate = _debate_cfg()
    nodes = _bullbear_nodes(rounds=2) + [_judge_node()]
    draft = _draft(nodes, sinks=("n.judge",), debates=(debate,), budget_llm=10, snapshot=snap)
    issues = analyze_debates(draft, catalog=runtime, profile=STATIC_RUNTIME_PROFILE_V2)
    assert "debate_seat_not_llm" in _codes(issues)


def test_analyze_debates_over_two_rounds_rejected():
    """Invariant 5: max_rounds > 2 is rejected by the v2 analyzer."""
    assert DEBATE_MAX_ROUNDS == 2
    runtime, snap, _red = build_debate_catalog(llm_ids=("bull", "bear", "judge"))
    debate = _debate_cfg(max_rounds=3)
    nodes = _bullbear_nodes(rounds=2) + [_judge_node()]
    draft = _draft(nodes, sinks=("n.judge",), debates=(debate,), budget_llm=10, snapshot=snap)
    issues = analyze_debates(draft, catalog=runtime, profile=STATIC_RUNTIME_PROFILE_V2)
    assert "debate_rounds_exceed_profile_max" in _codes(issues)


# =========================================================================== #
# Row 9 — deterministic Plan-order fold (shuffle-invariant)                      #
# =========================================================================== #
def test_fold_builds_ordered_transcript():
    debate = _debate_cfg()
    nodes = _bullbear_nodes(rounds=2)
    committed = {n.id: _artifact(n.id, n.round_role) for n in nodes}
    t = fold_debate_messages(debate=debate, nodes=tuple(nodes), committed=committed)
    assert isinstance(t, DebateTranscript)
    assert [(m.round, m.turn, m.role) for m in t.messages] == [
        (1, 1, "bull"), (1, 2, "bear"), (2, 1, "bull"), (2, 2, "bear")]


def test_fold_is_shuffle_invariant_over_20_seeds_via_reducer_seam():
    """Row 9: the ``debate.transcript_reducer`` handler over the Task-8
    ``fold_reducer_producers`` seam yields a byte-identical transcript under 20
    shuffled completion orders."""
    from guanlan_v2.orchestration.pool import fold_reducer_producers

    debate = _debate_cfg()
    nodes = _bullbear_nodes(rounds=2)
    artifacts = [_artifact(n.id, n.round_role) for n in nodes]
    handler = debate_transcript_reducer_handler(debate=debate, nodes=tuple(nodes))
    baseline = fold_reducer_producers(artifacts, handler=handler).semantic_digest()
    for seed in range(20):
        shuffled = list(artifacts)
        random.Random(seed).shuffle(shuffled)
        got = fold_reducer_producers(shuffled, handler=handler).semantic_digest()
        assert got == baseline, seed


def test_reducer_id_is_the_frozen_logical_id():
    assert DEBATE_TRANSCRIPT_REDUCER_ID == "debate.transcript_reducer"


# =========================================================================== #
# Rows 10/11 — persist-then-publish via the REAL Phase-2 EventStore              #
# =========================================================================== #
def _stores():
    reg = _registry()
    resolver = SchemaRegistryResolver()
    resolver.register(reg)
    stores = RuntimeStores(resolver=resolver, clock=AdvancingClock())
    return stores, reg.registry_digest


def test_publish_debate_message_persists_visible_event():
    stores, rdig = _stores()
    node = _seat_node("n.bull.r1", "w.bull", "deb.bullbear", 1, 1, "bull")
    art = _artifact(node.id, "bull")
    ev = publish_debate_message(stores, registry_digest=rdig, run_id="run-1", plan_digest=DIG,
                                debate_id="deb.bullbear", node=node, artifact=art)
    assert ev.event_type is EventType.DEBATE_MESSAGE_PUBLISHED
    assert ev.partition == "main" and ev.visible_seq is not None


def test_duplicate_message_same_round_turn_is_idempotency_conflict():
    """Row 10: a second message for the same (debate_id, round, turn, role) with
    DIFFERENT content is an IdempotencyConflict, never an update."""
    stores, rdig = _stores()
    node = _seat_node("n.bull.r1", "w.bull", "deb.bullbear", 1, 1, "bull")
    a1 = _artifact(node.id, "bull", hour=1)
    publish_debate_message(stores, registry_digest=rdig, run_id="run-1", plan_digest=DIG,
                           debate_id="deb.bullbear", node=node, artifact=a1)
    # identical re-publish is idempotent (same content) ...
    publish_debate_message(stores, registry_digest=rdig, run_id="run-1", plan_digest=DIG,
                           debate_id="deb.bullbear", node=node, artifact=a1)
    # ... but a different artifact under the same (round,turn,role) conflicts.
    a2 = _artifact("n.bull.r1.v2", "bull", hour=5)
    a2 = a2  # distinct artifact_id + created_at
    node2 = _seat_node("n.bull.r1", "w.bull", "deb.bullbear", 1, 1, "bull")
    with pytest.raises(IdempotencyConflict):
        publish_debate_message(stores, registry_digest=rdig, run_id="run-1", plan_digest=DIG,
                               debate_id="deb.bullbear", node=node2, artifact=a2)


def test_replay_equals_pool_fold():
    """Row 11: replaying the DebateMessagePublished visible events reconstructs the
    same transcript as the deterministic pool fold."""
    stores, rdig = _stores()
    debate = _debate_cfg()
    nodes = _bullbear_nodes(rounds=2)
    committed = {n.id: _artifact(n.id, n.round_role, hour=1 + i) for i, n in enumerate(nodes)}
    # publish in a SHUFFLED order to prove replay folds by Plan order, not arrival.
    order = list(nodes)
    random.Random(7).shuffle(order)
    for n in order:
        publish_debate_message(stores, registry_digest=rdig, run_id="run-1", plan_digest=DIG,
                               debate_id=debate.id, node=n, artifact=committed[n.id])
    replayed = replay_debate_transcript(stores, run_id="run-1", debate=debate, max_rounds=2)
    folded = fold_debate_messages(debate=debate, nodes=tuple(nodes), committed=committed)
    assert replayed.semantic_digest() == folded.semantic_digest()
    assert [(m.round, m.turn) for m in replayed.messages] == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_debate_message_key_is_domain_tagged_and_stable():
    k1 = debate_message_key(run_id="run-1", plan_digest=DIG, debate_id="deb.bullbear",
                            round_=1, turn=1, role="bull")
    k2 = debate_message_key(run_id="run-1", plan_digest=DIG, debate_id="deb.bullbear",
                            round_=1, turn=1, role="bull")
    k3 = debate_message_key(run_id="run-1", plan_digest=DIG, debate_id="deb.bullbear",
                            round_=1, turn=2, role="bear")
    assert k1 == k2 and k1 != k3
    assert len(k1) == 64


# =========================================================================== #
# Row 12 — EventType additivity + visibility rule + schema ref                  #
# =========================================================================== #
def test_debate_event_member_is_additive_and_named():
    assert EventType.DEBATE_MESSAGE_PUBLISHED.value == "DebateMessagePublished"
    values = [m.value for m in EventType]
    # the pre-existing 25 members are the byte-frozen prefix; debate is appended last.
    assert values[-1] == "DebateMessagePublished"
    assert len(EventType) == 26
    # every pre-existing value is unchanged (nothing renumbered / revalued).
    for old in ("RunRequested", "TrialReserved", "ShadowTargetApplied", "LayerCommitted"):
        assert old in values


def test_debate_message_schema_ref_is_v1():
    assert DEBATE_MESSAGE_SCHEMA_REF == SchemaRef(name="DebateMessage", version="1")
