# -*- coding: utf-8 -*-
"""Phase 10 · Task 0b — production assembly: catalog loading, worker tier
gateways, plan-runner composition.

The hole this closes
--------------------
The Phase 1-9 kernel classes all exist — :class:`~guanlan_v2.orchestration.
catalog_runtime.CatalogRuntime`, :class:`~guanlan_v2.orchestration.
catalog_runtime.TrustedFactoryRegistry`, the ``worker.py`` execution runtime and
its :class:`~guanlan_v2.orchestration.worker.ModelGateway` port, the ``pool.py``
artifact pool, the Phase-9 launcher — but nothing ever ASSEMBLED them for lane
workers in production: the launcher's own docstring records "there is no
production assembler for any of them", and the only production gateway is the
planner seat's :class:`~guanlan_v2.orchestration.planner_gateway.
PlannerLLMModelGateway`. This module is that assembler, and it is composition
ONLY: it imports the implemented kernel surfaces and wires them together — it
never re-implements admission, execution, freezing, or approval, and it adds no
approval/admission bypass (the runner it returns is exactly
:func:`~guanlan_v2.orchestration.adapters.launcher.build_admitted_plan_runner`'s,
red lines inherited intact).

The four assembly surfaces
--------------------------
* :func:`build_production_catalog_runtime` — resolves a catalog snapshot's
  materials from their reviewed ``config/orchestration/`` physical sources per
  the manifests (never a path inside any contract object), builds the verified
  :class:`CatalogRuntime` through the Phase-2 builder, and registers
  deterministic handler factories through the reviewed
  :class:`TrustedFactoryRegistry` mechanism (Task 11 registers the ``cand.*``
  handlers here). **E1 correction, recorded:** the brief sketched
  ``(catalog_snapshot, *, registry, handler_registry) -> CatalogRuntime``, but
  the implemented :class:`TrustedFactoryRegistry` is constructed FROM the built
  runtime (catalog_runtime.py:437) and its registrations must travel with the
  runtime into :class:`~guanlan_v2.orchestration.worker.ExecutionRuntime` — so
  this returns the frozen :class:`ProductionCatalogRuntime` pair instead of
  discarding the registry.
* :class:`WorkerSeatModelGateway` — the worker-side
  :class:`~guanlan_v2.orchestration.worker.ModelGateway`: per invocation it
  rehash-refuses the prompt binding FIRST (the shared
  :func:`~guanlan_v2.orchestration.worker.verify_model_request_binding`), maps
  the invoked worker's catalog ``model_tier`` to its Phase-8 llm.yaml seat
  through the reviewed :data:`~guanlan_v2.orchestration.model_tiers.
  ORCHESTRATION_TIER_ALIASES` join, and resolves the engine client via
  ``LLMClient.for_agent(<seat>, config_path=<repo>/config/llm.yaml)`` — the
  explicit repo path, NEVER ``find_config`` (the planner-gateway idiom,
  planner_gateway.py:95). An unconfigured/unknown tier raises the typed
  :class:`~guanlan_v2.orchestration.model_tiers.ModelTierUnconfigured` naming
  the seat — never a silent vendor fallback. Single-shot, thread-isolated.
* :func:`build_production_plan_runner` — composes CatalogRuntime +
  ExecutionRuntime + ArtifactPool + gateways into exactly what
  ``build_admitted_plan_runner`` consumes. ``gateway_factory`` is the ONLY seam
  that differs between tests (scripted gateways) and production
  (:func:`production_gateway_factory`); the assembly code itself is identical in
  both. **E1 correction, recorded:** the brief's four named kwargs
  (``stores`` / ``catalog_snapshot`` / ``registry`` / ``gateway_factory``)
  cannot alone satisfy the implemented launcher ABI — ``build_admitted_plan_runner``
  requires the caller's own admission artifacts (``admission`` /
  ``lane_bindings`` / ``run_context_factory`` / ``request_id``,
  launcher.py:313-322) and ``build_dag_plan_executor`` requires
  ``clock`` / ``runtime_limit`` / the runtime registry digest
  (launcher.py:269-284) — so those are explicit pass-through parameters here,
  with no new semantics of their own.
* :func:`build_phase10_preset_registry` — the sealed preset-extension
  mechanism: a new sealed :class:`PlanPresetRegistry` = the verified Phase-7
  base records + the reviewed Phase-10 records. A base that is unsealed, or
  does not hold the committed Phase-7 baseline preset byte-faithfully, is a
  typed :class:`PresetBaseRefused` — the Phase-7 preset golden
  (``config/orchestration/presets/main_research_baseline.json``) is never
  touched. Task 3 adds the screening lane record, Task 6 the deep-decide record.

Physical material resolution — honest scope
-------------------------------------------
Physical locators are service configuration, never contract data (Phase-2 rule),
so the manifest→bytes mapping must come from a reviewed loader. The default
universe today is the pilot-lineage loader (``load_pilot_catalog``); a snapshot
referencing material no reviewed loader holds raises
:class:`~guanlan_v2.orchestration.catalog_runtime.CatalogMaterialError` NAMING
the material — assembly never fabricates bytes. Callers whose materials live in
their own reviewed loaders (Tasks 2/11's phase-10 catalog) pass
``material_source=`` explicitly; drift is still caught, because
``CatalogRuntime.build`` rebuilds the snapshot through the Phase-1
material-aware builder and requires exact pinned ``catalog_digest`` equality.

Thread discipline (the worker gateway)
--------------------------------------
``dag.run_plan`` invokes the gateway from ``asyncio.to_thread`` worker threads
(dag.py:728), possibly several nodes concurrently. The engine caches one
``httpx.AsyncClient`` per provider in a module global, and an httpx client binds
to the loop it is first used on — so this gateway owns ONE private event loop
and serializes invocations with a lock (thread-isolated, single-shot per
invocation, exactly the planner-gateway idiom's resolution of the loop-lifetime
hazard recorded at launcher.py:204-223). ``invoke`` blocks; it must never be
called from a thread running an event loop — the composed runner already
refuses that thread (the launcher's 9999 red line).
"""
from __future__ import annotations

import asyncio
import codecs
import dataclasses
import json
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping

from pydantic import ValidationError, model_validator

