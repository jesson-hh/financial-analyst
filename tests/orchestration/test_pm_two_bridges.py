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

* **Ruling 2, twice consciously FLIPPED.** The original ruling completed
  honest-empty for the one dead-shape row (``node_param`` bindings against a
  ``params_schema_ref=None`` worker). **L1 Task 3** made that exact sealed row
  RESOLVABLE (the subject projection; defect H healed at the root) and retired
  the dead-row provider for a worldless stopgap with three LOUD shapes.
  **L2-b Tasks 4+5** bound a real ``DataRuntimeWorld`` and closed the per-run
  subject seam, so the stopgap was DELETED: ``dec.pm``'s row is now READ, and
  every remaining refusal on this path is structural (a context that predates
  the production data world) rather than a wiring gap.

**L2-b Task 4 — the registration supersede (conscious flip, both directions
recorded).** ``build_production_bindings`` no longer calls the worldless
registration recipe: the ONE recipe at the sealed
``bridge.data_runtime.provider@1`` identity is now
``adapters/data_world.py::register_production_data_provider``, so the REGISTERED
incumbent is ``ProductionDataProvider`` over a real per-run
``DataRuntimeWorld``. Two consequences are pinned below:

* the seam pins (``TestTheProductionRegistrationSeam``) assert
  ``ProductionDataProvider`` — pre-flip they asserted the worldless class;
* the pv aux nodes FREEZE the catalog-licensed EMPTY contribution instead of
  refusing loudly (``TestFullTrunkShape``) — the analyzer summed 0/0 bounds
  over zero prefetch rows, which is exactly what an empty completed
  ``BridgeContribution`` licenses. Shape 1's fate was chartered as L2-b's
  conscious flip and this is it.

