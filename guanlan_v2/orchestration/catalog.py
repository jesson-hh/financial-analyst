# -*- coding: utf-8 -*-
"""Task 9A — prompt / skill / capability refs and the runnable Worker catalog ABI.

This module freezes the *shape and validation rules* of a runnable worker
catalog. It is the identity contract between the design's 24-worker map and the
runtime that later resolves prompt/skill/guardrail/handler bytes and capability
descriptors. Phase 1 deliberately does **not** load files, write the 24
playbooks, sync skills, instantiate agents, call tools or populate all 24 final
workers — it only defines the models plus *pure* builder / validator functions.

Load-bearing invariants
------------------------
* **Refs are the only identity.** Prompt / skill / guardrail / handler refs are
  catalog-owned :class:`~guanlan_v2.orchestration.refs.ContentRef` values;
  capability refs are :class:`~guanlan_v2.orchestration.refs.CapabilityRef`;
  every schema type is a :class:`~guanlan_v2.orchestration.refs.SchemaRef`, never
  a ``"Model@1"`` string. A ``LogicalId`` can never be a physical file path (the
  ref grammar rejects ``/``, ``\\``, ``:``, ``@`` and uppercase). MCP transport
  is routing metadata on the capability manifest and grants no authorization.
* **skill-v1 grammar.** :func:`parse_skill_v1` enforces the exact frontmatter
  (only ``name`` + ``description``, no duplicate/unknown keys), the three
  LF-separated description lines (``summary`` / ``Perfect for: `` + Task-1
  canonical JSON / ``Not ideal for: `` + canonical JSON), and the mandatory
  ``## ⚠️ CRITICAL: Data Source Priority`` heading followed by a non-empty
  ordered source-priority list.
* **Material digests.** :func:`catalog_material_digest` hashes Task-1 canonical
  JSON with domain tag ``catalog-material-v1``. Text is decoded strict UTF-8
  without BOM, Unicode-NFC normalized, CRLF/CR→LF; every other code point is
  preserved. A capability's content is the descriptor's semantic projection.
* **Pure build / validate.** :func:`build_catalog_snapshot` computes those
  digests, requires exact one-to-one ref/material coverage, verifies each
  manifest entry against its material, sorts the set-like manifests/workers, and
  seals the ``catalog_digest`` — never loading a path.
  :func:`validate_catalog_snapshot` re-verifies declared digest equality and
  cross-references on an already-built snapshot (it has no bytes).
"""
from __future__ import annotations

import json
import unicodedata
from typing import Any, ClassVar, Literal, NamedTuple, Union

from pydantic import ConfigDict, model_validator

from guanlan_v2.orchestration.digest import (
    ContractModel,
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    canonical_json,
    content_digest,
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
    LogicalId,
    SchemaRef,
)

__all__ = [
    "CatalogError",
    "SkillFormatError",
    "ContentManifestEntry",
    "SkillManifest",
    "CapabilityDescriptor",
    "CapabilityManifestEntry",
    "ResolvedTextMaterial",
    "ResolvedCapabilityMaterial",
    "ResolvedMaterial",
    "SkillBinding",
    "InputBinding",
    "OutputBinding",
    "ExecutionSpec",
    "EvidencePolicy",
    "CompatibilityBinding",
    "WorkerSpec",
    "WorkerCatalogSnapshot",
    "ParsedSkillV1",
    "parse_skill_v1",
    "catalog_material_digest",
    "build_catalog_snapshot",
    "validate_catalog_snapshot",
    "count_final_workers",
]

_DIGEST_PLACEHOLDER = "0" * 64
_MATERIAL_DOMAIN = "catalog-material-v1"

#: text material kinds (a superset of content-manifest kinds; adds ``skill``).
TextMaterialKind = Literal[
    "prompt", "skill", "guardrail", "handler",
    "condition", "reducer", "stop_condition", "gate_metric",
]
#: content-manifest kinds (everything except ``skill``, which has its own manifest).
ContentKind = Literal[
    "prompt", "guardrail", "handler",
    "condition", "reducer", "stop_condition", "gate_metric",
]
CapabilityKind = Literal["tool", "mcp_method", "data_adapter"]
Transport = Literal["in_process", "mcp", "http"]
ModelTier = Literal["fast", "reasoner", "reasoner_deep"]
Lane = Literal["market", "quant", "pv", "text", "decision", "xcut"]
ReadCategory = Literal[
    "context", "upstream_artifacts", "market_data", "memory", "experience_cases",
]
CatalogRole = Literal["final", "compatibility"]
SelectionScope = Literal["dynamic_allowed", "static_legacy_only"]
DecisionAuthority = Literal["none", "advisory_only"]
Cardinality = Literal["one", "many"]

