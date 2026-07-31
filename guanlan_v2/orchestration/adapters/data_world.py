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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from guanlan_v2.orchestration.data.calendar import (
    ImmutableTradingCalendar,
    TradingCalendarMaterial,
    TradingCalendarResolver,
)
from guanlan_v2.orchestration.data.catalog import phase3_data_surface
from guanlan_v2.orchestration.data.registry import DataSourceRegistry
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
    """
    p = path if path is not None else _REPO_ROOT / PRODUCTION_CALENDAR_MATERIAL_PATH
    doc = json.loads(p.read_text(encoding="utf-8"))
    material = TradingCalendarMaterial(
        calendar_id=doc["calendar_id"], sessions=tuple(doc["sessions"])
    )
    ref = ContentRef(
        id=doc["material_id"],
        version=doc["material_version"],
        content_digest=doc["content_digest"],
    )
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
                    effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
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


def production_data_recipe() -> ProductionDataWorldRecipe:
    """The cached production data-world recipe (the ``phase3_data_surface`` idiom).

    Pure; no I/O beyond reading the committed calendar material file.  Any
    build failure (missing material, digest drift, unsealed registry) raises
    loudly at first use — Task 4 binds that first use to binding construction
    so a broken recipe kills the deep lane at startup, before any lease.
    """
    global _RECIPE
    if _RECIPE is None:
        _RECIPE = _build_recipe(
            schema_registry_digest=_production_chain_registry_digest()
        )
    return _RECIPE
