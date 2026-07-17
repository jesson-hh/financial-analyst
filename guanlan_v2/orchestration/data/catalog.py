# -*- coding: utf-8 -*-
"""Phase 3 · Task 5 — the reviewed data capability / execution-bridge catalog
extension over the canonical end-of-Phase-2 static catalog.

Everything here is *reviewed material*, never runtime behavior: the seven closed
Phase-1 :class:`~guanlan_v2.orchestration.catalog.CapabilityDescriptor`\\ @1 data
capabilities (``RawFetch`` is an unpublished gateway/adapter intermediate and is
never a capability output schema), the strict registered
:class:`DataPrefetchOperation`\\ @1 / :class:`DataBridgePrefetchBinding`\\ @1
catalog-material contracts, the one canonical Phase-2
``ExecutionBridgeDescriptor@1`` material for the ``data.runtime`` bridge, the pure
reviewed :class:`DataBridgeSupportAnalyzer` (bounds are *summed from rows*, never
trusted from declared summary numbers) and the cumulative
:func:`build_phase3_catalog` builder that extends **only** the canonical
``PHASE2_STATIC_CATALOG_DIGEST`` base.

No capability is granted globally: the initial integration grants ``dec.pm``
exactly one data method (``verified_snapshot``), coverage between grants and
prefetch rows is one-to-one, and ``compat.*`` remains ``static_legacy_only`` with
no data capability.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from pydantic import ValidationError, model_validator

from guanlan_v2.orchestration.catalog import (
    CapabilityDescriptor,
    CapabilityManifestEntry,
    CatalogError,
    ContentManifestEntry,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    WorkerCatalogSnapshot,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
)
from guanlan_v2.orchestration.catalog_runtime import (
    build_text_material,
    serialize_bridge_descriptor,
)
from guanlan_v2.orchestration.data.pit import FreshnessPolicy
from guanlan_v2.orchestration.data.source import (
    DATA_METHOD_SCHEMAS,
    DataMethodSpec,
    DataSourceDescriptor,
    RouteEntry,
)
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    content_digest,
)
from guanlan_v2.orchestration.enums import DataBackend, DataMode, ExecutionKind
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    LogicalId,
    SchemaRef,
)
from guanlan_v2.orchestration.runtime_contracts import (
    BridgeStaticSupportSummary,
    ExecutionBridgeDescriptor,
)

__all__ = [
    "ParamBinding",
    "DataPrefetchOperation",
    "DataBridgePrefetchBinding",
    "serialize_prefetch_binding",
    "parse_prefetch_binding",
    "DataBridgeSupportAnalyzer",
    "DATA_BRIDGE_ID",
    "PHASE3_DATA_CAPABILITIES",  # noqa: F822 — lazy via module __getattr__
    "PHASE3_BASE_CATALOG_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE3_DATA_CATALOG_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "data_capability_refs",
    "phase3_data_surface",
    "build_phase3_catalog",
    "phase3_data_catalog_snapshot",
    "PHASE3_DATA_CATALOG_VERSION",
    "CATALOG_PUBLIC_MODELS",
    "CATALOG_INTERNAL_MODELS",
]

_DIGEST_PLACEHOLDER = "0" * 64
DATA_BRIDGE_ID = "data.runtime"
PHASE3_DATA_CATALOG_VERSION = "phase3-data-v1"

#: the one reviewed initial integration grant: worker id -> granted data method ids.
_REVIEWED_INTEGRATION_GRANTS: dict[str, tuple[str, ...]] = {
    "dec.pm": ("verified_snapshot",),
}


# --------------------------------------------------------------------------- #
# ParamBinding / DataPrefetchOperation / DataBridgePrefetchBinding              #
# --------------------------------------------------------------------------- #
class ParamBinding(DigestModel):
    """One exact JSON-pointer/value binding into a method's params schema.

    Sources are closed: an already-validated ``PlanNode.params`` pointer
    (``node_param``), a named InputSnapshot value pointer (``input_value``) or a
    reviewed JSON constant (``const``). There is no expression, callable,
    model-generated value, clock, global config or dynamic late-call escape —
    the closed shape *is* the guarantee.
    """

    schema_version: Literal["1"] = "1"
    target_pointer: NonEmptyStr
    source_kind: Literal["node_param", "input_value", "const"]
    source_pointer: NonEmptyStr | None = None
    input_name: LogicalId | None = None
    const_value: Any = None

    @model_validator(mode="after")
    def _verify(self) -> "ParamBinding":
        if not self.target_pointer.startswith("/"):
            raise ValueError("target_pointer must be a JSON pointer starting with '/'")
        if self.source_kind == "node_param":
            if self.source_pointer is None or not self.source_pointer.startswith("/"):
                raise ValueError("node_param binding requires a JSON source_pointer")
            if self.input_name is not None or self.const_value is not None:
                raise ValueError("node_param binding must not carry input_name/const_value")
        elif self.source_kind == "input_value":
            if self.input_name is None:
                raise ValueError("input_value binding requires the named input")
            if self.source_pointer is None or not self.source_pointer.startswith("/"):
                raise ValueError("input_value binding requires a JSON source_pointer")
            if self.const_value is not None:
                raise ValueError("input_value binding must not carry a const_value")
        else:  # const
            if self.const_value is None:
                raise ValueError("const binding requires a JSON const_value")
            if self.source_pointer is not None or self.input_name is not None:
                raise ValueError("const binding must not carry source_pointer/input_name")
        return self


class DataPrefetchOperation(DigestModel):
    """One reviewed (worker, method, capability) prefetch row.

    Binds the complete ordered non-empty fallback route of
    ``(source_ref, capability_ref)`` entries, the closed ``invocation_mode``,
    ``success_requires_finalized_call`` and the closed param projection.
    ``always_invoke`` bypasses a cache hit only when explicitly reviewed and is
    never inferred from ``tool_calls=REQUIRED``;
    ``success_requires_finalized_call=True`` is legal only under ``always_invoke``
    (node success impossible on cache/optional exhaustion).
    """

    schema_version: Literal["1"] = "1"
    worker_id: LogicalId
    method_ref: ContentRef
    capability_ref: CapabilityRef
    frozen_route: tuple[RouteEntry, ...]
    invocation_mode: Literal["cache_or_invoke", "always_invoke"]
    success_requires_finalized_call: bool = False
    param_bindings: tuple[ParamBinding, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "DataPrefetchOperation":
        if not self.frozen_route:
            raise ValueError("frozen_route must be a non-empty ordered fallback route")
        if self.success_requires_finalized_call and self.invocation_mode != "always_invoke":
            raise ValueError(
                "success_requires_finalized_call=True is legal only under always_invoke "
                "(node success must be impossible on cache/optional exhaustion)"
            )
        targets = [b.target_pointer for b in self.param_bindings]
        if targets != sorted(targets):
            raise ValueError("param_bindings must be canonically ordered by target_pointer")
        if len(set(targets)) != len(targets):
            raise ValueError("param_bindings must be duplicate-free by target_pointer")
        return self

    @property
    def row_max_invocations(self) -> int:
        """The row's maximum capability invocations = the full fallback route length."""
        return len(self.frozen_route)

    @property
    def row_min_finalized(self) -> int:
        """1 only for a reviewed ``always_invoke`` + required-finalized row, else 0."""
        return 1 if (
            self.invocation_mode == "always_invoke" and self.success_requires_finalized_call
        ) else 0

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        return (
            self.worker_id, self.method_ref.id, self.method_ref.version,
            self.capability_ref.id,
        )


