# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — the ``memory.runtime`` execution bridge + deterministic
renderer.

``MemoryRuntimeBridgeProvider`` is a Phase-2
:class:`~guanlan_v2.orchestration.worker.ExecutionBridgeProvider` with
``pre_input_kind="memory_refs_v1"`` / ``lifecycle="static_prefetch_v1"``:

* ``prepare_input`` (pre-input stage, one executor-issued token): resolves the
  frozen ContextSnapshot → bound MemorySnapshot through the read-only payload
  store, builds the exact worker query from the reviewed prefetch projection
  (admitted node params only), applies :func:`select_memory` (visibility BEFORE
  ranking), persists the ``MemoryQuery``/``MemorySelection`` in ``main``
  through the injected ``BridgeEvidenceWriter``, and freezes the direct typed
  evidence refs plus canonical Phase-1 ``MemoryRecordRef``s in its prepared
  handle. It uses only frozen registry / read-only PayloadStore facts plus the
  evidence writer — no legacy store, FTS, filesystem, clock, CapabilityGateway,
  PromptAssembler or model I/O.
* ``open_execution`` (after RUNNING): verifies its prepared selection/ref tuple
  against the frozen InputSnapshot; on ``LLM`` renders/persists one
  :class:`RenderedMemoryBlock` and returns it ONLY as an untrusted-block DTO;
  on ``DETERMINISTIC`` it renders nothing. Memory retrieval performs ZERO
  capability invocations (the reviewed analyzer bounds are 0/0).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from guanlan_v2.orchestration.context import ContextSnapshot, MemoryRecordRef
from guanlan_v2.orchestration.memory.models import (
    MemoryContractError,
    MemoryQuery,
    MemoryRecord,
    MemorySelection,
    MemorySnapshot,
    RenderedMemoryBlock,
    ResolvedMemoryPolicy,
    build_worker_memory_query,
)
from guanlan_v2.orchestration.memory.catalog import parse_memory_prefetch_binding
from guanlan_v2.orchestration.memory.store import select_memory
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.worker import (
    BridgeContribution,
    BridgeInputContribution,
    BridgeOpenRequest,
    BridgePrepareRequest,
    BridgeStageOutcome,
    PreparedBridgeHandle,
    ProviderUntrustedBlock,
)

__all__ = [
    "render_memory",
    "MemoryRuntimeBridgeProvider",
    "make_memory_bridge_provider_factory",
]

_SR = lambda name: SchemaRef(name=name, version="1")  # noqa: E731


# --------------------------------------------------------------------------- #
# deterministic renderer                                                       #
# --------------------------------------------------------------------------- #
def render_memory(
    selection: MemorySelection,
    records: dict[tuple[str, str], MemoryRecord],
    *,
    policy: ResolvedMemoryPolicy,
    renderer_ref: ContentRef,
) -> RenderedMemoryBlock:
    """Render one selection deterministically; overflow fails BEFORE assembly.

    Raw memory text stays untrusted data — the block never becomes prompt/
    skill/guardrail material, and truncation/partial mandatory rendering is
    structurally absent (any byte/count overflow raises instead).
    """
    p = policy.policy
    lines: list[str] = ["<memory trust=\"untrusted_data\">"]
    per_record: list[int] = []
    for entry in selection.entries:
        key = (entry.record_ref.record_id, entry.record_ref.revision_id)
        record = records.get(key)
        if record is None:
            raise MemoryContractError(
                f"selection entry {key} has no resolved record; render fails closed"
            )
        rendered = (
            f"- [{record.kind}|{record.scope}|rank={entry.rank}"
            f"{'|mandatory' if entry.mandatory else ''}] {record.text}"
        )
        encoded = len(rendered.encode("utf-8"))
        if encoded > p.max_record_bytes:
            raise MemoryContractError(
                "a rendered record exceeds the per-record byte bound; "
                "fail closed before prompt assembly"
            )
        per_record.append(encoded)
        lines.append(rendered)
    lines.append("</memory>")
    text = "\n".join(lines)
    total = len(text.encode("utf-8"))
    if total > p.max_total_rendered_bytes:
        raise MemoryContractError(
            "the rendered memory block exceeds the total byte bound; "
            "fail closed before prompt assembly"
        )
    return RenderedMemoryBlock.build(
        selection_digest=selection.content_digest,
        snapshot_digest=selection.snapshot_digest,
        renderer_ref=renderer_ref,
        text=text,
        per_record_bytes=tuple(per_record),
        total_bytes=total,
    )


