# -*- coding: utf-8 -*-
"""L2-b Task 0 — the handoff gate: pin the upstream ABIs the production
data-world layer stands on (plan: docs/superpowers/plans/
2026-07-31-orchestration-L2b-data-runtime.md, Task 0).

An executable consumer gate in the fix-pm-dead-binding idiom: every pin is
driven against the REAL modules (real ``phase3_data_surface()``, the real
``_DataRuntimeBridgeSession`` guards, the real ``adapters/live_data.py``
``LiveClientSource``, the real production catalog bundle) and every pin is
RED-provable — either its wrong-input arm is driven live in a sibling test, or
the pin is the conscious-flip target of a named later task (recorded per test).

Gate items (Task 0 Step 1):

1.  Fact F + the landing state (the declared-order gate). **Consciously flipped
    by L2-b Task 4** — pre-flip: ``data_runtime_provider_factory`` had no
    production caller, the registered incumbent at
    ``phase3_data_surface().provider_ref`` was ``WorldlessDataBridgeProvider``
    and the pm pins were L1's flipped worldless set. Post-flip: the factory has
    exactly ONE enumerated production caller
    (``adapters/data_world.py``), the registered incumbent is
    ``ProductionDataProvider``, and the pm pins are L2-b's production set (the
    worldless ones survive, scoped, until Task 5 deletes the class). The
    dead-row names still exist nowhere in code — that one did not move.
2.  The route-equality obligation (``_frozen_route_for``).
3.  The context-equality obligation (``_verify_context_binding``).
4.  The ``LiveClientSource`` echo + its closed supported-method set.
5.  The BJ-920 identity gate — HONESTLY RED today (xfail strict, escalated).
D-A..D-E: the five binding-correction clauses, pinned empirically; the
    corrected interface truths are recorded in the Task-0 report
    (``.superpowers/sdd/task-L2b-0-handoff-gate-report.md``) and flow into the
    later task briefs.

Run: ``python -m pytest tests/orchestration/test_l2b_handoff.py -q``
"""
from __future__ import annotations

import ast
import inspect
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.adapters.data_world as DW
import guanlan_v2.orchestration.data.runtime as RT
import guanlan_v2.orchestration.pipeline.live_decide as LD
import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration.adapters import chain, live_data
from guanlan_v2.orchestration.adapters.live_data import (
    LiveClientSource,
    build_online_capture_manifest,
)
from guanlan_v2.orchestration.catalog_runtime import (
    CatalogMaterialError,
    TrustedFactoryRegistry,
)
from guanlan_v2.orchestration.data.calendar import (
    TradingCalendarResolver,
    build_trading_calendar,
)
from guanlan_v2.orchestration.data.catalog import (
    data_capability_refs,
    phase3_data_surface,
)
from guanlan_v2.orchestration.data.errors import RoutingConfigurationError
from guanlan_v2.orchestration.data.pit import PitGuard
from guanlan_v2.orchestration.data.registry import DataSourceRegistry
from guanlan_v2.orchestration.data.runtime import DataRuntimeError
from guanlan_v2.orchestration.data.schema_registry import build_phase3_registry
from guanlan_v2.orchestration.data.source import (
    DataInvocationScope,
    DataPolicyResolver,
    ResolvedMethodRoute,
    RouteEntry,
    build_data_request,
)
from guanlan_v2.orchestration.data.symbols import (
    LimitRuleEntry,
    build_limit_rule_policy,
    normalize_symbol,
)
from guanlan_v2.orchestration.eventstore import (
    RuntimeStores,
    SchemaRegistryResolver,
)
from guanlan_v2.orchestration.pipeline.assembly import (
    _catalog_handler_ref,
    build_production_catalog_runtime,
)
from guanlan_v2.orchestration.pipeline.candidates import as_handler_factory
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    PHASE2_BASE_REGISTRY_DIGEST,
    ExecutionEvidenceOrdinalToken,
    phase2_runtime_registry,
)

UTC = timezone.utc
#: 2026-07-16 07:00Z == 15:00 Asia/Shanghai (A-share close) — the frozen as_of.
AS_OF = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)

