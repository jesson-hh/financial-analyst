# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — the routing / snapshot / cache ABI + the sole runtime
:class:`DataContext` constructor.

This module freezes *how a run's data universe is described and cached* without
ever fetching, reading a clock directly or opening a store. Its load-bearing
rules (locked by ``tests/orchestration/data/test_snapshot.py``):

* **The routing snapshot is the sole producer of resolved vendor chains.**
  :func:`build_data_routing_snapshot` derives ``resolved_vendor_chains`` from the
  explicit ordered method routes; registry insertion order is never a routing
  policy, and :func:`build_data_context` copies that exact chain map.
* **A snapshot manifest's locator is audit-only.** ``data_snapshot_id`` is excluded
  from the semantic projection, so relocating byte-identical manifest content
  changes audit/dereference identity only. A ``pit_frozen`` manifest is complete
  and immutable for replay; an ``online_capture_root`` freezes the run-start
  boundary/routing but appends recorded results without mutating its digest.
* **A cache key binds semantics, never a locator.** :class:`DataCacheKey` never uses
  ``data_snapshot_id``; a snapshot-id / object-id relocation alone is cache-
  invariant, while a mismatched snapshot-content / result / PIT / schema digest is
  rejected. In ``PIT_REPLAY`` a miss may continue only to an explicitly frozen
  ``PIT_STORE``; ``CACHE``-only and every ``LIVE`` continuation are refused.
