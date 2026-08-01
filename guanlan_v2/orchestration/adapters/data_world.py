# -*- coding: utf-8 -*-
"""L2-b — the production data world.  Task 1: the deterministic frozen recipe.

Plan: ``docs/superpowers/plans/2026-07-31-orchestration-L2b-data-runtime.md``,
corrected by the Task-0 gate (``.superpowers/sdd/task-L2b-0-handoff-gate-report.md``
D-A..D-E).  This module holds the world's FROZEN half: the sealed production
:class:`~guanlan_v2.orchestration.data.registry.DataSourceRegistry`, the sealed
source-config and routing snapshots, the committed trading calendar and the
:class:`~guanlan_v2.orchestration.data.source.DataPolicyResolver` over them.

**The controlling constraint — byte-determinism across processes.**  The deep
lane's real session verifies ``registry.default_route(m).entries ==
row.frozen_route`` per sealed prefetch row and ``world.ctx ==
snapshot.data_context`` by FULL equality, where the admitted
``ContextSnapshot`` is committed by Lane 0 in a DIFFERENT process.  Every
recipe component is therefore derived only from module constants,
``phase3_data_surface()`` and the committed calendar material bytes — never
from wall clocks, environment, or per-process state.  Pinned by
``tests/orchestration/test_data_world_recipe.py`` (subprocess digest-triple).

**Method specs are registered UNCHANGED** from the surface (spec digests
equal) — the test-suite idiom of rebuilding specs with a session freshness ref
is exactly what production must NOT do: the sealed rows' ``method_ref``
cross-resolution at ``_DataRuntimeBridgeSession._frozen_route_for`` depends on
spec-digest identity.

**Calendar material (dated note, 2026-07-31).**  The committed file
``config/orchestration/materials/data/cn-a-share-sessions-2026.json`` was
generated ONCE from the reviewed reader behind
``guanlan_v2/seats/api.py::_trading_calendar`` (the engine full day calendar;
242 sessions, 2026-01-05..2026-12-31) and is thereafter FROZEN — this module
reads bytes and never re-derives (「控制器已裁」determinism over
auto-derivation).  Coverage ends 2026-12-31: a session date outside coverage
is an honest refusal downstream (``data/calendar.py`` coverage contract), and
extending into 2027 is a reviewed one-line material bump chartered to whoever
hits it — regenerate with the same reviewed reader, bump
``material_version``, and re-freeze the new golden consciously.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from guanlan_v2.orchestration.adapters.live_data import (
    LiveClientSource,
    build_online_capture_manifest,
    build_online_data_context,
)
from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.calendar import (
    ImmutableTradingCalendar,
    TradingCalendarMaterial,
    TradingCalendarResolver,
)
from guanlan_v2.orchestration.data.errors import RoutingConfigurationError
from guanlan_v2.orchestration.data.catalog import (
    data_capability_refs,
    parse_prefetch_binding,
    phase3_data_surface,
)
from guanlan_v2.orchestration.data.pit import DataFetchRefusalDetails, FreshnessPolicy
from guanlan_v2.orchestration.data.registry import DataSourceRegistry
from guanlan_v2.orchestration.data.runtime import (
    DataRuntimeBridgeProvider,
    DataRuntimeError,
    DataRuntimeWorld,
    DataSourceCapabilityBackend,
    SubjectParams,
    data_runtime_provider_factory,
)
from guanlan_v2.orchestration.data.snapshot import (
    DataRoutingSnapshot,
    DataSnapshotManifest,
    DataSourceConfigSnapshot,
    build_data_source_config_snapshot,
)
from guanlan_v2.orchestration.data.source import (
    DataPolicyResolver,
    ResolvedMethodRoute,
    RouteEntry,
)
from guanlan_v2.orchestration.data.symbols import (
    LimitRuleEntry,
    build_limit_rule_policy,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.eventstore import EventRefusalAuditSink
from guanlan_v2.orchestration.refs import ContentRef, PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.runtime_contracts import (
    default_audit_detail_registry,
)

__all__ = [
    "PRODUCTION_DATA_REGISTRY_VERSION",
    "PRODUCTION_ROUTING_AUDIT_ID",
    "PRODUCTION_CALENDAR_MATERIAL_PATH",
    "PRODUCTION_CAPTURE_NAMESPACE",
    "PRODUCTION_CAPTURE_TIMEZONE",
    "PRODUCTION_LIMIT_POLICY_ID",
    "PRODUCTION_ROUTE_POLICY_REF",
    "PRODUCTION_SESSION_FRESHNESS_METHOD_ID",
    "PRODUCTION_VERIFIED_SNAPSHOT_FRESHNESS_POLICY_ID",
    "ProductionDataProvider",
    "ProductionDataWorldRecipe",
    "ProductionDataWorldResolver",
    "ProductionNoCache",
    "ThreadConfinedDataBackend",
    "build_production_capture",
    "persist_capture",
    "production_data_adapters",
    "production_data_backend",
    "production_data_provider_factory",
    "production_data_recipe",
    "production_data_refusal_audit_sink",
    "register_production_data_provider",
]

#: frozen identity constants (Task 1 interface).
PRODUCTION_DATA_REGISTRY_VERSION = "prod-data-v1"
PRODUCTION_ROUTING_AUDIT_ID = "prod-data-routing-v1"
PRODUCTION_LIMIT_POLICY_ID = "policy.limit.cn-a-share-prod"

#: the method the ADDENDUM's session-counted freshness policy is scoped to, and
#: that policy's own frozen identity.  ``method_or_category`` carries the method
#: id, which is what makes ``DataPolicyResolver`` select it for this method only
#: (``data/source.py::DataPolicyResolver._resolve_freshness``) — the method SPEC
#: is registered unchanged, because its digest is sealed catalog material.
PRODUCTION_SESSION_FRESHNESS_METHOD_ID = "verified_snapshot"
PRODUCTION_VERIFIED_SNAPSHOT_FRESHNESS_POLICY_ID = (
    "policy.freshness.verified-snapshot-session"
)

#: the committed, digest-sealed 2026 A-share session material (repo-relative).
PRODUCTION_CALENDAR_MATERIAL_PATH = (
    "config/orchestration/materials/data/cn-a-share-sessions-2026.json"
)

#: …/guanlan_v2/orchestration/adapters/data_world.py -> the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: the frozen deterministic route-policy identity.  ``_frozen_route_for``
#: compares route ENTRIES only (pinned at the Task-0 gate), so this ref is
#: audit identity; its digest is still derived canonically from a frozen
#: constant document — never a per-process value.
_ROUTE_POLICY_DOC = {
    "route_policy": "single-source.guanlan-datafeed",
    "audit_id": PRODUCTION_ROUTING_AUDIT_ID,
    "version": "1",
}
PRODUCTION_ROUTE_POLICY_REF = ContentRef(
    id="policy.route.prod-data",
    version="1",
    content_digest=content_digest(_ROUTE_POLICY_DOC),
)

#: the ONE configured source's non-secret options (credentials NEVER enter a
#: DataSourceConfigSnapshot — its builder scans and refuses).
_SOURCE_OPTIONS = {
    "facade": "guanlan_v2.datafeed.live_client",
    "transport": "subprocess_probe",
}


def _load_calendar_material(*, path: Path | None = None) -> ImmutableTradingCalendar:
    """Load + digest-verify the committed calendar material (bytes, no re-derivation).

    :class:`ImmutableTradingCalendar` re-derives the material digest and requires
    it to equal the file's declared ``content_digest`` — one tampered byte in the
    session set (or a drifted declared digest) raises
    :class:`~guanlan_v2.orchestration.data.errors.SnapshotMismatchError` loudly.

    A missing/unreadable file or a corrupt document (bad JSON, missing keys,
    malformed sessions) is a typed
    :class:`~guanlan_v2.orchestration.data.errors.RoutingConfigurationError`
    naming the material path — never an untyped ``FileNotFoundError`` /
    ``KeyError``, and never a fabricated or repaired calendar.
    """
    p = path if path is not None else _REPO_ROOT / PRODUCTION_CALENDAR_MATERIAL_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingConfigurationError(
            f"committed calendar material missing or unreadable at {p} "
            f"({type(exc).__name__}: {exc}); the recipe reads committed bytes "
            "and never fabricates a calendar"
        ) from exc
    try:
        doc = json.loads(text)
        material = TradingCalendarMaterial(
            calendar_id=doc["calendar_id"], sessions=tuple(doc["sessions"])
        )
        ref = ContentRef(
            id=doc["material_id"],
            version=doc["material_version"],
            content_digest=doc["content_digest"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RoutingConfigurationError(
            f"committed calendar material at {p} is corrupt "
            f"({type(exc).__name__}: {exc}); the recipe never fabricates or "
            "repairs a calendar"
        ) from exc
    # digest drift stays its own typed failure: SnapshotMismatchError, unwrapped.
    return ImmutableTradingCalendar(material=material, material_ref=ref)


@dataclass(frozen=True)
class ProductionDataWorldRecipe:
    """The world's frozen half — sealed registry + config + routing + calendar
    + policy resolver, with the derived digest triple as properties.

    Built only by :func:`production_data_recipe`; byte-deterministic across
    processes (the Lane-0 producer and the deep-lane verifier live in
    different processes and must agree on every digest).
    """

    registry: DataSourceRegistry
    source_config: DataSourceConfigSnapshot
    routing: DataRoutingSnapshot
    calendar: ImmutableTradingCalendar
    policy_resolver: DataPolicyResolver

    @property
    def source_registry_digest(self) -> str:
        return self.registry.snapshot().source_registry_digest

    @property
    def routing_snapshot_digest(self) -> str:
        return self.routing.routing_digest

    @property
    def source_config_digest(self) -> str:
        return self.source_config.source_config_digest


def _build_recipe(*, schema_registry_digest: str) -> ProductionDataWorldRecipe:
    """Build the recipe from the surface + the committed material (pure).

    ``schema_registry_digest`` is a PARAMETER (the production chain registry
    digest, bound by :func:`production_data_recipe`) — never read from a
    module global at this seam.
    """
    surf = phase3_data_surface()
    calendar = _load_calendar_material()

    registry = DataSourceRegistry(registry_version=PRODUCTION_DATA_REGISTRY_VERSION)
    # the surface's seven method specs, UNCHANGED (spec-digest identity).
    for spec in surf.method_specs:
        registry.register_method(spec)
    # the ONE reviewed source under the sealed guanlan.datafeed@1 identity.
    registry.register_descriptor(surf.source_descriptor)
    # the surface's elapsed freshness policy, unchanged — the DEFAULT for every
    # method whose id no registered policy scopes itself to.
    registry.register_freshness(surf.freshness_policy)
    # RULING ADDENDUM Part 2 — the ADDITIONAL, method-scoped SESSION policy for
    # ``verified_snapshot``.  WHY: the default policy is elapsed-based
    # (max_elapsed_seconds=86400, data/catalog.py), so a settled datum stamped at
    # FRIDAY's 15:00 close is 57h old at MONDAY's session midnight — every Monday
    # deep run would read STALE, a permanent weekly hole with no honest way out
    # (the datum genuinely IS the newest settled one).  Counted in TRADING
    # SESSIONS over the committed calendar it is exactly ONE session old, which
    # is what "the most recent completed session" means.  ``FreshnessPolicy``
    # was built for this (max_trading_sessions + the exact calendar identity,
    # data/pit.py); the policy therefore carries the SAME committed material ref
    # the world binds to its PitGuard, so ``_check_freshness``'s calendar-identity
    # assertion is a real tripwire, not a formality.  Every OTHER method keeps
    # the elapsed default (gate D-B's limit-policy binding included).
    #
    # SCOPE OF THE CLOSURE (review M7 — the controller's ruling, recorded so the
    # campaign day is chosen with open eyes): this closes the Monday hole ONLY
    # when the venue serves an anchor on the CURRENT session.  A genuine weekend
    # pull is still served FRIDAY's snapshot, whose ``last_close`` is THURSDAY's
    # close — two sessions old at Monday's boundary — so a Sunday run is honestly
    # STALE and refuses.  That is BY DESIGN, not a gap: the Monday intraday run
    # (anchor = Monday, settled = Friday, exactly one session) is the fresh path.
    # Widening it would mean admitting two-session-old data, which is a policy
    # decision (one field + another conscious re-freeze), never a silent one.
    registry.register_freshness(
        FreshnessPolicy.build(
            policy_id=PRODUCTION_VERIFIED_SNAPSHOT_FRESHNESS_POLICY_ID,
            policy_version="1",
            method_or_category=PRODUCTION_SESSION_FRESHNESS_METHOD_ID,
            max_trading_sessions=1,
            calendar_id=calendar.calendar_id,
            calendar_material_ref=calendar.material_ref,
        )
    )
    # gate D-B: an elapsed method policy requires >=1 registered limit policy
    # carrying a calendar identity — the reviewed A-share board table bound to
    # the committed cn_a_share material.
    registry.register_limit(
        build_limit_rule_policy(
            policy_id=PRODUCTION_LIMIT_POLICY_ID,
            policy_version="1",
            calendar=calendar,
            entries=(
                LimitRuleEntry(
                    # Provenance: 2023-02-17 = 全面注册制 (the full
                    # registration-based reform taking effect across all
                    # boards) — the earliest date this WHOLE row is true at
                    # once: the main-board 5-session listing window exists
                    # only from that reform, and chinext 0.20 only from
                    # 2020-08-24. This table serves the ONLINE-at-2026 lane;
                    # a PIT replay over 2020-2023 must NOT trust it (that
                    # needs a properly windowed multi-entry table).
                    effective_from=datetime(2023, 2, 17, tzinfo=timezone.utc),
                    board_pct={"main": 0.1, "star": 0.2, "chinext": 0.2, "bj": 0.3},
                    st_pct=0.05,
                    first_session_window=5,
                ),
            ),
        )
    )
    # one explicit single-entry default route per method — for ALL SEVEN method
    # ids the entry is exactly (surface.source_ref, spec.capability_ref), so a
    # sealed prefetch row's frozen_route equals the registry-frozen default
    # route byte-for-byte, and a future L3 grant of another method freezes the
    # same route without re-opening this module.
    for spec in surf.method_specs:
        registry.register_route(
            ResolvedMethodRoute(
                method_ref=spec.method_ref,
                entries=(
                    RouteEntry(
                        source_ref=surf.source_ref,
                        capability_ref=spec.capability_ref,
                    ),
                ),
                route_policy_ref=PRODUCTION_ROUTE_POLICY_REF,
            )
        )
    registry.seal()

    source_config = build_data_source_config_snapshot(
        config_version=PRODUCTION_DATA_REGISTRY_VERSION,
        method_selections={
            spec.method_id: surf.source_descriptor.source_id
            for spec in surf.method_specs
        },
        source_options={surf.source_descriptor.source_id: dict(_SOURCE_OPTIONS)},
    )
    routing = registry.build_routing_snapshot(
        audit_id=PRODUCTION_ROUTING_AUDIT_ID,
        schema_registry_digest=schema_registry_digest,
        source_config=source_config,
    )
    policy_resolver = DataPolicyResolver(
        registry.snapshot(),
        calendar_resolver=TradingCalendarResolver([calendar]),
    )
    return ProductionDataWorldRecipe(
        registry=registry,
        source_config=source_config,
        routing=routing,
        calendar=calendar,
        policy_resolver=policy_resolver,
    )


def _production_chain_registry_digest() -> str:
    """The production chain (phase-9 lineage) registry digest — deterministic."""
    from guanlan_v2.orchestration.adapters import chain

    return chain.build_phase9_registry(
        chain.PHASE9_BASE_REGISTRY_DIGEST
    ).registry_digest


_RECIPE: ProductionDataWorldRecipe | None = None
_RECIPE_LOCK = threading.Lock()


def production_data_recipe() -> ProductionDataWorldRecipe:
    """The cached production data-world recipe (the ``phase3_data_surface`` idiom).

    Pure; no I/O beyond reading the committed calendar material file.  Any
    build failure (missing material, digest drift, unsealed registry) raises
    loudly at first use — Task 4 binds that first use to binding construction
    so a broken recipe kills the deep lane at startup, before any lease.

    The module cache is double-checked under ``_RECIPE_LOCK`` so concurrent
    first calls build exactly once (the build is deterministic, so the race
    was benign — but one build is the contract worth pinning).
    """
    global _RECIPE
    if _RECIPE is None:
        with _RECIPE_LOCK:
            if _RECIPE is None:
                _RECIPE = _build_recipe(
                    schema_registry_digest=_production_chain_registry_digest()
                )
    return _RECIPE


# --------------------------------------------------------------------------- #
# Task 2 — the reviewed source adapter under the sealed identity + the ONE     #
# production backend                                                           #
# --------------------------------------------------------------------------- #
def production_data_adapters() -> Mapping[str, Any]:
    """Exactly ``{"guanlan.datafeed": LiveClientSource(calendar=<committed>)}``.

    The key is the sealed surface identity's id — ``phase3_data_surface()``'s
    ``source_ref.id``, the exact string the backend resolves each staged
    ``source_ref.id`` against (the sealed identity is the registration key,
    never new bytes).  The value is the **facade-default** construction of the
    reviewed :class:`~guanlan_v2.orchestration.adapters.live_data.LiveClientSource`
    (the real ``guanlan_v2.datafeed.live_client`` facade, lazily imported at
    call time — no import-time vendor touch) carrying ONE bound dependency: the
    recipe's **committed trading calendar** (RULING ADDENDUM Part 1).  That
    calendar is what attributes a quote row's settled close to a session
    (``live_data._settled_candidate``); it is the SAME object
    :meth:`ProductionDataWorldResolver.world_for` binds to the run's
    ``PitGuard``, so the settled stamp and a session-counted freshness policy
    are evaluated against one identical material.  Adapters WRAP reviewed readers
    (Global Constraints): no new vendor client, no re-implemented probe, no
    fabricated row.  Tests inject fakes through the adapter's existing
    ``probe_fn``/``resolve_source_fn``/``known_sources_fn`` ports — never a
    stub of the class itself.

    Supported-set honesty (Task-1 review Minor #4, chartered here): the
    recipe's ``method_selections`` names ``guanlan.datafeed`` for all seven
    method ids while :class:`LiveClientSource` serves only
    ``{verified_snapshot, news, ohlcv}`` — the guard that keeps that honest is
    the Task-2 test pinning every sealed prefetch ROW's method id inside the
    adapter's supported set, so an L3 grant of an unsupported method fails in
    the tree first, never live.

    **Cost statement, corrected (review M5).**  "No import-time vendor touch"
    is still true of the VENDOR facade, but since the addendum this function is
    no longer free: building the adapter map now needs the recipe's committed
    trading calendar, i.e. it reads + digests
    :data:`PRODUCTION_CALENDAR_MATERIAL_PATH` on the FIRST call.  Repeated calls
    do not re-read it — :func:`production_data_recipe` is the module-cached,
    lock-guarded single build, so every call after the first is a dict literal
    over the SAME already-loaded ``TradingCalendar`` object (pinned by test).
    Lock order is unidirectional (``_BACKEND_LOCK`` → ``_RECIPE_LOCK``, never the
    reverse), so the nesting at :func:`production_data_backend` cannot deadlock.
    """
    return {
        phase3_data_surface().source_ref.id: LiveClientSource(
            calendar=production_data_recipe().calendar)
    }


class ThreadConfinedDataBackend(DataSourceCapabilityBackend):
    """The ONE process-stable production data backend — thread-local staging.

    Why (gate D-A, pinned by test): the dag executor runs nodes concurrently
    in ``asyncio.to_thread`` worker threads (``pipeline/assembly.py`` module
    thread-discipline note; ``runtime_limit=4`` at ``live_decide.py:1248``),
    and the Phase-2 gateway resolves the capability-backend factory **per
    capability invocation** with a ``capability_ref=`` kwarg
    (``worker.py:920``/``:926``) — so the production registration
    (``lambda **kw: production_data_backend()``, Task 4) hands this ONE shared
    instance to every concurrent node thread.  The base class's single
    ``_staged`` slot would interleave across nodes; its ``stage`` → ``invoke``
    → ``clear`` discipline is same-thread synchronous
    (``data/runtime.py::_DataGatewayAdapter.invoke``), so a **thread-local**
    staged slot makes cross-node interleaving structurally impossible without
    a lock.  The underlying ``live_client.probe`` is a subprocess per call —
    thread-safe by isolation.

    **Concurrency statement for the record (charter §2.2 item 9):** the data
    provider does NOT serialize — N concurrent nodes may probe concurrently.
    The LLM throughput ceiling remains the ``WorkerSeatModelGateway``
    single-loop lock (``pipeline/assembly.py``) — that seam is the charter's
    user-surfaced item and is NOT changed here.

    Only the staged-slot **storage** is overridden (the ``_staged`` property
    below shadows the base slot descriptor); ``stage`` / ``clear`` /
    ``invoke`` — including invoke's whole verification chain (adapter present,
    ``RawFetch`` type, source echo, request digest) — are INHERITED from
    :class:`~guanlan_v2.orchestration.data.runtime.DataSourceCapabilityBackend`,
    never copied.
    """

    __slots__ = ("_tls",)

    def __init__(self, adapters: Mapping[str, Any]) -> None:
        # the thread-local store must exist BEFORE the base __init__ writes
        # its initial ``self._staged = None`` through the property below.
        self._tls = threading.local()
        super().__init__(adapters)

    @property
    def _staged(self) -> Any:
        # a thread that never staged reads an honest None — the inherited
        # ``invoke`` then raises its loud misuse DataRuntimeError on the
        # CALLING thread, never consuming another thread's target.
        return getattr(self._tls, "staged", None)

    @_staged.setter
    def _staged(self, value: Any) -> None:
        self._tls.staged = value


_BACKEND: ThreadConfinedDataBackend | None = None
_BACKEND_LOCK = threading.Lock()


def production_data_backend() -> ThreadConfinedDataBackend:
    """The cached ONE production backend (the ``_RECIPE_LOCK`` idiom).

    Backend SHARING is the registered factory's job (gate D-A): Task 4 binds
    ``lambda **kw: production_data_backend()`` for each of the seven data
    capability refs, so every per-invocation factory call returns this ONE
    thread-confined instance.  Double-checked under ``_BACKEND_LOCK`` so
    concurrent first calls build exactly once.
    """
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = ThreadConfinedDataBackend(production_data_adapters())
    return _BACKEND


# --------------------------------------------------------------------------- #
# Task 3 — the per-run world resolver + the production data provider           #
# --------------------------------------------------------------------------- #
_MANIFEST_SCHEMA_REF = SchemaRef(name="DataSnapshotManifest", version="1")

#: the ONE payload namespace the per-run capture lives in.  The producer
#: (:func:`persist_capture`, Lane 0) and the consumer
#: (:meth:`ProductionDataWorldResolver.world_for` step 3, the deep lane) MUST
#: agree, and they live in different processes — so the string is a shared
#: constant here rather than a literal at each end (L2-b Task 7).
PRODUCTION_CAPTURE_NAMESPACE = "main"

#: the three DataContext fields the resolver verifies against the recipe — the
#: recipe property names are identical by construction (Task 1).
_WORLD_DIGEST_FIELDS = (
    "source_registry_digest",
    "routing_snapshot_digest",
    "source_config_digest",
)


class _FrozenContextClock:
    """A frozen clock reading exactly the admitted snapshot's ``ctx.as_of``.

    Gate D-C (pinned empirically at ``test_l2b_handoff.py``):
    ``PitGuard.from_context`` requires ``clock_now(clock) == ctx.as_of``
    EXACTLY, in EVERY mode including ONLINE, and dispatch builds a guard per
    read from the world's clock — so an advancing ``SystemClock`` passes the
    first build and refuses the second.  The world therefore binds THIS
    per-run frozen clock, constructed at :meth:`ProductionDataWorldResolver.
    world_for` from the admitted context — never a wall clock.
    """

    __slots__ = ("_as_of",)

    def __init__(self, as_of: datetime) -> None:
        self._as_of = as_of

    def now(self) -> datetime:
        return self._as_of


class ProductionNoCache:
    """The honest production no-cache — REVIEWED STOPGAP, not silently absent.

    ``get_verified`` always answers ``None``: every ``cache_or_invoke`` row
    probes its first frozen-route entry, and the results still persist as
    evidence through the reader/writer path (nothing is lost, nothing is
    fabricated — a fabricated cache hit is the m-family mutation this class's
    pin guards against).  A verified :class:`~guanlan_v2.orchestration.data.
    snapshot.DataCache` over the persisted evidence is post-L2-b work; when it
    lands it replaces this class at :meth:`ProductionDataWorldResolver.
    world_for`'s one construction site.
    """

    __slots__ = ()

    def get_verified(self, key: Any, *, ctx: Any, manifest: Any, registry: Any,
                     payload_store: Any) -> None:
        return None


def production_data_refusal_audit_sink(
    clock: AuthoritativeClock,
) -> EventRefusalAuditSink:
    """The ONE data-aware refusal audit sink recipe (one body, no fork).

    An :class:`EventRefusalAuditSink` over the reviewed base audit-detail
    registry PLUS :class:`DataFetchRefusalDetails`, sealed — the
    ``data_audit_sink`` composition of the Phase-3 suite.  The data half is
    load-bearing, not decorative: a data refusal reaches the sink as a
    ``DataFetchRefusalDetails@1`` payload, and a sink over the bare base
    registry refuses it with ``UnknownAuditDetailSchema`` instead of recording
    it.

    Callers hoist ONE of these per binding set and pass it through
    :func:`register_production_data_provider`'s
    ``refusal_audit_sink_factory`` (``live_decide.build_production_bindings``,
    L2-b Task 4) — the ``ExperienceRetrievalBackend`` idiom: build once outside
    the factory, hand the SAME instance to every per-run world.  Without the
    hoist the default below builds a fresh sink inside every ``world_for``
    call, and the records it accumulates are unreachable — nothing outside that
    one world ever holds the object.
    """
    registry = default_audit_detail_registry()
    registry.register(DataFetchRefusalDetails)
    registry.seal()
    return EventRefusalAuditSink(detail_registry=registry, clock=clock)


def _default_refusal_audit_sink_factory(clock: AuthoritativeClock):
    """The unhoisted fallback: a fresh sink per world, same ONE recipe.

    Kept for callers that bind no factory (the unit harnesses).  Production
    hoists — see :func:`production_data_refusal_audit_sink`.
    """

    def build() -> EventRefusalAuditSink:
        return production_data_refusal_audit_sink(clock)

    return build


class ProductionDataWorldResolver:
    """Resolve one opened session's :class:`DataRuntimeWorld` — per run,
    never a process-global frozen at binding time.

    Design forced by the verified constraints (Task-0 gate items 2-3): the
    world's ``ctx`` must equal the ADMITTED snapshot's ``data_context`` by
    full value equality, and the admitted snapshot is chosen per run — so the
    world is resolved from the session's own
    ``request.input_snapshot.context_snapshot_ref``.

    ``catalog_runtime`` is the bound bundle runtime the rendered blocks
    resolve their renderer material through (the plan's ``world_for`` step 4
    requires it on the world; it rides the same registration call that hands
    the resolver everything else).
    """

    __slots__ = ("_stores", "_recipe", "_schema_resolver", "_clock",
                 "_sink_factory", "_catalog_runtime")

    def __init__(self, *, stores: Any, recipe: ProductionDataWorldRecipe,
                 schema_resolver: Any, clock: AuthoritativeClock,
                 catalog_runtime: Any,
                 refusal_audit_sink_factory: Any = None) -> None:
        self._stores = stores
        self._recipe = recipe
        self._schema_resolver = schema_resolver
        self._clock = clock
        self._catalog_runtime = catalog_runtime
        self._sink_factory = (
            refusal_audit_sink_factory
            if refusal_audit_sink_factory is not None
            else _default_refusal_audit_sink_factory(clock))

    def world_for(self, request: Any) -> DataRuntimeWorld:
        """The per-run world: admitted snapshot → digest checks → manifest →
        audit-locator check → :class:`DataRuntimeWorld`.  Every refusal is a
        typed loud :class:`DataRuntimeError`; nothing is rebuilt or
        fabricated."""
        # 1) the admitted ContextSnapshot, through EXACTLY the resolution
        #    _verify_context_binding performs (same ref, same reader).
        ctx_ref = request.input_snapshot.context_snapshot_ref
        snapshot = request.reader.get(
            ctx_ref.payload_ref, expected_schema_ref=ctx_ref.schema_ref)
        ctx = snapshot.data_context

        # 2) three loud typed digest checks — each names the failing pair and
        #    the remedy.  A pilot-era context ("a"/"b"/"c" * 64 placeholders)
        #    fails here, before any session, read, gateway begin or lease.
        for field in _WORLD_DIGEST_FIELDS:
            admitted = getattr(ctx, field)
            expected = getattr(self._recipe, field)
            if admitted != expected:
                raise DataRuntimeError(
                    f"the admitted ContextSnapshot's DataContext {field} "
                    f"({admitted}) does not equal the production data-world "
                    f"recipe's ({expected}): the committed ContextSnapshot "
                    "predates the production data world (L2-b); re-run Lane 0"
                )

        # 3) the run's DataSnapshotManifest through the D-D payload-store
        #    channel: content-addressed by ctx.data_snapshot_content_digest
        #    under the ADMITTED plan's schema-registry digest (run-scoped key
        #    discipline).  Absent => typed refusal, NEVER a rebuilt stand-in —
        #    a rebuilt manifest could not honestly carry the run-start
        #    boundary Lane 0 froze.
        digest = ctx.data_snapshot_content_digest
        manifest_ref = self._stores.payloads.find_ref(
            namespace=PRODUCTION_CAPTURE_NAMESPACE,
            schema_ref=_MANIFEST_SCHEMA_REF,
            registry_digest=request.plan.schema_registry_digest,
            content_digest=digest)
        if manifest_ref is None:
            raise DataRuntimeError(
                f"the run's DataSnapshotManifest (content digest {digest}) is "
                "not persisted in the payload store -- manifest not persisted; "
                "the context producer did not run the L2-b recipe (Lane 0 "
                "persists the per-run capture, Task 7).  Refusing rather than "
                "rebuilding a stand-in: a rebuilt manifest could not honestly "
                "carry the run-start boundary"
            )
        manifest = self._stores.payloads.get(
            manifest_ref, expected_schema_ref=_MANIFEST_SCHEMA_REF)
        # 3b) the audit locator agrees.  ``data_snapshot_id`` is SEMANTICALLY
        #     EXCLUDED from both models, so a relocated manifest still matches
        #     the content-digest lookup above and binds.  The disagreement does
        #     surface downstream (``data/reader.py``'s frozen-world proof and
        #     ``registry.py``'s per-request frozen-set check) — but only after
        #     the session is opened, and only as a generic "not one frozen set"
        #     message with no remedy.  Same typed loud family, same remedy, here.
        if manifest.data_snapshot_id != ctx.data_snapshot_id:
            raise DataRuntimeError(
                f"the run's DataSnapshotManifest audit locator data_snapshot_id "
                f"({manifest.data_snapshot_id}) does not equal the admitted "
                f"ContextSnapshot's DataContext data_snapshot_id "
                f"({ctx.data_snapshot_id}): the committed ContextSnapshot "
                "predates the production data world (L2-b); re-run Lane 0"
            )

        # 4) the full world: the recipe's frozen half + the run's admitted
        #    ctx/manifest + the D-C frozen clock + the honest no-cache + the
        #    ONE thread-confined backend.
        return DataRuntimeWorld(
            source_registry=self._recipe.registry,
            routing=self._recipe.routing,
            manifest=manifest,
            source_config=self._recipe.source_config,
            ctx=ctx,
            schema_resolver=self._schema_resolver,
            policy_resolver=self._recipe.policy_resolver,
            calendar=self._recipe.calendar,
            clock=_FrozenContextClock(ctx.as_of),
            cache=ProductionNoCache(),
            catalog_runtime=self._catalog_runtime,
            refusal_audit_sink=self._sink_factory(),
            backend=production_data_backend(),
        )


class _RowlessLicensedEmptyDataSession:
    """The zero-rows session: freeze completes with the catalog-licensed EMPTY.

    The reviewed analyzer summed its bounds over ZERO prefetch rows (0/0), so
    an empty completed :class:`BridgeContribution` is exactly what the sealed
    summary licenses for such a worker (today: the two pv aux workers) — this
    is NOT a refusal, and it is NOT the retired dead-row discrimination (the
    partition upstream is on row COUNT only; post-L1 that category does not
    exist).  Zero I/O, zero gateway begins, zero evidence writes.
    """

    __slots__ = ("_bridge", "_summary")

    def __init__(self, *, bridge: Any, summary: Any) -> None:
        self._bridge = bridge
        self._summary = summary

    def freeze_for_execution(self, *, kind: Any) -> Any:
        from guanlan_v2.orchestration.worker import (
            BridgeContribution,
            BridgeStageOutcome,
        )

        contribution = BridgeContribution(
            bridge_id=self._bridge.bridge_id,
            bridge_priority=self._bridge.priority,
            summary_digest=self._summary.summary_digest,
            tool_call_records=(), data_result_refs=(),
            direct_evidence_refs=(), untrusted_blocks=())
        return BridgeStageOutcome(
            status="completed", frozen_contribution=contribution)


class ProductionDataProvider:
    """The production two-stage ``data.runtime`` provider (L2-b Task 3) —
    registered under the sealed ``bridge.data_runtime.provider@1`` identity by
    :func:`register_production_data_provider` (bound into
    ``build_production_bindings`` at Task 4).

    ``prepare_input`` is DELEGATED to the real provider's I/O-free empty
    prepare (never a third copy).  ``open_execution`` partitions on row COUNT
    only:

    * **zero rows** → :class:`_RowlessLicensedEmptyDataSession` (the
      catalog-licensed EMPTY freeze — the analyzer bounds are 0/0);
    * **rows present** → resolve the per-run world via
      :meth:`ProductionDataWorldResolver.world_for` and delegate the WHOLE
      session to the real :class:`DataRuntimeBridgeProvider`, constructed
      THROUGH its own reviewed :func:`data_runtime_provider_factory` — zero
      re-implementation of the Phase-3 dispatch/PitGuard/render machinery, and
      the ONE production caller the fact-F AST pins enumerate (L2-b Task 4's
      conscious flip: routing through the single named factory symbol keeps
      that pin a single-symbol pin instead of a class-name scan).
      Never an EMPTY freeze over a row that could have been read (the
      silenced-outage shape every tombstone in this lineage forbids).

    **The run-subject source (L2-b Task 5 — the L1<->L2-b integration seam).**
    ``subject_params`` is the run's committed subject projection
    (:class:`~guanlan_v2.orchestration.data.runtime.SubjectParams`), carried
    construction-side and threaded into the DELEGATED session, where
    ``_assemble_params`` consumes it as the second, closed source for the
    row's ``node_param`` pointers. It is what makes the sealed
    ``dec.pm``/``verified_snapshot`` row resolvable at all: that row's
    ``InstrumentUniverseParams.symbols`` is a strict tuple no legal
    ``PlanNode.params`` can carry.

    ``None`` is the honest UNBOUND posture and is what
    :func:`register_production_data_provider` binds process-level — a run
    subject is service-stamped PER RUN, so only the per-run factories view
    (``pipeline/assembly.py::_SubjectScopedFactories``, built over
    :func:`production_data_provider_factory`) ever binds one. Unbound, a
    rows-present worker refuses at the runner seam inside the real session
    (the surviving semantics of L1's retired worldless shape 3) rather than
    reading with a guessed subject.
    """

    __slots__ = ("_bridge", "_summary", "_resolver", "_subject_params")

    def __init__(self, *, bridge: Any, summary: Any,
                 resolver: ProductionDataWorldResolver,
                 subject_params: SubjectParams | None = None) -> None:
        self._bridge = bridge
        self._summary = summary
        self._resolver = resolver
        self._subject_params = subject_params

    def prepare_input(self, request: Any) -> Any:
        # DELEGATED, never a third copy: the real provider's prepare touches
        # only self._bridge / self._summary (both carried here), so the
        # unbound forward IS its exact logic — bit-for-bit, pinned by test.
        return DataRuntimeBridgeProvider.prepare_input(self, request)

    def open_execution(self, request: Any) -> Any:
        binding = parse_prefetch_binding(request.bridge.config_bytes)
        if binding.bridge_id != self._bridge.bridge_id:
            raise DataRuntimeError("prefetch binding names a different bridge")
        rows = tuple(op for op in binding.operations
                     if op.worker_id == request.worker.id)
        if not rows:
            # row COUNT is the ONLY partition (post-L1 no other category
            # exists): a rowless worker's EMPTY freeze is catalog-licensed,
            # and the world is not even resolved for it.
            return _RowlessLicensedEmptyDataSession(
                bridge=self._bridge, summary=self._summary)
        world = self._resolver.world_for(request)
        return data_runtime_provider_factory(world, subject_params=self._subject_params)(
            bridge=self._bridge, summary=self._summary,
        ).open_execution(request)


def production_data_provider_factory(
    subject_params: SubjectParams | None = None, *,
    stores: Any, schema_resolver: Any, clock: AuthoritativeClock,
    catalog_runtime: Any, refusal_audit_sink_factory: Any = None,
):
    """The ONE construction recipe for a bound ``data.runtime`` provider factory
    (L2-b Task 5 — the L1<->L2-b integration seam).

    Returns the provider-handler factory (``factory(*, bridge, summary)``) that
    hands out a :class:`ProductionDataProvider` over a
    :class:`ProductionDataWorldResolver` built from the caller's own
    collaborators plus the module-cached :func:`production_data_recipe` and
    :func:`production_data_backend`. ONE recipe, two callers, differing in
    exactly one argument — the run's subject projection:

    * :func:`register_production_data_provider` (process level, via
      ``live_decide.build_production_bindings``) leaves ``subject_params``
      UNBOUND — a run subject is per-run and cannot honestly live on a
      process-level binding;
    * ``pipeline/assembly.py::build_production_plan_runner._plan_executor``
      calls it with the run's stamped projection and installs the result as
      the SINGLE override of the thin per-run ``_SubjectScopedFactories`` view
      over the same base registry.

    **Why the view builds its own provider instead of decorating the base's.**
    The view must serve the sealed provider ref even on a bundle where the
    base holds NO binding for it (the pilot catalog is exactly that, pinned by
    ``test_pipeline_assembly.py``'s negative ``register_handler`` arm), and it
    must never mutate the base. Wiring divergence between the two call sites
    is the real risk that trade takes on, so it is pinned by OBJECT IDENTITY
    against the runner's own seam objects
    (``test_the_views_provider_is_wired_from_the_runners_own_seam_objects``),
    including the refusal-audit sink — production hands BOTH seams the same
    hoisted data-aware sink.

    Construction is I/O-free apart from the recipe's first build (committed
    calendar material bytes); it performs no store, reader or gateway touch,
    so a caller may build it before a run and a broken recipe still refuses at
    the earliest possible point.
    """
    resolver = ProductionDataWorldResolver(
        stores=stores, recipe=production_data_recipe(),
        schema_resolver=schema_resolver, clock=clock,
        catalog_runtime=catalog_runtime,
        refusal_audit_sink_factory=refusal_audit_sink_factory)

    def factory(*, bridge: Any, summary: Any) -> ProductionDataProvider:
        return ProductionDataProvider(
            bridge=bridge, summary=summary, resolver=resolver,
            subject_params=subject_params)

    return factory


def register_production_data_provider(
    *, factories: Any, stores: Any, schema_resolver: Any,
    clock: AuthoritativeClock, catalog_runtime: Any,
    refusal_audit_sink_factory: Any = None,
) -> None:
    """The ONE production ``data.runtime`` registration recipe (L2-b Task 3).

    Builds :func:`production_data_recipe` — through
    :func:`production_data_provider_factory`, the shared construction recipe
    (L2-b Task 5; no fork) — so a broken recipe (missing calendar material,
    digest drift) kills the caller at registration time, before any lease: the
    burned-lease precedent. Binds that factory under the exact sealed
    ``bridge.data_runtime.provider@1`` identity
    (``phase3_data_surface().provider_ref``), and registers all seven data
    capability backends via the gate D-A per-invocation shape
    (``lambda **kw: production_data_backend()`` — the ONE thread-confined
    instance handed out on every confined invoke).

    The process-level binding is deliberately SUBJECT-UNBOUND: this recipe
    takes no ``subject_params`` knob at all, because a run subject is stamped
    per run and the only honest place to bind one is the per-run factories
    view (see :func:`production_data_provider_factory`).

    Sole intended production caller: ``live_decide.build_production_bindings``
    (Task 4 superseded the worldless incumbent registration with this recipe).
    """
    factories.register_handler(
        phase3_data_surface().provider_ref,
        production_data_provider_factory(
            stores=stores, schema_resolver=schema_resolver, clock=clock,
            catalog_runtime=catalog_runtime,
            refusal_audit_sink_factory=refusal_audit_sink_factory))
    for _method_id, cap_ref in sorted(data_capability_refs().items()):
        factories.register_capability_backend(
            cap_ref, lambda **kw: production_data_backend())


# --------------------------------------------------------------------------- #
# Task 7 — the PRODUCER half: the per-run ONLINE capture + its D-D persistence  #
# --------------------------------------------------------------------------- #
#: the exchange wall-clock zone every A-share session date is reduced under.
#: The same zone the reviewed ONLINE/PIT_REPLAY adapters localize a naive vendor
#: ``pulled_at`` with (``adapters/live_data.py`` / ``adapters/replay_data.py``
#: ``_EXCHANGE_TZ``) and the one ``adapters/api.py`` names as the session zone —
#: never a second convention. ``PitGuard`` reduces an instant to a session date
#: through ``ctx.clock.timezone`` (``data/pit.py``), so this string is
#: load-bearing, not decorative.
PRODUCTION_CAPTURE_TIMEZONE = "Asia/Shanghai"


def build_production_capture(
    *, clock: AuthoritativeClock, data_snapshot_id: str,
) -> tuple[DataSnapshotManifest, DataContext]:
    """The run's ONLINE capture root + its start-frozen :class:`DataContext`.

    The producer half of the ONE recipe (L2-b Task 7).  Composition only — both
    halves are the reviewed Phase-9 ONLINE builders
    (:func:`~guanlan_v2.orchestration.adapters.live_data.build_online_capture_manifest`
    and
    :func:`~guanlan_v2.orchestration.adapters.live_data.build_online_data_context`)
    over :func:`production_data_recipe`'s frozen half; nothing is
    re-implemented and no digest is invented.  The returned context therefore
    carries the recipe's MEASURED ``source_registry`` / ``routing_snapshot`` /
    ``source_config`` digests — the exact three
    :meth:`ProductionDataWorldResolver.world_for` compares — instead of
    ``presets.pilot_data_context``'s ``"a"``/``"b"``/``"c"`` placeholders.

    **The clock is read twice, deliberately, and disagreement is LOUD.**  The
    manifest needs the boundary before it exists, and the reviewed
    ``build_data_context`` reads the clock itself (exactly once, freezing
    ``as_of`` into the context) and then requires ``as_of == manifest.as_of``.
    Handing it the SAME clock keeps that equality check a real tripwire: a
    caller that passes an advancing clock gets a typed
    :class:`~guanlan_v2.orchestration.data.errors.SnapshotMismatchError` rather
    than a manifest and a context frozen at two different instants.  Production
    (``lane0_driver.run_lane0_bootstrap``) passes its session-frozen clock, so
    the two readings are the same +08:00 session stamp the plan already carries.

    ``data_snapshot_id`` is the capture root's AUDIT locator — semantically
    excluded from the manifest digest (``DataSnapshotManifest.SEMANTIC_EXCLUDE``)
    and therefore invisible to :func:`persist_capture`'s content-addressed
    lookup.  It must consequently be a function of what the capture already
    encodes (the session + the frozen recipe) and never of a per-attempt run
    identity; see :func:`persist_capture`'s relocation refusal.
    """
    recipe = production_data_recipe()
    as_of = clock_now(clock)
    manifest = build_online_capture_manifest(
        data_snapshot_id=data_snapshot_id,
        as_of=as_of,
        timezone=PRODUCTION_CAPTURE_TIMEZONE,
        calendar_id=recipe.calendar.calendar_id,
        routing_snapshot_digest=recipe.routing_snapshot_digest,
        # the routing snapshot is the authority on which schema registry this
        # world was frozen against; build_data_context re-asserts equality.
        schema_registry_digest=recipe.routing.schema_registry_digest,
    )
    ctx = build_online_data_context(
        clock=clock,
        source_config=recipe.source_config,
        source_registry=recipe.registry.snapshot(),
        routing=recipe.routing,
        manifest=manifest,
    )
    return manifest, ctx


def persist_capture(
    *, stores_or_archive: Any, registry_digest: str,
    manifest: DataSnapshotManifest,
) -> PayloadRef:
    """Persist one capture root through the D-D channel the resolver READS.

    The channel is the payload store, keyed exactly as
    :meth:`ProductionDataWorldResolver.world_for` step 3 keys its lookup:
    ``namespace=PRODUCTION_CAPTURE_NAMESPACE``, ``DataSnapshotManifest@1``, the
    admitted plan's ``registry_digest``, and the manifest's CONTENT digest.
    There is no second "archive" surface — inventing one would create a channel
    the consumer cannot read — so ``stores_or_archive`` (the brief's parameter
    name) is the ``RuntimeStores``-shaped object whose ``.payloads`` is that
    store.

    **Idempotent by content digest.**  A capture already held under this content
    digest is BOUND, not rewritten — the driver's own ContextSnapshot idiom
    (``lane0_driver.py``: "bind what the store already holds for this exact
    semantic identity, and write only when nothing holds it yet. A read, not a
    relaxation").  The write-once byte check is untouched.

    **Relocation refuses, loudly and here.**  ``data_snapshot_id`` is excluded
    from the digest, so a stored capture with the same content but a DIFFERENT
    audit locator is indistinguishable to ``find_ref`` — binding it would commit
    a ContextSnapshot whose ``data_snapshot_id`` names a locator the store does
    not hold, and the deep lane would later refuse that snapshot with the
    resolver's ``data_snapshot_id``-mismatch message ("the committed
    ContextSnapshot predates the production data world"), which is both false
    and unactionable.  The producer therefore refuses at the real cause,
    naming both locators.

    **The two registry digests must AGREE, and disagreement refuses here.**
    ``registry_digest`` is the key half of the D-D channel (the admitted plan's
    schema-registry digest, which is what ``world_for`` step 3 filters
    ``find_ref`` on), while ``manifest.schema_registry_digest`` is the registry
    the routing snapshot froze the world against. In production both are the
    sealed Phase-9 digest. If they ever diverged, ``payloads.put``'s idempotency
    check compares content digest / namespace / schema only, so the capture
    would bind silently under the OTHER key and ``find_ref`` could not locate
    it: the deep lane would report "manifest not persisted; the context producer
    did not run the L2-b recipe" — a false accusation, after the run, with
    nothing red in the tree (L2-b Task 7 review M3).
    """
    if registry_digest != manifest.schema_registry_digest:
        raise DataRuntimeError(
            f"the capture would be persisted under schema-registry digest "
            f"{registry_digest}, but the manifest was frozen against "
            f"{manifest.schema_registry_digest}. The deep lane's resolver looks "
            "the manifest up under the ADMITTED PLAN's registry digest, so a "
            "capture bound under any other key is unreachable and surfaces as "
            "the (false) 'manifest not persisted' refusal at run time. Both "
            "halves must carry the one sealed registry digest"
        )
    payloads = stores_or_archive.payloads
    existing = payloads.find_ref(
        namespace=PRODUCTION_CAPTURE_NAMESPACE, schema_ref=_MANIFEST_SCHEMA_REF,
        registry_digest=registry_digest,
        content_digest=manifest.content_digest)
    if existing is not None:
        stored = payloads.get(existing, expected_schema_ref=_MANIFEST_SCHEMA_REF)
        if stored.data_snapshot_id != manifest.data_snapshot_id:
            raise DataRuntimeError(
                f"a data capture with content digest {manifest.content_digest} "
                f"is already persisted under the audit locator "
                f"{stored.data_snapshot_id!r}, but this capture names "
                f"{manifest.data_snapshot_id!r}. data_snapshot_id is excluded "
                "from the manifest digest, so the two are ONE payload to the "
                "deep lane's content-addressed lookup and the committed "
                "ContextSnapshot would name a locator nothing resolves. Derive "
                "the capture locator from the session + recipe (what the "
                "capture content already encodes), never from a per-attempt "
                "run identity"
            )
        return existing
    return payloads.put(
        _MANIFEST_SCHEMA_REF, manifest, registry_digest=registry_digest,
        namespace=PRODUCTION_CAPTURE_NAMESPACE,
        idempotency_key=f"data-capture:{manifest.content_digest}")
