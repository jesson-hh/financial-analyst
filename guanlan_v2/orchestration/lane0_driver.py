# -*- coding: utf-8 -*-
"""L2-a — the Lane-0 production driver: the thing that makes a real run possible.

The hole this closes
--------------------
Every orchestration run downstream of Lane 0 — the deep 落子 chain, the screening
chain, any main-phase plan — binds a committed ``ContextSnapshot``, and Lane 0 is
its only producer. On 2026-07-29 a live deep-chain run through the production
assembly got everything right (14 store namespaces bound, a real quote, the fast
lane really calling an LLM, escalation legitimately fired, the reduced preset's
support check GREEN) and still refused honestly with ``no committed
ContextSnapshot``: ``var/orchestration/`` held one hand-issued lease and nothing
else. **Lane 0 had never run in production, and nothing in the repository could
drive it.** ``tests/orchestration/test_bootstrap_e2e.py`` drives the whole chain
with fake gateways and in-memory stores; the production launcher
(``GUANLAN_ORCH_LAUNCHER``) binds only the replay/shadow router. This module is
the missing driver, and it is composition only — every load-bearing piece below
already existed and is reused, never re-implemented:

===============================  =========================================
reviewed piece                   owner
===============================  =========================================
PIT input loading                ``market/factors.load_market_factor_inputs``
preset + draft + run context     ``bootstrap.build_bootstrap_plan{,_draft}`` /
                                 ``bootstrap.build_bootstrap_run_context``
deterministic factor handler     ``bootstrap.register_bootstrap_runtime_factories``
snapshot + honest manifest       ``bootstrap.build_context_snapshot_from_bootstrap``
catalog / bridge / registry      ``pipeline.assembly.build_production_catalog_runtime``
                                 / ``production_bridge_view`` /
                                 ``adapters.chain.build_phase9_registry``
the real model gateway           ``pipeline.assembly.production_gateway_factory``
                                 (``WorkerSeatModelGateway``)
execution                        ``adapters.launcher.build_dag_plan_executor``
admission                        ``admission.PlanAdmissionService``
approval                         ``approval.PlanApprovalCoordinator`` over the
                                 durable ``var/orchestration/plan_approvals.jsonl``
===============================  =========================================

Four red lines, each structural rather than documentary
-------------------------------------------------------
1. **Never self-bind the stores.** :func:`build_production_lane0_bindings`
   consults the R23/R24 status record and raises :class:`Lane0StoresUnbound`
   when it is not ``bound`` — the exact posture of the deep lane's
   ``ProductionStoresUnbound``. ``bind_process_durable_stores_and_scan`` is
   idempotent-once and freezes its cell-namespace allowlist at construction, so
   a driver-side rebind would either no-op against a broken binding or starve
   every other durable-store consumer. A source sweep in the focused suite pins
   that no LIBRARY function names a binder. The one exception is deliberate and
   lives only in the CLI layer: a standalone ``python -m`` process has no startup
   of its own, so ``--bind-stores`` (off by default) lets that process perform
   the reviewed ``startup.bind_orchestration_stores`` once, first — the same call
   ``server.py`` makes. Inside the 9999 server the flag is never used and the
   library refusal is the only behaviour.
2. **Never self-approve.** The driver LOADS a terminal decision somebody else
   recorded; it constructs no :class:`~guanlan_v2.orchestration.events.PlanApproval`
   (pinned by a source sweep) and the coordinator
   :class:`RecordedApprovalAuthorization` builds carries ``verifier=None``, so
   ``decide`` / ``issue_lease`` are fail-closed refusals rather than a path a
   later edit could reach. Recording a decision is a SEPARATE, verifier-backed
   entry point (:func:`approve_lane0_candidate`, the CLI ``approve`` verb) that
   never runs a plan.
3. **Idempotent per +08:00 session date.** Run identity is ``lane0-<session>``,
   so today's committed snapshot is found by exact ``snapshot_id`` and returned
   as ``reused`` — never a second token burn. There is deliberately NO ``force``:
   ``dag.run_plan`` RESUMES a run id whose layers are committed rather than
   re-executing them, so a same-session "force" could not produce a second
   judgment even if it were offered; it would only crash in
   ``ArtifactPool.replay()`` (artifact envelopes are process-local upstream while
   ``LayerCommitted`` events are durable). That crash is itself detected and
   turned into the named ``partial_prior_run`` refusal.
4. **Never fabricate data.** ``load_market_factor_inputs`` returning ``None``
   fields is its documented honest steady state (the snapshot archive is young,
   the ``rot.*`` cross-sectional compute is deferred); an all-absent load yields
   a DEGRADED report, ``factor_degraded`` badges carried into the manifest and
   into :class:`Lane0RunResult` — never a zero.

Recorded ABI corrections (the Task-0b/6 precedent)
--------------------------------------------------
* The charter names ``assembly.build_production_plan_runner`` as the execution
  seam. It is the wrong shape for Lane 0 in two ways, both load-bearing: its
  runner returns only the FIRST SINK's artifact (Lane 0's product is the
  ``RunResult`` + all three committed artifacts, and
  ``build_context_snapshot_from_bootstrap`` needs the ``RunResult``), and it
  REFUSES any terminal status outside the usable set and any absent sink
  artifact — whereas a Lane-0 run whose ``lane0.regime`` node failed must still
  commit a ``regime_missing``-badged snapshot (``test_bootstrap_e2e``'s reviewed
  semantics). The driver therefore composes the SAME internals that builder
  composes — :func:`~guanlan_v2.orchestration.adapters.launcher.build_dag_plan_executor`
  over an :class:`~guanlan_v2.orchestration.worker.ExecutionRuntime` +
  :class:`~guanlan_v2.orchestration.pool.ArtifactPool` +
  :class:`~guanlan_v2.orchestration.budget.BudgetLedger` + the gateway from
  ``gateway_factory`` — and freezes through the admission service directly, the
  way ``test_bootstrap_e2e`` does. The gateway factory remains the ONLY
  test/production difference.
* Lane 0 has **no sealed** :class:`~guanlan_v2.orchestration.plan_presets.PlanPresetRecord`
  (its preset is the ``BootstrapPlan`` record, a different contract), and
  :class:`~guanlan_v2.orchestration.plan_diff.PendingPlanApproval` admits only
  ``DYNAMIC`` (no preset provenance) or ``PRESET_FALLBACK`` (both provenance
  fields required). The Lane-0 card is therefore ``DYNAMIC``, which states the
  literal truth — *no sealed preset record backs this card* — and has the
  deliberate consequence that ``_find_admissible_lease`` can never match it:
  **Lane 0 is approvable by a named human only, never by a standing lease.** The
  ``rendered_md`` banner names the bootstrap preset version so a reviewer is
  never left inferring it.
* The only reviewed :class:`~guanlan_v2.orchestration.context.DataContext`
  producer that needs no data-snapshot manifest chain is
  ``presets.pilot_data_context`` (``data.snapshot.build_data_context`` requires a
  frozen config/registry/routing/manifest quartet that has no production
  producer). The driver uses it and says so in the result's ``notes`` — the
  placeholder source/routing digests are an upstream gap, not something to
  invent here. It is the same ``DataContext`` the deep lane consumes today.

Out of scope, deliberately: nothing here touches ``server.py`` and no scheduler
is wired. Driving Lane 0 stays a decision an operator makes, not a side effect
of a restart.

CLI::

    python -m guanlan_v2.orchestration.lane0_driver status
    python -m guanlan_v2.orchestration.lane0_driver propose
    python -m guanlan_v2.orchestration.lane0_driver approve --digest <candidate>
    python -m guanlan_v2.orchestration.lane0_driver run
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from guanlan_v2.orchestration import bootstrap as _bootstrap
from guanlan_v2.orchestration import presets as _presets
from guanlan_v2.orchestration import worker as _worker
from guanlan_v2.orchestration.admission import ApprovalSubmission
from guanlan_v2.orchestration.adapters.launcher import build_dag_plan_executor
from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.catalog_runtime import TrustedFactoryRegistry
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.enums import ApprovalDecision, ApprovalPolicy, DataMode, PlanSource
from guanlan_v2.orchestration.eventstore import EventRefusalAuditSink
from guanlan_v2.orchestration.market.factors import (
    MarketFactorInputs,
    _session_date as session_date_of,
    build_market_factor_set_v1,
)
from guanlan_v2.orchestration.memory.experience import (
    ExperienceScalerSnapshot,
    RegimeGraderSpec,
)
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PendingPlanApproval,
    build_plan_diff,
    render_plan_diff_md,
)
from guanlan_v2.orchestration.pool import ArtifactPool
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.runtime_contracts import default_audit_detail_registry

__all__ = [
    "LANE0_DRIVER_ID",
    "LANE0_RUNTIME_PROFILE",
    "LANE0_REGIME_GRADER",
    "LANE0_SCALER_VERSION",
    "PROVIDER_URI_ENV",
    "ARCHIVE_DIR_ENV",
    "OUTCOME_COMPLETED",
    "OUTCOME_DEGRADED",
    "OUTCOME_DRY_RUN",
    "OUTCOME_REUSED",
    "Lane0DriverError",
    "Lane0StoresUnbound",
    "Lane0Unauthorized",
    "Lane0Refused",
    "Lane0Grant",
    "RecordedApprovalAuthorization",
    "Lane0Bindings",
    "Lane0Candidate",
    "Lane0RunResult",
    "InputAvailability",
    "describe_input_availability",
    "production_provider_uri",
    "load_lane0_inputs",
    "build_lane0_bindings",
    "build_production_lane0_bindings",
    "propose_lane0_candidate",
    "approve_lane0_candidate",
    "run_lane0_bootstrap",
    "render_run_report",
    "build_arg_parser",
    "main",
]

_LOG = logging.getLogger("guanlan.orchestration.lane0_driver")

#: the driver's own identity, stamped on the reviewer card and the CLI report.
LANE0_DRIVER_ID = "orchestration.lane0_driver"

#: the reviewed Lane-0 runtime profile (re-export, never a second definition).
LANE0_RUNTIME_PROFILE = _bootstrap.BOOTSTRAP_RUNTIME_PROFILE

#: the reviewed regime grader the Lane-0 preset binds — the exact thresholds the
#: bootstrap end-to-end suite proves against. It contributes only the preset's
#: ``grader_digest`` (the grading horizon for a future ``RegimeCase``), so a
#: change here is a new preset identity and a fresh human approval, by design.
LANE0_REGIME_GRADER: RegimeGraderSpec = RegimeGraderSpec.build(
    grader_version="regime-grader-v1", horizon_trading_days=20,
    benchmark_id="eqw_all_a", bull_min_return=0.03, bear_max_return=-0.03,
    risk_off_max_drawdown=-0.05, risk_on_min_return=0.02, risk_on_min_drawdown=-0.03)

#: the experience scaler identity a cold Lane 0 runs under. There is no fitted
#: production scaler and no reviewed experience view source, so retrieval is a
#: genuine cold start (zero neighbours, ``cold_start:0_neighbours`` on the
#: selection). The empty snapshot is the canonical honest value — NOT a stand-in
#: for numbers we do not have.
LANE0_SCALER_VERSION = "lane0-cold-start-v1"

#: ops seams (never a path inside any contract object).
PROVIDER_URI_ENV = "GUANLAN_ORCH_PROVIDER_URI"
ARCHIVE_DIR_ENV = "GUANLAN_ORCH_ARCHIVE_DIR"

#: the honest outcome vocabulary of one driver invocation.
OUTCOME_COMPLETED = "completed"
OUTCOME_DEGRADED = "degraded"
OUTCOME_DRY_RUN = "dry_run"
OUTCOME_REUSED = "reused"

_CONTEXT_SNAPSHOT_SR = SchemaRef(name="ContextSnapshot", version="1")
_LANE0_NODE_IDS = ("lane0.factor", "lane0.regime", "lane0.rotation")


# =========================================================================== #
# typed refusals                                                               #
# =========================================================================== #
class Lane0DriverError(Exception):
    """The Lane-0 driver refused (never a silent skip, never a fabricated run)."""


class Lane0StoresUnbound(Lane0DriverError):
    """The process has no healthy orchestration durable store (typed refusal).

    Mirrors the deep lane's ``ProductionStoresUnbound`` exactly, for the same
    reason: ``bind_process_durable_stores_and_scan`` runs once per process from
    :mod:`guanlan_v2.orchestration.startup` and freezes the cell-namespace
    allowlist at construction, so a driver-side rebind would be either a silent
    no-op over a broken binding or a one-namespace allowlist that starves every
    other consumer. Fix the startup binding and re-run.
    """


class Lane0Unauthorized(Lane0DriverError):
    """No reviewed authorization exists for this candidate — nothing is run."""


class Lane0Refused(Lane0DriverError):
    """A pre-execution invariant refused (``.code`` names which)."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


