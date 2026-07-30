# -*- coding: utf-8 -*-
"""2026-07-31 controller ruling — the deep chain's decision trunk, both causes.

The live deep run ``deep-8eb9afef6e9a5e48`` (300308, reduced preset) completed
its three debate-layer LLM nodes and then died at ``dec.research_mgr`` with the
verbatim NodeRun::

    reason_code = "bridge_preparation_failed"
    reason      = "no trusted provider factory bound for bridge
                   'experience.bridge': no handler factory bound for
                   lane0.experience.provider@1"

Two stacked causes, both ruled and both fixed here:

* **Cause 1** — the deep production runtime never bound the experience-bridge
  provider factory: the sealed catalog's ``experience.bridge`` activates for
  ``dec.research_mgr`` (the ``experience_cases`` read category), and
  ``ExecutionBridgeResolver._resolve_required_set`` requires a trusted factory
  for EVERY active bridge. The fix binds it through the SAME reviewed
  registration recipe the Lane-0 driver uses
  (:func:`bootstrap.register_lane0_experience_factories`, the extracted
  experience half of ``register_bootstrap_runtime_factories`` — one recipe, no
  fork), called by ``live_decide.build_production_bindings`` on the production
  bundle's factories. The DATA bridge's provider deliberately stays UNBOUND
  (the chartered L2-b gap) — pinned below.

* **Cause 2** — once bound, the provider's freeze path invoked
  ``experience.retrieve`` unconditionally while the ruled analyzer half
  (bootstrap.py, ruling C) gives a non-granted worker a ZERO-contribution
  summary (``max_capability_invocations=0``, empty capability set) — so the
  gateway refused and the node died late. The controller extended the C3
  discrimination to the experience provider half, mirroring
  ``memory/runtime.py`` (the C3 "both halves mirrored" precedent): a worker
  whose allowlist does NOT hold the experience capability freezes with an
  EMPTY, attributable contribution (the zero-bounds summary's digest rides it;
  zero invocations, zero evidence, never a fabricated case row and no
  empty-sentinel block — retrieval never ran); an allowlisted worker keeps
  today's behavior bit-for-bit (``test_bootstrap_e2e`` remains the granted-path
  proof), and the allowlisted-but-rowless ANALYZER branch stays LOUD
  (``test_pipeline_deep_preset.py::TestFullPhase9BridgeView``).

Everything here runs against the REAL sealed Phase-9 catalog / registry through
the REAL production assembly (``build_production_catalog_runtime`` +
``production_bridge_view``); zero network, zero LLM, zero ``var/`` writes.

Run from repo root:
``python -m pytest tests/orchestration/test_experience_provider_discrimination.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.bootstrap as bs
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.catalog_runtime import CatalogMaterialError
from guanlan_v2.orchestration.enums import ApprovalPolicy, ExecutionKind
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
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

#: the exact live failure string (run deep-8eb9afef6e9a5e48, node research-mgr).
LIVE_FAILURE_REASON = (
    "no trusted provider factory bound for bridge 'experience.bridge': "
    "no handler factory bound for lane0.experience.provider@1"
)
#: the exact live failure string of the two pv aux nodes (same run, node
#: price-action) — and, once research-mgr completes, the FIRST refusal dec.pm
#: hits (the chartered L2-b gap; see TestTheNamedRemainingGap).
LIVE_DATA_FAILURE_REASON = (
    "no trusted provider factory bound for bridge 'data.runtime': "
    "no handler factory bound for bridge.data_runtime.provider@1"
)


class _FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _ForbiddenEvidenceWriter:
    """A non-granted freeze must never persist evidence — any touch is a RED."""

    def put(self, **kwargs):  # pragma: no cover - failing is the assertion
        raise AssertionError(
            "evidence write attempted by the non-granted empty freeze")

    def record_existing(self, **kwargs):  # pragma: no cover
        raise AssertionError(
            "evidence write attempted by the non-granted empty freeze")


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
        namespace="main", object_id="ctx-expdisc-1",
        content_digest=context.content_digest)
    request = OrchestrationRequest(
        request_id="req-expdisc-1", goal="观澜 · 深链经验桥判别 (ruling)",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    return {
        "registry": registry, "snapshot": snapshot, "context": context,
        "ctx_ref": ctx_ref, "request": request,
    }


@pytest.fixture(scope="module")
def bundle(env):
    """The RAW production bundle — exactly what ``build_production_catalog_runtime``
    returns before any registration (the live run's shape)."""
    return build_production_catalog_runtime(env["snapshot"])


@pytest.fixture(scope="module")
def view(bundle):
    return production_bridge_view(bundle.runtime)


def _subject_ref(code: str = "300308") -> TypedPayloadRef:
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
        schema_registry=env["registry"], draft_id="draft-expdisc-1",
        run_id="run-expdisc-1")
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


