# -*- coding: utf-8 -*-
"""Phase 10 · Task 7 — the lease-gated deep-decide wrapper on the watcher decide_fn seam.

E1/E2b reconciliation transcribed (read at source BEFORE the implementation; the
full table lives in ``.superpowers/sdd/task-7-report.md``):

* **Watcher seams.** ``seats/watcher.py::tick(now=None, decide_fn=None, quote_fn=None,
  decisions_tail_fn=None)`` (:533); ``run_loop`` (:615) is the lifespan entry —
  Task 7's ADDITIVE change threads ``decide_fn`` through ``run_loop`` (default
  ``None`` ⇒ bit-unchanged). ``note_external_llm_use`` (:92) shares the tick's
  24/day counts; ``orchestrated_codes`` (:86) is read-only here.
* **Seats persistence.** ``seats.api._persist_decision(kind, rec)`` (:205) — the
  ONE reviewed append for both the decide row and the advisory conditional-order
  record (the existing ``kind="order"`` path, api.py:2052); only-if-present keys.
* **Escalation (Task 5).** ``build_escalation_context(*, code, as_of, fast_result,
  decisions_tail, quote=None, strat=None, news_titles_fn=None)`` + pure
  ``judge_escalation``; the quote fresh-gate takes booleans (a truthy string like
  ``"false"`` would read as fresh); ``hold_entry`` reaches the bands only through
  the latest matching tail row — the ``position_fn`` port therefore overlays the
  ledger entry onto that row (never a new signal).
* **Materializer (Task 6).** ``materialize_deep_decide_draft(...) -> MaterializedDeepDecide``
  (composite: draft + subject_ref + badges; typed ``SubjectRefused`` /
  ``ContextSnapshotRefused`` / ``ClockRefused``); no latest-committed-snapshot
  accessor exists upstream ⇒ the ``latest_snapshot_fn`` port.
* **Lease (Phase 7).** ``PlanApprovalCoordinator.register_and_try_lease(pending, *,
  idempotency_key, now, candidate_catalog_digest, candidate_registry_digest) ->
  LeaseAdmissionOutcome`` (``lease_admitted`` / ``pending_human``);
  ``admit_after_approval`` (approval.py:1160) is the shared freeze call site.
* **Runner (Task 0b).** ``build_production_plan_runner`` with the ``prompt_assembler``
  injection seam (assembly.py — parameter + ``StaticPromptAssembler()`` default,
  locate by name); the E2b per-run subject-closed assembler occupies it. The
  implemented trusted channel carries name+digest pairs only, so the rendered
  trusted subject block enters via that per-run assembler.

Everything runs on in-memory ``RuntimeStores`` + SCRIPTED gateways over the
trimmed bridge-free ten-worker catalog (the reviewed deep-preset stand-in — the
sealed catalog's data-prefetch grant gap is Task 11's, pinned strict-xfail there
and NOT weakened here). Zero network, zero LLM, zero ``var/`` writes.

Run from repo root:
``python -m pytest tests/orchestration/test_pipeline_live_decide.py -v``
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.lane_catalog as lc
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.admission import PlanAdmissionService
from guanlan_v2.orchestration.approval import PlanApprovalCoordinator
from guanlan_v2.orchestration.catalog import (
    ContentManifestEntry,
    EvidencePolicy,
    SkillManifest,
    build_catalog_snapshot,
)
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogRuntime,
    InMemoryMaterialSource,
    TrustedFactoryRegistry,
)
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.enums import (
    PortfolioRating,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.memory.models import AuthenticatedAdminPrincipal
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_support import STATIC_RUNTIME_PROFILE_V2
from guanlan_v2.orchestration.schemas import (
    Confidence,
    PortfolioDecision,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
)
from guanlan_v2.orchestration.shadow import (
    PortfolioTargetProposal,
    TargetPosition,
    TrancheTrigger,
)
from guanlan_v2.orchestration.skilltree import parse_skill_v1
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    ProductionCatalogRuntime,
    build_production_plan_runner,
    load_phase10_preset_registry,
)
from guanlan_v2.orchestration.pipeline.deep_decide import (
    DEEP_DECIDE_PRESET_ID,
    DEEP_DECIDE_WORKER_IDS,
)

from guanlan_v2.orchestration.pipeline import live_decide
from guanlan_v2.orchestration.pipeline.live_decide import (
    DeepDecideBindings,
    SUBJECT_ASSEMBLER_ID,
    SUBJECT_TRUSTED_INPUT_NAME,
    SubjectPromptAssembler,
    make_orchestrated_decide,
)

UTC = timezone.utc
#: 2026-07-24 10:30 +08:00 — an intraday instant of the SAME +08:00 session the
#: fixture ContextSnapshot is dated (so the recency badge stays quiet).
NOW = datetime(2026, 7, 24, 2, 30, tzinfo=UTC)
PHASE8_REGISTRY_DIGEST = (
    "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089"
)
GOOD_CRED = "good-cred"
_SYM = normalize_symbol("600519.SH")


class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _Verifier:
    def verify(self, credential) -> AuthenticatedAdminPrincipal:
        if credential != GOOD_CRED:
            raise AssertionError(f"unverifiable credential {credential!r}")
        return AuthenticatedAdminPrincipal(actor="human-approver", verified_by="t7")


# =========================================================================== #
# scripted payloads (reviewed shapes, one per deep worker)                     #
# =========================================================================== #
def _pa_report() -> lp.PriceActionFeatureReport:
    keys = lp.PA_FEATURE_SET_KEYS["pa-15key-v1"]
    return lp.PriceActionFeatureReport(
        symbol=_SYM, as_of=NOW, feature_set_version="pa-15key-v1",
        features={k: float(i) for i, k in enumerate(keys)})


def _ms_report() -> lp.MicrostructureReport:
    return lp.MicrostructureReport(
        symbol=_SYM, as_of=NOW, l1_spread_bp=None, bid_ask_imbalance=None,
        break_ratio=None, whale_net_inflow=None,
        degradation=("scripted: all optional feeds stood down",),
        narrative="scripted microstructure (nothing imputed)")


def _proposal(*, tranches: bool) -> PortfolioTargetProposal:
    entry = (TrancheTrigger(price_low=180.0, price_high=190.0, fraction=0.5),
             TrancheTrigger(price_low=170.0, price_high=176.0, fraction=0.5)) if tranches else ()
    return PortfolioTargetProposal(
        positions=(TargetPosition(
            symbol=_SYM, target_weight=0.25, stop_loss_pct=0.08,
            take_profit_pct=0.18, entry_tranches=entry),),
        cash_weight=0.75, rationale="scripted deep-run trader proposal",
        confidence=Confidence.MEDIUM)


def _llm_payload(worker_id: str, *, tranches: bool):
    if worker_id == "pv.technical":
        return lp.TechnicalReport(
            symbol=_SYM, as_of=NOW,
            indicators=(lp.IndicatorReading(name="RSI14", value=55.0),),
            verified_anchor_digest=None, bias="neutral", summary="scripted technical")
    if worker_id == "text.news":
        return lp.NewsDigestReport(as_of=NOW, scope="market", items=(),
                                   coverage_note="scripted: feeds quiet")
    if worker_id == "text.sentiment":
        return SentimentReport(
            overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
            confidence=Confidence.MEDIUM, narrative="Balanced flow, mild caution.")
    if worker_id == "dec.bull":
        return lp.BullCase(symbol=_SYM, as_of=NOW, thesis_bullets=("scripted bull",))
    if worker_id == "dec.bear":
        return lp.BearCase(symbol=_SYM, as_of=NOW, thesis_bullets=("scripted bear",))
    if worker_id == "dec.research_mgr":
        return ResearchPlan(recommendation=PortfolioRating.HOLD,
                            rationale="Hold pending confirmation.",
                            strategic_actions=("Monitor breadth",))
    if worker_id == "dec.pm":
        return PortfolioDecision(rating=PortfolioRating.HOLD,
                                 executive_summary="Advisory hold.",
                                 investment_thesis="Risk/reward balanced.")
    if worker_id == "dec.trader":
        return _proposal(tranches=tranches)
    raise AssertionError(f"unexpected LLM worker {worker_id!r}")


class _DeepGateway:
    """Scripted trusted single-shot gateway: real binding verification, fixed payloads."""

    def __init__(self, payload_reader, *, tranches: bool, fail: frozenset):
        self._reader = payload_reader
        self._tranches = tranches
        self._fail = fail
        self.records: list = []      # verified PromptAssemblyRecords, in order
        self.requests: list = []     # the AssembledModelRequests, in order

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._reader)
        self.records.append(record)
        self.requests.append(request)
        if record.worker_id in self._fail:
            # invalid payload -> the executor's output_schema_invalid honesty fires.
            return W.ModelResult(payload={"schema_version": "1"}, rendered_text="bad",
                                 input_tokens=3, output_tokens=1,
                                 provider="scripted", model="scripted")
        return W.ModelResult(
            payload=_llm_payload(record.worker_id, tranches=self._tranches),
            rendered_text=f"scripted:{record.worker_id}", input_tokens=7,
            output_tokens=5, provider="scripted", model="scripted")


class _GatewayFactory:
    def __init__(self, *, tranches: bool = True, fail: frozenset = frozenset()):
        self.tranches = tranches
        self.fail = fail
        self.gateways: list[_DeepGateway] = []

    def __call__(self, *, payload_reader, catalog_runtime):
        gw = _DeepGateway(payload_reader, tranches=self.tranches, fail=self.fail)
        self.gateways.append(gw)
        return gw


def _pa_handler_factory(worker, resolved):
    def handler(*, node, input_snapshot, contributions, data_result_refs):
        return W.ModelResult(payload=_pa_report(), rendered_text="pa det",
                             input_tokens=0, output_tokens=1)
    return handler


def _ms_handler_factory(worker, resolved):
    def handler(*, node, input_snapshot, contributions, data_result_refs):
        return W.ModelResult(payload=_ms_report(), rendered_text="ms det",
                             input_tokens=0, output_tokens=1)
    return handler


# =========================================================================== #
# the trimmed bridge-free ten-worker catalog (reviewed deep-preset stand-in)    #
# =========================================================================== #
@pytest.fixture(scope="module")
def heavy():
    """Module-scoped heavy fixtures: phase-9 registry + trimmed catalog + presets."""
    registry = chain.build_phase9_registry(PHASE8_REGISTRY_DIGEST)

    text_m = lc.load_text_lane_materials()
    pv_m = lc.load_pv_lane_materials()
    dec_m = lc.load_decision_lane_materials()
    specs: dict = {}
    specs.update({w.id: w for w in lc.build_text_worker_specs(materials=text_m)})
    specs.update({w.id: w for w in lc.build_pv_worker_specs(materials=pv_m)})
    specs.update({w.id: w for w in lc.build_decision_worker_specs(materials=dec_m)})
    workers = []
    for wid in DEEP_DECIDE_WORKER_IDS:
        spec = specs[wid]
        update: dict = {}
        if spec.capability_allowlist:
            update["capability_allowlist"] = ()
        if spec.evidence_policy.tool_calls is ToolCallRequirement.REQUIRED:
            update["evidence_policy"] = EvidencePolicy(
                tool_calls=ToolCallRequirement.OPTIONAL)
        workers.append(spec.model_copy(update=update) if update else spec)

    reducer_material = lc._debate_reducer_material()
    by_key = {}
    for m in list(text_m) + list(pv_m) + list(dec_m) + [reducer_material]:
        by_key[m.ref_key] = m
    materials = tuple(by_key.values())
    content, skills = [], []
    for m in materials:
        if m.kind == "skill":
            parsed = parse_skill_v1(m.raw_utf8.decode("utf-8"))
            skills.append(SkillManifest(
                ref=m.ref, name=parsed.name, summary=parsed.summary,
                perfect_for=parsed.perfect_for, not_ideal_for=parsed.not_ideal_for,
                critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
                source_identity=m.ref.id))
        else:
            content.append(ContentManifestEntry(
                ref=m.ref, kind=m.kind, name="x", description="d",
                source_identity="gl"))
    snapshot = build_catalog_snapshot(
        catalog_version="deep-live-decide-v1", content_manifest=tuple(content),
        skill_manifest=tuple(skills), capability_manifest=(), workers=tuple(workers),
        resolved_material=materials)
    source = InMemoryMaterialSource(
        text={(m.ref.id, m.ref.version): m.raw_utf8 for m in materials},
        capabilities={})
    runtime = CatalogRuntime.build(snapshot, source)
    view = BridgeCatalogView.build(runtime, {})
    presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    record = presets.get(DEEP_DECIDE_PRESET_ID)
    by_id = {w.id: w for w in snapshot.workers}
    return SimpleNamespace(
        registry=registry, snapshot=snapshot, runtime=runtime, view=view,
        presets=presets, record=record,
        pa_ref=by_id["pv.price_action"].execution.handler_ref,
        ms_ref=by_id["pv.microstructure"].execution.handler_ref)


# =========================================================================== #
# per-test environment: stores + journal + spies + factories                    #
# =========================================================================== #
def _build_env(tmp_path, heavy, *, tranches=True, fail=frozenset(), lease=True,
               lease_budget_cap=200, lease_max_admissions=20):
    clock = _FixedClock(NOW)
    resolver = SchemaRegistryResolver()
    resolver.register(heavy.registry)
    rt_digest = heavy.registry.registry_digest
    stores = RuntimeStores(resolver=resolver, clock=clock,
                           allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=NOW), stores=stores,
        registry_digest=rt_digest, built_at=NOW)
    context = mem.context
    ctx_ref = PayloadRef(namespace="main", object_id="ctx-live-1",
                         content_digest=context.content_digest)

    factories = TrustedFactoryRegistry(heavy.runtime)
    factories.register_handler(heavy.pa_ref, _pa_handler_factory)
    factories.register_handler(heavy.ms_ref, _ms_handler_factory)
    bundle = ProductionCatalogRuntime(runtime=heavy.runtime, factories=factories)

    gateway_factory = _GatewayFactory(tranches=tranches, fail=fail)
    journal = tmp_path / "plan_approvals.jsonl"

    env = SimpleNamespace(
        clock=clock, stores=stores, rt_digest=rt_digest, context=context,
        ctx_ref=ctx_ref, bundle=bundle, gateway_factory=gateway_factory,
        journal=journal, declog=[], persisted=[], noted=[], calls=[])

    def persist_spy(kind, rec):
        env.persisted.append((kind, dict(rec)))
        # mirror the real seats log so a later tick's tail sees the appended row
        env.declog.append({"id": f"{kind}_spy", "ts": clock.now().isoformat(),
                           "kind": kind, **rec})

    def note_spy(n):
        env.noted.append(int(n))

    def tail_fn(code):
        env.calls.append("tail")
        return [dict(r) for r in env.declog]

    def admission_factory(*, run_id, request, draft, context, approvals, run_budget):
        return PlanAdmissionService(
            run_id=run_id, requests={request.request_id: request},
            drafts={draft.id: draft}, contexts={context.content_digest: context},
            attestations={}, approvals=approvals, catalog=heavy.runtime,
            bridge_view=heavy.view, phase1_registry=heavy.registry,
            runtime_registry_digest=rt_digest, profile=STATIC_RUNTIME_PROFILE_V2,
            stores=stores, run_budget=run_budget, clock=clock)

    def coordinator_factory(*, admission, approvals_sink):
        return PlanApprovalCoordinator.replay(
            journal, admission=admission, clock=clock, verifier=_Verifier(),
            console_emit=None, approvals_sink=approvals_sink,
            preset_registry=heavy.presets,
            catalog_digest=heavy.snapshot.catalog_digest,
            registry_digest=heavy.registry.registry_digest)

    def plan_runner_factory(*, admission, lane_bindings, run_context_factory,
                            request_id, prompt_assembler):
        return build_production_plan_runner(
            stores=stores, catalog_snapshot=heavy.snapshot, registry=heavy.registry,
            gateway_factory=gateway_factory, admission=admission,
            lane_bindings=lane_bindings, run_context_factory=run_context_factory,
            request_id=request_id, clock=clock, runtime_registry_digest=rt_digest,
            runtime_limit=4, catalog=bundle, prompt_assembler=prompt_assembler)

    env.persist_spy = persist_spy
    env.note_spy = note_spy
    env.tail_fn = tail_fn
    env.admission_factory = admission_factory
    env.coordinator_factory = coordinator_factory
    env.plan_runner_factory = plan_runner_factory

    if lease:
        issuing = PlanApprovalCoordinator(
            journal, admission=SimpleNamespace(), clock=clock,
            verifier=_Verifier(), console_emit=None,
            preset_registry=heavy.presets,
            catalog_digest=heavy.snapshot.catalog_digest,
            registry_digest=heavy.registry.registry_digest)
        issuing.issue_lease(
            purpose="落子 deep-decide standing lease (test)",
            preset_id=DEEP_DECIDE_PRESET_ID,
            preset_record_digest=heavy.record.semantic_digest(),
            catalog_digest=heavy.snapshot.catalog_digest,
            registry_digest=heavy.registry.registry_digest,
            valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(days=1),
            max_admissions=lease_max_admissions,
            budget_cap_llm_invocations=lease_budget_cap,
            actor=GOOD_CRED, reason="reviewed standing deep lease")
    return env


def _bindings(env, heavy, **over) -> DeepDecideBindings:
    kw = dict(
        stores=env.stores, preset_registry=heavy.presets, catalog=heavy.snapshot,
        schema_registry=heavy.registry, coordinator=env.coordinator_factory,
        admission=env.admission_factory, clock=env.clock,
        plan_runner=env.plan_runner_factory, persist_decision=env.persist_spy,
        note_llm_use=env.note_spy,
        latest_snapshot_fn=lambda: (env.context, env.ctx_ref),
        news_titles_fn=None, position_fn=None, quote_fn=None, strat_fn=None,
        decisions_tail_fn=env.tail_fn)
    kw.update(over)
    return DeepDecideBindings(**kw)


def _fast_result(code: str, direction: str = "买入") -> dict:
    return {"ok": True, "code": code, "name": "测试标的", "asof": "2026-07-24",
            "direction": direction, "hybrid_direction": direction,
            "confidence": 70, "rationale": "fast scripted"}


def _make_fast(env, result: dict):
    """A fake fast decide that — like the real ``_decide_impl`` — persists its own
    decide row BEFORE returning (this is what arms the tail-before-fast trap)."""
    def fast(payload: dict) -> dict:
        env.calls.append("fast")
        env.declog.append({
            "id": "decide_fast", "ts": env.clock.now().isoformat(), "kind": "decide",
            "code": result.get("code") or payload.get("code"),
            "hybrid_direction": result.get("hybrid_direction"),
            "asof": result.get("asof")})
        return dict(result)
    return fast


def _payload(code: str, strategy_id: str = "s1") -> dict:
    return {"code": code, "name": "测试标的", "date": "2026-07-24",
            "seat_cn": "盯盘", "strategy_id": strategy_id, "strategy_name": "深研策略",
            "mode": "fast", "freq": "day", "source": "watcher"}


def _seed_flip(env, code: str, prev_direction: str = "卖出"):
    env.declog.append({"id": "decide_prev", "ts": "2026-07-23T14:55:00",
                       "kind": "decide", "code": code,
                       "hybrid_direction": prev_direction})


_OPT_IN_STRAT = {"deep_research": True, "clock": {}}


def _events(env, run_id):
    return [e.event_type for e in env.stores.events.journal(run_id, "main")]


def _decide_rows(env):
    return [rec for kind, rec in env.persisted if kind == "decide"]


def _order_rows(env):
    return [rec for kind, rec in env.persisted if kind == "order"]


# =========================================================================== #
# 1. happy deep path                                                           #
# =========================================================================== #
class TestHappyDeepPath:
    @pytest.fixture()
    def run(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        fast = _make_fast(env, _fast_result("300750"))
        decide = make_orchestrated_decide(
            fast_decide=fast,
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        out = decide(_payload("300750"))
        return SimpleNamespace(env=env, out=out)

    def test_returns_the_deep_derived_record(self, run):
        out = run.out
        assert out["ok"] is True
        assert out["deep_attempted"] is True
        assert out["deep_outcome"] == "completed"
        assert out["source"] == "orchestrated"
        assert isinstance(out["run_id"], str) and out["run_id"]
        assert out["escalated_from_asof"] == "2026-07-24"
        assert re.fullmatch(r"[0-9a-f]{64}", out["escalation_digest"])
        assert out["rationale"] == "scripted deep-run trader proposal"

    def test_exactly_one_orchestrated_row_with_the_ruled_keys(self, run):
        rows = _decide_rows(run.env)
        assert len(rows) == 1
        rec = rows[0]
        assert rec["source"] == "orchestrated"
        assert rec["run_id"] == run.out["run_id"]
        assert rec["code"] == "300750"
        assert rec["escalation_digest"] == run.out["escalation_digest"]
        assert rec["escalated_from_asof"] == "2026-07-24"
        assert rec["target_weights"] == [
            {"symbol": "600519.SH", "weight": 0.25,
             "stop_loss_pct": 0.08, "take_profit_pct": 0.18}]
        assert rec["cash_weight"] == 0.75
        assert rec["triggers"] == ["opt_in"]
        # only-if-present convention: no None / empty-string values ever land.
        assert all(v not in (None, "") for v in rec.values())

    def test_the_two_row_model_is_explicit(self, run):
        """The fast row persists via the fast path as today; the orchestrated row
        is one ADDITIONAL append — no suppression, no replacement."""
        rows = [r for r in run.env.declog if r.get("kind") == "decide"]
        sources = [r.get("source") for r in rows]
        assert sources.count("orchestrated") == 1
        assert any(r.get("id") == "decide_fast" for r in rows)  # the fast row survived
        assert rows[-1].get("source") == "orchestrated"          # appended after it

    def test_note_llm_use_fires_exactly_once_with_the_runs_count(self, run):
        assert run.env.noted == [8]  # 8 scripted LLM invocations, folded from the ledger

    def test_the_run_really_froze_and_completed_on_the_stores(self, run):
        kinds = _events(run.env, run.out["run_id"])
        assert EventType.PLAN_FROZEN in kinds
        assert EventType.RUN_COMPLETED in kinds

    def test_the_conditional_order_record_is_advisory_and_single(self, run):
        orders = _order_rows(run.env)
        assert len(orders) == 1
        order = orders[0]
        assert order["source"] == "orchestrated"
        assert order["run_id"] == run.out["run_id"]
        assert order["tranches"] == [
            {"symbol": "600519.SH", "price_low": 180.0, "price_high": 190.0,
             "fraction": 0.5},
            {"symbol": "600519.SH", "price_low": 170.0, "price_high": 176.0,
             "fraction": 0.5}]
        assert "advisory" in order["logic"] or "人工" in order["logic"]
        assert all(v not in (None, "") for v in order.values())

    def test_every_llm_request_carries_the_subject_closed_assembler(self, run):
        """E2b: the per-run assembler owns prompt construction — every verified
        prompt record names it, cites the committed RunSubject@1 digest in the
        trusted channel, and the rendered subject block carries the code."""
        gateways = run.env.gateway_factory.gateways
        assert len(gateways) == 1
        records = gateways[0].records
        assert sorted({r.worker_id for r in records}) == sorted(
            wid for wid in DEEP_DECIDE_WORKER_IDS
            if wid not in ("pv.price_action", "pv.microstructure"))
        digests = set()
        for record in records:
            assert record.assembler_id == SUBJECT_ASSEMBLER_ID
            named = [e for e in record.trusted_input_digests
                     if e.name == SUBJECT_TRUSTED_INPUT_NAME]
            assert len(named) == 1
            digests.add(named[0].digest)
        assert len(digests) == 1  # one committed subject digest serves the whole run
        for request in gateways[0].requests:
            blob = request.canonical_request_bytes.decode("utf-8")
            assert "300750" in blob            # the rendered trusted subject block
            assert "trusted_subject" in blob

    #: the plan-side/admission evidence the "code-free journal" claim covers —
    #: worker OUTPUT artifacts are excluded (their payloads legitimately carry
    #: symbols the model wrote), and ``RunSubject@1`` is the ONE sanctioned carrier.
    _PLAN_SIDE_SCHEMAS = (
        "Plan@1", "AdmissionCandidate@1", "PlanValidationReport@1",
        "RuntimeSupportReport@1", "PlanAdmitted@1", "PlanApproval@1",
        "PromptAssemblyRecord@1",
    )

    def test_the_draft_stays_code_free_the_subject_enters_only_at_assembly(self, run):
        """Task 6's invariant is preserved through the runner: the admitted plan
        and every piece of its admission/prompt evidence carry NO six-digit code
        — the subject reaches the model exclusively through the per-run
        assembler channel, anchored by the ONE committed ``RunSubject@1``."""
        env = run.env
        # test-side read of the in-memory payload map (no public listing exists);
        # pydantic's own serialization covers every stored contract model.
        stored_by_schema: dict[str, list] = {}
        for stored in dict(env.stores._shared.backend.payloads).values():
            stored_by_schema.setdefault(stored.schema_key, []).append(stored)

        def _blob(stored) -> str:
            return stored.model.model_dump_json()

        assert stored_by_schema.get("Plan@1"), "the frozen Plan must be persisted"
        assert stored_by_schema.get("PromptAssemblyRecord@1")
        # the fixture code appears in NO persisted payload except the subject.
        for schema_key, entries in stored_by_schema.items():
            if schema_key == "RunSubject@1":
                continue
            for stored in entries:
                assert "300750" not in _blob(stored), schema_key
        # generic sweep on the plan-side evidence: no quoted six-digit string of
        # ANY code, not just the fixture's.
        for schema_key in self._PLAN_SIDE_SCHEMAS:
            for stored in stored_by_schema.get(schema_key, ()):
                blob = _blob(stored)
                assert re.search(r'"\d{6}"', blob) is None, (schema_key, blob)
        # and the subject really IS committed — carried, not implied.
        assert any("300750" in _blob(s)
                   for s in stored_by_schema.get("RunSubject@1", ()))
        # the frozen-plan event exists on the run journal (the original check).
        rows = env.stores.events.journal(run.out["run_id"], "main")
        assert any(e.event_type == EventType.PLAN_FROZEN for e in rows)


# =========================================================================== #
# 2. the tail-before-fast trap (mandatory regression)                          #
# =========================================================================== #
class TestTailBeforeFast:
    def test_the_tail_is_read_before_fast_decide_runs(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        _seed_flip(env, "600519", prev_direction="卖出")
        fast = _make_fast(env, _fast_result("600519", direction="买入"))
        decide = make_orchestrated_decide(
            fast_decide=fast, bindings=_bindings(env, heavy))
        out = decide(_payload("600519"))
        # order proof: the tail snapshot precedes the fast call.
        assert env.calls.index("tail") < env.calls.index("fast")
        # substance proof: the flip FIRED. Had the tail been read after
        # fast_decide (which persists its own 买入 row), prev == fast and the
        # flip could never fire — this is Task 5's trap, pinned.
        assert out.get("deep_outcome") == "completed"
        rec = _decide_rows(env)[0]
        assert rec["triggers"] == ["direction_flip"]

    def test_a_post_fast_tail_would_have_killed_the_flip(self, tmp_path, heavy):
        """The counterfactual, executed: judging over the post-fast tail (which
        already holds the fast row) yields NO escalation — proving the pre-capture
        is load-bearing, not decorative."""
        from guanlan_v2.orchestration.pipeline import escalation as esc

        env = _build_env(tmp_path, heavy)
        _seed_flip(env, "600519", prev_direction="卖出")
        fast_result = _fast_result("600519", direction="买入")
        fast = _make_fast(env, fast_result)
        fast(_payload("600519"))  # the fast row lands first (the wrong order)
        ctx = esc.build_escalation_context(
            code="600519", as_of=NOW, fast_result=fast_result,
            decisions_tail=[dict(r) for r in env.declog])
        assert esc.judge_escalation(ctx).escalate is False


# =========================================================================== #
# 3. non-escalation + fast-failure passthrough                                  #
# =========================================================================== #
class TestPassthrough:
    def test_no_escalation_returns_the_fast_result_untouched(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        fast_result = _fast_result("300750")
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, fast_result),
            bindings=_bindings(env, heavy))
        out = decide(_payload("300750"))
        assert out == fast_result
        assert "deep_attempted" not in out and "deep_outcome" not in out
        assert env.persisted == [] and env.noted == []

    def test_fast_failure_passes_through_unchanged(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        _seed_flip(env, "300750")

        def broken_fast(payload):
            env.calls.append("fast")
            return {"ok": False, "code": "300750", "reason": "LLMError: scripted"}

        decide = make_orchestrated_decide(
            fast_decide=broken_fast,
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        out = decide(_payload("300750"))
        assert out == {"ok": False, "code": "300750", "reason": "LLMError: scripted"}
        assert "deep_attempted" not in out
        assert env.persisted == [] and env.noted == []

    def test_an_unnameable_code_returns_fast_untouched(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        fast_result = dict(_fast_result("300750"), code="人工智能")
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, fast_result),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        out = decide(dict(_payload("300750"), code="人工智能"))
        assert out == fast_result
        assert "deep_attempted" not in out


# =========================================================================== #
# 4. degradation matrix — every deep failure returns the fast result           #
# =========================================================================== #
class TestDegradedHonest:
    def test_pending_human_falls_back_no_lease(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy, lease=False)  # no lease in the journal
        fast_result = _fast_result("300750")
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, fast_result),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        out = decide(_payload("300750"))
        assert out.get("deep_attempted") is True
        assert out.get("deep_outcome") == "no_lease"
        for key, value in fast_result.items():
            assert out[key] == value
        assert _decide_rows(env) == [] and env.noted == []
        # the pending card stayed registered for a human (never silently dropped)
        reader = PlanApprovalCoordinator(
            env.journal, admission=SimpleNamespace(), clock=env.clock,
            verifier=None, console_emit=None)
        pending = reader.list_pending()
        assert len(pending) == 1
        assert pending[0].preset_id == DEEP_DECIDE_PRESET_ID

    def test_missing_snapshot_falls_back_refused(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        fast_result = _fast_result("300750")
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, fast_result),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT),
                               latest_snapshot_fn=lambda: None))
        out = decide(_payload("300750"))
        assert out.get("deep_attempted") is True
        assert out.get("deep_outcome") == "refused"
        assert _decide_rows(env) == [] and env.noted == []

    def test_a_naive_clock_falls_back_refused(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, _fast_result("300750")),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT),
                               clock=_FixedClock(datetime(2026, 7, 24, 10, 30))))
        out = decide(_payload("300750"))
        assert out.get("deep_outcome") == "refused"
        assert _decide_rows(env) == [] and env.noted == []

    def test_run_failure_falls_back_failed_and_reports_settled_spend(
            self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy, fail=frozenset({"dec.pm"}))
        fast_result = _fast_result("300750")
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, fast_result),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        out = decide(_payload("300750"))
        assert out.get("deep_attempted") is True
        assert out.get("deep_outcome") == "failed"
        for key, value in fast_result.items():
            assert out[key] == value
        assert _decide_rows(env) == []
        # the failed run reports its settled spend (upstream nodes really ran).
        assert len(env.noted) == 1 and 0 < env.noted[0] <= 8

    def test_a_post_runner_landing_failure_still_notes_the_settled_spend(
            self, tmp_path, heavy):
        """Review I-1: once the runner completed, its settled invocations are
        REAL spend against the watcher's 24/day pool — a failure in the landing
        steps (extract/persist) must not lose them, and must note exactly once."""
        env = _build_env(tmp_path, heavy)
        kinds: list = []

        def broken_persist(kind, rec):
            kinds.append(kind)
            raise RuntimeError("disk full (scripted)")

        fast_result = _fast_result("300750")
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, fast_result),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT),
                               persist_decision=broken_persist))
        out = decide(_payload("300750"))
        assert out.get("deep_attempted") is True
        assert out.get("deep_outcome") == "failed"
        assert out.get("run_id")
        for key, value in fast_result.items():
            assert out[key] == value
        assert env.noted == [8]      # the folded count reached the pool, ONCE
        assert kinds == ["decide"]   # the landing died at the first append

    def test_a_materialize_refusal_falls_back_refused_through_the_real_branch(
            self, tmp_path, heavy, monkeypatch):
        """Review Minor 1: drive a typed refusal THROUGH the materialize call so
        the ``except DeepDecideError`` branch itself is exercised."""
        from guanlan_v2.orchestration.pipeline.deep_decide import SubjectRefused

        env = _build_env(tmp_path, heavy)
        fast_result = _fast_result("300750")

        def refusing_materialize(**kwargs):
            raise SubjectRefused("scripted: the subject artifact was refused")

        monkeypatch.setattr(live_decide, "materialize_deep_decide_draft",
                            refusing_materialize)
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, fast_result),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        out = decide(_payload("300750"))
        assert out.get("deep_attempted") is True
        assert out.get("deep_outcome") == "refused"
        for key, value in fast_result.items():
            assert out[key] == value
        assert _decide_rows(env) == [] and env.noted == []

    def test_a_raising_binding_never_raises_into_the_tick(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        fast_result = _fast_result("300750")

        def exploding(*a, **kw):
            raise RuntimeError("scripted binding explosion")

        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, fast_result),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT),
                               admission=exploding))
        out = decide(_payload("300750"))
        assert out.get("deep_outcome") == "failed"
        for key, value in fast_result.items():
            assert out[key] == value


# =========================================================================== #
# 5. idempotent re-tick                                                        #
# =========================================================================== #
class TestIdempotentReTick:
    def test_same_key_yields_one_orchestrated_row_and_one_llm_note(
            self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, _fast_result("002415")),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        first = decide(_payload("002415"))
        assert first["deep_outcome"] == "completed"
        second = decide(_payload("002415"))
        assert second.get("deep_outcome") == "completed"
        assert second.get("deep_replayed") is True
        assert second.get("run_id") == first["run_id"]
        assert len(_decide_rows(env)) == 1      # ONE orchestrated row, ever
        assert env.noted == [8]                 # note_llm_use exactly once per run
        # the scripted gateway was not re-invoked for the replay.
        total = sum(len(g.records) for g in env.gateway_factory.gateways)
        assert total == 8


# =========================================================================== #
# 6. escalation ports through the wrapper                                       #
# =========================================================================== #
class TestEscalationPorts:
    def test_position_fn_feeds_the_band_trigger(self, tmp_path, heavy):
        """Task 5 ruling: hold_entry reaches the bands via position_fn (ledger
        state replay) — preferred over the tail row's own value."""
        env = _build_env(tmp_path, heavy)
        # a prior row EXISTS but carries no hold_entry — the port supplies it.
        env.declog.append({"id": "d0", "ts": "2026-07-23T14:00:00", "kind": "decide",
                           "code": "000001", "hybrid_direction": "买入"})
        fast = _make_fast(env, _fast_result("000001", direction="买入"))
        decide = make_orchestrated_decide(
            fast_decide=fast,
            bindings=_bindings(
                env, heavy,
                strat_fn=lambda sid: {"clock": {"stopLoss": 0.08, "takeProfit": 0.18}},
                position_fn=lambda: {"SZ000001": 100.0},
                quote_fn=lambda code: {"fresh": True, "price": 92.5}))
        out = decide(_payload("000001"))
        assert out.get("deep_outcome") == "completed"
        assert _decide_rows(env)[0]["triggers"] == ["stop_take_proximity"]

    def test_a_string_false_quote_is_not_fresh(self, tmp_path, heavy):
        """The fresh-gate boolean contract: a truthy string like "false" must
        never arm the band trigger through the wrapper."""
        env = _build_env(tmp_path, heavy)
        env.declog.append({"id": "d0", "ts": "2026-07-23T14:00:00", "kind": "decide",
                           "code": "000001", "hybrid_direction": "买入"})
        fast = _make_fast(env, _fast_result("000001", direction="买入"))
        decide = make_orchestrated_decide(
            fast_decide=fast,
            bindings=_bindings(
                env, heavy,
                strat_fn=lambda sid: {"clock": {"stopLoss": 0.08, "takeProfit": 0.18}},
                position_fn=lambda: {"SZ000001": 100.0},
                quote_fn=lambda code: {"fresh": "false", "price": 92.5}))
        out = decide(_payload("000001"))
        assert "deep_attempted" not in out      # nothing escalated

    def test_a_raising_position_port_is_an_absence_not_a_guess(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        env.declog.append({"id": "d0", "ts": "2026-07-23T14:00:00", "kind": "decide",
                           "code": "000001", "hybrid_direction": "买入"})

        def broken_position():
            raise RuntimeError("ledger unreadable")

        fast = _make_fast(env, _fast_result("000001", direction="买入"))
        decide = make_orchestrated_decide(
            fast_decide=fast,
            bindings=_bindings(
                env, heavy,
                strat_fn=lambda sid: {"clock": {"stopLoss": 0.08, "takeProfit": 0.18}},
                position_fn=broken_position,
                quote_fn=lambda code: {"fresh": True, "price": 92.5}))
        out = decide(_payload("000001"))
        assert "deep_attempted" not in out      # bands stayed inert; no invented entry


# =========================================================================== #
# 7. conditional-order record only when trigger ranges exist                    #
# =========================================================================== #
class TestConditionalOrder:
    def test_no_tranches_means_no_order_record(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy, tranches=False)
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, _fast_result("300750")),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        out = decide(_payload("300750"))
        assert out["deep_outcome"] == "completed"
        assert len(_decide_rows(env)) == 1
        assert _order_rows(env) == []