# =========================================================================== #
# the authorization channel                                                    #
# =========================================================================== #
@dataclass(frozen=True)
class Lane0Grant:
    """The proof the driver was HANDED an authorization (it mints none)."""

    candidate_plan_digest: str
    actor_id: str
    approval_event_id: str
    channel: str
    decided_at: datetime


class RecordedApprovalAuthorization:
    """The production authorization: a decision a HUMAN already recorded.

    Folds the durable plan-approval journal
    (``var/orchestration/plan_approvals.jsonl``, the same file the reviewed
    Phase-7 coordinator owns), looks up the terminal decision for the exact
    candidate digest the driver computed, and refuses when there is none or when
    it is a rejection. It records nothing: the coordinator it builds is
    constructed with ``verifier=None``, so ``decide`` and ``issue_lease`` are
    fail-closed refusals — this object is structurally incapable of approving
    anything (pinned by the focused suite).

    Handing the loaded decision on to the admission service is the reviewed
    Phase-2 path: the decision is deposited into the service-owned approvals
    mapping and ``record_approval`` LOADS it from there (the service never
    trusts a caller-carried authority record).
    """

    def __init__(self, *, clock: Any, journal_path: Path | str | None = None) -> None:
        self._clock = clock
        self._journal_path = Path(journal_path) if journal_path is not None else None

    @property
    def journal_path(self) -> Path | None:
        return self._journal_path

    def coordinator_for(self, *, admission: Any, approvals: Any) -> Any:
        """The verifier-FREE coordinator this authorization reads through."""
        from guanlan_v2.orchestration.adapters.api import build_plan_approval_coordinator

        return build_plan_approval_coordinator(
            admission=admission, clock=self._clock, verifier=None,
            approvals=approvals, journal_path=self._journal_path)

    def grant(self, *, admission, request, candidate_plan_digest, approvals, now) -> Lane0Grant:
        coordinator = self.coordinator_for(admission=admission, approvals=approvals)
        decision = coordinator.load_decision(request.request_id, candidate_plan_digest)
        if decision is None:
            raise Lane0Unauthorized(
                "no recorded PlanApproval exists for request "
                f"{request.request_id!r} / candidate {candidate_plan_digest} in "
                f"{_coordinator_journal_hint(coordinator)}. The driver never approves "
                "its own run: record a decision first "
                "(python -m guanlan_v2.orchestration.lane0_driver approve --digest "
                f"{candidate_plan_digest}).")
        if decision.decision is not ApprovalDecision.APPROVED:
            raise Lane0Unauthorized(
                f"the recorded decision for candidate {candidate_plan_digest} is "
                f"{decision.decision.value!r}, not approved; nothing is run.")
        # the reviewed Phase-2 authority path: deposit, then let the service LOAD it.
        approvals[(request.request_id, candidate_plan_digest)] = decision
        event = admission.record_approval(
            candidate_plan_digest,
            ApprovalSubmission(
                request_id=request.request_id,
                candidate_plan_digest=candidate_plan_digest,
                decision=ApprovalDecision.APPROVED),
            authenticated_actor=decision.actor_id,
            idempotency_key=f"lane0-approve:{candidate_plan_digest}")
        return Lane0Grant(
            candidate_plan_digest=candidate_plan_digest, actor_id=decision.actor_id,
            approval_event_id=event.event_id, channel="recorded_approval",
            decided_at=decision.decided_at)


def _coordinator_journal_hint(coordinator: Any) -> str:
    """The journal a coordinator reads, for an operator-facing message."""
    return str(getattr(coordinator, "_path", "the plan-approval journal"))


# =========================================================================== #
# PIT inputs — honest absence, never a zero                                    #
# =========================================================================== #
@dataclass(frozen=True)
class InputAvailability:
    """Which PIT series actually arrived, series by series."""

    present: tuple[str, ...]
    absent: tuple[str, ...]

    @property
    def degraded(self) -> bool:
        """``True`` when not one series arrived (every factor UNAVAILABLE)."""
        return not self.present

    def badge(self) -> str | None:
        if self.degraded:
            return "pit_inputs_all_absent"
        if self.absent:
            return "pit_inputs_partial"
        return None