# =========================================================================== #
# 1. Cause 1 — the live failure, verbatim, and the reviewed binding             #
# =========================================================================== #
class TestTheLiveFailureAndTheBinding:
    def test_the_bridge_activation_facts_this_module_rests_on(self, bundle, view):
        """What I checked (empirical, not read off a doc): which bridges the
        sealed catalog activates for each decision-trunk worker."""
        workers = {w.id: w for w in bundle.snapshot.workers}

        def active(wid):
            return tuple(rb.bridge_id for rb in view.active_bridges_for(workers[wid]))

        assert active("dec.research_mgr") == ("experience.bridge",)
        # dec.pm: cap.data.verified_snapshot activates data.runtime (priority
        # 100) and the 'memory' read category activates memory.runtime (200).
        assert active("dec.pm") == ("data.runtime", "memory.runtime")
        assert active("dec.trader") == ()
        assert active("text.sentiment") == ()
        assert active("dec.bull") == ()
        assert active("dec.bear") == ()

    def test_the_raw_production_bundle_reproduces_the_live_failure_verbatim(
            self, bundle, view, reduced, env):
        """The CONTROL: ``build_production_catalog_runtime`` alone binds no
        experience provider, so the research-mgr resolver refuses with the
        exact live NodeRun reason. (``dag.py`` maps this PreflightError to the
        ``bridge_preparation_failed`` reason_code — dag.py:647/971.)"""
        runtime = _exec_runtime(bundle, view, reduced.report)
        with pytest.raises(W.PreflightError) as exc_info:
            _resolver(runtime, reduced.draft, env["snapshot"], "research-mgr")
        assert str(exc_info.value) == LIVE_FAILURE_REASON

    def test_the_reviewed_registration_binds_provider_and_backend(self, bundle, env):
        """The extracted driver recipe binds BOTH halves the driver binds:
        the provider handler by exact catalog ref and the ``experience.retrieve``
        capability backend by exact capability identity."""
        from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry

        lane0 = bs.load_lane0_catalog()
        factories = TrustedFactoryRegistry(bundle.runtime)
        bs.register_lane0_experience_factories(
            factories=factories, catalog=lane0, pool=None,
            registry=env["registry"], experience_views=(),
            experience_scaler=None, as_of=NOW)
        factory = factories.handler_factory(lane0.refs["lane0.experience.provider"])
        provider = factory(
            bridge=SimpleNamespace(bridge_id="experience.bridge", priority=10),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(provider, bs.Lane0ExperienceBridgeProvider)
        backend_factory = factories.capability_backend_factory(lane0.capability_ref)
        assert isinstance(backend_factory(), bs.ExperienceRetrievalBackend)

    def test_the_driver_recipe_routes_through_the_same_body(self, monkeypatch):
        """One recipe, no fork: ``register_bootstrap_runtime_factories`` (the
        Lane-0 driver's reviewed registration) performs its experience half by
        CALLING the extracted function — behaviorally spied, not source-grepped."""
        calls: list[dict] = []
        real = bs.register_lane0_experience_factories
        monkeypatch.setattr(
            bs, "register_lane0_experience_factories",
            lambda **kw: calls.append(kw) or real(**kw))
        monkeypatch.setattr(
            bs, "bootstrap_market_factor_handler",
            lambda **kw: (lambda **_kw: None))
        from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
        from guanlan_v2.orchestration.catalog_runtime import CatalogRuntime

        lane0 = bs.load_lane0_catalog()
        runtime = CatalogRuntime.build(lane0.snapshot, lane0.source)
        factories = TrustedFactoryRegistry(runtime)
        bs.register_bootstrap_runtime_factories(
            factories=factories, catalog=lane0, pool=None, registry=None,
            spec=None, inputs=None, as_of=NOW, experience_views=(),
            experience_scaler=None)
        assert len(calls) == 1
        assert calls[0]["factories"] is factories
        assert calls[0]["catalog"] is lane0
        # and the binding really landed through that one body:
        assert factories.handler_factory(lane0.refs["lane0.experience.provider"])

    def test_the_registered_runtime_serves_the_research_mgr_resolver(
            self, bundle, view, reduced, env):
        """The FIX, at the exact live seam: with the reviewed registration on a
        production-bundle factory registry, the research-mgr resolver constructs
        and derives exactly the experience bridge as its required set."""
        from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry

        factories = TrustedFactoryRegistry(bundle.runtime)
        bs.register_lane0_experience_factories(
            factories=factories, catalog=bs.load_lane0_catalog(), pool=None,
            registry=env["registry"], experience_views=(),
            experience_scaler=None, as_of=NOW)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        resolver = _resolver(runtime, reduced.draft, env["snapshot"], "research-mgr")
        assert resolver.required_bridge_ids == ("experience.bridge",)

    def test_build_production_bindings_registers_through_the_one_recipe(
            self, monkeypatch, tmp_path):
        """The deep seam (Cause 1 closed where the live run died): the
        production bindings register the experience provider on the SAME bundle
        factories the per-run plan runner executes over, via the extracted
        driver recipe — and the DATA provider stays honestly UNBOUND (the
        chartered L2-b gap; the two pv aux nodes keep degrading), as does the
        memory provider (no ruling covers it yet — see TestTheNamedRemainingGap).
        """
        from guanlan_v2 import orch_store_status as status_mod
        from guanlan_v2.orchestration.adapters import durable as durable_mod
        from guanlan_v2.orchestration.adapters.durable import (
            build_durable_runtime_stores,
        )
        from guanlan_v2.orchestration.data.catalog import phase3_data_surface
        from guanlan_v2.orchestration.memory.catalog import phase3_memory_surface
        from guanlan_v2.orchestration.pipeline import live_decide

        stores = build_durable_runtime_stores(tmp_path / "orch")
        monkeypatch.setattr(durable_mod, "process_durable_stores", lambda: stores)
        monkeypatch.setattr(status_mod, "orchestration_store_bound", lambda: True)

        calls: list[dict] = []
        real = bs.register_lane0_experience_factories
        monkeypatch.setattr(
            bs, "register_lane0_experience_factories",
            lambda **kw: calls.append(kw) or real(**kw))

        live_decide.build_production_bindings()

        assert len(calls) == 1, "the deep bindings must register exactly once"
        factories = calls[0]["factories"]
        lane0 = bs.load_lane0_catalog()
        # bound: the experience provider + backend (the ruled fix)…
        assert factories.handler_factory(lane0.refs["lane0.experience.provider"])
        assert factories.capability_backend_factory(lane0.capability_ref)
        # …and NOT bound: the data provider (chartered L2-b) and the memory
        # provider (unruled) — pinned so binding them is a conscious flip.
        with pytest.raises(CatalogMaterialError, match="no handler factory"):
            factories.handler_factory(phase3_data_surface().provider_ref)
        with pytest.raises(CatalogMaterialError, match="no handler factory"):
            factories.handler_factory(phase3_memory_surface().provider_ref)


# =========================================================================== #
# 2. Cause 2 — the ruled provider-half discrimination (C3 mirror)               #
# =========================================================================== #
def _zero_summary_for(report, node_id: str):
    mine = [s for s in report.bridge_support_summaries
            if s.bridge_id == "experience.bridge" and s.node_id == node_id]
    assert len(mine) == 1
    summary = mine[0]
    # the ruled analyzer facts (ruling C) the empty contribution is attributable to:
    assert summary.allowed_capability_refs == ()
    assert summary.max_capability_invocations == 0
    assert summary.min_finalized_tool_calls_on_success == 0
    return summary


class _RefusalSpy:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, **kwargs) -> None:
        self.records.append(kwargs)


