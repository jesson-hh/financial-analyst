# -*- coding: utf-8 -*-
"""2026-07-31 controller ruling — pm's TWO bridges: the last trunk blockers.

The live deep run ``deep-a06fd33840c0b3ee`` (833509, reduced preset, HEAD with
the experience ruling applied) completed sentiment / bull-r1 / bear-r1 /
research-mgr (4 real LLM invocations — the experience empty-contribution ruling
held in production) and then ``dec.pm`` died at bridge prepare::

    reason_code = "bridge_preparation_failed"
    reason      = "no trusted provider factory bound for bridge 'data.runtime':
                   no handler factory bound for bridge.data_runtime.provider@1"

with ``dec.trader`` blocked behind it and the two pv aux nodes showing the same
data message. ``dec.pm`` activates BOTH ``data.runtime`` (its
``cap.data.verified_snapshot`` allowlist entry, priority 100) and
``memory.runtime`` (its ``memory`` read category, priority 200), and
``ExecutionBridgeResolver._resolve_required_set`` requires a bound factory for
EVERY active bridge. Two rulings close it:

* **Ruling 1 — memory provider registration.** ``make_memory_bridge_provider_
  factory`` (C3-discriminated since Task 11 / d265f48) had zero production call
  sites. ``memory/runtime.py::register_phase3_memory_provider_factory`` is the
  ONE reviewed recipe; ``live_decide.build_production_bindings`` calls it on the
  bundle factories. The production prefetch binding has ZERO rows, so dec.pm is
  the C3 ROWLESS reader: honest empty prefetch, empty contribution, stores never
  touched (pinned below with a poisoned stores object).

* **Ruling 2 — data provider, honest-empty ONLY for structurally-dead rows.**
  ``data/runtime.py::StructurallyDeadRowDataProvider`` serves the deep lane. Its
  ``open_execution`` discriminates NARROWLY: a row whose param bindings are
  unresolvable BY CONSTRUCTION (``node_param``-sourced pointers against a
  ``params_schema_ref=None`` worker — defect H's exact pinned shape, decidable
  statically from the catalog with no execution attempt) freezes with an EMPTY
  completed contribution, zero gateway begins, attributable through the summary
  digest plus a logged named fact. EVERYTHING ELSE stays LOUD: a resolvable row
  (the real L2-b gap — no production ``DataRuntimeWorld`` is bound), an
  allowlisted worker without a row (the two pv aux nodes keep degrading), a
  drifted handle, a foreign config.

Everything runs against the REAL sealed Phase-9 catalog through the REAL
production assembly; zero network, zero LLM, zero ``var/`` writes.

Run from repo root:
``python -m pytest tests/orchestration/test_pm_two_bridges.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.bootstrap as bs
import guanlan_v2.orchestration.data.runtime as RT
import guanlan_v2.orchestration.memory.runtime as MR
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
from guanlan_v2.orchestration.data.catalog import (
    DataBridgePrefetchBinding,
    DataPrefetchOperation,
    ParamBinding,
    phase3_data_surface,
    serialize_prefetch_binding,
)
from guanlan_v2.orchestration.enums import ApprovalPolicy, ExecutionKind
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.memory.catalog import phase3_memory_surface
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_support import (
    STATIC_RUNTIME_PROFILE_V2,
    check_runtime_support,
)
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    compute_candidate_plan_digest,
    validate_plan_draft,
)
from guanlan_v2.orchestration.pipeline.assembly import (
    PRODUCTION_PRESETS_DIR,
    build_production_catalog_runtime,
    load_phase10_preset_registry,
    production_bridge_view,
)
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline.deep_decide import (
    REDUCED_DEEP_DECIDE_PRESET_ID,
    materialize_deep_decide_draft,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
PHASE8_REGISTRY_DIGEST = (
    "d719e19bc8c64f56324ee36ca0d3aa039e5eac1c9488d80babe6ddce81e5e089"
)

#: the exact live failure string (run deep-a06fd33840c0b3ee, node pm — and,
#: before the experience ruling, the pv aux nodes of deep-8eb9afef6e9a5e48).
LIVE_PM_FAILURE_REASON = (
    "no trusted provider factory bound for bridge 'data.runtime': "
    "no handler factory bound for bridge.data_runtime.provider@1"
)


class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _ForbiddenEvidenceWriter:
    """pm's empty bridge layer must never persist evidence — any touch is RED."""

    def put(self, **kwargs):  # pragma: no cover - failing is the assertion
        raise AssertionError("evidence write attempted by an empty bridge path")

    def record_existing(self, **kwargs):  # pragma: no cover
        raise AssertionError("evidence write attempted by an empty bridge path")


