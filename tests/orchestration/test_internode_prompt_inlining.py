# -*- coding: utf-8 -*-
"""裁决 (2026-07-31) — the inter-node prompt seams carry CONTENT, not digests.

The first COMPLETED full-trunk deep run (``deep-73a70d27cb1651a0``, 600519)
executed every dependency edge correctly and still produced six monologues:
the prompt assembly rendered every upstream artifact as a name+digest
REFERENCE, so pm literally answered "The provided prompt includes only digests
for context_snapshot and sentiment blocks, with no actual data" and trader
defaulted to all cash one edge downstream of pm's committed PortfolioDecision.

This file locks the ruling's three lines at the unit seams (the e2e
load-bearing assertions live in ``test_pipeline_live_decide.py``,
``TestInterNodeInlining``):

* an upstream artifact COMMITTED BY THIS RUN's own runner (self-computed,
  digest-attested, resolved through the run's ArtifactPool) is TRUSTED and is
  INLINED into the downstream node's prompt, in a channelled section carrying
  the slot name (``inject_as``), the committed-artifact digest and the content;
* UNTRUSTED content stays a digest reference — byte-for-byte the reviewed
  six-key ``data_inputs`` shape, proven unchanged beside inlined trusted
  content;
* an ABSENT upstream (a dropped DEGRADE edge / failed aux node) is STATED as
  absent — an empty question must never masquerade as a complete one (Ruling
  1's own wording).

The mechanism is implemented ONCE in ``worker.py``
(:func:`~guanlan_v2.orchestration.worker.resolve_trusted_artifact_blocks` +
:func:`~guanlan_v2.orchestration.worker.trusted_upstream_channel_section`, the
``output_schema_section`` precedent) and shared by ``StaticPromptAssembler``,
the deep lane's ``SubjectPromptAssembler`` and Lane-0's
``Lane0PromptAssembler`` — assemblers must never drift on what a model is
shown.

Run from repo root:
``python -m pytest tests/orchestration/test_internode_prompt_inlining.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from guanlan_v2.orchestration import bootstrap as B
from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.catalog import InputBinding
from guanlan_v2.orchestration.catalog_runtime import build_text_material
from guanlan_v2.orchestration.context import InputArtifactBinding, InputSnapshot
from guanlan_v2.orchestration.digest import canonical_json, content_digest
from guanlan_v2.orchestration.enums import DependencyPolicy, NodeStatus, PortfolioRating
from guanlan_v2.orchestration.memory.experience import EXPERIENCE_SELECTION_SCHEMA_REF
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import (
    NamedEvidenceDigest,
    PromptUntrustedBlockRef,
)
from guanlan_v2.orchestration.schemas import (
    ArtifactRef,
    Confidence,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
)
from guanlan_v2.orchestration.spec import Dependency, PlanNode
from guanlan_v2.orchestration.pipeline.deep_decide import RUN_SUBJECT_SCHEMA_REF
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline.live_decide import SubjectPromptAssembler

from tests.orchestration.test_worker import FakeModelGateway, build_env
from tests.orchestration.test_runtime_support import SR_OUT, OtherOut

UTC = timezone.utc

SR_PLAN = SchemaRef(name="ResearchPlan", version="1")
SR_SENTI = SchemaRef(name="SentimentReport", version="1")


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #
def _plan_payload() -> ResearchPlan:
    return ResearchPlan(
        recommendation=PortfolioRating.HOLD,
        rationale="Hold pending confirmation.",
        strategic_actions=("Monitor breadth",))


def _senti_payload() -> SentimentReport:
    return SentimentReport(
        overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
        confidence=Confidence.MEDIUM, narrative="Balanced flow, mild caution.")


def _artifact(payload, schema_ref: SchemaRef):
    """A committed-Artifact stand-in with the three attributes the resolver reads."""
    return SimpleNamespace(
        content_digest=content_digest({"artifact-of": content_digest(payload)}),
        payload=payload, payload_schema_ref=schema_ref)


class _Pool:
    """The ArtifactPool surface the resolver is allowed to touch."""

    def __init__(self, artifacts: dict):
        self._arts = dict(artifacts)
        self.asked: list[tuple[str, str]] = []

    def committed_output(self, node_id: str, key: str):
        self.asked.append((node_id, key))
        return self._arts.get((node_id, key))


def _dep(upstream: str, slot: str, inject_as: str,
         policy: DependencyPolicy = DependencyPolicy.BLOCK) -> Dependency:
    accept = (frozenset({NodeStatus.COMPLETED})
              if policy is DependencyPolicy.BLOCK
              else frozenset({NodeStatus.COMPLETED, NodeStatus.DEGRADED}))
    return Dependency(upstream_node_id=upstream, artifact_slot=slot,
                      inject_as=inject_as, policy=policy, accept_statuses=accept)


def _pm_shaped():
    """A pm-shaped node/worker pair: two BLOCK-fed inputs, declaration order."""
    node = PlanNode(
        id="pm", worker_id="dec.pm", writes_slot="slot.portfolio_decision",
        dependencies=(_dep("research-mgr", "slot.research_plan", "research_plan"),
                      _dep("sentiment", "slot.sentiment", "sentiment")))
    worker = SimpleNamespace(inputs=(
        InputBinding(name="research_plan", schema_ref=SR_PLAN, cardinality="one"),
        InputBinding(name="sentiment", schema_ref=SR_SENTI, cardinality="one"),
    ))
    return node, worker


def _ref(payload_art, *, producer: str, slot: str, schema_ref: SchemaRef) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=f"art-{producer}", schema_ref=schema_ref,
        producer_node_id=producer, slot=slot, output_key="primary",
        content_digest=payload_art.content_digest)


def _snapshot(*bindings) -> SimpleNamespace:
    """The one InputSnapshot attribute the resolver reads, over REAL bindings."""
    return SimpleNamespace(artifact_inputs=tuple(bindings))


def _pm_fixture():
    node, worker = _pm_shaped()
    plan_art = _artifact(_plan_payload(), SR_PLAN)
    senti_art = _artifact(_senti_payload(), SR_SENTI)
    plan_ref = _ref(plan_art, producer="research-mgr",
                    slot="slot.research_plan", schema_ref=SR_PLAN)
    senti_ref = _ref(senti_art, producer="sentiment",
                     slot="slot.sentiment", schema_ref=SR_SENTI)
    snapshot = _snapshot(
        InputArtifactBinding(input_name="research_plan", cardinality="one",
                             artifact_refs=(plan_ref,)),
        InputArtifactBinding(input_name="sentiment", cardinality="one",
                             artifact_refs=(senti_ref,)))
    pool = _Pool({("research-mgr", "primary"): plan_art,
                  ("sentiment", "primary"): senti_art})
    return SimpleNamespace(node=node, worker=worker, snapshot=snapshot, pool=pool,
                           plan_art=plan_art, senti_art=senti_art,
                           plan_ref=plan_ref, senti_ref=senti_ref)


def _material(id_: str, kind: str, text: str):
    _r, mat = build_text_material(
        id=id_, version="1", kind=kind, raw=text.encode("utf-8"))
    return mat


def _untrusted_block(ordinal: int = 1) -> PromptUntrustedBlockRef:
    digest = content_digest({"experience": "untrusted retrieval payload"})
    return PromptUntrustedBlockRef.build(
        ordinal=ordinal,
        payload_ref=TypedPayloadRef(
            schema_ref=EXPERIENCE_SELECTION_SCHEMA_REF,
            payload_ref=PayloadRef(namespace="main", object_id="payload-9",
                                   content_digest=digest)),
        media_type="application/json", rendered_length=1234)


def _assemble(assembler, *, trusted_artifacts=(), blocks=(), trusted=()):
    trusted = tuple(trusted) + (
        NamedEvidenceDigest(name="context_snapshot", digest="cd" * 32),)
    return assembler.assemble(
        plan_digest="ab" * 32, node_id="pm", worker_id="dec.pm",
        system_prompt=_material("prompt.pm", "prompt", "SYSTEM TEXT"),
        skills=(_material("skill.dec.pm", "skill", "SKILL TEXT"),),
        guardrails=(_material("guard.provenance", "guardrail", "GUARDRAIL TEXT"),),
        trusted_input_digests=trusted, untrusted_blocks=tuple(blocks),
        trusted_artifacts=tuple(trusted_artifacts))


def _channel(assembled) -> dict:
    return json.loads(assembled.canonical_request_bytes.decode("utf-8"))


def _subject_assembler() -> SubjectPromptAssembler:
    subject = RunSubject(code="600519", as_of=datetime(2026, 7, 31, tzinfo=UTC))
    ref = TypedPayloadRef(
        schema_ref=RUN_SUBJECT_SCHEMA_REF,
        payload_ref=PayloadRef(namespace="main", object_id="subj-1",
                               content_digest=content_digest(subject)))
    return SubjectPromptAssembler(subject=subject, subject_ref=ref)


# =========================================================================== #
# 1. the resolver — trusted means resolved-through-the-pool, digest-attested   #
# =========================================================================== #
class TestResolver:
    def test_the_committed_payload_content_is_inlined(self):
        f = _pm_fixture()
        blocks = W.resolve_trusted_artifact_blocks(
            node=f.node, worker=f.worker, input_snapshot=f.snapshot, pool=f.pool)
        assert [b.inject_as for b in blocks] == ["research_plan", "sentiment"]
        plan_b, senti_b = blocks
        assert plan_b.status == "present"
        assert plan_b.content == canonical_json(_plan_payload())
        assert "Hold pending confirmation." in plan_b.content
        assert plan_b.artifact_digest == f.plan_ref.content_digest
        assert plan_b.schema_key == "ResearchPlan@1"
        assert plan_b.producer_node_id == "research-mgr"
        assert senti_b.status == "present"
        assert "Balanced flow, mild caution." in senti_b.content
        # resolved through the pool by the ref's own (producer, output_key).
        assert set(f.pool.asked) == {("research-mgr", "primary"),
                                     ("sentiment", "primary")}

    def test_a_pool_that_cannot_produce_the_bound_artifact_refuses(self):
        f = _pm_fixture()
        pool = _Pool({("sentiment", "primary"): f.senti_art})  # research plan gone
        with pytest.raises(W.WorkerExecutionError, match="research_plan"):
            W.resolve_trusted_artifact_blocks(
                node=f.node, worker=f.worker, input_snapshot=f.snapshot, pool=pool)

    def test_a_digest_mismatch_refuses_rather_than_misattributing(self):
        f = _pm_fixture()
        impostor = _artifact(ResearchPlan(
            recommendation=PortfolioRating.SELL, rationale="not the bound one",
            strategic_actions=()), SR_PLAN)
        pool = _Pool({("research-mgr", "primary"): impostor,
                      ("sentiment", "primary"): f.senti_art})
        with pytest.raises(W.WorkerExecutionError, match="not the one"):
            W.resolve_trusted_artifact_blocks(
                node=f.node, worker=f.worker, input_snapshot=f.snapshot, pool=pool)

    def test_bound_content_with_no_pool_refuses_rather_than_reverting_to_digests(self):
        """The reference-only shape is the defect under repair — a caller that
        cannot reach the committed content may not silently reproduce it."""
        f = _pm_fixture()
        with pytest.raises(W.WorkerExecutionError, match="pool"):
            W.resolve_trusted_artifact_blocks(
                node=f.node, worker=f.worker, input_snapshot=f.snapshot, pool=None)

    def test_a_fed_but_omitted_input_is_stated_absent(self):
        f = _pm_fixture()
        node = f.node.model_copy(update={"dependencies": (
            _dep("research-mgr", "slot.research_plan", "research_plan",
                 policy=DependencyPolicy.DEGRADE),
            _dep("sentiment", "slot.sentiment", "sentiment"),)})
        snapshot = _snapshot(  # the DEGRADE edge dropped: no research_plan binding
            InputArtifactBinding(input_name="sentiment", cardinality="one",
                                 artifact_refs=(f.senti_ref,)))
        blocks = W.resolve_trusted_artifact_blocks(
            node=node, worker=f.worker, input_snapshot=snapshot, pool=f.pool)
        assert [(b.inject_as, b.status) for b in blocks] == [
            ("research_plan", "absent"), ("sentiment", "present")]
        absent = blocks[0]
        assert absent.content is None
        assert absent.artifact_digest is None
        # absence needs no pool read for the absent slot.
        assert ("research-mgr", "primary") not in f.pool.asked

    def test_a_node_with_no_fed_inputs_yields_nothing(self):
        node = PlanNode(id="sentiment", worker_id="text.sentiment",
                        writes_slot="slot.sentiment")
        worker = SimpleNamespace(inputs=())
        blocks = W.resolve_trusted_artifact_blocks(
            node=node, worker=worker, input_snapshot=_snapshot(), pool=None)
        assert blocks == ()
        assert W.trusted_upstream_channel_section(blocks) is None

    def test_an_input_absent_by_design_is_not_stated(self):
        """A declared worker input the Plan never feeds is absent BY DESIGN
        (the ``_absent_input_degradations`` line): stating it would turn every
        optional input into noise."""
        f = _pm_fixture()
        node = f.node.model_copy(update={"dependencies": (
            _dep("sentiment", "slot.sentiment", "sentiment"),)})
        snapshot = _snapshot(
            InputArtifactBinding(input_name="sentiment", cardinality="one",
                                 artifact_refs=(f.senti_ref,)))
        blocks = W.resolve_trusted_artifact_blocks(
            node=node, worker=f.worker, input_snapshot=snapshot, pool=f.pool)
        assert [b.inject_as for b in blocks] == ["sentiment"]


# =========================================================================== #
# 2. the shared channel section — one renderer, every assembler                #
# =========================================================================== #
class TestSharedSection:
    def _blocks(self):
        f = _pm_fixture()
        return f, W.resolve_trusted_artifact_blocks(
            node=f.node, worker=f.worker, input_snapshot=f.snapshot, pool=f.pool)

    def test_static_assembler_inlines_trusted_content(self):
        f, blocks = self._blocks()
        assembled = _assemble(W.StaticPromptAssembler(), trusted_artifacts=blocks)
        blob = assembled.canonical_request_bytes.decode("utf-8")
        assert "Hold pending confirmation." in blob
        assert "Balanced flow, mild caution." in blob
        section = _channel(assembled)[W.TRUSTED_UPSTREAM_SECTION]
        by_name = {b["inject_as"]: b for b in section["blocks"]}
        assert by_name["research_plan"]["artifact_digest"] == f.plan_ref.content_digest
        assert by_name["research_plan"]["content"] == canonical_json(_plan_payload())

    def test_subject_assembler_carries_the_identical_section(self):
        """The two assemblers share ONE renderer — they cannot drift."""
        _, blocks = self._blocks()
        static_ch = _channel(_assemble(W.StaticPromptAssembler(),
                                       trusted_artifacts=blocks))
        subject_ch = _channel(_assemble(_subject_assembler(),
                                        trusted_artifacts=blocks))
        assert (static_ch[W.TRUSTED_UPSTREAM_SECTION]
                == subject_ch[W.TRUSTED_UPSTREAM_SECTION])

    def test_an_untrusted_block_stays_a_reference_beside_inlined_content(self):
        """The trusted/untrusted line is the whole point — it must not move."""
        _, blocks = self._blocks()
        block = _untrusted_block()
        for assembler in (W.StaticPromptAssembler(), _subject_assembler()):
            assembled = _assemble(assembler, trusted_artifacts=blocks,
                                  blocks=(block,))
            channel = _channel(assembled)
            blob = assembled.canonical_request_bytes.decode("utf-8")
            assert channel["data_inputs"] == [{
                "ordinal": 1,
                "schema": EXPERIENCE_SELECTION_SCHEMA_REF.key,
                "namespace": "main",
                "content_digest": block.payload_ref.payload_ref.content_digest,
                "media_type": "application/json",
                "length": 1234,
            }]
            assert "untrusted retrieval payload" not in blob
            assert "Hold pending confirmation." in blob  # trusted still inlined

    def test_no_blocks_means_a_byte_identical_channel(self):
        """A node with nothing to inline keeps the reviewed shape bit-for-bit
        (and ``run_planner``, which never passes the kwarg, is untouched)."""
        for assembler in (W.StaticPromptAssembler(), _subject_assembler()):
            with_kwarg = _assemble(assembler, trusted_artifacts=())
            without = assembler.assemble(
                plan_digest="ab" * 32, node_id="pm", worker_id="dec.pm",
                system_prompt=_material("prompt.pm", "prompt", "SYSTEM TEXT"),
                skills=(_material("skill.dec.pm", "skill", "SKILL TEXT"),),
                guardrails=(_material("guard.provenance", "guardrail",
                                      "GUARDRAIL TEXT"),),
                trusted_input_digests=(
                    NamedEvidenceDigest(name="context_snapshot",
                                        digest="cd" * 32),),
                untrusted_blocks=())
            assert (with_kwarg.canonical_request_bytes
                    == without.canonical_request_bytes)
            assert W.TRUSTED_UPSTREAM_SECTION not in _channel(with_kwarg)

    def test_an_absent_block_is_stated_in_the_rendered_channel(self):
        absent = W.TrustedArtifactBlock(inject_as="technical", status="absent")
        section = W.trusted_upstream_channel_section((absent,))
        [entry] = section["blocks"]
        assert entry["status"] == "absent"
        assert entry["content"] == W.TRUSTED_UPSTREAM_ABSENT_TEXT
        assert entry["artifact_digest"] is None
        assert "never invent" in W.TRUSTED_UPSTREAM_ABSENT_TEXT


# =========================================================================== #
# 3. Lane-0 — the factor slot stays with its ruled R1 section; anything else   #
#    would flow through the same shared mechanism                              #
# =========================================================================== #
class TestLane0Assembler:
    def _lane0(self):
        class _EmptyPool:
            def committed_output(self, node_id, key):
                return None

        return B.Lane0PromptAssembler(pool=_EmptyPool(), registry=None)

    def _assemble(self, assembler, trusted_artifacts):
        return assembler.assemble(
            plan_digest="ab" * 32, node_id="lane0.regime", worker_id="market.regime",
            system_prompt=_material("lane0.regime.prompt", "prompt", "SYSTEM TEXT"),
            skills=(), guardrails=(),
            trusted_input_digests=(
                NamedEvidenceDigest(name="context_snapshot", digest="cd" * 32),),
            untrusted_blocks=(), trusted_artifacts=tuple(trusted_artifacts))

    def test_the_factor_slot_stays_with_the_ruled_r1_section(self):
        """Lane-0's ONLY inter-node artifact is the factor report and R1
        already inlines it (coverage stats, citable-reading notes); the generic
        section must not double-carry it — so for every legal Lane-0 plan the
        generic section is absent and Lane-0 request bytes are unchanged."""
        factor_only = (W.TrustedArtifactBlock(
            inject_as="market_factor_report", status="present",
            schema_key="MarketFactorReport@1", artifact_digest="ef" * 32,
            producer_node_id="lane0.factor", content="{}"),)
        assembled = self._assemble(self._lane0(), factor_only)
        channel = _channel(assembled)
        assert W.TRUSTED_UPSTREAM_SECTION not in channel
        assert B.LANE0_FACTOR_REPORT_SECTION in channel  # the R1 section carries it

    def test_a_non_factor_upstream_would_flow_through_the_shared_section(self):
        """If Lane-0 ever grows another inter-node edge, the shared mechanism
        carries it automatically — no second reference-only gap can open."""
        extra = (W.TrustedArtifactBlock(
            inject_as="regime_report", status="present",
            schema_key="RegimeReport@1", artifact_digest="ab" * 32,
            producer_node_id="lane0.regime",
            content='{"narrative":"regime content travels"}'),)
        channel = _channel(self._assemble(self._lane0(), extra))
        section = channel[W.TRUSTED_UPSTREAM_SECTION]
        assert [b["inject_as"] for b in section["blocks"]] == ["regime_report"]
        assert "regime content travels" in json.dumps(section, ensure_ascii=False)


# =========================================================================== #
# 4. the executor threads the pool-resolved blocks into the REAL assembler     #
# =========================================================================== #
class _CaptureGateway(FakeModelGateway):
    def __init__(self, stores, **kw):
        super().__init__(stores, **kw)
        self.requests: list = []

    def invoke(self, request, *, prompt_assembly_ref):
        self.requests.append(request)
        return super().invoke(request, prompt_assembly_ref=prompt_assembly_ref)


class TestExecutorThreading:
    def test_the_executor_states_an_absent_fed_input_in_the_prompt(self):
        """A weakened-away DEGRADE input is stated absent in the model request
        the executor actually authorizes — through the real ``execute_node`` +
        the real ``StaticPromptAssembler``."""
        e = build_env(one_input="optional_degrade")
        gw = _CaptureGateway(e.stores)
        node_run, _ = e.run(model_gateway=gw)
        # the omitted DEGRADE input already degrades the honest result status;
        # the ruling adds: the PROMPT must state the absence too.
        assert node_run.status is NodeStatus.DEGRADED
        [request] = gw.requests
        channel = json.loads(request.canonical_request_bytes.decode("utf-8"))
        section = channel[W.TRUSTED_UPSTREAM_SECTION]
        [entry] = section["blocks"]
        assert entry["inject_as"] == "opt_in"
        assert entry["status"] == "absent"
        assert entry["content"] == W.TRUSTED_UPSTREAM_ABSENT_TEXT

    def test_the_executor_inlines_a_bound_input_through_the_pool(self):
        """The present half through the same executor seam: the bound upstream
        artifact's content reaches the authorized request bytes."""
        e = build_env(one_input="optional_degrade")
        payload = OtherOut(note="upstream-fact-travels")
        art = SimpleNamespace(
            content_digest=content_digest({"artifact-of": content_digest(payload)}),
            payload=payload, payload_schema_ref=SR_OUT)
        ref = ArtifactRef(
            artifact_id="art-up", schema_ref=SR_OUT, producer_node_id="n0",
            slot="s0", output_key="primary", content_digest=art.content_digest)
        snap = e.input_snapshot
        e.input_snapshot = InputSnapshot.build(
            snapshot_id=snap.snapshot_id, run_id=snap.run_id, plan_id=snap.plan_id,
            plan_digest=snap.plan_digest, node_id=snap.node_id,
            layer_index=snap.layer_index, attempt=snap.attempt,
            context_snapshot_ref=snap.context_snapshot_ref,
            artifact_inputs=(InputArtifactBinding(
                input_name="opt_in", cardinality="one", artifact_refs=(ref,)),),
            data_result_refs=(), memory_record_refs=(), readiness="ready",
            built_at=snap.built_at)
        pool = _Pool({("n0", "primary"): art})
        gw = _CaptureGateway(e.stores)
        node_run, _ = e.run(model_gateway=gw, artifact_pool=pool)
        assert node_run.status is NodeStatus.COMPLETED
        [request] = gw.requests
        blob = request.canonical_request_bytes.decode("utf-8")
        assert "upstream-fact-travels" in blob
        channel = json.loads(blob)
        [entry] = channel[W.TRUSTED_UPSTREAM_SECTION]["blocks"]
        assert entry["inject_as"] == "opt_in"
        assert entry["status"] == "present"
        assert entry["artifact_digest"] == art.content_digest

    def test_a_bound_input_the_pool_cannot_produce_is_prompt_assembly_failed(self):
        """The executor converges the resolver's refusals exactly like Lane-0's:
        ``prompt_assembly_failed`` (INCOMPLETE), never a misattributed inline."""
        e = build_env(one_input="optional_degrade")
        payload = OtherOut(note="bound-but-unreachable")
        art = SimpleNamespace(
            content_digest=content_digest({"artifact-of": content_digest(payload)}),
            payload=payload, payload_schema_ref=SR_OUT)
        ref = ArtifactRef(
            artifact_id="art-up", schema_ref=SR_OUT, producer_node_id="n0",
            slot="s0", output_key="primary", content_digest=art.content_digest)
        snap = e.input_snapshot
        e.input_snapshot = InputSnapshot.build(
            snapshot_id=snap.snapshot_id, run_id=snap.run_id, plan_id=snap.plan_id,
            plan_digest=snap.plan_digest, node_id=snap.node_id,
            layer_index=snap.layer_index, attempt=snap.attempt,
            context_snapshot_ref=snap.context_snapshot_ref,
            artifact_inputs=(InputArtifactBinding(
                input_name="opt_in", cardinality="one", artifact_refs=(ref,)),),
            data_result_refs=(), memory_record_refs=(), readiness="ready",
            built_at=snap.built_at)
        node_run, artifact = e.run(model_gateway=_CaptureGateway(e.stores),
                                   artifact_pool=_Pool({}))
        assert node_run.status is NodeStatus.INCOMPLETE
        assert node_run.reason_code == "prompt_assembly_failed"
        assert artifact is None