def describe_input_availability(inputs: MarketFactorInputs) -> InputAvailability:
    """Report which ``MarketFactorInputs`` fields carry data (pure, no I/O)."""
    present: list[str] = []
    absent: list[str] = []
    for name in type(inputs).model_fields:
        value = getattr(inputs, name)
        (present if value else absent).append(name)
    return InputAvailability(present=tuple(present), absent=tuple(absent))


def production_provider_uri() -> tuple[str, str]:
    """``(provider_uri, source)`` for the daily bar root — never a guessed path.

    Resolution order, most explicit first:

    1. the ``GUANLAN_ORCH_PROVIDER_URI`` ops override;
    2. ``config.PROVIDER_URI_MAP['day']`` — the source every production reader
       uses (``strategy/vendor/v4_ranking.py``, ``seats/fm_backfill.py``). Those
       run inside the ``G:/stocks`` environment where that module is importable;
       inside THIS repo ``import config`` resolves to the repo's own
       ``config/`` package (orchestration materials), which carries no such
       attribute — so this step is attempted and honestly skipped rather than
       silently assumed;
    3. ``strategy.compute.regen.DEFAULT_PROVIDER`` — the in-repo reviewed
       constant carrying the same value, used by the artifact regenerator.

    The chosen source travels into :class:`Lane0RunResult` so a reader never has
    to guess which one answered.
    """
    import os

    override = (os.environ.get(PROVIDER_URI_ENV) or "").strip()
    if override:
        return override, PROVIDER_URI_ENV
    try:
        import config as _cfg  # noqa: PLC0415 — optional, environment-dependent

        mapping = getattr(_cfg, "PROVIDER_URI_MAP", None)
        if isinstance(mapping, Mapping) and mapping.get("day"):
            return str(mapping["day"]), "config.PROVIDER_URI_MAP['day']"
    except Exception:  # noqa: BLE001 — an absent/shadowed config is not a failure
        pass
    from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER

    return str(DEFAULT_PROVIDER), "strategy.compute.regen.DEFAULT_PROVIDER"


def load_lane0_inputs(
    *, as_of: datetime, provider_uri: str, archive_dir: str | None = None,
) -> MarketFactorInputs:
    """The production PIT load, straight through the reviewed loader.

    Delegates entirely to
    :func:`~guanlan_v2.orchestration.market.factors.load_market_factor_inputs`,
    whose ``None`` fields are its documented honest steady state (young snapshot
    archive; deferred ``rot.*`` compute). This wrapper adds exactly one thing —
    the ``end`` day derived from the +08:00 session of ``as_of`` — and NO
    fallback, NO substitution and no zero fill. A loader-level failure surfaces
    as absent series, which the battery renders UNAVAILABLE and the run reports
    DEGRADED.
    """
    from guanlan_v2.orchestration.market import factors as _factors

    return _factors.load_market_factor_inputs(
        provider_uri=provider_uri, end=session_date_of(as_of), as_of=as_of,
        archive_dir=archive_dir)


# =========================================================================== #
# bindings                                                                     #
# =========================================================================== #
@dataclass(frozen=True)
class Lane0Bindings:
    """Everything one Lane-0 run is allowed to reach.

    A plain frozen dataclass (never a ContractModel). ``gateway_factory`` is the
    ONLY field that differs between production and tests — production binds
    ``assembly.production_gateway_factory`` (the real
    :class:`~guanlan_v2.orchestration.pipeline.assembly.WorkerSeatModelGateway`
    over the repo's ``config/llm.yaml`` seats); tests bind a scripted gateway
    through the same seam.
    """

    stores: Any
    catalog_snapshot: Any
    catalog_runtime: Any
    bridge_view: Any
    registry: Any
    runtime_registry_digest: str
    clock: Any
    gateway_factory: Callable[..., Any]
    spec: Any
    grader: Any
    provider_uri: str
    provider_uri_source: str
    archive_dir: str | None = None
    experience_views: tuple = ()
    runtime_limit: int = 3


def build_lane0_bindings(
    *,
    stores: Any,
    clock: Any,
    gateway_factory: Callable[..., Any] | None = None,
    provider_uri: str | None = None,
    archive_dir: str | None = None,
) -> Lane0Bindings:
    """Assemble the Lane-0 bindings over ALREADY-BOUND stores.

    Shared by production and tests: the sealed Phase-9 cumulative catalog + its
    verified :class:`~guanlan_v2.orchestration.catalog_runtime.CatalogRuntime`,
    the full three-analyzer bridge view, and the cumulative Phase-9 schema
    registry (which holds every Lane-0 contract — ``MarketFactorReport@1`` /
    ``RegimeReport@1`` / ``RotationReport@1`` / ``ExperienceSelection@1`` /
    ``BootstrapContextManifest@1`` / ``ContextSnapshot@1``). The registry is
    registered into the stores' resolver (idempotent) so every payload write
    below names a resolvable digest.

    This function NEVER binds the process stores; it is handed them.
    """
    import os

    from guanlan_v2.orchestration.adapters.chain import (
        PHASE9_BASE_REGISTRY_DIGEST,
        build_phase9_registry,
        phase9_catalog_snapshot,
    )
    from guanlan_v2.orchestration.pipeline.assembly import (
        build_production_catalog_runtime,
        production_bridge_view,
        production_gateway_factory,
    )

    registry = build_phase9_registry(PHASE9_BASE_REGISTRY_DIGEST)
    rt_digest = stores.resolver.register(registry)
    snapshot = phase9_catalog_snapshot()
    bundle = build_production_catalog_runtime(snapshot)
    view = production_bridge_view(bundle.runtime)

    if provider_uri is None:
        provider_uri, source = production_provider_uri()
    else:
        source = "caller"
    if archive_dir is None:
        archive_dir = (os.environ.get(ARCHIVE_DIR_ENV) or "").strip() or None

    return Lane0Bindings(
        stores=stores, catalog_snapshot=snapshot, catalog_runtime=bundle.runtime,
        bridge_view=view, registry=registry, runtime_registry_digest=rt_digest,
        clock=clock, gateway_factory=gateway_factory or production_gateway_factory,
        spec=build_market_factor_set_v1(), grader=LANE0_REGIME_GRADER,
        provider_uri=provider_uri, provider_uri_source=source,
        archive_dir=archive_dir)


def build_production_lane0_bindings(
    *, gateway_factory: Callable[..., Any] | None = None,
) -> Lane0Bindings:
    """The production bindings — or a typed refusal when the store is unbound.

    The ONE gate before assembly is the R23/R24 process durable-store binding.
    The driver never self-binds (see the module docstring's red line 1): a state
    other than ``bound`` raises :class:`Lane0StoresUnbound` naming the state and
    pointing at ``GET /orchestration/store_status``.
    """
    from guanlan_v2 import orch_store_status as _status
    from guanlan_v2.orchestration.adapters.durable import process_durable_stores
    from guanlan_v2.orchestration.runtime_clock import SystemClock

    stores = process_durable_stores()
    if stores is None or not _status.orchestration_store_bound():
        raise Lane0StoresUnbound(
            "the process orchestration durable-store binding is "
            f"{_status.orchestration_store_state()!r} (only 'bound' is healthy); "
            "the Lane-0 driver never self-binds — fix the startup binding "
            "(guanlan_v2.orchestration.startup / GET /orchestration/store_status) "
            "and re-run. Nothing was executed.")
    return build_lane0_bindings(stores=stores, clock=SystemClock(),
                                gateway_factory=gateway_factory)


# =========================================================================== #
# results                                                                      #
# =========================================================================== #
@dataclass(frozen=True)
class Lane0Candidate:
    """One prepared (not yet reserved, not yet approved) Lane-0 candidate."""

    session_date: str
    as_of: datetime
    request: Any
    draft: Any
    preparation: Any
    pending_card: PendingPlanApproval
    candidate_plan_digest: str
    supported: bool
    support_issue_codes: tuple[str, ...]
    registered: bool = False
    attempt: int = 1


@dataclass(frozen=True)
class Lane0RunResult:
    """The observable outcome of one driver invocation (charter §7)."""

    outcome: str
    session_date: str
    as_of: datetime
    run_id: str
    request_id: str
    draft_id: str
    candidate_plan_digest: str | None = None
    plan_digest: str | None = None
    terminal_status: str | None = None
    node_statuses: Mapping[str, str] = field(default_factory=dict)
    llm_invocations: int = 0
    settled_tokens: int = 0
    snapshot_id: str | None = None
    snapshot_digest: str | None = None
    snapshot_ref: PayloadRef | None = None
    manifest_digest: str | None = None
    manifest_context_snapshot_digest: str | None = None
    degradation_badges: tuple[str, ...] = ()
    input_availability: InputAvailability | None = None
    provider_uri: str | None = None
    provider_uri_source: str | None = None
    reused: bool = False
    snapshot_visible_to_deep_lane: bool = False
    approval: Lane0Grant | None = None
    support_issue_codes: tuple[str, ...] = ()
    refusal_detail: str | None = None
    notes: tuple[str, ...] = ()