class _PoisonedStores:
    """The rowless memory path must never touch the stores — any attribute
    access is the failure (least privilege, proven not asserted)."""

    def __getattr__(self, name):  # pragma: no cover - failing is the assertion
        raise AssertionError(f"stores.{name} touched by the rowless memory path")


# =========================================================================== #
# fixtures — the REAL sealed catalog through the REAL production assembly       #
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
    ctx_ref = PayloadRef(
        namespace="main", object_id="ctx-pmtwo-1",
        content_digest=context.content_digest)
    request = OrchestrationRequest(
        request_id="req-pmtwo-1", goal="观澜 · 深链 pm 双桥裁决",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    return {
        "registry": registry, "snapshot": snapshot, "context": context,
        "ctx_ref": ctx_ref, "request": request,
    }


@pytest.fixture(scope="module")
def bundle(env):
    """The RAW production bundle — exactly what ``build_production_catalog_runtime``
    returns before any registration."""
    return build_production_catalog_runtime(env["snapshot"])


@pytest.fixture(scope="module")
def view(bundle):
    return production_bridge_view(bundle.runtime)


def _subject_ref(code: str = "833509") -> TypedPayloadRef:
    subject = RunSubject(code=code, as_of=NOW)
    return TypedPayloadRef(
        payload_ref=PayloadRef(
            namespace="main", object_id=f"subject-{code}",
            content_digest=subject.semantic_digest()),
        schema_ref=SchemaRef(name="RunSubject", version="1"))


@pytest.fixture(scope="module")
def reduced(env, bundle, view):
    """The materialized reduced draft + its REAL support report (the live shape)."""
    presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    materialized = materialize_deep_decide_draft(
        request=env["request"], preset_registry=presets,
        preset_id=REDUCED_DEEP_DECIDE_PRESET_ID,
        context_snapshot_ref=env["ctx_ref"], subject_ref=_subject_ref(),
        clock=_FixedClock(), context=env["context"], catalog=env["snapshot"],
        schema_registry=env["registry"], draft_id="draft-pmtwo-1",
        run_id="run-pmtwo-1")
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


def _exec_runtime(bundle, view, report, *, factories=None):
    return W.ExecutionRuntime(
        catalog=bundle.runtime, bridge_view=view,
        factories=factories if factories is not None else bundle.factories,
        support_report=report,
        runtime_registry_digest="r" * 64)


def _resolver(runtime, draft, snapshot, node_id: str):
    node = next(n for n in draft.nodes if n.id == node_id)
    worker = {w.id: w for w in snapshot.workers}[node.worker_id]
    sequencer = W.ExecutionEvidenceSequencer(node_id=node_id, attempt=1)
    return W.ExecutionBridgeResolver(
        runtime=runtime, node=node, worker=worker, sequencer=sequencer)


def _production_registered_factories(bundle, env, *, stores=None):
    """EXACTLY the three reviewed recipes ``build_production_bindings`` calls,
    on a fresh registry over the same sealed runtime (the seam test below
    proves production calls these very recipes on the runner's bundle)."""
    factories = TrustedFactoryRegistry(bundle.runtime)
    bs.register_lane0_experience_factories(
        factories=factories, catalog=bs.load_lane0_catalog(), pool=None,
        registry=env["registry"], experience_views=(),
        experience_scaler=None, as_of=NOW)
    MR.register_phase3_memory_provider_factory(
        factories=factories,
        stores=stores if stores is not None else _PoisonedStores())
    RT.register_structurally_dead_row_data_provider(factories=factories)
    return factories


def _summary_for(report, node_id: str, bridge_id: str):
    mine = [s for s in report.bridge_support_summaries
            if s.bridge_id == bridge_id and s.node_id == node_id]
    assert len(mine) == 1
    return mine[0]


class _RefusalSpy:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kwargs) -> None:
        self.records.append(kwargs)