def _real_gateway(env, reduced, bundle, worker, summary, sequencer):
    """A REAL CapabilityGateway over the zero summary — the closed door the
    pre-ruling provider died on (scenario-4's exact construction)."""
    from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry

    refusals = _RefusalSpy()
    gateway = W.CapabilityGateway(
        plan_digest=compute_candidate_plan_digest(
            request=env["request"], draft=reduced.draft,
            context_content_digest=env["context"].content_digest),
        worker=worker, summaries={summary.summary_digest: summary},
        catalog=bundle.runtime,
        factories=TrustedFactoryRegistry(bundle.runtime),
        phase1_registry=env["registry"], refusal_sink=refusals,
        clock=_FixedClock(), sequencer=sequencer)
    gateway.mark_running()
    return gateway, refusals


class TestNonGrantedProviderHalf:
    """The ruled discrimination, provider half — the C3 memory mirror
    (``memory/runtime.py`` rows==0 ⇒ prepared-empty ⇒ empty contribution)."""

    @pytest.fixture()
    def parts(self, env, view, reduced):
        worker = {w.id: w for w in env["snapshot"].workers}["dec.research_mgr"]
        # the discriminator's premise, asserted not assumed (the same pin the
        # analyzer's ruled branch rests on): no experience grant in the allowlist.
        cap_ref, _mat = bs.experience_retrieve_capability()
        assert all(
            (c.id, c.version, c.content_digest)
            != (cap_ref.id, cap_ref.version, cap_ref.content_digest)
            for c in worker.capability_allowlist)
        summary = _zero_summary_for(reduced.report, "research-mgr")
        rb = view.resolve("experience.bridge")
        node = next(n for n in reduced.draft.nodes if n.id == "research-mgr")
        sequencer = W.ExecutionEvidenceSequencer(node_id="research-mgr", attempt=1)
        token = sequencer.issue_call_token(
            bridge_priority=rb.priority, bridge_id=rb.bridge_id,
            summary_digest=summary.summary_digest)
        provider = bs.make_lane0_experience_bridge_factory(
            pool=None, registry=env["registry"], capability_ref=cap_ref,
            views=(), scaler=None, k=bs.BOOTSTRAP_EXPERIENCE_K, as_of=NOW,
        )(bridge=rb, summary=summary)
        return SimpleNamespace(
            worker=worker, summary=summary, rb=rb, node=node,
            sequencer=sequencer, token=token, provider=provider)

    def test_prepare_is_the_prepared_empty_handle(self, parts):
        """Stage 1 (already the ruled shape for every worker —
        ``pre_input_kind='none'``): a prepared handle with an all-empty input
        contribution and zero evidence writes."""
        outcome = parts.provider.prepare_input(
            SimpleNamespace(token=parts.token,
                            evidence_writer=_ForbiddenEvidenceWriter()))
        assert outcome.status == "prepared"
        handle = outcome.prepared_handle
        assert handle.bridge_id == "experience.bridge"
        assert handle.input_contribution == W.BridgeInputContribution()

    def test_non_granted_freeze_is_the_empty_attributable_contribution(
            self, env, reduced, bundle, parts):
        """The FIX (was: ``CapabilityGatewayError`` at the gateway's allowlist
        door): freeze completes with an EMPTY contribution — zero invocations,
        zero ToolCallRecords, zero evidence writes, zero untrusted blocks (no
        fabricated case row, and no empty-sentinel block either: retrieval
        never ran) — attributable through the zero-bounds analyzer summary's
        digest, exactly the memory rowless mirror."""
        gateway, refusals = _real_gateway(
            env, reduced, bundle, parts.worker, parts.summary, parts.sequencer)
        prepared = parts.provider.prepare_input(
            SimpleNamespace(token=parts.token,
                            evidence_writer=_ForbiddenEvidenceWriter()))
        session = parts.provider.open_execution(SimpleNamespace(
            plan=None, node=parts.node, worker=parts.worker, bridge=parts.rb,
            summary=parts.summary, handle=prepared.prepared_handle,
            input_snapshot=SimpleNamespace(artifact_inputs=()),
            capability_gateway=gateway,
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            sequencer=parts.sequencer))
        outcome = session.freeze_for_execution(kind=ExecutionKind.LLM)
        assert outcome.status == "completed"
        c = outcome.frozen_contribution
        assert c is not None
        assert c.bridge_id == "experience.bridge"
        assert c.bridge_priority == parts.rb.priority
        # the attribution: the zero-bounds analyzer summary, never a case row.
        assert c.summary_digest == parts.summary.summary_digest
        assert c.tool_call_records == ()
        assert c.data_result_refs == ()
        assert c.direct_evidence_refs == ()
        assert c.untrusted_blocks == ()
        # the gateway was never touched — zero begun, zero finalized, zero
        # refusal records (nothing was even attempted).
        assert gateway.begun_count() == 0
        assert gateway.finalized_records() == ()
        assert refusals.records == []

    def test_the_summary_consistency_is_not_an_accident(self, parts):
        """The tripwire making the discrimination unambiguous: the provider's
        predicate is the SAME membership test the ruled analyzer used, over the
        SAME capability identity — the catalog assembly cross-verifies the
        descriptor's activation refs against the in-code
        ``experience.retrieve`` identity (bootstrap.py ``assemble_lane0_catalog``),
        so 'not granted' at the provider is exactly the population that received
        the zero summary at admission."""
        lane0 = bs.load_lane0_catalog()
        cap_ref, _ = bs.experience_retrieve_capability()
        assert lane0.descriptor.activation_capability_refs == (cap_ref,)
        assert lane0.capability_ref == cap_ref
        # the production facts: exactly the two Lane-0 readers hold the grant;
        # no Phase-8 lane worker does.
        holders = sorted(
            w.id for w in chain.phase9_catalog_snapshot().workers
            if any(c.id == cap_ref.id for c in w.capability_allowlist))
        assert holders == ["market.regime", "market.rotation"]