from guanlan_v2.orchestration import planner_gateway as _planner_gateway
from guanlan_v2.orchestration.adapters.launcher import (
    build_admitted_plan_runner,
    build_dag_plan_executor,
    pool_sink_artifact_resolver,
)
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog import WorkerCatalogSnapshot
from guanlan_v2.orchestration.catalog_runtime import (
    BridgeCatalogView,
    CatalogMaterialError,
    CatalogRuntime,
    InMemoryMaterialSource,
    MaterialSource,
    TrustedFactoryRegistry,
    load_pilot_catalog,
)
from guanlan_v2.orchestration.enums import ExecutionKind
from guanlan_v2.orchestration.eventstore import EventRefusalAuditSink
from guanlan_v2.orchestration.debate import DEBATE_MAX_ROUNDS
from guanlan_v2.orchestration.model_tiers import (
    ORCHESTRATION_TIER_ALIASES,
    ModelTierBinding,
    ModelTierUnconfigured,
    tier_map_from_llm_yaml,
)
from guanlan_v2.orchestration.plan_presets import (
    PlanPresetError,
    PlanPresetRecord,
    PlanPresetRegistry,
    load_preset_registry,
)
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import default_audit_detail_registry
from guanlan_v2.orchestration.spec import DebateCfg, ReducerCfg
from guanlan_v2.orchestration.worker import (
    AssembledModelRequest,
    ExecutionRuntime,
    ModelResult,
    StaticPromptAssembler,
    verify_model_request_binding,
)

__all__ = [
    "PRODUCTION_PRESETS_DIR",
    "PHASE7_BASELINE_PRESET_ID",
    "PHASE10_PRESETS_V2_DIRNAME",
    "PHASE10_PRESETS_V2_DIR",
    "PLAN_PRESET_RECORD_V2_SCHEMA_REF",
    "PlanPresetRecordV2",
    "load_phase10_preset_registry",
    "PipelineAssemblyError",
    "PresetBaseRefused",
    "WorkerSeatModelInvocationError",
    "ProductionCatalogRuntime",
    "WorkerSeatModelGateway",
    "production_material_source",
    "build_production_catalog_runtime",
    "production_bridge_analyzers",
    "production_bridge_view",
    "build_production_planner_runner",
    "production_gateway_factory",
    "build_production_plan_runner",
    "build_phase10_preset_registry",
]

#: the repo's committed preset directory (``parents[3]`` == repo root from
#: ``guanlan_v2/orchestration/pipeline/assembly.py``) — the physical home of the
#: Phase-7 baseline golden. A path constant is service configuration; it never
#: enters any record.
PRODUCTION_PRESETS_DIR: Path = (
    Path(__file__).resolve().parents[3] / "config" / "orchestration" / "presets"
)

#: v2 preset files live in this subdirectory of the committed presets directory
#: precisely so the Phase-7 non-recursive ``root/*.json`` loader cannot see them:
#: a ``schema_version="2"`` file beside the baseline is a hard ``PlanPresetError``
#: for ``load_preset_registry`` and for every existing caller of it.
PHASE10_PRESETS_V2_DIRNAME: str = "v2"
PHASE10_PRESETS_V2_DIR: Path = PRODUCTION_PRESETS_DIR / PHASE10_PRESETS_V2_DIRNAME

#: the Phase-7 reviewed baseline preset — the extension mechanism's base anchor.
PHASE7_BASELINE_PRESET_ID = "main.research_baseline"

#: the registered identity of the Phase-10 preset record generation. Registration
#: itself belongs to the Phase-10 chain task (the Task-1 contracts precedent).
PLAN_PRESET_RECORD_V2_SCHEMA_REF: SchemaRef = SchemaRef(
    name="PlanPresetRecord", version="2")

#: worker completion temperature — mirrors the planner gateway (deepseek-reasoner
#: ignores it; kept low for the non-reasoner tiers). JSON output is forced.
_WORKER_TEMPERATURE = 0.2

#: outer-wait buffer over the seat timeout so the engine transport's own
#: per-request deadline fires first with an accurate message (planner idiom).
_OUTER_WAIT_BUFFER_SEC = 5.0

#: fallback wall-clock bound when a tier binding declares no ``timeout``.
_DEFAULT_TIMEOUT_SEC = 300.0

Factory = Callable[..., Any]


class PipelineAssemblyError(Exception):
    """The production assembly refused to compose (identity drift, wrong seam)."""


class PresetBaseRefused(PipelineAssemblyError):
    """The preset-extension base is not the sealed Phase-7 production registry."""


class WorkerSeatModelInvocationError(Exception):
    """A provider/transport failure during a single worker-seat completion.

    Raised only *after* the request/prompt binding verified (never masking a
    :class:`~guanlan_v2.orchestration.worker.PromptBindingError`) and only for a
    genuine provider-side failure; the executor maps it to a FAILED
    ``model_error`` NodeRun — never a fabricated result. The underlying provider
    exception is preserved as ``__cause__``.
    """


# =========================================================================== #
# 1. production catalog runtime                                                #
# =========================================================================== #
@dataclasses.dataclass(frozen=True)
class ProductionCatalogRuntime:
    """One verified catalog runtime + its trusted factory registry, assembled.

    The pair travels together because :class:`ExecutionRuntime` consumes both
    and the registry's bindings are keyed by THIS runtime's catalog identities.
    """

    runtime: CatalogRuntime
    factories: TrustedFactoryRegistry

    @property
    def snapshot(self) -> WorkerCatalogSnapshot:
        return self.runtime.snapshot

    @property
    def catalog_digest(self) -> str:
        return self.runtime.catalog_digest