"""
from __future__ import annotations

from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import model_validator

from guanlan_v2.orchestration.context import ClockSpec, DataContext
from guanlan_v2.orchestration.data.errors import (
    LiveFallbackRefused,
    RoutingConfigurationError,
    SnapshotMismatchError,
)
from guanlan_v2.orchestration.data.result import PitAudit
from guanlan_v2.orchestration.data.source import (
    DataSourceRegistrySnapshot,
    ResolvedMethodRoute,
    RouteEntry,
    VerifiedDataCacheHit,
)
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataBackend, DataMode
from guanlan_v2.orchestration.refs import (
    ContentRef,
    LogicalId,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now

__all__ = [
    "DataSourceConfigSnapshot",
    "build_data_source_config_snapshot",
    "ResolvedMethodRoute",  # single definition lives in source.py; snapshot re-exports
    "RouteEntry",
    "DataRoutingSnapshot",
    "build_data_routing_snapshot",
    "DataSnapshotEntry",
    "DataSnapshotManifest",
    "build_data_snapshot_manifest",
    "build_data_context",
    "DataCacheKey",
    "build_data_cache_key",
    "DataCacheEntry",
    "build_data_cache_entry",
    "DataCache",
    "cache_miss_continuation",
    "SECRET_KEY_TOKENS",
    "SNAPSHOT_PUBLIC_MODELS",
    "SNAPSHOT_INTERNAL_MODELS",
]

_DIGEST_PLACEHOLDER = "0" * 64
#: substrings that mark a config key as a credential (never allowed in a snapshot).
SECRET_KEY_TOKENS: frozenset[str] = frozenset(
    {"password", "passwd", "secret", "token", "api_key", "apikey", "credential", "private_key"}
)


def _scan_secrets(obj: Any, path: str = "config") -> None:
    """Recursively reject any credential-looking key in a raw option mapping."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            low = str(k).lower()
            if any(tok in low for tok in SECRET_KEY_TOKENS):
                raise ValueError(
                    f"credential-looking key {k!r} at {path}: secrets never enter a "
                    "DataSourceConfigSnapshot; the gateway owns them"
                )
            _scan_secrets(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _scan_secrets(v, f"{path}[{i}]")


# --------------------------------------------------------------------------- #
# DataSourceConfigSnapshot                                                     #
# --------------------------------------------------------------------------- #
class DataSourceConfigSnapshot(DigestModel):
    """Normalized, non-secret method/source selections + per-source option digests.

    Credential values never enter this model — only method→source selections and
    the ``sha256+cjson-v1`` digest of each source's non-secret options. The declared
    ``source_config_digest`` is builder-verified.
    """

    schema_version: Literal["1"] = "1"
    config_version: NonEmptyStr
    method_selections: dict[LogicalId, LogicalId]
    option_digests: dict[LogicalId, DigestHex]
    source_config_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"source_config_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataSourceConfigSnapshot":
        if self.source_config_digest != self.semantic_digest():
            raise ValueError("declared source_config_digest does not match canonical digest")
        return self


def build_data_source_config_snapshot(
    *, config_version: str, method_selections: dict[str, str], source_options: dict[str, Any]
) -> DataSourceConfigSnapshot:
    """Seal a config snapshot, rejecting credential-looking option keys.

    ``source_options`` maps a source id to its *raw* non-secret option dict; each is
    scanned for credential keys (rejected) and reduced to one content digest so no
    raw option value is persisted.
    """
    _scan_secrets(source_options, "source_options")
    option_digests = {sid: content_digest(opts) for sid, opts in source_options.items()}
    fields = dict(
        config_version=config_version,
        method_selections=dict(method_selections),
        option_digests=option_digests,
    )
    try:
        digest = DataSourceConfigSnapshot.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = _DIGEST_PLACEHOLDER
    return DataSourceConfigSnapshot(**fields, source_config_digest=digest)


# --------------------------------------------------------------------------- #
# DataRoutingSnapshot — the SOLE producer of resolved_vendor_chains             #
# --------------------------------------------------------------------------- #
class DataRoutingSnapshot(DigestModel):
    """Canonically keyed method routes + the derived resolved vendor chains.

    ``audit_id`` is audit-only; the source-registry / schema-registry / source-config
    digests, the sorted method routes and the derived ``resolved_vendor_chains`` are
    semantic. Its builder is the sole producer of ``DataContext.resolved_vendor_chains``.
    """

    schema_version: Literal["1"] = "1"
    audit_id: NonEmptyStr
    source_registry_digest: DigestHex
    schema_registry_digest: DigestHex
    source_config_digest: DigestHex
    method_routes: tuple[ResolvedMethodRoute, ...]
    resolved_vendor_chains: dict[LogicalId, tuple[LogicalId, ...]]
    routing_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"audit_id"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"routing_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataRoutingSnapshot":
        keys = [(r.method_ref.id, r.method_ref.version) for r in self.method_routes]
        if len(set(keys)) != len(keys):
            raise ValueError("method_routes must be unique by method ref")
        if keys != sorted(keys):
            raise ValueError("method_routes must be canonically sorted")
        derived = _derive_vendor_chains(self.method_routes)
        if self.resolved_vendor_chains != derived:
            raise ValueError(
                "resolved_vendor_chains must be derived from the ordered method routes "
                "(registry insertion order is never a routing policy)"
            )
        if self.routing_digest != self.semantic_digest():
            raise ValueError("declared routing_digest does not match canonical digest")
        return self


def _derive_vendor_chains(routes: tuple[ResolvedMethodRoute, ...]) -> dict[str, tuple[str, ...]]:
    return {r.method_ref.id: tuple(e.source_ref.id for e in r.entries) for r in routes}


def build_data_routing_snapshot(
    *,
    audit_id: str,
    source_registry: DataSourceRegistrySnapshot,
    schema_registry_digest: str,
    source_config: DataSourceConfigSnapshot,
    method_routes: tuple[ResolvedMethodRoute, ...],
) -> DataRoutingSnapshot:
    """Seal a routing snapshot, deriving the resolved vendor chains from the routes.

    Rejects a route whose method is not in the source registry snapshot, then sorts
    routes canonically and derives the chain map from their explicit order.
    """
    routes = tuple(sorted(method_routes, key=lambda r: (r.method_ref.id, r.method_ref.version)))
    known_methods = {(s.method_id, s.method_version) for s in source_registry.method_specs}
    for r in routes:
        key = (r.method_ref.id, r.method_ref.version)
        if key not in known_methods:
            raise RoutingConfigurationError(
                f"route method {key[0]}@{key[1]} is not in the source registry snapshot"
            )
    fields = dict(
        audit_id=audit_id,
        source_registry_digest=source_registry.source_registry_digest,
        schema_registry_digest=schema_registry_digest,
        source_config_digest=source_config.source_config_digest,
        method_routes=routes,
        resolved_vendor_chains=_derive_vendor_chains(routes),
    )
    try:
        digest = DataRoutingSnapshot.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = _DIGEST_PLACEHOLDER
    return DataRoutingSnapshot(**fields, routing_digest=digest)


# --------------------------------------------------------------------------- #
# DataSnapshotEntry + DataSnapshotManifest                                     #
# --------------------------------------------------------------------------- #
class DataSnapshotEntry(DigestModel):
    """One dataset/method/source/revision identity + payload schema/content/max-avail.

    ``content_digest`` is the digest of the *referenced payload* (an ordinary
    semantic field, like a :class:`ContentRef`'s digest). It never embeds a
    cache-key digest; cache keys bind the completed manifest in one direction,
    avoiding a digest cycle.
    """

    schema_version: Literal["1"] = "1"
    dataset_id: LogicalId
    method_id: LogicalId
    source_id: LogicalId
    revision_id: NonEmptyStr | None = None
    payload_schema_ref: SchemaRef
    content_digest: DigestHex
    max_available_at: UtcDateTime

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.dataset_id, self.method_id, self.source_id, self.revision_id or "")