_CRITICAL_HEADING = "⚠️ CRITICAL: Data Source Priority"
_CRITICAL_HEADING_LINE = "## " + _CRITICAL_HEADING
_PERFECT_PREFIX = "Perfect for: "
_NOT_IDEAL_PREFIX = "Not ideal for: "


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #
class CatalogError(Exception):
    """A catalog build / validation invariant was violated."""


class SkillFormatError(CatalogError):
    """A skill-v1 document does not conform to the frozen grammar."""


# --------------------------------------------------------------------------- #
# Manifest entries + descriptor                                               #
# --------------------------------------------------------------------------- #
class ContentManifestEntry(DigestModel):
    """One reviewed text-material declaration (prompt/guardrail/handler/…).

    ``kind`` must equal the resolved material's kind; ``source_identity`` is a
    provenance :class:`LogicalId`, never a file path.
    """

    ref: ContentRef
    kind: ContentKind
    name: NonEmptyStr
    description: NonEmptyStr
    source_identity: LogicalId

    @property
    def ref_key(self) -> tuple[str, str]:
        return (self.ref.id, self.ref.version)


class SkillManifest(DigestModel):
    """The machine-readable envelope of a skill-v1 document.

    Phase 1 validates this envelope (summary + ordered trigger tuples + the
    mandatory data-source heading); it does not finalize the skill's body prose.
    """

    ref: ContentRef
    name: NonEmptyStr
    summary: NonEmptyStr
    format_version: Literal["skill-v1"] = "skill-v1"
    perfect_for: tuple[NonEmptyStr, ...]
    not_ideal_for: tuple[NonEmptyStr, ...]
    critical_data_source_heading: Literal["⚠️ CRITICAL: Data Source Priority"]
    source_identity: LogicalId

    @property
    def ref_key(self) -> tuple[str, str]:
        return (self.ref.id, self.ref.version)

    @model_validator(mode="after")
    def _verify(self) -> "SkillManifest":
        for label, vals in (("perfect_for", self.perfect_for),
                            ("not_ideal_for", self.not_ideal_for)):
            if not vals:
                raise ValueError(f"{label} trigger tuple must be non-empty")
            if len(set(vals)) != len(vals):
                raise ValueError(f"{label} trigger tuple must be duplicate-free")
        return self


class CapabilityDescriptor(DigestModel):
    """The sole hashed source of a capability's kind/transport/operation/schemas."""

    schema_version: Literal["1"] = "1"
    id: LogicalId
    version: NonEmptyStr
    capability_kind: CapabilityKind
    transport: Transport
    operation: LogicalId
    input_schema_ref: SchemaRef
    output_schema_ref: SchemaRef


class CapabilityManifestEntry(DigestModel):
    """A capability declaration; kind/transport are copied from the descriptor.

    Transport is routing metadata and never grants authorization.
    """

    ref: CapabilityRef
    capability_kind: CapabilityKind
    transport: Transport

    @property
    def ref_key(self) -> tuple[str, str]:
        return (self.ref.id, self.ref.version)