def _reviewed_material_universe():
    """``(id, version) -> resolved material`` from the reviewed physical loaders.

    **Task 11 promotion (Task-0b concern 2 closed).** The universe is the UNION
    of every sealed owning-module loader — exactly the nine-loader recipe the
    deep-preset suite proved against the full Phase-9 catalog
    (``test_pipeline_deep_preset._full_phase9_runtime``), plus the Phase-10
    chain's own four pipeline materials. Every entry comes from the material's
    OWN reviewed loader (never guessed bytes); duplicates across loaders keep
    the first resolution (identical bytes — ``CatalogRuntime.build`` recomputes
    every digest downstream, so a byte drift is loud there). With this universe
    :func:`production_material_source` resolves the full Phase-9 AND Phase-10
    cumulative catalogs with ``material_source=None``.
    """
    import guanlan_v2.orchestration.bootstrap as _bs
    import guanlan_v2.orchestration.data.catalog as _dcat
    import guanlan_v2.orchestration.lane_catalog as _lc
    import guanlan_v2.orchestration.memory.catalog as _mcat
    import guanlan_v2.orchestration.phase7_registry as _p7
    import guanlan_v2.orchestration.trial as _trial
    from guanlan_v2.orchestration import presets as _presets
    from guanlan_v2.orchestration.adapters import chain as _p9chain
    from guanlan_v2.orchestration.pipeline import chain as _p10chain

    text: dict[tuple[str, str], Any] = {}
    caps: dict[tuple[str, str], Any] = {}

    def _add(materials) -> None:
        for material in materials:
            key = (material.ref.id, material.ref.version)
            if hasattr(material, "raw_utf8"):
                text.setdefault(key, material)
            elif hasattr(material, "descriptor"):
                caps.setdefault(key, material)
            else:
                # never drop silently: an unrecognized material shape would
                # otherwise resurface later as a misattributed "no reviewed
                # physical source resolves material(s) ..." error.
                raise CatalogMaterialError(
                    f"reviewed loader yielded a material of unexpected shape "
                    f"{type(material).__name__!r} for {key[0]}@{key[1]}: "
                    "neither raw_utf8 (text) nor descriptor (capability)"
                )

    # (1) the P8 lane materials (text/pv/quant/xcut/dec + tier map + reducer)
    _add(_lc.load_phase8_lane_materials())
    # (2) the P7 planner materials (prompt/skill/guardrail)
    _add(_p7.load_planner_materials())
    # (3) the two P9 adapter source materials (handlers + descriptors)
    _replay_desc, _live_desc, p9_source_materials = (
        _p9chain.build_phase9_source_materials())
    _add(p9_source_materials)
    # (4) the P3 data surface (methods/capabilities/bridge)
    data_surface = _dcat.phase3_data_surface()
    _add(data_surface.text_materials())
    _add(data_surface.capability_materials)
    # (5) the P3 memory surface (facade/policy/bridge + memory.propose)
    memory_surface = _mcat.phase3_memory_surface()
    _add(memory_surface.text_materials())
    _add([memory_surface.proposal_capability_material])
    # (6) Lane 0 (experience bridge + market workers) + the #25 placeholder
    _add(_bs.load_lane0_catalog().resolved)
    _add(_bs.factor_miner_placeholder().resolved)
    # (7) the pilot lineage (P2 static) + the compat mirror
    pilot = load_pilot_catalog()
    pilot_text, pilot_caps = pilot.resolved_materials()
    _add(pilot_text)
    _add(pilot_caps)
    _add(_presets._compat_materials()["text_materials"])
    # (8) the P4 joint gate guardrail
    _add([_trial.joint_gate_material()[1]])
    # (9) the Phase-10 pipeline materials (cand.* handlers + preset reference)
    _refs, p10_materials = _p10chain.build_phase10_materials()
    _add(p10_materials)
    return text, caps


def production_material_source(
    catalog_snapshot: WorkerCatalogSnapshot,
) -> InMemoryMaterialSource:
    """Resolve ``catalog_snapshot``'s manifests to physical bytes — or refuse.

    Filters the reviewed universe to EXACTLY the refs the snapshot's manifests
    reference (the Phase-2 runtime rejects extra material, so the filter is
    load-bearing) and raises :class:`CatalogMaterialError` naming every material
    no reviewed loader holds. Byte drift is not this function's problem to
    detect: ``CatalogRuntime.build`` recomputes every digest downstream.
    """
    text_universe, cap_universe = _reviewed_material_universe()
    text_bytes: dict[tuple[str, str], bytes] = {}
    cap_descs: dict[tuple[str, str], Any] = {}
    missing: list[str] = []
    for entry in tuple(catalog_snapshot.content_manifest) + tuple(
        catalog_snapshot.skill_manifest
    ):
        material = text_universe.get(entry.ref_key)
        if material is None:
            missing.append(f"{entry.ref_key[0]}@{entry.ref_key[1]}")
        else:
            text_bytes[entry.ref_key] = material.raw_utf8
    for entry in catalog_snapshot.capability_manifest:
        material = cap_universe.get(entry.ref_key)
        if material is None:
            missing.append(f"{entry.ref_key[0]}@{entry.ref_key[1]}")
        else:
            cap_descs[entry.ref_key] = material.descriptor
    if missing:
        raise CatalogMaterialError(
            "no reviewed physical source under config/orchestration/ resolves "
            f"material(s) {sorted(set(missing))} for catalog "
            f"{catalog_snapshot.catalog_version!r}; pass material_source= from the "
            "materials' own reviewed loader — assembly never fabricates bytes"
        )
    return InMemoryMaterialSource(text=text_bytes, capabilities=cap_descs)


def _catalog_handler_ref(
    catalog_snapshot: WorkerCatalogSnapshot, handler_id: str
) -> ContentRef:
    """The exact catalog ``ContentRef`` for one ``kind='handler'`` material id."""
    entries = [
        e
        for e in catalog_snapshot.content_manifest
        if e.kind == "handler" and e.ref.id == handler_id
    ]
    if not entries:
        raise CatalogMaterialError(
            f"handler_registry names {handler_id!r}, which is not a "
            "kind='handler' catalog material id in this snapshot — off-catalog "
            "handlers are never registered"
        )
    if len(entries) > 1:
        raise CatalogMaterialError(
            f"handler id {handler_id!r} is ambiguous across versions "
            f"{sorted(e.ref.version for e in entries)}; refusing to guess"
        )
    return entries[0].ref


def build_production_catalog_runtime(
    catalog_snapshot: WorkerCatalogSnapshot,
    *,
    material_source: MaterialSource | None = None,
    handler_registry: Mapping[str, Factory] | None = None,
) -> ProductionCatalogRuntime:
    """Build the verified production :class:`CatalogRuntime` + factory registry.

    ``material_source=None`` resolves physically per :func:`production_material_source`;
    an explicit source (a reviewed loader's) replaces it wholesale. Handler
    factories are registered by catalog handler material id through the reviewed
    :meth:`TrustedFactoryRegistry.register_handler` — an off-catalog id or a
    rebind refuses upstream, never here. Task 11 registers the ``cand.*``
    handlers through ``handler_registry``.
    """
    source = (
        material_source
        if material_source is not None
        else production_material_source(catalog_snapshot)
    )
    runtime = CatalogRuntime.build(catalog_snapshot, source)
    factories = TrustedFactoryRegistry(runtime)
    registrations = dict(handler_registry or {})
    for handler_id in sorted(registrations):
        factories.register_handler(
            _catalog_handler_ref(catalog_snapshot, handler_id),
            registrations[handler_id],
        )
    return ProductionCatalogRuntime(runtime=runtime, factories=factories)


