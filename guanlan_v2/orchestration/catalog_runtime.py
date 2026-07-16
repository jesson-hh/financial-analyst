# -*- coding: utf-8 -*-
"""Phase 2 · Task 3 — read-only catalog material runtime + trusted factory registry.

This module builds an **immutable runtime view** over exactly one already-verified
Phase 1 :class:`~guanlan_v2.orchestration.catalog.WorkerCatalogSnapshot`. It is a
*consumer* of the Phase 1 catalog ABI, never a fork of it: every digest, skill-v1
and material-coverage rule is inherited by delegating to the Phase 1
:func:`~guanlan_v2.orchestration.catalog.build_catalog_snapshot` /
:func:`~guanlan_v2.orchestration.catalog.validate_catalog_snapshot` builders.

Load-bearing design
-------------------
* **Catalog authority is read-only.** :class:`CatalogRuntime` exposes lookup /
  resolution only (``worker`` / ``text`` / ``capability`` / ``resolve_worker`` +
  the pinned snapshot & catalog digest). There is deliberately **no**
  ``register(spec)``, mutable version or second catalog-digest implementation.
* **Physical locators are service configuration, never contract data.** A
  :class:`MaterialSource` is the sole place a logical ref becomes physical bytes /
  a descriptor. That mapping never enters a snapshot, ``WorkerSpec``, ``PlanDraft``
  or ``Plan``. The bundled :class:`InMemoryMaterialSource` stores bytes only; the
  pilot loader reads files into it and never persists a path in any object.
* **Startup fails on drift / missing / extra material.** :meth:`CatalogRuntime.build`
  resolves every referenced ref, rebuilds the snapshot through the Phase 1
  material-aware builder (which recomputes each material digest and re-checks
  coverage / skill-v1) and requires *exact* pinned ``catalog_digest`` equality.
  A source that holds an unreferenced material is rejected as *extra*.
* **Trusted factories are keyed by catalog ref identity.**
  :class:`TrustedFactoryRegistry` binds a handler factory to a handler
  ``ContentRef`` (id + version + content digest) drawn from the catalog and a
  model factory to a model tier declared by a catalog LLM worker — never to a
  caller-supplied callable or filesystem path, and never rebindable.

Reconciliation note
-------------------
The frozen plan document referred to a single ``ResolvedMaterial`` build input;
the implemented Phase 1 API exposes the two concrete classes
:class:`~guanlan_v2.orchestration.catalog.ResolvedTextMaterial` and
:class:`~guanlan_v2.orchestration.catalog.ResolvedCapabilityMaterial` (with
``ResolvedMaterial`` as their ``Union`` alias). This module consumes those real
classes directly.
"""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

import yaml

from pydantic import BaseModel, ConfigDict

from guanlan_v2.orchestration.catalog import (
    CapabilityDescriptor,
    CapabilityManifestEntry,
    CatalogError,
    ContentManifestEntry,
    EvidencePolicy,
    ExecutionSpec,
    InputBinding,
    OutputBinding,
    ResolvedCapabilityMaterial,
    ResolvedTextMaterial,
    SkillBinding,
    SkillManifest,
    WorkerCatalogSnapshot,
    WorkerSpec,
    build_catalog_snapshot,
    catalog_material_digest,
    count_final_workers,
    parse_skill_v1,
    validate_catalog_snapshot,
)
from guanlan_v2.orchestration.enums import (
    DataMode,
    ExecutionKind,
    Tier,
    ToolCallRequirement,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    SchemaRef,
)

__all__ = [
    "CatalogMaterialError",
    "MaterialSource",
    "InMemoryMaterialSource",
    "ResolvedWorkerRuntime",
    "CatalogRuntime",
    "TrustedFactoryRegistry",
    "load_pilot_catalog",
    "PILOT_CATALOG_PATH",
    "PILOT_MATERIALS_DIR",
]

_DIGEST_PLACEHOLDER = "0" * 64
TextKey = tuple[str, str]
CapabilityKey = tuple[str, str]
HandlerKey = tuple[str, str, str]


# --------------------------------------------------------------------------- #
# Error                                                                       #
# --------------------------------------------------------------------------- #
class CatalogMaterialError(Exception):
    """A physical material failed to resolve, drifted, or violated confinement."""