# =========================================================================== #
# internal helpers (pure where they can be)                                    #
# =========================================================================== #
def _lane0_identity(session_date: str, attempt: int = 1) -> tuple[str, str, str]:
    """``(run_id, draft_id, request_id)`` — deterministic per session date.

    Determinism is what makes the whole flow work across processes: the operator
    approves a candidate digest in one invocation and a later ``run`` recomputes
    exactly the same digest to look that decision up.

    ``attempt`` (default 1, the plain identity) is the documented supersede for a
    **spent** identity — one whose durable ``RunResult`` already exists, which the
    kernel can never rewrite (``runresult:{run_id}:payload`` is a once-only
    idempotency key). Attempt ``N ≥ 2`` suffixes every id with ``-r{N}``, so it is
    a genuinely different plan with a **different candidate digest** and therefore
    requires its own human approval. It is not a ``force``: a session date that
    already produced a ContextSnapshot is still reused, never re-burned.
    """
    attempt = int(attempt)
    if attempt < 1:
        raise Lane0Refused(f"attempt must be >= 1, got {attempt}", code="bad_attempt")
    suffix = "" if attempt == 1 else f"-r{attempt}"
    return (f"lane0-{session_date}{suffix}", f"plan-lane0-{session_date}{suffix}",
            f"req-lane0-{session_date}{suffix}")


def _session_as_of(session_date: str) -> datetime:
    """The session's canonical whole-day artifact stamp (+08:00 midnight).

    **Why the plan does not carry the wall clock.** ``compute_candidate_plan_digest``
    hashes the draft, and the draft carries ``as_of`` — so a wall-clock ``as_of``
    would give every invocation a DIFFERENT candidate digest, and the
    propose → approve → run flow could never agree on one plan (measured on the
    real box: two invocations 90 seconds apart produced two digests and the second
    reservation died on ``IdempotencyConflict``). The plan therefore stamps the
    SESSION, using the same convention the deep lane's ``RunSubject.as_of`` uses
    (``pipeline.candidates.session_date_to_utc``); the PIT window keeps the real
    wall clock, because "what was knowable at the instant the battery ran" is a
    measurement and must never be rounded to a nicer number.
    """
    from guanlan_v2.orchestration.pipeline.candidates import session_date_to_utc

    return session_date_to_utc(session_date)


def _snapshot_object_id(run_id: str) -> str:
    """The assembled Lane-0 ContextSnapshot's stable ``snapshot_id``.

    ``build_context_snapshot_from_bootstrap`` seals it as
    ``bootstrap-ctx-{run_id}``; the input-side snapshot ``dag.run_plan``
    synthesizes for the frozen InputSnapshots is ``bootstrap-run-ctx-{run_id}``.
    Two distinct ``ContextSnapshot@1`` payloads per run is upstream behaviour;
    the driver names the assembled one exactly so idempotence never mistakes the
    other for it.
    """
    return f"bootstrap-ctx-{run_id}"


def _scan_snapshots(stores: Any):
    """Yield ``(payload_ref, model)`` for every committed ``ContextSnapshot@1``.

    Reads the payload backend directly, exactly as the deep lane's
    ``live_decide._latest_snapshot_production`` must (no reviewed listing
    accessor exists upstream — Task 6 finding). Any doubt yields nothing.
    """
    try:
        backend = stores._shared.backend  # noqa: SLF001 — no public listing exists
        for object_id, stored in dict(backend.payloads).items():
            if getattr(stored, "schema_key", None) != _CONTEXT_SNAPSHOT_SR.key:
                continue
            yield (PayloadRef(namespace=stored.namespace, object_id=object_id,
                              content_digest=stored.content_digest), stored.model)
    except Exception as exc:  # noqa: BLE001 — an unreadable backend is an absence
        _LOG.warning("ContextSnapshot scan failed: %s", exc)


def _committed_snapshot_for_session(stores: Any, session_date: str):
    """Any assembled Lane-0 snapshot for this +08:00 session date, or ``None``.

    One judgment per session date is the red line, and it must hold ACROSS
    attempts: ``--attempt 2`` exists to finish a day that produced nothing, never
    to buy a second token burn on a day that already has a snapshot. Attempt 1's
    id is the bare ``bootstrap-ctx-lane0-{date}``; later attempts suffix ``-r{N}``.
    """
    base = _snapshot_object_id(f"lane0-{session_date}")
    for ref, model in _scan_snapshots(stores):
        sid = getattr(model, "snapshot_id", None)
        if sid == base or (isinstance(sid, str) and sid.startswith(f"{base}-r")):
            return model, ref
    return None


#: the durable event types that mean "this run identity has been spent".
#: ``LayerCommitted`` — a prior attempt executed at least one layer; its artifact
#: ENVELOPES live in the process-local ``pool._PoolStorage`` while the events are
#: durable, so a fresh pool's ``replay()`` raises ``PoolError``.
#: The three terminal ``Run*`` events — a durable ``RunResult`` payload already
#: exists under the once-only ``runresult:{run_id}:payload`` idempotency key, so a
#: second run of the same identity dies with ``IdempotencyConflict`` at the very
#: end, AFTER burning its tokens. That is exactly how the 2026-07-29 live run
#: died: a `RunCancelled` from an earlier attempt, no committed layer at the time,
#: so the layer-only scan below let the run proceed.
_SPENT_IDENTITY_EVENTS: tuple[str, ...] = (
    "LAYER_COMMITTED", "RUN_COMPLETED", "RUN_FAILED", "RUN_CANCELLED")


def _spent_run_identity(stores: Any, run_id: str) -> str | None:
    """The name of the durable evidence that this run identity is spent, or ``None``.

    Returns the event type (``"RunCancelled"``, ``"LayerCommitted"``, …) so the
    refusal can name what it actually found rather than assert a category.
    """
    from guanlan_v2.orchestration.events import EventType

    spent = {getattr(EventType, name) for name in _SPENT_IDENTITY_EVENTS}
    try:
        for ev in stores.events.journal(run_id, "main"):
            if ev.event_type in spent:
                return ev.event_type.value
    except Exception as exc:  # noqa: BLE001 — an unreadable journal is not a claim
        _LOG.warning("run-identity scan failed for %s: %s", run_id, exc)
    return None


def _run_identity_already_executed(stores: Any, run_id: str) -> bool:
    """Has this run identity already been spent (see :func:`_spent_run_identity`)?"""
    return _spent_run_identity(stores, run_id) is not None


def _supersede_hint(attempt: int) -> str:
    """The one actionable sentence a spent identity owes the operator."""
    nxt = int(attempt) + 1
    return (
        f"To produce today's judgment anyway, supersede with a FRESH identity: "
        f"re-run `propose --attempt {nxt}`, have the reviewer approve the digest it "
        f"prints, then `run --attempt {nxt}` (a different identity is a different "
        f"plan, so it needs its own human approval — it is not a force). A session "
        f"date that already committed a ContextSnapshot is still reused, never "
        f"re-burned. Otherwise wait for the next session date.")


def _deep_lane_sees(stores: Any, snapshot_digest: str) -> bool:
    """Does the deep lane's OWN reader hand back this exact snapshot?"""
    from guanlan_v2.orchestration.pipeline.live_decide import _latest_snapshot_production

    pair = _latest_snapshot_production(stores)
    return bool(pair) and pair[0].content_digest == snapshot_digest


def _lane0_scaler(as_of: datetime, spec: Any) -> ExperienceScalerSnapshot:
    """The canonical cold-start scaler snapshot (zero observations, honestly)."""
    return ExperienceScalerSnapshot.build(
        feature_schema_version=spec.feature_schema_version,
        scaler_version=LANE0_SCALER_VERSION, fit_as_of=as_of, n_obs=0, mu={}, sd={})


def _resolve_as_of(as_of: datetime | None, clock: Any) -> datetime:
    now = as_of if as_of is not None else clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise Lane0Refused(
            f"the Lane-0 driver needs an aware as_of; got {now!r}. A naive stamp "
            "would silently shift the +08:00 session date.", code="naive_clock")
    return now


