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
from datetime import datetime, timezone
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
from guanlan_v2.orchestration.data.symbols import normalize_symbol
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
    "SubjectParams",
    "SUBJECT_PARAM_POINTERS",
    "DataSourceCapabilityBackend",
    "DataRuntimeBridgeProvider",
    "data_runtime_provider_factory",
    "WorldlessDataBridgeProvider",
    "worldless_data_provider_factory",
    "register_worldless_data_provider",
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
# the closed subject->data param projection (L1, D-0 option (i))                #
# --------------------------------------------------------------------------- #
#: The CLOSED set of source pointers the run-subject projection may serve.
#: Exactly the two pointers the ONE reviewed prefetch row binds; widening this
#: set is a reviewed decision, never a convenience edit.
SUBJECT_PARAM_POINTERS: frozenset[str] = frozenset({"/asof_date", "/code"})


@dataclass(frozen=True)
class SubjectParams:
    """The closed subject-params document projected from a committed run subject.

    A plain frozen service object — NEVER a registered model (it carries no
    digest, feeds no schema, and must not move any sealed byte). Built ONLY by
    :meth:`project`, the ONE reviewed projection recipe; carried out-of-band
    beside a materialized plan composite and consumed by :func:`_assemble_params`
    as the second, closed source for ``node_param`` pointers.

    ``code_value`` is the singleton instrument set the run subject denotes —
    ``(normalize_symbol(code),)``, the ratified singleton-universe semantic:
    the one-element ``tuple`` of the structured :class:`Symbol` the EXISTING
    reviewed constructor derives (board rules are never re-derived here).
    ``asof_value`` is the subject's ``as_of`` as an aware UTC ISO-8601 string
    (satisfies ``IsoAwareDateTime``).
    """

    code_value: Any
    asof_value: str

    @classmethod
    def project(cls, *, code: str, as_of: datetime) -> "SubjectParams":
        """Project a committed subject's primitives into the closed document.

        Layering-clean: takes primitives, imports nothing from ``pipeline/``.
        Refusals are typed :class:`DataRuntimeError`, never repairs:

        * ``code`` must be the watcher-canonical BARE six-digit form (exactly
          what ``RunSubject.code`` stores). An alternate spelling that the
          constructor could parse (dotted / engine) is still refused — the ONE
          recipe accepts ONE input grammar.
        * ``as_of`` must be a tz-aware :class:`datetime`; a naive instant cannot
          prove PIT and is rejected.
        """
        if type(code) is not str or not code:
            raise DataRuntimeError(
                "subject code must be a non-empty str in the canonical "
                f"six-digit form; got {code!r}")
        try:
            sym = normalize_symbol(code)
        except (TypeError, ValueError) as exc:
            raise DataRuntimeError(
                f"subject code {code!r} is not a canonical A-share code: {exc}"
            ) from exc
        if sym.code != code:
            raise DataRuntimeError(
                f"subject code {code!r} is not the canonical six-digit form "
                f"(the committed RunSubject stores {sym.code!r}); refusing an "
                "alternate spelling rather than repairing it")
        if not isinstance(as_of, datetime):
            raise DataRuntimeError(
                f"subject as_of must be a tz-aware datetime, got "
                f"{type(as_of).__name__}")
        if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
            raise DataRuntimeError(
                "subject as_of must be tz-aware; a naive datetime cannot prove "
                "PIT and is rejected")
        return cls(
            code_value=(sym,),
            asof_value=as_of.astimezone(timezone.utc).isoformat(),
        )

    def as_document(self) -> dict:
        """The CLOSED two-key document — no extension point."""
        return {"asof_date": self.asof_value, "code": self.code_value}


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


def _node_param_cause(node: Any, node_params: Mapping, *, subject_bound: bool) -> str:
    """Name the CAUSE of an unresolvable ``node_param`` binding, not just the symptom.

    A bare "pointer does not resolve" hides the interesting distinctions: a node
    that carries *some* params and simply misses this one (a row/params mismatch),
    vs. a node that structurally CANNOT carry params at all — whose only honest
    source is the run-subject projection, so the discriminating fact is whether
    that projection was bound (``subject_bound``) when the lookup failed.
    """
    node_id = getattr(node, "id", "?")
    if subject_bound:
        tail = (
            " A subject projection IS bound, but it serves only the closed "
            "pointer set '/asof_date', '/code' -- this pointer is outside that "
            "closure and is refused, never guessed."
        )
    else:
        tail = (
            " The subject->data projection EXISTS (L1); the failure is that the "
            "run's subject projection was not bound at the runner seam "
            "(subject_params=None) -- a wiring gap at the caller, no longer an "
            "unbuilt projection."
        )
    if node_params:
        return (
            f" -- node {node_id!r} carries params {sorted(node_params)!r}, none of "
            "which is that pointer." + tail
        )
    return (
        f" -- node {node_id!r} carries NO params at all. A 'node_param' binding is "
        "resolvable from node params only on a node whose worker declares a "
        "params_schema_ref; a worker with params_schema_ref=None can never legally "
        "carry node params (plan validation refuses such a node with "
        "'params_not_allowed'), so such a row resolves only through the run-subject "
        "projection." + tail
    )