class DataSnapshotManifest(DigestModel):
    """The frozen data-snapshot manifest (``pit_frozen`` or ``online_capture_root``).

    ``data_snapshot_id`` is audit-only (excluded from the semantic projection), so
    relocating byte-identical content changes audit/dereference identity only. The
    ``vintage_manifest_digest`` is computed from the sorted vintage-entry projection
    and excludes itself; the overall ``content_digest`` is then computed without
    either declared digest self-referencing. A ``pit_frozen`` manifest is complete
    and immutable for replay; an ``online_capture_root`` freezes the run-start
    boundary/routing — subsequently recorded DataResults append *under* that root
    without mutating it.
    """

    schema_version: Literal["1"] = "1"
    data_snapshot_id: NonEmptyStr
    manifest_kind: Literal["pit_frozen", "online_capture_root"]
    as_of: UtcDateTime
    mode: DataMode
    timezone: NonEmptyStr
    calendar_id: NonEmptyStr
    routing_snapshot_digest: DigestHex
    schema_registry_digest: DigestHex
    entries: tuple[DataSnapshotEntry, ...] = ()
    vintage_manifest_digest: DigestHex
    content_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"data_snapshot_id"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"vintage_manifest_digest", "content_digest"}
    )

    @model_validator(mode="after")
    def _verify(self) -> "DataSnapshotManifest":
        keys = [e.key for e in self.entries]
        if keys != sorted(keys):
            raise ValueError("snapshot entries must be canonically sorted")
        if len(set(keys)) != len(keys):
            raise ValueError("snapshot entries must be duplicate-free")
        if self.mode is DataMode.PIT_REPLAY and self.manifest_kind != "pit_frozen":
            raise ValueError("a PIT_REPLAY snapshot manifest must be pit_frozen")
        if self.vintage_manifest_digest != content_digest(list(self.entries)):
            raise ValueError("declared vintage_manifest_digest does not match the sorted entries")
        if self.content_digest != self.semantic_digest():
            raise ValueError("declared content_digest does not match canonical digest")
        return self


def build_data_snapshot_manifest(
    *,
    data_snapshot_id: str,
    manifest_kind: str,
    as_of: Any,
    mode: DataMode,
    timezone: str,
    calendar_id: str,
    routing_snapshot_digest: str,
    schema_registry_digest: str,
    entries: tuple[DataSnapshotEntry, ...] = (),
) -> DataSnapshotManifest:
    """Seal a snapshot manifest: sort entries, then seal the vintage + content digests."""
    ordered = tuple(sorted(entries, key=lambda e: e.key))
    vintage = content_digest(list(ordered))
    fields = dict(
        data_snapshot_id=data_snapshot_id, manifest_kind=manifest_kind, as_of=as_of,
        mode=mode, timezone=timezone, calendar_id=calendar_id,
        routing_snapshot_digest=routing_snapshot_digest,
        schema_registry_digest=schema_registry_digest, entries=ordered,
        vintage_manifest_digest=vintage,
    )
    try:
        content = DataSnapshotManifest.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        content = _DIGEST_PLACEHOLDER
    return DataSnapshotManifest(**fields, content_digest=content)