#: the L1-retired dead-row names, built by concatenation so THIS file's own
#: source satisfies neither this scan nor L1's broader tombstone pin
#: (``test_subject_projection.py`` scans for the contiguous stems too, so the
#: split points must fall INSIDE those stems).
_DEAD_ROW_NEEDLES = (
    "Structurally" + "DeadRowDataProvider",
    "_row_is_structurally" + "_dead",
    "structurally" + "_dead_row_fact",
    "register_structurally" + "_dead_row_data_provider",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# small drivers (faithful copies of the test_adapters_live_data.py idioms)     #
# --------------------------------------------------------------------------- #
class _FrozenClock:
    def __init__(self, as_of: datetime) -> None:
        self._t = as_of

    def now(self) -> datetime:
        return self._t


class _AdvancingClock:
    """Steps forward on every read — the SystemClock stand-in for D-C."""

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        self._t = start

    def now(self) -> datetime:
        t = self._t
        self._t = self._t + timedelta(seconds=1)
        return t


class _PoisonedStores:
    """Registration must carry the stores/resolver, never touch them."""

    def __getattr__(self, name):  # pragma: no cover - failing is the assertion
        raise AssertionError(f"the registration recipe touched .{name}")


class _SpyRecorder:
    def __init__(self) -> None:
        self.records: list = []

    def record_data_refusal(self, details, evidence, *, idempotency_key):
        self.records.append((details.reason_code, idempotency_key))


class _ProbeStub:
    """A fake ``live_client.probe`` returning a controlled envelope."""

    def __init__(self, env) -> None:
        self.env = env
        self.calls: list = []

    def __call__(self, source, code="", date="", limit=20, timeout=90):
        self.calls.append({"source": source, "code": code, "date": date,
                           "limit": limit})
        return self.env


def _token(evidence: int) -> ExecutionEvidenceOrdinalToken:
    return ExecutionEvidenceOrdinalToken(
        attempt=1, call_ordinal=1, evidence_ordinal=evidence)


def _weekdays_2026() -> tuple[date, ...]:
    out: list[date] = []
    d = date(2026, 1, 1)
    while d <= date(2026, 12, 31):
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


CALENDAR = build_trading_calendar(
    calendar_id="cn_a_share", sessions=_weekdays_2026(),
    material_id="cal.cn.l2b-gate", material_version="1")

_ROUTE_POLICY_REF = ContentRef(
    id="policy.route.l2b-gate", version="1", content_digest="d" * 64)


def _surface_registry(*, route_entries=None, with_limit: bool = False) -> DataSourceRegistry:
    """A REAL sealed registry over the surface's UNCHANGED method specs.

    ``route_entries=None`` freezes the sealed row's exact single-entry route
    (the shape a production registry must reproduce — Task 1's controlling
    constraint); an explicit differing tuple is the wrong-input arm.
    """
    surf = phase3_data_surface()
    row = surf.prefetch_binding.operations[0]
    reg = DataSourceRegistry(registry_version="l2b-gate-v1")
    for s in surf.method_specs:
        reg.register_method(s)
    reg.register_descriptor(surf.source_descriptor)
    reg.register_freshness(surf.freshness_policy)
    if with_limit:
        reg.register_limit(build_limit_rule_policy(
            policy_id="policy.limit.l2b-gate", policy_version="1",
            calendar=CALENDAR,
            entries=(LimitRuleEntry(
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                board_pct={"main": 0.1, "star": 0.2, "chinext": 0.2, "bj": 0.3},
                st_pct=0.05),)))
    entries = tuple(route_entries) if route_entries is not None else tuple(row.frozen_route)
    reg.register_route(ResolvedMethodRoute(
        method_ref=row.method_ref, entries=entries,
        route_policy_ref=_ROUTE_POLICY_REF))
    reg.seal()
    return reg


def _session_over(world) -> RT._DataRuntimeBridgeSession:
    """The REAL session class over gate-shaped collaborators (guards only)."""
    return RT._DataRuntimeBridgeSession(
        bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
        summary=SimpleNamespace(summary_digest="s" * 64),
        world=world, request=world.request if hasattr(world, "request") else None)


# --------------------------------------------------------------------------- #
# module fixtures — the REAL sealed chain artifacts (built once)                #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def snap():
    return chain.phase9_catalog_snapshot()


@pytest.fixture(scope="module")
def bundle(snap):
    """The RAW production catalog bundle — exactly live_decide's construction
    (``build_production_catalog_runtime(snapshot)``, NO handler_registry)."""
    return build_production_catalog_runtime(snap)


@pytest.fixture(scope="module")
def reg9():
    """The production chain registry (build_phase9_registry lineage)."""
    return chain.build_phase9_registry(chain.PHASE9_BASE_REGISTRY_DIGEST)


@pytest.fixture(scope="module")
def p3_registry():
    ph2 = phase2_runtime_registry(PHASE2_BASE_REGISTRY_DIGEST)
    return build_phase3_registry(ph2.registry_digest)


# =========================================================================== #
# 1. Fact F + the landing state (the declared-order gate; flipped by Task 4)    #
# =========================================================================== #
class TestFactFAndL1LandingState:
    def test_fact_f_data_runtime_provider_factory_has_one_production_caller(self):
        """Fact F, POST-FLIP (L2-b Task 4 — the flip this pin's pre-flip arm
        named): the world-bound factory's code occurrences in
        ``guanlan_v2/orchestration`` are its own ``def`` AND the ONE delegation
        call in ``adapters/data_world.py`` (``ProductionDataProvider.
        open_execution`` routes its rows-present session through the factory).

        The same discriminating AST idiom as ``test_data_catalog.py``'s pin
        (prose neither satisfies nor trips it). Pre-flip arm, recorded: the
        only occurrence was ``["data/runtime.py"] == ["FunctionDef"]``. A
        production caller appearing any OTHER way is the RED arm.
        """
        pkg = Path(RT.__file__).resolve().parent.parent
        assert pkg.name == "orchestration"
        refs: dict[str, list[str]] = {}
        for path in sorted(pkg.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for n in ast.walk(tree):
                named = (n.name if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         else n.id if isinstance(n, ast.Name)
                         else n.attr if isinstance(n, ast.Attribute)
                         else n.name if isinstance(n, ast.alias) else None)
                if named == "data_runtime_provider_factory":
                    refs.setdefault(
                        path.relative_to(pkg).as_posix(), []).append(type(n).__name__)
        assert sorted(refs) == ["adapters/data_world.py", "data/runtime.py"], (
            "data_runtime_provider_factory grew a production caller outside the "
            "L2-b Task 4 flip; re-pin consciously, never absorb")
        assert refs["data/runtime.py"] == ["FunctionDef"]
        assert refs["adapters/data_world.py"] == ["alias", "Name"]

    def test_l2b_landing_state_production_incumbent_registered(self, bundle):
        """CONSCIOUS FLIP (L2-b Task 4; was
        ``test_l1_landing_state_worldless_incumbent_registered``).

        Pre-flip arm, recorded: the ONE production registration recipe was
        ``RT.register_worldless_data_provider`` and the incumbent constructed
        at ``phase3_data_surface().provider_ref`` was
        ``WorldlessDataBridgeProvider``; ``live_decide``'s source carried
        ``register_worldless_data_provider(factories=bundle.factories)``.

        Post-flip: ``build_production_bindings`` supersedes it with the ONE
        world-bound recipe ``register_production_data_provider``, so the
        incumbent is ``ProductionDataProvider``. The worldless CLASS still
        exists (the per-run ``_SubjectScopedFactories`` view constructs it until
        Task 5 re-targets that seam and deletes it) — this pin is about the
        REGISTERED incumbent, which is the thing production executes over.
        """
        factories = TrustedFactoryRegistry(bundle.runtime)
        # the stores/resolver are carried into the per-run resolver, never
        # touched at REGISTRATION — a poisoned pair proves it.
        DW.register_production_data_provider(
            factories=factories, stores=_PoisonedStores(),
            schema_resolver=_PoisonedStores(),
            clock=_FrozenClock(AS_OF), catalog_runtime=bundle.runtime)
        provider = factories.handler_factory(phase3_data_surface().provider_ref)(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(provider, DW.ProductionDataProvider)
        # the production caller is the ONE-recipe seam (live_decide), by source:
        src = inspect.getsource(LD)
        assert "register_production_data_provider(" in src
        # the pre-flip CALL is gone (the name survives only in the registration
        # block's recorded history, which is the point of a conscious flip).
        assert "register_worldless_data_provider(factories=bundle.factories)" \
            not in src

    def test_dead_row_names_exist_nowhere_in_code(self):
        """L1 deleted the dead-row provider AND its helpers; their fate was
        decided ONCE, there. Post-L1 the 'structurally dead row' category does
        not exist — the four names appear nowhere in ``guanlan_v2/`` or
        ``tests/`` (docs/reports excluded by scope). The needles are built by
        concatenation so this gate never trips its own scan.
        """
        hits: list[str] = []
        for root in (_REPO_ROOT / "guanlan_v2", _REPO_ROOT / "tests"):
            for path in sorted(root.rglob("*.py")):
                text = path.read_text(encoding="utf-8", errors="replace")
                for needle in _DEAD_ROW_NEEDLES:
                    if needle in text:
                        hits.append(f"{path.relative_to(_REPO_ROOT)}: {needle}")
        assert hits == [], (
            "the dead-row category was resurrected somewhere; its fate was "
            f"decided once, in L1: {hits}")

    def test_pm_pins_are_the_l2b_flipped_production_set(self):
        """CONSCIOUS FLIP (L2-b Task 4; was
        ``test_pm_pins_are_the_l1_flipped_worldless_set``).

        Pre-flip arm, recorded: ``test_pm_two_bridges.py`` carried L1's
        worldless markers as the REGISTERED-incumbent shapes. Post-flip the
        production registration is the incumbent there — the pm seam pins name
        ``ProductionDataProvider`` and the pv-aux nodes freeze the
        catalog-licensed EMPTY instead of refusing. The worldless markers
        SURVIVE (the class is alive until Task 5 deletes it) but only under the
        explicitly worldless helper/guard, so this pin now asserts BOTH: the
        L2-b production markers are present, and the worldless ones are still
        scoped to the surviving-class pins.
        """
        text = (_REPO_ROOT / "tests" / "orchestration"
                / "test_pm_two_bridges.py").read_text(encoding="utf-8")
        for marker in (
            "ProductionDataProvider",
            "_production_registered_factories",
            "production_data_backend",
            "catalog-licensed EMPTY",
        ):
            assert marker in text, f"L2-b's production pm pin marker missing: {marker!r}"
        for marker in (
            "_worldless_registered_factories",
            "_skip_unless_worldless_incumbent",
            "WorldlessDataBridgeProvider",
            "params resolved from the run subject projection",
            "not bound at the runner seam",
        ):
            assert marker in text, (
                f"the surviving worldless-class pin scope vanished: {marker!r} "
                "(the class dies at Task 5, not here)")
        for needle in _DEAD_ROW_NEEDLES:
            assert needle not in text


# =========================================================================== #
# 2. the route-equality obligation                                             #
# =========================================================================== #
class TestRouteEqualityObligation:
    def test_every_sealed_row_freezes_the_single_surface_route(self):
        """For EVERY sealed prefetch row: ``frozen_route`` is exactly ONE
        ``RouteEntry(source_ref=surface.source_ref,
        capability_ref=spec.capability_ref)`` — the equality a production
        registry's ``default_route`` must reproduce byte-for-byte (Task 1's
        controlling constraint).
        """
        surf = phase3_data_surface()
        assert len(surf.prefetch_binding.operations) >= 1
        for row in surf.prefetch_binding.operations:
            spec = surf.spec_by_method[row.method_ref.id]
            assert row.method_ref == spec.method_ref
            assert row.capability_ref == spec.capability_ref
            assert tuple(row.frozen_route) == (
                RouteEntry(source_ref=surf.source_ref,
                           capability_ref=spec.capability_ref),)

    def test_frozen_route_for_accepts_a_registry_freezing_the_same_route(self):
        """GREEN arm: a registry registering the surface's method specs
        UNCHANGED plus the exact single-entry default route cross-resolves the
        sealed row (spec identity + capability + entries all equal).
        """
        surf = phase3_data_surface()
        row = surf.prefetch_binding.operations[0]
        reg = _surface_registry()
        session = _session_over(SimpleNamespace(source_registry=reg, request=None))
        spec, route = session._frozen_route_for(row)
        assert spec.method_id == row.method_ref.id
        assert tuple(route.entries) == tuple(row.frozen_route)

    def test_frozen_route_for_refuses_a_differing_registry(self):
        """WRONG-INPUT arm (the pin's own RED proof): a registry whose default
        route differs — here two entries instead of the sealed one — is refused
        by the REAL ``_DataRuntimeBridgeSession._frozen_route_for`` with the
        typed ``DataRuntimeError``. This is what refuses any production
        registry that drifts from the sealed rows.
        """
        surf = phase3_data_surface()
        row = surf.prefetch_binding.operations[0]
        entry = tuple(row.frozen_route)[0]
        reg = _surface_registry(route_entries=(entry, entry))
        session = _session_over(SimpleNamespace(source_registry=reg, request=None))
        with pytest.raises(DataRuntimeError,
                           match="does not equal the registry-frozen default route"):
            session._frozen_route_for(row)


# =========================================================================== #
# 3. the context-equality obligation                                           #
# =========================================================================== #
def _binding_session(world_ctx, admitted_ctx) -> RT._DataRuntimeBridgeSession:
    ctx_ref = SimpleNamespace(payload_ref="p-1", schema_ref="s-1")

    class _Reader:
        def get(self, payload_ref, *, expected_schema_ref):
            assert payload_ref == "p-1" and expected_schema_ref == "s-1"
            return SimpleNamespace(data_context=admitted_ctx)

    request = SimpleNamespace(
        input_snapshot=SimpleNamespace(context_snapshot_ref=ctx_ref),
        reader=_Reader())
    return RT._DataRuntimeBridgeSession(
        bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
        summary=SimpleNamespace(summary_digest="s" * 64),
        world=SimpleNamespace(ctx=world_ctx), request=request)


class TestContextEqualityObligation:
    def test_equal_contexts_pass_across_instances(self):
        """FULL-equality semantics: two separately built (equal-valued) REAL
        ``DataContext`` instances verify — the obligation is value equality,
        never object identity (Lane 0 commits in a DIFFERENT process)."""
        a = P.pilot_data_context(as_of=AS_OF)
        b = P.pilot_data_context(as_of=AS_OF)
        assert a is not b
        _binding_session(a, b)._verify_context_binding()  # no raise

    def test_one_field_different_context_is_refused(self):
        """WRONG-INPUT arm (the pin's own RED proof): ONE differing field —
        here ``source_registry_digest`` — refuses through the REAL
        ``_verify_context_binding``. This is exactly what forces Task 1's
        recipe to be byte-deterministic across processes."""
        world_ctx = P.pilot_data_context(as_of=AS_OF)
        admitted = world_ctx.model_copy(
            update={"source_registry_digest": "e" * 64})
        with pytest.raises(DataRuntimeError,
                           match="does not equal the service-frozen data world"):
            _binding_session(world_ctx, admitted)._verify_context_binding()


# =========================================================================== #
# 4. the LiveClientSource echo + the closed supported set                       #
# =========================================================================== #
class TestLiveClientSourceEcho:
    def test_fetch_echoes_the_frozen_route_source_ref(self, p3_registry):
        """``LiveClientSource.fetch`` builds its ``RawFetch`` with
        ``source_ref = scope.frozen_route.entries[0].source_ref`` — so staged
        with the sealed ``guanlan.datafeed@1`` identity it lawfully serves
        UNDER that identity (the echo Task 2 re-proves under production
        wiring). Driven through a REAL sealed request + scope over an injected
        fake probe (no vendor).
        """
        surf = phase3_data_surface()
        spec = surf.spec_by_method["verified_snapshot"]
        ctx = P.pilot_data_context(as_of=AS_OF)
        req = build_data_request(
            ctx, method_spec=spec,
            params={"symbols": ({"code": "600519", "exchange": "SH",
                                 "board": "main"},),
                    "as_of": AS_OF.isoformat()},
            registry=p3_registry, request_id="req-l2b-gate-1")
        route = ResolvedMethodRoute(
            method_ref=spec.method_ref,
            entries=(RouteEntry(source_ref=surf.source_ref,
                                capability_ref=spec.capability_ref),),
            route_policy_ref=_ROUTE_POLICY_REF)
        scope = DataInvocationScope(
            plan_digest="a" * 64, node_id="pm", worker_id="dec.pm",
            operation_token=_token(1), attempt_tokens=(_token(2),),
            frozen_route=route, invocation_mode="cache_or_invoke",
            catalog_digest="b" * 64,
            schema_registry_digest=p3_registry.registry_digest)
        stub = _ProbeStub({"ok": True, "status": "ok",
                           "items": [{"name": "贵州茅台", "price": 1000.0}],
                           "n": 1, "error": "", "note": "",
                           "pulled_at": "2026-07-16T15:00:00"})
        adapter = LiveClientSource(probe_fn=stub, resolve_source_fn=lambda s: s)
        raw = adapter.fetch(req, scope=scope)
        assert raw.source_ref == surf.source_ref
        assert raw.source_ref == scope.frozen_route.entries[0].source_ref
        assert raw.capability_ref == spec.capability_ref
        assert raw.request_digest == req.request_digest

    def test_supported_set_is_exactly_the_three_methods(self):
        assert live_data._SUPPORTED_METHOD_IDS == frozenset(
            {"verified_snapshot", "news", "ohlcv"})

    @pytest.mark.parametrize(
        "method_id", ["indicators", "fundamentals", "signals", "instrument_names"])
    def test_unsupported_methods_refuse_loudly(self, method_id):
        """WRONG-INPUT arm: the other four sealed method ids raise the typed
        ``RoutingConfigurationError`` BEFORE any probe/scope use — Task 2's
        supported-set guard stands on this refusal firing first."""
        surf = phase3_data_surface()
        adapter = LiveClientSource(
            probe_fn=lambda *a, **k: pytest.fail("probe must never be reached"))
        request = SimpleNamespace(
            method_spec_ref=surf.spec_by_method[method_id].method_ref)
        with pytest.raises(RoutingConfigurationError,
                           match="is not bound to this source"):
            adapter.fetch(request, scope=None)


# =========================================================================== #
# 5. the BJ-920 identity gate (inherited defect — HONESTLY RED, escalated)      #
# =========================================================================== #
def test_bj_920_identity_gate():
    """CONSCIOUSLY FLIPPED 2026-07-31: the great-meitner BJ-920 fix (296bd02)
    was merged at the controller's Task-0 ruling (merge c01d099) -- 920xxx now
    derives BJ/bj through the shared predicate. This gate stays as the
    permanent identity pin the production world stands on."""
    sym = normalize_symbol("920799")
    assert sym.exchange == "BJ"
    assert sym.board == "bj"


# =========================================================================== #
# D-A: the capability-backend registration ABI                                  #
# =========================================================================== #
class TestDACapabilityBackendRegistrationABI:
    def test_register_and_resolve_shape(self, bundle):
        """``TrustedFactoryRegistry.register_capability_backend(ref, factory)``
        keys by the exact catalog capability identity; the gateway invokes the
        factory PER capability invocation with ``capability_ref=...`` — so
        backend SHARING is the factory's job: ``lambda **kw: backend`` (the
        ``test_runtime_integration.py`` shape) returns the ONE instance on
        every invocation. This decides Task 4's backend-sharing shape.
        """
        cap = data_capability_refs()["verified_snapshot"]
        factories = TrustedFactoryRegistry(bundle.runtime)
        sentinel = object()
        factories.register_capability_backend(cap, lambda **kw: sentinel)
        f = factories.capability_backend_factory(cap)
        assert f(capability_ref=cap) is sentinel
        assert f(capability_ref=cap) is sentinel  # per-invocation, same instance

    def test_off_catalog_and_rebind_refuse(self, bundle):
        """WRONG-INPUT arms: a forged digest is not a catalog capability
        identity; a bound backend can never be replaced."""
        cap = data_capability_refs()["verified_snapshot"]
        factories = TrustedFactoryRegistry(bundle.runtime)
        forged = ContentRef.model_validate(
            {**cap.model_dump(), "content_digest": "f" * 64})
        with pytest.raises(CatalogMaterialError,
                           match="not a catalog capability identity"):
            factories.register_capability_backend(forged, lambda **kw: object())
        factories.register_capability_backend(cap, lambda **kw: object())
        with pytest.raises(CatalogMaterialError, match="already bound"):
            factories.register_capability_backend(cap, lambda **kw: object())

    def test_gateway_invokes_the_factory_per_invocation_at_source(self):
        """The Phase-2 gateway resolves the factory and calls it on EVERY
        confined invocation (never caching a backend per runtime) — pinned at
        source on ``worker.py``'s gateway invoke path."""
        src = inspect.getsource(W)
        assert ("self._factories.capability_backend_factory("
                "pending.capability_ref)") in src
        assert "factory(capability_ref=pending.capability_ref)" in src


# =========================================================================== #
# D-B: the policy-resolver elapsed branch                                       #
# =========================================================================== #
class TestDBPolicyResolverElapsedBranch:
    def test_surface_policy_is_elapsed_based(self):
        surf = phase3_data_surface()
        assert surf.freshness_policy.policy_id == "policy.freshness.default-elapsed"
        assert surf.freshness_policy.is_session_based is False

    def test_elapsed_policy_without_limit_policy_refuses(self):
        """WRONG-INPUT arm: an elapsed policy with ZERO registered limit
        policies cannot bind a session calendar — the exact ``ValueError`` the
        plan's D-B clause names. Task 1 MUST register one ``LimitRulePolicy``.
        """
        surf = phase3_data_surface()
        reg = _surface_registry(with_limit=False)
        resolver = DataPolicyResolver(
            reg.snapshot(), calendar_resolver=TradingCalendarResolver([CALENDAR]))
        with pytest.raises(ValueError,
                           match="requires a bound session calendar"):
            resolver.resolve_method(
                surf.spec_by_method["verified_snapshot"],
                ctx=P.pilot_data_context(as_of=AS_OF))

    def test_elapsed_policy_with_limit_policy_resolves(self):
        """GREEN arm + the exact build fields: ``build_limit_rule_policy(*,
        policy_id, policy_version, calendar, entries)`` — NOTE the class lives
        in ``data/symbols.py`` (NOT ``data/source.py`` as the plan wrote; the
        correction is recorded in the Task-0 report). The resolved bundle
        carries the limit policy's calendar identity."""
        surf = phase3_data_surface()
        reg = _surface_registry(with_limit=True)
        resolver = DataPolicyResolver(
            reg.snapshot(), calendar_resolver=TradingCalendarResolver([CALENDAR]))
        resolved = resolver.resolve_method(
            surf.spec_by_method["verified_snapshot"],
            ctx=P.pilot_data_context(as_of=AS_OF))
        assert resolved.limit_policy is not None
        assert resolved.limit_policy.policy_id == "policy.limit.l2b-gate"
        assert resolved.calendar_id == "cn_a_share"
        assert resolved.calendar_material_ref == CALENDAR.material_ref


# =========================================================================== #
# D-C: the PitGuard clock contract                                              #
# =========================================================================== #
class TestDCPitGuardClockContract:
    def test_online_guard_requires_a_clock_frozen_at_as_of(self):
        """GREEN arm: an ONLINE context binds a guard from a clock reading
        EXACTLY ``ctx.as_of``. Task 4's world therefore binds a FROZEN clock at
        the admitted context's as_of — never an advancing ``SystemClock``."""
        ctx = P.pilot_data_context(as_of=AS_OF)
        guard = PitGuard.from_context(
            ctx, clock=_FrozenClock(AS_OF), calendar=CALENDAR,
            refusal_recorder=_SpyRecorder())
        assert guard is not None

    def test_a_drifted_clock_reading_is_refused(self):
        """WRONG-INPUT arm: any clock reading != ctx.as_of refuses loudly."""
        ctx = P.pilot_data_context(as_of=AS_OF)
        with pytest.raises(RoutingConfigurationError,
                           match="frozen clock reading exactly the context as_of"):
            PitGuard.from_context(
                ctx, clock=_FrozenClock(AS_OF + timedelta(seconds=1)),
                calendar=CALENDAR, refusal_recorder=_SpyRecorder())

    def test_an_advancing_clock_cannot_serve_repeated_guard_builds(self):
        """The dispatch builds a guard PER read from the world's clock, so an
        advancing clock passes the first construction and refuses the second —
        the empirical proof that ONLINE mode still demands the frozen clock."""
        ctx = P.pilot_data_context(as_of=AS_OF)
        adv = _AdvancingClock(AS_OF)
        PitGuard.from_context(ctx, clock=adv, calendar=CALENDAR,
                              refusal_recorder=_SpyRecorder())
        with pytest.raises(RoutingConfigurationError):
            PitGuard.from_context(ctx, clock=adv, calendar=CALENDAR,
                                  refusal_recorder=_SpyRecorder())


# =========================================================================== #
# D-D: manifest persistability through the production chain registry            #
# =========================================================================== #
class TestDDManifestPersistability:
    def test_snapshot_models_are_registered_payload_schemas(self, reg9):
        """``DataSnapshotManifest@1`` / ``DataRoutingSnapshot@1`` /
        ``DataSourceConfigSnapshot@1`` ARE registered in the production chain
        registry — so per the plan's own no-pre-ruling-fork clause, Task 7's
        ``persist_capture`` channel IS ``stores.payloads.put`` (run-scoped
        idempotency keys for run-varying content), NOT the driver-archive
        fallback."""
        from guanlan_v2.orchestration.data.snapshot import (
            DataRoutingSnapshot,
            DataSnapshotManifest,
            DataSourceConfigSnapshot,
        )
        assert reg9.resolve(SchemaRef(name="DataSnapshotManifest", version="1")) \
            is DataSnapshotManifest
        assert reg9.resolve(SchemaRef(name="DataRoutingSnapshot", version="1")) \
            is DataRoutingSnapshot
        assert reg9.resolve(SchemaRef(name="DataSourceConfigSnapshot", version="1")) \
            is DataSourceConfigSnapshot

    def test_manifest_round_trips_through_a_real_payload_store(self, reg9):
        """A REAL ``online_capture_root`` manifest is accepted by a REAL
        ``stores.payloads.put`` under the chain registry digest and read back
        digest-identical; an unregistered schema name refuses (WRONG arm)."""
        resolver = SchemaRegistryResolver()
        resolver.register(reg9)
        stores = RuntimeStores(resolver=resolver, clock=_FrozenClock(AS_OF))
        manifest = build_online_capture_manifest(
            data_snapshot_id="l2b-gate-root", as_of=AS_OF,
            timezone="Asia/Shanghai", calendar_id="cn_a_share",
            routing_snapshot_digest="c" * 64,
            schema_registry_digest=reg9.registry_digest)
        sr = SchemaRef(name="DataSnapshotManifest", version="1")
        ref = stores.payloads.put(
            sr, manifest, registry_digest=reg9.registry_digest,
            namespace="main", idempotency_key="l2b-gate:manifest:1")
        back = stores.payloads.get(ref, expected_schema_ref=sr)
        assert back.content_digest == manifest.content_digest
        with pytest.raises(Exception):
            stores.payloads.put(
                SchemaRef(name="NotARegisteredL2bModel", version="1"), manifest,
                registry_digest=reg9.registry_digest, namespace="main",
                idempotency_key="l2b-gate:manifest:2")


# =========================================================================== #
# D-E: the deterministic-handler registration ABI + the pv gap                  #
# =========================================================================== #
class TestDEDeterministicHandlerABI:
    def test_pv_handler_materials_exist_but_have_no_production_registration(
            self, snap, bundle):
        """``handler.pv.price_action`` / ``handler.pv.microstructure`` EXIST as
        sealed catalog handler materials (registration via the Task-11
        ``handler_registry`` idiom is therefore possible without new bytes),
        and the RAW production bundle — exactly live_decide's
        ``build_production_catalog_runtime(snapshot)`` with NO
        ``handler_registry`` — binds NO factory for either (the expected D-E
        gap Task 6 closes)."""
        for hid in ("handler.pv.price_action", "handler.pv.microstructure"):
            ref = _catalog_handler_ref(snap, hid)
            assert ref.id == hid and ref.version == "1"
            with pytest.raises(CatalogMaterialError,
                               match="no handler factory bound"):
                bundle.factories.handler_factory(ref)
        # live_decide's deep bundle passes no handler_registry, by source:
        assert "build_production_catalog_runtime(snapshot)" in inspect.getsource(LD)

    def test_two_stage_handler_abi(self, snap, bundle):
        """The executor's deterministic branch is two-stage:
        ``handler_factory(worker=..., resolved=...)`` returns the inner handler
        called with ``(node=..., input_snapshot=..., contributions=...,
        data_result_refs=...)`` — pinned at source on ``worker.py`` (the
        candidates.py:12 line refs 1730-1735 have drifted; the call now lives
        at the ``handler_factory(worker=worker, resolved=resolved)`` site) and
        exercised through ``as_handler_factory`` + a real registration."""
        src = inspect.getsource(W)
        assert "handler = handler_factory(worker=worker, resolved=resolved)" in src
        assert "model_result = handler(" in src
        inner = object()
        factories = TrustedFactoryRegistry(bundle.runtime)
        ref = _catalog_handler_ref(snap, "handler.pv.price_action")
        factories.register_handler(ref, as_handler_factory(inner))
        assert factories.handler_factory(ref)(worker=None, resolved=None) is inner
