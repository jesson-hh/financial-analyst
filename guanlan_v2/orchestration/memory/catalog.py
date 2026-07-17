# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — the reviewed memory facade / execution-bridge catalog
extension over the immutable Phase-3 **data** catalog.

Everything here is *reviewed material*, never runtime behavior: the one closed
``memory.propose`` capability (input ``MemoryProposalRequest@1`` → output
``MemoryProposalReceipt@1``), the strict registered
:class:`~guanlan_v2.orchestration.memory.models.MemoryBridgePrefetchBinding`\\ @1 /
:class:`~guanlan_v2.orchestration.memory.models.MemoryFacadeDescriptor`\\ @1 /
:class:`~guanlan_v2.orchestration.memory.models.MemoryCapturePolicy`\\ @1
catalog materials, the one canonical Phase-2 ``ExecutionBridgeDescriptor@1``
material for the ``memory.runtime`` bridge (``pre_input_kind="memory_refs_v1"``,
a globally reviewed priority distinct from data), the pure reviewed
:class:`MemoryBridgeSupportAnalyzer` (memory retrieval is frozen pre-input
evidence: ``min_finalized_tool_calls_on_success=0`` and
``max_capability_invocations=0``) and the cumulative
:func:`build_phase3_full_catalog` builder that extends **only** the canonical
``PHASE3_DATA_CATALOG_DIGEST`` base.

The reviewed full-Phase-3 golden grants ``memory.propose`` to **no existing
worker**, contains no production proposal-grant row and preserves every
inherited ``read_categories`` tuple exactly. ``PHASE3_FULL_MEMORY_READERS`` is a
derived read-only set of inherited workers that already had ``"memory"`` — it is
empty today, so the reviewed prefetch binding carries zero rows; conformance
tests use a separately reviewed derived test snapshot.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from guanlan_v2.orchestration.catalog import (
    CapabilityDescriptor,
    CapabilityManifestEntry,
    CatalogError,
    ContentManifestEntry,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    WorkerCatalogSnapshot,
    build_catalog_snapshot,
    catalog_material_digest,
)
from guanlan_v2.orchestration.catalog_runtime import (
    build_text_material,
    parse_bridge_descriptor,
    serialize_bridge_descriptor,
)
from guanlan_v2.orchestration.digest import DigestHex, content_digest
from guanlan_v2.orchestration.enums import ExecutionKind
from guanlan_v2.orchestration.memory.models import (
    MEMORY_BRIDGE_ID,
    MemoryBorrowGrant,
    MemoryBridgePrefetchBinding,
    MemoryCapturePolicy,
    MemoryFacadeDescriptor,
    MemoryProposalGrant,
    MemorySourcePolicyEntry,
    ResolvedMemoryPolicy,
)
from guanlan_v2.orchestration.refs import CapabilityRef, ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import (
    BridgeStaticSupportSummary,
    ExecutionBridgeDescriptor,
)

__all__ = [
    "MEMORY_BRIDGE_ID",
    "PHASE3_FULL_CATALOG_VERSION",
    "MemoryBridgeSupportAnalyzer",
    "serialize_memory_prefetch_binding",
    "parse_memory_prefetch_binding",
    "serialize_memory_facade_descriptor",
    "parse_memory_facade_descriptor",
    "serialize_capture_policy",
    "parse_capture_policy",
    "reviewed_capture_policy",
    "memory_propose_capability",
    "phase3_memory_surface",
    "build_phase3_full_catalog",
    "phase3_full_catalog_snapshot",
    "resolved_memory_policy",
    "memory_readers_of",
    "PHASE3_MEMORY_FACADE_DESCRIPTOR_REF",  # noqa: F822 — lazy via __getattr__
    "PHASE3_FULL_BASE_CATALOG_DIGEST",  # noqa: F822 — lazy via __getattr__
    "PHASE3_FULL_CATALOG_DIGEST",  # noqa: F822 — lazy via __getattr__
    "PHASE3_FULL_MEMORY_READERS",  # noqa: F822 — lazy via __getattr__
]

PHASE3_FULL_CATALOG_VERSION = "phase3-full-v1"
#: the globally reviewed memory bridge priority — distinct from data's 100.
MEMORY_BRIDGE_PRIORITY = 200