# =========================================================================== #
# 8. red lines: no trade / order / signal writes                                #
# =========================================================================== #
class TestRedLines:
    def test_module_source_never_writes_a_trade(self):
        source = Path(live_decide.__file__).read_text(encoding="utf-8")
        assert '"trade"' not in source and "'trade'" not in source
        # the only persist kinds this module ever emits:
        kinds = set(re.findall(r"persist_decision\(\s*['\"](\w+)['\"]", source))
        assert kinds <= {"decide", "order"}

    def test_module_top_level_imports_stay_inside_orchestration(self):
        source = Path(live_decide.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:  # TOP-LEVEL only: seats/engine reads are lazy
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module.startswith("guanlan_v2"):
                    assert module.startswith("guanlan_v2.orchestration"), module
                assert not module.startswith("financial_analyst"), module
        # no HTTP client anywhere in the module — module-level OR lazy.
        every_import: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                every_import.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                every_import.add(node.module)
        for banned in ("httpx", "requests", "aiohttp", "urllib", "socket"):
            assert not any(m == banned or m.startswith(banned + ".")
                           for m in every_import), banned

    def test_a_full_run_writes_only_decide_and_order_kinds(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, _fast_result("300750")),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        decide(_payload("300750"))
        assert {kind for kind, _ in env.persisted} == {"decide", "order"}


# =========================================================================== #
# 9. watcher/server seams — env-off bit-unchanged                               #
# =========================================================================== #
class TestWatcherServerSeams:
    def test_run_loop_signature_is_additive(self):
        from guanlan_v2.seats import watcher

        sig = inspect.signature(watcher.run_loop)
        assert "decide_fn" in sig.parameters
        param = sig.parameters["decide_fn"]
        assert param.default is None
        assert param.kind is inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.parametrize("injected", [None, "SENTINEL"])
    def test_run_loop_threads_decide_fn_into_tick(self, monkeypatch, injected):
        from guanlan_v2.seats import watcher

        sentinel = object() if injected == "SENTINEL" else None
        seen: dict = {}

        def fake_tick(now=None, decide_fn=None, quote_fn=None, decisions_tail_fn=None):
            seen["decide_fn"] = decide_fn
            return {"judged": [], "skipped": {}}

        async def cancel_sleep(_seconds):
            raise asyncio.CancelledError

        monkeypatch.setattr(watcher, "tick", fake_tick)
        monkeypatch.setattr(watcher, "load_state", lambda: {"enabled": True})
        monkeypatch.setattr(watcher.asyncio, "sleep", cancel_sleep)
        with pytest.raises(asyncio.CancelledError):
            if sentinel is None:
                asyncio.run(watcher.run_loop())
            else:
                asyncio.run(watcher.run_loop(decide_fn=sentinel))
        assert seen["decide_fn"] is sentinel

    def test_server_wiring_is_env_gated_and_additive(self):
        source = Path("guanlan_v2/server.py").read_text(encoding="utf-8")
        assert 'os.environ.get("GUANLAN_SEATS_DEEP") == "1"' in source
        # the deep decide_fn is threaded through run_loop, defaulted to None.
        assert re.search(r"run_loop\(\s*decide_fn=", source)
        # the deep import is lazy INSIDE the env gate (env unset imports nothing).
        gate = source.index('GUANLAN_SEATS_DEEP')
        lazy = source.index("live_decide", gate)
        assert lazy > gate

    def test_build_production_decide_fn_wraps_the_fast_path(self, monkeypatch,
                                                            tmp_path, heavy):
        env = _build_env(tmp_path, heavy)
        monkeypatch.setattr(live_decide, "build_production_bindings",
                            lambda: _bindings(env, heavy))
        fn = live_decide.build_production_decide_fn()
        assert callable(fn)

    def test_an_unbound_process_store_is_a_typed_refusal_never_a_self_bind(
            self, monkeypatch):
        """Review I-3: with no healthy R23/R24 process binding, the deep lane
        REFUSES (typed) — it must never become the process binder itself (a
        deep-lane rebind would freeze a one-namespace allowlist and violate the
        server's 全进程唯一绑定 invariant)."""
        from guanlan_v2.orchestration.adapters import durable as durable_mod

        bind_calls: list = []
        monkeypatch.setattr(durable_mod, "process_durable_stores", lambda: None)

        def spy_bind(**kwargs):
            bind_calls.append(kwargs)
            raise AssertionError("the deep lane must never self-bind the stores")

        monkeypatch.setattr(
            durable_mod, "bind_process_durable_stores_and_scan", spy_bind)
        with pytest.raises(live_decide.ProductionStoresUnbound,
                           match="never self-binds"):
            live_decide.build_production_bindings()
        assert bind_calls == []

    def test_production_assembly_failure_is_loud_never_silent(self, monkeypatch):
        def exploding():
            raise RuntimeError("no reviewed material source yet (Task 11)")

        monkeypatch.setattr(live_decide, "build_production_bindings", exploding)
        with pytest.raises(RuntimeError, match="Task 11"):
            live_decide.build_production_decide_fn()

    def test_a_bound_process_store_passes_the_gate_and_builds_real_bindings(
            self, monkeypatch, tmp_path):
        """Task 11 (the store-gate HEALTHY arm + the promoted material source):
        with a healthy R23/R24 binding the gate passes through and
        ``build_production_bindings`` now assembles REAL bindings end to end —
        the full Phase-9 catalog runtime resolves from the promoted nine-loader
        universe and the three-analyzer bridge view builds. (The deep lane
        remains support-refused at admission time — the permanent-honest
        grant-gap ruling — but the ASSEMBLY no longer refuses.)"""
        from guanlan_v2 import orch_store_status as status_mod
        from guanlan_v2.orchestration.adapters import durable as durable_mod
        from guanlan_v2.orchestration.adapters.durable import (
            build_durable_runtime_stores,
        )

        stores = build_durable_runtime_stores(tmp_path / "orch")
        monkeypatch.setattr(durable_mod, "process_durable_stores", lambda: stores)
        monkeypatch.setattr(status_mod, "orchestration_store_bound", lambda: True)
        bindings = live_decide.build_production_bindings()
        assert isinstance(bindings, DeepDecideBindings)
        assert bindings.stores is stores
        assert bindings.catalog.catalog_digest == chain.PHASE9_CATALOG_DIGEST
        assert callable(bindings.admission)
        assert callable(bindings.coordinator)
        assert callable(bindings.plan_runner)
        assert callable(bindings.latest_snapshot_fn)


# =========================================================================== #
# 11b. Task 11 — the _ApprovalsBridge binding (untestable until production deps) #
# =========================================================================== #
class TestApprovalsBridge:
    def test_the_bridge_mirrors_every_deposit_into_the_sink(self):
        mirrored: list = []
        bridge = live_decide._ApprovalsBridge(mirrored.append)
        bridge[("r1", "d1")] = "approval-1"
        assert bridge[("r1", "d1")] == "approval-1"  # dict semantics retained
        assert mirrored == ["approval-1"]
        bridge[("r1", "d1")] = "approval-2"  # an overwrite mirrors again
        assert dict(bridge) == {("r1", "d1"): "approval-2"}
        assert mirrored == ["approval-1", "approval-2"]

    def test_the_bridge_delivers_the_durable_decision_to_both_stores(
            self, tmp_path, heavy):
        """The reviewed Task-8 seam, end to end over a REAL journal: the
        coordinator's decision path deposits the ONE authoritative
        ``PlanApproval`` into the bridged approvals store (which the admission
        service's ``record_approval`` loads) AND mirrors it into the caller's
        per-run sink — the exact ``build_plan_approval_coordinator`` +
        ``_ApprovalsBridge`` composition production wiring uses."""
        from guanlan_v2.orchestration.adapters.api import (
            build_plan_approval_coordinator,
        )
        from guanlan_v2.orchestration.enums import ApprovalDecision

        env = _build_env(tmp_path, heavy, lease=False)
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, _fast_result("300750")),
            bindings=_bindings(env, heavy,
                               strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        decide(_payload("300750"))  # registers ONE pending card in the journal

        mirrored: list = []
        bridge = live_decide._ApprovalsBridge(mirrored.append)
        stub_calls: list = []

        class _AdmissionStub:
            def record_approval(self, digest, submission, *,
                                authenticated_actor, idempotency_key):
                stub_calls.append((digest, submission.decision,
                                   authenticated_actor))
                return SimpleNamespace(event_id="ev-t11")

        coordinator = build_plan_approval_coordinator(
            admission=_AdmissionStub(), clock=env.clock, verifier=_Verifier(),
            approvals=bridge, journal_path=env.journal,
            preset_registry=heavy.presets,
            catalog_digest=heavy.snapshot.catalog_digest,
            registry_digest=heavy.registry.registry_digest)
        card = coordinator.list_pending()[0]
        approval, _event = coordinator.decide(
            request_id=card.request_id,
            candidate_plan_digest=card.candidate_plan_digest,
            decision=ApprovalDecision.APPROVED, actor=GOOD_CRED,
            reason="t11 bridge binding test", idempotency_key="t11-decide-1")
        key = (card.request_id, card.candidate_plan_digest)
        # both halves saw the EXACT authoritative object:
        assert bridge[key] is approval          # the admission-owned store
        assert mirrored == [approval]           # the caller's per-run sink
        # …and record_approval was driven with the same durable decision.
        assert stub_calls == [(card.candidate_plan_digest,
                               ApprovalDecision.APPROVED, approval.actor_id)]


# =========================================================================== #
# 10. the pending card + the subject assembler surfaces                         #
# =========================================================================== #
class TestSurfaces:
    def test_the_pending_card_carries_preset_provenance(self, tmp_path, heavy):
        env = _build_env(tmp_path, heavy, lease=False)
        decide = make_orchestrated_decide(
            fast_decide=_make_fast(env, _fast_result("300750")),
            bindings=_bindings(env, heavy, strat_fn=lambda sid: dict(_OPT_IN_STRAT)))
        decide(_payload("300750"))
        reader = PlanApprovalCoordinator(
            env.journal, admission=SimpleNamespace(), clock=env.clock,
            verifier=None, console_emit=None)
        card = reader.list_pending()[0]
        assert card.preset_id == DEEP_DECIDE_PRESET_ID
        assert card.preset_record_digest == heavy.record.semantic_digest()
        assert card.budget_request_llm_invocations == 8

    def test_the_subject_assembler_refuses_a_trusted_name_collision(self, heavy):
        from guanlan_v2.orchestration.pipeline.contracts import RunSubject
        from guanlan_v2.orchestration.refs import TypedPayloadRef
        from guanlan_v2.orchestration.runtime_contracts import NamedEvidenceDigest

        subject = RunSubject(code="300750", as_of=NOW)
        ref = TypedPayloadRef(
            schema_ref=SchemaRef(name="RunSubject", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="subj-x",
                                   content_digest="a" * 64))
        assembler = SubjectPromptAssembler(subject=subject, subject_ref=ref)
        resolved = heavy.runtime.resolve_worker("text.sentiment")
        clash = NamedEvidenceDigest(name=SUBJECT_TRUSTED_INPUT_NAME, digest="b" * 64)
        with pytest.raises(Exception, match=SUBJECT_TRUSTED_INPUT_NAME):
            assembler.assemble(
                plan_digest="0" * 64, node_id="n1", worker_id="text.sentiment",
                system_prompt=resolved.system_prompt, skills=resolved.skills,
                guardrails=resolved.guardrails, trusted_input_digests=(clash,),
                untrusted_blocks=())

    def test_the_subject_assembler_binding_verifies_end_to_end(self, heavy):
        """The assembled request passes the shared reviewed rehash-refusal —
        i.e. the per-run assembler produces a REAL, verifiable prompt binding."""
        from guanlan_v2.orchestration.pipeline.contracts import RunSubject
        from guanlan_v2.orchestration.refs import TypedPayloadRef

        subject = RunSubject(code="300750", as_of=NOW)
        ref = TypedPayloadRef(
            schema_ref=SchemaRef(name="RunSubject", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="subj-x",
                                   content_digest="a" * 64))
        assembler = SubjectPromptAssembler(subject=subject, subject_ref=ref)
        resolved = heavy.runtime.resolve_worker("text.sentiment")
        request = assembler.assemble(
            plan_digest="0" * 64, node_id="n1", worker_id="text.sentiment",
            system_prompt=resolved.system_prompt, skills=resolved.skills,
            guardrails=resolved.guardrails, trusted_input_digests=(),
            untrusted_blocks=())
        record_ref = TypedPayloadRef(
            schema_ref=SchemaRef(name="PromptAssemblyRecord", version="1"),
            payload_ref=PayloadRef(namespace="main", object_id="rec-x",
                                   content_digest="0" * 64))

        class _Reader:
            def get(self, r, *, expected_schema_ref):
                return request.prompt_record

        record = W.verify_model_request_binding(
            request, record_ref, reader=_Reader())
        assert record.assembler_id == SUBJECT_ASSEMBLER_ID
        assert [e.name for e in record.trusted_input_digests].count(
            SUBJECT_TRUSTED_INPUT_NAME) == 1
