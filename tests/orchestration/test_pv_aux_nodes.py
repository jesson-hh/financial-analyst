# -*- coding: utf-8 -*-
"""The two deterministic price-volume seats after the L3 production grant.

The suite pins RunSubject@2, blocking prefetch semantics, typed OHLCV/L1
computation, honest optional-feed warnings, incomplete-L1 refusal and the full
ten-seat graph's BLOCK dependencies.  It uses no network, LLM or ``var/`` write.

Run from repo root:
``python -m pytest tests/orchestration/test_pv_aux_nodes.py -v``
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.adapters.data_world as DW
import guanlan_v2.orchestration.bootstrap as bs
import guanlan_v2.orchestration.memory.runtime as MR
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.adapters.live_data import (
    POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX,
)
from guanlan_v2.orchestration.approval import admit_after_approval
from guanlan_v2.orchestration.admission import (
    ApprovalSubmission,
    PlanAdmissionService,
)
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog_runtime import (
    CatalogMaterialError,
    TrustedFactoryRegistry,
    load_pilot_catalog,
)
from guanlan_v2.orchestration.context import InputSnapshot, RunBudget, RunContext
from guanlan_v2.orchestration.enums import (
    ApprovalDecision,
    ApprovalPolicy,
    DataMode,
    DataStatus,
    DependencyPolicy,
    NodeStatus,
)
from guanlan_v2.orchestration.data.result import PitAudit, SourceAttempt
from guanlan_v2.orchestration.data.source import (
    IndicatorDataResult,
    IndicatorRecord,
    IndicatorRows,
    OHLCVDataResult,
    OHLCVRecord,
    OHLCVRows,
    VerifiedSnapshotDataResult,
    VerifiedSnapshotRecord,
    VerifiedSnapshotRows,
)
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    check_runtime_support,
)
from guanlan_v2.orchestration.events import PlanApproval
from guanlan_v2.orchestration.spec import OrchestrationRequest, validate_plan_draft
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    build_production_catalog_runtime,
    load_phase10_preset_registry,
    production_bridge_view,
)
from guanlan_v2.orchestration.pipeline.deep_decide import (
    DEEP_DECIDE_PRESET_ID,
    RUN_SUBJECT_SCHEMA_REF,
    REDUCED_DEEP_DECIDE_PRESET_ID,
    DeepRunSubject,
    materialize_deep_decide_draft,
)
from guanlan_v2.orchestration.pipeline import pv_aux as PV

UTC = timezone.utc
NOW = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
_SYMBOL = normalize_symbol("600519.SH")
PHASE8_REGISTRY_DIGEST = (
    "201e7f2cccdf56369e6c8244982eb0647172a7be7d2b8060f8eebb75de6ca650"
)
CTX_SR = SchemaRef(name="ContextSnapshot", version="1")

#: the two aux worker ids and their catalog handler material ids.
PV_AUX = {
    "pv.price_action": "handler.pv.price_action",
    "pv.microstructure": "handler.pv.microstructure",
}

#: The pre-Task-6 shape, MEASURED (2026-08-01, this harness on the Task-5 tree)
#: and kept as a NEGATIVE pin.  With no factory bound, ``worker.execute_node``
#: did not seal a NodeRun at all: ``runtime.factories.handler_factory(...)``
#: raised ``CatalogMaterialError: no handler factory bound for
#: handler.pv.price_action@1`` straight out of the executor (the lookup sits
#: OUTSIDE the deterministic branch's ``try``).  ``dag.run_plan`` then
#: synthesized a FAILED node with ``reason_code="executor_exception"`` carrying
#: that text — so the node's whole story was "a wire was missing", never
#: anything about the market.  Neither may occur on the bound path.
UNRESOLVED_HANDLER_TEXT = "no handler factory bound"

#: the reason codes the aux node must NOT end on: the measured pre-Task-6 one,
#: the bridge-layer one the Task-3/4 rowless arm already ruled out, and the
#: shape the brief guessed at (which does not exist in this codebase — recorded
#: so a future reader knows the guess was checked, not adopted).
NOT_THESE_REASONS = (
    "executor_exception", "bridge_execution_error", "handler_unresolved")


def _pit_audit(row_count: int) -> PitAudit:
    return PitAudit(
        mode=DataMode.ONLINE, as_of=NOW, rows_seen=row_count,
        rows_returned=row_count, future_rows=0,
        missing_available_at_rows=0, guard_result="passed")


def _ok_data_result(result_cls, *, object_id: str, method: str, data, row_count: int):
    return result_cls.build(
        id=object_id, method=method, request_digest="a" * 64,
        status=DataStatus.OK, data=data, resolved_vendor_chain=("fixture",),
        source_config_digest="b" * 64, fetched_at=NOW,
        attempts=(SourceAttempt(
            vendor="fixture", configured=True, outcome="success",
            started_at=NOW, finished_at=NOW),),
        pit_audit=_pit_audit(row_count),
    )


def _typed_result(payload, object_id: str) -> TypedPayloadRef:
    return TypedPayloadRef(
        payload_ref=PayloadRef(
            namespace="main", object_id=object_id,
            content_digest=payload.semantic_digest()),
        schema_ref=SchemaRef(name=type(payload).__name__, version="1"),
    )


class _PayloadReader:
    def __init__(self, payloads: dict[str, object]) -> None:
        self._payloads = dict(payloads)

    def get(self, ref, *, expected_schema_ref=None):
        payload = self._payloads[ref.object_id]
        if expected_schema_ref is not None:
            assert expected_schema_ref.name == type(payload).__name__
        assert ref.content_digest == payload.semantic_digest()
        return payload


def _ohlcv_result():
    rows = []
    for i in range(25):
        at = NOW - timedelta(days=24 - i)
        open_price = 100.0 + (i * 0.8)
        close_price = open_price + (0.5 if i % 2 else -0.2)
        rows.append(OHLCVRecord.build(
            symbol=_SYMBOL, open=open_price,
            high=max(open_price, close_price) + 1.0,
            low=min(open_price, close_price) - 1.0, close=close_price,
            volume=1000 + (i * 50), effective_at=at,
            available_at=at, ingested_at=at))
    return _ok_data_result(
        OHLCVDataResult, object_id="ohlcv-result", method="ohlcv",
        data=OHLCVRows.build(rows), row_count=len(rows))


def _micro_results(
        *, complete_l1: bool = True,
        revision_ids: tuple[str | None, str | None] = (None, None)):
    anchor = VerifiedSnapshotRecord.build(
        symbol=_SYMBOL, last_price=120.0, prev_close=119.0,
        effective_at=None, available_at=NOW, ingested_at=NOW)
    anchor_result = _ok_data_result(
        VerifiedSnapshotDataResult, object_id="snapshot-result",
        method="verified_snapshot", data=VerifiedSnapshotRows.build([anchor]),
        row_count=1)
    indicators = [IndicatorRecord.build(
        symbol=_SYMBOL, indicator_id="l1_spread_bp", value=2.5,
        effective_at=NOW, available_at=NOW, ingested_at=NOW,
        revision_id=revision_ids[0])]
    if complete_l1:
        indicators.append(IndicatorRecord.build(
            symbol=_SYMBOL, indicator_id="bid_ask_imbalance", value=0.25,
            effective_at=NOW, available_at=NOW, ingested_at=NOW,
            revision_id=revision_ids[1]))
    indicator_result = _ok_data_result(
        IndicatorDataResult, object_id="indicator-result", method="indicators",
        data=IndicatorRows.build(indicators), row_count=len(indicators))
    return anchor_result, indicator_result


class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _PoisonedStores:
    """The rowless memory path must never touch the stores."""

    def __getattr__(self, name):  # pragma: no cover - failing is the assertion
        raise AssertionError(f"stores.{name} touched by the rowless memory path")


class _ForbiddenEvidenceWriter:
    """An empty bridge layer must never persist evidence."""

    def put(self, **kwargs):  # pragma: no cover - failing is the assertion
        raise AssertionError("evidence write attempted by an empty bridge path")

    def record_existing(self, **kwargs):  # pragma: no cover
        raise AssertionError("evidence write attempted by an empty bridge path")


class _RefusalSpy:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kwargs) -> None:
        self.records.append(kwargs)


# =========================================================================== #
# fixtures — the REAL sealed Phase-9 catalog through the REAL assembly          #
# =========================================================================== #
@pytest.fixture(scope="module")
def env():
    registry = chain.build_phase9_registry(PHASE8_REGISTRY_DIGEST)
    snapshot = chain.phase9_catalog_snapshot()
    resolver = SchemaRegistryResolver()
    resolver.register(registry)
    stores = RuntimeStores(
        resolver=resolver, clock=_FixedClock(),
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))
    mem = P.build_empty_memory_context(
        data_context=P.pilot_data_context(as_of=NOW), stores=stores,
        registry_digest=registry.registry_digest, built_at=NOW)
    context = mem.context
    ctx_ref = stores.payloads.put(
        CTX_SR, context, registry_digest=registry.registry_digest,
        namespace="main", idempotency_key="pvaux:ctx")
    request = OrchestrationRequest(
        request_id="req-pvaux-1", goal="观澜 · 落子深度研判 (pv aux)",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    return {
        "registry": registry, "snapshot": snapshot, "context": context,
        "ctx_ref": ctx_ref,
        "ctx_typed": TypedPayloadRef(schema_ref=CTX_SR, payload_ref=ctx_ref),
        "request": request, "stores": stores,
        "by_id": {w.id: w for w in snapshot.workers},
    }


@pytest.fixture(scope="module")
def bundle(env):
    """The production bundle — exactly ``live_decide``'s
    ``build_production_catalog_runtime(snapshot)``, no explicit
    ``handler_registry``."""
    return build_production_catalog_runtime(env["snapshot"])


@pytest.fixture(scope="module")
def view(bundle):
    return production_bridge_view(bundle.runtime)


def _subject_ref(code: str = "600519") -> TypedPayloadRef:
    subject = DeepRunSubject(code=code, as_of=NOW, evidence_tf="day")
    return TypedPayloadRef(
        payload_ref=PayloadRef(
            namespace="main", object_id=f"subject-{code}",
            content_digest=subject.semantic_digest()),
        schema_ref=RUN_SUBJECT_SCHEMA_REF)


def _materialize(env, preset_id: str, *, draft_id: str, run_id: str):
    return materialize_deep_decide_draft(
        request=env["request"],
        preset_registry=load_phase10_preset_registry(PRODUCTION_PRESETS_DIR),
        preset_id=preset_id, context_snapshot_ref=env["ctx_ref"],
        subject_ref=_subject_ref(), clock=_FixedClock(),
        context=env["context"], catalog=env["snapshot"],
        schema_registry=env["registry"], draft_id=draft_id, run_id=run_id)


@pytest.fixture(scope="module")
def reduced(env, bundle, view):
    """The materialized REDUCED draft + its REAL support report (the live shape)."""
    materialized = _materialize(
        env, REDUCED_DEEP_DECIDE_PRESET_ID,
        draft_id="draft-pvaux-1", run_id="run-pvaux-1")
    draft = materialized.draft
    phase1 = validate_plan_draft(
        draft, request=env["request"], context=env["context"],
        catalog=env["snapshot"], schema_registry=env["registry"])
    assert phase1.valid is True, [(i.code, i.node_id) for i in phase1.issues]
    report = check_runtime_support(
        draft, phase1_report=phase1, context=env["context"],
        context_requirements=None, catalog=bundle.runtime, bridge_view=view,
        schema_registry=env["registry"], profile=STATIC_RUNTIME_PROFILE_V2)
    assert report.supported is True, [(i.code, i.node_id) for i in report.issues]
    return SimpleNamespace(draft=draft, report=report)


@pytest.fixture(scope="module")
def full_draft(env):
    """The SEALED ten-node deep draft — the graph in which the aux nodes have a
    downstream consumer at all (``price-action`` → ``technical`` → bull/bear)."""
    return _materialize(
        env, DEEP_DECIDE_PRESET_ID,
        draft_id="draft-pvaux-full", run_id="run-pvaux-full").draft


@pytest.fixture(scope="module")
def wired(env):
    """A production bundle carrying ``build_production_bindings``' OWN shape:
    the three reviewed provider recipes registered on the bundle's own
    ``factories`` — the same registry object the Task-6 pv defaults land on, so
    the executor sees one registry, exactly as production does.

    (``test_pm_two_bridges`` registers onto a FRESH registry on purpose, to keep
    its raw-bundle control pristine; that shape would hide the pv defaults, so
    it is deliberately not reused here.)"""
    built = build_production_catalog_runtime(env["snapshot"])
    bs.register_lane0_experience_factories(
        factories=built.factories, catalog=bs.load_lane0_catalog(), pool=None,
        registry=env["registry"], experience_views=(),
        experience_scaler=None, as_of=NOW)
    MR.register_phase3_memory_provider_factory(
        factories=built.factories, stores=_PoisonedStores())
    DW.register_production_data_provider(
        factories=built.factories, stores=env["stores"],
        schema_resolver=env["stores"].resolver, clock=_FixedClock(),
        catalog_runtime=built.runtime)
    return SimpleNamespace(bundle=built, view=production_bridge_view(built.runtime))


# =========================================================================== #
# (a) the production bundle resolves a handler factory for BOTH pv aux refs     #
# =========================================================================== #
class TestTheProductionHandlerRegistration:
    """Task-0 D-E's gap, closed: ``build_production_catalog_runtime`` — the
    exact call ``live_decide`` / ``api`` / ``lane0_driver`` make, with NO
    explicit ``handler_registry`` — now binds a trusted factory for both pv
    handler materials."""

    def test_both_pv_handler_refs_resolve_a_factory_on_the_production_bundle(
            self, bundle, env):
        for wid, handler_id in sorted(PV_AUX.items()):
            worker = env["by_id"][wid]
            ref = worker.execution.handler_ref
            assert ref.id == handler_id and ref.version == "1"
            factory = bundle.factories.handler_factory(ref)
            assert callable(factory)
            handler = factory(worker=worker, resolved=None)
            assert callable(handler)

    def test_the_registration_is_keyed_by_the_sealed_catalog_material_ref(
            self, bundle, env):
        """Bound by the snapshot's own ``kind='handler'`` ContentRef — never an
        id string, never a rebuildable digest: a ref with a different digest is
        not the bound identity."""
        from guanlan_v2.orchestration.refs import ContentRef

        ref = env["by_id"]["pv.price_action"].execution.handler_ref
        assert bundle.factories.handler_factory(ref) is not None
        with pytest.raises(CatalogMaterialError):
            bundle.factories.handler_factory(
                ContentRef(id=ref.id, version=ref.version, content_digest="0" * 64))

    def test_an_explicit_handler_registry_entry_still_wins(self, env):
        """A caller's own binding for the same id REPLACES the production
        default (never a double ``register_handler``, which the registry
        refuses as a rebind) — the scripted-handler seam other suites use."""

        def scripted(**_kw):  # pragma: no cover - identity carrier only
            return None

        other = build_production_catalog_runtime(
            env["snapshot"],
            handler_registry={"handler.pv.price_action": scripted})
        pa = env["by_id"]["pv.price_action"].execution.handler_ref
        ms = env["by_id"]["pv.microstructure"].execution.handler_ref
        assert other.factories.handler_factory(pa) is scripted
        # the sibling default is untouched by the override.
        assert other.factories.handler_factory(ms) is not scripted

    def test_a_snapshot_without_the_materials_builds_unchanged(self):
        """A catalog that does not seal the pv handler materials (the pilot
        lineage) gets NO phantom binding and still builds — the default is
        registered only for material the snapshot actually carries."""
        pilot = load_pilot_catalog()
        built = build_production_catalog_runtime(pilot.snapshot)
        assert built.catalog_digest == pilot.snapshot.catalog_digest
        ids = {e.ref.id for e in pilot.snapshot.content_manifest
               if e.kind == "handler"}
        assert not (set(PV_AUX.values()) & ids)


# =========================================================================== #
# (b) the REAL execution runtime: EMPTY bridge, then the typed refusal          #
# =========================================================================== #
def _admitted(env, bundle, view, reduced):
    """The reduced draft driven through the REAL admission pipeline to an
    admitted ``Plan`` + its plan-scope reservation (the test_worker idiom)."""
    approvals: dict = {}
    # ≥ the reduced record's own request (4.5M tokens / 6 invocations /
    # concurrency 4) — a smaller run budget is refused by ``validate_budget_
    # command`` at ``persist_and_reserve_candidate``, before any execution.
    run_budget = RunBudget(ledger_id="led-pvaux", max_tokens=9_000_000,
                           max_llm_invocations=64, max_concurrency=8)
    service = PlanAdmissionService(
        run_id=reduced.draft.run_id,
        requests={env["request"].request_id: env["request"]},
        drafts={reduced.draft.id: reduced.draft},
        contexts={env["context"].content_digest: env["context"]},
        attestations={}, approvals=approvals, catalog=bundle.runtime,
        bridge_view=view, phase1_registry=env["registry"],
        runtime_registry_digest=env["registry"].registry_digest,
        profile=STATIC_RUNTIME_PROFILE_V2, stores=env["stores"],
        run_budget=run_budget, clock=_FixedClock())
    prep = service.prepare_candidate(
        reduced.draft.id, request_id=env["request"].request_id)
    assert prep.support_report.supported, [i.code for i in prep.support_report.issues]
    cand, res = service.persist_and_reserve_candidate(
        prep, idempotency_key="pvaux-reserve-1")
    cd = cand.candidate_plan_digest
    approvals[(env["request"].request_id, cd)] = PlanApproval(
        request_id=env["request"].request_id, candidate_plan_digest=cd,
        decision=ApprovalDecision.APPROVED, actor_id="human-pvaux",
        decided_at=NOW)
    ev = service.record_approval(
        cd, ApprovalSubmission(
            request_id=env["request"].request_id, candidate_plan_digest=cd,
            decision=ApprovalDecision.APPROVED),
        authenticated_actor="human-pvaux", idempotency_key="pvaux-approve-1")
    plan, _admitted_ev = admit_after_approval(
        admission=service, candidate_id=cd, reservation_id=res.reservation_id,
        approval_event_id=ev.event_id, idempotency_key="pvaux-freeze-1")
    dispatch = service.verify_for_dispatch(plan.plan_digest)
    return SimpleNamespace(service=service, plan=plan, reservation=res,
                           dispatch=dispatch, run_budget=run_budget)


@pytest.fixture(scope="module")
def admitted(env, wired, reduced):
    return _admitted(env, wired.bundle, wired.view, reduced)


def _execute_aux_node(env, wired, admitted, worker_id: str, *, attempt: int = 1):
    """Drive ONE pv aux node through the REAL ``worker.execute_node``."""
    bundle, view = wired.bundle, wired.view
    plan = admitted.plan
    node = next(n for n in plan.nodes if n.worker_id == worker_id)
    worker = env["by_id"][worker_id]
    runtime = W.ExecutionRuntime(
        catalog=bundle.runtime, bridge_view=view,
        factories=bundle.factories,
        support_report=admitted.dispatch.support_report,
        runtime_registry_digest=env["registry"].registry_digest)
    sequencer = W.ExecutionEvidenceSequencer(node_id=node.id, attempt=attempt)
    resolver = W.ExecutionBridgeResolver(
        runtime=runtime, node=node, worker=worker, sequencer=sequencer)
    assert resolver.required_bridge_ids == ("data.runtime",)
    prepared = resolver.prepare_input(
        plan=plan, context_snapshot_ref=env["ctx_typed"],
        evidence_writer=_ForbiddenEvidenceWriter())

    summaries = [s for s in admitted.dispatch.support_report.bridge_support_summaries
                 if s.node_id == node.id and s.bridge_id == "data.runtime"]
    assert len(summaries) == 1
    refusals = _RefusalSpy()
    gateway = W.CapabilityGateway(
        plan_digest=plan.plan_digest, worker=worker,
        summaries={s.summary_digest: s for s in summaries},
        catalog=bundle.runtime, factories=TrustedFactoryRegistry(bundle.runtime),
        phase1_registry=env["registry"], refusal_sink=refusals,
        clock=_FixedClock(), sequencer=sequencer)

    input_snapshot = InputSnapshot.build(
        snapshot_id=f"isnap-{node.id}-{attempt}", run_id=plan.run_id,
        plan_id=plan.plan_id, plan_digest=plan.plan_digest, node_id=node.id,
        layer_index=0, attempt=attempt, context_snapshot_ref=env["ctx_typed"],
        artifact_inputs=(), data_result_refs=(), memory_record_refs=(),
        readiness="ready", built_at=NOW)
    ledger = BudgetLedger(
        sink=env["stores"].budget_event_sink(
            run_id=plan.run_id, ledger_id=admitted.run_budget.ledger_id),
        run_budget=admitted.run_budget)
    node_res = ledger.reserve_node(
        plan_reservation_id=admitted.reservation.reservation_id, node_id=node.id,
        attempt=attempt, tokens=1000, llm_invocations=0, concurrency=1,
        idempotency_key=f"pvaux-node-res-{node.id}-{attempt}")
    run_ctx = RunContext(
        run_id=plan.run_id, data=env["context"].data_context,
        context_snapshot_id=env["context"].snapshot_id,
        memory_snapshot_hash=env["context"].memory_snapshot_hash,
        budget=admitted.run_budget, cancellation_token_id="cancel-pvaux")

    node_run, artifact = W.execute_node(
        plan, node, runtime=runtime, prepared_bridges=prepared,
        input_snapshot=input_snapshot, ctx=run_ctx, node_reservation=node_res,
        bridge_resolver=resolver, model_gateway=None,
        capability_gateway=gateway, registry=env["registry"],
        stores=env["stores"], clock=_FixedClock(), attempt=attempt)
    return SimpleNamespace(node_run=node_run, artifact=artifact,
                           refusals=refusals, gateway=gateway, node=node)


@pytest.fixture(scope="module")
def executed(env, wired, admitted):
    """ONE real ``execute_node`` per aux node, reused by every assertion.

    Module-scoped on purpose: each drive draws a live node reservation against
    the plan reservation's concurrency (the admitted draft asks for 4), and a
    per-test re-drive would exhaust it — the harness must not spend budget it
    does not need."""
    return {wid: _execute_aux_node(env, wired, admitted, wid)
            for wid in sorted(PV_AUX)}


class TestTheAuxNodeOutcome:
    """Granted rows are blocking: an invalid committed data world stops the
    node before the deterministic handler can pretend that no evidence exists."""

    @pytest.mark.parametrize("worker_id", sorted(PV_AUX))
    def test_the_aux_node_blocks_on_an_unusable_committed_data_context(
            self, executed, worker_id):
        out = executed[worker_id]
        run = out.node_run
        assert run.status is NodeStatus.FAILED
        assert out.artifact is None
        assert run.error_type == "DataRuntimeError"
        assert run.reason_code == "bridge_execution_error"
        assert "predates the production data world" in run.reason
        assert "re-run Lane 0" in run.reason
        assert UNRESOLVED_HANDLER_TEXT not in run.reason

    @pytest.mark.parametrize("worker_id", sorted(PV_AUX))
    def test_the_blocking_bridge_refusal_creates_no_fake_data_or_evidence(
            self, executed, worker_id):
        """The precondition, re-proven at the node seam (not borrowed): the
        data bridge contributed NOTHING — zero tool records, zero data results,
        zero evidence, zero gateway begins — so the refusal really is 'no data
        reached this node' and not a swallowed read.  The
        ``_ForbiddenEvidenceWriter`` handed to ``prepare_input`` is the other
        half: any persisted byte on this path is an AssertionError."""
        out = executed[worker_id]
        run = out.node_run
        assert run.tool_call_records == ()
        assert run.data_result_refs == ()
        assert run.execution_evidence_refs == ()
        assert out.gateway.finalized_records() == ()
        assert out.refusals.records == []
        assert run.input_tokens == 0 and run.output_tokens == 0

    def test_both_rows_are_blocking_not_licensed_empty(self, executed):
        for worker_id in sorted(PV_AUX):
            run = executed[worker_id].node_run
            assert run.status is NodeStatus.FAILED
            assert run.reason_code == "bridge_execution_error"

    def test_neither_failure_can_be_misread_as_a_completed_empty_read(self, executed):
        for worker_id in sorted(PV_AUX):
            run = executed[worker_id].node_run
            assert run.error_type != "AuxDataUngranted"
            assert "no data reached" not in run.reason


class TestTheHandlerUnit:
    """The refusal is the handler's own, not an accident of the harness."""

    def _handler(self, env, bundle, worker_id: str):
        worker = env["by_id"][worker_id]
        factory = bundle.factories.handler_factory(worker.execution.handler_ref)
        return factory(worker=worker, resolved=None)

    def _typed_handler(self, env, worker_id: str, payloads):
        worker = env["by_id"][worker_id]
        reader = _PayloadReader({object_id: payload
                                 for object_id, payload in payloads})
        factory = PV.build_pv_aux_handler_factory(
            worker_id, payload_reader=reader)
        return factory(worker=worker, resolved=None)

    @pytest.mark.parametrize("worker_id", sorted(PV_AUX))
    def test_no_rows_raises_the_typed_ungranted_refusal(
            self, env, bundle, worker_id):
        handler = self._handler(env, bundle, worker_id)
        with pytest.raises(PV.AuxDataUngranted) as exc:
            handler(node=None, input_snapshot=None, contributions=(),
                    data_result_refs=())
        assert exc.value.reason_code == PV.AUX_DATA_UNGRANTED_CODE
        assert exc.value.worker_id == worker_id

    @pytest.mark.parametrize("worker_id", sorted(PV_AUX))
    def test_a_row_that_DID_arrive_is_its_own_typed_refusal_not_a_report(
            self, env, bundle, worker_id):
        """The L3 tripwire: the day a grant lands, the L2-b handler has no code
        to read it — and says so, rather than emitting a report that ignores
        the data it was handed."""
        handler = self._handler(env, bundle, worker_id)
        with pytest.raises(PV.AuxComputeNotWired) as exc:
            handler(node=None, input_snapshot=None, contributions=(),
                    data_result_refs=(SimpleNamespace(),))
        assert exc.value.reason_code == PV.AUX_COMPUTE_NOT_WIRED_CODE
        assert "payload reader" in str(exc.value)

    def test_both_refusals_share_one_typed_base(self):
        assert issubclass(PV.AuxDataUngranted, PV.PvAuxHandlerError)
        assert issubclass(PV.AuxComputeNotWired, PV.PvAuxHandlerError)

    def test_price_action_computes_the_reviewed_feature_set_from_typed_ohlcv(
            self, env):
        result = _ohlcv_result()
        handler = self._typed_handler(
            env, "pv.price_action", (("ohlcv-result", result),))
        model_result = handler(
            node=None, input_snapshot=None, contributions=(),
            data_result_refs=(_typed_result(result, "ohlcv-result"),))

        report = model_result.payload
        assert report.feature_set_version == "pa-15key-v1"
        assert set(report.features) == {
            "body", "close_pos", "ema20_rel", "inside_streak",
            "lower_wick", "range_atr", "upper_wick", "vol_ratio",
        }
        assert all(isinstance(value, float) for value in report.features.values())
        assert model_result.model == "compute_pa_features"

    def test_microstructure_computes_real_l1_and_keeps_optional_warnings(
            self, env):
        anchor, indicators = _micro_results()
        handler = self._typed_handler(
            env, "pv.microstructure",
            (("snapshot-result", anchor), ("indicator-result", indicators)))
        model_result = handler(
            node=None, input_snapshot=None, contributions=(),
            data_result_refs=(
                _typed_result(anchor, "snapshot-result"),
                _typed_result(indicators, "indicator-result"),
            ))

        report = model_result.payload
        assert report.l1_spread_bp == pytest.approx(2.5)
        assert report.bid_ask_imbalance == pytest.approx(0.25)
        assert report.break_ratio is None and report.whale_net_inflow is None
        assert report.degradation == (
            "tick_tape_unavailable", "fundflow_unavailable")
        assert "盘后保留快照" not in report.narrative
        assert model_result.model == "tdx_orderbook_projection"

    def test_microstructure_marks_a_consistent_retained_book_as_non_live(self, env):
        revision = (
            POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX
            + "2026-08-28T15:26:50.406000+08:00"
        )
        anchor, indicators = _micro_results(revision_ids=(revision, revision))
        handler = self._typed_handler(
            env, "pv.microstructure",
            (("snapshot-result", anchor), ("indicator-result", indicators)))

        model_result = handler(
            node=None, input_snapshot=None, contributions=(),
            data_result_refs=(
                _typed_result(anchor, "snapshot-result"),
                _typed_result(indicators, "indicator-result"),
            ))

        assert model_result.payload.degradation == (
            "tick_tape_unavailable", "fundflow_unavailable",
            "post_close_retained_book")
        assert "盘后保留快照" in model_result.payload.narrative
        assert "不可用于下单" in model_result.payload.narrative
        assert "not live, not for orders" in model_result.rendered_text

    @pytest.mark.parametrize("revision_ids", [
        (
            POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX
            + "2026-08-28T15:26:50+08:00",
            None,
        ),
        (
            POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX
            + "2026-08-28T15:26:50+08:00",
            POST_CLOSE_RETAINED_BOOK_REVISION_PREFIX
            + "2026-08-28T15:26:51+08:00",
        ),
    ])
    def test_microstructure_refuses_inconsistent_l1_observation_revisions(
            self, env, revision_ids):
        anchor, indicators = _micro_results(revision_ids=revision_ids)
        handler = self._typed_handler(
            env, "pv.microstructure",
            (("snapshot-result", anchor), ("indicator-result", indicators)))

        with pytest.raises(PV.AuxEvidenceInsufficient, match="inconsistent"):
            handler(
                node=None, input_snapshot=None, contributions=(),
                data_result_refs=(
                    _typed_result(anchor, "snapshot-result"),
                    _typed_result(indicators, "indicator-result"),
                ))

    def test_microstructure_refuses_an_incomplete_l1_pair(self, env):
        anchor, indicators = _micro_results(complete_l1=False)
        handler = self._typed_handler(
            env, "pv.microstructure",
            (("snapshot-result", anchor), ("indicator-result", indicators)))
        with pytest.raises(PV.AuxEvidenceInsufficient, match="bid_ask_imbalance"):
            handler(
                node=None, input_snapshot=None, contributions=(),
                data_result_refs=(
                    _typed_result(anchor, "snapshot-result"),
                    _typed_result(indicators, "indicator-result"),
                ))

    def test_the_wrapper_never_fabricates_a_capability_list(self, bundle, env):
        """A factory handed no worker cannot name what the seat holds, so it
        refuses at BIND time rather than inventing an allowlist."""
        ref = env["by_id"]["pv.price_action"].execution.handler_ref
        factory = bundle.factories.handler_factory(ref)
        with pytest.raises(PV.PvAuxHandlerError):
            factory(worker=None, resolved=None)

    def test_the_wrapper_binds_the_reviewed_compute_spines(self):
        """The production wrapper now consumes typed rows and binds the shared
        price-action implementation; sealed material is still never executed."""
        import ast
        from pathlib import Path

        tree = ast.parse(Path(PV.__file__).read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert "guanlan_v2.seats.price_action" in modules
        assert "guanlan_v2.orchestration.data.source" in modules
        assert "guanlan_v2.orchestration.lane_payloads" in modules
        # the material bytes are a sealed CONTRACT artifact, never a loader —
        # asserted on CALLS, so the docstring may keep naming what it forbids.
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                called.add(fn.id if isinstance(fn, ast.Name)
                           else getattr(fn, "attr", ""))
        for banned in ("exec", "eval", "compile", "read_bytes", "read_text",
                       "import_module"):
            assert banned not in called, banned


# =========================================================================== #
# (c) the reduced graph keeps every retained evidence face on the verdict path  #
# =========================================================================== #
class TestTheTrunkIsUnharmed:
    """Reduced is full-minus technical/news, not an evidence-bypassing graph."""

    TRUNK = ("sentiment", "research-mgr", "pm", "trader")

    def test_the_trunk_edges_are_exactly_the_sealed_spine(self, reduced):
        edges = {
            n.id: tuple(d.upstream_node_id for d in n.dependencies)
            for n in reduced.draft.nodes if n.id in self.TRUNK
        }
        assert edges == {
            "sentiment": (),
            "research-mgr": (
                "price-action", "microstructure", "sentiment", "bull-r1", "bear-r1"),
            "pm": ("research-mgr", "sentiment"),
            "trader": ("pm",),
        }

    def test_research_manager_blocks_on_both_retained_aux_nodes(self, reduced, env):
        aux_ids = {n.id for n in reduced.draft.nodes
                   if n.worker_id in PV_AUX}
        assert len(aux_ids) == 2
        manager = next(n for n in reduced.draft.nodes if n.id == "research-mgr")
        aux_dependencies = {
            dependency.upstream_node_id: dependency.policy
            for dependency in manager.dependencies
            if dependency.upstream_node_id in aux_ids
        }
        assert aux_dependencies == {
            "price-action": DependencyPolicy.BLOCK,
            "microstructure": DependencyPolicy.BLOCK,
        }

    def test_the_aux_nodes_are_not_sinks_so_their_failure_cannot_fail_the_run(
            self, reduced):
        aux_ids = {n.id for n in reduced.draft.nodes if n.worker_id in PV_AUX}
        assert not (set(reduced.draft.sink_node_ids) & aux_ids)
        assert "trader" in reduced.draft.sink_node_ids

    def test_the_full_graph_blocks_on_both_real_aux_evidence_faces(
            self, full_draft):
        """The full ten-seat verdict cannot survive either critical aux face."""
        aux_ids = {n.id for n in full_draft.nodes if n.worker_id in PV_AUX}
        consuming = [
            (n.id, d.upstream_node_id, d.policy)
            for n in full_draft.nodes for d in n.dependencies
            if d.upstream_node_id in aux_ids
        ]
        assert consuming == [
            ("technical", "price-action", DependencyPolicy.BLOCK),
            ("research-mgr", "price-action", DependencyPolicy.BLOCK),
            ("research-mgr", "microstructure", DependencyPolicy.BLOCK),
        ]

    def test_the_admitted_plan_keeps_the_trunk_order(self, admitted):
        """Through the REAL admission of the same draft: the layering the
        executor walks still puts the spine in order and the aux nodes on layer
        zero beside sentiment."""
        order = [n.id for n in admitted.plan.nodes]
        for earlier, later in zip(self.TRUNK, self.TRUNK[1:]):
            assert order.index(earlier) < order.index(later)


# =========================================================================== #
# (d) the absence is STATED downstream — never imagined                        #
# =========================================================================== #
class TestTheAbsenceReachesTheDebate:
    """The 0c601b5 inliner machinery, driven on the REAL debate seats.

    HONEST SCOPING (verified against both committed presets): in the REDUCED
    preset the two aux nodes feed no node at all, so their failure reaches no
    prompt — which is exactly why the run degrades without blocking.  In the
    SEALED ten-node preset ``price-action`` reaches the debate one hop away
    (``price-action`` --DEGRADE--> ``technical`` --DEGRADE--> ``dec.bull`` /
    ``dec.bear``), so the aux failure surfaces as a weakened-away input on the
    debate seats.  Both hops run through the SAME shared mechanism
    (``resolve_trusted_artifact_blocks`` + ``trusted_upstream_channel_section``),
    and this is the pin that it states the absence rather than omitting it."""

    def _blocks(self, env, full_draft, node_id: str, worker_id: str):
        """The downstream node's trusted-upstream blocks with EVERY fed input
        weakened away — the state a FAILED upstream produces (the pool is
        ``None`` on purpose: an absent slot needs no pool read, which is itself
        the inliner pin's property)."""
        node = next(n for n in full_draft.nodes if n.id == node_id)
        worker = env["by_id"][worker_id]
        snapshot = InputSnapshot.build(
            snapshot_id=f"isnap-{node_id}", run_id=full_draft.run_id,
            plan_id=full_draft.id, plan_digest="ab" * 32, node_id=node_id,
            layer_index=1, attempt=1, context_snapshot_ref=env["ctx_typed"],
            artifact_inputs=(), data_result_refs=(), memory_record_refs=(),
            readiness="ready", built_at=NOW)
        return W.resolve_trusted_artifact_blocks(
            node=node, worker=worker, input_snapshot=snapshot, pool=None)

    @pytest.mark.parametrize("node_id,worker_id",
                             [("bull-r1", "dec.bull"), ("bear-r1", "dec.bear")])
    def test_the_weakened_away_upstream_is_stated_absent_to_the_debate_seat(
            self, env, full_draft, node_id, worker_id):
        blocks = self._blocks(env, full_draft, node_id, worker_id)
        by_slot = {b.inject_as: b for b in blocks}
        assert "technical" in by_slot, sorted(by_slot)
        tech = by_slot["technical"]
        assert tech.status == "absent"
        assert tech.content is None and tech.artifact_digest is None
        section = W.trusted_upstream_channel_section(blocks)
        entry = next(e for e in section["blocks"] if e["inject_as"] == "technical")
        assert entry["status"] == "absent"
        assert entry["content"] == W.TRUSTED_UPSTREAM_ABSENT_TEXT
        assert "never invent" in W.TRUSTED_UPSTREAM_ABSENT_TEXT
        # the statement travels in the rendered channel, not just the object.
        assert "technical" in json.dumps(section, ensure_ascii=False)

    def test_the_technical_seat_is_told_the_aux_reading_is_absent(
            self, env, full_draft):
        """One hop up: ``pv.technical``'s own ``price_action`` slot — fed by the
        FAILING aux node — is the block that states the absence first."""
        blocks = self._blocks(env, full_draft, "technical", "pv.technical")
        by_slot = {b.inject_as: b for b in blocks}
        assert by_slot["price_action"].status == "absent"
        section = W.trusted_upstream_channel_section(blocks)
        entry = next(e for e in section["blocks"]
                     if e["inject_as"] == "price_action")
        assert entry["content"] == W.TRUSTED_UPSTREAM_ABSENT_TEXT
