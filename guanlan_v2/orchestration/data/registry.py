# -*- coding: utf-8 -*-
"""Phase 3 · Task 6 — the sealed :class:`DataSourceRegistry` + the narrow-fallback
``dispatch`` router (the routing heart of the data plane).

Two things live here:

* :class:`DataSourceRegistry` — a *sealed* builder over reviewed
  :class:`~guanlan_v2.orchestration.data.source.DataMethodSpec` /
  :class:`~guanlan_v2.orchestration.data.source.DataSourceDescriptor` /
  :class:`~guanlan_v2.orchestration.data.source.ResolvedMethodRoute` declarations.
  Re-declaring an identical spec/descriptor/route is idempotent; a conflicting
  redeclaration, an unknown method/source ref referenced by a route, and *any*
  mutation after :meth:`~DataSourceRegistry.seal` all fail. Once sealed it produces
  one immutable :class:`~guanlan_v2.orchestration.data.source.DataSourceRegistrySnapshot`
  (via :meth:`~DataSourceRegistry.snapshot`), a canonical
  :meth:`~DataSourceRegistry.manifest` (the ``data_source_manifest_v1.json`` golden)
  and, given a config + explicit routes, a
  :class:`~guanlan_v2.orchestration.data.snapshot.DataRoutingSnapshot`
  (via :meth:`~DataSourceRegistry.build_routing_snapshot`). Registration order,
  later registration and a process hash seed never alter its digests or its explicit
  default route.

* :func:`dispatch` — the sealed narrow-fallback router. The error taxonomy's three
  disjoint buckets (:mod:`guanlan_v2.orchestration.data.errors`) *are* its control
  surface, and it keys purely on type:

  1. **Advance the frozen chain (narrow fallback).** Only
     :class:`~guanlan_v2.orchestration.data.errors.RateLimitError` and
     :class:`~guanlan_v2.orchestration.data.errors.NotConfiguredError` advance to the
     next already-frozen source. Each failed pending invocation is terminalized
     exactly once through ``gateway.reject`` (or, for a ``NotConfiguredError`` that
     fails at ``begin``, is recorded by the gateway) *before* advancing.
  2. **Terminate the chain, typed.** ``PitGuard``-derived ``NO_DATA`` / ``STALE`` end
     the current chain with a named terminal :class:`DataResult`, persisted and
     ``finalize_success``-ed as a legitimate capability outcome — never continued to
     the next vendor. An *adapter*-raised
     :class:`~guanlan_v2.orchestration.data.errors.NoDataError` /
     :class:`~guanlan_v2.orchestration.data.errors.StaleDataError` *before* it returns
     candidates is a broken source: rejected, then raised.
  3. **Raise, loud.** ``FutureDataRefused`` / ``MissingAvailabilityRefused`` are
     already transitioned + audited by the guard's pending-reject recorder and are
     re-raised unchanged (no second reject, no continue, no ``finalize_success``, no
     cache/main write). Every other :class:`DataError` rejects a still-open pending
     exactly once, then raises. Optional-method chain exhaustion builds a named
     ``UNAVAILABLE`` result at the operation token (no fabricated success record);
     core exhaustion raises the first retryable error *after* every failed-attempt
     refusal has persisted.

Binding obligations closed here
-------------------------------
* **Structural-refusal audit closure.** ``PitGuard.from_context``'s construction
  refusals (replay / clock / calendar) previously raised without any audit because
  the Task-4 :class:`~guanlan_v2.orchestration.data.pit.DataFetchRefusalDetails`
  vocabulary (``context_validation`` / ``candidate_check``) cannot express a
  pre-request construction failure. Dispatch *has* the request context, so it wraps
  guard construction (and the pre-source frozen-set verification) and persists one
  idempotent audit through the :class:`EventRefusalAuditSink` **before** re-raising.
  It records these through the already-registered
  :class:`~guanlan_v2.orchestration.runtime_contracts.GenericRefusalDetails` rather
  than extending the frozen typed detail: widening ``DataFetchRefusalDetails``'s
  ``Literal`` vocabulary would churn the frozen ``data_schema_manifest_v1.json``
  golden and ``PHASE3_DATA_REGISTRY_DIGEST`` (Task 5's frozen surface) — out of scope
  for this task. The gap is closed; the vocabulary is not silently forked.
* **method vs source identity honesty.** Every attempt and every audit records the
  method identity (from ``req.method_spec_ref``) and the source identity (the route
  entry's ``source_ref``) *distinctly*: ``SourceAttempt.vendor`` is the source id
  (never conflated with ``DataResult.method``), and every refusal detail names the
  method id and the source id as separate facts.
* **calendar-policy binding (review I-1).** The injected trading ``calendar`` is
  verified against the ONE resolved policy bundle before any cache/source access:
  ``resolved_policy.calendar_id == ctx.calendar_id``, ``calendar.calendar_id ==
  resolved_policy.calendar_id`` and ``calendar.material_ref ==
  resolved_policy.calendar_material_ref`` — a right-id/wrong-material calendar is an
  audited structural refusal, never a silent session-arithmetic substitution.
* **DEGRADED coverage matrix (review I-2).** When the resolved policy carries a
  reviewed ``coverage_floor`` (see :class:`ResolvedDispatchPolicy`), a PIT-passed
  *non-empty* batch whose measured coverage of the requested universe/window falls
  below the floor is finalized ``DEGRADED`` with an honest ``coverage`` fraction and
  ``degradation_reason``; at/above the floor stays ``OK``; an empty batch stays
  ``NO_DATA``. Fallback count alone NEVER means ``DEGRADED``.

Note on the collaborator ABI. Task 6's tests exercise the router against strict
fakes (a fake ``CapabilityGateway`` / evidence writer / cache), exactly as the brief
prescribes; Task 8 wires the real Phase-2 ``CapabilityGateway`` /
``BridgeEvidenceWriter`` / ``DataCache`` and resolves ``registry`` / ``calendar``
service-side from the admitted Plan. The gateway/writer/cache the router calls are
duck-typed to the method surface documented on :func:`dispatch`; the router never
calls a descriptor handler or a registered Python object directly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.errors import (
    DataError,
    FutureDataRefused,
    MissingAvailabilityRefused,
    NoDataError,
    NotConfiguredError,
    RateLimitError,
    RoutingConfigurationError,
    SnapshotMismatchError,
    SourceBrokenError,
    StaleDataError,
)
from guanlan_v2.orchestration.data.pit import (
    DataFetchRefusalDetails,
    DataRefusalRecorder,
    PitGuard,
)
from guanlan_v2.orchestration.data.result import DataResult, PitAudit, SourceAttempt
from guanlan_v2.orchestration.data.snapshot import (
    DataRoutingSnapshot,
    DataSnapshotManifest,
    build_data_cache_key,
    build_data_routing_snapshot,
)
from guanlan_v2.orchestration.data.source import (
    DATA_METHOD_SCHEMAS,
    DataInvocationScope,
    DataMethodSpec,
    DataRequest,
    DataSourceDescriptor,
    DataSourceRegistrySnapshot,
    RawFetch,
    ResolvedDataMethodPolicy,
    ResolvedMethodRoute,
    RouteEntry,
    VerifiedDataCacheHit,
    build_data_result,
)
from guanlan_v2.orchestration.data.source import DataReadOutcome
from guanlan_v2.orchestration.digest import FiniteFloat, content_digest
from guanlan_v2.orchestration.enums import DataStatus
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, clock_now
from guanlan_v2.orchestration.runtime_contracts import (
    ExecutionEvidenceOrdinalToken,
    GenericRefusalDetails,
    NamedEvidenceDigest,
)

__all__ = [
    "SealedRegistryError",
    "RegistryConflictError",
    "DataSourceRegistry",
    "ResolvedDispatchPolicy",
    "DataCapabilityGateway",
    "DataEvidenceWriter",
    "dispatch",
    "FALLBACK_USED_BADGE",
    "REQUEST_SCHEMA_REF",
    "GENERIC_DETAIL_SCHEMA_REF",
    "DATA_FETCH_REFUSAL_SCHEMA_REF",
    "REGISTRY_INTERNAL_MODELS",
]

#: badge added to an ``OK`` result that a fallback source served.
FALLBACK_USED_BADGE = "FALLBACK_USED"

#: the registered request payload schema (a ``DataRequest`` main-namespace payload).
REQUEST_SCHEMA_REF = SchemaRef(name="DataRequest", version="1")
#: the registered generic audit-detail schema (base audit-detail registry).
GENERIC_DETAIL_SCHEMA_REF = SchemaRef(name="GenericRefusalDetails", version="1")
#: the registered typed data refusal-detail schema (candidate-stage refusals).
DATA_FETCH_REFUSAL_SCHEMA_REF = SchemaRef(name="DataFetchRefusalDetails", version="1")

#: method id -> its canonical reviewed schema binding (adapter + batch/result cls).
_BINDING_BY_METHOD = {b.method_id: b for b in DATA_METHOD_SCHEMAS}


# --------------------------------------------------------------------------- #
# Registry errors                                                             #
# --------------------------------------------------------------------------- #
class SealedRegistryError(RuntimeError):
    """A mutation was attempted on a sealed :class:`DataSourceRegistry`."""


class RegistryConflictError(ValueError):
    """A declaration conflicts with an incompatible earlier declaration."""


# --------------------------------------------------------------------------- #
# ResolvedDispatchPolicy — the dispatch-owned policy extension (coverage floor) #
# --------------------------------------------------------------------------- #
class ResolvedDispatchPolicy(ResolvedDataMethodPolicy):
    """The dispatch-owned policy-resolution extension carrying the coverage floor.

    Task 5's :class:`ResolvedDataMethodPolicy` is a frozen internal carrier owned by
    ``source.py``; the reviewed per-method **coverage floor** (the plan's ``DEGRADED``
    matrix) is a *dispatch* policy concern, so it lives here on the policy-resolution
    surface Task 6 owns — never as a field added to a frozen Task-5 model. There is
    still exactly ONE policy bundle: a resolver that binds a floor returns this
    subclass through the same ``resolved_policy`` slot; dispatch takes no second
    policy/config parameter.

    ``coverage_floor`` is the ``[0, 1]`` fraction of the requested universe/window
    below which a PIT-passed *non-empty* batch is an honest ``DEGRADED`` rather than
    ``OK``. ``None`` (or the plain base class) means the method policy approves no
    partial-coverage judgement and every PIT-passed batch is ``OK``. Like its base,
    this is an intentionally unregistered frozen dispatch carrier (see
    :data:`REGISTRY_INTERNAL_MODELS`).
    """

    coverage_floor: FiniteFloat | None = Field(default=None, ge=0, le=1)


# --------------------------------------------------------------------------- #
# The sealed source registry                                                  #
# --------------------------------------------------------------------------- #
class DataSourceRegistry:
    """A sealable builder that freezes to one immutable source-registry snapshot.

    Method specs, source descriptors and explicit default routes accumulate through
    idempotent registration (a byte-identical re-declaration is a no-op; a conflict
    under the same key raises). :meth:`seal` freezes the builder and materializes the
    one :class:`DataSourceRegistrySnapshot` (whose own validator rejects an unknown
    route method, duplicate/unsorted declarations and any digest drift — a
    configured unknown source/method is a freeze-time error, never silently
    filtered). Registration order never alters the sealed digests or the explicit
    default route.
    """

    __slots__ = (
        "_registry_version",
        "_methods",
        "_descriptors",
        "_routes",
        "_freshness",
        "_limits",
        "_sealed",
        "_snapshot",
    )

    def __init__(self, *, registry_version: str) -> None:
        self._registry_version = registry_version
        self._methods: dict[tuple[str, str], DataMethodSpec] = {}
        self._descriptors: dict[tuple[str, str], DataSourceDescriptor] = {}
        self._routes: dict[tuple[str, str], ResolvedMethodRoute] = {}
        self._freshness: dict[tuple[str, str], Any] = {}
        self._limits: dict[tuple[str, str], Any] = {}
        self._sealed = False
        self._snapshot: DataSourceRegistrySnapshot | None = None

    # -- mutation (rejected after seal) ------------------------------------- #
    def _require_open(self) -> None:
        if self._sealed:
            raise SealedRegistryError(
                "the DataSourceRegistry is sealed; no method/source/route/policy "
                "may be registered after seal()"
            )

    def register_method(self, spec: DataMethodSpec) -> "DataSourceRegistry":
        """Register a reviewed method spec; an identical re-declaration is idempotent."""
        self._require_open()
        key = (spec.method_id, spec.method_version)
        prior = self._methods.get(key)
        if prior is not None and prior.spec_digest != spec.spec_digest:
            raise RegistryConflictError(
                f"method {key[0]}@{key[1]} re-declared with a different spec digest"
            )
        self._methods[key] = spec
        return self

    def register_descriptor(self, descriptor: DataSourceDescriptor) -> "DataSourceRegistry":
        """Register a reviewed source descriptor; identical re-declaration is idempotent."""
        self._require_open()
        key = (descriptor.source_id, descriptor.source_version)
        prior = self._descriptors.get(key)
        if prior is not None and prior.descriptor_digest != descriptor.descriptor_digest:
            raise RegistryConflictError(
                f"source {key[0]}@{key[1]} re-declared with a different descriptor digest"
            )
        self._descriptors[key] = descriptor
        return self

    def register_route(self, route: ResolvedMethodRoute) -> "DataSourceRegistry":
        """Register an explicit default fallback route for a method (idempotent)."""
        self._require_open()
        key = (route.method_ref.id, route.method_ref.version)
        prior = self._routes.get(key)
        if prior is not None and content_digest(prior) != content_digest(route):
            raise RegistryConflictError(
                f"default route for {key[0]}@{key[1]} re-declared with different entries"
            )
        self._routes[key] = route
        return self

    def register_freshness(self, policy: Any) -> "DataSourceRegistry":
        """Register a reviewed freshness policy material (idempotent by ref/digest)."""
        self._require_open()
        key = (policy.policy_id, policy.policy_version)
        prior = self._freshness.get(key)
        if prior is not None and prior.policy_digest != policy.policy_digest:
            raise RegistryConflictError(
                f"freshness policy {key[0]}@{key[1]} re-declared with a different digest"
            )
        self._freshness[key] = policy
        return self

    def register_limit(self, policy: Any) -> "DataSourceRegistry":
        """Register a reviewed limit-rule policy material (idempotent by ref/digest)."""
        self._require_open()
        key = (policy.policy_id, policy.policy_version)
        prior = self._limits.get(key)
        if prior is not None and prior.policy_digest != policy.policy_digest:
            raise RegistryConflictError(
                f"limit policy {key[0]}@{key[1]} re-declared with a different digest"
            )
        self._limits[key] = policy
        return self

    # -- sealing + views ---------------------------------------------------- #
    @property
    def sealed(self) -> bool:
        return self._sealed

    def seal(self) -> "DataSourceRegistry":
        """Freeze the builder and materialize the immutable snapshot (idempotent).

        The :class:`DataSourceRegistrySnapshot` validator rejects an unknown route
        method, a duplicate/unsorted declaration or a digest drift; a configured
        unknown source/method therefore fails here, never being filtered out.
        """
        if self._sealed:
            return self
        snapshot = DataSourceRegistrySnapshot.build(
            registry_version=self._registry_version,
            method_specs=tuple(self._methods.values()),
            source_descriptors=tuple(self._descriptors.values()),
            default_routes=tuple(self._routes.values()),
            freshness_policies=tuple(self._freshness.values()),
            limit_policies=tuple(self._limits.values()),
        )
        # a route may only reference a registered method + a registered source.
        known_methods = {(s.method_id, s.method_version) for s in snapshot.method_specs}
        known_sources = {(d.source_id, d.source_version) for d in snapshot.source_descriptors}
        for route in snapshot.default_routes:
            mkey = (route.method_ref.id, route.method_ref.version)
            if mkey not in known_methods:
                raise RoutingConfigurationError(
                    f"default route method {mkey[0]}@{mkey[1]} is not a registered method"
                )
            for entry in route.entries:
                skey = (entry.source_ref.id, entry.source_ref.version)
                if skey not in known_sources:
                    raise RoutingConfigurationError(
                        f"default route source {skey[0]}@{skey[1]} is not a registered source"
                    )
        self._snapshot = snapshot
        self._sealed = True
        return self

    def _require_sealed(self) -> DataSourceRegistrySnapshot:
        if not self._sealed or self._snapshot is None:
            raise SealedRegistryError("the DataSourceRegistry must be sealed first")
        return self._snapshot

    def snapshot(self) -> DataSourceRegistrySnapshot:
        """The immutable sealed source-registry snapshot."""
        return self._require_sealed()

    def method_spec(self, method_id: str) -> DataMethodSpec:
        """Resolve the reviewed method spec for ``method_id`` (sealed only)."""
        return self._require_sealed().method_spec(method_id)

    def default_route(self, method_id: str) -> ResolvedMethodRoute:
        """The explicit default fallback route for ``method_id`` (sealed only)."""
        snap = self._require_sealed()
        for route in snap.default_routes:
            if route.method_ref.id == method_id:
                return route
        raise RoutingConfigurationError(f"no default route registered for {method_id!r}")

    def manifest(self) -> dict[str, Any]:
        """The canonical ``data_source_manifest_v1.json`` document (sealed only).

        A registration-order-independent projection: the sealed
        ``source_registry_digest``, the sorted method / source / route rows and the
        explicit default vendor chains. Two registries built in different declaration
        orders produce the identical manifest and digest.
        """
        snap = self._require_sealed()
        return {
            "algorithm": "sha256+cjson-v1",
            "registry_version": snap.registry_version,
            "source_registry_digest": snap.source_registry_digest,
            "methods": [
                {
                    "method_id": s.method_id,
                    "method_version": s.method_version,
                    "category": s.category,
                    "optional": s.optional,
                    "result_schema_ref": s.result_schema_ref.key,
                    "spec_digest": s.spec_digest,
                }
                for s in snap.method_specs
            ],
            "sources": [
                {
                    "source_id": d.source_id,
                    "source_version": d.source_version,
                    "descriptor_digest": d.descriptor_digest,
                }
                for d in snap.source_descriptors
            ],
            "default_routes": [
                {
                    "method_id": r.method_ref.id,
                    "method_version": r.method_ref.version,
                    "chain": [e.source_ref.id for e in r.entries],
                }
                for r in snap.default_routes
            ],
        }

    def build_routing_snapshot(
        self,
        *,
        audit_id: str,
        schema_registry_digest: str,
        source_config: Any,
        method_routes: tuple[ResolvedMethodRoute, ...] | None = None,
    ) -> DataRoutingSnapshot:
        """Seal a :class:`DataRoutingSnapshot` from the sealed registry + explicit routes.

        When ``method_routes`` is omitted the sealed registry's explicit default
        routes are used; the derived vendor chains come only from the ordered route
        entries (registration order is never a routing policy).
        """
        snap = self._require_sealed()
        routes = method_routes if method_routes is not None else snap.default_routes
        return build_data_routing_snapshot(
            audit_id=audit_id,
            source_registry=snap,
            schema_registry_digest=schema_registry_digest,
            source_config=source_config,
            method_routes=routes,
        )


# --------------------------------------------------------------------------- #
# Collaborator protocols (documented ABI the router calls; fakes in tests)     #
# --------------------------------------------------------------------------- #
@runtime_checkable
class DataCapabilityGateway(Protocol):
    """The two-stage capability state machine the router drives (never a handler).

    ``begin`` authorizes one attempt token against the frozen route entry and may
    raise :class:`NotConfiguredError` when a known source's runtime capability /
    credentials are absent (a real attempt, recorded, before advancing). ``invoke``
    calls only the trusted resolved source and returns an untrusted :class:`RawFetch`
    (or raises a source-level :class:`DataError`). ``finalize_success`` re-verifies
    the persisted main refs and mints the Phase-1 ``ToolCallRecord`` at the attempt
    ordinal; ``reject`` terminalizes a still-open pending through the one audit sink.
    """

    def begin(self, *, attempt_token: ExecutionEvidenceOrdinalToken, source_ref: ContentRef,
              capability_ref: Any, request_schema_ref: SchemaRef,
              result_schema_ref: SchemaRef, idempotency_key: str) -> Any: ...

    def invoke(self, pending: Any, request: DataRequest, *, scope: DataInvocationScope) -> RawFetch: ...

    def finalize_success(self, pending: Any, *, request_ref: Any, result_ref: Any,
                         request_digest: str, result_digest: str) -> Any: ...

    def reject(self, pending: Any, *, detail_schema_ref: SchemaRef, detail_payload: Any,
               reason_code: str, idempotency_key: str) -> Any: ...


@runtime_checkable
class DataEvidenceWriter(Protocol):
    """The fixed-main evidence write port (one atomic payload+control+event per put)."""

    def put(self, *, token: ExecutionEvidenceOrdinalToken, role: str, schema_ref: SchemaRef,
            payload: Any, idempotency_key: str) -> Any: ...

    def record_existing(self, *, token: ExecutionEvidenceOrdinalToken, role: str,
                        typed_ref: Any, idempotency_key: str) -> Any: ...


# --------------------------------------------------------------------------- #
# The guard's pending-bound refusal recorder                                  #
# --------------------------------------------------------------------------- #
class _DispatchGuardRecorder(DataRefusalRecorder):
    """Routes a ``PitGuard`` candidate-stage refusal through ``gateway.reject``.

    The guard records a typed :class:`DataFetchRefusalDetails` exactly once before it
    raises; this adapter terminalizes the *currently bound* pending invocation through
    the gateway (which persists the audit through the one sink) so a
    ``FutureDataRefused`` / ``MissingAvailabilityRefused`` is transitioned + audited
    before the guard's exception propagates. Dispatch's catch never rejects a second
    time.
    """

    __slots__ = ("_gateway", "_pending")

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway
        self._pending: Any = None

    def bind(self, pending: Any) -> None:
        self._pending = pending

    def record_data_refusal(
        self, details: DataFetchRefusalDetails, evidence: tuple[NamedEvidenceDigest, ...],
        *, idempotency_key: str,
    ) -> None:
        if self._pending is None:  # pragma: no cover - defensive (always bound first)
            raise RoutingConfigurationError(
                "a PitGuard candidate refusal fired without a bound pending invocation"
            )
        self._gateway.reject(
            self._pending,
            detail_schema_ref=DATA_FETCH_REFUSAL_SCHEMA_REF,
            detail_payload=details,
            reason_code=details.reason_code,
            idempotency_key=idempotency_key,
        )


# --------------------------------------------------------------------------- #
# dispatch — the sealed narrow-fallback router                                #
# --------------------------------------------------------------------------- #
def dispatch(
    req: DataRequest,
    *,
    invocation_scope: DataInvocationScope,
    ctx: DataContext,
    routing: DataRoutingSnapshot,
    manifest: DataSnapshotManifest,
    registry: DataSourceRegistry,
    schema_registry: Any,
    resolved_policy: ResolvedDataMethodPolicy,
    gateway: Any,
    evidence_writer: Any,
    payload_reader: Any,
    refusal_audit_sink: Any,
    cache: Any,
    clock: AuthoritativeClock,
    calendar: Any,
) -> DataReadOutcome:
    """Route one data read over the frozen fallback chain (narrow fallback).

    ``registry`` is the sealed :class:`DataSourceRegistry`, ``calendar`` the exact
    trading calendar the resolved policy bound; both are service-owned (Task 8
    resolves them from the admitted Plan and ``resolved_policy.calendar_material_ref``)
    and are never caller/model input. See the module docstring for the full outcome
    matrix; the router keys purely on the error taxonomy's three buckets.
    """
    snapshot = registry.snapshot()
    method_id = req.method_spec_ref.id
    method_spec = snapshot.method_spec(method_id)
    frozen_route = invocation_scope.frozen_route

    # -- 1) frozen-set verification (audit one detail before any source call) - #
    _verify_frozen_set(
        req, ctx=ctx, routing=routing, manifest=manifest, snapshot=snapshot,
        method_spec=method_spec, resolved_policy=resolved_policy,
        invocation_scope=invocation_scope, schema_registry=schema_registry,
        registry=registry, method_id=method_id, frozen_route=frozen_route,
        refusal_audit_sink=refusal_audit_sink, clock=clock, calendar=calendar,
    )

    op_token = invocation_scope.operation_token
    attempt_tokens = invocation_scope.attempt_tokens

    # -- 2) persist / reuse the request typed ref (one write) ---------------- #
    request_typed = evidence_writer.put(
        token=op_token, role="request", schema_ref=REQUEST_SCHEMA_REF,
        payload=req, idempotency_key=f"data:{req.request_digest}:request",
    )

    # -- 3) build the PitGuard (audit construction refusals before re-raising) - #
    guard_recorder = _DispatchGuardRecorder(gateway)
    guard = _build_guard(
        ctx, clock=clock, calendar=calendar, recorder=guard_recorder,
        req=req, method_id=method_id, frozen_route=frozen_route,
        refusal_audit_sink=refusal_audit_sink,
    )
    request_context_digest = content_digest(ctx)

    # -- 4) cache_or_invoke: a verified hit terminates at the operation token - #
    if invocation_scope.invocation_mode == "cache_or_invoke":
        hit = _cache_lookup(
            cache, req=req, ctx=ctx, manifest=manifest, method_spec=method_spec,
            frozen_route=frozen_route, schema_registry=schema_registry,
            payload_reader=payload_reader,
        )
        if hit is not None:
            evidence_writer.record_existing(
                token=op_token, role="result", typed_ref=hit.result_ref,
                idempotency_key=f"data:{req.request_digest}:cache-result",
            )
            return DataReadOutcome(
                operation_token=op_token, result_token=op_token, result=hit.result,
                request_ref=request_typed, result_ref=hit.result_ref,
                tool_call_record=None,
            )

    # -- 5) walk the frozen fallback chain ---------------------------------- #
    attempts: list[SourceAttempt] = []
    first_retryable: DataError | None = None
    chain = tuple(e.source_ref.id for e in frozen_route.entries)

    for i, entry in enumerate(frozen_route.entries):
        token = attempt_tokens[i]
        source_ref = entry.source_ref
        started = clock_now(clock)
        idem = f"data:{req.request_digest}:{source_ref.id}:{i}"

        # -- 5a. authorize the attempt (may fail closed as not-configured) --- #
        # only NotConfiguredError is catchable here by ABI contract: ``begin`` is
        # pure authorization (allowlist/summary/credential presence) and performs
        # no vendor I/O, so it can never rate-limit — RateLimitError can arise
        # only from ``invoke``.
        try:
            pending = gateway.begin(
                attempt_token=token, source_ref=source_ref,
                capability_ref=entry.capability_ref,
                request_schema_ref=REQUEST_SCHEMA_REF,
                result_schema_ref=method_spec.result_schema_ref,
                idempotency_key=idem,
            )
        except NotConfiguredError as exc:
            attempts.append(_attempt(
                source_ref, configured=False, outcome="not_configured",
                started=started, finished=clock_now(clock),
                fallback_reason="not_configured",
            ))
            first_retryable = first_retryable or exc
            continue

        # -- 5b. invoke only the trusted resolved source --------------------- #
        try:
            raw = gateway.invoke(pending, req, scope=invocation_scope)
        except RateLimitError as exc:
            gateway.reject(
                pending, detail_schema_ref=GENERIC_DETAIL_SCHEMA_REF,
                detail_payload=_generic_detail("rate_limited", method_id, source_ref),
                reason_code="rate_limited", idempotency_key=f"{idem}:reject",
            )
            attempts.append(_attempt(
                source_ref, configured=True, outcome="rate_limited",
                started=started, finished=clock_now(clock), fallback_reason="rate_limited",
            ))
            first_retryable = first_retryable or exc
            continue
        except NotConfiguredError as exc:
            gateway.reject(
                pending, detail_schema_ref=GENERIC_DETAIL_SCHEMA_REF,
                detail_payload=_generic_detail("not_configured", method_id, source_ref),
                reason_code="not_configured", idempotency_key=f"{idem}:reject",
            )
            attempts.append(_attempt(
                source_ref, configured=False, outcome="not_configured",
                started=started, finished=clock_now(clock), fallback_reason="not_configured",
            ))
            first_retryable = first_retryable or exc
            continue
        except (NoDataError, StaleDataError) as exc:
            # an adapter that raises a terminal BEFORE returning candidates/audit is
            # a broken source: reject once, then raise (never a fabricated audit).
            gateway.reject(
                pending, detail_schema_ref=GENERIC_DETAIL_SCHEMA_REF,
                detail_payload=_generic_detail(
                    "broken_source_premature_terminal", method_id, source_ref),
                reason_code="broken_source", idempotency_key=f"{idem}:reject",
            )
            raise SourceBrokenError(
                f"source {source_ref.id!r} raised {type(exc).__name__} before returning "
                "candidates; a premature terminal is a broken source, not a passed audit"
            ) from exc
        except (FutureDataRefused, MissingAvailabilityRefused):
            # a raise-bucket PIT breach surfaced from the adapter itself: reject +
            # re-raise (never fall back).
            gateway.reject(
                pending, detail_schema_ref=GENERIC_DETAIL_SCHEMA_REF,
                detail_payload=_generic_detail("adapter_pit_breach", method_id, source_ref),
                reason_code="adapter_pit_breach", idempotency_key=f"{idem}:reject",
            )
            raise
        except DataError as exc:
            gateway.reject(
                pending, detail_schema_ref=GENERIC_DETAIL_SCHEMA_REF,
                detail_payload=_generic_detail("source_error", method_id, source_ref),
                reason_code="source_error", idempotency_key=f"{idem}:reject",
            )
            raise

        # -- 5c. the PIT firewall (guard-derived terminals + refusals) ------- #
        guard_recorder.bind(pending)
        try:
            passed, pit_audit = guard.check_raw(
                raw.candidates, request_digest=req.request_digest,
                request_context_digest=request_context_digest, source_ref=source_ref,
                freshness=resolved_policy.freshness_policy,
            )
        except (FutureDataRefused, MissingAvailabilityRefused):
            # the guard's recorder already rejected the pending + audited exactly
            # once; re-raise unchanged (no second reject, no continue, no finalize,
            # no cache/main write).
            raise
        except StaleDataError as exc:
            return _finalize_terminal(
                DataStatus.STALE, exc.pit_audit if isinstance(exc.pit_audit, PitAudit) else pit_audit_fallback(ctx),
                method_spec=method_spec, req=req, ctx=ctx, chain=chain,
                attempts=attempts, source_ref=source_ref, fallback_index=i,
                schema_registry=schema_registry, evidence_writer=evidence_writer,
                gateway=gateway, pending=pending, request_typed=request_typed,
                op_token=op_token, attempt_token=token, clock=clock,
            )

        if not passed:
            # zero candidates that pass PIT is an authoritative "nothing here".
            return _finalize_terminal(
                DataStatus.NO_DATA, pit_audit, method_spec=method_spec, req=req, ctx=ctx,
                chain=chain, attempts=attempts, source_ref=source_ref, fallback_index=i,
                schema_registry=schema_registry, evidence_writer=evidence_writer,
                gateway=gateway, pending=pending, request_typed=request_typed,
                op_token=op_token, attempt_token=token, clock=clock,
            )

        # -- 5d. success: adapt -> batch -> named OK/DEGRADED result --------- #
        return _finalize_ok(
            passed, pit_audit, method_spec=method_spec, req=req, ctx=ctx, chain=chain,
            attempts=attempts, source_ref=source_ref, fallback_index=i,
            schema_registry=schema_registry, evidence_writer=evidence_writer,
            gateway=gateway, pending=pending, request_typed=request_typed,
            op_token=op_token, attempt_token=token, clock=clock,
            resolved_policy=resolved_policy, calendar=calendar,
        )

    # -- 6) chain exhausted (only RateLimit/NotConfigured advanced) --------- #
    return _exhaustion(
        method_spec=method_spec, req=req, ctx=ctx, chain=chain, attempts=attempts,
        first_retryable=first_retryable, schema_registry=schema_registry,
        evidence_writer=evidence_writer, request_typed=request_typed, op_token=op_token,
        clock=clock,
    )


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def pit_audit_fallback(ctx: DataContext) -> PitAudit:  # pragma: no cover - defensive
    return PitAudit(mode=ctx.mode, as_of=ctx.as_of, rows_seen=0, rows_returned=0,
                    future_rows=0, missing_available_at_rows=0, guard_result="refused")


def _attempt(
    source_ref: ContentRef, *, configured: bool, outcome: str, started: Any, finished: Any,
    fallback_reason: str | None = None,
) -> SourceAttempt:
    """Build one recorded fallback attempt (``vendor`` == the source identity)."""
    return SourceAttempt(
        vendor=source_ref.id, configured=configured, outcome=outcome,
        fallback_reason=fallback_reason, started_at=started, finished_at=finished,
    )


def _generic_detail(summary: str, method_id: str, source_ref: ContentRef) -> GenericRefusalDetails:
    """A safe generic refusal detail recording method + source identity distinctly."""
    return GenericRefusalDetails(
        summary=f"{summary} method={method_id} source={source_ref.id}",
        category="data_dispatch",
    )


def _build_guard(
    ctx: DataContext, *, clock: AuthoritativeClock, calendar: Any,
    recorder: DataRefusalRecorder, req: DataRequest, method_id: str,
    frozen_route: ResolvedMethodRoute, refusal_audit_sink: Any,
) -> PitGuard:
    """Build the PitGuard, auditing a construction refusal before re-raising.

    ``PitGuard.from_context`` re-asserts the replay/clock/calendar structural
    invariants and raises a raise-bucket error on a mis-built context. The Task-4
    typed refusal-detail vocabulary cannot express such a *pre-request* construction
    failure, so dispatch records one idempotent
    :class:`~guanlan_v2.orchestration.runtime_contracts.GenericRefusalDetails` through
    the sink before re-raising — closing the structural-refusal audit gap without
    forking the frozen typed vocabulary/golden.
    """
    source_ref = frozen_route.entries[0].source_ref if frozen_route.entries else None
    try:
        return PitGuard.from_context(
            ctx, clock=clock, calendar=calendar, refusal_recorder=recorder,
        )
    except DataError as exc:
        refusal_audit_sink.record(
            detail_schema_ref=GENERIC_DETAIL_SCHEMA_REF,
            detail_payload=GenericRefusalDetails(
                summary=(
                    f"guard_construction_refused reason={type(exc).__name__} "
                    f"method={method_id}"
                    + (f" source={source_ref.id}" if source_ref is not None else "")
                ),
                category="data_guard_construction",
            ),
            reason_code="guard_construction_refused",
            idempotency_key=f"data:{req.request_digest}:guard-construction",
            evidence_digests=_evidence_method_only(req),
        )
        raise


def _evidence_method_only(req: DataRequest) -> tuple[NamedEvidenceDigest, ...]:
    items = [
        NamedEvidenceDigest(name="method", digest=req.method_spec_ref.content_digest),
        NamedEvidenceDigest(name="request", digest=req.request_digest),
    ]
    items.sort(key=lambda e: e.name)
    return tuple(items)


def _verify_frozen_set(
    req: DataRequest, *, ctx: DataContext, routing: DataRoutingSnapshot,
    manifest: DataSnapshotManifest, snapshot: DataSourceRegistrySnapshot,
    method_spec: DataMethodSpec, resolved_policy: ResolvedDataMethodPolicy,
    invocation_scope: DataInvocationScope, schema_registry: Any,
    registry: DataSourceRegistry, method_id: str, frozen_route: ResolvedMethodRoute,
    refusal_audit_sink: Any, clock: AuthoritativeClock, calendar: Any,
) -> None:
    """Verify request/context/route/manifest/registry/calendar are one frozen set.

    A mismatch records exactly one :class:`GenericRefusalDetails` directly through
    the sink and raises :class:`SnapshotMismatchError` *before* any cache lookup,
    ``begin`` or source invocation (invokes no source). The injected ``calendar`` is
    bound to the ONE resolved policy bundle here (plan L19: the policy's sole
    calendar goes to PitGuard; there is no second calendar parameter) — an injected
    calendar with the right id but different session material is refused loud.
    """
    def fail(reason: str) -> None:
        refusal_audit_sink.record(
            detail_schema_ref=GENERIC_DETAIL_SCHEMA_REF,
            detail_payload=GenericRefusalDetails(
                summary=f"frozen_set_mismatch {reason} method={method_id}",
                category="data_frozen_set",
            ),
            reason_code="frozen_set_mismatch",
            idempotency_key=f"data:{req.request_digest}:frozen-set:{reason}",
            evidence_digests=_evidence_method_only(req),
        )
        raise SnapshotMismatchError(
            f"data read refused: {reason} (request/context/route/manifest/registry "
            "are not one frozen set)"
        )

    schema_digest = getattr(schema_registry, "registry_digest", None)
    if ctx.data_snapshot_content_digest != manifest.content_digest:
        fail("ctx snapshot content digest != manifest content digest")
    if ctx.data_snapshot_id != manifest.data_snapshot_id:
        fail("ctx snapshot id != manifest snapshot id")
    # a replay context pins the exact vintage; ONLINE carries None by design (the
    # manifest's own vintage is already bound transitively via the content digest).
    if ctx.vintage_manifest_digest is not None and (
        ctx.vintage_manifest_digest != manifest.vintage_manifest_digest
    ):
        fail("ctx vintage digest != manifest vintage digest")
    if ctx.routing_snapshot_digest != routing.routing_digest:
        fail("ctx routing digest != routing snapshot digest")
    if manifest.routing_snapshot_digest != routing.routing_digest:
        fail("manifest routing digest != routing snapshot digest")
    if req.routing_snapshot_digest != routing.routing_digest:
        fail("request routing digest != routing snapshot digest")
    if req.data_snapshot_content_digest != ctx.data_snapshot_content_digest:
        fail("request snapshot content digest != ctx snapshot content digest")
    if req.source_registry_digest != snapshot.source_registry_digest:
        fail("request source-registry digest != sealed registry digest")
    if req.vintage_manifest_digest != ctx.vintage_manifest_digest:
        fail("request vintage digest != ctx vintage digest")
    if schema_digest is not None and req.schema_registry_digest != schema_digest:
        fail("request schema-registry digest != schema registry digest")
    if schema_digest is not None and invocation_scope.schema_registry_digest != schema_digest:
        fail("invocation scope schema-registry digest != schema registry digest")
    if method_spec.method_ref != req.method_spec_ref:
        fail("request method ref != registry method spec ref")
    if resolved_policy.method_ref != method_spec.method_ref:
        fail("resolved policy method ref != method spec ref")
    # the injected calendar must BE the resolved policy's bound calendar (I-1):
    # identity AND session material — right id with different material is refused.
    if resolved_policy.calendar_id != ctx.calendar_id:
        fail("resolved policy calendar id != ctx calendar id")
    if getattr(calendar, "calendar_id", None) != resolved_policy.calendar_id:
        fail("injected calendar id != resolved policy calendar id")
    if getattr(calendar, "material_ref", None) != resolved_policy.calendar_material_ref:
        fail("injected calendar material != resolved policy calendar material")
    # the runtime route must equal the catalog-frozen default route exactly.
    default_route = registry.default_route(method_id)
    if content_digest(frozen_route) != content_digest(default_route):
        fail("invocation frozen route != registry default route")
    if len(invocation_scope.attempt_tokens) != len(frozen_route.entries):
        fail("attempt token count != frozen route length")


def _cache_lookup(
    cache: Any, *, req: DataRequest, ctx: DataContext, manifest: DataSnapshotManifest,
    method_spec: DataMethodSpec, frozen_route: ResolvedMethodRoute, schema_registry: Any,
    payload_reader: Any,
) -> VerifiedDataCacheHit | None:
    """A single verified-cache probe keyed on the primary source (never a locator)."""
    primary = frozen_route.entries[0]
    key = build_data_cache_key(
        method_ref=method_spec.method_ref, source_ref=primary.source_ref,
        result_schema_ref=method_spec.result_schema_ref,
        params_digest=content_digest(req.params), as_of=ctx.as_of, mode=ctx.mode,
        backend=ctx.backend, data_snapshot_content_digest=ctx.data_snapshot_content_digest,
        vintage_manifest_digest=ctx.vintage_manifest_digest,
        source_config_digest=ctx.source_config_digest,
        routing_snapshot_digest=ctx.routing_snapshot_digest,
        source_registry_digest=ctx.source_registry_digest,
        schema_registry_digest=req.schema_registry_digest,
    )
    return cache.get_verified(
        key, ctx=ctx, manifest=manifest, registry=schema_registry, payload_store=payload_reader,
    )


def _batch_coverage(
    req: DataRequest, records: tuple[Any, ...], *, calendar: Any, ctx: DataContext,
) -> float | None:
    """Measure a PIT-passed batch's coverage of the *requested* universe/window.

    A pure, deterministic fraction in ``[0, 1]``, or ``None`` when the request shape
    yields no measurable denominator (whole-market universe, an unresolvable clock
    timezone, or calendar material that does not span the requested window — an
    uncovered range cannot honestly count sessions):

    * **universe** (params carry non-empty ``symbols``): the fraction of requested
      ``(code, exchange)`` instruments the batch covers;
    * **series** (params carry ``start``/``end``): the fraction of the bound
      calendar's trading sessions in the local-date window that have at least one
      row (``effective_at`` reduced in the frozen clock's timezone, mirroring the
      guard's local-session-date rule).
    """
    params = req.params
    symbols = params.get("symbols")
    if symbols is not None:
        requested = {
            (s.get("code"), s.get("exchange")) for s in symbols if isinstance(s, dict)
        }
        requested.discard((None, None))
        if not requested:
            return None  # a whole-market request has no finite denominator
        covered: set[tuple[str, str]] = set()
        for r in records:
            sym = getattr(r, "symbol", None)
            if sym is not None:
                covered.add((sym.code, sym.exchange))
        return min(1.0, len(covered & requested) / len(requested))
    start, end = params.get("start"), params.get("end")
    if start is not None and end is not None:
        try:
            tz = ZoneInfo(ctx.clock.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return None
        start_d = datetime.fromisoformat(start).astimezone(tz).date()
        end_d = datetime.fromisoformat(end).astimezone(tz).date()
        span = getattr(calendar, "coverage", None)
        if span is None:
            return None
        lo, hi = span
        if start_d < lo or end_d > hi:
            return None  # uncovered material: counting would silently undercount
        expected = calendar.sessions_between(start_d, end_d)
        if expected <= 0:
            return None
        covered_dates: set[Any] = set()
        for r in records:
            eff = getattr(r, "effective_at", None)
            if eff is not None:
                d = eff.astimezone(tz).date()
                if calendar.is_session(d):
                    covered_dates.add(d)
        return min(1.0, len(covered_dates) / expected)
    return None


def _finalize_ok(
    passed: tuple[Any, ...], pit_audit: PitAudit, *, method_spec: DataMethodSpec,
    req: DataRequest, ctx: DataContext, chain: tuple[str, ...],
    attempts: list[SourceAttempt], source_ref: ContentRef, fallback_index: int,
    schema_registry: Any, evidence_writer: Any, gateway: Any, pending: Any,
    request_typed: Any, op_token: ExecutionEvidenceOrdinalToken,
    attempt_token: ExecutionEvidenceOrdinalToken, clock: AuthoritativeClock,
    resolved_policy: ResolvedDataMethodPolicy, calendar: Any,
) -> DataReadOutcome:
    """Adapt PIT-passed candidates into the named OK/DEGRADED result, persist + finalize.

    The DEGRADED matrix: only a resolved-policy ``coverage_floor`` (see
    :class:`ResolvedDispatchPolicy`) can degrade a non-empty PIT-passed batch — the
    measured coverage below the floor yields ``DEGRADED`` with honest ``coverage`` +
    ``degradation_reason``; at/above the floor stays ``OK``. Fallback position never
    enters this judgement (it only adds the ``FALLBACK_USED`` badge on ``OK`` or
    ``DEGRADED`` alike). A declared floor with an unmeasurable request shape is
    refused loud (reject once, then raise) rather than claiming OK unmeasured.
    """
    binding = _BINDING_BY_METHOD[method_spec.method_id]
    records = tuple(binding.adapt(c) for c in passed)
    status: DataStatus = DataStatus.OK
    coverage: float | None = None
    reason: str | None = None
    floor = getattr(resolved_policy, "coverage_floor", None)
    if floor is not None:
        measured = _batch_coverage(req, records, calendar=calendar, ctx=ctx)
        if measured is None:
            gateway.reject(
                pending, detail_schema_ref=GENERIC_DETAIL_SCHEMA_REF,
                detail_payload=_generic_detail(
                    "coverage_unmeasurable", method_spec.method_id, source_ref),
                reason_code="coverage_unmeasurable",
                idempotency_key=f"data:{req.request_digest}:{source_ref.id}:coverage",
            )
            raise RoutingConfigurationError(
                f"method {method_spec.method_id!r} declares coverage floor {floor:g} "
                "but the request shape yields no measurable coverage; refusing to "
                "claim OK unmeasured"
            )
        if measured < floor:
            status = DataStatus.DEGRADED
            coverage = measured
            reason = (
                f"coverage {measured:.4f} of the requested universe/window is below "
                f"the method policy floor {floor:g}"
            )
    batch = binding.batch_cls.build(records)
    finished = clock_now(clock)
    attempts.append(_attempt(source_ref, configured=True, outcome="success",
                             started=finished, finished=finished))
    badges = (FALLBACK_USED_BADGE,) if fallback_index > 0 else ()
    result = build_data_result(
        method_spec, registry=schema_registry, id=f"data-{req.request_digest[:16]}",
        request_digest=req.request_digest, status=status, data=batch,
        resolved_vendor_chain=chain, source_config_digest=ctx.source_config_digest,
        fetched_at=finished, attempts=tuple(attempts), pit_audit=pit_audit, badges=badges,
        coverage=coverage, degradation_reason=reason,
    )
    return _persist_and_finalize(
        result, method_spec=method_spec, req=req, evidence_writer=evidence_writer,
        gateway=gateway, pending=pending, request_typed=request_typed, op_token=op_token,
        attempt_token=attempt_token,
    )


def _finalize_terminal(
    status: DataStatus, pit_audit: PitAudit, *, method_spec: DataMethodSpec,
    req: DataRequest, ctx: DataContext, chain: tuple[str, ...],
    attempts: list[SourceAttempt], source_ref: ContentRef, fallback_index: int,
    schema_registry: Any, evidence_writer: Any, gateway: Any, pending: Any,
    request_typed: Any, op_token: ExecutionEvidenceOrdinalToken,
    attempt_token: ExecutionEvidenceOrdinalToken, clock: AuthoritativeClock,
) -> DataReadOutcome:
    """Persist + finalize a guard-derived NO_DATA / STALE terminal (a real answer)."""
    finished = clock_now(clock)
    outcome = "no_data" if status is DataStatus.NO_DATA else "stale"
    attempts.append(_attempt(source_ref, configured=True, outcome=outcome,
                             started=finished, finished=finished))
    result = build_data_result(
        method_spec, registry=schema_registry, id=f"data-{req.request_digest[:16]}",
        request_digest=req.request_digest, status=status, data=None,
        resolved_vendor_chain=chain, source_config_digest=ctx.source_config_digest,
        fetched_at=finished, attempts=tuple(attempts), pit_audit=pit_audit,
    )
    return _persist_and_finalize(
        result, method_spec=method_spec, req=req, evidence_writer=evidence_writer,
        gateway=gateway, pending=pending, request_typed=request_typed, op_token=op_token,
        attempt_token=attempt_token,
    )


def _persist_and_finalize(
    result: DataResult, *, method_spec: DataMethodSpec, req: DataRequest,
    evidence_writer: Any, gateway: Any, pending: Any, request_typed: Any,
    op_token: ExecutionEvidenceOrdinalToken, attempt_token: ExecutionEvidenceOrdinalToken,
) -> DataReadOutcome:
    """Persist the named result once, then finalize the successful capability call."""
    result_typed = evidence_writer.put(
        token=attempt_token, role="result", schema_ref=method_spec.result_schema_ref,
        payload=result, idempotency_key=f"data:{result.content_digest}:result",
    )
    record = gateway.finalize_success(
        pending, request_ref=request_typed, result_ref=result_typed,
        request_digest=req.request_digest, result_digest=result.content_digest,
    )
    return DataReadOutcome(
        operation_token=op_token, result_token=attempt_token, result=result,
        request_ref=request_typed, result_ref=result_typed, tool_call_record=record,
    )


def _exhaustion(
    *, method_spec: DataMethodSpec, req: DataRequest, ctx: DataContext,
    chain: tuple[str, ...], attempts: list[SourceAttempt],
    first_retryable: DataError | None, schema_registry: Any, evidence_writer: Any,
    request_typed: Any, op_token: ExecutionEvidenceOrdinalToken, clock: AuthoritativeClock,
) -> DataReadOutcome:
    """Resolve chain exhaustion by the versioned method-spec ``optional`` policy.

    An *optional* method builds/persists a named ``UNAVAILABLE`` result at the
    operation token with no fabricated success ``ToolCallRecord``. A *core* method
    raises the first retryable error (every failed-attempt refusal has already
    persisted during the walk).
    """
    if not method_spec.optional:
        if first_retryable is not None:
            raise first_retryable
        raise RoutingConfigurationError(  # pragma: no cover - empty route is a freeze error
            f"core method {method_spec.method_id!r} exhausted its route with no attempt"
        )
    finished = clock_now(clock)
    pit_audit = PitAudit(mode=ctx.mode, as_of=ctx.as_of, rows_seen=0, rows_returned=0,
                         future_rows=0, missing_available_at_rows=0, guard_result="passed")
    result = build_data_result(
        method_spec, registry=schema_registry, id=f"data-{req.request_digest[:16]}",
        request_digest=req.request_digest, status=DataStatus.UNAVAILABLE, data=None,
        resolved_vendor_chain=chain, source_config_digest=ctx.source_config_digest,
        fetched_at=finished, attempts=tuple(attempts), pit_audit=pit_audit,
    )
    result_typed = evidence_writer.put(
        token=op_token, role="result", schema_ref=method_spec.result_schema_ref,
        payload=result, idempotency_key=f"data:{result.content_digest}:unavailable",
    )
    return DataReadOutcome(
        operation_token=op_token, result_token=op_token, result=result,
        request_ref=request_typed, result_ref=result_typed, tool_call_record=None,
    )


# --------------------------------------------------------------------------- #
# Reviewed Task-6 model partition (for the Phase-1 completeness firewall)       #
# --------------------------------------------------------------------------- #
#: intentionally-unregistered model this module contributes, with a reviewed reason
#: (the same idiom as source.py's SOURCE_INTERNAL_MODELS; folded into the Phase-1
#: INTERNAL_MODELS map by schema_registry._load_population).
REGISTRY_INTERNAL_MODELS: dict[type, str] = {
    ResolvedDispatchPolicy: (
        "intentionally unregistered frozen dispatch-carrier value (the Task 6 "
        "policy-resolution extension adding the reviewed coverage floor); like its "
        "ResolvedDataMethodPolicy base it preserves service policy without a second "
        "public schema"
    ),
}