def _render_card_markdown(*, preset, draft, session_date: str, diff) -> str:
    """The reviewer's card body: what this run IS, above the plan diff."""
    return (
        f"# Lane 0 · 市场情境层 bootstrap ({session_date})\n\n"
        f"- preset: `BootstrapPlan@{preset.preset_version}` "
        f"(factor_set `{preset.factor_set_version}`, "
        f"factor_set_digest `{preset.factor_set_digest[:12]}`, "
        f"grader_digest `{preset.grader_digest[:12]}`)\n"
        f"- driver: `{LANE0_DRIVER_ID}`\n"
        f"- nodes: {', '.join(n.id for n in draft.nodes)} "
        f"(`lane0.factor` is deterministic — zero LLM invocations)\n"
        f"- budget request: {draft.budget_request_llm_invocations} LLM invocation(s), "
        f"{draft.budget_request_tokens} tokens\n"
        f"- products: a committed `ContextSnapshot@1` + an honest "
        f"`BootstrapContextManifest@1`; **no decision-class output, no order, "
        f"no signal**\n"
        f"- card source is `dynamic` because Lane 0 has no sealed "
        f"`PlanPresetRecord`; the consequence is deliberate — **no standing lease "
        f"can ever admit this card, only a named human**\n\n"
        + render_plan_diff_md(diff))


# =========================================================================== #
# step 1 — prepare (pure: validate + support, no persistence)                  #
# =========================================================================== #
def propose_lane0_candidate(
    *,
    as_of: datetime | None = None,
    bindings: Lane0Bindings | None = None,
    journal_path: Path | str | None = None,
    register: bool = True,
    attempt: int = 1,
) -> Lane0Candidate:
    """Build + validate today's Lane-0 candidate and (optionally) register its card.

    ``prepare_candidate`` is pure (Phase-1 validation + the pure runtime support
    check; the first persistence happens in ``persist_and_reserve_candidate``),
    so proposing costs nothing and burns no token. ``register=True`` additionally
    appends the pending reviewer card to the durable plan-approval journal, which
    is the precondition for :func:`approve_lane0_candidate` — the coordinator
    refuses to decide a candidate it holds no card for.

    The candidate digest is a pure function of the request/draft/context and
    therefore does NOT depend on the PIT inputs: an operator can approve today's
    plan before the day's data has finished arriving, and the run that consumes
    it will still be the plan that was reviewed.
    """
    bindings = bindings if bindings is not None else build_production_lane0_bindings()
    now = _resolve_as_of(as_of, bindings.clock)
    session_date = session_date_of(now)
    run_id, draft_id, request_id = _lane0_identity(session_date, attempt)

    preset = _bootstrap.build_bootstrap_plan(spec=bindings.spec, grader=bindings.grader)
    request = _presets.preset_orchestration_request(
        request_id=request_id,
        goal=f"观澜 · Lane 0 市场情境层 bootstrap({session_date})")
    draft = _bootstrap.build_bootstrap_plan_draft(
        preset, request=request, as_of=_session_as_of(session_date),
        mode=DataMode.ONLINE, catalog=bindings.catalog_snapshot,
        schema_registry_digest=bindings.registry.registry_digest,
        draft_id=draft_id, run_id=run_id)

    service, approvals, _run_budget = _admission_for(
        bindings=bindings, request=request, draft=draft, run_id=run_id)
    preparation = service.prepare_candidate(draft.id, request_id=request.request_id)
    digest = preparation.candidate_plan_digest

    diff = build_plan_diff(draft, request=request, candidate_plan_digest=digest,
                           baseline=None, baseline_kind="none")
    diff_digest = diff.semantic_digest()
    card = PendingPlanApproval(
        request_id=request.request_id, candidate_plan_digest=digest,
        goal=request.goal, source=PlanSource.DYNAMIC,
        approval_policy=ApprovalPolicy.REQUIRED, node_count=len(draft.nodes),
        worker_ids=tuple(sorted({n.worker_id for n in draft.nodes})),
        budget_request_tokens=draft.budget_request_tokens,
        budget_request_llm_invocations=draft.budget_request_llm_invocations,
        plan_diff_ref=TypedPayloadRef(
            schema_ref=PLAN_DIFF_SCHEMA_REF,
            payload_ref=PayloadRef(namespace="main",
                                   object_id=f"diff-{digest[:12]}",
                                   content_digest=diff_digest)),
        rendered_md=_render_card_markdown(
            preset=preset, draft=draft, session_date=session_date, diff=diff),
        rendered_from_diff_digest=diff_digest, planner_rationale=None,
        candidate_id=f"cand-{run_id}", requested_at=now,
        preset_id=None, preset_record_digest=None)

    registered = False
    if register:
        if not preparation.support_report.supported:
            raise Lane0Refused(
                "today's Lane-0 candidate is not runtime-supported: "
                + "; ".join(i.code for i in preparation.support_report.issues)
                + " — no card is registered for a plan that cannot run.",
                code="unsupported")
        _, _, _ = _reserve(service, preparation, run_id)
        auth = RecordedApprovalAuthorization(
            clock=bindings.clock, journal_path=journal_path)
        coordinator = auth.coordinator_for(admission=service, approvals=approvals)
        coordinator.register_pending(card, idempotency_key=f"lane0-pending:{digest}")
        registered = True

    return Lane0Candidate(
        session_date=session_date, as_of=now, request=request, draft=draft,
        preparation=preparation, pending_card=card, candidate_plan_digest=digest,
        supported=bool(preparation.support_report.supported),
        support_issue_codes=tuple(i.code for i in preparation.support_report.issues),
        registered=registered, attempt=int(attempt))


# =========================================================================== #
# step 2 — approve (the HUMAN's own act; never reachable from a run)           #
# =========================================================================== #
def approve_lane0_candidate(
    *,
    candidate_plan_digest: str,
    actor: str,
    as_of: datetime | None = None,
    bindings: Lane0Bindings | None = None,
    journal_path: Path | str | None = None,
    allowlist_path: Path | str | None = None,
    reason: str | None = None,
    decision: ApprovalDecision = ApprovalDecision.APPROVED,
    attempt: int = 1,
) -> Any:
    """Record ONE terminal human decision for today's Lane-0 candidate.

    A separate entry point on purpose: :func:`run_lane0_bootstrap` can never
    reach it, and it never runs a plan. The authority is the reviewed
    :class:`~guanlan_v2.orchestration.adapters.identity.ConfigOperatorVerifier`
    over ``config/orchestration/operators.json`` — an actor that is not a
    declared operator is refused by the verifier before anything is persisted
    (``OperatorNotAllowed``). ``candidate_plan_digest`` must equal the digest
    today's plan actually computes to: a stale one is refused rather than
    decided, so a decision can never land on a plan the reviewer did not see.
    """
    from guanlan_v2.orchestration.adapters.api import build_plan_approval_coordinator
    from guanlan_v2.orchestration.adapters.identity import ConfigOperatorVerifier

    bindings = bindings if bindings is not None else build_production_lane0_bindings()
    candidate = propose_lane0_candidate(
        as_of=as_of, bindings=bindings, journal_path=journal_path, register=False,
        attempt=attempt)
    if candidate.candidate_plan_digest != candidate_plan_digest:
        raise Lane0Refused(
            f"digest {candidate_plan_digest} is not today's Lane-0 candidate "
            f"({candidate.candidate_plan_digest}); re-run `propose` and approve the "
            "digest it prints — a decision is never recorded for a plan the "
            "reviewer did not see.", code="digest_drift")
    if not candidate.supported:
        raise Lane0Refused(
            "today's Lane-0 candidate is not runtime-supported: "
            + ("; ".join(candidate.support_issue_codes) or "(no issue code)")
            + " — no decision is recorded for a plan that cannot run.",
            code="unsupported")

    run_id, _draft_id, _request_id = _lane0_identity(
        candidate.session_date, candidate.attempt)
    service, approvals, _budget = _admission_for(
        bindings=bindings, request=candidate.request, draft=candidate.draft,
        run_id=run_id)
    preparation = service.prepare_candidate(
        candidate.draft.id, request_id=candidate.request.request_id)
    _reserve(service, preparation, run_id)
    coordinator = build_plan_approval_coordinator(
        admission=service, clock=bindings.clock,
        verifier=ConfigOperatorVerifier(allowlist_path=allowlist_path),
        approvals=approvals, journal_path=journal_path)
    coordinator.register_pending(
        candidate.pending_card,
        idempotency_key=f"lane0-pending:{candidate.candidate_plan_digest}")
    approval, event = coordinator.decide(
        request_id=candidate.request.request_id,
        candidate_plan_digest=candidate.candidate_plan_digest,
        decision=decision, actor=actor,
        reason=reason or f"Lane 0 bootstrap {candidate.session_date}",
        idempotency_key=f"lane0-decide:{candidate.candidate_plan_digest}")
    _LOG.info("Lane-0 candidate %s decided %s by %s",
              candidate.candidate_plan_digest[:12], approval.decision.value,
              approval.actor_id)
    return approval, event