# --------------------------------------------------------------------------- #
# Resolved materials (build inputs; not stored in the snapshot)               #
# --------------------------------------------------------------------------- #
class _FrozenContract(ContractModel):
    """Strict, immutable base for build-input value objects."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResolvedTextMaterial(_FrozenContract):
    """Resolved text bytes for a :class:`ContentRef` (prompt/skill/guardrail/…)."""

    ref: ContentRef
    kind: TextMaterialKind
    raw_utf8: bytes

    @property
    def ref_key(self) -> tuple[str, str]:
        return (self.ref.id, self.ref.version)


class ResolvedCapabilityMaterial(_FrozenContract):
    """Resolved descriptor for a :class:`CapabilityRef`."""

    ref: CapabilityRef
    descriptor: CapabilityDescriptor

    @property
    def ref_key(self) -> tuple[str, str]:
        return (self.ref.id, self.ref.version)


ResolvedMaterial = Union[ResolvedTextMaterial, ResolvedCapabilityMaterial]


# --------------------------------------------------------------------------- #
# Worker bindings                                                             #
# --------------------------------------------------------------------------- #
class SkillBinding(DigestModel):
    """A mandatory, ordered skill binding; every v1 binding is required."""

    skill_ref: ContentRef
    required: Literal[True] = True


class InputBinding(DigestModel):
    """A named, dependency-injected runtime artifact input."""

    name: LogicalId
    schema_ref: SchemaRef
    required: bool = True
    cardinality: Cardinality = "one"


class OutputBinding(DigestModel):
    """A named published output; a worker declares exactly one named ``primary``."""

    name: LogicalId
    schema_ref: SchemaRef


class ExecutionSpec(DigestModel):
    """The worker's execution contract.

    ``DETERMINISTIC`` requires a ``handler_ref`` and forbids model tier / thinking
    budget. ``LLM`` requires a ``model_tier``, defaults ``thinking_budget`` to
    ``0`` (non-negative), and forbids an arbitrary handler ref.
    """

    kind: ExecutionKind
    handler_ref: ContentRef | None = None
    model_tier: ModelTier | None = None
    thinking_budget: NonNegativeInt | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_llm_budget(cls, data: Any) -> Any:
        if isinstance(data, dict):
            kind = data.get("kind")
            is_llm = kind is ExecutionKind.LLM or kind == "llm"
            if is_llm and data.get("thinking_budget") is None:
                data = dict(data)
                data["thinking_budget"] = 0
        return data

    @model_validator(mode="after")
    def _matrix(self) -> "ExecutionSpec":
        if self.kind is ExecutionKind.DETERMINISTIC:
            if self.handler_ref is None:
                raise ValueError("DETERMINISTIC execution requires a handler_ref")
            if self.model_tier is not None:
                raise ValueError("DETERMINISTIC execution forbids a model_tier")
            if self.thinking_budget is not None:
                raise ValueError("DETERMINISTIC execution forbids a thinking_budget")
        elif self.kind is ExecutionKind.LLM:
            if self.model_tier is None:
                raise ValueError("LLM execution requires a model_tier")
            if self.handler_ref is not None:
                raise ValueError("LLM execution forbids an arbitrary handler_ref")
            if self.thinking_budget is None:
                raise ValueError("LLM execution requires a non-negative thinking_budget")
        return self


class EvidencePolicy(DigestModel):
    """The worker's cross-cutting evidence discipline."""

    tool_calls: ToolCallRequirement = ToolCallRequirement.OPTIONAL
    require_input_refs: bool = True
    require_number_anchors: bool = True
    allow_unsourced_numbers: bool = False
    optional_data_may_degrade: bool = True


class CompatibilityBinding(DigestModel):
    """Legacy-preservation binding for a ``compat.*`` worker."""

    legacy_source_schema: SchemaRef
    source_config_digest: DigestHex
    legacy_mapping_digest: DigestHex