**L2-b Task 5 — the L1<->L2-b integration seam (this file's second flip).**
The per-run ``_SubjectScopedFactories`` view's override target moved from the
worldless factory to ``adapters/data_world.py::production_data_provider_factory
(subject_params)``, and the worldless class + factory + registration recipe
were DELETED. Consequences here:

* ``TestPmDataBridgeUnderTheProductionRegistration`` replaces
  ``TestPmDataBridgeRefusesLoudly``: stage 1 (two prepared-empty handles) is
  re-pinned verbatim, stage 2 now reaches the REAL per-run world;
* the whole ``TestWorldlessProviderGuards`` unit class died WITH the class it
  constructed — its surviving semantics are re-homed and enumerated in
  ``data/test_subject_projection.py::TestTheTombstoneIsHonored::
  test_the_worldless_provider_names_survive_nowhere_outside_docs``, which also
  pins repo-wide that the three worldless names are gone;
* ``TestThePerRunSubjectScopedView`` now drives the world-bound subject-bound
  provider through the ACTUAL view object.

Everything runs against the REAL sealed Phase-9 catalog through the REAL
production assembly; zero network, zero LLM, zero ``var/`` writes.

Run from repo root:
``python -m pytest tests/orchestration/test_pm_two_bridges.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.adapters.data_world as DW
import guanlan_v2.orchestration.bootstrap as bs
import guanlan_v2.orchestration.data.runtime as RT
import guanlan_v2.orchestration.memory.runtime as MR
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
from guanlan_v2.orchestration.data.catalog import (
    data_capability_refs,
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
CTX_SR = SchemaRef(name="ContextSnapshot", version="1")

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
    # L2-b Task 5 (additive): the ContextSnapshot is PERSISTED, because the
    # world-bound provider's per-run resolver reads the ADMITTED snapshot back
    # through ``request.reader`` — the worldless incumbent never opened a
    # session, so a hand-built PayloadRef was enough for it. The ref's
    # content_digest is unchanged (payloads are content-addressed), so nothing
    # downstream of ``materialize_deep_decide_draft`` moves.
    ctx_ref = stores.payloads.put(
        CTX_SR, context, registry_digest=registry.registry_digest,
        namespace="main", idempotency_key="pmtwo:ctx")
    assert ctx_ref.content_digest == context.content_digest
    request = OrchestrationRequest(
        request_id="req-pmtwo-1", goal="观澜 · 深链 pm 双桥裁决",
        workflow="orchestrate_only", fallback_preset_id=None,
        approval_policy=ApprovalPolicy.REQUIRED)
    return {
        "registry": registry, "snapshot": snapshot, "context": context,
        "ctx_ref": ctx_ref,
        "ctx_typed": TypedPayloadRef(schema_ref=CTX_SR, payload_ref=ctx_ref),
        "request": request, "stores": stores,
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
    # L2-b Task 5 (additive): the plan identity the world-bound provider's
    # resolver keys its manifest lookup on (``plan.schema_registry_digest``).
    plan = SimpleNamespace(
        plan_digest=compute_candidate_plan_digest(
            request=env["request"], draft=draft,
            context_content_digest=env["context"].content_digest),
        catalog_digest=env["snapshot"].catalog_digest,
        schema_registry_digest=env["registry"].registry_digest)
    return SimpleNamespace(draft=draft, report=report, plan=plan)


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


def _production_registered_factories(bundle, env):
    """EXACTLY the three reviewed recipes ``build_production_bindings`` calls,
    on a fresh registry over the same sealed runtime (the seam test below
    proves production calls these very recipes on the runner's bundle).

    CONSCIOUS FLIP (L2-b Task 4): the data slot was L1's worldless
    registration recipe (which took a ``subject_params=`` knob); it is now the
    world-bound ``DW.register_production_data_provider``. The knob did NOT come
    along: since L2-b Task 5 ``ProductionDataProvider`` has a run-subject
    source, but only the per-run ``_SubjectScopedFactories`` view may bind it —
    the process-level recipe stays UNBOUND by design.

    The memory half keeps its POISONED stores default (its rowless branch must
    touch nothing); the data half is handed the module env's real stores,
    because its per-run ``world_for`` genuinely reads the admitted snapshot and
    the manifest through them."""
    factories = TrustedFactoryRegistry(bundle.runtime)
    bs.register_lane0_experience_factories(
        factories=factories, catalog=bs.load_lane0_catalog(), pool=None,
        registry=env["registry"], experience_views=(),
        experience_scaler=None, as_of=NOW)
    MR.register_phase3_memory_provider_factory(
        factories=factories, stores=_PoisonedStores())
    DW.register_production_data_provider(
        factories=factories, stores=env["stores"],
        schema_resolver=env["stores"].resolver, clock=_FixedClock(),
        catalog_runtime=bundle.runtime)
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
           over ``bundle.factories`` when the per-run subject projection is
           UNBOUND — and, as of L1 Task 4, over the thin per-run
           ``_SubjectScopedFactories`` view OVER that same registry when it is
           bound — so the registration and the execution seam cannot be two
           different registries (docstring flip recorded in the L1 Task 4
           report: identity when unbound, view-over-it when bound);
        3. the constructed runner is the real launcher's (it refuses an
           undeclared digest at its own seam), not a husk;
        4. (L1 Task 4, the seam echo) the production ``plan_runner_factory``
           THREADS ``subject_params`` through to ``build_production_plan_
           runner`` — by object identity, so the half-wired-kwarg defect class
           (accepted but dropped, the campaign's signature catch) reddens
           here — and its default stays an honest ``None`` (the process-level
           UNBOUND posture);
        5. (L2-b Task 4) the data provider bound on that bundle is the
           world-bound ``ProductionDataProvider`` — the CONSCIOUS FLIP of the
           pre-flip isinstance assertion against L1's worldless stopgap
           assertion — and all SEVEN data capability backends resolve on the
           SAME registry to the ONE ``production_data_backend()`` instance
           (gate D-A: the per-invocation ``lambda **kw`` shape hands the one
           thread-confined backend to every concurrent node thread);
        6. (L2-b Task 4) REGISTRATION PRECEDES BINDING RETURN: at the moment
           ``make_orchestrated_decide`` is handed the bindings — i.e. after
           ``build_production_bindings`` returned — the sealed data identity is
           ALREADY bound on the bundle. A lazily-registered provider would
           leave a window in which a lease could be drawn against an unbound
           bridge (the burned-lease precedent);
        7. (L2-b Task 4 review, I-1) BOTH refusal-sink wirings are pinned BY
           IDENTITY to the ONE hoisted ``production_data_refusal_audit_sink``:
           the world seam's ``refusal_audit_sink_factory=`` and the plan
           runner's ``refusal_sink=``. Deleting either kwarg is the
           half-wired-kwarg defect class (item 4's sibling) with a SILENT
           failure mode — the hoist would fall back to a fresh per-world
           unreachable sink, and the gateway to a sink over the BARE audit
           detail registry that answers ``UnknownAuditDetailSchema`` to the
           very ``DataFetchRefusalDetails@1`` a PitGuard refusal routes
           through it, masking the refusal it exists to audit.
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

        # (7) the refusal-sink identity pin: capture every sink the ONE recipe
        # builds during binding construction. The hoist means EXACTLY one.
        sinks: list = []
        real_sink_recipe = DW.production_data_refusal_audit_sink

        def spy_sink_recipe(clock):
            built_sink = real_sink_recipe(clock)
            sinks.append(built_sink)
            return built_sink

        monkeypatch.setattr(
            DW, "production_data_refusal_audit_sink", spy_sink_recipe)

        captured: dict = {}
        real_make = live_decide.make_orchestrated_decide

        def spy_make(*, fast_decide, bindings):
            captured["bindings"] = bindings
            # (6) ordering proof: build_production_bindings has RETURNED by the
            # time this runs, so the sealed data identity must already resolve.
            captured["data_bound_at_return"] = bool(
                bundles and bundles[0].factories.handler_factory(
                    phase3_data_surface().provider_ref))
            return real_make(fast_decide=fast_decide, bindings=bindings)

        monkeypatch.setattr(live_decide, "make_orchestrated_decide", spy_make)

        fn = live_decide.build_production_decide_fn()
        assert callable(fn)
        assert len(bundles) == 1, "exactly ONE production bundle per binding build"
        built = bundles[0]
        assert captured["data_bound_at_return"] is True, (
            "the data provider must be registered DURING binding construction; "
            "a lazy registration leaves a window in which a lease could be "
            "drawn against an unbound bridge")

        data_factory = built.factories.handler_factory(
            phase3_data_surface().provider_ref)
        provider = data_factory(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        # CONSCIOUS FLIP (L2-b Task 4). Pre-flip this asserted L1's
        # worldless stopgap class (deleted outright at Task 5).
        assert isinstance(provider, DW.ProductionDataProvider)
        # (7a) the WORLD seam's kwarg, by identity. Exactly ONE sink is built
        # per binding set (the ExperienceRetrievalBackend hoist idiom), and the
        # registered resolver's factory must hand out THAT object. Drop
        # `refusal_audit_sink_factory=` at the registration call and the
        # resolver falls back to `_default_refusal_audit_sink_factory`, whose
        # `build()` re-enters this spy and returns a FRESH per-world sink that
        # nothing outside the world ever holds — the `is` below reddens (and so
        # does the count, since the spy records the extra build).
        assert len(sinks) == 1, (
            "binding construction must build EXACTLY ONE data-aware refusal "
            "audit sink (hoisted, not one per world)")
        hoisted_sink = sinks[0]
        assert provider._resolver._sink_factory() is hoisted_sink, (
            "the production registration must hand the HOISTED sink through "
            "refusal_audit_sink_factory=; a fresh per-world sink records "
            "refusals nobody can ever read")
        assert len(sinks) == 1, "the hoisted seam must not rebuild the sink"
        # (5) the seven data capability backends, on the SAME registry, all the
        # ONE thread-confined instance (gate D-A's per-invocation shape).
        caps = data_capability_refs()
        assert len(caps) == 7
        one = DW.production_data_backend()
        for _method_id, cap_ref in sorted(caps.items()):
            factory = built.factories.capability_backend_factory(cap_ref)
            assert factory(capability_ref=cap_ref) is one
            assert factory(capability_ref=cap_ref) is one  # per invocation
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
        # L1 Task 4: a factory call that says nothing about the subject stays
        # honestly UNBOUND at the runner (never a defaulted projection).
        assert runner_kwargs.get("subject_params") is None
        # (7b) the RUNNER seam's kwarg, by identity — the same hoisted sink the
        # per-run worlds record through. Drop `refusal_sink=` from the
        # `build_production_plan_runner` call and assembly builds its own sink
        # over the BARE default_audit_detail_registry(), which answers
        # UnknownAuditDetailSchema to the DataFetchRefusalDetails@1 a PitGuard
        # candidate refusal routes through CapabilityGateway.reject — masking
        # the refusal instead of auditing it. Silent until a real refusal
        # fires in production, hence pinned by identity here.
        assert runner_kwargs.get("refusal_sink") is hoisted_sink, (
            "the capability gateway must share the data-aware refusal sink; "
            "the runner's own default sink cannot validate a data refusal")
        assert callable(runner)
        with pytest.raises(LaunchRefused, match="no declared lane binding"):
            runner(lane="main", point=SimpleNamespace(point_ordinal=0),
                   approval=None, data_context=None, memory_binding=None,
                   candidate_plan_digest="0" * 64)

        # L1 Task 4 — the seam ECHO (the anti-half-wired-kwarg pin): the
        # production plan_runner_factory must pass the run's stamped
        # projection through BY IDENTITY. A factory that accepts the kwarg
        # and drops it on the floor (mutation m2, the campaign's signature
        # defect class) reddens on the identity assert below.
        sentinel = RT.SubjectParams.project(code="600519", as_of=NOW)
        runner2 = captured["bindings"].plan_runner(
            admission=object(), lane_bindings={"main": binding},
            run_context_factory=lambda **kw: None,
            request_id="req-pmtwo-runner-2", prompt_assembler=None,
            subject_params=sentinel)
        assert callable(runner2)
        assert runner_kwargs["subject_params"] is sentinel

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

    def test_a_broken_data_recipe_kills_the_deep_lane_at_binding_construction(
            self, monkeypatch, tmp_path):
        """L2-b Task 4 (Task-3 review Minor 2 — the burned-lease precedent):
        a data-world recipe that cannot be built (missing calendar material,
        drifted material digest, unsealed registry) must kill
        ``build_production_bindings`` AT BINDING CONSTRUCTION — loudly,
        unmasked, before any bindings object exists and therefore before any
        coordinator, any admission, any ``register_and_try_lease``, any lease.

        The precedent is the 2026-07-31 live-store defect: an analyzer-binding
        refusal that fired inside ``plan_runner_factory`` consumed a human
        ``ApprovalLease`` and executed nothing. A recipe failure must never buy
        that trade again.
        """
        from guanlan_v2 import orch_store_status as status_mod
        from guanlan_v2.orchestration.adapters import durable as durable_mod
        from guanlan_v2.orchestration.adapters.durable import (
            build_durable_runtime_stores,
        )
        from guanlan_v2.orchestration.data.errors import RoutingConfigurationError
        from guanlan_v2.orchestration.pipeline import live_decide

        stores = build_durable_runtime_stores(tmp_path / "orch")
        monkeypatch.setattr(durable_mod, "process_durable_stores", lambda: stores)
        monkeypatch.setattr(status_mod, "orchestration_store_bound", lambda: True)

        boom = RoutingConfigurationError(
            "committed calendar material missing or unreadable at <probe>")

        def broken_recipe():
            raise boom

        monkeypatch.setattr(DW, "production_data_recipe", broken_recipe)

        # the bindings object itself is the thing a lease could ever hang off:
        # if it is ever CONSTRUCTED the failure was not at binding construction.
        built: list = []
        real_bindings = live_decide.DeepDecideBindings
        monkeypatch.setattr(
            live_decide, "DeepDecideBindings",
            lambda **kw: (built.append(kw) or real_bindings(**kw)))

        with pytest.raises(RoutingConfigurationError) as exc:
            live_decide.build_production_bindings()
        assert exc.value is boom, "the recipe failure must propagate UNMASKED"
        assert built == [], (
            "binding construction completed past the broken data recipe; the "
            "deep lane must die AT the registration, before any bindings "
            "object (hence any coordinator / admission / lease) exists")


# =========================================================================== #
# 2. pm PREPARES; the SURVIVING worldless class's refusal shapes (die Task 5)   #
# =========================================================================== #
class TestPmDataBridgeUnderTheProductionRegistration:
    """CONSCIOUS FLIP (L2-b Task 5; was ``TestPmDataBridgeRefusesLoudly``).

    | | pm's data bridge at stage 2, over what production registers |
    |---|---|
    | L1 (worldless incumbent) | no world existed ⇒ EVERY rows-present shape was a refusal: unbound → the runner-seam shape, bound → the resolved-but-no-world shape |
    | here (world-bound + the per-run subject seam closed) | pm's rows resolve a REAL per-run ``DataRuntimeWorld`` from the run's OWN admitted ``ContextSnapshot``; the refusal that survives is a **structural** one (this module's committed context is the PILOT-era placeholder quartet, so the world refuses with the re-run-Lane-0 remedy), not a wiring one |

    Stage 1 did NOT move and is re-pinned verbatim (two prepared-empty,
    I/O-free handles). The memory rowless-empty pins are UNTOUCHED
    (``TestPmMemoryBranch``). The subject-BOUND arm moved to
    ``TestThePerRunSubjectScopedView`` — after Task 5 the projection reaches
    the provider through the per-run VIEW, which is the seam that owns it, and
    no longer through a bound process-level registration (production's
    process-level registration is UNBOUND by design).

    The real-read end state (a recipe-built context + an injected fake probe ⇒
    one ``ToolCallRecord``) is pinned where its harness lives:
    ``test_production_data_provider.py::TestSyntheticLiveRowEndToEnd``."""

    def test_pm_resolver_now_constructs_with_both_bridges(
            self, bundle, view, reduced, env):
        """THE FIX at the exact live seam (was: the control's PreflightError).
        Driven over the L2-b PRODUCTION registration — the resolver's required
        set is a registration fact, not a provider-semantics fact."""
        factories = _production_registered_factories(bundle, env)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        resolver = _resolver(runtime, reduced.draft, env["snapshot"], "pm")
        assert resolver.required_bridge_ids == ("data.runtime", "memory.runtime")

    def test_pm_prepares_empty_then_the_bound_world_refuses_the_pilot_context(
            self, bundle, view, reduced, env):
        """Stage 1 is UNCHANGED (two prepared-empty handles, I/O-free); stage
        2 through the REAL resolver now reaches the REAL per-run world
        resolution and refuses on this module's PILOT-era committed context —
        with ZERO gateway begins, zero finalized records, zero refusals, zero
        evidence writes.

        The two negatives are the Task-5 discrimination: the message is NOT
        L1's runner-seam wiring cause (the per-run view is not in play here,
        but the process-level provider now opens a real session instead of
        refusing before one) and NOT the deleted worldless marker."""
        factories = _production_registered_factories(bundle, env)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        node = next(n for n in reduced.draft.nodes if n.id == "pm")
        worker = _worker_of(env, "dec.pm")
        sequencer = W.ExecutionEvidenceSequencer(node_id="pm", attempt=1)
        resolver = W.ExecutionBridgeResolver(
            runtime=runtime, node=node, worker=worker, sequencer=sequencer)
        prepared = resolver.prepare_input(
            plan=reduced.plan, context_snapshot_ref=env["ctx_typed"],
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
        with pytest.raises(RT.DataRuntimeError) as exc:
            resolver.open_execution(
                plan=reduced.plan, prepared=prepared,
                input_snapshot=SimpleNamespace(
                    context_snapshot_ref=env["ctx_typed"]),
                capability_gateway=gateway,
                evidence_writer=_ForbiddenEvidenceWriter(),
                reader=env["stores"].payloads, kind=ExecutionKind.LLM)
        msg = str(exc.value)
        assert "predates the production data world" in msg
        assert "re-run Lane 0" in msg
        assert "not bound at the runner seam" not in msg
        assert "params resolved from the run subject projection" not in msg
        assert gateway.begun_count() == 0
        assert gateway.finalized_records() == ()
        assert refusals.records == []

    def test_the_real_pm_summaries_still_license_zero_finalized_calls(
            self, reduced):
        """Arithmetic honesty, verified against the REAL support report (not
        asserted from the briefing): pm's data summary has ``min == 0`` (the
        one granted row is ``cache_or_invoke`` + ``success_requires_finalized_
        call=False`` ⇒ ``row_min_finalized == 0``); the memory summary is the
        C3 zero-bounds one. CONSCIOUS NOTE (updated at L2-b Task 5): these
        catalog facts never moved. Under L1's worldless incumbent the
        data-side license was unreachable (every rows-present shape refused
        before a bound could bite); with the world bound AND the per-run
        subject seam closed it is LIVE again — pm's one ``cache_or_invoke``
        row may honestly finalize zero calls (a cache-served read)."""
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
        """The bridge-layer resolver shape (unchanged facts): research-mgr →
        the empty experience contribution; pm → both bridges construct;
        trader → no bridge at all. CONSCIOUS NOTE (L2-b Task 4, was L1 Task 3's
        'pm FAILS loudly at bridge execution'): with the world-bound provider
        registered, pm's rows-present arm now resolves a real per-run
        ``DataRuntimeWorld``; it still cannot COMPLETE until Task 5 threads the
        run-subject source (the real session's ``_assemble_params`` refuses at
        the runner seam), so trader stays blocked honestly behind it. The
        resolver SHAPE below is unchanged by either flip."""
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

    def test_the_aux_nodes_freeze_the_catalog_licensed_empty(
            self, bundle, view, reduced, env):
        """CONSCIOUS FLIP (L2-b Task 4 — shape 1's chartered fate).

        PRE-FLIP (recorded): both pv aux nodes kept DEGRADING — the worldless
        provider raised ``DataRuntimeError`` matching ``"L2-b"`` at bridge
        execution (``bridge_execution_error``), and this test asserted that
        raise. POST-FLIP: the world-bound provider partitions on row COUNT
        only, and the sealed prefetch binding carries NO row for either aux
        worker, so the reviewed analyzer summed 0/0 bounds over them — an empty
        completed ``BridgeContribution`` is exactly what the sealed summary
        LICENSES. They now freeze the catalog-licensed EMPTY: no refusal, no
        degradation, and (proven, not assumed) zero gateway, zero reader, zero
        evidence writes — the world is not even resolved for a rowless worker.

        This is NOT the retired dead-row empty-complete: that one completed
        empty over a row that COULD have been read (a silenced outage). Here
        there is no row at all."""
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
            contributions = resolver.open_execution(
                plan=None, prepared=prepared, input_snapshot=None,
                capability_gateway=None,
                evidence_writer=_ForbiddenEvidenceWriter(), reader=None,
                kind=ExecutionKind.DETERMINISTIC)
            assert len(contributions) == 1
            c = contributions[0]
            assert c.bridge_id == "data.runtime"
            assert c.tool_call_records == ()
            assert c.data_result_refs == ()
            assert c.direct_evidence_refs == ()
            assert c.untrusted_blocks == ()

    def test_the_live_failure_string_never_occurs_on_the_bound_path(
            self, bundle, view, reduced, env):
        """The NEGATIVE pin kept from the pre-flip control (L2-b Task 4): the
        live ``deep-a06fd33840c0b3ee`` reason — the unbound-factory
        ``PreflightError`` that killed pm, trader and both aux nodes — must
        never occur once the production registration is bound, for ANY node of
        the reduced graph. ``TestTheLiveFailureControl`` keeps proving it still
        fires on a RAW bundle, so the string itself is not dead code."""
        factories = _production_registered_factories(bundle, env)
        runtime = _exec_runtime(bundle, view, reduced.report, factories=factories)
        for node in reduced.draft.nodes:
            try:
                _resolver(runtime, reduced.draft, env["snapshot"], node.id)
            except W.PreflightError as exc:  # pragma: no cover - the assertion
                pytest.fail(
                    f"node {node.id!r} still refuses at bridge prepare with "
                    f"{str(exc)!r}"
                    + (" — THE live failure string" if str(exc) ==
                       LIVE_PM_FAILURE_REASON else ""))


# =========================================================================== #
# 6. L1 Task 4 — the per-run subject-scoped factories view (seam pins)          #
# =========================================================================== #
class TestThePerRunSubjectScopedView:
    """The L1 Task 4 view over the REAL production bundle (design resolution
    R2): ``_plan_executor`` wraps ``bundle.factories`` in the thin per-run
    ``_SubjectScopedFactories`` view exactly when the run's projection is
    bound. These pins drive the view through the REAL
    ``ExecutionBridgeResolver`` over the REAL registered production registry
    (the ``_production_registered_factories`` recipes, process-level UNBOUND
    — the production posture).

    Delegation-family note (verified at source): ``cand.*`` handler
    identities are NOT ``kind='handler'`` entries of the sealed Phase-9
    production snapshot (the 24 handler entries are bridge/adapter/lane0/
    memory/pv/quant/x ids; Task 11's cand registration rides
    ``handler_registry`` on OTHER snapshots), so the delegation pins cover
    the identities THIS catalog actually holds — the memory provider, the
    experience pair — plus a same-family member registered THROUGH the view.

    L2-b Task 5 — the chartered hand-off, EXECUTED (both directions recorded).

    | | the view's override factory |
    |---|---|
    | L1 / L2-b Task 4 | L1's worldless factory, subject-bound — a provider with no world, whose every rows-present shape refused |
    | here | ``production_data_provider_factory(subject_params, stores=…, schema_resolver=…, clock=…, catalog_runtime=…)`` — the SAME ONE recipe ``register_production_data_provider`` binds process-level, differing in exactly one argument: the run's projection |

    That asymmetry was Task 4's recorded concern (a subject-BOUND run was
    strictly less capable than a subject-less one); it is closed here. The
    order-conditional guard that made the flip conscious went with the class
    it guarded."""

    @pytest.fixture()
    def base(self, bundle, env):
        return _production_registered_factories(bundle, env)

    @staticmethod
    def _scoped(base, sp, bundle, env):
        """The ACTUAL production seam object, over the ACTUAL Task-5 factory —
        the same argument set ``assembly._plan_executor`` binds from the
        runner's own ``stores`` / ``clock`` / ``bundle.runtime``."""
        from guanlan_v2.orchestration.pipeline import assembly

        return assembly._SubjectScopedFactories(
            base, provider_ref=phase3_data_surface().provider_ref,
            factory=DW.production_data_provider_factory(
                sp, stores=env["stores"],
                schema_resolver=env["stores"].resolver, clock=_FixedClock(),
                catalog_runtime=bundle.runtime))

    def test_the_view_hands_pm_the_subject_bound_production_provider(
            self, bundle, view, reduced, env, base):
        """Invariant 2, FLIPPED to the end state (was: the view constructed a
        subject-bound WORLDLESS provider and refused shape 2 naming the run's
        code).

        The REAL pm resolver over the per-run VIEW (base UNBOUND — exactly
        production's process-level state) now constructs a subject-BOUND
        ``ProductionDataProvider``: it carries the run's OWN projection object
        (identity, never a re-projection — mutation m1's red) and takes the
        rows arm into the REAL per-run world resolution. This module's
        committed context is the PILOT-era placeholder quartet, so the world
        refuses structurally with the re-run-Lane-0 remedy, before any gateway
        begin — and the two negatives prove the refusal is no longer either
        L1's wiring gap or the deleted worldless marker."""
        sp = RT.SubjectParams.project(code="833509", as_of=NOW)
        scoped = self._scoped(base, sp, bundle, env)
        provider = scoped.handler_factory(phase3_data_surface().provider_ref)(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(provider, DW.ProductionDataProvider)
        assert provider._subject_params is sp

        runtime = _exec_runtime(bundle, view, reduced.report, factories=scoped)
        node = next(n for n in reduced.draft.nodes if n.id == "pm")
        worker = _worker_of(env, "dec.pm")
        sequencer = W.ExecutionEvidenceSequencer(node_id="pm", attempt=1)
        resolver = W.ExecutionBridgeResolver(
            runtime=runtime, node=node, worker=worker, sequencer=sequencer)
        prepared = resolver.prepare_input(
            plan=reduced.plan, context_snapshot_ref=env["ctx_typed"],
            evidence_writer=_ForbiddenEvidenceWriter())
        summaries = (
            _summary_for(reduced.report, "pm", "data.runtime"),
            _summary_for(reduced.report, "pm", "memory.runtime"))
        gateway, refusals = _real_gateway(
            env, reduced, bundle, worker, summaries, sequencer)
        with pytest.raises(RT.DataRuntimeError) as exc:
            resolver.open_execution(
                plan=reduced.plan, prepared=prepared,
                input_snapshot=SimpleNamespace(
                    context_snapshot_ref=env["ctx_typed"]),
                capability_gateway=gateway,
                evidence_writer=_ForbiddenEvidenceWriter(),
                reader=env["stores"].payloads, kind=ExecutionKind.LLM)
        msg = str(exc.value)
        assert "predates the production data world" in msg
        assert "re-run Lane 0" in msg
        assert "not bound at the runner seam" not in msg
        assert "params resolved from the run subject projection" not in msg
        assert gateway.begun_count() == 0
        assert refusals.records == []

    def test_the_view_delegates_memory_experience_and_the_handler_family_identically(
            self, bundle, env, base):
        """Invariant 3: every non-data resolution through the view IS the
        base's — factory object identity, never a copy — and the sealed data
        ref is the ONE divergence. Mutation m1 (override ALL refs) reddens
        here."""
        sp = RT.SubjectParams.project(code="833509", as_of=NOW)
        scoped = self._scoped(base, sp, bundle, env)

        mem_ref = phase3_memory_surface().provider_ref
        assert scoped.handler_factory(mem_ref) is base.handler_factory(mem_ref)
        lane0 = bs.load_lane0_catalog()
        exp_ref = lane0.refs["lane0.experience.provider"]
        assert scoped.handler_factory(exp_ref) is base.handler_factory(exp_ref)
        assert scoped.capability_backend_factory(lane0.capability_ref) is \
            base.capability_backend_factory(lane0.capability_ref)
        # the sealed data ref is the ONE divergence — and after L2-b Task 5
        # both sides are the SAME class from the SAME recipe, differing in
        # exactly one construction argument: the run's projection.
        data_ref = phase3_data_surface().provider_ref
        assert scoped.handler_factory(data_ref) is not \
            base.handler_factory(data_ref)
        bound = scoped.handler_factory(data_ref)(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(bound, DW.ProductionDataProvider)
        assert bound._subject_params is sp
        base_provider = base.handler_factory(data_ref)(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        # CONSCIOUS FLIP, twice. At Task 4 the base stopped serving the UNBOUND
        # worldless provider (this asserted ``_subject_params is None``) and
        # briefly had NO subject source at all (``not hasattr``). Task 5 gives
        # ``ProductionDataProvider`` that source, so the process-level posture
        # is expressible again — and it is UNBOUND, by design: only a per-run
        # view may bind a subject.
        assert isinstance(base_provider, DW.ProductionDataProvider)
        assert base_provider._subject_params is None

    def test_the_view_accepts_and_rejects_exactly_what_the_base_does(
            self, bundle, env, base):
        """Invariant 4: the view rejects nothing the base accepts and accepts
        nothing the base rejects — registration delegates INTO the base
        (off-catalog id refused; bound identity never rebindable; a free
        catalog handler identity registered through the view resolves
        identically from both)."""
        from guanlan_v2.orchestration.catalog_runtime import CatalogMaterialError
        from guanlan_v2.orchestration.refs import ContentRef

        sp = RT.SubjectParams.project(code="833509", as_of=NOW)
        scoped = self._scoped(base, sp, bundle, env)

        bogus = ContentRef(id="handler.off_catalog", version="1",
                           content_digest="0" * 64)
        with pytest.raises(CatalogMaterialError,
                           match="not a catalog handler identity"):
            scoped.register_handler(bogus, lambda **kw: None)
        with pytest.raises(CatalogMaterialError, match="already bound"):
            scoped.register_handler(
                phase3_memory_surface().provider_ref, lambda **kw: None)

        free_ref = next(
            e.ref for e in sorted(env["snapshot"].content_manifest,
                                  key=lambda e: (e.ref.id, e.ref.version))
            if e.kind == "handler" and e.ref.id == "handler.pv.price_action")

        def marker(**kw):  # pragma: no cover - identity carrier only
            return None

        scoped.register_handler(free_ref, marker)
        assert base.handler_factory(free_ref) is marker
        assert scoped.handler_factory(free_ref) is marker