# =========================================================================== #
# admission assembly (shared by propose / approve / run)                       #
# =========================================================================== #
def _admission_for(*, bindings: Lane0Bindings, request, draft, run_id: str):
    from guanlan_v2.orchestration.admission import PlanAdmissionService

    approvals: dict = {}
    run_budget = RunBudget(
        ledger_id=f"led-{run_id}",
        max_tokens=draft.budget_request_tokens,
        max_llm_invocations=draft.budget_request_llm_invocations,
        max_concurrency=draft.max_concurrency)
    service = PlanAdmissionService(
        run_id=run_id, requests={request.request_id: request},
        drafts={draft.id: draft}, contexts={}, attestations={}, approvals=approvals,
        catalog=bindings.catalog_runtime, bridge_view=bindings.bridge_view,
        phase1_registry=bindings.registry,
        runtime_registry_digest=bindings.runtime_registry_digest,
        profile=_bootstrap.bootstrap_runtime_profile(), stores=bindings.stores,
        run_budget=run_budget, clock=bindings.clock)
    return service, approvals, run_budget


def _reserve(service, preparation, run_id: str):
    candidate, reservation = service.persist_and_reserve_candidate(
        preparation, idempotency_key=f"lane0-reserve:{run_id}")
    return candidate, reservation, preparation.candidate_plan_digest