# --------------------------------------------------------------------------- #
# build_data_context — the sole Phase-3 runtime DataContext constructor         #
# --------------------------------------------------------------------------- #
def build_data_context(
    clock: AuthoritativeClock,
    *,
    mode: DataMode,
    backend: DataBackend,
    source_config: DataSourceConfigSnapshot,
    source_registry: DataSourceRegistrySnapshot,
    routing: DataRoutingSnapshot,
    manifest: DataSnapshotManifest,
) -> DataContext:
    """Fill the exact Phase-1 :class:`DataContext` fields from the frozen inputs.

    Reads the frozen ``as_of`` through the injected clock and requires it to equal
    the manifest as-of; verifies exact digest/mode/calendar consistency across the
    config / registry / routing / manifest; and enforces the mode/manifest matrix:
    ``PIT_REPLAY`` accepts only a complete ``pit_frozen`` manifest (non-LIVE
    backend, vintage present, ``strict_pit=True``); ``ONLINE`` uses an
    ``online_capture_root`` with per-result persisted evidence.
    """
    as_of = clock_now(clock)
    if as_of != manifest.as_of:
        raise SnapshotMismatchError(
            f"clock as_of {as_of.isoformat()} does not equal the manifest as_of "
            f"{manifest.as_of.isoformat()}"
        )
    if manifest.mode is not mode:
        raise SnapshotMismatchError("manifest mode does not match the requested mode")
    if routing.source_config_digest != source_config.source_config_digest:
        raise SnapshotMismatchError(
            "routing source_config_digest does not match the config snapshot"
        )
    if routing.source_registry_digest != source_registry.source_registry_digest:
        raise SnapshotMismatchError(
            "routing source_registry_digest does not match the registry snapshot"
        )
    if manifest.routing_snapshot_digest != routing.routing_digest:
        raise SnapshotMismatchError("manifest routing digest does not match the routing snapshot")
    if manifest.schema_registry_digest != routing.schema_registry_digest:
        raise SnapshotMismatchError(
            "manifest schema_registry_digest does not match the routing snapshot"
        )

    if mode is DataMode.PIT_REPLAY:
        if backend is DataBackend.LIVE:
            raise LiveFallbackRefused("PIT_REPLAY may never bind a LIVE backend")
        if manifest.manifest_kind != "pit_frozen":
            raise SnapshotMismatchError("PIT_REPLAY requires a complete pit_frozen manifest")
        vintage: str | None = manifest.vintage_manifest_digest
        strict_pit = True
    else:  # ONLINE
        if manifest.manifest_kind != "online_capture_root":
            raise SnapshotMismatchError("ONLINE requires an online_capture_root manifest")
        vintage = None
        strict_pit = False

    clock_spec = ClockSpec(
        as_of=as_of, timezone=manifest.timezone, calendar_id=manifest.calendar_id
    )
    return DataContext(
        as_of=as_of,
        clock=clock_spec,
        mode=mode,
        backend=backend,
        strict_pit=strict_pit,
        calendar_id=manifest.calendar_id,
        resolved_vendor_chains=dict(routing.resolved_vendor_chains),
        source_config_digest=source_config.source_config_digest,
        source_registry_digest=source_registry.source_registry_digest,
        routing_snapshot_digest=routing.routing_digest,
        data_snapshot_id=manifest.data_snapshot_id,
        data_snapshot_content_digest=manifest.content_digest,
        vintage_manifest_digest=vintage,
        built_at=as_of,
    )


# --------------------------------------------------------------------------- #
# Cache ABI                                                                    #
# --------------------------------------------------------------------------- #
class DataCacheKey(DigestModel):
    """A method/source/params + semantic-digest cache key (never a locator).

    Binds the method/source refs, the exact result schema, the canonical params
    digest, as-of, mode/backend and every semantic snapshot-content / vintage /
    source-config / routing / source-registry / schema-registry digest. It never
    uses ``data_snapshot_id`` as a semantic key dimension. ``key_digest`` self-seals.
    """

    schema_version: Literal["1"] = "1"
    method_ref: ContentRef
    source_ref: ContentRef
    result_schema_ref: SchemaRef
    params_digest: DigestHex
    as_of: UtcDateTime
    mode: DataMode
    backend: DataBackend
    data_snapshot_content_digest: DigestHex
    vintage_manifest_digest: DigestHex | None = None
    source_config_digest: DigestHex
    routing_snapshot_digest: DigestHex
    source_registry_digest: DigestHex
    schema_registry_digest: DigestHex
    key_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"key_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataCacheKey":
        if self.key_digest != self.semantic_digest():
            raise ValueError("declared key_digest does not match canonical digest")
        return self


def build_data_cache_key(**fields: Any) -> DataCacheKey:
    """Seal a :class:`DataCacheKey` from its semantic dimensions."""
    try:
        digest = DataCacheKey.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = _DIGEST_PLACEHOLDER
    return DataCacheKey(**fields, key_digest=digest)