def _assemble_params(
    row: DataPrefetchOperation, node: Any, *,
    subject_params: SubjectParams | None = None,
) -> dict:
    """Apply the row's closed param projection — node params / subject / consts.

    Resolution order for a ``node_param`` binding: (1) ``node.params`` (unchanged
    semantics for workers that legally carry params); (2) else, iff
    ``subject_params`` is bound AND the pointer is in the CLOSED
    :data:`SUBJECT_PARAM_POINTERS`, the subject document; (3) else the
    cause-named :class:`DataRuntimeError`. When BOTH sources supply the pointer
    the values must be identical — a node may never contradict the run subject
    (loud conflict, never a silent winner).

    Honesty note (the L1 plan's R1 framing): the subject params ARE
    materialization-stamped node params in every semantic sense; they travel
    out-of-band solely because Phase-1's ``params_not_allowed`` guards the
    *untrusted in-band* channel (model/caller-supplied ``PlanNode.params``),
    while this channel is service-stamped from a digest-committed ``RunSubject@1``
    after validation. That is why ``spec.py``'s guard and the sealed preset's
    code-free rule stay untouched and un-weakened, and why the sealed
    ``ParamBinding`` docstring bytes do not move.

    ``input_value`` bindings remain unsupported by the v1 data bridge (loading a
    named artifact payload is the pool's concern); a reviewed row using one fails
    loudly rather than being silently skipped.
    """
    params: dict = {}
    node_params = dict(node.params)
    subject_doc = subject_params.as_document() if subject_params is not None else None
    for binding in row.param_bindings:
        if binding.source_kind == "node_param":
            pointer = binding.source_pointer
            node_exc: DataRuntimeError | None = None
            try:
                value = _pointer_get(node_params, pointer)
            except DataRuntimeError as exc:
                node_exc = exc
            subject_serves = subject_doc is not None and pointer in SUBJECT_PARAM_POINTERS
            if node_exc is None and subject_serves:
                if value != _pointer_get(subject_doc, pointer):
                    raise DataRuntimeError(
                        f"param source pointer {pointer!r} resolves from BOTH "
                        f"node {getattr(node, 'id', '?')!r} params and the "
                        "run-subject projection with DIFFERING values -- a node "
                        "may never contradict the run subject")
            elif node_exc is not None and subject_serves:
                value = _pointer_get(subject_doc, pointer)
            elif node_exc is not None:
                raise DataRuntimeError(
                    f"{node_exc}"
                    f"{_node_param_cause(node, node_params, subject_bound=subject_doc is not None)}"
                ) from node_exc
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
# the worldless deep-lane provider (L1 Task 3, R3 — retired the dead-row one)   #
# --------------------------------------------------------------------------- #
class WorldlessDataBridgeProvider:
    """The deep lane's ``data.runtime`` provider while NO production
    :class:`DataRuntimeWorld` is bound — three LOUD shapes, never an empty
    contribution for a resolvable row (L1 plan Task 3, design resolution R3).

    This class replaces the dead-row empty-complete provider, retired per its
    own tombstone: that provider's empty contribution was honest ONLY while
    the sealed ``dec.pm``/``verified_snapshot`` row was unrunnable in every
    legal plan. Under the L1 subject projection (:class:`SubjectParams` +
    :func:`_assemble_params`'s subject source) that row RESOLVES, so
    'structurally dead' ceased to exist as a category and completing empty
    over the row would be a silenced outage — exactly what the tombstone
    forbade widening into.

    ``prepare_input`` is bit-for-bit the world-bound provider's I/O-free empty
    prepare. ``open_execution`` NEVER returns a session — without a world
    there is nothing lawful to read, so every shape is a typed
    :class:`DataRuntimeError`:

    1. an allowlisted worker whose sealed prefetch binding carries NO reviewed
       row → the UNCHANGED loud text (the pv-aux shape; their
       ``bridge_execution_error`` + degrade behavior is re-pinned unchanged);
    2. reviewed rows + a BOUND subject projection → the provider first PROVES
       resolvability against the REAL sealed rows (the real
       :func:`_assemble_params` + ``params_cls.model_validate`` per row —
       zero I/O, zero gateway begins, zero evidence writes), then refuses
       naming the resolved subject. The message carries the FROZEN marker
       ``params resolved from the run subject projection`` (the L1 Task-6
       live-verification target — a frozen contract within the L1 plan), the
       subject's code and as-of values, the row's method id, and the literal
       ``no production DataRuntimeWorld is bound (the chartered L2-b gap)``.
       A proof failure refuses loudly too — raising the resolved-shape
       message over a row that did NOT resolve would be a fabrication;
    3. reviewed rows + NO subject projection bound → loud
       ``subject projection not bound at the runner seam`` — a WIRING defect
       at the per-run threading (the projection exists as of L1 Task 1), no
       longer a structural catalog fact.

    Plus the inherited guards unchanged: a config naming a foreign bridge, a
    drifted prepared handle.

    SUCCESSOR TOMBSTONE (the one-train release, L1 → L2-b) — this class is
    itself a stopgap, retired BY TASK:

    * **L2-b Task 4** supersedes the registration:
      ``live_decide.build_production_bindings`` re-targets the sealed
      ``bridge.data_runtime.provider@1`` handler identity to the world-bound
      production provider factory;
    * **L2-b Task 5 (the L1↔L2-b integration seam)** re-targets the per-run
      factories view away from :func:`worldless_data_provider_factory` and
      DELETES this class. Shape 3's runner-seam semantics survive inside the
      real session's :func:`_assemble_params` refusal (the cause-named
      unbound-subject raise); shape 1's fate — loud refusal vs the
      catalog-licensed EMPTY for rowless workers — is L2-b's conscious flip.
      Neither is silently inherited.

    Material-drift note (bytes frozen, the C3 discipline): the sealed handler
    material ``bridge.data_runtime.provider@1`` (``data/catalog.py``
    ``_PROVIDER_BYTES``) describes the static-prefetch provider generically;
    this ruling is recorded HERE in code — zero material bytes move, zero
    catalog digests move.
    """

    __slots__ = ("_bridge", "_summary", "_subject_params")

    def __init__(self, *, bridge: Any, summary: Any,
                 subject_params: SubjectParams | None = None) -> None:
        self._bridge = bridge
        self._summary = summary
        self._subject_params = subject_params

    def prepare_input(self, request: Any) -> Any:
        """Bit-for-bit the world-bound provider's I/O-free empty prepare."""
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
        contribution = BridgeInputContribution()
        handle = PreparedBridgeHandle(
            bridge_id=self._bridge.bridge_id, bridge_priority=self._bridge.priority,
            summary_digest=self._summary.summary_digest, token=token,
            input_contribution=contribution)
        return BridgeStageOutcome(status="prepared", input_contribution=contribution,
                                  prepared_handle=handle)

    def open_execution(self, request: Any) -> Any:
        """The three loud shapes — no path returns a session or completes."""
        from guanlan_v2.orchestration.worker import BridgeInputContribution

        binding = parse_prefetch_binding(request.bridge.config_bytes)
        if binding.bridge_id != self._bridge.bridge_id:
            raise DataRuntimeError("prefetch binding names a different bridge")
        worker = request.worker
        rows = tuple(op for op in binding.operations if op.worker_id == worker.id)
        if not rows:
            # shape 1 — text UNCHANGED from the retired provider (pinned).
            raise DataRuntimeError(
                f"worker {worker.id!r} activates the data.runtime bridge through "
                "its capability allowlist but the sealed prefetch binding carries "
                "no reviewed row for it, and this lane binds no production "
                "DataRuntimeWorld (the chartered L2-b gap) -- honest refusal, "
                "never a fabricated read"
            )
        handle = request.handle
        # GUARD ORDER IS DELIBERATE (do not reorder; L1 Task 5 sweep, Task-3
        # review): the inherited drift guard sits AFTER shape 1 — a rowless
        # worker keeps the UNCHANGED pv-aux refusal even on a drifted handle —
        # and BEFORE shapes 3/2, which ALWAYS raise; any later and it would be
        # unreachable for rows-present workers, any earlier and it would
        # silently change which refusal a rowless worker gets.
        if handle.input_contribution != BridgeInputContribution():
            raise DataRuntimeError(
                f"worker {worker.id!r}'s prepared handle carries a non-empty "
                "input contribution (prepared-handle drift)"
            )
        sp = self._subject_params
        if sp is None:
            # shape 3 — the wiring defect: the projection exists (L1 Task 1)
            # but the per-run runner seam did not bind it.
            raise DataRuntimeError(
                f"worker {worker.id!r} carries {len(rows)} reviewed data "
                "prefetch row(s) with the subject projection not bound at the "
                "runner seam (subject_params=None) -- a wiring defect at the "
                "per-run threading, not a structural catalog fact; refusing "
                "loudly, never an empty read"
            )
        # shape 2 — PROVE resolvability first: the real param assembly + the
        # reviewed params schema, per row, zero I/O, zero gateway begins, zero
        # evidence writes. Only a PROVEN row may be named resolved.
        methods: list[str] = []
        for row in rows:
            schema_binding = _BINDING_BY_METHOD.get(row.method_ref.id)
            if schema_binding is None:
                raise DataRuntimeError(
                    f"worker {worker.id!r} row names method "
                    f"{row.method_ref.id!r} which has no reviewed schema "
                    "binding -- refusing loudly, never a fabricated read"
                )
            doc = _assemble_params(row, request.node, subject_params=sp)
            try:
                schema_binding.params_cls.model_validate(doc)
            except Exception as exc:
                raise DataRuntimeError(
                    f"worker {worker.id!r} row (method={row.method_ref.id!r}): "
                    "the assembled params do not validate against the reviewed "
                    "params schema -- the row did NOT resolve; refusing "
                    "loudly, never a fabricated read"
                ) from exc
            methods.append(row.method_ref.id)
        # The invariant STATED, not caught (L1 Task 5 sweep, Task-3 review):
        # ``SubjectParams.project`` always makes ``code_value`` the singleton
        # instrument tuple; only a hand-built document can violate that, and
        # the echo must not mask a foreign TypeError from inside the join to
        # cope with it.
        if isinstance(sp.code_value, (tuple, list)):
            codes = ", ".join(str(getattr(s, "code", s)) for s in sp.code_value)
        else:
            codes = str(sp.code_value)
        raise DataRuntimeError(
            f"worker {worker.id!r}: {len(rows)} reviewed data prefetch row(s) "
            f"(methods: {', '.join(repr(m) for m in methods)}) have "
            "params resolved from the run subject projection "
            f"(code {codes!r}, as_of {sp.asof_value!r}), but no production "
            "DataRuntimeWorld is bound (the chartered L2-b gap) -- refusing "
            "rather than faking a data read"
        )