# =========================================================================== #
# step 3 — the run                                                             #
# =========================================================================== #
def run_lane0_bootstrap(
    *,
    authorization: Any,
    as_of: datetime | None = None,
    dry_run: bool = False,
    bindings: Lane0Bindings | None = None,
    inputs: MarketFactorInputs | None = None,
    attempt: int = 1,
) -> Lane0RunResult:
    """Drive ONE Lane-0 bootstrap run and commit its ``ContextSnapshot``.

    Seam order (``test_bootstrap_e2e``'s, unchanged): bindings → session identity
    → same-session reuse → PIT inputs → preset/draft → admission
    (validate → support → reserve) → the HANDED authorization → freeze →
    ``verify_for_dispatch`` → execute → ``build_context_snapshot_from_bootstrap``
    → commit the snapshot payload → read it back through the deep lane's own
    reader.

    ``authorization`` is required and is never minted here: production passes
    :class:`RecordedApprovalAuthorization`, which loads a decision a human
    already recorded. ``authorization=None`` refuses with
    :class:`Lane0Unauthorized` before ANY store write.
    """
    if authorization is None:
        raise Lane0Unauthorized(
            "run_lane0_bootstrap requires a reviewed authorization; the driver "
            "never approves its own run. Nothing was prepared, reserved or "
            "executed.")
    bindings = bindings if bindings is not None else build_production_lane0_bindings()
    now = _resolve_as_of(as_of, bindings.clock)
    session_date = session_date_of(now)
    run_id, draft_id, request_id = _lane0_identity(session_date, attempt)
    notes: list[str] = [
        "DataContext comes from the reviewed presets.pilot_data_context (the only "
        "producer that needs no frozen data-snapshot manifest chain); its "
        "source/routing digests are that builder's placeholders, not measurements.",
    ]

    # ---- idempotence ①: today's committed snapshot IS the receipt ---------- #
    # scoped to the SESSION DATE, not to this attempt: a day that already produced
    # a judgment is reused whichever attempt produced it.
    existing = _committed_snapshot_for_session(bindings.stores, session_date)
    if existing is not None:
        context, ref = existing
        return Lane0RunResult(
            outcome=OUTCOME_REUSED, session_date=session_date, as_of=now,
            run_id=run_id, request_id=request_id, draft_id=draft_id,
            snapshot_id=context.snapshot_id,
            snapshot_digest=context.content_digest, snapshot_ref=ref,
            reused=True,
            snapshot_visible_to_deep_lane=_deep_lane_sees(
                bindings.stores, context.content_digest),
            provider_uri=bindings.provider_uri,
            provider_uri_source=bindings.provider_uri_source,
            notes=tuple(notes + [
                "a committed ContextSnapshot already exists for this session date; "
                "reused rather than re-burning tokens. Lane-0 run identity is one "
                "per +08:00 session date, so a same-day re-run is a RESUME by "
                "kernel design, never a second judgment."]))

    # ---- idempotence ②: a prior execution of this identity, no snapshot ---- #
    spent = None if dry_run else _spent_run_identity(bindings.stores, run_id)
    if spent is not None:
        raise Lane0Refused(
            f"run identity {run_id!r} is spent: the durable journal already carries "
            f"a {spent} event for it, and no assembled ContextSnapshot exists — a "
            "prior attempt died between the run and the snapshot commit. Its "
            "RunResult key (runresult:<run_id>:payload) and its artifact envelopes "
            "(process-local pool._PoolStorage) can neither be rewritten nor "
            "replayed here, so re-running this identity would burn tokens and then "
            f"die on IdempotencyConflict. {_supersede_hint(attempt)} Nothing was "
            "re-run and no token was burned.", code="partial_prior_run")

    # ---- PIT inputs: honest absence, never a zero -------------------------- #
    if inputs is None:
        inputs = load_lane0_inputs(
            as_of=now, provider_uri=bindings.provider_uri,
            archive_dir=bindings.archive_dir)
    availability = describe_input_availability(inputs)
    badge = availability.badge()
    if badge:
        notes.append(
            f"PIT inputs {badge}: present={list(availability.present) or 'none'} "
            "(absent series render UNAVAILABLE; nothing is zero-filled)")

    # ---- preset + draft + admission ---------------------------------------- #
    preset = _bootstrap.build_bootstrap_plan(spec=bindings.spec, grader=bindings.grader)
    request = _presets.preset_orchestration_request(
        request_id=request_id,
        goal=f"观澜 · Lane 0 市场情境层 bootstrap({session_date})")
    draft = _bootstrap.build_bootstrap_plan_draft(
        preset, request=request, as_of=_session_as_of(session_date),
        mode=DataMode.ONLINE, catalog=bindings.catalog_snapshot,
        schema_registry_digest=bindings.registry.registry_digest,
        draft_id=draft_id, run_id=run_id)
    service, approvals, run_budget = _admission_for(
        bindings=bindings, request=request, draft=draft, run_id=run_id)
    preparation = service.prepare_candidate(draft.id, request_id=request.request_id)
    digest = preparation.candidate_plan_digest
    support_codes = tuple(i.code for i in preparation.support_report.issues)

    if dry_run:
        return Lane0RunResult(
            outcome=OUTCOME_DRY_RUN, session_date=session_date, as_of=now,
            run_id=run_id, request_id=request_id, draft_id=draft_id,
            candidate_plan_digest=digest, input_availability=availability,
            provider_uri=bindings.provider_uri,
            provider_uri_source=bindings.provider_uri_source,
            support_issue_codes=support_codes,
            degradation_badges=(badge,) if badge else (),
            notes=tuple(notes + [
                "dry run: validated + support-checked only. Nothing was persisted, "
                "reserved, approved or executed."]))

    if not preparation.support_report.supported:
        raise Lane0Refused(
            "today's Lane-0 candidate is not runtime-supported: "
            + ("; ".join(support_codes) or "(no issue code)")
            + " — refusing rather than running a plan the runtime cannot serve.",
            code="unsupported")

    _candidate, reservation, _ = _reserve(service, preparation, run_id)

    # ---- the HANDED authorization (never minted here) ---------------------- #
    grant = authorization.grant(
        admission=service, request=request, candidate_plan_digest=digest,
        approvals=approvals, now=now)

    plan, _admitted = service.freeze_and_admit_candidate(
        digest, reservation_id=reservation.reservation_id,
        approval_event_id=grant.approval_event_id,
        idempotency_key=f"lane0-freeze:{run_id}")
    dispatch = service.verify_for_dispatch(plan.plan_digest)

    # ---- runtime assembly (the same internals build_production_plan_runner
    #      composes; see the module docstring's ABI correction) -------------- #
    pool = ArtifactPool(
        stores=bindings.stores, registry_digest=bindings.runtime_registry_digest,
        plan=plan, catalog=bindings.catalog_snapshot, clock=bindings.clock).replay()
    budget = BudgetLedger(
        sink=bindings.stores.budget_event_sink(
            run_id=plan.run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)
    factories = TrustedFactoryRegistry(bindings.catalog_runtime)
    _bootstrap.register_bootstrap_runtime_factories(
        factories=factories, catalog=_bootstrap.load_lane0_catalog(), pool=pool,
        registry=bindings.registry, spec=bindings.spec, inputs=inputs, as_of=now,
        experience_views=bindings.experience_views,
        experience_scaler=_lane0_scaler(now, bindings.spec),
        experience_k=preset.experience_k)
    runtime = _worker.ExecutionRuntime(
        catalog=bindings.catalog_runtime, bridge_view=bindings.bridge_view,
        factories=factories, support_report=dispatch.support_report,
        runtime_registry_digest=bindings.runtime_registry_digest)
    data_context = _presets.pilot_data_context(as_of=_session_as_of(session_date))
    run_context = _bootstrap.build_bootstrap_run_context(
        run_id=plan.run_id, data=data_context, budget=run_budget,
        cancellation_token_id=f"cancel-{run_id}")

    detail_registry = default_audit_detail_registry()
    detail_registry.seal()
    refusal_sink = EventRefusalAuditSink(
        detail_registry=detail_registry, clock=bindings.clock)
    # the runtime owns as_of / factor_report_digest / content_digest on the two
    # Lane-0 reports (a model cannot know a full 64-hex digest or its own
    # self-seal), and a single-key envelope named for the schema is unwrapped.
    # Anything else the model wrote comes through untouched, so a real refusal
    # stays the executor's honest `output_schema_invalid`.
    gateway = _bootstrap.wrap_lane0_gateway(
        bindings.gateway_factory(
            payload_reader=bindings.stores.payloads,
            catalog_runtime=bindings.catalog_runtime),
        catalog_runtime=bindings.catalog_runtime, pool=pool,
        registry=bindings.registry)
    recorder = _dag_run_recorder()
    executor = build_dag_plan_executor(
        pool=pool, budget=budget, runtime=runtime, registry=bindings.registry,
        stores=bindings.stores, runtime_limit=int(bindings.runtime_limit),
        clock=bindings.clock, admission=service, model_gateway=gateway,
        prompt_assembler=_worker.StaticPromptAssembler(), refusal_sink=refusal_sink,
        recorder_factory=lambda: recorder)
    try:
        run_result = executor(
            plan=plan, run_context=run_context, lane="main", point=_DecisionPoint())
    except _idempotency_conflict() as exc:
        # gate ② above catches this state from the durable journal, so reaching
        # here means some OTHER once-only key for this identity was already
        # written. Either way the operator gets one honest line, never a raw
        # kernel traceback out of the CLI.
        raise Lane0Refused(
            f"run identity {run_id!r} collided with a durable once-only key: "
            f"{exc}. {_supersede_hint(attempt)}", code="partial_prior_run") from exc
    finally:
        close = getattr(gateway, "close", None)
        if callable(close):
            close()

    # the reviewed observational surface: the LAST terminal NodeRun per Lane-0
    # node (the durable authority is the NodeStateChanged journal the recorder
    # is rebuilt from on resume).
    statuses: dict[str, str] = {}
    for node_run in recorder.node_runs:
        if node_run.node_id in _LANE0_NODE_IDS:
            statuses[node_run.node_id] = node_run.status.value

    # ---- the committed snapshot + honest manifest (one all-or-none UoW) ----- #
    context, manifest = _bootstrap.build_context_snapshot_from_bootstrap(
        run_result=run_result, pool=pool, data_context=data_context,
        stores=bindings.stores, registry=bindings.registry, clock=bindings.clock)

    # ---- make it READABLE by the deep lane --------------------------------- #
    # `build_context_snapshot_from_bootstrap` commits the manifest + the
    # CONTEXT_SNAPSHOT_FROZEN event but persists no ContextSnapshot@1 payload, so
    # without this put the deep lane's scan would only ever find the run's own
    # input-side snapshot — a different digest from the one the manifest badges.
    # Idempotent by run id; the registry validates the write.
    snapshot_ref = bindings.stores.payloads.put(
        _CONTEXT_SNAPSHOT_SR, context,
        registry_digest=bindings.runtime_registry_digest, namespace="main",
        idempotency_key=f"lane0-driver-context:{plan.run_id}")
    visible = _deep_lane_sees(bindings.stores, context.content_digest)
    badges = tuple(manifest.degradation_badges) + ((badge,) if badge else ())
    if not visible:
        badges = badges + ("snapshot_shadowed",)
        notes.append(
            "the deep lane's latest-snapshot reader does NOT return this snapshot "
            "(another ContextSnapshot@1 carries a later built_at); the run stands, "
            "but the deep lane would bind a different context — investigate before "
            "relying on it.")
        _LOG.warning("Lane-0 snapshot %s is shadowed for the deep lane",
                     context.snapshot_id)

    degraded = (run_result.terminal_status != "completed"
                or bool(manifest.degradation_badges)
                or not visible          # the deep lane would bind another context
                or any(s != "completed" for s in statuses.values()))
    return Lane0RunResult(
        outcome=OUTCOME_DEGRADED if degraded else OUTCOME_COMPLETED,
        session_date=session_date, as_of=now, run_id=plan.run_id,
        request_id=request_id, draft_id=draft_id, candidate_plan_digest=digest,
        plan_digest=plan.plan_digest, terminal_status=run_result.terminal_status,
        node_statuses=statuses,
        llm_invocations=int(run_result.settled_llm_invocations),
        settled_tokens=int(run_result.settled_tokens),
        snapshot_id=context.snapshot_id, snapshot_digest=context.content_digest,
        snapshot_ref=snapshot_ref, manifest_digest=manifest.content_digest,
        manifest_context_snapshot_digest=manifest.context_snapshot_digest,
        degradation_badges=tuple(sorted(set(badges))),
        input_availability=availability, provider_uri=bindings.provider_uri,
        provider_uri_source=bindings.provider_uri_source, reused=False,
        snapshot_visible_to_deep_lane=visible, approval=grant,
        support_issue_codes=support_codes, notes=tuple(notes))


class _DecisionPoint:
    """The single decision point of one Lane-0 run (the executor's point seam)."""

    point_ordinal = 1


def _dag_run_recorder():
    """A fresh reviewed :class:`~guanlan_v2.orchestration.dag.RunRecorder`."""
    from guanlan_v2.orchestration.dag import RunRecorder

    return RunRecorder()


# =========================================================================== #
# the CLI                                                                      #
# =========================================================================== #
def render_run_report(result: Lane0RunResult) -> str:
    """The operator-facing, step-by-step honest report of one invocation."""
    lines = [
        f"[lane0] outcome           : {result.outcome}",
        f"[lane0] session           : {result.session_date} "
        f"(as_of {result.as_of.isoformat()})",
        "[lane0] stores            : bound (the driver never self-binds)",
        f"[lane0] run_id            : {result.run_id}",
    ]
    if result.approval is not None:
        lines.append(
            f"[lane0] authorization     : {result.approval.channel} by "
            f"{result.approval.actor_id} "
            f"(event {result.approval.approval_event_id})")
    else:
        lines.append("[lane0] authorization     : (not consulted on this path)")
    if result.input_availability is not None:
        av = result.input_availability
        lines.append(
            f"[lane0] inputs            : {len(av.present)} present / "
            f"{len(av.absent)} absent — {', '.join(av.present) or 'NONE'} "
            f"[{result.provider_uri} via {result.provider_uri_source}]")
    else:
        lines.append("[lane0] inputs            : (not loaded on this path)")
    lines.append(
        f"[lane0] admission         : candidate "
        f"{(result.candidate_plan_digest or '-')[:16]} "
        f"plan {(result.plan_digest or '-')[:16]} "
        f"support_issues={list(result.support_issue_codes) or 'none'}")
    for node_id in _LANE0_NODE_IDS:
        lines.append(
            f"[lane0]   {node_id:<16}: {result.node_statuses.get(node_id, '-')}")
    lines.append(
        f"[lane0] snapshot          : {result.snapshot_id or '-'} "
        f"digest={(result.snapshot_digest or '-')[:12]} "
        f"ref={result.snapshot_ref.object_id if result.snapshot_ref else '-'} "
        f"deep_lane_sees={result.snapshot_visible_to_deep_lane}")
    lines.append(
        f"[lane0] manifest          : {(result.manifest_digest or '-')[:12]} "
        f"badges={list(result.degradation_badges) or 'none'}")
    lines.append(
        f"[lane0] llm_invocations   : {result.llm_invocations} "
        f"(settled_tokens={result.settled_tokens})")
    for note in result.notes:
        lines.append(f"[lane0] note              : {note}")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m guanlan_v2.orchestration.lane0_driver",
        description="Drive one Lane-0 bootstrap run and commit its ContextSnapshot.")
    parser.add_argument(
        "verb", choices=("status", "propose", "approve", "run"),
        help="status: what the process/store/snapshot state is; propose: build + "
             "validate today's candidate and register its reviewer card; approve: "
             "record ONE human decision (verifier-gated); run: execute.")
    parser.add_argument("--as-of", default=None,
                        help="ISO-8601 aware timestamp (default: now).")
    parser.add_argument("--digest", default=None,
                        help="approve: the candidate digest the reviewer saw.")
    parser.add_argument("--actor", default=None,
                        help="approve: the declared operator id "
                             "(default: the single declared operator).")
    parser.add_argument("--reason", default=None, help="approve: the decision reason.")
    parser.add_argument("--journal", default=None,
                        help="plan-approval journal path (default: the production "
                             "var/orchestration/plan_approvals.jsonl). Point it at a "
                             "scratch file for a 9998-style verification run.")
    parser.add_argument("--allowlist", default=None,
                        help="approve: operator declaration path (default: "
                             "config/orchestration/operators.json).")
    parser.add_argument("--reject", action="store_true",
                        help="approve: record a REJECTED decision instead.")
    parser.add_argument("--dry-run", action="store_true",
                        help="run: validate + support-check only; persist nothing.")
    parser.add_argument("--attempt", type=int, default=1,
                        help="the documented supersede for a SPENT run identity "
                             "(default 1). N>=2 suffixes the identity with -rN, so "
                             "it is a different plan with a different candidate "
                             "digest and needs its own approval — pass the SAME "
                             "--attempt to propose, approve and run. A session date "
                             "that already committed a ContextSnapshot is still "
                             "reused, never re-burned: this is not a force.")
    parser.add_argument("--bind-stores", action="store_true",
                        help="perform THIS process's own one-and-only durable-store "
                             "startup binding (guanlan_v2.orchestration.startup) "
                             "before anything else. Required in a standalone CLI "
                             "process, which — unlike the 9999 server — has no "
                             "startup that binds for it. Off by default so the "
                             "binding is always a deliberate act.")
    return parser