# --------------------------------------------------------------------------- #
# Material source                                                             #
# --------------------------------------------------------------------------- #
@runtime_checkable
class MaterialSource(Protocol):
    """Service configuration mapping a logical catalog ref to physical material.

    A ``MaterialSource`` is the only component that turns a logical ref into
    physical bytes / a descriptor; that mapping is deployment configuration and
    never appears in a snapshot, ``WorkerSpec``, ``PlanDraft`` or ``Plan``.
    Implementations resolve by ref identity and enumerate the keys they hold so
    the runtime can reject an unreferenced (extra) material at startup.
    """

    def text_bytes(self, ref: ContentRef) -> bytes:
        """Return the raw UTF-8 bytes for a text ref, or raise on a miss."""
        ...

    def capability_descriptor(self, ref: CapabilityRef) -> CapabilityDescriptor:
        """Return the descriptor for a capability ref, or raise on a miss."""
        ...

    def text_keys(self) -> frozenset[TextKey]:
        """Every ``(id, version)`` text material this source holds."""
        ...

    def capability_keys(self) -> frozenset[CapabilityKey]:
        """Every ``(id, version)`` capability material this source holds."""
        ...


class InMemoryMaterialSource:
    """A concrete in-memory :class:`MaterialSource`.

    Holds bytes / descriptors only — never a filesystem path. The pilot loader
    reads material files and hands the bytes here, so a resolved path lives only
    as a local variable during loading and is never stored on any object.
    """

    def __init__(
        self,
        *,
        text: Mapping[TextKey, bytes],
        capabilities: Mapping[CapabilityKey, CapabilityDescriptor],
    ) -> None:
        self._text: dict[TextKey, bytes] = dict(text)
        self._caps: dict[CapabilityKey, CapabilityDescriptor] = dict(capabilities)

    def text_bytes(self, ref: ContentRef) -> bytes:
        key = (ref.id, ref.version)
        try:
            return self._text[key]
        except KeyError:
            raise CatalogMaterialError(
                f"no text material for {ref.id}@{ref.version}"
            ) from None

    def capability_descriptor(self, ref: CapabilityRef) -> CapabilityDescriptor:
        key = (ref.id, ref.version)
        try:
            return self._caps[key]
        except KeyError:
            raise CatalogMaterialError(
                f"no capability material for {ref.id}@{ref.version}"
            ) from None

    def text_keys(self) -> frozenset[TextKey]:
        return frozenset(self._text)

    def capability_keys(self) -> frozenset[CapabilityKey]:
        return frozenset(self._caps)