class WorkerSpec(DigestModel):
    """A complete, runnable worker declaration (v1).

    Freezes stable identity, lane/persona/tier, the execution contract, an
    optional prompt ref, ordered skill/guardrail bindings, an exact capability
    allowlist, read categories, a separate params schema, named runtime
    input/output bindings, the :class:`EvidencePolicy`, supported modes, decision
    authority and provenance labels.
    """

    schema_version: Literal["1"] = "1"
    id: LogicalId
    catalog_role: CatalogRole
    selection_scope: SelectionScope
    compatibility: CompatibilityBinding | None = None
    lane: Lane
    persona: NonEmptyStr
    tier: Tier
    execution: ExecutionSpec
    system_prompt_ref: ContentRef | None = None
    skills: tuple[SkillBinding, ...] = ()
    guardrail_refs: tuple[ContentRef, ...] = ()
    capability_allowlist: tuple[CapabilityRef, ...] = ()
    read_categories: tuple[ReadCategory, ...] = ()
    params_schema_ref: SchemaRef | None = None
    inputs: tuple[InputBinding, ...] = ()
    outputs: tuple[OutputBinding, ...]
    evidence_policy: EvidencePolicy = EvidencePolicy()
    supported_modes: tuple[DataMode, ...]
    can_emit_decision: bool = False
    decision_authority: DecisionAuthority
    borrowed_from: tuple[LogicalId, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "WorkerSpec":
        # -- execution / prompt matrix ------------------------------------- #
        if self.execution.kind is ExecutionKind.LLM and self.system_prompt_ref is None:
            raise ValueError("an LLM worker requires a system_prompt_ref")

        # -- tool-call requirement vs allowlist ---------------------------- #
        tc = self.evidence_policy.tool_calls
        if tc is ToolCallRequirement.FORBIDDEN and self.capability_allowlist:
            raise ValueError("tool_calls=FORBIDDEN requires an empty capability_allowlist")
        if tc is ToolCallRequirement.REQUIRED and not self.capability_allowlist:
            raise ValueError("tool_calls=REQUIRED requires a non-empty capability_allowlist")

        # -- decision authority -------------------------------------------- #
        if self.can_emit_decision and self.decision_authority != "advisory_only":
            raise ValueError(
                "can_emit_decision=True requires decision_authority='advisory_only'"
            )
        if not self.can_emit_decision and self.decision_authority != "none":
            raise ValueError("can_emit_decision=False requires decision_authority='none'")

        # -- catalog role / scope / id / compatibility matrix -------------- #
        if self.catalog_role == "final":
            if self.selection_scope != "dynamic_allowed":
                raise ValueError("final worker requires selection_scope='dynamic_allowed'")
            if self.compatibility is not None:
                raise ValueError("final worker must not carry a CompatibilityBinding")
            if self.id.startswith("compat."):
                raise ValueError("final worker id must not use the compat.* prefix")
        else:  # compatibility
            if self.selection_scope != "static_legacy_only":
                raise ValueError(
                    "compatibility worker requires selection_scope='static_legacy_only'"
                )
            if self.compatibility is None:
                raise ValueError("compatibility worker requires a CompatibilityBinding")
            if not self.id.startswith("compat."):
                raise ValueError("compatibility worker id must use the compat.* prefix")

        # -- outputs: exactly one primary, unique names -------------------- #
        out_names = [o.name for o in self.outputs]
        if len(set(out_names)) != len(out_names):
            raise ValueError("output binding names must be unique")
        if out_names.count("primary") != 1:
            raise ValueError("a worker requires exactly one output named 'primary'")

        # -- inputs: unique names ------------------------------------------ #
        in_names = [i.name for i in self.inputs]
        if len(set(in_names)) != len(in_names):
            raise ValueError("input binding names must be unique")

        # -- supported_modes: non-empty, unique, sorted -------------------- #
        modes = [m.value for m in self.supported_modes]
        if not modes:
            raise ValueError("supported_modes must be non-empty")
        if len(set(modes)) != len(modes):
            raise ValueError("supported_modes must be unique")
        if modes != sorted(modes):
            raise ValueError("supported_modes must be canonically sorted")

        # -- capability_allowlist: unique + canonically sorted ------------- #
        cap_keys = [(c.id, c.version) for c in self.capability_allowlist]
        if len(set(cap_keys)) != len(cap_keys):
            raise ValueError("capability_allowlist must be unique")
        if cap_keys != sorted(cap_keys):
            raise ValueError("capability_allowlist must be canonically sorted")

        # -- ordered but unique bindings ----------------------------------- #
        g_keys = [(g.id, g.version) for g in self.guardrail_refs]
        if len(set(g_keys)) != len(g_keys):
            raise ValueError("guardrail_refs must be unique")
        s_keys = [(s.skill_ref.id, s.skill_ref.version) for s in self.skills]
        if len(set(s_keys)) != len(s_keys):
            raise ValueError("skill bindings must be unique")
        if len(set(self.read_categories)) != len(self.read_categories):
            raise ValueError("read_categories must be unique")

        # -- borrowed_from: unique + sorted -------------------------------- #
        bf = list(self.borrowed_from)
        if len(set(bf)) != len(bf):
            raise ValueError("borrowed_from must be unique")
        if bf != sorted(bf):
            raise ValueError("borrowed_from must be sorted")
        return self


# --------------------------------------------------------------------------- #
# skill-v1 grammar parser                                                     #
# --------------------------------------------------------------------------- #
class ParsedSkillV1(NamedTuple):
    """The parsed machine-readable envelope of a skill-v1 document."""

    name: str
    summary: str
    perfect_for: tuple[str, ...]
    not_ideal_for: tuple[str, ...]
    critical_data_source_heading: str
    source_priority: tuple[str, ...]


def _normalize_text(text: str) -> str:
    """NFC-normalize and fold CRLF/CR to LF (idempotent on already-clean text)."""
    text = unicodedata.normalize("NFC", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode_material_text(raw: bytes) -> str:
    """Strict UTF-8 (no BOM) decode + NFC + CRLF/CR→LF; other code points kept."""
    if raw[:3] == b"\xef\xbb\xbf":
        raise ValueError("catalog text material must not carry a UTF-8 BOM")
    return _normalize_text(raw.decode("utf-8"))


def _parse_trigger_line(line: str, prefix: str) -> tuple[str, ...]:
    if not line.startswith(prefix):
        raise SkillFormatError(f"skill-v1 description line must start with {prefix!r}")
    raw = line[len(prefix):]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillFormatError(f"skill-v1 trigger list is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list) or not parsed:
        raise SkillFormatError("skill-v1 trigger list must be a non-empty JSON array")
    if not all(isinstance(x, str) and x.strip() for x in parsed):
        raise SkillFormatError("skill-v1 trigger list items must be non-empty strings")
    if canonical_json(parsed) != raw:
        raise SkillFormatError(
            "skill-v1 trigger list must be Task-1 canonical JSON (exact escaping/order)"
        )
    if len(set(parsed)) != len(parsed):
        raise SkillFormatError("skill-v1 trigger list must be duplicate-free")
    return tuple(parsed)


def parse_skill_v1(text: str) -> ParsedSkillV1:
    """Parse + validate a skill-v1 document envelope (grammar frozen in Phase 1)."""
    lines = _normalize_text(text).split("\n")
    if not lines or lines[0] != "---":
        raise SkillFormatError("skill-v1 must begin with a '---' frontmatter fence")
    try:
        close = lines.index("---", 1)
    except ValueError as exc:
        raise SkillFormatError(
            "skill-v1 frontmatter must be closed by a '---' fence"
        ) from exc
    frontmatter = lines[1:close]
    body = lines[close + 1:]

    name: str | None = None
    desc: list[str] | None = None
    seen: set[str] = set()
    i = 0
    while i < len(frontmatter):
        entry = frontmatter[i]
        if entry == "":
            raise SkillFormatError("skill-v1 frontmatter must not contain blank lines")
        if entry[0] in " \t":
            raise SkillFormatError("unexpected indented line in skill-v1 frontmatter")
        key, sep, rest = entry.partition(":")
        if sep == "":
            raise SkillFormatError("skill-v1 frontmatter line must be 'key: value'")
        if key in seen:
            raise SkillFormatError(f"duplicate skill-v1 frontmatter key {key!r}")
        if key not in ("name", "description"):
            raise SkillFormatError(f"unknown skill-v1 frontmatter key {key!r}")
        seen.add(key)
        if key == "name":
            name = rest.strip()
            if not name:
                raise SkillFormatError("skill-v1 'name' must be non-empty")
            i += 1
        else:  # description literal block scalar
            if rest.strip() != "|":
                raise SkillFormatError(
                    "skill-v1 'description' must be a literal block scalar '|'"
                )
            i += 1
            block: list[str] = []
            while i < len(frontmatter) and frontmatter[i].startswith("  "):
                block.append(frontmatter[i][2:])
                i += 1
            desc = block

    if seen != {"name", "description"}:
        raise SkillFormatError(
            "skill-v1 frontmatter must have exactly the keys 'name' and 'description'"
        )
    assert name is not None
    if desc is None or len(desc) != 3:
        raise SkillFormatError("skill-v1 description must have exactly three lines")
    summary, line2, line3 = desc
    if not summary.strip():
        raise SkillFormatError("skill-v1 summary (line 1) must be non-empty")
    perfect_for = _parse_trigger_line(line2, _PERFECT_PREFIX)
    not_ideal_for = _parse_trigger_line(line3, _NOT_IDEAL_PREFIX)

    # -- body: first heading must be the critical data-source priority ----- #
    j = 0
    while j < len(body) and body[j] == "":
        j += 1
    if j >= len(body) or body[j] != _CRITICAL_HEADING_LINE:
        raise SkillFormatError(
            f"skill-v1 first body heading must be {_CRITICAL_HEADING_LINE!r}"
        )
    k = j + 1
    items: list[str] = []
    while k < len(body) and body[k].startswith("- "):
        item = body[k][2:].strip()
        if not item:
            raise SkillFormatError("skill-v1 source-priority list item must be non-empty")
        items.append(item)
        k += 1
    if not items:
        raise SkillFormatError(
            "skill-v1 critical heading must be followed by a non-empty ordered list"
        )
    return ParsedSkillV1(
        name=name,
        summary=summary,
        perfect_for=perfect_for,
        not_ideal_for=not_ideal_for,
        critical_data_source_heading=_CRITICAL_HEADING,
        source_priority=tuple(items),
    )


# --------------------------------------------------------------------------- #
# Material digest                                                             #
# --------------------------------------------------------------------------- #
def catalog_material_digest(material: ResolvedMaterial) -> DigestHex:
    """Task-1 canonical digest of a resolved material (domain ``catalog-material-v1``).

    Text content is strict-UTF-8-no-BOM decoded, NFC-normalized and CRLF/CR→LF
    folded — every other code point (trailing newline/space included) survives.
    Capability content is the descriptor's semantic projection.
    """
    if isinstance(material, ResolvedTextMaterial):
        payload: dict[str, Any] = {
            "domain": _MATERIAL_DOMAIN,
            "material_kind": material.kind,
            "content": _decode_material_text(material.raw_utf8),
        }
    elif isinstance(material, ResolvedCapabilityMaterial):
        d = material.descriptor
        payload = {
            "domain": _MATERIAL_DOMAIN,
            "material_kind": "capability",
            "content": {
                "id": d.id,
                "version": d.version,
                "capability_kind": d.capability_kind,
                "transport": d.transport,
                "operation": d.operation,
                "input_schema_ref": d.input_schema_ref,
                "output_schema_ref": d.output_schema_ref,
            },
        }
    else:  # pragma: no cover - defensive
        raise TypeError(f"unsupported material type: {type(material).__name__}")
    return content_digest(payload)


# --------------------------------------------------------------------------- #
# Snapshot                                                                    #
# --------------------------------------------------------------------------- #
class WorkerCatalogSnapshot(DigestModel):
    """A reviewed, complete, verified runnable worker catalog.

    Holds declarations only (no prompt/skill bytes). The set-like manifests and
    workers are canonically sorted and unique, every worker ref resolves into a
    manifest with a matching declared digest, and ``catalog_digest`` seals the
    whole snapshot (registration-order independent; changes when any bound
    prompt/skill/guardrail/handler/capability/WorkerSpec changes).
    """

    schema_version: Literal["1"] = "1"
    catalog_version: NonEmptyStr
    content_manifest: tuple[ContentManifestEntry, ...] = ()
    skill_manifest: tuple[SkillManifest, ...] = ()
    capability_manifest: tuple[CapabilityManifestEntry, ...] = ()
    workers: tuple[WorkerSpec, ...] = ()
    catalog_digest: DigestHex

    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"catalog_digest"})

    @model_validator(mode="after")
    def _verify(self) -> "WorkerCatalogSnapshot":
        errors = _snapshot_errors(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


def _sorted_unique_errors(entries, keyfn, label: str) -> list[str]:
    keys = [keyfn(e) for e in entries]
    errs: list[str] = []
    if len(set(keys)) != len(keys):
        errs.append(f"{label} contains duplicate refs")
    if keys != sorted(keys):
        errs.append(f"{label} must be canonically sorted")
    return errs


def _snapshot_errors(snap: "WorkerCatalogSnapshot") -> list[str]:
    """Return every structural / cross-reference / digest error (empty ⇒ valid)."""
    errors: list[str] = []
    errors += _sorted_unique_errors(
        snap.content_manifest, lambda e: e.ref_key, "content_manifest")
    errors += _sorted_unique_errors(
        snap.skill_manifest, lambda e: e.ref_key, "skill_manifest")
    errors += _sorted_unique_errors(
        snap.capability_manifest, lambda e: e.ref_key, "capability_manifest")
    errors += _sorted_unique_errors(snap.workers, lambda w: w.id, "workers")

    content_by_key: dict[tuple[str, str], ContentManifestEntry] = {
        e.ref_key: e for e in snap.content_manifest
    }
    skill_by_key: dict[tuple[str, str], SkillManifest] = {
        e.ref_key: e for e in snap.skill_manifest
    }
    cap_by_key: dict[tuple[str, str], CapabilityManifestEntry] = {
        e.ref_key: e for e in snap.capability_manifest
    }

    def resolve_content(ref: ContentRef, expected_kind: str, ctx: str) -> None:
        entry = content_by_key.get((ref.id, ref.version))
        if entry is None:
            errors.append(f"{ctx}: unknown content ref {ref.id}@{ref.version}")
        elif entry.kind != expected_kind:
            errors.append(
                f"{ctx}: ref {ref.id}@{ref.version} is kind={entry.kind!r}, "
                f"expected {expected_kind!r}"
            )
        elif entry.ref.content_digest != ref.content_digest:
            errors.append(f"{ctx}: declared digest mismatch for {ref.id}@{ref.version}")

    for w in snap.workers:
        if w.system_prompt_ref is not None:
            resolve_content(w.system_prompt_ref, "prompt", f"worker {w.id} prompt")
        if w.execution.handler_ref is not None:
            resolve_content(w.execution.handler_ref, "handler", f"worker {w.id} handler")
        for g in w.guardrail_refs:
            resolve_content(g, "guardrail", f"worker {w.id} guardrail")
        for sb in w.skills:
            entry_s = skill_by_key.get((sb.skill_ref.id, sb.skill_ref.version))
            if entry_s is None:
                errors.append(
                    f"worker {w.id} skill: unknown skill ref "
                    f"{sb.skill_ref.id}@{sb.skill_ref.version}"
                )
            elif entry_s.ref.content_digest != sb.skill_ref.content_digest:
                errors.append(
                    f"worker {w.id} skill: declared digest mismatch for "
                    f"{sb.skill_ref.id}@{sb.skill_ref.version}"
                )
        for c in w.capability_allowlist:
            entry_c = cap_by_key.get((c.id, c.version))
            if entry_c is None:
                errors.append(
                    f"worker {w.id} capability: unknown ref {c.id}@{c.version}"
                )
            elif entry_c.ref.content_digest != c.content_digest:
                errors.append(
                    f"worker {w.id} capability: declared digest mismatch for "
                    f"{c.id}@{c.version}"
                )

    if snap.catalog_digest != snap.semantic_digest():
        errors.append("declared catalog_digest does not match canonical semantic digest")
    return errors


def _verify_text_material(entry_ref: ContentRef, material: ResolvedTextMaterial) -> None:
    recomputed = catalog_material_digest(material)
    if recomputed != entry_ref.content_digest:
        raise CatalogError(
            f"declared content_digest mismatch for {entry_ref.id}@{entry_ref.version}"
        )


def _verify_capability_material(
    entry_ref: CapabilityRef, material: ResolvedCapabilityMaterial
) -> None:
    recomputed = catalog_material_digest(material)
    if recomputed != entry_ref.content_digest:
        raise CatalogError(
            f"declared content_digest mismatch for {entry_ref.id}@{entry_ref.version}"
        )


def build_catalog_snapshot(
    *,
    catalog_version: str,
    content_manifest,
    skill_manifest,
    capability_manifest,
    workers,
    resolved_material,
) -> WorkerCatalogSnapshot:
    """Pure, material-aware builder that seals a runnable :class:`WorkerCatalogSnapshot`.

    Requires exact one-to-one ref/material coverage, verifies each manifest entry
    against its resolved material (declared digest + kind/transport + skill-v1
    parse), sorts the set-like manifests/workers and computes/verifies
    ``catalog_digest``. It never loads a path.
    """
    content_manifest = tuple(content_manifest)
    skill_manifest = tuple(skill_manifest)
    capability_manifest = tuple(capability_manifest)
    workers = tuple(workers)

    # -- partition materials (reject duplicates) --------------------------- #
    text_mats: dict[tuple[str, str], ResolvedTextMaterial] = {}
    cap_mats: dict[tuple[str, str], ResolvedCapabilityMaterial] = {}
    for m in resolved_material:
        key = m.ref_key
        if key in text_mats or key in cap_mats:
            raise CatalogError(f"duplicate resolved material for {key[0]}@{key[1]}")
        if isinstance(m, ResolvedTextMaterial):
            text_mats[key] = m
        elif isinstance(m, ResolvedCapabilityMaterial):
            cap_mats[key] = m
        else:  # pragma: no cover - defensive
            raise CatalogError(f"unsupported material type: {type(m).__name__}")

    covered_text: set[tuple[str, str]] = set()

    # -- content manifest -------------------------------------------------- #
    for entry in content_manifest:
        key = entry.ref_key
        material = text_mats.get(key)
        if material is None:
            raise CatalogError(f"no resolved material for content ref {key[0]}@{key[1]}")
        if material.kind != entry.kind:
            raise CatalogError(
                f"content manifest kind {entry.kind!r} != material kind "
                f"{material.kind!r} for {key[0]}@{key[1]}"
            )
        _verify_text_material(entry.ref, material)
        covered_text.add(key)

    # -- skill manifest ---------------------------------------------------- #
    for entry in skill_manifest:
        key = entry.ref_key
        material = text_mats.get(key)
        if material is None:
            raise CatalogError(f"no resolved material for skill ref {key[0]}@{key[1]}")
        if material.kind != "skill":
            raise CatalogError(
                f"skill manifest ref {key[0]}@{key[1]} points at material kind "
                f"{material.kind!r}, expected 'skill'"
            )
        _verify_text_material(entry.ref, material)
        parsed = parse_skill_v1(_decode_material_text(material.raw_utf8))
        _check_skill_manifest_matches(entry, parsed)
        covered_text.add(key)

    if covered_text != set(text_mats):
        orphans = sorted(set(text_mats) - covered_text)
        raise CatalogError(
            "orphan text material(s) with no manifest entry: "
            + ", ".join(f"{i}@{v}" for i, v in orphans)
        )

    # -- capability manifest ----------------------------------------------- #
    covered_cap: set[tuple[str, str]] = set()
    for entry in capability_manifest:
        key = entry.ref_key
        material = cap_mats.get(key)
        if material is None:
            raise CatalogError(f"no resolved material for capability ref {key[0]}@{key[1]}")
        if entry.capability_kind != material.descriptor.capability_kind:
            raise CatalogError(
                f"capability manifest kind {entry.capability_kind!r} != descriptor kind "
                f"{material.descriptor.capability_kind!r} for {key[0]}@{key[1]}"
            )
        if entry.transport != material.descriptor.transport:
            raise CatalogError(
                f"capability manifest transport {entry.transport!r} != descriptor "
                f"transport {material.descriptor.transport!r} for {key[0]}@{key[1]}"
            )
        _verify_capability_material(entry.ref, material)
        covered_cap.add(key)

    if covered_cap != set(cap_mats):
        orphans = sorted(set(cap_mats) - covered_cap)
        raise CatalogError(
            "orphan capability material(s) with no manifest entry: "
            + ", ".join(f"{i}@{v}" for i, v in orphans)
        )

    # -- sort set-like collections ----------------------------------------- #
    content_sorted = tuple(sorted(content_manifest, key=lambda e: e.ref_key))
    skill_sorted = tuple(sorted(skill_manifest, key=lambda e: e.ref_key))
    cap_sorted = tuple(sorted(capability_manifest, key=lambda e: e.ref_key))
    workers_sorted = tuple(sorted(workers, key=lambda w: w.id))

    fields: dict[str, Any] = dict(
        catalog_version=catalog_version,
        content_manifest=content_sorted,
        skill_manifest=skill_sorted,
        capability_manifest=cap_sorted,
        workers=workers_sorted,
    )
    try:
        digest = WorkerCatalogSnapshot.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = _DIGEST_PLACEHOLDER

    try:
        return WorkerCatalogSnapshot(**fields, catalog_digest=digest)
    except Exception as exc:  # surface structural failures as CatalogError
        raise CatalogError(f"catalog snapshot is not runnable: {exc}") from exc


def _check_skill_manifest_matches(entry: SkillManifest, parsed: ParsedSkillV1) -> None:
    if entry.name != parsed.name:
        raise CatalogError(f"skill manifest name != document for {entry.ref.id}")
    if entry.summary != parsed.summary:
        raise CatalogError(f"skill manifest summary != document for {entry.ref.id}")
    if tuple(entry.perfect_for) != parsed.perfect_for:
        raise CatalogError(f"skill manifest perfect_for != document for {entry.ref.id}")
    if tuple(entry.not_ideal_for) != parsed.not_ideal_for:
        raise CatalogError(f"skill manifest not_ideal_for != document for {entry.ref.id}")
    if entry.critical_data_source_heading != parsed.critical_data_source_heading:
        raise CatalogError(
            f"skill manifest critical heading != document for {entry.ref.id}"
        )


def validate_catalog_snapshot(snapshot: WorkerCatalogSnapshot) -> None:
    """Verify declared digest equality + cross-references on a built snapshot.

    This is byteless: it does not re-hash prompt/skill material (that happens in
    :func:`build_catalog_snapshot`); it only confirms the snapshot's declared
    ``catalog_digest`` and that every worker ref resolves into a manifest with a
    matching declared digest. Raises :class:`CatalogError` on any violation.
    """
    errors = _snapshot_errors(snapshot)
    if errors:
        raise CatalogError("; ".join(errors))


def count_final_workers(source) -> int:
    """Count ``catalog_role=="final"`` workers (``compat.*`` mirrors excluded)."""
    workers = source.workers if isinstance(source, WorkerCatalogSnapshot) else tuple(source)
    return sum(1 for w in workers if w.catalog_role == "final")