# --------------------------------------------------------------------------- #
# the provider                                                                 #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _PreparedMemoryRead:
    """The provider-internal frozen read (never leaves the provider)."""

    query_ref: TypedPayloadRef
    selection_ref: TypedPayloadRef
    selection: MemorySelection
    record_refs: tuple[MemoryRecordRef, ...]
    records: dict[tuple[str, str], MemoryRecord]


class MemoryRuntimeBridgeProvider:
    """The trusted catalog-bound ``memory.runtime`` provider (both stages)."""

    def __init__(
        self,
        *,
        bridge: Any,
        summary: Any,
        stores: Any,
        policy: ResolvedMemoryPolicy,
        renderer_ref: ContentRef,
        config_bytes: bytes,
    ) -> None:
        self._bridge = bridge
        self._summary = summary
        self._stores = stores
        self._policy = policy
        self._renderer_ref = renderer_ref
        self._binding = parse_memory_prefetch_binding(config_bytes)

    # -- read-only typed resolution ----------------------------------------- #
    def _resolve(self, ref: TypedPayloadRef, schema: str) -> Any:
        if ref.schema_ref.name != schema or ref.schema_ref.version != "1":
            raise MemoryContractError(f"typed ref does not resolve {schema}@1")
        return self._stores.payloads.get(ref.payload_ref, expected_schema_ref=ref.schema_ref)

    # -- stage 1: pre-input ---------------------------------------------------- #
    def prepare_input(self, request: BridgePrepareRequest) -> BridgeStageOutcome:
        node, worker = request.node, request.worker
        rows = tuple(r for r in self._binding.rows if r.worker_id == worker.id)
        if len(rows) != 1:
            raise MemoryContractError(
                f"worker {worker.id!r} requires exactly one reviewed memory query "
                f"projection; found {len(rows)}"
            )
        row = rows[0]
        pointer = row.query_text_param_pointer.lstrip("/")
        params = dict(node.params)
        if pointer not in params or not str(params[pointer]).strip():
            raise MemoryContractError(
                f"admitted node params carry no query text at /{pointer}"
            )
        query_text = str(params[pointer])

        context: ContextSnapshot = self._resolve(
            request.context_snapshot_ref, "ContextSnapshot")
        snapshot: MemorySnapshot = self._resolve(
            context.memory_snapshot_ref, "MemorySnapshot")
        records: dict[tuple[str, str], MemoryRecord] = {}
        pairs = []
        for entry in snapshot.entries:
            record: MemoryRecord = self._resolve(entry.record_payload_ref, "MemoryRecord")
            records[(record.record_id, record.revision_id)] = record
            pairs.append((record, entry.record_ref))

        query: MemoryQuery = build_worker_memory_query(
            query_text, row.top_k, worker=worker, context=context,
            snapshot=snapshot, policy=self._policy)
        entries = select_memory(
            query, tuple(pairs), as_of=snapshot.as_of, policy=self._policy)

        query_ref = request.evidence_writer.put(
            token=request.token, role="provider_prefetch",
            schema_ref=_SR("MemoryQuery"), payload=query,
            idempotency_key=(f"{self._bridge.bridge_id}:{node.id}:"
                             f"a{request.token.attempt}:c{request.token.call_ordinal}:query"))
        selection = MemorySelection.build(
            snapshot_digest=snapshot.content_digest,
            query_ref=query_ref,
            query_digest=query_ref.payload_ref.content_digest,
            entries=entries)
        selection_ref = request.evidence_writer.put(
            token=request.token, role="provider_prefetch",
            schema_ref=_SR("MemorySelection"), payload=selection,
            idempotency_key=(f"{self._bridge.bridge_id}:{node.id}:"
                             f"a{request.token.attempt}:c{request.token.call_ordinal}:selection"))

        record_refs = tuple(sorted(
            (e.record_ref for e in entries),
            key=lambda r: (r.record_id, r.revision_id, r.content_digest)))
        handle = PreparedBridgeHandle(
            bridge_id=self._bridge.bridge_id,
            bridge_priority=self._bridge.priority,
            summary_digest=self._summary.summary_digest,
            token=request.token,
            input_contribution=BridgeInputContribution(
                memory_refs=record_refs,
                memory_evidence_refs=(query_ref, selection_ref)),
        )
        return BridgeStageOutcome(status="prepared", prepared_handle=handle)

    # -- stage 2: open + freeze -------------------------------------------------- #
    def open_execution(self, request: BridgeOpenRequest) -> "_MemoryBridgeSession":
        """Reconstruct + verify the prepared read from the FROZEN handle.

        The Phase-2 resolver builds a fresh provider per stage, so the session
        is stateless: everything it needs is the prepared handle's typed refs
        re-resolved through the read-only store — no live query rewrite.
        """
        handle = request.handle
        ev = handle.input_contribution.memory_evidence_refs
        if len(ev) != 2:
            raise MemoryContractError(
                "the memory prepared handle must carry exactly (query_ref, selection_ref)"
            )
        query_ref, selection_ref = ev
        selection: MemorySelection = self._resolve(selection_ref, "MemorySelection")
        if selection.query_ref.payload_ref.content_digest != query_ref.payload_ref.content_digest:
            raise MemoryContractError("prepared selection does not bind the prepared query")
        context: ContextSnapshot = self._resolve(
            request.input_snapshot.context_snapshot_ref, "ContextSnapshot")
        snapshot: MemorySnapshot = self._resolve(
            context.memory_snapshot_ref, "MemorySnapshot")
        if selection.snapshot_digest != snapshot.content_digest:
            raise MemoryContractError(
                "prepared selection does not bind the frozen memory snapshot"
            )
        records: dict[tuple[str, str], MemoryRecord] = {}
        for entry in snapshot.entries:
            record: MemoryRecord = self._resolve(entry.record_payload_ref, "MemoryRecord")
            records[(record.record_id, record.revision_id)] = record
        key = lambda r: (r.record_id, r.revision_id, r.content_digest)  # noqa: E731
        record_refs = tuple(sorted(handle.input_contribution.memory_refs, key=key))
        selected = tuple(sorted((e.record_ref for e in selection.entries), key=key))
        if record_refs != selected:
            raise MemoryContractError(
                "prepared handle memory refs drifted from the persisted selection"
            )
        prepared = _PreparedMemoryRead(
            query_ref=query_ref, selection_ref=selection_ref, selection=selection,
            record_refs=record_refs, records=records)
        return _MemoryBridgeSession(provider=self, request=request, prepared=prepared)