class TestGrantedPathUnchanged:
    """The allowlisted worker keeps today's behavior bit-for-bit — the loud and
    the retrieval halves both stay owned by their existing pins:

    * granted retrieval (one finalized ``experience.retrieve`` per LLM node,
      empty selection ⇒ COMPLETED): ``test_bootstrap_e2e.py`` §4 — untouched.
    * allowlisted-but-rowless ANALYZER refusal stays LOUD:
      ``test_pipeline_deep_preset.py::TestFullPhase9BridgeView::
      test_an_allowlisted_worker_without_a_row_still_raises_loudly`` — untouched.

    Here: the structural pin that the granted branch still reaches the gateway
    (the discrimination can only ever remove the CALL for non-granted workers,
    never reroute a granted one)."""

    def test_a_granted_worker_still_invokes_the_gateway(self, env, view, reduced):
        lane0 = bs.load_lane0_catalog()
        worker = {w.id: w for w in env["snapshot"].workers}["market.regime"]
        cap_ref = lane0.capability_ref
        assert any(
            (c.id, c.version, c.content_digest)
            == (cap_ref.id, cap_ref.version, cap_ref.content_digest)
            for c in worker.capability_allowlist)
        rb = view.resolve("experience.bridge")
        summary = _zero_summary_for(reduced.report, "research-mgr")
        sequencer = W.ExecutionEvidenceSequencer(node_id="n-granted", attempt=1)
        token = sequencer.issue_call_token(
            bridge_priority=rb.priority, bridge_id=rb.bridge_id,
            summary_digest=summary.summary_digest)

        class _TouchedGateway:
            """begin() raising proves the granted branch still CALLS it —
            without executing a real retrieval in this unit probe."""

            def begin(self, **kwargs):
                raise _GatewayTouched()

        provider = bs.make_lane0_experience_bridge_factory(
            pool=None, registry=env["registry"], capability_ref=cap_ref,
            views=(), scaler=SimpleNamespace(feature_schema_version="probe-v1"),
            k=1, as_of=NOW)(bridge=rb, summary=summary)
        prepared = provider.prepare_input(
            SimpleNamespace(token=token,
                            evidence_writer=_ForbiddenEvidenceWriter()))
        node = SimpleNamespace(id="n-granted", params={}, dependencies=())
        session = provider.open_execution(SimpleNamespace(
            plan=None, node=node, worker=worker, bridge=rb, summary=summary,
            handle=prepared.prepared_handle,
            input_snapshot=SimpleNamespace(artifact_inputs=()),
            capability_gateway=_TouchedGateway(),
            evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
            sequencer=sequencer))
        with pytest.raises(_GatewayTouched):
            session.freeze_for_execution(kind=ExecutionKind.LLM)