class DataBridgePrefetchBinding(DigestModel):
    """The reviewed catalog-material binding of one execution bridge to its rows.

    Binds one exact bridge id to canonically ordered, duplicate-free
    :class:`DataPrefetchOperation` rows. ``binding_digest`` is verified; every
    referenced worker/method/route source/capability/schema cross-resolves in
    :func:`build_phase3_catalog`.
    """

    schema_version: Literal["1"] = "1"
    bridge_id: LogicalId
    bridge_version: NonEmptyStr
    operations: tuple[DataPrefetchOperation, ...]
    binding_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"binding_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "DataBridgePrefetchBinding":
        if not self.operations:
            raise ValueError("a prefetch binding must carry at least one operation row")
        keys = [op.sort_key for op in self.operations]
        if keys != sorted(keys):
            raise ValueError("operations must be canonically ordered")
        grant_keys = [(op.worker_id, op.capability_ref.id, op.capability_ref.version)
                      for op in self.operations]
        if len(set(grant_keys)) != len(grant_keys):
            raise ValueError("operations must be duplicate-free per (worker, capability)")
        if self.binding_digest != self.semantic_digest():
            raise ValueError("declared binding_digest does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "DataBridgePrefetchBinding":
        ops = tuple(sorted(fields.pop("operations", ()), key=lambda op: op.sort_key))
        body = dict(operations=ops, **fields)
        try:
            digest = cls.digest_of_fields(projection="semantic", **body)
        except (ValueError, TypeError, AttributeError, KeyError):
            digest = _DIGEST_PLACEHOLDER
        return cls(**body, binding_digest=digest)


def serialize_prefetch_binding(binding: DataBridgePrefetchBinding) -> bytes:
    """Serialize a binding to deterministic canonical guardrail-material bytes."""
    return json.dumps(
        binding.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_prefetch_binding(raw: bytes) -> DataBridgePrefetchBinding:
    """Parse config guardrail bytes into a verified binding (strict marker material)."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CatalogError(f"prefetch binding material is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise CatalogError("prefetch binding material must be a JSON object")
    try:
        # reviewed catalog material: JSON arrays become tuples (strict=False), but
        # extra fields, closed literals and the binding digest re-verify still hold.
        return DataBridgePrefetchBinding.model_validate(obj, strict=False)
    except ValidationError as exc:
        raise CatalogError(f"invalid DataBridgePrefetchBinding material: {exc}") from exc


# --------------------------------------------------------------------------- #
# The pure reviewed bridge support analyzer                                    #
# --------------------------------------------------------------------------- #
class DataBridgeSupportAnalyzer:
    """The pure reviewed :class:`BridgeSupportAnalyzer` for the data bridge.

    Derives its per-node aggregate bounds by *summing rows* rather than trusting
    declared summary numbers: each row contributes
    ``max_capability_invocations = len(frozen_route)``; ``cache_or_invoke`` has
    minimum zero; ``always_invoke`` has minimum one only when
    ``success_requires_finalized_call=True``. Multi-route fallback therefore never
    claims max one, and optional/cache success never claims a false minimum. It
    performs no I/O, reads no clock and calls no gateway.
    """

    def analyze(
        self,
        *,
        candidate_plan_digest: str,
        node: Any,
        worker: WorkerSpec,
        descriptor: ExecutionBridgeDescriptor,
        descriptor_ref: ContentRef,
        config_bytes: bytes,
    ) -> BridgeStaticSupportSummary:
        binding = parse_prefetch_binding(config_bytes)
        if binding.bridge_id != descriptor.bridge_id:
            raise CatalogError(
                f"prefetch binding bridge_id {binding.bridge_id!r} does not equal the "
                f"descriptor bridge_id {descriptor.bridge_id!r}"
            )
        allow_keys = {(c.id, c.version, c.content_digest) for c in worker.capability_allowlist}
        rows = tuple(op for op in binding.operations if op.worker_id == worker.id)
        allowed: dict[tuple[str, str], CapabilityRef] = {}
        min_finalized = 0
        max_invocations = 0
        for op in rows:
            cap = op.capability_ref
            if (cap.id, cap.version, cap.content_digest) not in allow_keys:
                raise CatalogError(
                    f"prefetch row grants {cap.id}@{cap.version} outside worker "
                    f"{worker.id!r}'s allowlist (analyzer failure, not authority)"
                )
            allowed[(cap.id, cap.version)] = cap
            min_finalized += op.row_min_finalized
            max_invocations += op.row_max_invocations
        allowed_refs = tuple(sorted(allowed.values(), key=lambda c: (c.id, c.version)))
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
            allowed_capability_refs=allowed_refs,
            min_finalized_tool_calls_on_success=min_finalized,
            max_capability_invocations=max_invocations,
            pre_input_kind=descriptor.pre_input_kind,
            lifecycle="static_prefetch_v1",
        )


# --------------------------------------------------------------------------- #
# The reviewed data capability descriptors + refs                              #
# --------------------------------------------------------------------------- #
def _capability_id(method_id: str) -> str:
    return f"cap.data.{method_id}"


def _build_data_capabilities() -> tuple[CapabilityDescriptor, ...]:
    """The seven reviewed, unchanged closed ``CapabilityDescriptor@1`` entries.

    Each entry contains only id/version/capability_kind/transport/operation/
    input_schema_ref/output_schema_ref; the SchemaRefs are the registered
    ``DataRequest`` and the method's named concrete DataResult. ``RawFetch`` is an
    unpublished gateway/adapter intermediate and is never the output SchemaRef.
    Phase 3 must not add mode/backend/side-effect/handler fields to this v1
    descriptor — modes/backends/read_only live in :class:`DataMethodSpec` and
    trusted handler material in :class:`DataSourceDescriptor`.
    """
    out = []
    for binding in DATA_METHOD_SCHEMAS:
        out.append(CapabilityDescriptor(
            id=_capability_id(binding.method_id),
            version="1",
            capability_kind="data_adapter",
            transport="in_process",
            operation=binding.operation,
            input_schema_ref=SchemaRef(name="DataRequest", version="1"),
            output_schema_ref=binding.result_schema_ref,
        ))
    return tuple(out)


def _capability_material(desc: CapabilityDescriptor) -> tuple[CapabilityRef, ResolvedCapabilityMaterial]:
    tmp = ResolvedCapabilityMaterial(
        ref=CapabilityRef(id=desc.id, version=desc.version, content_digest=_DIGEST_PLACEHOLDER),
        descriptor=desc,
    )
    digest = catalog_material_digest(tmp)
    ref = CapabilityRef(id=desc.id, version=desc.version, content_digest=digest)
    return ref, ResolvedCapabilityMaterial(ref=ref, descriptor=desc)


def data_capability_refs() -> dict[str, CapabilityRef]:
    """Method id → its sealed data-capability ref (digest from the descriptor material)."""
    return {
        b.method_id: _capability_material(desc)[0]
        for b, desc in zip(DATA_METHOD_SCHEMAS, _build_data_capabilities())
    }


# --------------------------------------------------------------------------- #
# The reviewed deterministic Phase-3 data surface (in-memory materials)         #
# --------------------------------------------------------------------------- #
_RENDERER_BYTES = (
    b"# data.render.deterministic v1\n"
    b"Deterministic canonical-JSON rendering of a typed DataResult\n"
    b"(see guanlan_v2/orchestration/data/render.py); trust=untrusted_data.\n"
)
_SOURCE_HANDLER_BYTES = (
    b"# data.source.guanlan_datafeed v1\n"
    b"Trusted service-owned data-source adapter handler (guanlan datafeed).\n"
    b"Dispatch reaches it only through the Phase-2 CapabilityGateway.\n"
)
_PROVIDER_BYTES = (
    b"# bridge.data_runtime.provider v1\n"
    b"Trusted static-prefetch provider handler for the data.runtime bridge.\n"
)
_ANALYZER_BYTES = (
    b"# bridge.data_runtime.analyzer v1\n"
    b"Pure reviewed support analyzer: bounds are summed from prefetch rows\n"
    b"(max=len(frozen_route) per row; min=1 only for always_invoke+required).\n"
)


class _Phase3DataSurface:
    """The deterministic reviewed Phase-3 data surface (pure, in-memory).

    Everything is derived from module constants and sealed digests, so the surface
    (and therefore the Phase-3 catalog digest) is location / load-order independent.
    """

    def __init__(self) -> None:
        # -- capabilities ---------------------------------------------------- #
        self.capability_descriptors = _build_data_capabilities()
        pairs = [_capability_material(d) for d in self.capability_descriptors]
        self.capability_refs = {d.id: ref for d, (ref, _m) in zip(self.capability_descriptors, pairs)}
        self.capability_materials = tuple(m for _r, m in pairs)

        # -- shared handler / renderer materials ----------------------------- #
        self.renderer_ref, self.renderer_material = build_text_material(
            id="data.render.deterministic", version="1", kind="handler", raw=_RENDERER_BYTES)
        self.source_handler_ref, self.source_handler_material = build_text_material(
            id="data.source.guanlan_datafeed", version="1", kind="handler",
            raw=_SOURCE_HANDLER_BYTES)
        self.provider_ref, self.provider_material = build_text_material(
            id="bridge.data_runtime.provider", version="1", kind="handler", raw=_PROVIDER_BYTES)
        self.analyzer_ref, self.analyzer_material = build_text_material(
            id="bridge.data_runtime.analyzer", version="1", kind="handler", raw=_ANALYZER_BYTES)

        # -- the reviewed default freshness policy value (a registry material) - #
        self.freshness_policy = FreshnessPolicy.build(
            policy_id="policy.freshness.default-elapsed", policy_version="1",
            method_or_category="default", max_elapsed_seconds=86400)
        self.freshness_ref = ContentRef(
            id="policy.freshness.default-elapsed", version="1",
            content_digest=self.freshness_policy.policy_digest)

        # -- the seven reviewed method specs ---------------------------------- #
        specs = []
        for binding in DATA_METHOD_SCHEMAS:
            cap_ref = self.capability_refs[_capability_id(binding.method_id)]
            specs.append(DataMethodSpec.build(
                method_id=binding.method_id, method_version="1", category=binding.category,
                params_schema_ref=binding.params_schema_ref,
                batch_schema_ref=binding.batch_schema_ref,
                result_schema_ref=binding.result_schema_ref,
                optional=binding.optional,
                supported_modes=(DataMode.ONLINE, DataMode.PIT_REPLAY),
                supported_backends=(DataBackend.CACHE, DataBackend.LIVE, DataBackend.PIT_STORE),
                read_only=True,
                capability_ref=cap_ref,
                freshness_policy_ref=self.freshness_ref,
                renderer_ref=self.renderer_ref,
            ))
        self.method_specs = tuple(sorted(specs, key=lambda s: (s.method_id, s.method_version)))
        self.spec_by_method = {s.method_id: s for s in self.method_specs}

        # -- the one reviewed source descriptor -------------------------------- #
        self.source_descriptor = DataSourceDescriptor.build(
            source_id="guanlan.datafeed", source_version="1",
            method_refs=tuple(s.method_ref for s in self.method_specs),
            method_capability_refs=tuple(
                sorted(self.capability_refs.values(), key=lambda c: (c.id, c.version))),
            supported_modes=(DataMode.ONLINE, DataMode.PIT_REPLAY),
            supported_backends=(DataBackend.CACHE, DataBackend.LIVE, DataBackend.PIT_STORE),
            handler_ref=self.source_handler_ref,
            source_config_schema_ref=SchemaRef(name="DataSourceConfigSnapshot", version="1"),
        )
        self.source_ref = ContentRef(
            id=self.source_descriptor.source_id,
            version=self.source_descriptor.source_version,
            content_digest=self.source_descriptor.descriptor_digest,
        )

        # -- the reviewed prefetch binding (integration grants only) ----------- #
        ops = []
        for worker_id, method_ids in _REVIEWED_INTEGRATION_GRANTS.items():
            for method_id in method_ids:
                spec = self.spec_by_method[method_id]
                ops.append(DataPrefetchOperation(
                    worker_id=worker_id,
                    method_ref=spec.method_ref,
                    capability_ref=spec.capability_ref,
                    frozen_route=(RouteEntry(
                        source_ref=self.source_ref, capability_ref=spec.capability_ref),),
                    invocation_mode="cache_or_invoke",
                    success_requires_finalized_call=False,
                    param_bindings=(
                        ParamBinding(target_pointer="/as_of", source_kind="node_param",
                                     source_pointer="/asof_date"),
                        ParamBinding(target_pointer="/symbols", source_kind="node_param",
                                     source_pointer="/code"),
                    ),
                ))
        self.prefetch_binding = DataBridgePrefetchBinding.build(
            bridge_id=DATA_BRIDGE_ID, bridge_version="1", operations=tuple(ops))
        config_bytes = serialize_prefetch_binding(self.prefetch_binding)
        self.config_ref, self.config_material = build_text_material(
            id="bridge.data_runtime.prefetch", version="1", kind="guardrail", raw=config_bytes)

        # -- the one canonical execution-bridge descriptor ---------------------- #
        self.bridge_descriptor = ExecutionBridgeDescriptor(
            bridge_id=DATA_BRIDGE_ID,
            bridge_version="1",
            priority=100,  # reviewed for later data + memory composition
            provider_handler_ref=self.provider_ref,
            config_ref=self.config_ref,
            support_analyzer_ref=self.analyzer_ref,
            config_schema_ref=SchemaRef(name="DataBridgePrefetchBinding", version="1"),
            activation_capability_refs=tuple(
                sorted(self.capability_refs.values(), key=lambda c: (c.id, c.version))),
            activation_read_categories=(),
            supported_execution_kinds=(ExecutionKind.DETERMINISTIC, ExecutionKind.LLM),
            pre_input_kind="none",
            lifecycle="static_prefetch_v1",
        )
        descriptor_bytes = serialize_bridge_descriptor(self.bridge_descriptor)
        self.descriptor_ref, self.descriptor_material = build_text_material(
            id="bridge.data_runtime.descriptor", version="1", kind="guardrail",
            raw=descriptor_bytes)

    # -- assembled build inputs -------------------------------------------- #
    def text_materials(self) -> tuple[ResolvedTextMaterial, ...]:
        return (
            self.renderer_material, self.source_handler_material,
            self.provider_material, self.analyzer_material,
            self.config_material, self.descriptor_material,
        )

    # manifest entries are derived inside :func:`build_phase3_catalog` from the
    # actual resolved materials, so a reviewed drift (new bytes) yields a new
    # digest instead of an entry/material mismatch.


_SURFACE: _Phase3DataSurface | None = None


def phase3_data_surface() -> _Phase3DataSurface:
    """The cached deterministic reviewed data surface (pure; built on first use)."""
    global _SURFACE
    if _SURFACE is None:
        _SURFACE = _Phase3DataSurface()
    return _SURFACE


# --------------------------------------------------------------------------- #
# The cumulative Phase-3 catalog builder                                       #
# --------------------------------------------------------------------------- #
def _worker_update_is_allowlist_only(base: WorkerSpec, update: WorkerSpec) -> bool:
    """True iff ``update`` differs from ``base`` only in ``capability_allowlist``."""
    for name in type(base).model_fields:
        if name == "capability_allowlist":
            continue
        if getattr(base, name) != getattr(update, name):
            return False
    return True


def build_phase3_catalog(
    phase2_snapshot: WorkerCatalogSnapshot,
    *,
    reviewed_worker_updates: tuple[WorkerSpec, ...],
    reviewed_source_descriptors: tuple[DataSourceDescriptor, ...],
    reviewed_method_specs: tuple[DataMethodSpec, ...],
    data_bridge_descriptor: ExecutionBridgeDescriptor,
    data_bridge_prefetch_binding: DataBridgePrefetchBinding,
    resolved_materials: tuple[Any, ...],
) -> WorkerCatalogSnapshot:
    """Extend the canonical end-of-Phase-2 catalog with the reviewed data surface.

    First rejects every base other than the exact canonical
    ``PHASE2_STATIC_CATALOG_DIGEST``, then cross-resolves the whole reviewed
    identity set (method spec ↔ capability ↔ renderer/handler material ↔ bridge
    descriptor/config/provider/analyzer ↔ prefetch rows ↔ worker grants — coverage
    is one-to-one), applies only capability-allowlist worker updates, and rebuilds
    through the Phase-1 material-aware builder (which re-verifies every digest).
    It never mutates the Phase-2 snapshot; Task 9 extends the result by a new
    digest/golden rather than overwriting it.
    """
    # -- (1) the sole canonical base --------------------------------------- #
    from guanlan_v2.orchestration.presets import PHASE2_STATIC_CATALOG_DIGEST

    if phase2_snapshot.catalog_digest != PHASE2_STATIC_CATALOG_DIGEST:
        raise CatalogError(
            "build_phase3_catalog requires the canonical end-of-Phase-2 static catalog "
            f"({PHASE2_STATIC_CATALOG_DIGEST}); got base {phase2_snapshot.catalog_digest}"
        )

    # -- (2) index materials ------------------------------------------------ #
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

    # -- (3) the closed data capability set --------------------------------- #
    expected_caps = {d.id: d for d in _build_data_capabilities()}
    data_cap_refs: dict[tuple[str, str], CapabilityRef] = {}
    for cap_id, desc in expected_caps.items():
        m = caps_by_key.get((cap_id, desc.version))
        if m is None:
            raise CatalogError(f"no resolved capability material for {cap_id}@{desc.version}")
        if m.descriptor != desc:
            raise CatalogError(f"capability descriptor drift for {cap_id}@{desc.version}")
        data_cap_refs[(cap_id, desc.version)] = m.ref
    extra_caps = set(caps_by_key) - {
        (e.ref.id, e.ref.version) for e in phase2_snapshot.capability_manifest
    } - set(data_cap_refs)
    if extra_caps:
        raise CatalogError(f"unreviewed extra capability material(s): {sorted(extra_caps)}")

    # -- (4) method specs cross-resolve -------------------------------------- #
    binding_by_method = {b.method_id: b for b in DATA_METHOD_SCHEMAS}
    spec_by_ref: dict[tuple[str, str, str], DataMethodSpec] = {}
    for spec in reviewed_method_specs:
        canon = binding_by_method.get(spec.method_id)
        if canon is None:
            raise CatalogError(f"method spec {spec.method_id!r} is not a canonical data method")
        if (spec.params_schema_ref != canon.params_schema_ref
                or spec.batch_schema_ref != canon.batch_schema_ref
                or spec.result_schema_ref != canon.result_schema_ref):
            raise CatalogError(
                f"method spec {spec.method_id!r} schema refs drift from the canonical binding"
            )
        cap_key = (spec.capability_ref.id, spec.capability_ref.version)
        if data_cap_refs.get(cap_key) != spec.capability_ref:
            raise CatalogError(
                f"method spec {spec.method_id!r} capability_ref does not cross-resolve"
            )
        _text_material_of(spec.renderer_ref, "handler", f"method {spec.method_id} renderer")
        spec_by_ref[(spec.method_id, spec.method_version, spec.spec_digest)] = spec

    # -- (5) source descriptors cross-resolve -------------------------------- #
    source_refs: dict[tuple[str, str], str] = {}
    for sd in reviewed_source_descriptors:
        _text_material_of(sd.handler_ref, "handler", f"source {sd.source_id} handler")
        known_method_refs = {
            (s.method_id, s.method_version, s.spec_digest) for s in reviewed_method_specs
        }
        for mref in sd.method_refs:
            if (mref.id, mref.version, mref.content_digest) not in known_method_refs:
                raise CatalogError(
                    f"source {sd.source_id!r} references unreviewed method "
                    f"{mref.id}@{mref.version}"
                )
        for cref in sd.method_capability_refs:
            if data_cap_refs.get((cref.id, cref.version)) != cref:
                raise CatalogError(
                    f"source {sd.source_id!r} references unreviewed capability "
                    f"{cref.id}@{cref.version}"
                )
        source_refs[(sd.source_id, sd.source_version)] = sd.descriptor_digest

    # -- (6) the bridge descriptor / config / provider / analyzer ------------- #
    desc = data_bridge_descriptor
    if desc.pre_input_kind != "none" or desc.lifecycle != "static_prefetch_v1":
        raise CatalogError(
            "the data bridge requires pre_input_kind='none' + lifecycle='static_prefetch_v1'"
        )
    if desc.config_schema_ref != SchemaRef(name="DataBridgePrefetchBinding", version="1"):
        raise CatalogError("the data bridge config schema must be DataBridgePrefetchBinding@1")
    expected_activation = tuple(sorted(data_cap_refs.values(), key=lambda c: (c.id, c.version)))
    if desc.activation_capability_refs != expected_activation:
        raise CatalogError(
            "the data bridge activation predicates must be the exact Phase-3 data "
            "capability refs"
        )
    provider_m = _text_material_of(desc.provider_handler_ref, "handler", "bridge provider")
    analyzer_m = _text_material_of(desc.support_analyzer_ref, "handler", "bridge analyzer")
    if provider_m.ref == analyzer_m.ref:
        raise CatalogError("bridge provider and analyzer must be distinct handler materials")
    config_m = _text_material_of(desc.config_ref, "guardrail", "bridge config")
    if parse_prefetch_binding(config_m.raw_utf8) != data_bridge_prefetch_binding:
        raise CatalogError("bridge config material does not encode the reviewed binding")
    # the descriptor itself must exist as an exact marker-bearing guardrail material.
    descriptor_bytes = serialize_bridge_descriptor(desc)
    descriptor_key = None
    for key, m in text_by_key.items():
        if m.kind == "guardrail" and m.raw_utf8 == descriptor_bytes:
            descriptor_key = key
            break
    if descriptor_key is None:
        raise CatalogError(
            "the exact data bridge descriptor guardrail material is missing/drifted"
        )

    # -- (7) prefetch binding rows cross-resolve + one-to-one coverage --------- #
    if data_bridge_prefetch_binding.bridge_id != desc.bridge_id:
        raise CatalogError("prefetch binding bridge_id does not equal the descriptor bridge_id")
    spec_by_method_ref = {
        (s.method_id, s.method_version, s.spec_digest): s for s in reviewed_method_specs
    }
    base_workers = {w.id: w for w in phase2_snapshot.workers}
    updates_by_id: dict[str, WorkerSpec] = {}
    for upd in reviewed_worker_updates:
        base = base_workers.get(upd.id)
        if base is None:
            raise CatalogError(f"worker update {upd.id!r} does not name a Phase-2 worker")
        if upd.id.startswith("compat."):
            raise CatalogError(
                "compat.* remains static_legacy_only and gains no data capability "
                "without explicit legacy mapping/material evidence"
            )
        if not _worker_update_is_allowlist_only(base, upd):
            raise CatalogError(
                f"worker update {upd.id!r} changes more than the capability allowlist "
                "(unreviewed grant)"
            )
        updates_by_id[upd.id] = upd

    workers = tuple(updates_by_id.get(w.id, w) for w in phase2_snapshot.workers)
    granted: set[tuple[str, str, str]] = set()
    for w in workers:
        for cap in w.capability_allowlist:
            if data_cap_refs.get((cap.id, cap.version)) == cap:
                granted.add((w.id, cap.id, cap.version))

    rows: set[tuple[str, str, str]] = set()
    for op in data_bridge_prefetch_binding.operations:
        mkey = (op.method_ref.id, op.method_ref.version, op.method_ref.content_digest)
        spec = spec_by_method_ref.get(mkey)
        if spec is None:
            raise CatalogError(
                f"prefetch row method {op.method_ref.id}@{op.method_ref.version} does not "
                "cross-resolve to a reviewed method spec"
            )
        if op.capability_ref != spec.capability_ref:
            raise CatalogError(
                f"prefetch row capability does not equal the {spec.method_id!r} method "
                "capability"
            )
        for entry in op.frozen_route:
            if source_refs.get((entry.source_ref.id, entry.source_ref.version)) != \
                    entry.source_ref.content_digest:
                raise CatalogError(
                    f"prefetch route source {entry.source_ref.id}@{entry.source_ref.version} "
                    "does not cross-resolve to a reviewed source descriptor"
                )
            if entry.capability_ref != op.capability_ref:
                raise CatalogError(
                    "prefetch route entries must carry the row's exact method capability"
                )
        rows.add((op.worker_id, op.capability_ref.id, op.capability_ref.version))

    if granted != rows:
        missing_rows = granted - rows
        ungranted = rows - granted
        raise CatalogError(
            "worker data-capability grants and prefetch rows must be one-to-one; "
            f"grants without a row: {sorted(missing_rows)}; rows without a grant: "
            f"{sorted(ungranted)}"
        )

    # -- (8) assemble + rebuild through the Phase-1 material-aware builder ---- #
    # the ONLY new text materials allowed are the refs the reviewed inputs name:
    # per-method renderers, source handlers, and the bridge descriptor/config/
    # provider/analyzer. Anything else is unreviewed extra material and fails.
    allowed_new: set[tuple[str, str]] = {descriptor_key}
    for spec in reviewed_method_specs:
        allowed_new.add((spec.renderer_ref.id, spec.renderer_ref.version))
    for sd in reviewed_source_descriptors:
        allowed_new.add((sd.handler_ref.id, sd.handler_ref.version))
    for ref in (desc.provider_handler_ref, desc.support_analyzer_ref, desc.config_ref):
        allowed_new.add((ref.id, ref.version))

    base_text = {(e.ref.id, e.ref.version) for e in phase2_snapshot.content_manifest} | {
        (e.ref.id, e.ref.version) for e in phase2_snapshot.skill_manifest
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
                f"Reviewed Phase-3 data material {key[0]}@{key[1]} "
                f"({text_by_key[key].kind})."
            ),
            source_identity=text_by_key[key].ref.id,
        )
        for key in sorted(new_keys)
    )

    new_cap_entries = tuple(
        CapabilityManifestEntry(
            ref=caps_by_key[key].ref,
            capability_kind=caps_by_key[key].descriptor.capability_kind,
            transport=caps_by_key[key].descriptor.transport,
        )
        for key in sorted(data_cap_refs)
    )

    return build_catalog_snapshot(
        catalog_version=PHASE3_DATA_CATALOG_VERSION,
        content_manifest=tuple(phase2_snapshot.content_manifest) + new_content,
        skill_manifest=tuple(phase2_snapshot.skill_manifest),
        capability_manifest=tuple(phase2_snapshot.capability_manifest) + new_cap_entries,
        workers=workers,
        resolved_material=tuple(text_by_key.values()) + tuple(caps_by_key.values()),
    )


def phase3_data_catalog_snapshot() -> WorkerCatalogSnapshot:
    """The canonical immutable Phase-3 data catalog snapshot.

    Loads the canonical end-of-Phase-2 static catalog (pilot finals + ``compat.*``,
    digest-pinned), grants ``dec.pm`` exactly the reviewed ``verified_snapshot``
    method, and extends through :func:`build_phase3_catalog`. Location /
    load-order independent — every material is addressed by ref identity.
    """
    from guanlan_v2.orchestration.presets import load_compat_catalog

    runtime = load_compat_catalog()
    base = runtime.snapshot
    p_text, p_caps = runtime.resolved_materials()
    surface = phase3_data_surface()

    updates = []
    for worker_id, method_ids in _REVIEWED_INTEGRATION_GRANTS.items():
        base_worker = next(w for w in base.workers if w.id == worker_id)
        grant_refs = tuple(surface.spec_by_method[m].capability_ref for m in method_ids)
        new_allowlist = tuple(sorted(
            tuple(base_worker.capability_allowlist) + grant_refs,
            key=lambda c: (c.id, c.version),
        ))
        fields = {name: getattr(base_worker, name) for name in type(base_worker).model_fields}
        fields["capability_allowlist"] = new_allowlist
        updates.append(WorkerSpec(**fields))

    return build_phase3_catalog(
        base,
        reviewed_worker_updates=tuple(updates),
        reviewed_source_descriptors=(surface.source_descriptor,),
        reviewed_method_specs=surface.method_specs,
        data_bridge_descriptor=surface.bridge_descriptor,
        data_bridge_prefetch_binding=surface.prefetch_binding,
        resolved_materials=tuple(p_text) + tuple(p_caps)
        + surface.text_materials() + surface.capability_materials,
    )


# --------------------------------------------------------------------------- #
# Lazy canonical digests (PEP 562)                                             #
# --------------------------------------------------------------------------- #
_PHASE3_DATA_CATALOG_DIGEST: str | None = None


def __getattr__(name: str) -> Any:
    if name == "PHASE3_DATA_CAPABILITIES":
        return _build_data_capabilities()
    if name == "PHASE3_BASE_CATALOG_DIGEST":
        from guanlan_v2.orchestration.presets import PHASE2_STATIC_CATALOG_DIGEST

        return PHASE2_STATIC_CATALOG_DIGEST
    if name == "PHASE3_DATA_CATALOG_DIGEST":
        global _PHASE3_DATA_CATALOG_DIGEST
        if _PHASE3_DATA_CATALOG_DIGEST is None:
            _PHASE3_DATA_CATALOG_DIGEST = phase3_data_catalog_snapshot().catalog_digest
        return _PHASE3_DATA_CATALOG_DIGEST
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


#: reviewed Task-5 data-only partition contribution of this module.
CATALOG_PUBLIC_MODELS: tuple[type, ...] = (
    ParamBinding,
    DataPrefetchOperation,
    DataBridgePrefetchBinding,
)
CATALOG_INTERNAL_MODELS: dict[type, str] = {}
