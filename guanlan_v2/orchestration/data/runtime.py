# -*- coding: utf-8 -*-
"""Phase 3 · Task 8 — the thin :class:`DataRuntimeBridge` over the real Phase-2
runtime (provenance + replay integration).

This module makes the Task-6 :func:`~guanlan_v2.orchestration.data.registry.dispatch`
router and the Task-7 :class:`~guanlan_v2.orchestration.data.reader.DataReader` run
behind the REAL Phase-2 owners — :class:`~guanlan_v2.orchestration.worker.CapabilityGateway`,
:class:`~guanlan_v2.orchestration.worker.BridgeEvidenceWriter`, the read-only
PayloadStore view and the one :class:`~guanlan_v2.orchestration.eventstore.EventRefusalAuditSink`
— without duplicating any storage, event, capability, snapshot, Artifact or
provenance builder. It only *adapts* ABIs and carries Phase-3 typed refs/digests
across the Phase-1/2 owners:

* :class:`DataRuntimeBridgeProvider` / :class:`_DataRuntimeBridgeSession` implement
  Phase 2's generic ``ExecutionBridgeProvider`` / ``ExecutionBridgeSession``
  two-stage protocol. ``prepare_input`` is I/O-free and contributes an EMPTY
  input-snapshot addition (data is never prefetched into the current node's
  InputSnapshot); ``freeze_for_execution`` executes only the catalog-bound
  :class:`~guanlan_v2.orchestration.data.catalog.DataBridgePrefetchBinding` rows.
* :class:`_DataGatewayAdapter` / :class:`_DataEvidenceWriterAdapter` bridge the
  Task-6 duck-typed ``DataCapabilityGateway`` / ``DataEvidenceWriter`` seams onto
  the real gateway/writer. The adapters *echo* executor-issued tokens (via
  :class:`_DataTokenMap`); Phase 3 validates/echoes but never mints, reuses or
  renumbers an ordinal — additional tokens are drawn from the SAME continued node
  sequencer Phase 2 passes on :class:`~guanlan_v2.orchestration.worker.BridgeOpenRequest`.
* :class:`DataSourceCapabilityBackend` is the ONE trusted capability backend for
  the data capabilities: it routes each gateway-confined invocation to the exact
  frozen-route source adapter the service registered (never a caller callable).
* :func:`audit_replay_data_evidence` reconstructs the recorded named DataResult /
  render evidence from a terminal NodeRun with ZERO source, renderer or
  model-dependent live calls (both the prompt-record and the deterministic branch).

This module introduces NO registered payload schema (see the module-level
partition constants) — every persisted value uses the already-registered Phase-2/3
schemas through the existing owners.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from guanlan_v2.orchestration.data.catalog import (
    DataPrefetchOperation,
    parse_prefetch_binding,
)
from guanlan_v2.orchestration.data.errors import (
    NotConfiguredError,
    SourceBrokenError,
)
from guanlan_v2.orchestration.data.reader import DataEvidenceCollector, DataReader
from guanlan_v2.orchestration.data.registry import DataSourceRegistry
from guanlan_v2.orchestration.data.render import build_rendered_data_block, render_for_prompt
from guanlan_v2.orchestration.data.source import (
    DATA_METHOD_SCHEMAS,
    DataInvocationScope,
    RawFetch,
)
from guanlan_v2.orchestration.refs import (
    ContentRef,
    SchemaRef,
    TypedPayloadRef,
    typed_ref_sort_key,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock
from guanlan_v2.orchestration.runtime_contracts import ExecutionEvidenceOrdinalToken

__all__ = [
    "DataRuntimeError",
    "DataRuntimeWorld",
    "DataSourceCapabilityBackend",
    "DataRuntimeBridgeProvider",
    "data_runtime_provider_factory",
    "ResolvedDataRead",
    "DataAuditReplay",
    "audit_replay_data_evidence",
    "RENDERED_BLOCK_SCHEMA_REF",
    "PROMPT_ASSEMBLY_SCHEMA_REF",
    "RUNTIME_PUBLIC_MODELS",
    "RUNTIME_INTERNAL_MODELS",
]

RENDERED_BLOCK_SCHEMA_REF = SchemaRef(name="RenderedDataBlock", version="1")
PROMPT_ASSEMBLY_SCHEMA_REF = SchemaRef(name="PromptAssemblyRecord", version="1")

#: method id -> its reviewed schema binding (concrete params class etc.).
_BINDING_BY_METHOD = {b.method_id: b for b in DATA_METHOD_SCHEMAS}

#: method id -> the typed DataReader door for it. ``signals`` is special-cased
#: (its door additionally requires the approved method ref); a method without a
#: reader door cannot be granted through the v1 data bridge.
_READER_DOORS: dict[str, str] = {
    "ohlcv": "get_ohlcv",
    "indicators": "get_indicators",
    "verified_snapshot": "get_verified_snapshot",
    "fundamentals": "get_fundamentals",
    "news": "get_news",
}

#: dispatch-side role -> the registered BridgeEvidenceRecorded within-call role.
_ROLE_MAP = {
    "request": "provider_prefetch",   # the persisted DataRequest (pre-call evidence)
    "result": "tool_result",          # the persisted named DataResult
    "render": "provider_prefetch",    # the persisted RenderedDataBlock (pre-assembly)
}


class DataRuntimeError(RuntimeError):
    """A structural Task-8 bridge invariant was violated (never a data miss)."""


# --------------------------------------------------------------------------- #
# the service-owned frozen data world                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DataRuntimeWorld:
    """The service-owned frozen data world one run's data bridge executes in.

    Constructed by service wiring from reviewed material — never by a worker,
    model or caller. The bridge session verifies it against the admitted Plan /
    ContextSnapshot before any read. ``schema_resolver`` is the Phase-2
    :class:`~guanlan_v2.orchestration.eventstore.SchemaRegistryResolver`: the
    session resolves the exact sealed Phase-3 registry through the admitted
    Plan's ``schema_registry_digest`` (neither worker input nor a global
    "latest" can substitute one).
    """

    source_registry: DataSourceRegistry
    routing: Any
    manifest: Any
    source_config: Any
    ctx: Any
    schema_resolver: Any
    policy_resolver: Any
    calendar: Any
    clock: AuthoritativeClock
    cache: Any
    catalog_runtime: Any
    refusal_audit_sink: Any
    backend: "DataSourceCapabilityBackend"


# --------------------------------------------------------------------------- #
# the ONE trusted data capability backend                                      #
# --------------------------------------------------------------------------- #
class DataSourceCapabilityBackend:
    """The trusted resolved backend behind every data capability.

    The Phase-2 :class:`CapabilityGateway` resolves backends by exact catalog
    capability identity; the *vendor* inside a frozen fallback route is a Phase-3
    routing fact the gateway deliberately does not carry. This backend closes
    that seam service-side: the :class:`_DataGatewayAdapter` (service code, never
    a worker/model) stages the exact frozen-route ``(source_ref, scope)`` target
    immediately before each gateway-confined ``invoke`` and the backend consumes
    it exactly once. Source adapters are registered by source id at construction
    (:class:`~guanlan_v2.orchestration.data.source.DataSource` protocol) — a
    caller can never inject one per call.
    """

    __slots__ = ("_adapters", "_staged")

    def __init__(self, adapters: Mapping[str, Any]) -> None:
        self._adapters = dict(adapters)
        self._staged: tuple[ContentRef, DataInvocationScope] | None = None

    def stage(self, source_ref: ContentRef, scope: DataInvocationScope) -> None:
        if self._staged is not None:  # pragma: no cover - defensive (cleared in finally)
            raise DataRuntimeError("a staged source target was never consumed")
        self._staged = (source_ref, scope)

    def clear(self) -> None:
        self._staged = None

    def invoke(self, *, capability_ref: Any, request: Any) -> RawFetch:
        staged = self._staged
        self._staged = None
        if staged is None:
            raise DataRuntimeError(
                "data capability invoked without a staged frozen-route source "
                "(only the data bridge's gateway adapter may drive this backend)"
            )
        source_ref, scope = staged
        adapter = self._adapters.get(source_ref.id)
        if adapter is None:
            raise NotConfiguredError(
                f"data source {source_ref.id!r} has no configured adapter"
            )
        raw = adapter.fetch(request, scope=scope)
        if not isinstance(raw, RawFetch):
            raise SourceBrokenError(
                f"source {source_ref.id!r} returned {type(raw).__name__}, not a RawFetch"
            )
        if raw.source_ref != source_ref:
            raise SourceBrokenError(
                f"source {source_ref.id!r} returned a RawFetch claiming a different source"
            )
        if raw.request_digest != request.request_digest:
            raise SourceBrokenError(
                f"source {source_ref.id!r} returned a RawFetch for a different request"
            )
        return raw


# --------------------------------------------------------------------------- #
# token echo map (narrow ordinal projection <-> full executor-issued token)     #
# --------------------------------------------------------------------------- #
class _DataTokenMap:
    """Echoes executor-issued tokens across the Task-6/7 narrow-token seams.

    Dispatch and the collector speak the registered narrow
    :class:`ExecutionEvidenceOrdinalToken` projection; the real gateway/writer
    require the full executor-minted ``EvidenceIssuanceToken``. This map admits
    each full token ONCE (as minted by the node sequencer) and resolves the
    projection back to it — a token it never admitted is foreign and fails.
    """

    __slots__ = ("_by_key",)

    def __init__(self) -> None:
        self._by_key: dict[tuple[int, int, int], Any] = {}

    def admit(self, full_token: Any) -> ExecutionEvidenceOrdinalToken:
        narrow = full_token.ordinal_projection()
        key = (narrow.attempt, narrow.call_ordinal, narrow.evidence_ordinal)
        existing = self._by_key.get(key)
        if existing is not None and existing != full_token:
            raise DataRuntimeError("two distinct full tokens collide on one projection")
        self._by_key[key] = full_token
        return narrow

    def full(self, narrow: ExecutionEvidenceOrdinalToken) -> Any:
        key = (narrow.attempt, narrow.call_ordinal, narrow.evidence_ordinal)
        full = self._by_key.get(key)
        if full is None:
            raise DataRuntimeError(
                f"evidence token {key} was never issued to this data bridge session"
            )
        return full


# --------------------------------------------------------------------------- #
# the two ABI adapters (dispatch seam -> real Phase-2 owners)                   #
# --------------------------------------------------------------------------- #
class _DataGatewayAdapter:
    """Adapts Task-6's ``DataCapabilityGateway`` seam onto the real gateway.

    ``begin`` maps the narrow attempt token to its full executor-minted token and
    remembers the frozen-route source for the pending invocation; ``invoke``
    stages that exact source on the trusted backend and returns the gateway's
    unpublished ``RawFetch``; ``finalize_success`` consumes ONLY the existing
    persisted TypedPayloadRefs (no second write) after re-checking the digests it
    was handed; ``reject`` forwards to the closed state machine, which persists
    the refusal through the one authoritative sink.
    """

    __slots__ = ("_gateway", "_tokens", "_backend", "_sources")

    def __init__(self, gateway: Any, tokens: _DataTokenMap,
                 backend: DataSourceCapabilityBackend) -> None:
        self._gateway = gateway
        self._tokens = tokens
        self._backend = backend
        self._sources: dict[int, ContentRef] = {}

    def begin(self, *, attempt_token: ExecutionEvidenceOrdinalToken, source_ref: ContentRef,
              capability_ref: Any, request_schema_ref: SchemaRef,
              result_schema_ref: SchemaRef, idempotency_key: str) -> Any:
        full = self._tokens.full(attempt_token)
        pending = self._gateway.begin(
            ordinal_token=full, capability_ref=capability_ref,
            request_schema_ref=request_schema_ref, idempotency_key=idempotency_key)
        if pending.result_schema_ref != result_schema_ref:
            raise DataRuntimeError(
                "the catalog capability's output schema does not equal the reviewed "
                f"method result schema ({pending.result_schema_ref.key} != "
                f"{result_schema_ref.key})"
            )
        self._sources[id(pending)] = source_ref
        return pending

    def invoke(self, pending: Any, request: Any, *, scope: DataInvocationScope) -> RawFetch:
        source_ref = self._sources.get(id(pending))
        if source_ref is None:  # pragma: no cover - defensive (begin always precedes)
            raise DataRuntimeError("invoke on a pending invocation this adapter never began")
        self._backend.stage(source_ref, scope)
        try:
            unpublished = self._gateway.invoke(pending, request)
        finally:
            self._backend.clear()
        return unpublished.raw_result

    def finalize_success(self, pending: Any, *, request_ref: TypedPayloadRef,
                         result_ref: TypedPayloadRef, request_digest: str,
                         result_digest: str) -> Any:
        # the four-argument seam: the digests are already bound INSIDE the refs;
        # verify the echo, then let the gateway re-resolve the exact persisted
        # main payloads (it performs no second write).
        if request_ref.payload_ref.content_digest != request_digest:
            raise DataRuntimeError("request_ref content digest != declared request digest")
        if result_ref.payload_ref.content_digest != result_digest:
            raise DataRuntimeError("result_ref content digest != declared result digest")
        return self._gateway.finalize_success(
            pending, request_ref=request_ref, result_ref=result_ref)

    def reject(self, pending: Any, *, detail_schema_ref: SchemaRef, detail_payload: Any,
               reason_code: str, idempotency_key: str) -> Any:
        return self._gateway.reject(
            pending, detail_schema_ref=detail_schema_ref, detail_payload=detail_payload,
            reason_code=reason_code, idempotency_key=idempotency_key)


class _DataEvidenceWriterAdapter:
    """Adapts Task-6's ``DataEvidenceWriter`` seam onto the real atomic writer.

    Each ``put`` / ``record_existing`` maps the narrow token back to its full
    executor-minted token and delegates to the real
    :class:`~guanlan_v2.orchestration.worker.BridgeEvidenceWriter` — one atomic
    evidence payload + ``BridgeEvidenceRecorded`` control payload + journal
    RunEvent per new value; a verified re-record revalidates the existing main
    ref and writes only control + event.
    """

    __slots__ = ("_writer", "_tokens")

    def __init__(self, writer: Any, tokens: _DataTokenMap) -> None:
        self._writer = writer
        self._tokens = tokens

    @staticmethod
    def _scoped(full_token: Any, idempotency_key: str) -> str:
        """Scope dispatch's semantic key to this node attempt.

        A NEW attempt of the same frozen request legitimately commits NEW
        control facts (its own ordinal positions) for the semantically identical
        evidence, so the unit-of-work key must not collide with the previous
        attempt's batch. Within one attempt the key is stable, so a crash-retry
        replays the identical batch idempotently; semantic payload dedup remains
        content-digest-based at the typed-ref level.
        """
        return f"{full_token.node_id}:a{full_token.attempt}:{idempotency_key}"

    def put(self, *, token: ExecutionEvidenceOrdinalToken, role: str, schema_ref: SchemaRef,
            payload: Any, idempotency_key: str) -> TypedPayloadRef:
        full = self._tokens.full(token)
        return self._writer.put(
            token=full, role=_ROLE_MAP[role], schema_ref=schema_ref,
            payload=payload, idempotency_key=self._scoped(full, idempotency_key))

    def record_existing(self, *, token: ExecutionEvidenceOrdinalToken, role: str,
                        typed_ref: TypedPayloadRef, idempotency_key: str) -> TypedPayloadRef:
        full = self._tokens.full(token)
        return self._writer.record_existing(
            token=full, role=_ROLE_MAP[role], typed_ref=typed_ref,
            idempotency_key=self._scoped(full, idempotency_key))


# --------------------------------------------------------------------------- #
# closed param-binding application (JSON pointers; no expression escape)        #
# --------------------------------------------------------------------------- #
def _unescape(segment: str) -> str:
    return segment.replace("~1", "/").replace("~0", "~")


def _pointer_get(doc: Any, pointer: str) -> Any:
    cur = doc
    for raw in pointer.lstrip("/").split("/"):
        seg = _unescape(raw)
        if isinstance(cur, Mapping):
            if seg not in cur:
                raise DataRuntimeError(f"param source pointer {pointer!r} does not resolve")
            cur = cur[seg]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError) as exc:
                raise DataRuntimeError(
                    f"param source pointer {pointer!r} does not resolve") from exc
        else:
            raise DataRuntimeError(f"param source pointer {pointer!r} does not resolve")
    return cur


def _pointer_set(doc: dict, pointer: str, value: Any) -> None:
    parts = [_unescape(p) for p in pointer.lstrip("/").split("/")]
    cur = doc
    for seg in parts[:-1]:
        cur = cur.setdefault(seg, {})
        if not isinstance(cur, dict):
            raise DataRuntimeError(f"param target pointer {pointer!r} collides with a value")
    cur[parts[-1]] = value


def _assemble_params(row: DataPrefetchOperation, node: Any) -> dict:
    """Apply the row's closed param projection — node params / consts ONLY.

    ``input_value`` bindings are not supported by the v1 data bridge (loading a
    named artifact payload is the pool's concern); a reviewed row using one fails
    loudly rather than being silently skipped.
    """
    params: dict = {}
    node_params = dict(node.params)
    for binding in row.param_bindings:
        if binding.source_kind == "node_param":
            value = _pointer_get(node_params, binding.source_pointer)
        elif binding.source_kind == "const":
            value = binding.const_value
        else:
            raise DataRuntimeError(
                "input_value param bindings are not supported by the v1 data bridge"
            )
        _pointer_set(params, binding.target_pointer, value)
    return params


# --------------------------------------------------------------------------- #
# the two-stage bridge provider                                                #
# --------------------------------------------------------------------------- #
class DataRuntimeBridgeProvider:
    """The Phase-2 ``ExecutionBridgeProvider`` for the ``data.runtime`` bridge.

    Constructed ONLY by the service-owned :class:`ExecutionBridgeResolver`
    through the registered provider-handler factory (never passed by a worker or
    model). ``prepare_input`` is I/O-free and returns an EMPTY InputSnapshot
    addition plus the frozen handle; ``open_execution`` binds one session over
    the frozen world + the exact Phase-2 collaborators.
    """

    __slots__ = ("_bridge", "_summary", "_world")

    def __init__(self, *, bridge: Any, summary: Any, world: DataRuntimeWorld) -> None:
        self._bridge = bridge
        self._summary = summary
        self._world = world

    def prepare_input(self, request: Any) -> Any:
        from guanlan_v2.orchestration.worker import (
            BridgeInputContribution,
            BridgeStageOutcome,
            PreparedBridgeHandle,
        )

        token = request.token
        if token.bridge_id != self._bridge.bridge_id:
            raise DataRuntimeError("prepare token names a different bridge")
        if token.summary_digest != self._summary.summary_digest:
            raise DataRuntimeError("prepare token is not bound to this bridge's summary")
        contribution = BridgeInputContribution()  # data is NEVER prefetched into the
        # current node's InputSnapshot; this phase performs no capability/live I/O.
        handle = PreparedBridgeHandle(
            bridge_id=self._bridge.bridge_id, bridge_priority=self._bridge.priority,
            summary_digest=self._summary.summary_digest, token=token,
            input_contribution=contribution)
        return BridgeStageOutcome(status="prepared", input_contribution=contribution,
                                  prepared_handle=handle)

    def open_execution(self, request: Any) -> "_DataRuntimeBridgeSession":
        return _DataRuntimeBridgeSession(
            bridge=self._bridge, summary=self._summary, world=self._world, request=request)


class _DataRuntimeBridgeSession:
    """One opened execution session: catalog-bound rows -> reads -> contribution."""

    __slots__ = ("_bridge", "_summary", "_world", "_request")

    def __init__(self, *, bridge: Any, summary: Any, world: DataRuntimeWorld,
                 request: Any) -> None:
        self._bridge = bridge
        self._summary = summary
        self._world = world
        self._request = request

    # -- session helpers ---------------------------------------------------- #
    def _resolve_schema_registry(self) -> Any:
        """The exact sealed Phase-3 registry, via the admitted Plan's digest ONLY."""
        return self._world.schema_resolver.resolve(self._request.plan.schema_registry_digest)

    def _verify_context_binding(self) -> None:
        """The bound ContextSnapshot's DataContext must BE the frozen world's."""
        ctx_ref = self._request.input_snapshot.context_snapshot_ref
        snapshot = self._request.reader.get(
            ctx_ref.payload_ref, expected_schema_ref=ctx_ref.schema_ref)
        if snapshot.data_context != self._world.ctx:
            raise DataRuntimeError(
                "the admitted ContextSnapshot's DataContext does not equal the "
                "service-frozen data world (catalog/schema/source/routing/snapshot "
                "digests are not one frozen set)"
            )

    def _rows(self) -> tuple[DataPrefetchOperation, ...]:
        """Only the catalog-bound prefetch rows for this exact worker."""
        binding = parse_prefetch_binding(self._request.bridge.config_bytes)
        if binding.bridge_id != self._bridge.bridge_id:
            raise DataRuntimeError("prefetch binding names a different bridge")
        return tuple(op for op in binding.operations
                     if op.worker_id == self._request.worker.id)

    def _frozen_route_for(self, row: DataPrefetchOperation) -> Any:
        """The registry-frozen route; the catalog row must bind it EXACTLY."""
        registry = self._world.source_registry
        spec = registry.method_spec(row.method_ref.id)
        if spec.method_ref != row.method_ref:
            raise DataRuntimeError(
                f"catalog prefetch row method {row.method_ref.id!r} does not "
                "cross-resolve to the sealed source-registry method spec"
            )
        if row.capability_ref != spec.capability_ref:
            raise DataRuntimeError(
                f"catalog prefetch row capability for {row.method_ref.id!r} does not "
                "equal the reviewed method capability"
            )
        route = registry.default_route(spec.method_id)
        if tuple(row.frozen_route) != tuple(route.entries):
            raise DataRuntimeError(
                f"catalog prefetch row route for {row.method_ref.id!r} does not equal "
                "the registry-frozen default route"
            )
        return spec, route

    # -- stage 2: the frozen execution contribution -------------------------- #
    def freeze_for_execution(self, *, kind: Any) -> Any:
        from guanlan_v2.orchestration.enums import ExecutionKind
        from guanlan_v2.orchestration.worker import (
            BridgeContribution,
            BridgeStageOutcome,
            ProviderUntrustedBlock,
        )

        req = self._request
        sequencer = getattr(req, "sequencer", None)
        if sequencer is None:
            raise DataRuntimeError(
                "the data bridge requires the continued shared node sequencer on "
                "BridgeOpenRequest (Phase-2 runner wiring)"
            )
        schema_registry = self._resolve_schema_registry()
        self._verify_context_binding()
        rows = self._rows()

        tokens = _DataTokenMap()
        gateway = _DataGatewayAdapter(req.capability_gateway, tokens, self._world.backend)
        writer = _DataEvidenceWriterAdapter(req.evidence_writer, tokens)

        # pre-issue in canonical operation-then-frozen-route order: one operation
        # token and DISTINCT attempt tokens for every possible begin, each bound
        # to this node/bridge's embedded support-summary digest. The prepared
        # handle's token is the FIRST operation token; every further token comes
        # from the continued shared sequencer — Phase 3 never mints an ordinal.
        planned: list[tuple[DataPrefetchOperation, Any, Any, DataInvocationScope]] = []
        issued: list[ExecutionEvidenceOrdinalToken] = []
        first = True
        for row in rows:
            spec, route = self._frozen_route_for(row)
            if first:
                op_full = req.handle.token
                first = False
            else:
                op_full = sequencer.issue_call_token(
                    bridge_priority=self._bridge.priority, bridge_id=self._bridge.bridge_id,
                    summary_digest=self._summary.summary_digest)
            attempt_fulls = tuple(
                sequencer.issue_call_token(
                    bridge_priority=self._bridge.priority, bridge_id=self._bridge.bridge_id,
                    summary_digest=self._summary.summary_digest)
                for _ in route.entries)
            op_narrow = tokens.admit(op_full)
            attempt_narrows = tuple(tokens.admit(t) for t in attempt_fulls)
            scope = DataInvocationScope(
                plan_digest=req.plan.plan_digest, node_id=req.node.id,
                worker_id=req.worker.id, operation_token=op_narrow,
                attempt_tokens=attempt_narrows, frozen_route=route,
                invocation_mode=row.invocation_mode,
                catalog_digest=req.plan.catalog_digest,
                schema_registry_digest=schema_registry.registry_digest)
            issued.append(op_narrow)
            issued.extend(attempt_narrows)
            planned.append((row, spec, op_full, scope))

        collector = DataEvidenceCollector(
            issued_tokens=tuple(issued), bridge_id=self._bridge.bridge_id,
            bridge_priority=self._bridge.priority)

        blocks: list[Any] = []
        for row, spec, op_full, scope in planned:
            reader = DataReader(
                ctx=self._world.ctx, routing=self._world.routing,
                manifest=self._world.manifest, registry=self._world.source_registry,
                schema_registry=schema_registry, source_config=self._world.source_config,
                scope=scope, collector=collector,
                policy_resolver=self._world.policy_resolver, clock=self._world.clock,
                calendar=self._world.calendar, gateway=gateway, evidence_writer=writer,
                payload_reader=req.reader,
                refusal_audit_sink=self._world.refusal_audit_sink,
                cache=self._world.cache)
            params_cls = _BINDING_BY_METHOD[spec.method_id].params_cls
            params = params_cls.model_validate(_assemble_params(row, req.node))
            if spec.method_id == "signals":
                result = reader.get_signal(spec.method_ref, params)
            else:
                door = _READER_DOORS.get(spec.method_id)
                if door is None:
                    raise DataRuntimeError(
                        f"method {spec.method_id!r} has no DataReader door; it cannot "
                        "be granted through the v1 data bridge"
                    )
                result = getattr(reader, door)(params)

            if kind is ExecutionKind.LLM:
                # render ONCE through the pure Task-7 renderer, persist ONCE in
                # main, and return only an ordered untrusted-block DTO — the
                # provider never holds/calls a PromptAssembler.
                result_ref = _result_ref_for(collector, result)
                block = render_for_prompt(
                    result, result_schema_ref=spec.result_schema_ref,
                    result_ref=result_ref, method_spec=spec,
                    schema_registry=schema_registry,
                    catalog_runtime=self._world.catalog_runtime)
                block_ref = writer.put(
                    token=scope.operation_token, role="render",
                    schema_ref=RENDERED_BLOCK_SCHEMA_REF, payload=block,
                    idempotency_key=f"data:{block.block_digest}:render")
                blocks.append(ProviderUntrustedBlock(
                    bridge_id=self._bridge.bridge_id,
                    bridge_priority=self._bridge.priority,
                    call_ordinal=op_full.call_ordinal, payload_ref=block_ref,
                    media_type=block.media_type,
                    rendered_length=block.rendered_length))

        # classify: finalized calls -> ToolCallRecords; consumed named results ->
        # data_result_refs; a request no record represents (cache hit / honest
        # UNAVAILABLE) -> direct evidence. Nothing is double-listed.
        records = collector.tool_call_records()
        represented = {typed_ref_sort_key(r.request_ref) for r in records}
        direct = tuple(r for r in collector.request_refs()
                       if typed_ref_sort_key(r) not in represented)
        contribution = BridgeContribution(
            bridge_id=self._bridge.bridge_id, bridge_priority=self._bridge.priority,
            summary_digest=self._summary.summary_digest, tool_call_records=records,
            data_result_refs=collector.data_result_refs(), direct_evidence_refs=direct,
            untrusted_blocks=tuple(blocks))
        return BridgeStageOutcome(status="completed", frozen_contribution=contribution)


def _result_ref_for(collector: DataEvidenceCollector, result: Any) -> TypedPayloadRef:
    """The exact persisted main typed ref of ``result`` (digest-exact, never a guess)."""
    for ref in collector.data_result_refs():
        if ref.payload_ref.content_digest == result.content_digest:
            return ref
    raise DataRuntimeError(  # pragma: no cover - the reader always records first
        "the read result has no recorded typed ref in this session's collector"
    )


def data_runtime_provider_factory(world: DataRuntimeWorld):
    """The trusted provider-handler factory for the ``data.runtime`` bridge.

    Registered by service wiring in the Phase-2 ``TrustedFactoryRegistry`` under
    the exact catalog provider-handler ref; the ``ExecutionBridgeResolver`` then
    constructs the provider from reviewed descriptor/summary material only.
    """

    def factory(*, bridge: Any, summary: Any) -> DataRuntimeBridgeProvider:
        return DataRuntimeBridgeProvider(bridge=bridge, summary=summary, world=world)

    return factory


# --------------------------------------------------------------------------- #
# audit replay (zero live calls; both branches)                                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ResolvedDataRead:
    """One finalized tool read resolved from the record's exact typed refs."""

    request: Any
    result: Any
    request_ref: TypedPayloadRef
    result_ref: TypedPayloadRef


@dataclass(frozen=True)
class DataAuditReplay:
    """The reconstructed recorded data/render evidence of one terminal NodeRun."""

    prompt_record: Any
    rendered_blocks: tuple[Any, ...]
    tool_reads: tuple[ResolvedDataRead, ...]
    data_results: tuple[Any, ...]
    direct_payloads: tuple[Any, ...] = ()


def audit_replay_data_evidence(
    node_run: Any,
    *,
    payload_reader: Any,
    source_registry: DataSourceRegistry,
    schema_registry: Any,
) -> DataAuditReplay:
    """Reconstruct the recorded named DataResult / render evidence — zero live calls.

    Both branches of the brief's replay contract:

    * with a valid persisted ``PromptAssemblyRecord`` ref, resolves that typed ref
      and its transitive ordered render refs plus the ToolCallRecord request/result
      and typed DataResult refs, and re-renders each block through the PURE
      builder (no renderer material, source or model call) — a drifted rendering
      fails loudly instead of reconstructing silently;
    * without one (deterministic execution or pre-assembly failure), resolves the
      direct execution-evidence refs — including any orphan request/render —
      through their exact typed schemas.

    Re-execution is deliberately NOT this function: a new run identity may call
    sources only under its own newly frozen context/policy.
    """
    tool_reads: list[ResolvedDataRead] = []
    for record in node_run.tool_call_records:
        request = payload_reader.get(record.request_ref.payload_ref,
                                     expected_schema_ref=record.request_ref.schema_ref)
        result = payload_reader.get(record.result_ref.payload_ref,
                                    expected_schema_ref=record.result_ref.schema_ref)
        # Phase-1 cross-match: the finalized tool result IS the named DataResult.
        if result.request_digest != request.request_digest:
            raise DataRuntimeError(
                "recorded tool result does not cross-match its recorded request"
            )
        tool_reads.append(ResolvedDataRead(
            request=request, result=result,
            request_ref=record.request_ref, result_ref=record.result_ref))

    data_results = tuple(
        payload_reader.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)
        for ref in node_run.data_result_refs)
    for result in data_results:
        if not hasattr(result, "request_digest") or not hasattr(result, "pit_audit"):
            raise DataRuntimeError(
                "a data_result_ref resolved to a non-DataResult payload"
            )

    prompt_ref = next(
        (r for r in node_run.execution_evidence_refs
         if r.schema_ref == PROMPT_ASSEMBLY_SCHEMA_REF), None)

    prompt_record = None
    rendered_blocks: list[Any] = []
    direct_payloads: list[Any] = []
    if prompt_ref is not None:
        prompt_record = payload_reader.get(
            prompt_ref.payload_ref, expected_schema_ref=PROMPT_ASSEMBLY_SCHEMA_REF)
        for block_ref in prompt_record.untrusted_blocks:
            if block_ref.payload_ref.schema_ref != RENDERED_BLOCK_SCHEMA_REF:
                continue  # a non-data provider's block is not this replay's subject
            block = payload_reader.get(
                block_ref.payload_ref.payload_ref,
                expected_schema_ref=RENDERED_BLOCK_SCHEMA_REF)
            result = payload_reader.get(
                block.result_ref.payload_ref,
                expected_schema_ref=block.result_ref.schema_ref)
            spec = source_registry.method_spec(block.method)
            rebuilt = build_rendered_data_block(
                method_spec=spec, result=result, result_ref=block.result_ref,
                registry=schema_registry)
            if (rebuilt.rendered_text != block.rendered_text
                    or rebuilt.block_digest != block.block_digest):
                raise DataRuntimeError(
                    "recorded RenderedDataBlock does not reproduce from its exact "
                    "typed result (render drift)"
                )
            rendered_blocks.append(block)
        # the remaining direct refs (e.g. a cache request) also resolve typed.
        for ref in node_run.execution_evidence_refs:
            if ref == prompt_ref:
                continue
            direct_payloads.append(
                payload_reader.get(ref.payload_ref, expected_schema_ref=ref.schema_ref))
    else:
        for ref in node_run.execution_evidence_refs:
            direct_payloads.append(
                payload_reader.get(ref.payload_ref, expected_schema_ref=ref.schema_ref))

    return DataAuditReplay(
        prompt_record=prompt_record, rendered_blocks=tuple(rendered_blocks),
        tool_reads=tuple(tool_reads), data_results=data_results,
        direct_payloads=tuple(direct_payloads))


# --------------------------------------------------------------------------- #
# Reviewed Task-8 model partition (Phase-1/3 completeness firewalls)            #
# --------------------------------------------------------------------------- #
#: Task 8 registers NO new payload schema: every persisted value already uses a
#: registered Phase-2/3 schema through the existing owners. The world/session/
#: adapter/replay carriers are plain service objects (dataclasses), never models.
RUNTIME_PUBLIC_MODELS: tuple[type, ...] = ()
RUNTIME_INTERNAL_MODELS: dict[type, str] = {}
