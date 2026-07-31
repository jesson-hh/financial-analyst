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

from guanlan_v2.orchestration.adapters.live_data import LiveClientSource
from guanlan_v2.orchestration.data.calendar import (
    ImmutableTradingCalendar,
    TradingCalendarMaterial,
    TradingCalendarResolver,
)
from guanlan_v2.orchestration.data.errors import RoutingConfigurationError
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.registry import DataSourceRegistry
from guanlan_v2.orchestration.data.runtime import DataSourceCapabilityBackend
from guanlan_v2.orchestration.data.snapshot import (
    DataRoutingSnapshot,
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
from guanlan_v2.orchestration.refs import ContentRef

__all__ = [
    "PRODUCTION_DATA_REGISTRY_VERSION",
    "PRODUCTION_ROUTING_AUDIT_ID",
    "PRODUCTION_CALENDAR_MATERIAL_PATH",
    "PRODUCTION_LIMIT_POLICY_ID",
    "PRODUCTION_ROUTE_POLICY_REF",
    "ProductionDataWorldRecipe",
    "ThreadConfinedDataBackend",
    "production_data_adapters",
    "production_data_backend",
    "production_data_recipe",
]

#: frozen identity constants (Task 1 interface).
PRODUCTION_DATA_REGISTRY_VERSION = "prod-data-v1"
PRODUCTION_ROUTING_AUDIT_ID = "prod-data-routing-v1"
PRODUCTION_LIMIT_POLICY_ID = "policy.limit.cn-a-share-prod"

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
    # the surface's elapsed freshness policy, unchanged.
    registry.register_freshness(surf.freshness_policy)
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
    """Exactly ``{"guanlan.datafeed": LiveClientSource()}``.

    The key is the sealed surface identity's id — ``phase3_data_surface()``'s
    ``source_ref.id``, the exact string the backend resolves each staged
    ``source_ref.id`` against (the sealed identity is the registration key,
    never new bytes).  The value is the **facade-default** construction of the
    reviewed :class:`~guanlan_v2.orchestration.adapters.live_data.LiveClientSource`
    (the real ``guanlan_v2.datafeed.live_client`` facade, lazily imported at
    call time — no import-time vendor touch).  Adapters WRAP reviewed readers
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
    """
    return {phase3_data_surface().source_ref.id: LiveClientSource()}


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