def production_bridge_analyzers() -> dict:
    """The ONE reviewed three-analyzer map, keyed by the exact sealed refs.

    Both halves of the production seam consume THIS recipe and no other: the
    admission half through :func:`production_bridge_view`, and the execution
    half as ``build_production_plan_runner``'s ``bridge_analyzers`` (the
    2026-07-31 live-store defect: ``live_decide.plan_runner_factory`` handed
    the runner NO analyzers, so ``BridgeCatalogView.build`` refused ``bridge
    'data.runtime' has no reviewed analyzer bound`` — AFTER the ApprovalLease
    was already consumed. Same disease Task 11 cured at the admission seam; a
    second recipe is how the two halves drifted apart, so there is exactly one).
    """
    import guanlan_v2.orchestration.bootstrap as _bs
    import guanlan_v2.orchestration.data.catalog as _dcat
    import guanlan_v2.orchestration.memory.catalog as _mcat

    data_surface = _dcat.phase3_data_surface()
    memory_surface = _mcat.phase3_memory_surface()
    lane0 = _bs.load_lane0_catalog()

    def _key(ref: ContentRef):
        return (ref.id, ref.version, ref.content_digest)

    return {
        _key(data_surface.analyzer_ref): _dcat.DataBridgeSupportAnalyzer(),
        _key(memory_surface.analyzer_ref): _mcat.MemoryBridgeSupportAnalyzer(),
        lane0.analyzer_key: _bs.ExperienceBridgeSupportAnalyzer(),
    }


def production_bridge_view(runtime: CatalogRuntime, analyzers: Mapping | None = None):
    """The full three-analyzer :class:`BridgeCatalogView` production admission binds.

    Task 11 promotion of the deep-preset suite's reviewed recipe
    (``test_pipeline_deep_preset._full_phase9_bridge_view``): the sealed Phase-9
    catalog carries exactly three execution-bridge descriptors (Phase-3 data,
    Phase-3 memory, Lane-0 experience), and ``BridgeCatalogView.build`` REFUSES
    a runtime whose bridges lack a reviewed analyzer binding — so production
    admission must bind the three real analyzers, keyed by the exact sealed
    analyzer-handler refs. With the Task-11 rowless-reader discriminations the
    resulting support check COMPLETES for every Phase-8 worker and reports the
    remaining data-grant gap honestly (``pv.technical`` / ``text.news``
    ``tool_calls_required_unmet``) instead of crashing.

    ``analyzers=None`` builds :func:`production_bridge_analyzers` (the default
    for every single-view caller: api.py, lane0_driver). A caller that must hand
    the SAME analyzer instances to both the admission view and the plan runner
    (``live_decide.build_production_bindings``) builds the map once and passes
    it here — the map's construction stays this module's, never the caller's.
    """
    from guanlan_v2.orchestration.catalog_runtime import BridgeCatalogView

    return BridgeCatalogView.build(
        runtime,
        dict(production_bridge_analyzers() if analyzers is None else analyzers),
    )


def build_production_planner_runner(
    *,
    stores: Any,
    catalog: ProductionCatalogRuntime,
    schema_registry: Any,
    preset_registry: PlanPresetRegistry,
    clock: Any,
    model_gateway_factory: Factory | None = None,
) -> Factory:
    """The production goal-mode planner seam (Task 11; the reviewed
    ``run_planner`` call shape the pipeline router's ``planner_runner`` field
    documents: ``(*, request, context, context_snapshot_ref, run_id, draft_id)
    -> PlannerResult``).

    The :class:`~guanlan_v2.orchestration.orchestrator.PlannerSpec` is derived
    from the verified production catalog's own planner materials
    (``build_phase7_planner_spec`` — exact id/version/digest, never a path).
    Per call it builds a PLANNER-SCOPED :class:`BudgetLedger` bounded by the
    spec's reviewed attempt arithmetic (``attempt_token_reservation`` ×
    ``max_generation_attempts``; one invocation per attempt) over its own
    ``{run_id}.planner`` budget-event scope — deliberately NOT the run's
    admission ledger, whose ``RunBudget`` the api binds AFTER the planner
    returns a draft (``bind_run_budget`` is first-bind-wins per run).
    ``model_gateway_factory=None`` constructs the production single-shot
    :class:`~guanlan_v2.orchestration.planner_gateway.PlannerLLMModelGateway`
    per call (explicit repo llm.yaml path; closed in ``finally``); tests inject
    a scripted gateway factory — the ONLY test/production difference (the
    Task-0b gateway rule).
    """
    from guanlan_v2.orchestration.context import RunBudget
    from guanlan_v2.orchestration.orchestrator import run_planner
    from guanlan_v2.orchestration.phase7_registry import build_phase7_planner_spec
    from guanlan_v2.orchestration.worker import StaticPromptAssembler

    spec = build_phase7_planner_spec(catalog.snapshot)

    def runner(*, request, context, context_snapshot_ref, run_id, draft_id):
        run_budget = RunBudget(
            ledger_id=f"led-planner-{run_id}",
            max_tokens=spec.attempt_token_reservation * spec.max_generation_attempts,
            max_llm_invocations=spec.max_generation_attempts,
            max_concurrency=1,
        )
        budget = BudgetLedger(
            sink=stores.budget_event_sink(
                run_id=f"{run_id}.planner", ledger_id=run_budget.ledger_id),
            run_budget=run_budget,
        )
        if model_gateway_factory is not None:
            gateway = model_gateway_factory()
        else:
            gateway = _planner_gateway.PlannerLLMModelGateway(
                payload_reader=stores.payloads)
        try:
            return run_planner(
                request=request,
                context=context,
                context_snapshot_ref=context_snapshot_ref,
                catalog_runtime=catalog.runtime,
                schema_registry=schema_registry,
                planner_spec=spec,
                presets=preset_registry,
                budget=budget,
                prompt_assembler=StaticPromptAssembler(),
                model_gateway=gateway,
                payload_store=stores.payloads,
                clock=clock,
                run_id=run_id,
                draft_id=draft_id,
            )
        finally:
            close = getattr(gateway, "close", None)
            if close is not None:
                close()

    return runner


