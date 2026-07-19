# -*- coding: utf-8 -*-
"""Phase 7 · Task 9 — the cumulative registry/catalog chain + planner materials.

Phase 7 turns the static-runtime kernel (Phases 1-6) into a *dynamic* Planner.
This module owns the reviewed *classification + chain* half of that phase, built
as a pure ADDITION over the sealed Phase-6 chain (never a fork, never a "latest"
alias):

* :data:`PHASE7_PUBLIC_MODELS` — the eight dynamic-Planner payloads the Phase-7
  cumulative registry resolves a ``SchemaRef`` to;
* :func:`build_phase7_registry` — a *fresh sealed* cumulative registry over the
  exact Phase-6 cumulative set + :data:`PHASE7_PUBLIC_MODELS`; the supplied base
  digest is verified against :data:`PHASE6_REGISTRY_DIGEST` *before any
  registration*; inherited JSON Schemas stay byte-identical to their Phase-<=6
  goldens; :data:`PHASE7_REGISTRY_DIGEST` is the frozen, order-independent digest;
* :func:`build_phase7_catalog_snapshot` — extends the immutable Phase-6 catalog
  with ONLY the catalog-owned planner content/skill/guardrail materials and
  **zero WorkerSpec**. The Planner is deliberately NOT a selectable worker: a
  ``final`` spec would be ``dynamic_allowed`` and could recursively select itself,
  so the reviewed containment is materials-without-worker. ``count_final_workers``
  is therefore unchanged from Phase 6;
* :func:`build_phase7_planner_spec` — derives a :class:`PlannerSpec` from the
  Phase-7 catalog manifests by exact id/version/digest (never a path);
  ``model_tier="reasoner_deep"`` is a reviewed deviation from spec §10 (which
  reserves ``reasoner_deep`` for the ``dec.pm`` seat): the Planner is not a lane
  worker and plan-shaping quality dominates its once-per-run cost. Its semantic
  digest is frozen as ``planner_spec_digest`` in the catalog golden.

The Phase-7 public/internal partition over the five Phase-7 contract modules
(``orchestrator`` / ``plan_presets`` / ``plan_diff`` / ``approval`` /
``planner_gateway``) is exhaustive and disjoint; the completeness firewall lives
in ``tests/orchestration/test_phase7_registry.py``. This module adds no
``EventType`` member and touches no Phase-1 absence/deferred-payload guard —
nothing pins a Phase-7 model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from guanlan_v2.orchestration.approval import (
    ApprovalJournalRow,
    ApprovalLease,
    LeaseRevocation,
)
from guanlan_v2.orchestration.catalog import (
    CatalogError,
    ContentManifestEntry,
    ResolvedMaterial,
    ResolvedTextMaterial,
    SkillBinding,
    SkillManifest,
    WorkerCatalogSnapshot,
    catalog_material_digest,
    parse_skill_v1,
    validate_catalog_snapshot,
)
from guanlan_v2.orchestration.catalog_runtime import build_text_material
from guanlan_v2.orchestration.digest import DigestHex, DigestModel
from guanlan_v2.orchestration.orchestrator import (
    PlannerAttemptRecord,
    PlannerDependencyEnvelope,
    PlannerDraftEnvelope,
    PlannerNodeEnvelope,
    PlannerRunRecord,
    PlannerSpec,
)
from guanlan_v2.orchestration.plan_diff import (
    PendingPlanApproval,
    PlanDiff,
    PlanDiffEntry,
)
from guanlan_v2.orchestration.plan_presets import (
    PlanPresetManifestEntry,
    PlanPresetRecord,
)

__all__ = [
    # registry
    "PHASE7_PUBLIC_MODELS",
    "Phase7RegistryError",
    "build_phase7_registry",
    # catalog + planner materials
    "PLANNER_SOURCE_IDENTITY",
    "PLANNER_MATERIALS_DIR",
    "PHASE7_CATALOG_VERSION",
    "load_planner_material_bytes",
    "load_planner_materials",
    "build_phase7_catalog_snapshot",
    "phase7_catalog_snapshot",
    # planner spec
    "PLANNER_ID",
    "PLANNER_VERSION",
    "build_phase7_planner_spec",
    "phase7_planner_spec",
    # lazy (PEP 562) constants
    "PHASE7_INTERNAL_MODELS",  # noqa: F822 — lazy via module __getattr__
    "PHASE7_BASE_REGISTRY_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE7_REGISTRY_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE7_BASE_CATALOG_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE7_CATALOG_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE7_PLANNER_SPEC_DIGEST",  # noqa: F822 — lazy via module __getattr__
]


# =========================================================================== #
# The reviewed Phase-7 public / internal contract partition                     #
# =========================================================================== #
#: the exactly-eight registered Phase-7 payload contracts the Phase-7 cumulative
#: registry resolves a :class:`SchemaRef` to. Ordered by owning module (planner
#: identity/records, then the fallback-preset record, the typed diff pair + review
#: card, then the standing-approval lease).
PHASE7_PUBLIC_MODELS: tuple[type[DigestModel], ...] = (
    PlannerSpec,
    PlannerAttemptRecord,
    PlannerRunRecord,
    PlanPresetRecord,
    PlanDiffEntry,
    PlanDiff,
    PendingPlanApproval,
    ApprovalLease,
)

_R7_PLANNER_ENVELOPE = (
    "internal closed model-authored proposal envelope (frozen ContractModel, "
    "no independent schema_version); an authority-free draft carrier lifted into a "
    "real Phase-1 PlanDraft, never a registered cross-boundary payload"
)
_R7_PRESET_MANIFEST = (
    "internal preset-registry manifest entry (registry-internal structure); "
    "carries a referenced PlanPresetRecord's digest for the order-independent "
    "PlanPresetRegistry digest, never itself a registered payload"
)
_R7_JOURNAL_ROW = (
    "internal append-only approval-journal storage row (self-verifying by "
    "row_digest over row_kind/seq/payload); an on-disk durability record, never a "
    "registered cross-boundary payload"
)
_R7_LEASE_RECOVERY = (
    "internal lease-revocation record (a lease_revoked journal-row payload); a "
    "durable recovery/value carrier, never a registered cross-boundary payload"
)


def _phase7_internal_models() -> "dict[type[DigestModel], str]":
    """The reviewed ``ContractModel -> reason`` map of every Phase-7 public
    contract deliberately NOT registered.

    :data:`PHASE7_PUBLIC_MODELS` ∪ this map partitions every public
    ``ContractModel`` defined across the five Phase-7 contract modules exactly
    (proved by ``tests/orchestration/test_phase7_registry.py``). Note the value
    carriers the runtime returns — ``PlannerResult`` (a ``NamedTuple``),
    ``LeaseAdmissionOutcome`` / ``LeaseBalanceView`` (frozen dataclasses) — are not
    ``ContractModel`` subclasses at all, so they never enter this contract-model
    partition.
    """
    return {
        # orchestrator.py — the closed model-authored draft envelope + sub-envelopes
        PlannerDraftEnvelope: _R7_PLANNER_ENVELOPE,
        PlannerNodeEnvelope: _R7_PLANNER_ENVELOPE,
        PlannerDependencyEnvelope: _R7_PLANNER_ENVELOPE,
        # plan_presets.py — the preset-registry manifest entry
        PlanPresetManifestEntry: _R7_PRESET_MANIFEST,
        # approval.py — the durable journal row + the lease-revocation record
        ApprovalJournalRow: _R7_JOURNAL_ROW,
        LeaseRevocation: _R7_LEASE_RECOVERY,
    }


# =========================================================================== #
# The cumulative Phase-7 schema registry (linear chain over Phase 6)            #
# =========================================================================== #
class Phase7RegistryError(Exception):
    """A Phase-7 cumulative-registry construction invariant was violated."""


def _phase6_registry_digest() -> str:
    from guanlan_v2.orchestration import shadow

    return shadow.PHASE6_REGISTRY_DIGEST


def _phase6_catalog_digest() -> str:
    from guanlan_v2.orchestration import shadow

    return shadow.PHASE6_CATALOG_DIGEST


def build_phase7_registry(expected_phase6_digest: "DigestHex"):
    """Build + seal the cumulative Phase-7 registry, pinned to the Phase-6 base.

    Verifies ``expected_phase6_digest`` against the actual sealed Phase-6 cumulative
    registry digest first — any other base digest is rejected *before any
    registration* — then registers the complete inherited cumulative set (Phase-1
    public + Phase-2 runtime facts + Phase-3 data + Phase-3 memory + Phase-4
    Evaluator-Optimizer + Phase-5 Bootstrap Lane 0 +
    :data:`~guanlan_v2.orchestration.shadow.PHASE6_PUBLIC_MODELS`) followed by
    :data:`PHASE7_PUBLIC_MODELS` into a *fresh*
    :class:`~guanlan_v2.orchestration.runtime_contracts.Phase2RuntimeRegistry`
    and seals it. No upstream registry is mutated; a fresh sealed instance is
    returned per call. Inherited JSON Schemas stay byte-identical to their
    Phase-<=6 goldens; there is no "latest" alias.
    """
    from guanlan_v2.orchestration import bootstrap, shadow, trial
    from guanlan_v2.orchestration.data.schema_registry import (
        PHASE3_PUBLIC_MODELS as _DATA_PUBLIC,
    )
    from guanlan_v2.orchestration.memory.schema_registry import (
        PHASE3_MEMORY_PUBLIC_MODELS as _MEM_PUBLIC,
    )
    from guanlan_v2.orchestration.runtime_contracts import (
        Phase2RuntimeRegistry,
        phase2_public_models,
    )

    actual = _phase6_registry_digest()
    if expected_phase6_digest != actual:
        raise Phase7RegistryError(
            "build_phase7_registry requires the exact Phase-6 registry digest "
            f"{actual!r}; got {expected_phase6_digest!r}"
        )
    reg = Phase2RuntimeRegistry()
    for model in (
        tuple(phase2_public_models())
        + tuple(_DATA_PUBLIC)
        + tuple(_MEM_PUBLIC)
        + tuple(trial.PHASE4_PUBLIC_MODELS)
        + tuple(bootstrap.PHASE5_PUBLIC_MODELS)
        + tuple(shadow.PHASE6_PUBLIC_MODELS)
        + PHASE7_PUBLIC_MODELS
    ):
        reg.register(model)
    reg.seal()
    return reg


# =========================================================================== #
# The Phase-7 catalog: catalog-owned planner materials, ZERO new workers        #
# =========================================================================== #
#: the physical planner-material inventory root (paths never enter a contract —
#: this Path is a local input to the loader, exactly like the Lane-0 precedent).
PLANNER_MATERIALS_DIR = (
    Path(__file__).resolve().parents[2]
    / "config" / "orchestration" / "materials" / "planner"
)

#: the reviewed provenance ``LogicalId`` stamped on every planner manifest entry.
PLANNER_SOURCE_IDENTITY = "orchestrator.planner"

#: the merged cumulative Phase-7 catalog version.
PHASE7_CATALOG_VERSION = "phase7-full-v1"

#: (content_id, material kind, filename, reviewed description). The three
#: catalog-owned planner materials — a system prompt, a skill-v1 SKILL.md, and a
#: guardrail — that the Planner (never a catalog worker) is bound to via
#: :func:`build_phase7_planner_spec`. The ids are ``LogicalId`` values; the
#: filenames are local loader inputs only.
_PLANNER_FILES: tuple[tuple[str, str, str, str], ...] = (
    (
        "orchestrator.planner.prompt",
        "prompt",
        "system_prompt.md",
        "orchestrator.planner system prompt (advisory plan-shaper, closed "
        "authored field set, untrusted-narrative discipline, subtractive authority)",
    ),
    (
        "orchestrator.planner.skill",
        "skill",
        "SKILL.md",
        "orchestrator.planner skill-v1 (roster projection is the only worker "
        "universe; data-source priority)",
    ),
    (
        "orchestrator.planner.guardrail",
        "guardrail",
        "guardrail.md",
        "orchestrator.planner guardrail (subtractive authority; honest failure; "
        "untrusted narrative carries no authority)",
    ),
)


def load_planner_material_bytes(
    materials_dir: Path = PLANNER_MATERIALS_DIR,
) -> dict[str, bytes]:
    """Read the physical planner inventory into ``{content_id: bytes}``.

    The directory is a local variable and never enters any returned object
    (contracts carry content-digest-sealed refs, never physical paths).
    """
    return {
        content_id: (materials_dir / filename).read_bytes()
        for content_id, _kind, filename, _desc in _PLANNER_FILES
    }


def load_planner_materials(
    materials_dir: Path = PLANNER_MATERIALS_DIR,
) -> tuple[ResolvedMaterial, ...]:
    """Resolve the planner inventory into content-digest-sealed text materials.

    Digests are computed from the supplied bytes (never pinned by hand per ref)
    via :func:`~guanlan_v2.orchestration.catalog_runtime.build_text_material`.
    """
    material_bytes = load_planner_material_bytes(materials_dir)
    materials: list[ResolvedMaterial] = []
    for content_id, kind, _filename, _desc in _PLANNER_FILES:
        _ref, mat = build_text_material(
            id=content_id, version="1", kind=kind, raw=material_bytes[content_id]
        )
        materials.append(mat)
    return tuple(materials)


def _planner_manifest_entries(
    planner_materials: tuple[ResolvedMaterial, ...],
) -> tuple[tuple[ContentManifestEntry, ...], tuple[SkillManifest, ...]]:
    """Build (+ self-verify) the planner content/skill manifest entries.

    Each material must be a text material whose declared ``content_digest``
    recomputes from its bytes; its id must be one of the three reviewed planner
    ids with the matching kind. The skill material is parsed under the skill-v1
    grammar; its manifest envelope mirrors the parsed document.
    """
    spec_by_id = {cid: (kind, desc) for cid, kind, _fn, desc in _PLANNER_FILES}
    by_id: dict[str, ResolvedTextMaterial] = {}
    for mat in planner_materials:
        if not isinstance(mat, ResolvedTextMaterial):
            raise CatalogError(
                "planner materials must be text materials (prompt/skill/guardrail); "
                f"got {type(mat).__name__}"
            )
        if catalog_material_digest(mat) != mat.ref.content_digest:
            raise CatalogError(
                f"planner material {mat.ref.id}@{mat.ref.version} content_digest does "
                "not recompute from its bytes"
            )
        by_id[mat.ref.id] = mat

    if set(by_id) != set(spec_by_id):
        raise CatalogError(
            "planner materials must be exactly "
            f"{sorted(spec_by_id)}; got {sorted(by_id)}"
        )

    content_entries: list[ContentManifestEntry] = []
    skill_entries: list[SkillManifest] = []
    for content_id, (kind, desc) in spec_by_id.items():
        mat = by_id[content_id]
        if mat.kind != kind:
            raise CatalogError(
                f"planner material {content_id} kind {mat.kind!r} != expected {kind!r}"
            )
        if kind == "skill":
            parsed = parse_skill_v1(mat.raw_utf8.decode("utf-8"))
            skill_entries.append(
                SkillManifest(
                    ref=mat.ref,
                    name=parsed.name,
                    summary=parsed.summary,
                    perfect_for=parsed.perfect_for,
                    not_ideal_for=parsed.not_ideal_for,
                    critical_data_source_heading="⚠️ CRITICAL: Data Source Priority",
                    source_identity=PLANNER_SOURCE_IDENTITY,
                )
            )
        else:
            content_entries.append(
                ContentManifestEntry(
                    ref=mat.ref,
                    kind=kind,
                    name=content_id,
                    description=desc,
                    source_identity=PLANNER_SOURCE_IDENTITY,
                )
            )
    return tuple(content_entries), tuple(skill_entries)


def build_phase7_catalog_snapshot(
    phase6_snapshot: "WorkerCatalogSnapshot",
    *,
    planner_materials: tuple[ResolvedMaterial, ...],
) -> "WorkerCatalogSnapshot":
    """Extend the immutable Phase-6 catalog with the catalog-owned planner materials.

    Rejects any base whose ``catalog_digest`` differs from the canonical Phase-6
    cumulative catalog digest (the sole legal base), then adds ONLY the planner
    content (system prompt + guardrail) and skill (SKILL.md) manifest entries —
    **no** ``WorkerSpec`` and **no** capability. The Planner is deliberately not a
    selectable worker (a ``final`` spec would be ``dynamic_allowed`` and could
    recursively select itself; materials-without-worker is the reviewed
    containment), so every inherited Phase-<=6 worker/capability passes through
    byte-identical and ``count_final_workers`` is unchanged. The merge rejects any
    id collision, sorts the set-like manifests, seals the cumulative
    ``catalog_digest`` and re-validates every cross-reference.
    """
    base_digest = _phase6_catalog_digest()
    if phase6_snapshot.catalog_digest != base_digest:
        raise CatalogError(
            "build_phase7_catalog_snapshot requires the canonical immutable Phase-6 "
            f"catalog ({base_digest}); got base {phase6_snapshot.catalog_digest}"
        )

    planner_content, planner_skill = _planner_manifest_entries(planner_materials)

    content = tuple(phase6_snapshot.content_manifest) + planner_content
    skills = tuple(phase6_snapshot.skill_manifest) + planner_skill
    caps = tuple(phase6_snapshot.capability_manifest)
    workers = tuple(phase6_snapshot.workers)

    _reject_collisions(content, lambda e: e.ref_key, "content material")
    _reject_collisions(skills, lambda e: e.ref_key, "skill material")

    content_sorted = tuple(sorted(content, key=lambda e: e.ref_key))
    skills_sorted = tuple(sorted(skills, key=lambda e: e.ref_key))

    fields: dict[str, Any] = dict(
        catalog_version=PHASE7_CATALOG_VERSION,
        content_manifest=content_sorted,
        skill_manifest=skills_sorted,
        capability_manifest=caps,
        workers=workers,
    )
    try:
        digest = WorkerCatalogSnapshot.digest_of_fields(projection="semantic", **fields)
    except (ValueError, TypeError, AttributeError, KeyError):
        digest = "0" * 64
    try:
        snapshot = WorkerCatalogSnapshot(**fields, catalog_digest=digest)
    except Exception as exc:  # surface structural failures as CatalogError
        raise CatalogError(f"phase7 catalog snapshot is not runnable: {exc}") from exc
    validate_catalog_snapshot(snapshot)
    return snapshot


def _reject_collisions(items, keyfn, label: str) -> None:
    seen: set[Any] = set()
    for item in items:
        key = keyfn(item)
        if key in seen:
            raise CatalogError(
                f"phase7 catalog {label} key collides with an inherited entry: {key!r}"
            )
        seen.add(key)


def phase7_catalog_snapshot() -> "WorkerCatalogSnapshot":
    """The canonical cumulative Phase-7 catalog snapshot.

    Rebuilds the reviewed cumulative Phase-6 catalog the same way its owning module
    assembles it, resolves the catalog-owned planner materials from disk, and adds
    them through :func:`build_phase7_catalog_snapshot`.
    """
    from guanlan_v2.orchestration import shadow

    base = shadow.phase6_catalog_snapshot()
    return build_phase7_catalog_snapshot(
        base, planner_materials=load_planner_materials()
    )


# =========================================================================== #
# The reviewed Planner identity (derived from the Phase-7 catalog manifests)     #
# =========================================================================== #
#: the reviewed Planner identity + budget knobs. ``model_tier="reasoner_deep"`` is
#: a reviewed deviation from spec §10 (which reserves ``reasoner_deep`` for the
#: ``dec.pm`` seat): the Planner is not a lane worker, runs once per run, and its
#: plan-shaping quality dominates that once-per-run cost. ``max_generation_attempts``
#: is the reviewed hard budget (parser caps it at 3); ``attempt_token_reservation``
#: is the reviewed per-attempt token reservation (headroom for the roster
#: projection + a k>1 repair pass over a multi-node plan). ``thinking_budget`` stays
#: at the field default 0 — the tier already encodes the reasoning depth, so the
#: only reviewed deviation is the tier itself.
PLANNER_ID = "orchestrator.planner"
PLANNER_VERSION = "1"
_PLANNER_MODEL_TIER = "reasoner_deep"
_PLANNER_MAX_GENERATION_ATTEMPTS = 2
_PLANNER_ATTEMPT_TOKEN_RESERVATION = 16000


def build_phase7_planner_spec(catalog: "WorkerCatalogSnapshot") -> PlannerSpec:
    """Derive the reviewed :class:`PlannerSpec` from the Phase-7 catalog manifests.

    The prompt / skill / guardrail refs are resolved from ``catalog`` by exact
    id/version/digest (never a path), so the spec can never bind material bytes
    that drifted from the sealed catalog. A missing planner material is a loud
    :class:`CatalogError` (the Phase-7 catalog must have been built with the
    planner materials).
    """
    content_by_id = {(e.ref.id, e.ref.version): e for e in catalog.content_manifest}
    skill_by_id = {(e.ref.id, e.ref.version): e for e in catalog.skill_manifest}

    def _content_ref(content_id: str):
        entry = content_by_id.get((content_id, "1"))
        if entry is None:
            raise CatalogError(
                f"phase7 catalog is missing planner content material {content_id}@1; "
                "build the catalog with the planner materials first"
            )
        return entry.ref

    prompt_ref = _content_ref("orchestrator.planner.prompt")
    guardrail_ref = _content_ref("orchestrator.planner.guardrail")
    skill_entry = skill_by_id.get(("orchestrator.planner.skill", "1"))
    if skill_entry is None:
        raise CatalogError(
            "phase7 catalog is missing planner skill material "
            "orchestrator.planner.skill@1"
        )

    return PlannerSpec(
        planner_id=PLANNER_ID,
        version=PLANNER_VERSION,
        system_prompt_ref=prompt_ref,
        skills=(SkillBinding(skill_ref=skill_entry.ref),),
        guardrail_refs=(guardrail_ref,),
        model_tier=_PLANNER_MODEL_TIER,
        max_generation_attempts=_PLANNER_MAX_GENERATION_ATTEMPTS,
        attempt_token_reservation=_PLANNER_ATTEMPT_TOKEN_RESERVATION,
    )


def phase7_planner_spec() -> PlannerSpec:
    """The canonical reviewed :class:`PlannerSpec` over the canonical Phase-7 catalog."""
    return build_phase7_planner_spec(phase7_catalog_snapshot())


# --------------------------------------------------------------------------- #
# Lazy canonical digests (PEP 562) — computed once, never a mutable "latest"    #
# --------------------------------------------------------------------------- #
_PHASE7_REGISTRY_DIGEST: str | None = None
_PHASE7_CATALOG_DIGEST: str | None = None
_PHASE7_PLANNER_SPEC_DIGEST: str | None = None
_PHASE7_INTERNAL_MODELS: "dict[type[DigestModel], str] | None" = None


def __getattr__(name: str) -> "Any":
    global _PHASE7_REGISTRY_DIGEST, _PHASE7_CATALOG_DIGEST
    global _PHASE7_PLANNER_SPEC_DIGEST, _PHASE7_INTERNAL_MODELS
    if name == "PHASE7_INTERNAL_MODELS":
        if _PHASE7_INTERNAL_MODELS is None:
            _PHASE7_INTERNAL_MODELS = _phase7_internal_models()
        return _PHASE7_INTERNAL_MODELS
    if name == "PHASE7_BASE_REGISTRY_DIGEST":
        return _phase6_registry_digest()
    if name == "PHASE7_REGISTRY_DIGEST":
        if _PHASE7_REGISTRY_DIGEST is None:
            _PHASE7_REGISTRY_DIGEST = build_phase7_registry(
                _phase6_registry_digest()
            ).registry_digest
        return _PHASE7_REGISTRY_DIGEST
    if name == "PHASE7_BASE_CATALOG_DIGEST":
        return _phase6_catalog_digest()
    if name == "PHASE7_CATALOG_DIGEST":
        if _PHASE7_CATALOG_DIGEST is None:
            _PHASE7_CATALOG_DIGEST = phase7_catalog_snapshot().catalog_digest
        return _PHASE7_CATALOG_DIGEST
    if name == "PHASE7_PLANNER_SPEC_DIGEST":
        if _PHASE7_PLANNER_SPEC_DIGEST is None:
            _PHASE7_PLANNER_SPEC_DIGEST = phase7_planner_spec().semantic_digest()
        return _PHASE7_PLANNER_SPEC_DIGEST
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
