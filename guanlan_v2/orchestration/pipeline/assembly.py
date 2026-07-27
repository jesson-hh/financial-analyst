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
import dataclasses
import json
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

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
from guanlan_v2.orchestration.refs import ContentRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import default_audit_detail_registry
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
    "PipelineAssemblyError",
    "PresetBaseRefused",
    "WorkerSeatModelInvocationError",
    "ProductionCatalogRuntime",
    "WorkerSeatModelGateway",
    "production_material_source",
    "build_production_catalog_runtime",
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

#: the Phase-7 reviewed baseline preset — the extension mechanism's base anchor.
PHASE7_BASELINE_PRESET_ID = "main.research_baseline"

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

    Today's universe is the pilot-lineage loader (the only cumulative catalog a
    production pipeline runs, and the one the Task-0b e2e proof exercises). A
    later phase-10 catalog brings its own reviewed loader and passes
    ``material_source=`` explicitly — this function is deliberately NOT a place
    to guess at bytes for materials whose loaders live elsewhere.
    """
    text: dict[tuple[str, str], Any] = {}
    caps: dict[tuple[str, str], Any] = {}
    pilot = load_pilot_catalog()
    pilot_text, pilot_caps = pilot.resolved_materials()
    for material in pilot_text:
        text.setdefault((material.ref.id, material.ref.version), material)
    for material in pilot_caps:
        caps.setdefault((material.ref.id, material.ref.version), material)
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
    committed = load_preset_registry(PRODUCTION_PRESETS_DIR)
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
                "committed config/orchestration/presets record; a smuggled base "
                "record never reaches the sealed production registry"
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