def _bind_cli_process_stores() -> dict:
    """This CLI process's own startup binding — the operator's explicit act.

    Deliberately in the CLI layer and nowhere else. The library
    (:func:`build_production_lane0_bindings`, :func:`run_lane0_bootstrap`) never
    binds and never can: it consults the status record and refuses. A standalone
    ``python -m`` process, though, is a process with no startup of its own, and
    the reviewed ``startup.bind_orchestration_stores`` is exactly the "one
    binding, done right, first" entry point for that — the same call
    ``server.py`` makes. It is gated behind ``--bind-stores`` so nobody binds a
    store as a side effect of typing a read-only verb.
    """
    from guanlan_v2.orchestration import startup as _startup

    status = _startup.bind_orchestration_stores()
    print(f"[lane0] store binding     : {status.get('state')} at "
          f"{status.get('root')} "
          f"({status.get('cell_namespace_count')} cell namespaces)")
    return status


def _parse_as_of(text: str | None) -> datetime | None:
    if not text:
        return None
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        raise SystemExit("--as-of must be timezone-aware (e.g. 2026-07-29T09:30+08:00)")
    return stamp


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # the reviewer card and several notes are Chinese; a Windows GBK console would
    # otherwise mangle or refuse them (the repo's long-standing PYTHONIOENCODING trap).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 - a stream that cannot be reconfigured is fine
            pass
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    as_of = _parse_as_of(args.as_of)
    if args.bind_stores:
        _bind_cli_process_stores()
    try:
        bindings = build_production_lane0_bindings()
    except Lane0StoresUnbound as exc:
        print(f"[lane0] REFUSED (stores): {exc}", file=sys.stderr)
        print("[lane0] hint: a standalone CLI process has no startup that binds "
              "the durable stores for it: re-run with --bind-stores to perform "
              "this process's own (single, first) binding.", file=sys.stderr)
        return 2

    try:
        if args.verb == "status":
            now = _resolve_as_of(as_of, bindings.clock)
            session_date = session_date_of(now)
            run_id, _d, _r = _lane0_identity(session_date, args.attempt)
            existing = _committed_snapshot_for_session(bindings.stores, session_date)
            spent = _spent_run_identity(bindings.stores, run_id)
            print(f"[lane0] session           : {session_date}")
            print(f"[lane0] run_id            : {run_id}"
                  + (f" (SPENT: {spent} already in the journal)" if spent else ""))
            print(f"[lane0] provider_uri      : {bindings.provider_uri} "
                  f"(via {bindings.provider_uri_source})")
            print(f"[lane0] committed snapshot: "
                  f"{existing[0].snapshot_id if existing else 'NONE (Lane 0 has not run)'}")
            if existing is not None:
                print(f"[lane0] deep lane sees it  : "
                      f"{_deep_lane_sees(bindings.stores, existing[0].content_digest)}")
            return 0

        if args.verb == "propose":
            candidate = propose_lane0_candidate(
                as_of=as_of, bindings=bindings, journal_path=args.journal,
                attempt=args.attempt)
            print(f"[lane0] session           : {candidate.session_date}")
            print(f"[lane0] candidate digest  : {candidate.candidate_plan_digest}")
            print(f"[lane0] runtime supported : {candidate.supported} "
                  f"{list(candidate.support_issue_codes) or ''}")
            print(f"[lane0] reviewer card     : registered={candidate.registered}")
            print()
            print(candidate.pending_card.rendered_md)
            print()
            attempt_flag = ("" if candidate.attempt == 1
                            else f" --attempt {candidate.attempt}")
            print("[lane0] next: python -m guanlan_v2.orchestration.lane0_driver "
                  f"approve --digest {candidate.candidate_plan_digest}{attempt_flag}")
            return 0

        if args.verb == "approve":
            if not args.digest:
                print("[lane0] --digest is required for approve", file=sys.stderr)
                return 2
            actor = args.actor
            if not actor:
                from guanlan_v2.orchestration.adapters.identity import (
                    declared_operator_actor,
                )

                actor = declared_operator_actor(args.allowlist)
            approval, event = approve_lane0_candidate(
                candidate_plan_digest=args.digest, actor=actor, as_of=as_of,
                bindings=bindings, reason=args.reason,
                journal_path=args.journal, allowlist_path=args.allowlist,
                attempt=args.attempt,
                decision=(ApprovalDecision.REJECTED if args.reject
                          else ApprovalDecision.APPROVED))
            print(f"[lane0] decision recorded : {approval.decision.value} by "
                  f"{approval.actor_id} (event {event.event_id})")
            return 0

        # verb == "run"
        authorization = RecordedApprovalAuthorization(
            clock=bindings.clock, journal_path=args.journal)
        result = run_lane0_bootstrap(
            authorization=authorization, as_of=as_of, bindings=bindings,
            dry_run=bool(args.dry_run), attempt=args.attempt)
        print(render_run_report(result))
        return 0
    except Lane0Unauthorized as exc:
        print(f"[lane0] REFUSED (authorization): {exc}", file=sys.stderr)
        return 3
    except Lane0Refused as exc:
        print(f"[lane0] REFUSED ({exc.code}): {exc}", file=sys.stderr)
        return 4
    except _operator_identity_error() as exc:
        # a fail-closed verifier refusal is an ANSWER, not a crash: an operator who
        # is not on the declaration should read one honest line, not a traceback
        # that looks like the tool broke.
        print(f"[lane0] REFUSED (operator): {exc}", file=sys.stderr)
        print(f"[lane0] hint: declare the actor in "
              f"{args.allowlist or 'config/orchestration/operators.json'} — there "
              "is no default operator and no wildcard.", file=sys.stderr)
        return 5


def _idempotency_conflict() -> type[Exception]:
    """The kernel's once-only-key refusal (imported lazily, like every kernel type)."""
    from guanlan_v2.orchestration.eventstore import IdempotencyConflict

    return IdempotencyConflict


def _operator_identity_error() -> type[Exception]:
    """The verifier's own refusal base (``OperatorNotAllowed`` / allowlist errors)."""
    from guanlan_v2.orchestration.adapters.identity import OperatorIdentityError

    return OperatorIdentityError


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
