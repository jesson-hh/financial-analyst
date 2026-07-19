"""Phase 7 · Task 3 — the sealed fallback plan-preset registry + materializer.

When the dynamic Planner (Phase 7) produces no admissible candidate, the runtime
does not improvise a plan — it materializes ONE reviewed, sealed **preset** named
by the trusted :class:`~guanlan_v2.orchestration.spec.OrchestrationRequest`'s
``fallback_preset_id`` field. This module owns that reviewed surface:

* :class:`PlanPresetRecord` — a static-profile-admissible reviewed plan
  (registered-to-be by Task 9; it exists here unregistered). Every node is
  admissible under the frozen static runtime profile: unique node ids, no gate
  ids, no debate identity, no ``condition_ref`` and ``max_attempts == 1`` — so a
  preset can never smuggle gate/debate/condition/retry authority that the static
  profile forbids.
* :class:`PlanPresetRegistry` — a :class:`SchemaRegistry`-shaped
  register/seal/get/manifest/registry_digest service. Duplicate ``preset_id``
  with different content is a :class:`PlanPresetError`; an identical re-register is
  idempotent; every mutation after :meth:`~PlanPresetRegistry.seal` is impossible.
* :func:`load_preset_registry` — a strict UTF-8-no-BOM, ``extra="forbid"`` loader
  over ``config/orchestration/presets/*.json`` that rejects a duplicate
  ``preset_id`` across files and seals before returning. A physical path never
  enters any record — a :class:`PlanPresetRecord` has no path/file field.
* :func:`materialize_fallback_draft` — **THE** request-level rule made
  executable: ``request.fallback_preset_id == preset.preset_id`` or
  :class:`PlanPresetError` (there is no "default preset", no ordering-based pick,
  no model-supplied preset id). It stamps the same runtime fields as Task 2's
  ``assemble_dynamic_draft`` but with ``source=PlanSource.PRESET_FALLBACK`` and
  copies the reviewed nodes/sinks/budget/concurrency verbatim from the preset. A
  preset worker that is not ``catalog_role="final"`` in the bound catalog is a
  :class:`PlanPresetError` *before* validation (v1 presets never take the
  ``StaticLegacyPlanAttestation`` compatibility path; the Phase-1 builder
  ``attest_static_legacy_plan`` remains the only attestation producer and is
  unused here).

Layering / stamping-sharing choice (deliberate): this module imports **no**
Phase-7 sibling — every primitive it needs comes from the frozen Phase-1/2
contract layer (``spec`` / ``context`` / ``refs`` / ``catalog`` /
``schema_registry`` / ``enums`` / ``digest``). It does NOT import
``orchestrator.assemble_dynamic_draft`` nor any private helper of it. The
runtime-stamping in ``assemble_dynamic_draft`` is inlined there (no cleanly
exported ``_stamp_main_draft`` helper), and extracting one would require editing
Task 2's committed ``orchestrator.py`` — outside this task's pathspec. The two
functions also differ materially (preset nodes are already real ``PlanNode``s, so
there is no envelope lift, no universe normalization and no generation-side shape
codes; the preset adds the ``fallback_preset_id`` equality rule and the
final-only catalog-role gate). The genuinely shared surface is only the final
``PlanDraft(...)`` field list and the ~6-line context-ref precondition, which are
transcribed here so each function stays independently reviewable. A small amount
of honest duplication beats a cross-module private import (and the required
``orchestrator.py`` edit) here.
"""
from __future__ import annotations

import codecs
import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, model_validator

from guanlan_v2.orchestration.catalog import WorkerCatalogSnapshot
from guanlan_v2.orchestration.context import ContextSnapshot
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    content_digest,
)
from guanlan_v2.orchestration.enums import PlanSource
from guanlan_v2.orchestration.refs import LogicalId, PayloadRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import (
    OrchestrationRequest,
    PlanDraft,
    PlanNode,
)

__all__ = [
    "PlanPresetError",
    "PlanPresetRecord",
    "PlanPresetManifestEntry",
    "PlanPresetRegistry",
    "load_preset_registry",
    "materialize_fallback_draft",
]


class PlanPresetError(ValueError):
    """A fallback preset is unknown, malformed, duplicated, mis-selected, or its
    worker set is not admissible for the bound catalog.

    Task 4 maps this (an unknown / unregistered ``fallback_preset_id`` at
    selection time) to the ``halted_no_fallback`` terminal outcome — never a
    silent substitute.
    """