class DataCacheEntry(DigestModel):
    """A cache key bound to an exact main typed result ref + result/PIT digests.

    Result SchemaRef / content / PIT identity is semantic; the ``result_audit_digest``
    (and the nested ``result_ref.payload_ref.object_id``, via the Phase-1
    :class:`PayloadRef` projection) is audit/dereference identity and cannot perturb
    semantic cache equivalence.
    """

    schema_version: Literal["1"] = "1"
    key: DataCacheKey
    result_ref: TypedPayloadRef
    result_content_digest: DigestHex
    result_audit_digest: DigestHex
    pit_audit_digest: DigestHex
    entry_digest: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"result_audit_digest"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"entry_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataCacheEntry":
        if self.result_ref.payload_ref.namespace != "main":
            raise ValueError("DataCacheEntry.result_ref must be main-namespace")
        if self.result_ref.payload_ref.content_digest != self.result_content_digest:
            raise ValueError("result_ref content digest must equal result_content_digest")
        if self.result_ref.schema_ref != self.key.result_schema_ref:
            raise ValueError("result_ref schema must equal the cache key's result schema")
        if self.entry_digest != self.semantic_digest():
            raise ValueError("declared entry_digest does not match canonical digest")
        return self


def build_data_cache_entry(
    *,
    key: DataCacheKey,
    result_ref: TypedPayloadRef,
    result_content_digest: str,
    result_audit_digest: str,
    pit_audit_digest: str,
) -> DataCacheEntry:
    """Seal a :class:`DataCacheEntry` (audit digest excluded from the semantic seal)."""
    fields = dict(
        key=key, result_ref=result_ref, result_content_digest=result_content_digest,
        result_audit_digest=result_audit_digest, pit_audit_digest=pit_audit_digest,
    )
    try:
        digest = DataCacheEntry.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = _DIGEST_PLACEHOLDER
    return DataCacheEntry(**fields, entry_digest=digest)


@runtime_checkable
class DataCache(Protocol):
    """The verified-cache ABI (carries no physical root).

    ``get_verified`` re-runs every bound-digest + context/PIT compatibility check
    and returns the exact typed :class:`VerifiedDataCacheHit` provenance needs — the
    stored typed result identity, never a detached Python object or a separately
    supplied schema — or ``None`` on a miss. ``put_verified`` never writes a
    rejected result. An in-memory conformance fake proves the ABI; production
    PIT/cache adapters remain later work.
    """

    def get_verified(
        self,
        key: DataCacheKey,
        *,
        ctx: DataContext,
        manifest: DataSnapshotManifest,
        registry: Any,
        payload_store: Any,
    ) -> VerifiedDataCacheHit | None:
        ...

    def put_verified(
        self,
        key: DataCacheKey,
        result_ref: TypedPayloadRef,
        *,
        result: Any,
        pit_audit: PitAudit,
    ) -> None:
        ...


def cache_miss_continuation(ctx: DataContext) -> Literal["pit_store", "live", "stop"]:
    """Decide the only legal continuation after a verified cache miss.

    ``PIT_REPLAY`` may continue only to an explicitly frozen, snapshot-bound
    ``PIT_STORE`` adapter; a replay ``LIVE`` backend is a structural fault
    (:class:`LiveFallbackRefused`) and a ``CACHE``-only replay has no continuation.
    ``ONLINE`` + ``CACHE`` likewise stops; an ``ONLINE`` live/pit-store backend
    continues to its own backend (never crossing into replay).
    """
    if ctx.mode is DataMode.PIT_REPLAY:
        if ctx.backend is DataBackend.LIVE:
            raise LiveFallbackRefused("PIT_REPLAY cache miss may never continue to LIVE")
        if ctx.backend is DataBackend.PIT_STORE:
            return "pit_store"
        return "stop"  # CACHE-only replay: no continuation
    if ctx.backend is DataBackend.CACHE:
        return "stop"
    if ctx.backend is DataBackend.PIT_STORE:
        return "pit_store"
    return "live"


# --------------------------------------------------------------------------- #
# Reviewed Task-5 data-only model partition (for the Phase-3 completeness gate)  #
# --------------------------------------------------------------------------- #
SNAPSHOT_PUBLIC_MODELS: tuple[type, ...] = (
    DataSourceConfigSnapshot,
    DataRoutingSnapshot,
    DataSnapshotEntry,
    DataSnapshotManifest,
    DataCacheKey,
    DataCacheEntry,
)
SNAPSHOT_INTERNAL_MODELS: dict[type, str] = {}