# --------------------------------------------------------------------------- #
# Canonical material serializers (strict round-trip guardrail bytes)           #
# --------------------------------------------------------------------------- #
def _canonical_material_bytes(model: Any) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def serialize_memory_prefetch_binding(binding: MemoryBridgePrefetchBinding) -> bytes:
    return _canonical_material_bytes(binding)


def parse_memory_prefetch_binding(raw: bytes) -> MemoryBridgePrefetchBinding:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CatalogError(f"memory prefetch binding material is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise CatalogError("memory prefetch binding material must be a JSON object")
    try:
        return MemoryBridgePrefetchBinding.model_validate(obj, strict=False)
    except ValidationError as exc:
        raise CatalogError(f"invalid MemoryBridgePrefetchBinding material: {exc}") from exc


def serialize_memory_facade_descriptor(descriptor: MemoryFacadeDescriptor) -> bytes:
    return _canonical_material_bytes(descriptor)


def parse_memory_facade_descriptor(raw: bytes) -> MemoryFacadeDescriptor:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CatalogError(f"memory facade material is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise CatalogError("memory facade material must be a JSON object")
    try:
        return MemoryFacadeDescriptor.model_validate(obj, strict=False)
    except ValidationError as exc:
        raise CatalogError(f"invalid MemoryFacadeDescriptor material: {exc}") from exc


def serialize_capture_policy(policy: MemoryCapturePolicy) -> bytes:
    return _canonical_material_bytes(policy)


def parse_capture_policy(raw: bytes) -> MemoryCapturePolicy:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CatalogError(f"capture policy material is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise CatalogError("capture policy material must be a JSON object")
    try:
        return MemoryCapturePolicy.model_validate(obj, strict=False)
    except ValidationError as exc:
        raise CatalogError(f"invalid MemoryCapturePolicy material: {exc}") from exc


# --------------------------------------------------------------------------- #
# The reviewed conservative capture/visibility/ranking policy                  #
# --------------------------------------------------------------------------- #
def reviewed_capture_policy() -> MemoryCapturePolicy:
    """The one reviewed conservative v1 policy (frozen by its hand-reviewed golden).

    Source mappings are honest to the two REAL stores: agent ``_shared``/own
    markdown and console keyed/unkeyed/archive-tail/session dated lines.
    ``archive_tail_chars=4000`` freezes the exact existing console behavior
    (``tools._archive_tail`` reads the trailing 4000 characters). No borrow
    grant is reviewed. ``top_k_non_mandatory=5`` matches the existing
    ``AgentMemory.load_relevant`` default.
    """
    return MemoryCapturePolicy.build(
        policy_id="memory.policy.conservative",
        policy_version="1",
        source_mappings=(
            MemorySourcePolicyEntry(
                source_id="agent.own", kind="semantic", scope="agent_own",
                importance=0.5, mandatory=False),
            MemorySourcePolicyEntry(
                source_id="agent.shared", kind="semantic", scope="agent_shared",
                importance=0.6, mandatory=False),
            MemorySourcePolicyEntry(
                source_id="console.global.archive", kind="episodic", scope="console_global",
                importance=0.3, mandatory=False),
            MemorySourcePolicyEntry(
                source_id="console.global.keyed", kind="procedural", scope="console_global",
                importance=0.8, mandatory=True),
            MemorySourcePolicyEntry(
                source_id="console.global.unkeyed", kind="episodic", scope="console_global",
                importance=0.4, mandatory=False),
            MemorySourcePolicyEntry(
                source_id="console.session", kind="episodic", scope="console_session",
                importance=0.4, mandatory=False),
        ),
        archive_tail_chars=4000,
        context_scopes=("console_global", "console_session"),
        worker_scopes=("agent_own", "agent_shared"),
        borrow_grants=(),
        allowed_kinds=("episodic", "procedural", "semantic"),
        top_k_non_mandatory=5,
        max_mandatory=64,
        max_record_bytes=4096,
        max_total_rendered_bytes=65536,
    )


# --------------------------------------------------------------------------- #
# The one closed memory.propose capability                                     #
# --------------------------------------------------------------------------- #
def _build_memory_propose_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="cap.memory.propose",
        version="1",
        capability_kind="tool",
        transport="in_process",
        operation="memory.propose",
        input_schema_ref=SchemaRef(name="MemoryProposalRequest", version="1"),
        output_schema_ref=SchemaRef(name="MemoryProposalReceipt", version="1"),
    )


def _capability_material(
    desc: CapabilityDescriptor,
) -> tuple[CapabilityRef, ResolvedCapabilityMaterial]:
    tmp = ResolvedCapabilityMaterial(
        ref=CapabilityRef(id=desc.id, version=desc.version, content_digest="0" * 64),
        descriptor=desc,
    )
    digest = catalog_material_digest(tmp)
    ref = CapabilityRef(id=desc.id, version=desc.version, content_digest=digest)
    return ref, ResolvedCapabilityMaterial(ref=ref, descriptor=desc)


def memory_propose_capability() -> tuple[CapabilityRef, ResolvedCapabilityMaterial]:
    """The sealed ``memory.propose`` capability ref + resolved material."""
    return _capability_material(_build_memory_propose_descriptor())


# --------------------------------------------------------------------------- #
# The pure reviewed memory bridge support analyzer                             #
# --------------------------------------------------------------------------- #
class MemoryBridgeSupportAnalyzer:
    """The pure reviewed :class:`BridgeSupportAnalyzer` for the memory bridge.

    Memory retrieval is frozen pre-input evidence, not a CapabilityGateway call:
    it reports ``min_finalized_tool_calls_on_success=0`` and
    ``max_capability_invocations=0`` and verifies every activated reader has
    exactly one closed query projection. It cannot access a clock, store,
    filesystem, gateway or provider.
    """

    def analyze(
        self,
        *,
        candidate_plan_digest: str,
        node: Any,
        worker: Any,
        descriptor: ExecutionBridgeDescriptor,
        descriptor_ref: ContentRef,
        config_bytes: bytes,
    ) -> BridgeStaticSupportSummary:
        binding = parse_memory_prefetch_binding(config_bytes)
        if binding.bridge_id != descriptor.bridge_id:
            raise CatalogError(
                f"memory prefetch binding bridge_id {binding.bridge_id!r} does not "
                f"equal the descriptor bridge_id {descriptor.bridge_id!r}"
            )
        if "memory" not in worker.read_categories:
            raise CatalogError(
                f"memory bridge analyzed for worker {worker.id!r} without the "
                "'memory' read category (activation drift)"
            )
        rows = tuple(r for r in binding.rows if r.worker_id == worker.id)
        if len(rows) != 1:
            raise CatalogError(
                f"every activated memory reader requires exactly one closed query "
                f"projection; worker {worker.id!r} has {len(rows)}"
            )
        return BridgeStaticSupportSummary.build(
            candidate_plan_digest=candidate_plan_digest,
            node_id=node.id,
            node_params_digest=content_digest(dict(node.params)),
            worker_id=worker.id,
            worker_digest=worker.semantic_digest(),
            bridge_id=descriptor.bridge_id,
            descriptor_ref=descriptor_ref,
            config_ref=descriptor.config_ref,
            provider_ref=descriptor.provider_handler_ref,
            analyzer_ref=descriptor.support_analyzer_ref,
            allowed_capability_refs=(),
            min_finalized_tool_calls_on_success=0,
            max_capability_invocations=0,
            pre_input_kind=descriptor.pre_input_kind,
            lifecycle="static_prefetch_v1",
        )


# --------------------------------------------------------------------------- #
# The deterministic reviewed memory surface (pure, in-memory)                   #
# --------------------------------------------------------------------------- #
_RENDERER_BYTES = (
    b"# memory.render.deterministic v1\n"
    b"Deterministic canonical rendering of a MemorySelection into one\n"
    b"RenderedMemoryBlock (see guanlan_v2/orchestration/memory/runtime.py);\n"
    b"trust=untrusted_data; byte/count overflow fails before prompt assembly.\n"
)
_PROPOSAL_HANDLER_BYTES = (
    b"# memory.facade.proposal_handler v1\n"
    b"Trusted service-owned memory.propose handler: idempotent pending-proposal\n"
    b"submission that DELEGATES to the existing reviewed writers (dream\n"
    b"proposal_writer / console memory_write_impl); it never gains a raw\n"
    b"accepted-write path.\n"
)
_PROVIDER_BYTES = (
    b"# bridge.memory_runtime.provider v1\n"
    b"Trusted static-prefetch MemoryRuntimeBridge provider handler for the\n"
    b"memory.runtime bridge (pre_input_kind=memory_refs_v1).\n"
)
_ANALYZER_BYTES = (
    b"# bridge.memory_runtime.analyzer v1\n"
    b"Pure reviewed memory support analyzer: memory retrieval is frozen\n"
    b"pre-input evidence -- min_finalized_tool_calls_on_success=0 and\n"
    b"max_capability_invocations=0; every activated reader needs exactly one\n"
    b"closed query projection.\n"
)


def memory_readers_of(snapshot: WorkerCatalogSnapshot) -> frozenset[str]:
    """The derived read-only set of workers already carrying ``\"memory\"``."""
    return frozenset(w.id for w in snapshot.workers if "memory" in w.read_categories)


class _Phase3MemorySurface:
    """The deterministic reviewed Phase-3 memory surface (pure, in-memory).

    Built against the inherited memory-reader set of a given base snapshot so a
    derived test snapshot (with a reviewed test reader/proposer) reuses the same
    assembly path; the canonical production surface uses the immutable data
    catalog base (whose reader set is empty today).
    """

    def __init__(
        self,
        *,
        base_memory_readers: frozenset[str] = frozenset(),
        prefetch_rows: tuple = (),
        proposal_grants: tuple[MemoryProposalGrant, ...] = (),
    ) -> None:
        # -- the one proposal capability -------------------------------------- #
        self.proposal_capability_ref, self.proposal_capability_material = (
            memory_propose_capability()
        )

        # -- handler materials ------------------------------------------------- #
        self.renderer_ref, self.renderer_material = build_text_material(
            id="memory.render.deterministic", version="1", kind="handler",
            raw=_RENDERER_BYTES)
        self.proposal_handler_ref, self.proposal_handler_material = build_text_material(
            id="memory.facade.proposal_handler", version="1", kind="handler",
            raw=_PROPOSAL_HANDLER_BYTES)
        self.provider_ref, self.provider_material = build_text_material(
            id="bridge.memory_runtime.provider", version="1", kind="handler",
            raw=_PROVIDER_BYTES)
        self.analyzer_ref, self.analyzer_material = build_text_material(
            id="bridge.memory_runtime.analyzer", version="1", kind="handler",
            raw=_ANALYZER_BYTES)

        # -- the reviewed policy material (guardrail) --------------------------- #
        self.policy = reviewed_capture_policy()
        policy_bytes = serialize_capture_policy(self.policy)
        self.policy_ref, self.policy_material = build_text_material(
            id="memory.facade.policy", version="1", kind="guardrail", raw=policy_bytes)

        # -- the reviewed prefetch binding (guardrail) --------------------------- #
        self.prefetch_binding = MemoryBridgePrefetchBinding.build(
            bridge_id=MEMORY_BRIDGE_ID, bridge_version="1", rows=tuple(prefetch_rows))
        row_workers = frozenset(r.worker_id for r in self.prefetch_binding.rows)
        if row_workers != base_memory_readers:
            raise CatalogError(
                "the frozen prefetch binding must contain exactly one reviewed query "
                f"projection per inherited memory reader; readers={sorted(base_memory_readers)} "
                f"rows={sorted(row_workers)}"
            )
        config_bytes = serialize_memory_prefetch_binding(self.prefetch_binding)
        self.config_ref, self.config_material = build_text_material(
            id="bridge.memory_runtime.prefetch", version="1", kind="guardrail",
            raw=config_bytes)

        # -- the self-contained facade descriptor (guardrail) ------------------- #
        self.facade_descriptor = MemoryFacadeDescriptor(
            descriptor_id="memory.facade",
            descriptor_version="1",
            proposal_capability_ref=self.proposal_capability_ref,
            proposal_handler_ref=self.proposal_handler_ref,
            policy_ref=self.policy_ref,
            renderer_ref=self.renderer_ref,
            prefetch_binding_ref=self.config_ref,
            supported_proposal_targets=("agent", "console"),
            proposal_grants=tuple(proposal_grants),
        )
        facade_bytes = serialize_memory_facade_descriptor(self.facade_descriptor)
        self.facade_ref, self.facade_material = build_text_material(
            id="memory.facade.descriptor", version="1", kind="guardrail",
            raw=facade_bytes)

        # -- the one canonical execution-bridge descriptor ----------------------- #
        self.bridge_descriptor = ExecutionBridgeDescriptor(
            bridge_id=MEMORY_BRIDGE_ID,
            bridge_version="1",
            priority=MEMORY_BRIDGE_PRIORITY,
            provider_handler_ref=self.provider_ref,
            config_ref=self.config_ref,
            support_analyzer_ref=self.analyzer_ref,
            config_schema_ref=SchemaRef(name="MemoryBridgePrefetchBinding", version="1"),
            activation_capability_refs=(),
            activation_read_categories=("memory",),
            supported_execution_kinds=(ExecutionKind.DETERMINISTIC, ExecutionKind.LLM),
            pre_input_kind="memory_refs_v1",
            lifecycle="static_prefetch_v1",
        )
        descriptor_bytes = serialize_bridge_descriptor(self.bridge_descriptor)
        self.descriptor_ref, self.descriptor_material = build_text_material(
            id="bridge.memory_runtime.descriptor", version="1", kind="guardrail",
            raw=descriptor_bytes)

    def text_materials(self) -> tuple[ResolvedTextMaterial, ...]:
        return (
            self.renderer_material, self.proposal_handler_material,
            self.provider_material, self.analyzer_material,
            self.policy_material, self.config_material,
            self.facade_material, self.descriptor_material,
        )


_SURFACE: _Phase3MemorySurface | None = None


def phase3_memory_surface() -> _Phase3MemorySurface:
    """The cached canonical reviewed memory surface (production: zero readers,
    zero prefetch rows, zero proposal grants)."""
    global _SURFACE
    if _SURFACE is None:
        _SURFACE = _Phase3MemorySurface()
    return _SURFACE


def resolved_memory_policy() -> ResolvedMemoryPolicy:
    """The exact catalog-bound policy material — the only policy with authority."""
    surface = phase3_memory_surface()
    return ResolvedMemoryPolicy(policy=surface.policy, material_ref=surface.policy_ref)


# --------------------------------------------------------------------------- #
# The cumulative full (data + memory) catalog builder                          #
# --------------------------------------------------------------------------- #
def build_phase3_full_catalog(
    phase3_data_snapshot: WorkerCatalogSnapshot,
    *,
    facade_descriptor_ref: ContentRef,
    facade_descriptor: MemoryFacadeDescriptor,
    memory_bridge_descriptor: ExecutionBridgeDescriptor,
    memory_bridge_prefetch_binding: MemoryBridgePrefetchBinding,
    memory_bridge_support_analyzer_ref: ContentRef,
    resolved_materials: tuple[Any, ...],
) -> WorkerCatalogSnapshot:
    """Extend the immutable Phase-3 data catalog with the reviewed memory surface.

    Rejects every non-canonical data base, requires ``facade_descriptor_ref ==
    PHASE3_MEMORY_FACADE_DESCRIPTOR_REF``, parses/round-trips every complete
    registered body, verifies semantic and external material digests separately,
    requires one-to-one prefetch coverage of the derived inherited memory-reader
    set and performs **no WorkerSpec update** (every inherited worker — and its
    ``read_categories`` tuple — passes through byte-identical).
    """
    # -- (1) the sole canonical data base ----------------------------------- #
    from guanlan_v2.orchestration.data.catalog import PHASE3_DATA_CATALOG_DIGEST

    if phase3_data_snapshot.catalog_digest != PHASE3_DATA_CATALOG_DIGEST:
        raise CatalogError(
            "build_phase3_full_catalog requires the canonical immutable Phase-3 "
            f"data catalog ({PHASE3_DATA_CATALOG_DIGEST}); got base "
            f"{phase3_data_snapshot.catalog_digest}"
        )

    # -- (2) the exact builder-owned facade ref ------------------------------ #
    canonical_facade_ref: ContentRef = __getattr__("PHASE3_MEMORY_FACADE_DESCRIPTOR_REF")
    if facade_descriptor_ref != canonical_facade_ref:
        raise CatalogError(
            "facade_descriptor_ref must equal PHASE3_MEMORY_FACADE_DESCRIPTOR_REF "
            "(supported-target/grant/handler/policy/prefetch drift changes the "
            "external descriptor ref)"
        )

    # -- (3) index materials -------------------------------------------------- #
    text_by_key: dict[tuple[str, str], ResolvedTextMaterial] = {}
    caps_by_key: dict[tuple[str, str], ResolvedCapabilityMaterial] = {}
    for m in resolved_materials:
        if isinstance(m, ResolvedTextMaterial):
            text_by_key[m.ref_key] = m
        elif isinstance(m, ResolvedCapabilityMaterial):
            caps_by_key[m.ref_key] = m
        else:
            raise CatalogError(f"unsupported resolved material type {type(m).__name__}")

    def _text_material_of(ref: ContentRef, kind: str, ctx: str) -> ResolvedTextMaterial:
        m = text_by_key.get((ref.id, ref.version))
        if m is None:
            raise CatalogError(f"{ctx}: no resolved material for {ref.id}@{ref.version}")
        if m.kind != kind:
            raise CatalogError(
                f"{ctx}: material {ref.id}@{ref.version} is kind={m.kind!r}, expected {kind!r}"
            )
        if m.ref.content_digest != ref.content_digest:
            raise CatalogError(f"{ctx}: material digest drift for {ref.id}@{ref.version}")
        return m

    # -- (4) the one memory.propose capability -------------------------------- #
    expected_cap_ref, expected_cap_material = memory_propose_capability()
    cap_m = caps_by_key.get((expected_cap_ref.id, expected_cap_ref.version))
    if cap_m is None:
        raise CatalogError("no resolved capability material for cap.memory.propose@1")
    if cap_m.descriptor != expected_cap_material.descriptor:
        raise CatalogError("cap.memory.propose descriptor drifted from the closed form")
    if cap_m.descriptor.input_schema_ref != SchemaRef(name="MemoryProposalRequest", version="1"):
        raise CatalogError("memory.propose input schema must be MemoryProposalRequest@1")
    if cap_m.descriptor.output_schema_ref != SchemaRef(name="MemoryProposalReceipt", version="1"):
        raise CatalogError("memory.propose output schema must be MemoryProposalReceipt@1")
    extra_caps = set(caps_by_key) - {
        (e.ref.id, e.ref.version) for e in phase3_data_snapshot.capability_manifest
    } - {(expected_cap_ref.id, expected_cap_ref.version)}
    if extra_caps:
        raise CatalogError(f"unreviewed extra capability material(s): {sorted(extra_caps)}")

    # -- (5) facade material: exact bytes + strict round-trip ------------------ #
    facade_m = _text_material_of(facade_descriptor_ref, "guardrail", "memory facade")
    if facade_m.raw_utf8 != serialize_memory_facade_descriptor(facade_descriptor):
        raise CatalogError("facade material bytes do not encode the given facade descriptor")
    if parse_memory_facade_descriptor(facade_m.raw_utf8) != facade_descriptor:
        raise CatalogError("facade material does not round-trip to the given descriptor")
    if facade_descriptor.proposal_capability_ref != expected_cap_ref:
        raise CatalogError("facade proposal_capability_ref must be the exact memory.propose ref")

    # -- (6) policy / prefetch / renderer / proposal-handler materials --------- #
    policy_m = _text_material_of(facade_descriptor.policy_ref, "guardrail", "memory policy")
    policy = parse_capture_policy(policy_m.raw_utf8)
    if serialize_capture_policy(policy) != policy_m.raw_utf8:
        raise CatalogError("capture policy material does not round-trip byte-identically")
    config_m = _text_material_of(
        facade_descriptor.prefetch_binding_ref, "guardrail", "memory prefetch binding"
    )
    if parse_memory_prefetch_binding(config_m.raw_utf8) != memory_bridge_prefetch_binding:
        raise CatalogError("prefetch material does not encode the reviewed binding")
    _text_material_of(facade_descriptor.renderer_ref, "handler", "memory renderer")
    _text_material_of(facade_descriptor.proposal_handler_ref, "handler", "memory proposal handler")

    # -- (7) the memory bridge descriptor -------------------------------------- #
    desc = memory_bridge_descriptor
    if desc.bridge_id != MEMORY_BRIDGE_ID:
        raise CatalogError(f"the memory bridge id must be {MEMORY_BRIDGE_ID!r}")
    if desc.pre_input_kind != "memory_refs_v1" or desc.lifecycle != "static_prefetch_v1":
        raise CatalogError(
            "the memory bridge requires pre_input_kind='memory_refs_v1' + "
            "lifecycle='static_prefetch_v1'"
        )
    if desc.config_schema_ref != SchemaRef(name="MemoryBridgePrefetchBinding", version="1"):
        raise CatalogError("the memory bridge config schema must be MemoryBridgePrefetchBinding@1")
    if desc.activation_read_categories != ("memory",) or desc.activation_capability_refs:
        raise CatalogError(
            "the memory bridge activates exactly on read_categories containing 'memory'"
        )
    if desc.support_analyzer_ref != memory_bridge_support_analyzer_ref:
        raise CatalogError("the memory bridge must bind the exact reviewed support-analyzer ref")
    if desc.config_ref != facade_descriptor.prefetch_binding_ref:
        raise CatalogError("the memory bridge config must be the facade's prefetch binding material")
    provider_m = _text_material_of(desc.provider_handler_ref, "handler", "memory bridge provider")
    analyzer_m = _text_material_of(desc.support_analyzer_ref, "handler", "memory bridge analyzer")
    if provider_m.ref == analyzer_m.ref:
        raise CatalogError("memory bridge provider and analyzer must be distinct handler materials")
    descriptor_bytes = serialize_bridge_descriptor(desc)
    descriptor_key: tuple[str, str] | None = None
    base_priorities: set[int] = set()
    for key, m in text_by_key.items():
        if m.kind != "guardrail":
            continue
        parsed = parse_bridge_descriptor(m.raw_utf8)
        if parsed is None:
            continue
        if m.raw_utf8 == descriptor_bytes:
            descriptor_key = key
        elif parsed.bridge_id != desc.bridge_id:
            base_priorities.add(parsed.priority)
    if descriptor_key is None:
        raise CatalogError(
            "the exact memory bridge descriptor guardrail material is missing/drifted"
        )
    if desc.priority in base_priorities:
        raise CatalogError(
            f"the memory bridge priority {desc.priority} collides with another "
            "bridge descriptor (a globally reviewed distinct priority is required)"
        )

    # -- (8) derived inherited reader set ↔ prefetch coverage (one-to-one) ------ #
    readers = memory_readers_of(phase3_data_snapshot)
    row_workers = frozenset(r.worker_id for r in memory_bridge_prefetch_binding.rows)
    if row_workers != readers:
        raise CatalogError(
            "prefetch rows must cover the derived inherited memory-reader set "
            f"one-to-one; readers={sorted(readers)} rows={sorted(row_workers)}"
        )

    # -- (9) proposal grants: catalog-bound least privilege --------------------- #
    worker_ids = {w.id for w in phase3_data_snapshot.workers}
    holders = {
        w.id
        for w in phase3_data_snapshot.workers
        if any(c.id == expected_cap_ref.id and c.version == expected_cap_ref.version
               for c in w.capability_allowlist)
    }
    if holders:
        raise CatalogError(
            "no inherited worker may already hold memory.propose (a production "
            f"grant requires a new reviewed catalog): {sorted(holders)}"
        )
    for grant in facade_descriptor.proposal_grants:
        if grant.worker_id not in worker_ids:
            raise CatalogError(
                f"proposal grant names unknown worker {grant.worker_id!r}"
            )

    # -- (10) assemble; NO WorkerSpec update ------------------------------------ #
    allowed_new: set[tuple[str, str]] = {descriptor_key}
    for ref in (
        facade_descriptor_ref, facade_descriptor.policy_ref,
        facade_descriptor.prefetch_binding_ref, facade_descriptor.renderer_ref,
        facade_descriptor.proposal_handler_ref, desc.provider_handler_ref,
        desc.support_analyzer_ref,
    ):
        allowed_new.add((ref.id, ref.version))
    base_text = {(e.ref.id, e.ref.version) for e in phase3_data_snapshot.content_manifest} | {
        (e.ref.id, e.ref.version) for e in phase3_data_snapshot.skill_manifest
    }
    new_keys = set(text_by_key) - base_text
    orphans = new_keys - allowed_new
    if orphans:
        raise CatalogError(f"unreviewed extra text material(s): {sorted(orphans)}")

    new_content = tuple(
        ContentManifestEntry(
            ref=text_by_key[key].ref,
            kind=text_by_key[key].kind,
            name=text_by_key[key].ref.id,
            description=(
                f"Reviewed Phase-3 memory material {key[0]}@{key[1]} "
                f"({text_by_key[key].kind})."
            ),
            source_identity=text_by_key[key].ref.id,
        )
        for key in sorted(new_keys)
    )
    new_cap_entries = (
        CapabilityManifestEntry(
            ref=cap_m.ref,
            capability_kind=cap_m.descriptor.capability_kind,
            transport=cap_m.descriptor.transport,
        ),
    )

    snapshot = build_catalog_snapshot(
        catalog_version=PHASE3_FULL_CATALOG_VERSION,
        content_manifest=tuple(phase3_data_snapshot.content_manifest) + new_content,
        skill_manifest=tuple(phase3_data_snapshot.skill_manifest),
        capability_manifest=tuple(phase3_data_snapshot.capability_manifest) + new_cap_entries,
        workers=tuple(phase3_data_snapshot.workers),
        resolved_material=tuple(text_by_key.values()) + tuple(caps_by_key.values()),
    )
    # inherited read_categories are preserved exactly (no WorkerSpec update).
    base_by_id = {w.id: w for w in phase3_data_snapshot.workers}
    for w in snapshot.workers:
        if w != base_by_id[w.id]:  # pragma: no cover - reviewed invariant
            raise CatalogError(f"worker {w.id!r} drifted through the full-catalog build")
    return snapshot


def phase3_full_catalog_snapshot() -> WorkerCatalogSnapshot:
    """The canonical immutable full (data + memory) catalog snapshot."""
    from guanlan_v2.orchestration.data.catalog import (
        phase3_data_catalog_snapshot,
        phase3_data_surface,
    )
    from guanlan_v2.orchestration.presets import load_compat_catalog

    base = phase3_data_catalog_snapshot()
    runtime = load_compat_catalog()
    p_text, p_caps = runtime.resolved_materials()
    data_surface = phase3_data_surface()
    surface = phase3_memory_surface()
    return build_phase3_full_catalog(
        base,
        facade_descriptor_ref=surface.facade_ref,
        facade_descriptor=surface.facade_descriptor,
        memory_bridge_descriptor=surface.bridge_descriptor,
        memory_bridge_prefetch_binding=surface.prefetch_binding,
        memory_bridge_support_analyzer_ref=surface.analyzer_ref,
        resolved_materials=tuple(p_text) + tuple(p_caps)
        + data_surface.text_materials() + data_surface.capability_materials
        + surface.text_materials() + (surface.proposal_capability_material,),
    )


# --------------------------------------------------------------------------- #
# Lazy canonical digests / refs (PEP 562)                                       #
# --------------------------------------------------------------------------- #
_PHASE3_FULL_CATALOG_DIGEST: str | None = None


def __getattr__(name: str) -> Any:
    if name == "PHASE3_MEMORY_FACADE_DESCRIPTOR_REF":
        return phase3_memory_surface().facade_ref
    if name == "PHASE3_FULL_BASE_CATALOG_DIGEST":
        from guanlan_v2.orchestration.data.catalog import PHASE3_DATA_CATALOG_DIGEST

        return PHASE3_DATA_CATALOG_DIGEST
    if name == "PHASE3_FULL_CATALOG_DIGEST":
        global _PHASE3_FULL_CATALOG_DIGEST
        if _PHASE3_FULL_CATALOG_DIGEST is None:
            _PHASE3_FULL_CATALOG_DIGEST = phase3_full_catalog_snapshot().catalog_digest
        return _PHASE3_FULL_CATALOG_DIGEST
    if name == "PHASE3_FULL_MEMORY_READERS":
        from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot

        return memory_readers_of(phase3_data_catalog_snapshot())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