# --------------------------------------------------------------------------- #
# Resolved worker runtime (materials, not factories)                          #
# --------------------------------------------------------------------------- #
class _StrictModel(BaseModel):
    """Strict, frozen, closed runtime DTO base.

    Mirrors the Phase 1 strict config without inheriting the registered-contract
    identity of ``ContractModel`` (the Task 1/2 idiom) so the Phase 1 contract
    completeness firewall never discovers a Phase 2 runtime DTO.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResolvedWorkerRuntime(_StrictModel):
    """A worker's service-owned materials, already checked against its ``WorkerSpec``.

    Holds the resolved prompt / skill / guardrail / handler / capability materials
    for one worker. It is frozen and carries no mutator: a consumer cannot add or
    replace a binding, and the materials it exposes are exactly the ones the
    selected :class:`WorkerSpec` declares (nothing more).
    """

    worker: WorkerSpec
    system_prompt: ResolvedTextMaterial | None = None
    skills: tuple[ResolvedTextMaterial, ...] = ()
    guardrails: tuple[ResolvedTextMaterial, ...] = ()
    handler: ResolvedTextMaterial | None = None
    capabilities: tuple[ResolvedCapabilityMaterial, ...] = ()

    @property
    def worker_id(self) -> str:
        return self.worker.id


# --------------------------------------------------------------------------- #
# Catalog runtime                                                             #
# --------------------------------------------------------------------------- #
class CatalogRuntime:
    """An immutable, read-only runtime index over one verified snapshot.

    Constructed via :meth:`build` from a pinned
    :class:`WorkerCatalogSnapshot` and a :class:`MaterialSource`. It exposes
    lookup / resolution only; there is no ``register`` / mutate surface and the
    snapshot (and its ``catalog_digest``) is fixed for the life of the object.
    """

    __slots__ = ("_snapshot", "_workers", "_text", "_caps")

    def __init__(
        self,
        *,
        snapshot: WorkerCatalogSnapshot,
        workers: Mapping[str, WorkerSpec],
        text: Mapping[TextKey, ResolvedTextMaterial],
        caps: Mapping[CapabilityKey, ResolvedCapabilityMaterial],
    ) -> None:
        self._snapshot = snapshot
        self._workers: Mapping[str, WorkerSpec] = MappingProxyType(dict(workers))
        self._text: Mapping[TextKey, ResolvedTextMaterial] = MappingProxyType(dict(text))
        self._caps: Mapping[CapabilityKey, ResolvedCapabilityMaterial] = MappingProxyType(
            dict(caps)
        )

    # -- construction ------------------------------------------------------- #
    @classmethod
    def build(
        cls, snapshot: WorkerCatalogSnapshot, source: MaterialSource
    ) -> "CatalogRuntime":
        """Resolve, verify and index the materials backing ``snapshot``.

        Steps: (1) re-validate the pinned snapshot's declared digests /
        cross-references; (2) resolve every referenced material (missing → error);
        (3) reject any *extra* material the source holds; (4) rebuild the snapshot
        via the Phase 1 material-aware builder (which recomputes each material
        digest and re-checks coverage / kind / skill-v1, catching drift); and
        (5) require exact pinned ``catalog_digest`` equality.
        """
        validate_catalog_snapshot(snapshot)  # raises CatalogError

        text_mats: dict[TextKey, ResolvedTextMaterial] = {}
        referenced_text: set[TextKey] = set()
        for entry in snapshot.content_manifest:
            raw = source.text_bytes(entry.ref)
            text_mats[entry.ref_key] = ResolvedTextMaterial(
                ref=entry.ref, kind=entry.kind, raw_utf8=raw
            )
            referenced_text.add(entry.ref_key)
        for entry in snapshot.skill_manifest:
            raw = source.text_bytes(entry.ref)
            text_mats[entry.ref_key] = ResolvedTextMaterial(
                ref=entry.ref, kind="skill", raw_utf8=raw
            )
            referenced_text.add(entry.ref_key)

        cap_mats: dict[CapabilityKey, ResolvedCapabilityMaterial] = {}
        referenced_cap: set[CapabilityKey] = set()
        for entry in snapshot.capability_manifest:
            desc = source.capability_descriptor(entry.ref)
            cap_mats[entry.ref_key] = ResolvedCapabilityMaterial(ref=entry.ref, descriptor=desc)
            referenced_cap.add(entry.ref_key)

        extra_text = source.text_keys() - referenced_text
        extra_cap = source.capability_keys() - referenced_cap
        if extra_text or extra_cap:
            raise CatalogMaterialError(
                "material source holds unreferenced material: "
                f"text={sorted(extra_text)} capability={sorted(extra_cap)}"
            )

        try:
            rebuilt = build_catalog_snapshot(
                catalog_version=snapshot.catalog_version,
                content_manifest=snapshot.content_manifest,
                skill_manifest=snapshot.skill_manifest,
                capability_manifest=snapshot.capability_manifest,
                workers=snapshot.workers,
                resolved_material=(*text_mats.values(), *cap_mats.values()),
            )
        except CatalogError as exc:
            raise CatalogMaterialError(
                f"resolved material does not match the pinned catalog: {exc}"
            ) from exc

        if rebuilt.catalog_digest != snapshot.catalog_digest:
            raise CatalogMaterialError(
                "rebuilt catalog digest does not equal the pinned snapshot digest "
                f"({rebuilt.catalog_digest} != {snapshot.catalog_digest})"
            )

        workers = {w.id: w for w in snapshot.workers}
        return cls(snapshot=snapshot, workers=workers, text=text_mats, caps=cap_mats)

    # -- pinned identity ---------------------------------------------------- #
    @property
    def snapshot(self) -> WorkerCatalogSnapshot:
        return self._snapshot

    @property
    def catalog_digest(self) -> str:
        return self._snapshot.catalog_digest

    @property
    def catalog_version(self) -> str:
        return self._snapshot.catalog_version

    # -- worker lookup ------------------------------------------------------ #
    def worker(self, worker_id: str) -> WorkerSpec:
        try:
            return self._workers[worker_id]
        except KeyError:
            raise CatalogMaterialError(f"unknown worker {worker_id!r}") from None

    def final_worker_ids(self) -> tuple[str, ...]:
        return tuple(w.id for w in self._snapshot.workers if w.catalog_role == "final")

    def final_worker_count(self) -> int:
        return count_final_workers(self._snapshot)

    # -- material lookup (confined by exact ref identity) ------------------- #
    def text(self, ref: ContentRef) -> ResolvedTextMaterial:
        mat = self._text.get((ref.id, ref.version))
        if mat is None:
            raise CatalogMaterialError(f"no text material for {ref.id}@{ref.version}")
        if mat.ref.content_digest != ref.content_digest:
            raise CatalogMaterialError(
                f"content digest mismatch for {ref.id}@{ref.version}"
            )
        return mat

    def capability(self, ref: CapabilityRef) -> ResolvedCapabilityMaterial:
        mat = self._caps.get((ref.id, ref.version))
        if mat is None:
            raise CatalogMaterialError(f"no capability material for {ref.id}@{ref.version}")
        if mat.ref.content_digest != ref.content_digest:
            raise CatalogMaterialError(
                f"capability digest mismatch for {ref.id}@{ref.version}"
            )
        return mat

    def resolve_worker(self, worker_id: str) -> ResolvedWorkerRuntime:
        """Return the pre-checked materials bound to ``worker_id``.

        Every material is looked up by the worker's own declared refs and
        cross-checked against the pinned content digest, so the result can only
        ever contain the bindings the :class:`WorkerSpec` itself declares.
        """
        w = self.worker(worker_id)
        system_prompt = self.text(w.system_prompt_ref) if w.system_prompt_ref else None
        skills = tuple(self.text(sb.skill_ref) for sb in w.skills)
        guardrails = tuple(self.text(g) for g in w.guardrail_refs)
        handler = self.text(w.execution.handler_ref) if w.execution.handler_ref else None
        capabilities = tuple(self.capability(c) for c in w.capability_allowlist)
        return ResolvedWorkerRuntime(
            worker=w,
            system_prompt=system_prompt,
            skills=skills,
            guardrails=guardrails,
            handler=handler,
            capabilities=capabilities,
        )


# --------------------------------------------------------------------------- #
# Trusted handler / model factory registry                                    #
# --------------------------------------------------------------------------- #
Factory = Callable[..., Any]


class TrustedFactoryRegistry:
    """Handler / model factories keyed by *catalog* ref identity.

    Registration keys are derived from the :class:`CatalogRuntime` — handler
    ``ContentRef`` identities of kind ``handler``, and model tiers declared by
    catalog LLM workers — never from a caller-supplied callable identity or
    filesystem path. A ref / tier not present in the catalog is rejected, and no
    binding may be replaced, so a caller can neither smuggle an off-catalog
    handler nor override a bound one.
    """

    def __init__(self, runtime: CatalogRuntime) -> None:
        self._handler_keys: frozenset[HandlerKey] = frozenset(
            (e.ref.id, e.ref.version, e.ref.content_digest)
            for e in runtime.snapshot.content_manifest
            if e.kind == "handler"
        )
        self._model_tiers: frozenset[str] = frozenset(
            w.execution.model_tier
            for w in runtime.snapshot.workers
            if w.execution.kind is ExecutionKind.LLM and w.execution.model_tier is not None
        )
        self._handlers: dict[HandlerKey, Factory] = {}
        self._models: dict[str, Factory] = {}

    def register_handler(self, ref: ContentRef, factory: Factory) -> None:
        key: HandlerKey = (ref.id, ref.version, ref.content_digest)
        if key not in self._handler_keys:
            raise CatalogMaterialError(
                f"handler ref {ref.id}@{ref.version} is not a catalog handler identity"
            )
        if key in self._handlers:
            raise CatalogMaterialError(
                f"handler {ref.id}@{ref.version} is already bound"
            )
        self._handlers[key] = factory

    def register_model_factory(self, model_tier: str, factory: Factory) -> None:
        if model_tier not in self._model_tiers:
            raise CatalogMaterialError(
                f"model tier {model_tier!r} is not used by any catalog LLM worker"
            )
        if model_tier in self._models:
            raise CatalogMaterialError(f"model tier {model_tier!r} is already bound")
        self._models[model_tier] = factory

    def handler_factory(self, ref: ContentRef) -> Factory:
        key: HandlerKey = (ref.id, ref.version, ref.content_digest)
        try:
            return self._handlers[key]
        except KeyError:
            raise CatalogMaterialError(
                f"no handler factory bound for {ref.id}@{ref.version}"
            ) from None

    def model_factory(self, model_tier: str) -> Factory:
        try:
            return self._models[model_tier]
        except KeyError:
            raise CatalogMaterialError(
                f"no model factory bound for tier {model_tier!r}"
            ) from None


# --------------------------------------------------------------------------- #
# Pilot catalog loader                                                        #
# --------------------------------------------------------------------------- #
_CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config" / "orchestration"
PILOT_CATALOG_PATH = _CONFIG_ROOT / "catalogs" / "phase2-pilot-v1.yaml"
PILOT_MATERIALS_DIR = _CONFIG_ROOT / "materials" / "phase2-pilot-v1"


def _schema_ref(token: str) -> SchemaRef:
    name, _, version = token.partition("@")
    if not version:
        raise CatalogMaterialError(f"schema token {token!r} must be 'Name@version'")
    return SchemaRef(name=name, version=version)


def _text_material(
    *, id: str, version: str, kind: str, raw: bytes
) -> tuple[ContentRef, ResolvedTextMaterial]:
    tmp = ResolvedTextMaterial(
        ref=ContentRef(id=id, version=version, content_digest=_DIGEST_PLACEHOLDER),
        kind=kind,
        raw_utf8=raw,
    )
    digest = catalog_material_digest(tmp)
    ref = ContentRef(id=id, version=version, content_digest=digest)
    return ref, ResolvedTextMaterial(ref=ref, kind=kind, raw_utf8=raw)


def _capability_material(entry: Mapping[str, Any]) -> tuple[CapabilityRef, ResolvedCapabilityMaterial]:
    desc = CapabilityDescriptor(
        id=entry["id"],
        version=str(entry["version"]),
        capability_kind=entry["capability_kind"],
        transport=entry["transport"],
        operation=entry["operation"],
        input_schema_ref=_schema_ref(entry["input_schema"]),
        output_schema_ref=_schema_ref(entry["output_schema"]),
    )
    tmp = ResolvedCapabilityMaterial(
        ref=CapabilityRef(id=desc.id, version=desc.version, content_digest=_DIGEST_PLACEHOLDER),
        descriptor=desc,
    )
    digest = catalog_material_digest(tmp)
    ref = CapabilityRef(id=desc.id, version=desc.version, content_digest=digest)
    return ref, ResolvedCapabilityMaterial(ref=ref, descriptor=desc)


def _build_pilot(
    spec: Mapping[str, Any], materials_dir: Path
) -> tuple[WorkerCatalogSnapshot, InMemoryMaterialSource]:
    """Assemble the pilot snapshot + its in-memory source from the YAML + files.

    Digests are computed from the physical material bytes (never pinned by hand
    per ref); the whole catalog is pinned once by :func:`load_pilot_catalog` via
    the sealed ``catalog_digest``.
    """
    materials = spec["materials"]
    content_entries: list[ContentManifestEntry] = []
    skill_entries: list[SkillManifest] = []
    cap_entries: list[CapabilityManifestEntry] = []
    text_materials: list[ResolvedTextMaterial] = []
    cap_materials: list[ResolvedCapabilityMaterial] = []
    text_bytes: dict[TextKey, bytes] = {}
    cap_descs: dict[CapabilityKey, CapabilityDescriptor] = {}
    ref_by_id: dict[str, ContentRef] = {}
    cap_ref_by_id: dict[str, CapabilityRef] = {}

    def _read(rel: str) -> bytes:
        return (materials_dir / rel).read_bytes()

    for item in materials.get("prompts", []):
        ref, mat = _text_material(id=item["id"], version=str(item["version"]),
                                  kind="prompt", raw=_read(item["file"]))
        content_entries.append(ContentManifestEntry(
            ref=ref, kind="prompt", name=item["name"],
            description=item["description"], source_identity=item["source_identity"]))
        text_materials.append(mat)
        text_bytes[(ref.id, ref.version)] = mat.raw_utf8
        ref_by_id[ref.id] = ref
    for item in materials.get("guardrails", []):
        ref, mat = _text_material(id=item["id"], version=str(item["version"]),
                                  kind="guardrail", raw=_read(item["file"]))
        content_entries.append(ContentManifestEntry(
            ref=ref, kind="guardrail", name=item["name"],
            description=item["description"], source_identity=item["source_identity"]))
        text_materials.append(mat)
        text_bytes[(ref.id, ref.version)] = mat.raw_utf8
        ref_by_id[ref.id] = ref
    for item in materials.get("skills", []):
        raw = _read(item["file"])
        ref, mat = _text_material(id=item["id"], version=str(item["version"]),
                                  kind="skill", raw=raw)
        parsed = parse_skill_v1(raw.decode("utf-8"))
        skill_entries.append(SkillManifest(
            ref=ref, name=parsed.name, summary=parsed.summary,
            perfect_for=parsed.perfect_for, not_ideal_for=parsed.not_ideal_for,
            critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
            source_identity=item["source_identity"]))
        text_materials.append(mat)
        text_bytes[(ref.id, ref.version)] = mat.raw_utf8
        ref_by_id[ref.id] = ref
    for item in materials.get("capabilities", []):
        ref, mat = _capability_material(item)
        cap_entries.append(CapabilityManifestEntry(
            ref=ref, capability_kind=mat.descriptor.capability_kind,
            transport=mat.descriptor.transport))
        cap_materials.append(mat)
        cap_descs[(ref.id, ref.version)] = mat.descriptor
        cap_ref_by_id[ref.id] = ref

    workers = tuple(_build_worker(w, ref_by_id, cap_ref_by_id) for w in spec["workers"])

    snapshot = build_catalog_snapshot(
        catalog_version=spec["catalog_version"],
        content_manifest=tuple(content_entries),
        skill_manifest=tuple(skill_entries),
        capability_manifest=tuple(cap_entries),
        workers=workers,
        resolved_material=tuple(text_materials) + tuple(cap_materials),
    )
    source = InMemoryMaterialSource(text=text_bytes, capabilities=cap_descs)
    return snapshot, source


def _build_worker(
    w: Mapping[str, Any],
    ref_by_id: Mapping[str, ContentRef],
    cap_ref_by_id: Mapping[str, CapabilityRef],
) -> WorkerSpec:
    tool_calls = ToolCallRequirement(w.get("tool_calls", "optional"))
    caps = tuple(sorted(
        (cap_ref_by_id[c] for c in w.get("capability_allowlist", [])),
        key=lambda r: (r.id, r.version),
    ))
    inputs = tuple(
        InputBinding(
            name=i["name"], schema_ref=_schema_ref(i["schema"]),
            required=i.get("required", True), cardinality=i.get("cardinality", "one"),
        )
        for i in w.get("inputs", [])
    )
    outputs = tuple(
        OutputBinding(name=o["name"], schema_ref=_schema_ref(o["schema"]))
        for o in w["outputs"]
    )
    modes = tuple(sorted((DataMode(m) for m in w["supported_modes"]), key=lambda m: m.value))
    return WorkerSpec(
        id=w["id"],
        catalog_role=w.get("catalog_role", "final"),
        selection_scope=w.get("selection_scope", "dynamic_allowed"),
        lane=w["lane"],
        persona=w["persona"],
        tier=Tier(w["tier"]),
        execution=ExecutionSpec(
            kind=ExecutionKind.LLM,
            model_tier=w["model_tier"],
            thinking_budget=w.get("thinking_budget", 0),
        ),
        system_prompt_ref=ref_by_id[w["system_prompt"]],
        skills=tuple(SkillBinding(skill_ref=ref_by_id[s]) for s in w.get("skills", [])),
        guardrail_refs=tuple(ref_by_id[g] for g in w.get("guardrails", [])),
        capability_allowlist=caps,
        read_categories=tuple(w.get("read_categories", [])),
        inputs=inputs,
        outputs=outputs,
        evidence_policy=EvidencePolicy(tool_calls=tool_calls),
        supported_modes=modes,
        can_emit_decision=w.get("can_emit_decision", False),
        decision_authority=w.get("decision_authority", "none"),
    )


def load_pilot_catalog(
    *,
    catalog_path: Path = PILOT_CATALOG_PATH,
    materials_dir: Path = PILOT_MATERIALS_DIR,
) -> CatalogRuntime:
    """Load the reviewed three-worker pilot catalog into a verified runtime.

    Reads the YAML definition + on-disk materials, rebuilds the snapshot through
    the Phase 1 builder, requires the sealed ``catalog_digest`` to equal the
    pinned attestation in the YAML (so any on-disk material drift fails startup),
    and returns a :class:`CatalogRuntime` re-verified against the same materials.
    """
    spec = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    snapshot, source = _build_pilot(spec, materials_dir)
    pinned = spec.get("catalog_digest")
    if pinned != snapshot.catalog_digest:
        raise CatalogMaterialError(
            "pilot catalog digest drift: pinned "
            f"{pinned!r} != built {snapshot.catalog_digest!r}"
        )
    return CatalogRuntime.build(snapshot, source)
