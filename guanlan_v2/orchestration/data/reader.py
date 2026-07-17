# -*- coding: utf-8 -*-
"""Phase 3 · Task 7 — the frozen :class:`DataReader` facade + the invocation-scoped
:class:`DataEvidenceCollector`.

The :class:`DataReader` is the single typed door an application node uses to read
data. It is constructed by Task 8 from one already-frozen data world (context /
routing / snapshot manifest / sealed source & schema registries / source config),
one Task-8-created :class:`~guanlan_v2.orchestration.data.source.DataInvocationScope`,
the invocation collector, the sealed :class:`DataPolicyResolver`, an
:class:`~guanlan_v2.orchestration.runtime_clock.AuthoritativeClock` and the
service-owned trading calendar + Phase-2 gateway/writer/cache/audit ports. Its
constructor proves the frozen set is internally consistent before any read.

Each typed method (:meth:`~DataReader.get_ohlcv`, …) does exactly one thing: it
selects its exact reviewed :class:`DataMethodSpec`, validates a *concrete* params
model, builds a :class:`DataRequest` from the frozen context, resolves the one
policy/calendar bundle, delegates to the Task-6 :func:`dispatch`, records the
complete :class:`DataReadOutcome` into the collector and returns **only** its
named concrete result. A caller can never supply plan/node/worker identity, a
second guard, mutable config, a source chain, a freshness map, a mode, a strict
flag, a wall clock or an alternate calendar — every one of those is bound at
construction and never a method argument.

The :class:`DataEvidenceCollector` is invocation-scoped and never mints an
ordinal: it is seeded with the Phase-2-issued tokens, accepts each complete
token-matching outcome and rejects a foreign / duplicate / conflicting token. It
normalizes contributions by the generic ``(call_ordinal, bridge_priority,
bridge_id, within_call_role)`` key and then emits each Phase-1 evidence class
through its own canonicalizer — :class:`ToolCallRecord`\\ s by ``call_ordinal``,
:class:`TypedPayloadRef`\\ s by the typed semantic projection — so completion
order changes none of the emitted tuples. It is not a second evidence schema.
"""
from __future__ import annotations

from typing import Any