# =========================================================================== #
# 2. the worker-seat model gateway                                             #
# =========================================================================== #
class WorkerSeatModelGateway:
    """The production worker-side single-shot :class:`ModelGateway` (module docstring).

    Conforms to ``invoke(request, *, prompt_assembly_ref) -> ModelResult``
    (worker.py:1112). Per invocation, in order: (1) the shared byte-rehash
    refusal — any binding mismatch raises ``PromptBindingError`` before the
    engine client is imported or built; (2) the invoked worker's catalog
    ``model_tier`` resolves to its Phase-8 seat (typed refusal on an
    unknown/unconfigured tier); (3) the resolved engine client's
    ``provider``/``model`` must EQUAL the reviewed tier binding — a client the
    engine resolved off the binding (default-vendor fallback, shadowed config)
    is a typed refusal naming the seat, so the silent-vendor red line is
    self-enforcing rather than documentary; (4) exactly one JSON single-shot
    completion on the seat, driven on the gateway-owned private loop under the
    invocation lock; (5) the completion text is parsed as JSON into ``ModelResult.payload``
    — a non-JSON completion yields ``payload=None`` so the executor's
    output-schema validation refuses honestly (``output_schema_invalid``), never
    a fabricated payload.
    """

    def __init__(
        self,
        *,
        payload_reader,
        catalog_runtime: CatalogRuntime,
        config_path: Path | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self._reader = payload_reader
        self._catalog = catalog_runtime
        self._config_path = (
            Path(config_path)
            if config_path is not None
            else _planner_gateway.REPO_LLM_CONFIG_PATH
        )
        self._timeout_override = timeout_sec
        # the loop slot exists before any raising work below, so a construction
        # refusal (unconfigured tier) leaves __del__ -> close() safe.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._invoke_lock = threading.Lock()
        # eager, typed, seat-naming: a tier whose alias is absent/underspecified
        # in the EXPLICIT repo config fails construction, never mid-run.
        self._tier_map = tier_map_from_llm_yaml(
            self._config_path.read_text(encoding="utf-8")
        )

    # -- tier resolution ---------------------------------------------------- #
    @property
    def config_path(self) -> Path:
        return self._config_path

    def seat_for_tier(self, tier: str) -> str:
        """The llm.yaml ``agent_overrides`` seat for one catalog model tier."""
        seat = ORCHESTRATION_TIER_ALIASES.get(tier)
        if seat is None:
            raise ModelTierUnconfigured(
                f"model tier {tier!r} is not in the closed orchestration tier "
                f"vocabulary {tuple(ORCHESTRATION_TIER_ALIASES)}; no llm.yaml "
                "seat exists for it"
            )
        return seat

    def binding_for_tier(self, tier: str) -> ModelTierBinding:
        """The reviewed provider/model binding for one tier (typed on absence)."""
        self.seat_for_tier(tier)  # unknown-tier refusal names the vocabulary
        return self._tier_map.binding_for(tier)

    # -- lifecycle (planner-gateway idiom) ----------------------------------- #
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def close(self) -> None:
        """Tear down the gateway-owned event loop (idempotent; construction-safe)."""
        loop = getattr(self, "_loop", None)
        self._loop = None
        if loop is not None and not loop.is_closed():
            try:
                loop.close()
            except Exception:  # pragma: no cover - defensive teardown
                pass

    def __enter__(self) -> "WorkerSeatModelGateway":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - GC-timing dependent
        self.close()

    # -- the single-shot invocation ------------------------------------------ #
    def invoke(
        self, request: AssembledModelRequest, *, prompt_assembly_ref: TypedPayloadRef
    ) -> ModelResult:
        # (1) rehash-refuse BEFORE any provider work (shared reviewed check).
        record = verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._reader
        )

        # (2) the worker's catalog tier -> the Phase-8 seat (typed refusals).
        worker = self._catalog.worker(record.worker_id)
        execution = worker.execution
        if execution.kind is not ExecutionKind.LLM or execution.model_tier is None:
            raise PipelineAssemblyError(
                f"worker {worker.id!r} is not a catalog LLM worker; the "
                "worker-seat gateway refuses to invoke a provider for it"
            )
        tier = execution.model_tier
        seat = self.seat_for_tier(tier)
        binding = self._tier_map.binding_for(tier)

        # (3) provider messages from the exact authorized canonical bytes —
        # deliberately the planner gateway's own reviewed constructor, so the
        # two production gateways can never drift apart on what reaches a model.
        messages = _planner_gateway._messages_from_canonical(
            request.canonical_request_bytes
        )

        # (4) the engine client on the EXPLICIT repo config path (never the
        # workspace find_config shadow). Imported lazily so this module loads in
        # engine-less envs and the binding refusal above stays provider-free.
        from financial_analyst.llm.client import LLMClient

        client = LLMClient.for_agent(seat, config_path=self._config_path)
        # the seat red line, self-enforcing: the engine re-reads the yaml itself,
        # so a client resolved off the reviewed binding (a default_provider /
        # default_model fallback, or a shadowed config) is refused BY SEAT before
        # a single provider byte — never a silent vendor substitution.
        resolved_provider = getattr(client, "provider", None)
        resolved_model = getattr(client, "model", None)
        if resolved_provider != binding.provider or resolved_model != binding.model:
            raise ModelTierUnconfigured(
                f"worker seat {seat!r}: the engine client resolved "
                f"{resolved_provider!r}/{resolved_model!r}, not the reviewed tier "
                f"binding {binding.provider!r}/{binding.model!r} from the explicit "
                "config path — refusing the silent vendor fallback"
            )
        timeout = float(
            self._timeout_override
            if self._timeout_override is not None
            else (binding.timeout_sec or _DEFAULT_TIMEOUT_SEC)
        )

        # (5) exactly one single-shot completion, serialized on the gateway-owned
        # loop (thread-isolated; see the module docstring's thread discipline).
        with self._invoke_lock:
            loop = self._ensure_loop()
            try:
                response = loop.run_until_complete(
                    asyncio.wait_for(
                        client.chat(
                            messages,
                            response_format={"type": "json_object"},
                            temperature=_WORKER_TEMPERATURE,
                        ),
                        timeout=timeout + _OUTER_WAIT_BUFFER_SEC,
                    )
                )
            except Exception as exc:  # provider/transport/timeout -> typed error
                raise WorkerSeatModelInvocationError(
                    f"worker seat {seat!r} provider invocation failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - shape guard
            raise WorkerSeatModelInvocationError(
                "provider response had no choices[0].message.content"
            ) from exc
        rendered_text = content if isinstance(content, str) else ""
        try:
            payload = json.loads(rendered_text)
        except (ValueError, TypeError):
            payload = None  # executor's output-schema validation refuses honestly

        return ModelResult(
            payload=payload,
            rendered_text=rendered_text,
            input_tokens=int(getattr(client, "total_prompt_tokens", 0) or 0),
            output_tokens=int(getattr(client, "total_completion_tokens", 0) or 0),
            provider=getattr(client, "provider", None),
            model=getattr(client, "model", None),
        )


def production_gateway_factory(
    *, payload_reader, catalog_runtime: CatalogRuntime
) -> WorkerSeatModelGateway:
    """The production ``gateway_factory`` seam value (tests script this seam)."""
    return WorkerSeatModelGateway(
        payload_reader=payload_reader, catalog_runtime=catalog_runtime
    )


# =========================================================================== #
# 3. the composed production plan runner                                       #
# =========================================================================== #
def build_production_plan_runner(
    *,
    stores,
    catalog_snapshot: WorkerCatalogSnapshot,
    registry,
    gateway_factory: Callable[..., Any],
    admission,
    lane_bindings: Mapping[str, Any],
    run_context_factory: Callable[..., Any],
    request_id: str,
    clock,
    runtime_registry_digest: str,
    runtime_limit: int,
    catalog: ProductionCatalogRuntime | None = None,
    material_source: MaterialSource | None = None,
    handler_registry: Mapping[str, Factory] | None = None,
    bridge_analyzers: Mapping[Any, Any] | None = None,
    prompt_assembler=None,
    refusal_sink=None,
    runtime_profile=None,
    recorder_factory: Callable[[], Any] | None = None,
    freeze_idempotency_prefix: str = "replay-freeze",
) -> Callable[..., Any]:
    """Compose the kernel into the launcher's production ``plan_runner``.

    The returned callable IS :func:`build_admitted_plan_runner`'s runner — the
    seam ``(lane, point, approval, data_context, memory_binding,
    candidate_plan_digest)`` — with the two previously-unwired backends bound to
    real assemblies:

    * ``plan_executor``: per admitted plan, ``verify_for_dispatch`` supplies the
      support report; :class:`ExecutionRuntime` is assembled over the ONE
      production catalog bundle; the Plan-scoped :class:`ArtifactPool` is built
      (and ``replay()``-ed, the reviewed resume precondition) and cached per
      plan digest; the :class:`BudgetLedger` folds from the stores' own budget
      event sink; the gateway comes from ``gateway_factory`` (fresh per
      execution — the loop-lifetime hazard recorded at launcher.py:204-223);
      execution itself is the reviewed
      :func:`build_dag_plan_executor` bridge — never a parallel ``run_plan``.
    * ``sink_artifact_resolver``: the launcher's own
      :func:`pool_sink_artifact_resolver` over the same per-plan pool.

    Nothing here approves, admits, or bypasses: an unbound digest, a missing
    approval and the event-loop thread are all refused by the inherited
    launcher/admission red lines.
    """
    if catalog is not None and catalog.catalog_digest != catalog_snapshot.catalog_digest:
        raise PipelineAssemblyError(
            "the pre-assembled catalog bundle's digest "
            f"{catalog.catalog_digest!r} is not the supplied snapshot's "
            f"{catalog_snapshot.catalog_digest!r}; refusing a split-brain catalog identity"
        )
    bundle = (
        catalog
        if catalog is not None
        else build_production_catalog_runtime(
            catalog_snapshot,
            material_source=material_source,
            handler_registry=handler_registry,
        )
    )
    bridge_view = BridgeCatalogView.build(bundle.runtime, dict(bridge_analyzers or {}))
    assembler = (
        prompt_assembler if prompt_assembler is not None else StaticPromptAssembler()
    )
    if refusal_sink is None:
        detail_registry = default_audit_detail_registry()
        detail_registry.seal()
        refusal_sink = EventRefusalAuditSink(detail_registry=detail_registry, clock=clock)

    pools: dict[str, ArtifactPool] = {}

    def _pool_for(plan) -> ArtifactPool:
        pool = pools.get(plan.plan_digest)
        if pool is None:
            pool = ArtifactPool(
                stores=stores,
                registry_digest=runtime_registry_digest,
                plan=plan,
                catalog=catalog_snapshot,
                clock=clock,
            ).replay()  # the reviewed resume precondition (dag.py run_plan docstring)
            pools[plan.plan_digest] = pool
        return pool

    def _plan_executor(*, plan, run_context, lane, point):
        dispatch = admission.verify_for_dispatch(plan.plan_digest)
        runtime = ExecutionRuntime(
            catalog=bundle.runtime,
            bridge_view=bridge_view,
            factories=bundle.factories,
            support_report=dispatch.support_report,
            runtime_registry_digest=runtime_registry_digest,
        )
        pool = _pool_for(plan)
        budget = BudgetLedger(
            sink=stores.budget_event_sink(
                run_id=plan.run_id, ledger_id=run_context.budget.ledger_id
            ),
            run_budget=run_context.budget,
        )
        gateway = gateway_factory(
            payload_reader=stores.payloads, catalog_runtime=bundle.runtime
        )
        executor = build_dag_plan_executor(
            pool=pool,
            budget=budget,
            runtime=runtime,
            registry=registry,
            stores=stores,
            runtime_limit=int(runtime_limit),
            clock=clock,
            admission=admission,
            model_gateway=gateway,
            prompt_assembler=assembler,
            refusal_sink=refusal_sink,
            runtime_profile=runtime_profile,
            recorder_factory=recorder_factory,
        )
        try:
            return executor(plan=plan, run_context=run_context, lane=lane, point=point)
        finally:
            close = getattr(gateway, "close", None)
            if callable(close):
                close()

    def _sink_artifact_resolver(*, plan, run_result, lane, point):
        pool = pools.get(plan.plan_digest)
        if pool is None:
            raise PipelineAssemblyError(
                f"no artifact pool exists for plan {plan.plan_digest!r}; nothing "
                "was executed through this composition for it — refusing to "
                "resolve a sink artifact from thin air"
            )
        return pool_sink_artifact_resolver(pool)(
            plan=plan, run_result=run_result, lane=lane, point=point
        )

    return build_admitted_plan_runner(
        admission=admission,
        lane_bindings=dict(lane_bindings),
        run_context_factory=run_context_factory,
        request_id=request_id,
        plan_executor=_plan_executor,
        sink_artifact_resolver=_sink_artifact_resolver,
        freeze_idempotency_prefix=freeze_idempotency_prefix,
    )


# =========================================================================== #
# PlanPresetRecordV2 — the Phase-10 debate-carrying preset record               #
# =========================================================================== #
class PlanPresetRecordV2(PlanPresetRecord):
    """A reviewed preset that MAY declare one bounded debate (``PlanPresetRecord@2``).

    A strict superset of the Phase-7 v1 record: same identity/budget fields, plus
    ``debates`` / ``reducers`` in the implemented Phase-1 shapes. It subclasses the
    Phase-7 record so the Phase-7 registry's ``isinstance`` gate accepts it, and it
    OVERRIDES the v1 ``_admissible`` validator (which refuses debate identity
    outright). Everything v1 forbids for a non-debate reason stays forbidden:

    * no ``gate_ids`` and no ``condition_ref`` on any node (conditions remain locked
      under the v2 runtime profile; this preset declares no gates at all);
    * **no node params at all** — the structural half of "no plan param carries a
      code". Every worker in this generation of presets is paramsless, so Phase 1
      would refuse node params anyway (``params_not_allowed``, spec.py:951-954);
      refusing them here makes a code-carrying param impossible one layer earlier;
    * ``max_attempts <= 2`` (the v2 profile cap ``max_attempts_limit``).

    The debate rules mirror the implemented runtime checks so a malformed debate
    can never be sealed: every seat node's ``debate_id`` must name a declared
    debate and its ``round_role`` must be one of that debate's seats; every
    debate's ``judge_node_id`` must be a plan node that is NOT a seat of the same
    debate (``debate_judge_is_seat``); ``max_rounds <= DEBATE_MAX_ROUNDS``; and
    every multi-writer slot must carry exactly one ``ReducerCfg`` whose
    ``producer_node_ids`` equal that slot's writers.
    """

    schema_version: Literal["2"] = "2"  # type: ignore[assignment]
    debates: tuple[DebateCfg, ...] = ()
    reducers: tuple[ReducerCfg, ...] = ()

    @model_validator(mode="after")
    def _admissible(self) -> "PlanPresetRecordV2":  # type: ignore[override]
        # (transcribed from the v1 validator rather than delegated: a pydantic
        #  same-name override REPLACES the parent validator, and the v1 body's
        #  debate refusal is exactly what v2 exists to relax.)
        if not self.nodes:
            raise ValueError("a preset must declare at least one node")
        if not self.sink_node_ids:
            raise ValueError("a preset must declare at least one sink node id")

        node_ids = [n.id for n in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("preset node ids must be unique")
        node_by_id = {n.id: n for n in self.nodes}
        for sink in self.sink_node_ids:
            if sink not in node_by_id:
                raise ValueError(f"sink node id {sink!r} is not a preset node")

        for node in self.nodes:
            if node.gate_ids != ():
                raise ValueError(
                    f"preset node {node.id!r} must carry no gate_ids (v2 declares "
                    "no gates)")
            if node.condition_ref is not None:
                raise ValueError(
                    f"preset node {node.id!r} must carry no condition_ref "
                    "(conditions stay locked under the v2 runtime profile)")
            if node.params:
                raise ValueError(
                    f"preset node {node.id!r} must carry no params — a sealed v2 "
                    "preset is structurally code-free")
            if node.max_attempts > 2:
                raise ValueError(
                    f"preset node {node.id!r} max_attempts={node.max_attempts} "
                    "exceeds the v2 profile cap max_attempts_limit=2")

        # -- debate coherence (mirrors the implemented runtime checks) -------- #
        debate_by_id: dict[str, DebateCfg] = {}
        for debate in self.debates:
            if debate.id in debate_by_id:
                raise ValueError(f"duplicate debate id {debate.id!r}")
            debate_by_id[debate.id] = debate
            if debate.max_rounds > DEBATE_MAX_ROUNDS:
                raise ValueError(
                    f"debate {debate.id!r} max_rounds={debate.max_rounds} exceeds "
                    f"the reviewed Lane-D cap DEBATE_MAX_ROUNDS={DEBATE_MAX_ROUNDS}")
            if debate.judge_node_id not in node_by_id:
                raise ValueError(
                    f"debate {debate.id!r} judge_node_id {debate.judge_node_id!r} "
                    "is not a preset node")

        for node in self.nodes:
            if node.debate_id is None:
                continue
            debate = debate_by_id.get(node.debate_id)
            if debate is None:
                raise ValueError(
                    f"preset node {node.id!r} references undeclared debate "
                    f"{node.debate_id!r}")
            if node.round_role not in set(debate.seats):
                raise ValueError(
                    f"preset node {node.id!r} round_role {node.round_role!r} is not "
                    f"a seat of debate {debate.id!r}")

        for debate in self.debates:
            judge = node_by_id[debate.judge_node_id]
            if judge.debate_id == debate.id:
                raise ValueError(
                    f"debate {debate.id!r} judge_node_id {debate.judge_node_id!r} is "
                    "itself a seat of the same debate; the judge must be a distinct "
                    "plan node")

        # -- multi-writer slots must be deterministically reduced ------------- #
        slot_writers: dict[str, list[str]] = {}
        for node in self.nodes:
            slot_writers.setdefault(node.writes_slot, []).append(node.id)
        reducer_by_slot: dict[str, ReducerCfg] = {}
        for reducer in self.reducers:
            if reducer.slot in reducer_by_slot:
                raise ValueError(
                    f"slot {reducer.slot!r} has more than one reducer")
            reducer_by_slot[reducer.slot] = reducer
            writers = sorted(slot_writers.get(reducer.slot, ()))
            if not writers:
                raise ValueError(
                    f"reducer {reducer.id!r} slot {reducer.slot!r} is written by no "
                    "preset node")
            if sorted(reducer.producer_node_ids) != writers:
                raise ValueError(
                    f"reducer {reducer.id!r} producer_node_ids "
                    f"{sorted(reducer.producer_node_ids)} do not equal slot "
                    f"{reducer.slot!r} writers {writers}")
        for slot, writers in slot_writers.items():
            if len(writers) > 1 and slot not in reducer_by_slot:
                raise ValueError(
                    f"slot {slot!r} is written by {sorted(writers)} without a "
                    "deterministic reducer")
        return self


# =========================================================================== #
# the Phase-10 strict loader (v1 + v2 from ONE committed directory)             #
# =========================================================================== #
def _load_record(path: Path, model: type[PlanPresetRecord]) -> PlanPresetRecord:
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
        return model.model_validate_json(text)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise PlanPresetError(
            f"preset file {path.name!r} is not a valid {model.__name__}: {exc}"
        ) from exc


def load_phase10_preset_registry(
    root: Path = PRODUCTION_PRESETS_DIR,
) -> PlanPresetRegistry:
    """Load, validate and seal BOTH preset generations under ``root``.

    ``root/*.json`` are Phase-7 :class:`PlanPresetRecord` v1 files (loaded exactly
    as ``plan_presets.load_preset_registry`` does — strict UTF-8, BOM refused,
    ``extra="forbid"``); ``root/v2/*.json`` are :class:`PlanPresetRecordV2` files.
    A ``preset_id`` duplicated *across either generation* is a
    :class:`PlanPresetError`, so the two directories are one namespace. The
    registry is sealed before it is returned and no physical path ever enters a
    record.

    The split is not cosmetic: the Phase-7 loader's glob is non-recursive, so
    keeping v2 files in the subdirectory is what lets that loader — and every
    existing caller of it — keep working byte-unchanged.
    """
    root = Path(root)
    registry = PlanPresetRegistry()
    seen: set[str] = set()

    def _ingest(paths, model: type[PlanPresetRecord]) -> None:
        for path in sorted(paths):
            record = _load_record(path, model)
            if record.preset_id in seen:
                raise PlanPresetError(
                    f"duplicate preset_id {record.preset_id!r} across preset files")
            seen.add(record.preset_id)
            registry.register(record)

    _ingest(root.glob("*.json"), PlanPresetRecord)
    _ingest((root / PHASE10_PRESETS_V2_DIRNAME).glob("*.json"), PlanPresetRecordV2)
    registry.seal()
    return registry


# =========================================================================== #
# 4. the sealed preset-registry extension                                      #
# =========================================================================== #
def build_phase10_preset_registry(
    phase7_registry: PlanPresetRegistry,
    records: Iterable[PlanPresetRecord] = (),
) -> PlanPresetRegistry:
    """A NEW sealed registry = the verified Phase-7 base + the Phase-10 records.

    The base must be a SEALED :class:`PlanPresetRegistry` that (a) holds the
    committed Phase-7 baseline preset with its exact reviewed content (semantic
    digest equality against the on-disk golden), and (b) holds NOTHING the
    committed ``config/orchestration/presets/`` loader does not hold identically
    — base ⊆ committed directory, record for record by semantic digest. A base
    carrying an extra or mutated record is a typed :class:`PresetBaseRefused`
    NAMING the record: a smuggled base record can never be copied into the
    sealed production registry. The subset shape survives later phases adding
    their own committed preset files (a base holding them still matches). The
    base object is never mutated and the golden file is never touched; a
    Phase-10 record colliding with a base ``preset_id`` under different content
    refuses through the inherited
    :class:`~guanlan_v2.orchestration.plan_presets.PlanPresetError`.

    **Task 6 (Amendment 3) — both preset generations.** "The committed directory"
    now means BOTH ``config/orchestration/presets/*.json`` (Phase-7
    :class:`PlanPresetRecord` v1) and ``config/orchestration/presets/v2/*.json``
    (Phase-10 ``PlanPresetRecordV2``, which carries the debate vocabulary Phase 7
    structurally refuses). The subset seal is therefore evaluated against the
    Phase-10 loader's merged view — v1 semantics are bit-unchanged (the same
    baseline record, the same digest equality), and a committed v2 record is now
    an admissible base member instead of being mistaken for a smuggled one. The
    v2 subdirectory exists because the Phase-7 loader's ``root/*.json`` glob is
    non-recursive: a v2 file beside the baseline would be a hard
    ``PlanPresetError`` for every existing caller of that loader.
    ``PlanPresetRecordV2`` subclasses :class:`PlanPresetRecord`, so ``records``
    may contain either generation and :meth:`PlanPresetRegistry.register`'s
    ``isinstance`` gate accepts both unchanged.
    """

    if not isinstance(phase7_registry, PlanPresetRegistry):
        raise PresetBaseRefused(
            "the extension base must be a PlanPresetRegistry; got "
            f"{type(phase7_registry).__name__}"
        )
    if not phase7_registry.sealed:
        raise PresetBaseRefused(
            "the extension base must be SEALED (the Phase-7 loader seals before "
            "returning); an unsealed registry is not the reviewed Phase-7 base"
        )
    committed = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)
    reviewed = committed.get(PHASE7_BASELINE_PRESET_ID)
    try:
        held = phase7_registry.get(PHASE7_BASELINE_PRESET_ID)
    except Exception as exc:
        raise PresetBaseRefused(
            f"the extension base does not hold the Phase-7 baseline preset "
            f"{PHASE7_BASELINE_PRESET_ID!r}; refusing a non-Phase-7 base"
        ) from exc
    if held.semantic_digest() != reviewed.semantic_digest():
        raise PresetBaseRefused(
            f"the extension base's {PHASE7_BASELINE_PRESET_ID!r} record does not "
            "equal the committed Phase-7 golden (semantic digest mismatch); a "
            "mutated baseline is not the Phase-7 base"
        )
    # base ⊆ committed: EVERY base record must equal the committed loader's
    # record of the same id — a record the directory does not hold (or holds
    # differently) is smuggled, and would otherwise be copied into the sealed
    # production registry below.
    for entry in phase7_registry.manifest():
        try:
            committed_record = committed.get(entry.preset_id)
        except PlanPresetError as exc:
            raise PresetBaseRefused(
                f"the extension base holds {entry.preset_id!r}, which is not a "
                "committed config/orchestration/presets record (neither the v1 "
                "root nor the v2 subdirectory); a smuggled base record never "
                "reaches the sealed production registry"
            ) from exc
        if entry.preset_digest != committed_record.semantic_digest():
            raise PresetBaseRefused(
                f"the extension base's {entry.preset_id!r} record does not equal "
                "the committed reviewed record (semantic digest mismatch); a "
                "mutated base record never reaches the sealed production registry"
            )

    extended = PlanPresetRegistry()
    for entry in phase7_registry.manifest():
        extended.register(phase7_registry.get(entry.preset_id))
    for record in tuple(records):
        extended.register(record)
    extended.seal()
    return extended