def _real_gateway(env, reduced, bundle, worker, summaries, sequencer):
    """A REAL CapabilityGateway over pm's real summaries — the untouched door."""
    refusals = _RefusalSpy()
    gateway = W.CapabilityGateway(
        plan_digest=compute_candidate_plan_digest(
            request=env["request"], draft=reduced.draft,
            context_content_digest=env["context"].content_digest),
        worker=worker, summaries={s.summary_digest: s for s in summaries},
        catalog=bundle.runtime,
        factories=TrustedFactoryRegistry(bundle.runtime),
        phase1_registry=env["registry"], refusal_sink=refusals,
        clock=_FixedClock(), sequencer=sequencer)
    gateway.mark_running()
    return gateway, refusals


def _worker_of(env, worker_id: str):
    return {w.id: w for w in env["snapshot"].workers}[worker_id]


def _node_for_worker(draft, worker_id: str):
    return next(n for n in draft.nodes if n.worker_id == worker_id)


# =========================================================================== #
# 0. the live failure, verbatim — the permanent control                         #
# =========================================================================== #
class TestTheLiveFailureControl:
    def test_pm_dies_naming_data_runtime_on_the_raw_production_bundle(
            self, bundle, view, reduced, env):
        """The CONTROL (run deep-a06fd33840c0b3ee): a production bundle WITHOUT
        the pm rulings' registrations refuses pm's resolver with the exact live
        NodeRun reason — data.runtime first (priority 100 before memory's 200).
        (``dag.py`` maps this PreflightError to ``bridge_preparation_failed``.)
        """
        runtime = _exec_runtime(bundle, view, reduced.report)
        with pytest.raises(W.PreflightError) as exc_info:
            _resolver(runtime, reduced.draft, env["snapshot"], "pm")
        assert str(exc_info.value) == LIVE_PM_FAILURE_REASON