def worldless_data_provider_factory(
    subject_params: SubjectParams | None = None,
):
    """The trusted provider-handler factory for the deep lane's data bridge.

    ``subject_params`` is the run-scoped projection: the process-level
    registration stays UNBOUND (``None`` → shape 3); the per-run factories
    view (L1 Task 4) hands out the BOUND factory. Rows come from the resolved
    bridge's own sealed ``config_bytes`` and the shapes from the admitted
    worker + the projection — there is nothing here to fabricate. See
    :class:`WorldlessDataBridgeProvider` for the shapes + successor tombstone.
    """

    def factory(*, bridge: Any, summary: Any) -> WorldlessDataBridgeProvider:
        return WorldlessDataBridgeProvider(
            bridge=bridge, summary=summary, subject_params=subject_params)

    return factory


def register_worldless_data_provider(
    *, factories: Any, subject_params: SubjectParams | None = None,
) -> None:
    """The ONE reviewed deep-lane data-provider registration recipe.

    Binds :func:`worldless_data_provider_factory` under the exact sealed
    ``bridge.data_runtime.provider@1`` handler identity
    (``phase3_data_surface().provider_ref`` — verified equal to the Phase-9
    resolved bridge's ``provider_ref``). Sole production caller:
    ``live_decide.build_production_bindings`` — which stays UNBOUND
    (``subject_params=None``); the per-run BOUND view is L1 Task 4's seam.
    SUPERSEDED by L2-b Task 4 (the world-bound registration).
    """
    from guanlan_v2.orchestration.data.catalog import phase3_data_surface

    factories.register_handler(
        phase3_data_surface().provider_ref,
        worldless_data_provider_factory(subject_params))


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