class _MemoryBridgeSession:
    def __init__(self, *, provider: MemoryRuntimeBridgeProvider,
                 request: BridgeOpenRequest, prepared: _PreparedMemoryRead) -> None:
        self._provider = provider
        self._request = request
        self._prepared = prepared

    def freeze_for_execution(self, *, kind: Any) -> BridgeStageOutcome:
        req = self._request
        prepared = self._prepared
        # the frozen InputSnapshot must carry EVERY prepared memory ref (the
        # resolver additionally re-verifies the whole expected tuple).
        frozen = {(r.record_id, r.revision_id, r.content_digest)
                  for r in req.input_snapshot.memory_record_refs}
        for ref in prepared.record_refs:
            if (ref.record_id, ref.revision_id, ref.content_digest) not in frozen:
                raise MemoryContractError(
                    "prepared memory selection drifted from the frozen InputSnapshot"
                )
        direct: list[TypedPayloadRef] = [prepared.query_ref, prepared.selection_ref]
        blocks: tuple[ProviderUntrustedBlock, ...] = ()
        kind_name = getattr(kind, "value", kind)
        if kind_name == "llm":
            block = render_memory(
                prepared.selection, prepared.records,
                policy=self._provider._policy,
                renderer_ref=self._provider._renderer_ref)
            block_ref = req.evidence_writer.put(
                token=req.handle.token, role="provider_prefetch",
                schema_ref=_SR("RenderedMemoryBlock"), payload=block,
                idempotency_key=(
                    f"{req.bridge.bridge_id}:{req.node.id}:"
                    f"a{req.handle.token.attempt}:"
                    f"c{req.handle.token.call_ordinal}:rendered-block"))
            direct.append(block_ref)
            blocks = (ProviderUntrustedBlock(
                bridge_id=req.bridge.bridge_id,
                bridge_priority=req.bridge.priority,
                call_ordinal=req.handle.token.call_ordinal,
                payload_ref=block_ref,
                media_type="text/markdown",
                rendered_length=block.total_bytes),)
        contribution = BridgeContribution(
            bridge_id=req.bridge.bridge_id,
            bridge_priority=req.bridge.priority,
            summary_digest=req.summary.summary_digest,
            tool_call_records=(),
            data_result_refs=(),
            direct_evidence_refs=tuple(direct),
            untrusted_blocks=blocks)
        return BridgeStageOutcome(status="completed", frozen_contribution=contribution)


def make_memory_bridge_provider_factory(
    *, stores: Any, policy: ResolvedMemoryPolicy, renderer_ref: ContentRef,
    config_bytes: bytes,
):
    """The trusted factory the service registers for the catalog provider ref."""

    def factory(*, bridge: Any, summary: Any) -> MemoryRuntimeBridgeProvider:
        return MemoryRuntimeBridgeProvider(
            bridge=bridge, summary=summary, stores=stores, policy=policy,
            renderer_ref=renderer_ref, config_bytes=config_bytes)

    return factory