from guanlan_v2.orchestration.data.registry import DataSourceRegistry, dispatch
from guanlan_v2.orchestration.data.source import (
    DATA_METHOD_SCHEMAS,
    DataInvocationScope,
    DataReadOutcome,
    FundamentalDataResult,
    IndicatorDataResult,
    InstrumentSeriesParams,
    InstrumentUniverseParams,
    NewsDataResult,
    OHLCVDataResult,
    SignalDataResult,
    VerifiedSnapshotDataResult,
    build_data_request,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.refs import ContentRef, TypedPayloadRef, typed_ref_sort_key
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock
from guanlan_v2.orchestration.runtime_contracts import ExecutionEvidenceOrdinalToken
from guanlan_v2.orchestration.schemas import ToolCallRecord

__all__ = [
    "EvidenceCollectorError",
    "ForeignEvidenceError",
    "DuplicateEvidenceError",
    "ConflictingEvidenceError",
    "DataReaderFrozenSetError",
    "DataEvidenceCollector",
    "DataReader",
]

#: the data-runtime bridge identity the reader's evidence belongs to.
_DATA_BRIDGE_ID = "data.runtime"
_DATA_BRIDGE_PRIORITY = 100
_DATA_WITHIN_CALL_ROLE = "provider_prefetch"

#: method id -> its reviewed schema binding (the concrete params/result classes).
_BINDING_BY_METHOD = {b.method_id: b for b in DATA_METHOD_SCHEMAS}


# --------------------------------------------------------------------------- #
# collector errors                                                            #
# --------------------------------------------------------------------------- #
class EvidenceCollectorError(ValueError):
    """Base for a rejected contribution to a :class:`DataEvidenceCollector`."""


class ForeignEvidenceError(EvidenceCollectorError):
    """An outcome carried a token the collector was never issued."""


class DuplicateEvidenceError(EvidenceCollectorError):
    """The same operation token was contributed twice."""


class ConflictingEvidenceError(EvidenceCollectorError):
    """Two contributions collide on the generic merge slot with different content."""


class DataReaderFrozenSetError(ValueError):
    """The reader's context/route/snapshot/registry/config digests are not one frozen set."""


# --------------------------------------------------------------------------- #
# the invocation-scoped evidence collector                                    #
# --------------------------------------------------------------------------- #
def _token_key(token: ExecutionEvidenceOrdinalToken) -> tuple[int, int, int]:
    return (token.attempt, token.call_ordinal, token.evidence_ordinal)


class _Contribution:
    """One normalized data-read contribution (never a persisted schema)."""

    __slots__ = ("operation_token", "result_token", "request_ref", "result_ref", "record")

    def __init__(self, *, operation_token, result_token, request_ref, result_ref, record):
        self.operation_token = operation_token
        self.result_token = result_token
        self.request_ref = request_ref
        self.result_ref = result_ref
        self.record = record


class DataEvidenceCollector:
    """An invocation-scoped, ordinal-token-seeded evidence collector.

    Seeded with the Phase-2-issued tokens (the operation token plus its per-route
    attempt tokens). It never creates an ordinal; it only accepts complete
    :class:`DataReadOutcome`\\ s whose operation and result tokens are among the
    issued set, rejecting a foreign / duplicate / conflicting token. Contributions
    are normalized by the generic ``(call_ordinal, bridge_priority, bridge_id,
    within_call_role)`` merge key, then emitted through the Phase-1 canonicalizers
    so completion order never changes an emitted tuple.
    """

    __slots__ = (
        "_issued", "_bridge_id", "_bridge_priority", "_within_call_role",
        "_by_merge", "_accepted_ops",
    )

    def __init__(
        self,
        *,
        issued_tokens: tuple[ExecutionEvidenceOrdinalToken, ...],
        bridge_id: str = _DATA_BRIDGE_ID,
        bridge_priority: int = _DATA_BRIDGE_PRIORITY,
        within_call_role: str = _DATA_WITHIN_CALL_ROLE,
    ) -> None:
        self._issued: frozenset[tuple[int, int, int]] = frozenset(
            _token_key(t) for t in issued_tokens
        )
        self._bridge_id = bridge_id
        self._bridge_priority = bridge_priority
        self._within_call_role = within_call_role
        self._by_merge: dict[tuple, _Contribution] = {}
        self._accepted_ops: set[tuple[int, int, int]] = set()

    def _merge_key(self, operation_token: ExecutionEvidenceOrdinalToken) -> tuple:
        return (
            operation_token.call_ordinal,
            self._bridge_priority,
            self._bridge_id,
            self._within_call_role,
        )

    def accept(self, outcome: DataReadOutcome) -> None:
        """Record one complete :class:`DataReadOutcome`, or reject it.

        Rejects (in order): a *foreign* operation/result token (never issued to
        this collector); a *duplicate* operation token (already contributed); a
        *conflicting* contribution that collides on the generic merge slot with a
        different operation. The outcome's refs are main-namespace by construction
        (the :class:`DataReadOutcome` validator enforces it).
        """
        op_key = _token_key(outcome.operation_token)
        res_key = _token_key(outcome.result_token)
        if op_key not in self._issued:
            raise ForeignEvidenceError(
                f"operation token {op_key} was never issued to this collector"
            )
        if res_key not in self._issued:
            raise ForeignEvidenceError(
                f"result token {res_key} was never issued to this collector"
            )
        if op_key in self._accepted_ops:
            raise DuplicateEvidenceError(
                f"operation token {op_key} has already been contributed"
            )
        merge_key = self._merge_key(outcome.operation_token)
        if merge_key in self._by_merge:
            raise ConflictingEvidenceError(
                f"a second operation collides on the merge slot {merge_key}; "
                "one call ordinal owns exactly one data contribution"
            )
        self._by_merge[merge_key] = _Contribution(
            operation_token=outcome.operation_token,
            result_token=outcome.result_token,
            request_ref=outcome.request_ref,
            result_ref=outcome.result_ref,
            record=outcome.tool_call_record,
        )
        self._accepted_ops.add(op_key)

    # -- deterministic emitters (Phase-1 canonicalizers) -------------------- #
    def tool_call_records(self) -> tuple[ToolCallRecord, ...]:
        """The finalized tool-call records, ordered by strictly-increasing ``call_ordinal``."""
        records = [c.record for c in self._by_merge.values() if c.record is not None]
        records.sort(key=lambda r: r.call_ordinal)
        return tuple(records)

    def data_result_refs(self) -> tuple[TypedPayloadRef, ...]:
        """The consumed data-result refs, canonically ordered + duplicate-free."""
        return self._dedupe_sorted(c.result_ref for c in self._by_merge.values())

    def request_refs(self) -> tuple[TypedPayloadRef, ...]:
        """The request (execution-evidence) refs, canonically ordered + duplicate-free."""
        return self._dedupe_sorted(c.request_ref for c in self._by_merge.values())

    @property
    def accepted_count(self) -> int:
        return len(self._by_merge)

    @staticmethod
    def _dedupe_sorted(refs: Any) -> tuple[TypedPayloadRef, ...]:
        seen: dict[tuple, TypedPayloadRef] = {}
        for ref in refs:
            seen.setdefault(typed_ref_sort_key(ref), ref)
        return tuple(seen[key] for key in sorted(seen))


# --------------------------------------------------------------------------- #
# the frozen DataReader facade                                                #
# --------------------------------------------------------------------------- #
class DataReader:
    """The single typed door an application node reads data through.

    Bound at construction to one frozen data world + one invocation scope; a
    caller supplies neither identity, config, chain, guard, mode, strict flag,
    clock nor calendar. Each typed method resolves its exact spec, validates its
    concrete params model, builds one request, delegates to :func:`dispatch`,
    records the outcome and returns only its named result envelope.
    """

    __slots__ = (
        "_ctx", "_routing", "_manifest", "_registry", "_schema_registry", "_source_config",
        "_scope", "_collector", "_policy_resolver", "_clock", "_calendar",
        "_gateway", "_evidence_writer", "_payload_reader", "_refusal_audit_sink", "_cache",
    )

    def __init__(
        self,
        *,
        ctx: Any,
        routing: Any,
        manifest: Any,
        registry: DataSourceRegistry,
        schema_registry: Any,
        source_config: Any,
        scope: DataInvocationScope,
        collector: DataEvidenceCollector,
        policy_resolver: Any,
        clock: AuthoritativeClock,
        calendar: Any,
        gateway: Any,
        evidence_writer: Any,
        payload_reader: Any,
        refusal_audit_sink: Any,
        cache: Any,
    ) -> None:
        self._ctx = ctx
        self._routing = routing
        self._manifest = manifest
        self._registry = registry
        self._schema_registry = schema_registry
        self._source_config = source_config
        self._scope = scope
        self._collector = collector
        self._policy_resolver = policy_resolver
        self._clock = clock
        self._calendar = calendar
        self._gateway = gateway
        self._evidence_writer = evidence_writer
        self._payload_reader = payload_reader
        self._refusal_audit_sink = refusal_audit_sink
        self._cache = cache
        self._verify_frozen_world()

    # -- constructor frozen-set proof --------------------------------------- #
    def _verify_frozen_world(self) -> None:
        """Prove context / route / snapshot / registries / config form one frozen set.

        A request-independent subset of the dispatch frozen-set check, run once at
        construction so a mis-wired reader fails before any read (dispatch re-checks
        the full per-request set on every call).
        """
        snap = self._registry.snapshot()
        routing_digest = self._routing.routing_digest

        def fail(reason: str) -> None:
            raise DataReaderFrozenSetError(
                f"data reader refused: {reason} (context/route/snapshot/registry/config "
                "are not one frozen set)"
            )

        if self._ctx.routing_snapshot_digest != routing_digest:
            fail("ctx routing digest != routing snapshot digest")
        if self._manifest.routing_snapshot_digest != routing_digest:
            fail("manifest routing digest != routing snapshot digest")
        if self._ctx.data_snapshot_content_digest != self._manifest.content_digest:
            fail("ctx snapshot content digest != manifest content digest")
        if self._ctx.data_snapshot_id != self._manifest.data_snapshot_id:
            fail("ctx snapshot id != manifest snapshot id")
        if self._ctx.source_registry_digest != snap.source_registry_digest:
            fail("ctx source-registry digest != sealed registry digest")
        if content_digest(self._source_config) != self._ctx.source_config_digest:
            fail("source config digest != ctx source-config digest")
        if self._schema_registry.registry_digest != self._scope.schema_registry_digest:
            fail("schema registry digest != invocation scope schema-registry digest")
        if len(self._scope.attempt_tokens) != len(self._scope.frozen_route.entries):
            fail("attempt token count != frozen route length")

    # -- typed method surface ----------------------------------------------- #
    def get_ohlcv(self, params: InstrumentSeriesParams) -> OHLCVDataResult:
        return self._read("ohlcv", params, OHLCVDataResult)

    def get_indicators(self, params: InstrumentSeriesParams) -> IndicatorDataResult:
        return self._read("indicators", params, IndicatorDataResult)

    def get_verified_snapshot(self, params: InstrumentUniverseParams) -> VerifiedSnapshotDataResult:
        return self._read("verified_snapshot", params, VerifiedSnapshotDataResult)

    def get_fundamentals(self, params: InstrumentSeriesParams) -> FundamentalDataResult:
        return self._read("fundamentals", params, FundamentalDataResult)

    def get_news(self, params: InstrumentUniverseParams) -> NewsDataResult:
        return self._read("news", params, NewsDataResult)

    def get_signal(self, method_ref: ContentRef, params: InstrumentSeriesParams) -> SignalDataResult:
        """Read a derived signal — ``method_ref`` must resolve to the approved signal spec.

        The ref cannot name an arbitrary method: it must equal the sealed registry's
        ``signals`` :class:`DataMethodSpec` ref exactly (id + version + spec digest),
        so a caller can neither read a non-signal method through this door nor forge a
        signal identity.
        """
        spec = self._registry.method_spec("signals")
        if spec.category != "signals" or method_ref != spec.method_ref:
            raise ValueError(
                "get_signal requires the approved signal method ref "
                f"({spec.method_ref.id}@{spec.method_ref.version}); the supplied ref does "
                "not resolve to the registry's signal method spec"
            )
        return self._read("signals", params, SignalDataResult)

    # -- the one shared read path ------------------------------------------- #
    def _read(self, method_id: str, params: Any, result_cls: type) -> Any:
        spec = self._registry.method_spec(method_id)
        binding = _BINDING_BY_METHOD[method_id]
        if not isinstance(params, binding.params_cls):
            raise TypeError(
                f"{method_id} requires a {binding.params_cls.__name__} params model; "
                f"got {type(params).__name__}"
            )
        # python-mode dump keeps sequence fields as tuples (a JSON list would fail the
        # params schema's strict tuple validation); every value stays JSON-shaped.
        params_dict = params.model_dump(mode="python")
        req = build_data_request(
            self._ctx, method_spec=spec, params=params_dict,
            registry=self._schema_registry, request_id=f"data:{self._scope.node_id}:{method_id}",
        )
        policy = self._policy_resolver.resolve_method(spec, ctx=self._ctx)
        self._verify_policy_calendar(policy)
        outcome = dispatch(
            req, invocation_scope=self._scope, ctx=self._ctx, routing=self._routing,
            manifest=self._manifest, registry=self._registry,
            schema_registry=self._schema_registry, resolved_policy=policy,
            gateway=self._gateway, evidence_writer=self._evidence_writer,
            payload_reader=self._payload_reader, refusal_audit_sink=self._refusal_audit_sink,
            cache=self._cache, clock=self._clock, calendar=self._calendar,
        )
        self._collector.accept(outcome)
        result = outcome.result
        if not isinstance(result, result_cls):
            raise TypeError(
                f"{method_id} produced {type(result).__name__}, not the expected "
                f"{result_cls.__name__} envelope"
            )
        return result

    def _verify_policy_calendar(self, policy: Any) -> None:
        """The resolved policy's calendar must be the exact service-owned calendar."""
        if policy.calendar_id != self._calendar.calendar_id:
            raise DataReaderFrozenSetError(
                f"resolved policy calendar {policy.calendar_id!r} does not match the "
                f"bound calendar {self._calendar.calendar_id!r}"
            )
        if policy.calendar_material_ref != self._calendar.material_ref:
            raise DataReaderFrozenSetError(
                "resolved policy calendar material ref does not match the bound calendar"
            )


#: reader.py introduces no registered payload schema; the collector/reader are pure
#: service objects (never persisted), so the Phase-3 data registry is unchanged.
READER_PUBLIC_MODELS: tuple[type, ...] = ()
READER_INTERNAL_MODELS: dict[type, str] = {}