# =========================================================================== #
# 1. the production registration — proven at the RUNNER's execution seam        #
# =========================================================================== #
class TestTheProductionRegistrationSeam:
    def test_build_production_decide_fn_registers_both_providers_on_the_runner_bundle(
            self, monkeypatch, tmp_path):
        """The load-bearing regression, driven through the REAL
        ``build_production_decide_fn`` → ``build_production_bindings`` →
        ``plan_runner_factory`` chain (never the bindings object alone):

        1. the ONE catalog bundle is built once and BOTH provider recipes land
           on ITS ``factories`` (plus the experience pair — four bindings);
        2. invoking ``bindings.plan_runner`` hands ``build_production_plan_
           runner`` THAT SAME bundle object (``catalog is bundle`` — identity,
           not equality), whose ``_plan_executor`` builds ``ExecutionRuntime``
           over ``bundle.factories`` (assembly.py) — so the registration and
           the execution seam cannot be two different registries;
        3. the constructed runner is the real launcher's (it refuses an
           undeclared digest at its own seam), not a husk.
        """
        from guanlan_v2 import orch_store_status as status_mod
        from guanlan_v2.orchestration.adapters import durable as durable_mod
        from guanlan_v2.orchestration.adapters.durable import (
            build_durable_runtime_stores,
        )
        from guanlan_v2.orchestration.adapters.launcher import (
            LaneExecutionBinding,
            LaunchRefused,
        )
        from guanlan_v2.orchestration.pipeline import assembly, live_decide

        stores = build_durable_runtime_stores(tmp_path / "orch")
        monkeypatch.setattr(durable_mod, "process_durable_stores", lambda: stores)
        monkeypatch.setattr(status_mod, "orchestration_store_bound", lambda: True)

        bundles: list = []
        real_bpcr = assembly.build_production_catalog_runtime

        def spy_bpcr(snapshot, **kw):
            built = real_bpcr(snapshot, **kw)
            bundles.append(built)
            return built

        monkeypatch.setattr(assembly, "build_production_catalog_runtime", spy_bpcr)

        runner_kwargs: dict = {}
        real_bppr = assembly.build_production_plan_runner

        def spy_bppr(**kw):
            runner_kwargs.update(kw)
            return real_bppr(**kw)

        monkeypatch.setattr(assembly, "build_production_plan_runner", spy_bppr)

        captured: dict = {}
        real_make = live_decide.make_orchestrated_decide

        def spy_make(*, fast_decide, bindings):
            captured["bindings"] = bindings
            return real_make(fast_decide=fast_decide, bindings=bindings)

        monkeypatch.setattr(live_decide, "make_orchestrated_decide", spy_make)

        fn = live_decide.build_production_decide_fn()
        assert callable(fn)
        assert len(bundles) == 1, "exactly ONE production bundle per binding build"
        built = bundles[0]

        data_factory = built.factories.handler_factory(
            phase3_data_surface().provider_ref)
        provider = data_factory(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(provider, RT.StructurallyDeadRowDataProvider)
        mem_factory = built.factories.handler_factory(
            phase3_memory_surface().provider_ref)
        mem_provider = mem_factory(
            bridge=SimpleNamespace(bridge_id="memory.runtime", priority=200),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(mem_provider, MR.MemoryRuntimeBridgeProvider)
        lane0 = bs.load_lane0_catalog()
        assert built.factories.handler_factory(
            lane0.refs["lane0.experience.provider"])
        assert built.factories.capability_backend_factory(lane0.capability_ref)

        binding = LaneExecutionBinding(
            lane="main", candidate_plan_digest="d" * 64,
            reservation_id="res-pmtwo", approval_event_id="ev-pmtwo")
        runner = captured["bindings"].plan_runner(
            admission=object(), lane_bindings={"main": binding},
            run_context_factory=lambda **kw: None,
            request_id="req-pmtwo-runner", prompt_assembler=None)
        assert runner_kwargs["catalog"] is built, (
            "the per-run runner must receive the SAME bundle object the "
            "providers were registered on — a different registry at the "
            "execution seam is exactly the live-contradiction class")
        assert callable(runner)
        with pytest.raises(LaunchRefused, match="no declared lane binding"):
            runner(lane="main", point=SimpleNamespace(point_ordinal=0),
                   approval=None, data_context=None, memory_binding=None,
                   candidate_plan_digest="0" * 64)

    def test_the_recipe_refs_are_the_phase9_resolved_bridge_refs(self, view):
        """Candidate (b) closed empirically: the refs the recipes register
        under ARE what the resolver looks up at execution (name/version/digest
        triple equality against the SEALED catalog's resolved bridges) — and
        the memory recipe's config bytes are the sealed guardrail bytes."""
        from guanlan_v2.orchestration.memory.catalog import (
            serialize_memory_prefetch_binding,
        )

        rb_data = view.resolve("data.runtime")
        rb_mem = view.resolve("memory.runtime")
        assert rb_data.provider_ref == phase3_data_surface().provider_ref
        assert rb_mem.provider_ref == phase3_memory_surface().provider_ref
        assert rb_data.config_bytes == serialize_prefetch_binding(
            phase3_data_surface().prefetch_binding)
        assert rb_mem.config_bytes == serialize_memory_prefetch_binding(
            phase3_memory_surface().prefetch_binding)


# =========================================================================== #
# 2. pm PREPARES and its whole bridge layer completes EMPTY                     #
# =========================================================================== #
class TestPmBridgeLayerCompletesEmpty:
    def test_pm_resolver_now_constructs_with_both_bridges(
            self, bundle, view, reduced, env):
        """THE FIX at the exact live seam (was: the control's PreflightError)."""
        factories = _production_registered_factories(bundle, env)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        resolver = _resolver(runtime, reduced.draft, env["snapshot"], "pm")
        assert resolver.required_bridge_ids == ("data.runtime", "memory.runtime")

    def test_pm_prepare_and_freeze_are_the_two_empty_attributable_contributions(
            self, bundle, view, reduced, env):
        """Stage 1 + stage 2 through the REAL resolver over a REAL gateway:
        two prepared-empty handles, two EMPTY completed contributions (each
        attributable through its own analyzer summary digest), ZERO gateway
        begins, zero finalized records, zero refusals, zero evidence writes."""
        factories = _production_registered_factories(bundle, env)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        node = next(n for n in reduced.draft.nodes if n.id == "pm")
        worker = _worker_of(env, "dec.pm")
        sequencer = W.ExecutionEvidenceSequencer(node_id="pm", attempt=1)
        resolver = W.ExecutionBridgeResolver(
            runtime=runtime, node=node, worker=worker, sequencer=sequencer)
        prepared = resolver.prepare_input(
            plan=None, context_snapshot_ref=None,
            evidence_writer=_ForbiddenEvidenceWriter())
        assert [h.bridge_id for h in prepared.handles] == [
            "data.runtime", "memory.runtime"]
        for handle in prepared.handles:
            assert handle.input_contribution == W.BridgeInputContribution()

        summaries = (
            _summary_for(reduced.report, "pm", "data.runtime"),
            _summary_for(reduced.report, "pm", "memory.runtime"))
        gateway, refusals = _real_gateway(
            env, reduced, bundle, worker, summaries, sequencer)
        contributions = resolver.open_execution(
            plan=None, prepared=prepared, input_snapshot=None,
            capability_gateway=gateway,
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            kind=ExecutionKind.LLM)
        assert [c.bridge_id for c in contributions] == [
            "data.runtime", "memory.runtime"]
        for c in contributions:
            assert c.tool_call_records == ()
            assert c.data_result_refs == ()
            assert c.direct_evidence_refs == ()
            assert c.untrusted_blocks == ()
        assert {c.summary_digest for c in contributions} == {
            s.summary_digest for s in summaries}
        assert gateway.begun_count() == 0
        assert gateway.finalized_records() == ()
        assert refusals.records == []

    def test_the_real_pm_summaries_license_the_empty_contributions(
            self, reduced):
        """Arithmetic honesty, verified against the REAL support report (not
        asserted from the briefing): pm's data summary has ``min == 0`` (the
        one granted row is ``cache_or_invoke`` + ``success_requires_finalized_
        call=False`` ⇒ ``row_min_finalized == 0``) so zero finalized calls pass
        the executor's success bound (worker.py ``charge.finalized < min``);
        the memory summary is the C3 zero-bounds one."""
        data_summary = _summary_for(reduced.report, "pm", "data.runtime")
        assert data_summary.min_finalized_tool_calls_on_success == 0
        assert data_summary.max_capability_invocations == 1
        row = phase3_data_surface().prefetch_binding.operations[0]
        assert row.worker_id == "dec.pm"
        assert row.invocation_mode == "cache_or_invoke"
        assert row.success_requires_finalized_call is False
        assert row.row_min_finalized == 0

        mem_summary = _summary_for(reduced.report, "pm", "memory.runtime")
        assert mem_summary.min_finalized_tool_calls_on_success == 0
        assert mem_summary.max_capability_invocations == 0
        assert mem_summary.allowed_capability_refs == ()


# =========================================================================== #
# 3. ruling 2 — the NARROW structurally-dead-row discrimination                 #
# =========================================================================== #
class TestStructurallyDeadRowDiscrimination:
    @pytest.fixture()
    def parts(self, env, view, reduced):
        worker = _worker_of(env, "dec.pm")
        rb = view.resolve("data.runtime")
        summary = _summary_for(reduced.report, "pm", "data.runtime")
        node = next(n for n in reduced.draft.nodes if n.id == "pm")
        sequencer = W.ExecutionEvidenceSequencer(node_id="pm", attempt=1)
        token = sequencer.issue_call_token(
            bridge_priority=rb.priority, bridge_id=rb.bridge_id,
            summary_digest=summary.summary_digest)
        provider = RT.structurally_dead_row_data_provider_factory()(
            bridge=rb, summary=summary)
        return SimpleNamespace(
            worker=worker, rb=rb, summary=summary, node=node,
            sequencer=sequencer, token=token, provider=provider)

    def _empty_handle(self, parts):
        return W.PreparedBridgeHandle(
            bridge_id="data.runtime", bridge_priority=parts.rb.priority,
            summary_digest=parts.summary.summary_digest, token=parts.token,
            input_contribution=W.BridgeInputContribution())

    def _open_request(self, parts, *, worker=None, bridge=None, handle=None):
        return SimpleNamespace(
            plan=None, node=parts.node,
            worker=worker if worker is not None else parts.worker,
            bridge=bridge if bridge is not None else parts.rb,
            summary=parts.summary,
            handle=handle if handle is not None else self._empty_handle(parts),
            input_snapshot=None, capability_gateway=None,
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            sequencer=parts.sequencer)

    # -- the dead shape, decided statically ---------------------------------- #
    def test_the_pm_row_is_dead_by_construction(self, env):
        """Defect H's exact shape on the REAL sealed row + REAL worker —
        decidable with zero execution attempts."""
        worker = _worker_of(env, "dec.pm")
        assert worker.params_schema_ref is None
        rows = phase3_data_surface().prefetch_binding.operations
        assert [r.worker_id for r in rows] == ["dec.pm"]
        assert [(b.target_pointer, b.source_kind, b.source_pointer)
                for b in rows[0].param_bindings] == [
            ("/as_of", "node_param", "/asof_date"),
            ("/symbols", "node_param", "/code")]
        assert RT._row_is_structurally_dead(rows[0], worker) is True

    def test_dead_row_freeze_is_empty_with_the_named_fact_and_untouched_gateway(
            self, env, reduced, bundle, parts, caplog):
        import logging

        gateway, refusals = _real_gateway(
            env, reduced, bundle, parts.worker, (parts.summary,), parts.sequencer)
        prepared = parts.provider.prepare_input(
            SimpleNamespace(token=parts.token,
                            evidence_writer=_ForbiddenEvidenceWriter()))
        assert prepared.status == "prepared"
        assert prepared.prepared_handle.input_contribution == \
            W.BridgeInputContribution()
        session = parts.provider.open_execution(self._open_request(
            parts, handle=prepared.prepared_handle))
        with caplog.at_level(logging.WARNING,
                             logger="guanlan_v2.orchestration.data.runtime"):
            outcome = session.freeze_for_execution(kind=ExecutionKind.LLM)
        assert outcome.status == "completed"
        c = outcome.frozen_contribution
        assert c is not None
        assert c.bridge_id == "data.runtime"
        assert c.bridge_priority == parts.rb.priority
        # attribution: the analyzer summary's digest — never a fabricated row.
        assert c.summary_digest == parts.summary.summary_digest
        assert c.tool_call_records == ()
        assert c.data_result_refs == ()
        assert c.direct_evidence_refs == ()
        assert c.untrusted_blocks == ()
        # the gateway was NEVER touched — zero begun/finalized/refused.
        assert gateway.begun_count() == 0
        assert gateway.finalized_records() == ()
        assert refusals.records == []
        # the NAMED FACT: the row, why it cannot run, and that no read ran.
        fact = "\n".join(caplog.messages)
        assert "DECLARED but NOT RUNNABLE by construction" in fact
        assert "'dec.pm'" in fact and "'verified_snapshot'" in fact
        assert "/asof_date" in fact and "/code" in fact
        assert "params_schema_ref=None" in fact
        assert "no data read was attempted" in fact

    # -- every OTHER shape stays LOUD (the discrimination cannot widen) ------- #
    def test_a_resolvable_row_still_refuses_loudly(self, parts):
        """The real L2-b gap must keep firing: a row whose params are
        resolvable BY CONSTRUCTION (const-bound) refuses — the worldless
        provider never fakes a data read."""
        sealed = phase3_data_surface().prefetch_binding.operations[0]
        live_row = DataPrefetchOperation(
            worker_id="dec.pm", method_ref=sealed.method_ref,
            capability_ref=sealed.capability_ref,
            frozen_route=sealed.frozen_route,
            invocation_mode="cache_or_invoke",
            param_bindings=(
                ParamBinding(target_pointer="/as_of", source_kind="const",
                             const_value="2026-07-24"),
                ParamBinding(target_pointer="/symbols", source_kind="const",
                             const_value=["833509"]),
            ))
        config = serialize_prefetch_binding(DataBridgePrefetchBinding.build(
            bridge_id="data.runtime", bridge_version="1",
            operations=(live_row,)))
        bridge = SimpleNamespace(bridge_id="data.runtime",
                                 priority=parts.rb.priority,
                                 config_bytes=config)
        provider = RT.structurally_dead_row_data_provider_factory()(
            bridge=bridge, summary=parts.summary)
        with pytest.raises(RT.DataRuntimeError,
                           match="L2-b.*never fakes a data read"):
            provider.open_execution(self._open_request(parts, bridge=bridge))

    def test_the_same_dead_row_on_a_params_carrying_worker_still_refuses(
            self, parts):
        """The predicate keys on the WORKER fact too: the sealed node_param row
        against a worker that DOES declare a params schema is resolvable in a
        dynamic plan — loud, never empty."""
        carrying = SimpleNamespace(
            id="dec.pm", params_schema_ref=SchemaRef(name="X", version="1"))
        with pytest.raises(RT.DataRuntimeError, match="resolvable as written"):
            parts.provider.open_execution(
                self._open_request(parts, worker=carrying))

    def test_an_allowlisted_worker_without_a_row_still_refuses_loudly(
            self, env, view, reduced, parts):
        """The two pv aux nodes' shape: capability-activated, rowless — LOUD
        (they keep degrading; conscious flip: the refusal now fires at bridge
        EXECUTION — ``bridge_execution_error`` — instead of bridge prepare,
        because the factory is bound)."""
        for wid in ("pv.price_action", "pv.microstructure"):
            worker = _worker_of(env, wid)
            assert tuple(rb.bridge_id
                         for rb in view.active_bridges_for(worker)) == (
                "data.runtime",)
            with pytest.raises(RT.DataRuntimeError,
                               match="no reviewed row.*L2-b"):
                parts.provider.open_execution(
                    self._open_request(parts, worker=worker))

    def test_a_drifted_prepared_handle_is_refused(self, parts):
        dirty = W.PreparedBridgeHandle(
            bridge_id="data.runtime", bridge_priority=parts.rb.priority,
            summary_digest=parts.summary.summary_digest, token=parts.token,
            input_contribution=W.BridgeInputContribution(
                memory_evidence_refs=(SimpleNamespace(name="fake"),)))
        with pytest.raises(RT.DataRuntimeError, match="drift"):
            parts.provider.open_execution(
                self._open_request(parts, handle=dirty))

    def test_a_foreign_bridge_config_is_refused(self, parts):
        sealed = phase3_data_surface().prefetch_binding.operations[0]
        config = serialize_prefetch_binding(DataBridgePrefetchBinding.build(
            bridge_id="other.bridge", bridge_version="1",
            operations=(sealed,)))
        bridge = SimpleNamespace(bridge_id="other.bridge",
                                 priority=parts.rb.priority,
                                 config_bytes=config)
        provider = RT.structurally_dead_row_data_provider_factory()(
            bridge=parts.rb, summary=parts.summary)
        with pytest.raises(RT.DataRuntimeError, match="different bridge"):
            provider.open_execution(self._open_request(parts, bridge=bridge))

    def test_prepare_token_checks_mirror_the_world_bound_provider(self, parts):
        foreign = SimpleNamespace(
            bridge_id="memory.runtime",
            summary_digest=parts.summary.summary_digest)
        with pytest.raises(RT.DataRuntimeError, match="different bridge"):
            parts.provider.prepare_input(SimpleNamespace(
                token=foreign, evidence_writer=_ForbiddenEvidenceWriter()))
        unbound = SimpleNamespace(bridge_id="data.runtime",
                                  summary_digest="x" * 64)
        with pytest.raises(RT.DataRuntimeError, match="summary"):
            parts.provider.prepare_input(SimpleNamespace(
                token=unbound, evidence_writer=_ForbiddenEvidenceWriter()))


# =========================================================================== #
# 4. ruling 1 — pm takes the C3 ROWLESS memory branch (verified, then pinned)   #
# =========================================================================== #
class TestPmMemoryBranch:
    def test_pm_is_the_c3_rowless_reader_in_production(self, env):
        """WHICH branch pm takes, verified not assumed: the production binding
        carries ZERO rows, so dec.pm ('memory' read category, no reviewed
        query projection) is the C3 rowless worker — honest empty prefetch."""
        surface = phase3_memory_surface()
        assert surface.prefetch_binding.rows == ()
        worker = _worker_of(env, "dec.pm")
        assert "memory" in worker.read_categories

    def test_the_recipe_serves_pm_the_empty_prefetch_without_touching_stores(
            self, bundle, view, reduced, env):
        """The registered production provider, driven for pm: prepare → the
        empty handle; open+freeze → the empty completed contribution; the
        POISONED stores prove the rowless path performs zero store reads."""
        factories = TrustedFactoryRegistry(bundle.runtime)
        MR.register_phase3_memory_provider_factory(
            factories=factories, stores=_PoisonedStores())
        factory = factories.handler_factory(
            phase3_memory_surface().provider_ref)
        rb = view.resolve("memory.runtime")
        summary = _summary_for(reduced.report, "pm", "memory.runtime")
        worker = _worker_of(env, "dec.pm")
        node = next(n for n in reduced.draft.nodes if n.id == "pm")
        sequencer = W.ExecutionEvidenceSequencer(node_id="pm", attempt=1)
        token = sequencer.issue_call_token(
            bridge_priority=rb.priority, bridge_id=rb.bridge_id,
            summary_digest=summary.summary_digest)
        provider = factory(bridge=rb, summary=summary)
        assert isinstance(provider, MR.MemoryRuntimeBridgeProvider)
        prepared = provider.prepare_input(SimpleNamespace(
            plan=None, node=node, worker=worker, bridge=rb, summary=summary,
            token=token, context_snapshot_ref=None,
            evidence_writer=_ForbiddenEvidenceWriter()))
        assert prepared.status == "prepared"
        handle = prepared.prepared_handle
        assert handle.input_contribution == W.BridgeInputContribution()
        session = provider.open_execution(SimpleNamespace(
            plan=None, node=node, worker=worker, bridge=rb, summary=summary,
            handle=handle, input_snapshot=None, capability_gateway=None,
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            sequencer=sequencer))
        outcome = session.freeze_for_execution(kind=ExecutionKind.LLM)
        assert outcome.status == "completed"
        c = outcome.frozen_contribution
        assert c.bridge_id == "memory.runtime"
        assert c.summary_digest == summary.summary_digest
        assert c.tool_call_records == ()
        assert c.data_result_refs == ()
        assert c.direct_evidence_refs == ()
        assert c.untrusted_blocks == ()  # no RenderedMemoryBlock, llm included


# =========================================================================== #
# 5. the full-trunk shape + the aux nodes' kept degradation                     #
# =========================================================================== #
class TestFullTrunkShape:
    def test_research_mgr_pm_trader_resolver_shapes(
            self, bundle, view, reduced, env):
        """The next live run's bridge-layer shape: research-mgr → the empty
        experience contribution; pm → BOTH bridges prepared-empty; trader →
        no bridge at all. Three LLM seats left on the trunk."""
        factories = _production_registered_factories(bundle, env)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        shapes = {
            node_id: _resolver(runtime, reduced.draft, env["snapshot"],
                               node_id).required_bridge_ids
            for node_id in ("research-mgr", "pm", "trader")}
        assert shapes == {
            "research-mgr": ("experience.bridge",),
            "pm": ("data.runtime", "memory.runtime"),
            "trader": (),
        }

    def test_the_aux_nodes_prepare_but_refuse_loudly_at_execution(
            self, bundle, view, reduced, env):
        """The pv aux nodes keep DEGRADING (the chartered L2-b gap keeps
        firing) — now at bridge execution (``bridge_execution_error``) instead
        of bridge prepare, because the provider factory is bound. Their
        failure stays loud; nothing silently succeeds."""
        factories = _production_registered_factories(bundle, env)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        for wid in ("pv.price_action", "pv.microstructure"):
            node = _node_for_worker(reduced.draft, wid)
            worker = _worker_of(env, wid)
            sequencer = W.ExecutionEvidenceSequencer(node_id=node.id, attempt=1)
            resolver = W.ExecutionBridgeResolver(
                runtime=runtime, node=node, worker=worker, sequencer=sequencer)
            assert resolver.required_bridge_ids == ("data.runtime",)
            prepared = resolver.prepare_input(
                plan=None, context_snapshot_ref=None,
                evidence_writer=_ForbiddenEvidenceWriter())
            with pytest.raises(RT.DataRuntimeError, match="L2-b"):
                resolver.open_execution(
                    plan=None, prepared=prepared, input_snapshot=None,
                    capability_gateway=None,
                    evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
                    kind=ExecutionKind.DETERMINISTIC)