class _GatewayTouched(Exception):
    """The granted-path probe's marker: the gateway WAS reached."""


# =========================================================================== #
# 3. the named remaining gap — what still blocks pm (and therefore trader)      #
# =========================================================================== #
class TestTheNamedRemainingGap:
    """CONSCIOUS PIN of the decision trunk's NEXT blocker, named not hidden.

    With Cause 1+2 fixed, ``research-mgr`` completes — but ``dec.pm`` activates
    ``data.runtime`` (its ``cap.data.verified_snapshot`` allowlist entry) and
    ``memory.runtime`` (its ``memory`` read category), and NEITHER provider
    factory is bound in the deep production runtime: ``data.runtime`` is the
    chartered L2-b gap (this controller ruling explicitly keeps it unbound —
    the two pv aux nodes keep degrading exactly as before), and the memory
    provider half (``memory/runtime.py``, C3-discriminated and ready) has no
    production registration ruling yet. So the next live run completes
    research-mgr and then dies at pm's bridge prepare with the data reason
    verbatim below (priority order: data 100 before memory 200); trader stays
    blocked behind it. Flipping this pin = consciously binding those providers.
    """

    def test_pm_is_still_refused_at_its_own_bridge_prepare(
            self, bundle, view, reduced, env):
        from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry

        factories = TrustedFactoryRegistry(bundle.runtime)
        bs.register_lane0_experience_factories(
            factories=factories, catalog=bs.load_lane0_catalog(), pool=None,
            registry=env["registry"], experience_views=(),
            experience_scaler=None, as_of=NOW)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        with pytest.raises(W.PreflightError) as exc_info:
            _resolver(runtime, reduced.draft, env["snapshot"], "pm")
        assert str(exc_info.value) == LIVE_DATA_FAILURE_REASON

    def test_trader_has_no_bridge_requirement_at_all(
            self, bundle, view, reduced, env):
        """Once pm produces, nothing at the bridge layer blocks trader."""
        from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry

        factories = TrustedFactoryRegistry(bundle.runtime)
        bs.register_lane0_experience_factories(
            factories=factories, catalog=bs.load_lane0_catalog(), pool=None,
            registry=env["registry"], experience_views=(),
            experience_scaler=None, as_of=NOW)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        resolver = _resolver(runtime, reduced.draft, env["snapshot"], "trader")
        assert resolver.required_bridge_ids == ()