# =========================================================================== #
# PlanPresetRecord — a static-profile-admissible reviewed plan                 #
# =========================================================================== #
class PlanPresetRecord(DigestModel):
    """One reviewed, sealed fallback plan (static-profile-admissible).

    Registered-to-be by Task 9 (it exists here unregistered). Every field is
    semantic identity — editing any of them (nodes, sinks, budget, concurrency,
    description) moves the record's semantic digest and therefore the sealed
    registry digest, so a preset can never be quietly changed under the same
    identity. The ``after`` validator enforces exactly the static profile's
    admissibility surface: unique node ids; a non-empty node tuple with a
    non-empty sink tuple whose ids are all real nodes; and — on **every** node —
    ``gate_ids == ()``, no debate identity field, ``condition_ref is None`` and
    ``max_attempts == 1``. A node that carries a gate, a debate seat, a
    ``condition_ref`` or a retry budget is not a v1 preset node.
    """

    schema_version: Literal["1"] = "1"
    preset_id: LogicalId
    version: NonEmptyStr
    phase: Literal["main"] = "main"
    description: NonEmptyStr
    nodes: tuple[PlanNode, ...]
    sink_node_ids: tuple[LogicalId, ...]
    budget_request_tokens: NonNegativeInt
    budget_request_llm_invocations: NonNegativeInt
    max_concurrency: PositiveInt

    @model_validator(mode="after")
    def _admissible(self) -> "PlanPresetRecord":
        if not self.nodes:
            raise ValueError("a preset must declare at least one node")
        if not self.sink_node_ids:
            raise ValueError("a preset must declare at least one sink node id")

        node_ids = [n.id for n in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("preset node ids must be unique")
        node_id_set = set(node_ids)
        for sink in self.sink_node_ids:
            if sink not in node_id_set:
                raise ValueError(f"sink node id {sink!r} is not a preset node")

        for node in self.nodes:
            if node.gate_ids != ():
                raise ValueError(
                    f"preset node {node.id!r} must carry no gate_ids (static profile)")
            if (
                node.debate_id is not None
                or node.round_role is not None
                or node.debate_round is not None
                or node.debate_turn is not None
            ):
                raise ValueError(
                    f"preset node {node.id!r} must carry no debate identity "
                    "(static profile)")
            if node.condition_ref is not None:
                raise ValueError(
                    f"preset node {node.id!r} must carry no condition_ref "
                    "(static profile)")
            if node.max_attempts != 1:
                raise ValueError(
                    f"preset node {node.id!r} must have max_attempts == 1; got "
                    f"{node.max_attempts}")
        return self


# =========================================================================== #
# Registry manifest entry                                                     #
# =========================================================================== #
class PlanPresetManifestEntry(DigestModel):
    """One entry of a preset-registry manifest: preset ref + its semantic digest.

    ``preset_digest`` is the referenced :class:`PlanPresetRecord`'s own content
    digest (an ordinary semantic field, never this entry's self-digest; named to
    not shadow the inherited ``DigestModel.semantic_digest`` method), so the
    registry digest changes iff a preset's content — or the preset set — changes.
    """

    schema_version: Literal["1"] = "1"
    preset_id: LogicalId
    version: NonEmptyStr
    preset_digest: DigestHex


# =========================================================================== #
# PlanPresetRegistry — SchemaRegistry-shaped                                   #
# =========================================================================== #
class PlanPresetRegistry:
    """Mutable-until-sealed registry of reviewed :class:`PlanPresetRecord`s.

    Mirrors :class:`SchemaRegistry`: :meth:`register` is idempotent for a
    byte-identical record and raises :class:`PlanPresetError` for a different
    record under an existing ``preset_id`` or after :meth:`seal`; :meth:`get`
    raises for an unknown id; :meth:`manifest` is sorted by ``(preset_id,
    version)`` so :attr:`registry_digest` is registration-order independent.
    """

    def __init__(self) -> None:
        self._records: dict[str, PlanPresetRecord] = {}
        self._sealed: bool = False

    @property
    def sealed(self) -> bool:
        return self._sealed

    # -- mutation ---------------------------------------------------------- #
    def register(self, record: PlanPresetRecord) -> None:
        """Register ``record`` under its ``preset_id``.

        Idempotent for a semantically-identical record; raises
        :class:`PlanPresetError` for a different record under an existing
        ``preset_id`` and once the registry is sealed.
        """
        if self._sealed:
            raise PlanPresetError("cannot register into a sealed preset registry")
        if not isinstance(record, PlanPresetRecord):
            raise PlanPresetError(
                f"only PlanPresetRecord instances can be registered; got {record!r}")
        existing = self._records.get(record.preset_id)
        if existing is not None:
            if existing.semantic_digest() == record.semantic_digest():
                return  # idempotent
            raise PlanPresetError(
                f"preset_id {record.preset_id!r} is already registered to a "
                "different reviewed record")
        self._records[record.preset_id] = record

    def seal(self) -> None:
        """Freeze the registry: subsequent :meth:`register` calls fail."""
        self._sealed = True

    # -- reads ------------------------------------------------------------- #
    def get(self, preset_id: str) -> PlanPresetRecord:
        """Return the record registered under ``preset_id`` or raise.

        This is the ONLY selection path — by exact ``preset_id`` equality. There
        is no "default preset", no ordering-based pick and no model-supplied id.
        """
        record = self._records.get(preset_id)
        if record is None:
            raise PlanPresetError(f"no plan preset registered for {preset_id!r}")
        return record

    def manifest(self) -> tuple[PlanPresetManifestEntry, ...]:
        """Return manifest entries sorted by ``(preset_id, version)``."""
        entries = [
            PlanPresetManifestEntry(
                preset_id=rec.preset_id,
                version=rec.version,
                preset_digest=rec.semantic_digest(),
            )
            for rec in self._records.values()
        ]
        entries.sort(key=lambda e: (e.preset_id, e.version))
        return tuple(entries)

    @property
    def registry_digest(self) -> DigestHex:
        """Registration-order-independent digest over the sorted manifest."""
        return content_digest(list(self.manifest()))


# =========================================================================== #
# Strict loader                                                                #
# =========================================================================== #
def load_preset_registry(root: Path) -> PlanPresetRegistry:
    """Load, validate and seal the reviewed presets under ``root``.

    Strict over ``root/*.json``: each file is read as UTF-8 and rejected if it
    carries a byte-order mark; validated through :class:`PlanPresetRecord`
    (``extra="forbid"`` + strict types), and a duplicate ``preset_id`` across
    files is a :class:`PlanPresetError`. The registry is sealed before it is
    returned. No physical path is ever placed inside a record.
    """
    registry = PlanPresetRegistry()
    seen_ids: set[str] = set()
    for path in sorted(Path(root).glob("*.json")):
        raw = path.read_bytes()
        if raw.startswith(codecs.BOM_UTF8):
            raise PlanPresetError(
                f"preset file {path.name!r} must be UTF-8 with no byte-order mark")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PlanPresetError(
                f"preset file {path.name!r} is not valid UTF-8: {exc}") from exc
        try:
            record = PlanPresetRecord.model_validate_json(text)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise PlanPresetError(
                f"preset file {path.name!r} is not a valid PlanPresetRecord: {exc}"
            ) from exc
        if record.preset_id in seen_ids:
            raise PlanPresetError(
                f"duplicate preset_id {record.preset_id!r} across preset files")
        seen_ids.add(record.preset_id)
        registry.register(record)
    registry.seal()
    return registry


# =========================================================================== #
# materialize_fallback_draft — the request-level rule made executable          #
# =========================================================================== #
def materialize_fallback_draft(
    preset: PlanPresetRecord,
    *,
    request: OrchestrationRequest,
    context: ContextSnapshot,
    context_snapshot_ref: PayloadRef,
    catalog: WorkerCatalogSnapshot,
    schema_registry: SchemaRegistry,
    draft_id: LogicalId,
    run_id: NonEmptyStr,
) -> PlanDraft:
    """Materialize a runtime-stamped ``PRESET_FALLBACK`` :class:`PlanDraft`.

    THE request-level rule made executable: ``request.fallback_preset_id`` must
    equal ``preset.preset_id`` (a mismatch — including a ``None`` request field —
    is a :class:`PlanPresetError`). There is no other selection path.

    The returned object is a *plain* Phase-1 ``PlanDraft`` (schema_version "2"):
    every kernel-authority / identity field is runtime-stamped, never
    preset-authored — ``id`` / ``run_id`` / ``request_id`` / ``phase='main'`` /
    ``source=PRESET_FALLBACK`` / ``goal`` come from the trusted request + caller;
    ``as_of`` / ``mode`` from ``context.data_context``; ``catalog_version`` /
    ``catalog_digest`` from the sealed catalog snapshot;
    ``schema_registry_digest`` from the sealed registry; and ``approval_policy`` is
    a verbatim copy of the request's (``AUTO`` still dies in Phase-1 validation —
    no relaxation here). ``debates`` / ``gates`` / ``reducers`` /
    ``stop_condition_refs`` are empty and the legacy compatibility tuple is
    ``None`` (v1 presets never take the ``StaticLegacyPlanAttestation`` path). The
    reviewed ``nodes`` / ``sink_node_ids`` / budget request / ``max_concurrency``
    are copied verbatim from the preset.

    Every preset worker must be ``catalog_role="final"`` in the bound catalog; a
    compat (or unknown) worker is a :class:`PlanPresetError` **before** any
    construction. ``context_snapshot_ref`` is a trusted runtime input; it must
    reference the supplied ``ContextSnapshot`` (``namespace == 'main'`` and
    ``content_digest == context.content_digest``) — a mismatch is a caller wiring
    error raised as a plain :class:`ValueError`. Semantic catalog/registry checks
    (params conformance, dependency schema equality, decision-sink authority, …)
    are deliberately left to Phase-1
    :func:`~guanlan_v2.orchestration.spec.validate_plan_draft`, which processes the
    returned plain draft unshimmed.
    """
    # -- 1. THE selection rule: exact fallback_preset_id equality ------------ #
    if request.fallback_preset_id != preset.preset_id:
        raise PlanPresetError(
            "request.fallback_preset_id "
            f"{request.fallback_preset_id!r} does not name preset "
            f"{preset.preset_id!r}; a preset is only ever selected by exact "
            "fallback_preset_id equality")

    # -- 2. context-ref precondition (trusted input, not the preset) --------- #
    if context_snapshot_ref.namespace != "main":
        raise ValueError(
            "context_snapshot_ref must use namespace='main'; got "
            f"{context_snapshot_ref.namespace!r}")
    if context_snapshot_ref.content_digest != context.content_digest:
        raise ValueError(
            "context_snapshot_ref.content_digest does not bind the supplied "
            "ContextSnapshot content")

    # -- 3. final-workers-only catalog-role gate (before any construction) --- #
    worker_by_id = {w.id: w for w in catalog.workers}
    non_final: list[str] = []
    for node in preset.nodes:
        worker = worker_by_id.get(node.worker_id)
        if worker is None or worker.catalog_role != "final":
            non_final.append(node.worker_id)
    if non_final:
        raise PlanPresetError(
            "preset worker(s) not catalog_role='final' in the bound catalog: "
            + ", ".join(sorted(set(non_final))))

    # -- 4. construct the runtime-stamped PRESET_FALLBACK main PlanDraft ------ #
    # (stamps the same field list as orchestrator.assemble_dynamic_draft; see the
    #  module docstring for why this is transcribed rather than shared.)
    return PlanDraft(
        id=draft_id,
        run_id=run_id,
        request_id=request.request_id,
        phase="main",
        source=PlanSource.PRESET_FALLBACK,
        goal=request.goal,
        as_of=context.data_context.as_of,
        mode=context.data_context.mode,
        context_snapshot_ref=context_snapshot_ref,
        universe=(),
        nodes=preset.nodes,
        sink_node_ids=preset.sink_node_ids,
        debates=(),
        gates=(),
        reducers=(),
        catalog_version=catalog.catalog_version,
        catalog_digest=catalog.catalog_digest,
        schema_registry_digest=schema_registry.registry_digest,
        approval_policy=request.approval_policy,
        budget_request_tokens=preset.budget_request_tokens,
        budget_request_llm_invocations=preset.budget_request_llm_invocations,
        max_concurrency=preset.max_concurrency,
        stop_condition_refs=(),
        legacy_source_schema=None,
        legacy_source_config_digest=None,
        legacy_mapping_digest=None,
    )
