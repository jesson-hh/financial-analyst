# -*- coding: utf-8 -*-
"""Phase 10 · Task 8 — deep-chain replay evidence (回放双曲线验收闸).

The executable proof a human cites when signing an intraday deep-decide lease
(spec §5: 有证据才签租约,人判断曲线). A short fixture interval is replayed with the
SEALED deep-decide preset (``pipeline.luozi_deep_decide``) standing in as the LLM
lane's per-point plan, and the run produces both curves under ONE
``ShadowExecutionConfig`` attestation.

WHICH SEAM (and why not the other one)
--------------------------------------
The replay is driven through the Phase-9 **injectable** seam:
``ReplayRuntimeBindings.admission`` — the :class:`ReplayPlanCoordinator` port
(``adapters/luozi.py:2362``) whose three methods the driver calls per point. That
is the sanctioned path. The *real-path* multi-point plan runner
(``adapters/launcher.py::build_admitted_plan_runner``) still refuses a second
point on one approved candidate digest
(:class:`~guanlan_v2.orchestration.adapters.launcher.MultiPointPlanExecutionRefused`,
launcher.py:381) — that refusal is upstream, chartered post-Phase-10, and is
PINNED here (``test_the_real_path_multi_point_runner_still_refuses``) rather than
fought: this suite gives every point its OWN materialized draft, its OWN candidate
plan digest and its OWN admitted plan, so nothing is ever resumed or borrowed.

WHAT IS REAL HERE
-----------------
Real: the Phase-9 driver (``run_interval_replay``), the Phase-6 schedule-time
computations + envelope constructor (``wrap_proposal_as_intent``), the Phase-2
``BudgetLedger``, the Task-6 materializer (``materialize_deep_decide_draft``), the
Phase-2 ``PlanAdmissionService``, the Phase-7 lease channel
(``register_and_try_lease`` / ``admit_after_approval``), the Task-0b production
plan runner over the REAL ``dag.run_plan``, the Task-5 deterministic rule
(``derive_deterministic_targets``) and ``build_dual_curves`` over the REAL fa
engine backtest runner.

Fixture (per the brief): SCRIPTED model gateways (zero LLM, zero network) and a
FAKE per-point data context (the reviewed ``presets.pilot_data_context`` at the
point's own ``decision_as_of``, not the production ``build_replay_data_context``
PIT context — the data layer is Task 12's assembly; this suite's evidence is the
preset chain). The catalog is the reviewed trimmed bridge-free ten-worker stand-in
built by Task 7's ``heavy`` fixture, imported by name so the two suites cannot
drift apart (the sealed catalog's data-prefetch grant gap is Task 11's, pinned as
a strict xfail in ``test_pipeline_deep_preset.py`` and NOT weakened here).

THE PRESET LINKAGE (brief item 2), recorded honestly
----------------------------------------------------
``DualCurveReport`` (adapters/contracts.py:260) carries NO preset-linkage field —
it is ``{execution_config, deterministic, llm_shadow, interval_*, decision_point_
count, delta_total_return, not_causal_attribution, badges}``. Per the brief, no
contract is invented for one; instead the **candidate-plan → preset digest chain**
is recorded through the run's own evidence, and asserted link by link:

    preset record digest (``PlanPresetRecordV2.semantic_digest()``)
      → the lease it was issued against (``ApprovalLease.preset_record_digest``)
      → the per-point ``PendingPlanApproval`` card (preset_id + record digest)
      → that card's ``candidate_plan_digest``
      → the ``PLAN_APPROVED`` admission event the lease minted
      → the frozen ``Plan`` (``PLAN_FROZEN`` / ``RUN_COMPLETED`` on the run journal)
      → the run's sink ``Artifact`` (``PortfolioTargetProposal@1``)
      → ``TargetPortfolioIntent.proposal_digest`` / ``proposal_artifact_id``
      → ``DualCurveReport.llm_shadow.applied_intent_digests``

``test_the_report_carries_no_preset_field_so_the_chain_is_the_linkage`` fails the
day Phase 9 grows a real linkage field, so the linkage is upgraded consciously
rather than left as a chain forever.

THE EVIDENCE CONVENTION (brief item 3) — procedural, human-judged
-----------------------------------------------------------------
``PlanApprovalCoordinator.issue_lease(..., reason=...)`` takes a ``NonEmptyStr``
(approval.py:624 / ``ApprovalLease.reason``, approval.py:254) and the console
lease card projects it verbatim (``console/api.py`` ``/plan/approvals/leases`` →
``"reason": v.lease.reason``, the same ``list_leases`` view this suite reads).

**The convention:** the ``reason`` on a ``DEEP_DECIDE_PRESET_ID`` intraday lease
CITES the ``DualCurveReport`` semantic digest of the replay run the human read the
curves of — evidence first, lease second. That convention is procedural and
human-judged: nothing structurally proves a cited digest was actually looked at,
and this plan does NOT pretend to enforce report quality. What is enforced here is
exactly what the contract enforces — the reason is non-empty, an empty one is
refused, and the digest string a human pastes survives to the card a human reads.

Run from repo root:
``python -m pytest tests/orchestration/test_pipeline_replay_evidence.py -v``
"""
from __future__ import annotations

import contextlib
import dataclasses
import re
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

# REAL fa engine cost model (the conftest prepends the in-repo engine fork).
from financial_analyst.backtest.costs import CostModel

import guanlan_v2.orchestration.worker as W
from guanlan_v2.orchestration import lane_payloads as lp
from guanlan_v2.orchestration import presets as P
from guanlan_v2.orchestration import shadow as shadow_mod
from guanlan_v2.orchestration.adapters import luozi as lz
from guanlan_v2.orchestration.adapters.contracts import (
    DualCurveReport,
    ShadowExecutionConfig,
)
from guanlan_v2.orchestration.adapters.launcher import (
    LaneExecutionBinding,
    MultiPointPlanExecutionRefused,
    build_admitted_plan_runner,
)
from guanlan_v2.orchestration.adapters.luozi import (
    DeterministicBook,
    ReplayIntentLedger,
    ReplayPointSnapshot,
    ReplayRuntimeBindings,
    build_dual_curves,
    derive_deterministic_targets,
    resolve_decision_points,
    run_interval_replay,
)
from guanlan_v2.orchestration.admission import PlanAdmissionService
from guanlan_v2.orchestration.approval import (
    ApprovalLease,
    PlanApprovalCoordinator,
    admit_after_approval,
)
from guanlan_v2.orchestration.catalog_runtime import (
    TrustedFactoryRegistry,
)
from guanlan_v2.orchestration.context import ClockSpec, RunBudget, RunContext
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import (
    ApprovalPolicy,
    Confidence,
    ExperimentStatus,
    PlanSource,
    PortfolioRating,
)
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.plan_diff import build_plan_diff
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import SystemClock
from guanlan_v2.orchestration.runtime_support import STATIC_RUNTIME_PROFILE_V2
from guanlan_v2.orchestration.schemas import (
    PortfolioDecision,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
)
from guanlan_v2.orchestration.shadow import (
    SHADOW_MATCHING_ENGINE_VERSION,
    PortfolioTargetProposal,
    TargetPosition,
)
from guanlan_v2.orchestration.spec import OrchestrationRequest

from guanlan_v2.orchestration.pipeline import live_decide
from guanlan_v2.orchestration.pipeline.assembly import (
    ProductionCatalogRuntime,
    build_production_plan_runner,
)
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.pipeline.deep_decide import (
    DEEP_DECIDE_PRESET_ID,
    DEEP_DECIDE_WORKER_IDS,
    materialize_deep_decide_draft,
    session_date_of,
)
from guanlan_v2.orchestration.pipeline.live_decide import SubjectPromptAssembler

# ---- reviewed sibling harnesses (the test_phase9_e2e.py precedent) --------- #
# `heavy` is Task 7's module-scoped deep-preset stand-in: the Phase-9 cumulative
# registry + the trimmed bridge-free ten-worker catalog + the sealed Phase-10
# preset registry. Imported (not re-derived) so the two Phase-10 suites can never
# drift on what "the deep preset's catalog" means.
from tests.orchestration.test_pipeline_live_decide import (  # noqa: F401
    GOOD_CRED,
    _FixedClock,
    _Verifier,
    heavy,
)
from tests.orchestration.test_dual_curves import _MemLoader, _MemReader
from tests.orchestration.test_luozi_replay import (
    _bindings as _driver_bindings,
    _calendar,
    _request,
    _schedule,
    _utc,
)

UTC = timezone.utc
TZ = "Asia/Shanghai"
CODE = "600000"
EXCHANGE = "SH"
SYM = normalize_symbol(f"{CODE}.{EXCHANGE}")
#: the +08:00 session zone the run subject's deterministic ``as_of`` anchors to.
_SESSION_TZ = timezone(timedelta(hours=8))
#: a genuine provenance digest for the deterministic lane's PIT factor scores
#: (an ``[UNSOURCED]`` digest is refused by ``derive_deterministic_targets``).
_SOURCED = content_digest({"factor_report": "market.factors", "suite": "phase10-task8"})
#: the PIT factor score every point feeds the deterministic rule (> τ=0.15 ⇒ long).
_FACTOR_SCORE = 0.5

#: a seed day plus a rising week — the ONE bar frame BOTH curve lanes consume.
_BARS = [
    ("2026-07-03", 9.9, 10.0, 9.7, 10.0, 1e6),
    ("2026-07-06", 10.0, 10.4, 9.8, 10.0, 1e6),
    ("2026-07-07", 10.0, 10.4, 9.8, 10.2, 1e6),
    ("2026-07-08", 10.2, 11.0, 10.1, 10.8, 1e6),
    ("2026-07-09", 10.8, 11.4, 10.7, 11.0, 1e6),
]
_SESSIONS = ["2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09"]

#: the eight LLM workers of the sealed deep preset (the other two are handlers).
_DETERMINISTIC_WORKERS = ("pv.price_action", "pv.microstructure")
_LLM_WORKERS = tuple(w for w in DEEP_DECIDE_WORKER_IDS if w not in _DETERMINISTIC_WORKERS)

#: the plan-side / admission evidence the "code-free" claim covers (Task 7's set).
#: Worker OUTPUT artifacts are excluded — their payloads legitimately carry the
#: symbol the scripted model wrote — and ``RunSubject@1`` is the ONE carrier.
_PLAN_SIDE_SCHEMAS = (
    "Plan@1", "AdmissionCandidate@1", "PlanValidationReport@1",
    "RuntimeSupportReport@1", "PlanAdmitted@1", "PlanApproval@1",
    "PromptAssemblyRecord@1",
)
#: long hex runs (digests), stripped before the code sweep: a digest is opaque hex
#: that may contain any digit run and is never a code carrier. Matched UNQUOTED
#: too, because digests also ride inside composite strings — the idempotency key
#: ``"plan-approval:<64hex>"`` produced a false positive on a quoted-only pattern
#: (found while arming tripwire T2).
_DIGEST_RE = re.compile(r'[0-9a-f]{32,}')
#: ANY six-digit run with digit boundaries on both sides — catches a code embedded
#: mid-string (a goal, a rationale, a node id), which a quoted-exact pattern misses,
#: while rejecting the sealed preset's own ``budget_request_tokens: 6000000``
#: (seven digits ⇒ the right-hand boundary fails). Proven by tripwire T2.
#:
#: KNOWN-BENIGN six-digit shapes this WOULD trip on if they ever reached the
#: plan-side evidence (none do today): a round token/budget figure written with
#: exactly six digits (``100000``, ``500000``), a microsecond field, or a
#: six-digit port/ordinal. A future trip on one of those is a false positive to
#: be narrowed here — it is NOT evidence of a leaked stock code. The assertion
#: prints the offending run so the two are distinguishable at a glance.
_CODE_RUN_RE = re.compile(r'(?<!\d)\d{6}(?!\d)')


def _code_free(blob: str) -> str | None:
    """The first six-digit code-shaped run in ``blob``, or ``None`` if clean."""
    match = _CODE_RUN_RE.search(_DIGEST_RE.sub('""', blob))
    return match.group(0) if match else None


# =========================================================================== #
# zero-network seal                                                            #
# =========================================================================== #
#: how many ``_network_sealed`` blocks are currently open. Recorded (not merely
#: asserted about) so a test can prove the replay ITSELF ran sealed: delete the
#: ``with _network_sealed()`` from the fixture and the recorded depths go to 0.
_SEAL_DEPTH = {"n": 0}

#: loopback hosts a sealed run may still reach. NOT a convenience hole: on Windows
#: the asyncio ProactorEventLoop builds its self-pipe with ``socket.socketpair()``,
#: which is a real 127.0.0.1 connect — and ``asyncio.run`` is exactly how the
#: launcher's sync bridge executes ``dag.run_plan`` (launcher.py:204-223). Sealing
#: loopback too would make the seal fail on the framework, not on a vendor call.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})


def _is_loopback(address) -> bool:
    """True for a loopback / non-IP address (see :data:`_LOOPBACK_HOSTS`)."""
    if not isinstance(address, tuple) or not address:
        return True                      # AF_UNIX / AF_PIPE — never off-machine
    host = address[0]
    return isinstance(host, str) and host in _LOOPBACK_HOSTS


@contextlib.contextmanager
def _network_sealed():
    """Arm a hard refusal on every OFF-MACHINE socket connect inside the block.

    Brief item 4's "zero network throughout" asserted rather than asserted-about:
    the whole replay + curve build runs with ``socket.socket.connect`` replaced by
    a stub that raises for any non-loopback address, so a real vendor / model /
    data call could not silently succeed. Loopback stays open for the event-loop
    self-pipe only (:data:`_LOOPBACK_HOSTS`); nothing in this suite listens.
    """
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def _guard(inner):
        def _connect(self, address, *args, **kwargs):  # noqa: ANN001
            if not _is_loopback(address):
                raise AssertionError(
                    "the deep-chain replay evidence run attempted a network "
                    f"connection to {address!r}; this suite is offline by "
                    "construction (scripted gateways only)")
            return inner(self, address, *args, **kwargs)
        return _connect

    socket.socket.connect = _guard(original_connect)
    socket.socket.connect_ex = _guard(original_connect_ex)
    _SEAL_DEPTH["n"] += 1
    try:
        yield
    finally:
        _SEAL_DEPTH["n"] -= 1
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex


# =========================================================================== #
# one coherent replay world: schedule + calendar + points + attested runner     #
# =========================================================================== #
def _runner(*, schedule, calendar, init_cash=1_000_000.0):
    return lz.ShadowBacktestRunner(
        reader=_MemReader(_SESSIONS),
        loader=_MemLoader({f"{EXCHANGE}{CODE}": list(_BARS)}),
        schedule=schedule,
        schedule_ref=shadow_mod.ContentRef(
            id=schedule.id, version=schedule.version,
            content_digest=schedule.content_digest),
        calendar=calendar,
        cost_model=CostModel(),
        init_cash=init_cash,
        clock=SystemClock(),
    )


def _exec_config(runner) -> ShadowExecutionConfig:
    """The ONE ``ShadowExecutionConfig`` both lanes are attested against."""
    schedule = runner._schedule
    return ShadowExecutionConfig(
        universe=(SYM,),
        init_cash=runner._init_cash,
        data_snapshot_content_digest="a" * 64,
        vintage_manifest_digest="b" * 64,
        calendar_id=runner._calendar.calendar_id,
        cost_model_digest=content_digest(dataclasses.asdict(runner._cost_model)),
        matching_engine_version=SHADOW_MATCHING_ENGINE_VERSION,
        clock=ClockSpec(as_of=_utc("2026-07-06", 14, 0), timezone=schedule.timezone,
                        calendar_id=runner._calendar.calendar_id),
        schedule_digest=schedule.content_digest,
        intrabar_exit_priority=schedule.intrabar_exit_priority,
    )


def _world():
    """schedule + calendar + the three decision points + the attested runner/config."""
    schedule = _schedule(kind="daily")
    calendar = _calendar(_SESSIONS)
    start, end = _utc("2026-07-06", 0, 0), _utc("2026-07-08", 23, 59)
    runner = _runner(schedule=schedule, calendar=calendar)
    points = resolve_decision_points(
        schedule, calendar=calendar, interval_start=start, interval_end=end)
    return SimpleNamespace(
        schedule=schedule, calendar=calendar, start=start, end=end,
        runner=runner, config=_exec_config(runner), points=points)


# =========================================================================== #
# scripted deep-preset model outputs — dated at THIS point's decision instant   #
# =========================================================================== #
def _scripted_payload(worker_id: str, *, as_of: datetime):
    """One reviewed-shape payload per deep worker, stamped at ``as_of``.

    Deliberately parameterized by the point's own ``decision_as_of`` rather than a
    single fixture instant: a replay whose scripted model outputs were all dated at
    one wall-clock instant would be quietly non-PIT evidence.
    """
    if worker_id == "pv.technical":
        return lp.TechnicalReport(
            symbol=SYM, as_of=as_of,
            indicators=(lp.IndicatorReading(name="RSI14", value=55.0),),
            verified_anchor_digest=None, bias="neutral",
            summary="scripted technical read")
    if worker_id == "text.news":
        return lp.NewsDigestReport(as_of=as_of, scope="market", items=(),
                                   coverage_note="scripted: feeds quiet")
    if worker_id == "text.sentiment":
        return SentimentReport(
            overall_band=SentimentBand.NEUTRAL, overall_score=5.0,
            confidence=Confidence.MEDIUM, narrative="Balanced flow, mild caution.")
    if worker_id == "dec.bull":
        return lp.BullCase(symbol=SYM, as_of=as_of, thesis_bullets=("scripted bull",))
    if worker_id == "dec.bear":
        return lp.BearCase(symbol=SYM, as_of=as_of, thesis_bullets=("scripted bear",))
    if worker_id == "dec.research_mgr":
        return ResearchPlan(recommendation=PortfolioRating.HOLD,
                            rationale="Hold pending confirmation.",
                            strategic_actions=("Monitor breadth",))
    if worker_id == "dec.pm":
        return PortfolioDecision(rating=PortfolioRating.HOLD,
                                 executive_summary="Advisory hold.",
                                 investment_thesis="Risk/reward balanced.")
    if worker_id == "dec.trader":
        return PortfolioTargetProposal(
            positions=(TargetPosition(symbol=SYM, target_weight=0.5),),
            cash_weight=0.5,
            rationale=f"shadow advisory only; deep-decide proposal at {as_of.isoformat()}",
            confidence=Confidence.MEDIUM)
    raise AssertionError(f"unexpected LLM worker {worker_id!r}")


class _ReplayGateway:
    """Scripted trusted single-shot gateway: REAL binding verification, fixed payloads."""

    def __init__(self, payload_reader, *, as_of: datetime, counter: list):
        self._reader = payload_reader
        self._as_of = as_of
        self._counter = counter
        self.records: list = []

    def invoke(self, request, *, prompt_assembly_ref):
        record = W.verify_model_request_binding(
            request, prompt_assembly_ref, reader=self._reader)
        self.records.append(record)
        self._counter.append(record.worker_id)
        return W.ModelResult(
            payload=_scripted_payload(record.worker_id, as_of=self._as_of),
            rendered_text=f"scripted:{record.worker_id}", input_tokens=7,
            output_tokens=5, provider="scripted", model="scripted")


class _GatewayFactory:
    def __init__(self, *, as_of: datetime, counter: list):
        self._as_of = as_of
        self._counter = counter
        self.gateways: list[_ReplayGateway] = []

    def __call__(self, *, payload_reader, catalog_runtime):
        gw = _ReplayGateway(payload_reader, as_of=self._as_of, counter=self._counter)
        self.gateways.append(gw)
        return gw


def _pa_handler_factory_for(as_of: datetime):
    keys = lp.PA_FEATURE_SET_KEYS["pa-15key-v1"]

    def factory(worker, resolved):
        def handler(*, node, input_snapshot, contributions, data_result_refs):
            return W.ModelResult(
                payload=lp.PriceActionFeatureReport(
                    symbol=SYM, as_of=as_of, feature_set_version="pa-15key-v1",
                    features={k: float(i) for i, k in enumerate(keys)}),
                rendered_text="pa det", input_tokens=0, output_tokens=1)
        return handler

    return factory


def _ms_handler_factory_for(as_of: datetime):
    def factory(worker, resolved):
        def handler(*, node, input_snapshot, contributions, data_result_refs):
            return W.ModelResult(
                payload=lp.MicrostructureReport(
                    symbol=SYM, as_of=as_of, l1_spread_bp=None,
                    bid_ask_imbalance=None, break_ratio=None, whale_net_inflow=None,
                    degradation=("scripted: all optional feeds stood down",),
                    narrative="scripted microstructure (nothing imputed)"),
                rendered_text="ms det", input_tokens=0, output_tokens=1)
        return handler

    return factory


# =========================================================================== #
# the deep-preset ReplayPlanCoordinator (the injectable Phase-9 seam)           #
# =========================================================================== #
class _AdmissionRouter:
    """Routes ONE long-lived approval coordinator's admission calls per candidate.

    WIRING FACT this suite discovered (Task 12 must know). Two reviewed properties
    collide over a multi-point interval:

    * ``PlanAdmissionService.record_approval`` requires the **reserving instance**
      (``self._candidates`` is per-instance, admission.py:475-477), and the deep
      chain needs one service per point (one run id, one draft, one context); and
    * ``PlanApprovalCoordinator.replay`` re-submits **every** folded decision row
      in the journal through ``_ensure_event`` → ``record_approval``
      (approval.py:939-945).

    So a per-point ``PlanApprovalCoordinator.replay`` over ONE shared journal dies
    at point 2 with ``AdmissionRejected(unknown_candidate)`` — point 2's service
    has never heard of point 1's candidate. (The live wrapper never sees this: it
    builds one coordinator per single-point run.) The honest resolutions are a
    journal per point (which would split the standing lease's own ledger) or ONE
    long-lived coordinator over an admission that dispatches per candidate digest.
    This is the latter, kept deliberately dumb: it forwards, it never decides.
    """

    def __init__(self) -> None:
        self._services: dict[str, object] = {}
        self._approvals: dict[str, dict] = {}

    def bind(self, candidate_plan_digest: str, service, approvals: dict) -> None:
        self._services[candidate_plan_digest] = service
        self._approvals[candidate_plan_digest] = approvals

    def _service_for(self, candidate_id: str):
        service = self._services.get(candidate_id)
        if service is None:
            raise AssertionError(
                f"no reserving PlanAdmissionService bound for candidate "
                f"{candidate_id!r}; the router never invents admission authority")
        return service

    def record_approval(self, candidate_id, approval_input, *, authenticated_actor,
                        idempotency_key):
        return self._service_for(candidate_id).record_approval(
            candidate_id, approval_input, authenticated_actor=authenticated_actor,
            idempotency_key=idempotency_key)

    def approvals_sink(self, approval) -> None:
        self._approvals[approval.candidate_plan_digest][
            (approval.request_id, approval.candidate_plan_digest)] = approval


@dataclasses.dataclass(frozen=True)
class _PointChain:
    """Everything one point's deep run produced — the linkage chain, recorded."""

    ordinal: int
    run_id: str
    draft: object
    candidate_plan_digest: str
    pending: object
    lease_id: str
    approval_event_id: str
    plan: object
    artifact: object
    subject_ref: object
    badges: tuple


class _DeepPresetReplayCoordinator:
    """A :class:`ReplayPlanCoordinator` whose LLM lane IS the sealed deep preset.

    Per decision point, ``llm_proposal`` runs the WHOLE deep chain for THAT point:
    materialize (Task 6) → real ``PlanAdmissionService`` prepare/persist/reserve →
    the Phase-7 lease channel → ``admit_after_approval`` → ONE call of the Task-0b
    production plan runner over the REAL ``dag.run_plan`` with this point's scripted
    gateway → the committed ``PortfolioTargetProposal@1`` sink artifact the driver
    then envelope-wraps.

    Every point gets its own draft id / run id / context snapshot, hence its own
    candidate plan digest and its own admitted plan: nothing resumes, nothing is
    borrowed (which is exactly why the real-path single-plan runner's
    ``MultiPointPlanExecutionRefused`` never fires here — see the module docstring).
    """

    def __init__(self, *, heavy, stores_factory, approvals_coordinator, router,
                 record_digest: str):
        self._heavy = heavy
        self._stores_factory = stores_factory
        self._approvals = approvals_coordinator
        self._router = router
        self._record_digest = record_digest
        self._contexts: dict[int, tuple] = {}
        #: the store each point ran against. The fixture hands every point the
        #: SAME shared instance — Task 8b run-scoped the prompt recovery cell
        #: key and de-forced the per-point split this dict used to carry.
        self.stores_by_point: dict[int, object] = {}
        self.chains: dict[int, _PointChain] = {}
        #: every scripted LLM invocation, in order (worker ids) — the zero-LLM
        #: deterministic-lane proof reads deltas of this list around each port call.
        self.llm_invocations: list[str] = []
        self.det_lane_llm_deltas: list[int] = []
        self.llm_lane_llm_deltas: list[int] = []
        #: open ``_network_sealed`` depth observed at each driver-invoked LLM lane.
        self.seal_depth_at_run: list[int] = []
        self.gateway_factories: dict[int, _GatewayFactory] = {}

    # -- ReplayPlanCoordinator port ----------------------------------------- #
    def _prepare_context(self, point, stores):
        """The point's frozen (fixture) ContextSnapshot, committed into ``stores``.

        Deterministic and idempotent: the same point + store yields the same
        content digest (the canonical empty-memory pair is content-addressed).
        """
        data_context = P.pilot_data_context(as_of=point.decision_as_of)
        memory = P.build_empty_memory_context(
            data_context=data_context, stores=stores,
            registry_digest=self._heavy.registry.registry_digest,
            built_at=point.decision_as_of,
            snapshot_id=f"ctx-replay-p{point.point_ordinal}",
            memory_snapshot_id=f"ms-replay-p{point.point_ordinal}")
        context = memory.context
        ctx_ref = PayloadRef(
            namespace="main", object_id=f"ctx-replay-p{point.point_ordinal}",
            content_digest=context.content_digest)
        return data_context, context, ctx_ref

    def bootstrap_context(self, point) -> ReplayPointSnapshot:
        """The point's frozen (fixture) context — its ``as_of`` IS the decision instant."""
        stores = self._stores_factory(point)
        self.stores_by_point[point.point_ordinal] = stores
        data_context, context, ctx_ref = self._prepare_context(point, stores)
        self._contexts[point.point_ordinal] = (context, ctx_ref)
        return ReplayPointSnapshot(data_context=data_context)

    def llm_proposal(self, point, snapshot):
        # recorded AT the driver's call, so a fixture that dropped the network
        # seal would record 0 here (see the seal test).
        self.seal_depth_at_run.append(_SEAL_DEPTH["n"])
        before = len(self.llm_invocations)
        artifact = self.run_point(
            point, stores=self.stores_by_point[point.point_ordinal],
            run_id=f"replay-deep.p{point.point_ordinal}")
        self.llm_lane_llm_deltas.append(len(self.llm_invocations) - before)
        return artifact

    def deterministic_targets(self, point, snapshot) -> DeterministicBook:
        before = len(self.llm_invocations)
        target_set = derive_deterministic_targets(
            point, factor_scores={CODE: _FACTOR_SCORE}, universe=(SYM,),
            provenance_digest=_SOURCED)
        self.det_lane_llm_deltas.append(len(self.llm_invocations) - before)
        return DeterministicBook(
            rule_id=target_set.rule_id, positions=tuple(target_set.positions),
            cash_weight=target_set.cash_weight)

    # -- the deep chain, per point ------------------------------------------ #
    def run_point(self, point, *, stores, run_id: str):
        """The WHOLE deep chain for one decision point, against ``stores``.

        ``stores`` and ``run_id`` are explicit so ``TestHonestGaps`` can re-run
        a point against the SAME shared store under a fresh run id and prove
        the run-scoped prompt recovery cells keep consecutive deep runs apart.
        """
        heavy = self._heavy
        ordinal = point.point_ordinal
        _dc, context, ctx_ref = self._prepare_context(point, stores)
        clock = _FixedClock(point.decision_as_of)
        rt_digest = heavy.registry.registry_digest

        request = OrchestrationRequest(
            request_id=f"req-{run_id}", goal=live_decide.DEEP_GOAL,
            workflow="orchestrate_only", fallback_preset_id=None,
            approval_policy=ApprovalPolicy.REQUIRED)

        # the run-scoped subject: the ONLY sanctioned carrier of the stock code.
        session_dt = datetime.strptime(
            session_date_of(clock.now()), "%Y-%m-%d").replace(tzinfo=_SESSION_TZ)
        subject = RunSubject(code=CODE, as_of=session_dt)
        subject_ref = live_decide._commit_run_subject(stores, subject, run_id)

        materialized = materialize_deep_decide_draft(
            request=request, preset_registry=heavy.presets,
            context_snapshot_ref=ctx_ref, subject_ref=subject_ref, clock=clock,
            context=context, catalog=heavy.snapshot,
            schema_registry=heavy.registry, draft_id=f"plan-{run_id}", run_id=run_id)
        draft = materialized.draft

        approvals: dict = {}
        run_budget = RunBudget(
            ledger_id=f"led-{run_id}", max_tokens=draft.budget_request_tokens,
            max_llm_invocations=draft.budget_request_llm_invocations,
            max_concurrency=draft.max_concurrency)
        service = PlanAdmissionService(
            run_id=run_id, requests={request.request_id: request},
            drafts={draft.id: draft}, contexts={context.content_digest: context},
            attestations={}, approvals=approvals, catalog=heavy.runtime,
            bridge_view=heavy.view, phase1_registry=heavy.registry,
            runtime_registry_digest=rt_digest, profile=STATIC_RUNTIME_PROFILE_V2,
            stores=stores, run_budget=run_budget, clock=clock)
        preparation = service.prepare_candidate(draft.id, request_id=request.request_id)
        if not preparation.support_report.supported:
            raise AssertionError(
                "the deep draft is not runtime-supported at point "
                f"{ordinal}: "
                + "; ".join(i.code for i in preparation.support_report.issues))
        candidate, reservation = service.persist_and_reserve_candidate(
            preparation, idempotency_key=f"reserve-{run_id}")
        digest = candidate.candidate_plan_digest

        diff = build_plan_diff(draft, request=request, candidate_plan_digest=digest,
                               baseline=None, baseline_kind="none")
        # the Task-7 reviewed card builder — one preset-provenance vocabulary for
        # the whole phase (source=PRESET_FALLBACK is the sealed card enum's only
        # preset-provenance-bearing value; live_decide.py:494-524 records why).
        pending = live_decide._build_pending_card(
            draft=draft, request=request, candidate_plan_digest=digest, diff=diff,
            preset_record_digest=self._record_digest, run_id=run_id,
            requested_at=clock.now())

        # bind this point's reserving service before the ONE long-lived approval
        # coordinator is asked to decide anything about its candidate.
        self._router.bind(digest, service, approvals)
        outcome = self._approvals.register_and_try_lease(
            pending, idempotency_key=f"lease-{run_id}", now=clock.now(),
            candidate_catalog_digest=draft.catalog_digest,
            candidate_registry_digest=draft.schema_registry_digest)
        if outcome.outcome != "lease_admitted":
            raise AssertionError(
                f"point {ordinal} did not admit under the standing replay lease "
                f"(outcome={outcome.outcome!r}); a replay evidence run never "
                "self-approves")

        plan, _admitted = admit_after_approval(
            admission=service, candidate_id=digest,
            reservation_id=reservation.reservation_id,
            approval_event_id=outcome.event.event_id,
            idempotency_key=f"freeze-{run_id}")

        # per-point catalog bundle + gateway: the deterministic handlers and the
        # scripted model outputs are all stamped at THIS point's instant.
        factories = TrustedFactoryRegistry(heavy.runtime)
        factories.register_handler(
            heavy.pa_ref, _pa_handler_factory_for(point.decision_as_of))
        factories.register_handler(
            heavy.ms_ref, _ms_handler_factory_for(point.decision_as_of))
        bundle = ProductionCatalogRuntime(runtime=heavy.runtime, factories=factories)
        gateway_factory = _GatewayFactory(
            as_of=point.decision_as_of, counter=self.llm_invocations)
        self.gateway_factories[ordinal] = gateway_factory

        assembler = SubjectPromptAssembler(subject=subject, subject_ref=subject_ref)
        lane_binding = LaneExecutionBinding(
            lane="main", candidate_plan_digest=digest,
            reservation_id=reservation.reservation_id,
            approval_event_id=outcome.event.event_id)

        def _run_context_factory(*, lane, point, plan, data_context, memory_binding):
            return RunContext(
                run_id=plan.run_id, data=data_context,
                context_snapshot_id=context.snapshot_id,
                memory_snapshot_hash=context.memory_snapshot_hash,
                budget=run_budget, cancellation_token_id=f"cancel-{run_id}")

        runner = build_production_plan_runner(
            stores=stores, catalog_snapshot=heavy.snapshot,
            registry=heavy.registry, gateway_factory=gateway_factory,
            admission=service, lane_bindings={"main": lane_binding},
            run_context_factory=_run_context_factory,
            request_id=request.request_id, clock=clock,
            runtime_registry_digest=rt_digest, runtime_limit=4, catalog=bundle,
            prompt_assembler=assembler)
        artifact = runner(
            lane="main", point=point, approval=None,
            data_context=context.data_context, memory_binding=None,
            candidate_plan_digest=digest)

        self.chains[ordinal] = _PointChain(
            ordinal=ordinal, run_id=run_id, draft=draft,
            candidate_plan_digest=digest, pending=pending,
            lease_id=outcome.lease_id, approval_event_id=outcome.event.event_id,
            plan=plan, artifact=artifact, subject_ref=subject_ref,
            badges=materialized.badges)
        return artifact


# =========================================================================== #
# the one replay run every assertion below reads                               #
# =========================================================================== #
@pytest.fixture(scope="module")
def replay(heavy, tmp_path_factory):
    """Replay the fixture interval with the deep preset, then build both curves."""
    tmp = tmp_path_factory.mktemp("replay-evidence")
    journal = tmp / "plan_approvals.jsonl"
    world = _world()
    record = heavy.presets.get(DEEP_DECIDE_PRESET_ID)
    record_digest = record.semantic_digest()

    clock = _FixedClock(world.points[0].decision_as_of)

    # ── ONE ``RuntimeStores`` shared by EVERY decision point — the natural
    # shape, de-forced by Task 8b. The prompt recovery cell key is now
    # run-scoped (``worker._persist_prompt_record`` folds ``ctx.run_id`` into
    # the digest), so consecutive deep runs of the SAME sealed preset against
    # one shared store no longer recover each other's prompt records. The whole
    # suite passing on this shared store is the end-to-end proof; the flipped
    # pin is ``TestHonestGaps::test_two_deep_runs_on_one_store_both_complete``.
    shared_resolver = SchemaRegistryResolver()
    shared_resolver.register(heavy.registry)
    shared_stores = RuntimeStores(
        resolver=shared_resolver, clock=clock,
        allowed_cell_namespaces=(W.PROMPT_CELL_NAMESPACE,))

    def _stores_for(point) -> RuntimeStores:
        """Every point binds THE one shared store. Its event stamps carry the
        interval's first instant — audit-only; nothing in this suite reads them."""
        return shared_stores

    # ── the STANDING replay lease: evidence-gathering, explicitly scoped ───── #
    issuing = PlanApprovalCoordinator(
        journal, admission=SimpleNamespace(), clock=clock, verifier=_Verifier(),
        console_emit=None, preset_registry=heavy.presets,
        catalog_digest=heavy.snapshot.catalog_digest,
        registry_digest=heavy.registry.registry_digest)
    replay_lease = issuing.issue_lease(
        purpose="回放取证 · deep-decide interval replay (shadow only, no trading authority)",
        preset_id=DEEP_DECIDE_PRESET_ID, preset_record_digest=record_digest,
        catalog_digest=heavy.snapshot.catalog_digest,
        registry_digest=heavy.registry.registry_digest,
        valid_from=world.points[0].decision_as_of - timedelta(days=1),
        valid_until=world.points[-1].decision_as_of + timedelta(days=1),
        max_admissions=20, budget_cap_llm_invocations=200, actor=GOOD_CRED,
        reason="双曲线回放取证运行:本 lease 只授权影子回放,不是盘中授权")

    # ONE long-lived approval coordinator over the ONE journal (see _AdmissionRouter
    # for why it cannot be rebuilt per point), replayed through the durable path.
    router = _AdmissionRouter()
    approvals_coordinator = PlanApprovalCoordinator.replay(
        journal, admission=router, clock=clock, verifier=_Verifier(),
        console_emit=None, approvals_sink=router.approvals_sink,
        preset_registry=heavy.presets,
        catalog_digest=heavy.snapshot.catalog_digest,
        registry_digest=heavy.registry.registry_digest)

    coordinator = _DeepPresetReplayCoordinator(
        heavy=heavy, stores_factory=_stores_for,
        approvals_coordinator=approvals_coordinator, router=router,
        record_digest=record_digest)
    base, sink = _driver_bindings(world.schedule, world.calendar)
    bindings = ReplayRuntimeBindings(
        admission=coordinator, budget=base.budget, run_budget=base.run_budget,
        schedule_registry=base.schedule_registry, calendar=world.calendar,
        clock_factory=base.clock_factory, seats_budget_seam=base.seats_budget_seam,
        intent_ledger=ReplayIntentLedger())
    request = _request(world.schedule)

    with _network_sealed():
        state = run_interval_replay(
            request=request, schedule=world.schedule,
            execution_config=world.config, interval_start=world.start,
            interval_end=world.end, bindings=bindings)
        intents = bindings.intent_ledger.intents
        # BOTH curves come from what the RUN produced: the LLM lane's envelope-
        # wrapped intents and the deterministic lane's book AS THE DRIVER RECORDED
        # IT (luozi.py:2318-2320) — never a fixture recompute. The independent
        # recompute below is kept only as the equality witness
        # (``test_the_deterministic_curve_is_the_drivers_own_book``).
        targets = bindings.intent_ledger.deterministic_targets
        recomputed = tuple(
            derive_deterministic_targets(
                point, factor_scores={CODE: _FACTOR_SCORE}, universe=(SYM,),
                provenance_digest=_SOURCED)
            for point in world.points)
        report = build_dual_curves(
            execution_config=world.config, points=world.points,
            deterministic_target_sets=targets, intents=intents,
            shadow_runner=world.runner)

    # (Minor 4) a SECOND issuing coordinator whose clock is genuinely PAST the
    # replayed interval — so "the evidence predates the lease that cites it" is a
    # real ordering fact, not a tautology of one frozen fixture instant. Bare
    # constructor (not `.replay`): it folds the journal's lease + decision rows
    # without re-submitting decisions to an admission it does not hold.
    after_now = report.interval_end + timedelta(days=1)
    issuing_after = PlanApprovalCoordinator(
        journal, admission=SimpleNamespace(), clock=_FixedClock(after_now),
        verifier=_Verifier(), console_emit=None, preset_registry=heavy.presets,
        catalog_digest=heavy.snapshot.catalog_digest,
        registry_digest=heavy.registry.registry_digest)

    return SimpleNamespace(
        world=world, state=state, report=report, intents=intents, targets=targets,
        coordinator=coordinator, bindings=bindings, sink=sink, record=record,
        record_digest=record_digest, replay_lease=replay_lease, issuing=issuing,
        request=request, recomputed=recomputed, issuing_after=issuing_after,
        after_now=after_now)


# =========================================================================== #
# 1. the deep preset really replayed, per point, through the sanctioned seam    #
# =========================================================================== #
class TestTheDeepPresetReplaysPerDecisionPoint:
    def test_the_seam_is_the_injectable_replay_plan_coordinator(self, replay):
        """The driver consumed OUR coordinator through ``bindings.admission`` —
        the Phase-9 injectable port, not a monkeypatched internal."""
        assert replay.bindings.admission is replay.coordinator
        for method in ("bootstrap_context", "llm_proposal", "deterministic_targets"):
            assert callable(getattr(lz.ReplayPlanCoordinator, method, None))
            assert callable(getattr(replay.coordinator, method))

    def test_every_point_ran_the_deep_preset_to_a_completed_plan(self, replay):
        points = replay.world.points
        assert len(points) == 3
        assert replay.state.completed_points == len(points)
        assert replay.state.total_points == len(points)
        assert replay.state.status is ExperimentStatus.RUNNING
        assert replay.state.request_id == replay.request.request_id
        assert sorted(replay.coordinator.chains) == [p.point_ordinal for p in points]
        for point in points:
            chain = replay.coordinator.chains[point.point_ordinal]
            stores = replay.coordinator.stores_by_point[point.point_ordinal]
            kinds = [e.event_type
                     for e in stores.events.journal(chain.run_id, "main")]
            assert EventType.PLAN_FROZEN in kinds, chain.run_id
            assert EventType.RUN_COMPLETED in kinds, chain.run_id

    def test_each_point_admitted_its_own_plan_nothing_was_borrowed(self, replay):
        """One point ⇒ one draft ⇒ one candidate digest ⇒ one plan ⇒ one artifact."""
        chains = [replay.coordinator.chains[p.point_ordinal]
                  for p in replay.world.points]
        digests = [c.candidate_plan_digest for c in chains]
        assert len(set(digests)) == len(digests)
        assert len({c.plan.plan_digest for c in chains}) == len(chains)
        assert len({c.plan.run_id for c in chains}) == len(chains)
        assert len({c.artifact.content_digest for c in chains}) == len(chains)

    def test_each_points_draft_is_stamped_at_that_points_decision_instant(self, replay):
        """PIT honesty: the admitted deep plan carries the point's own ``as_of``,
        and the GRAPH it carries is the sealed record's, copied verbatim."""
        record = replay.record
        for point in replay.world.points:
            chain = replay.coordinator.chains[point.point_ordinal]
            assert chain.draft.as_of == point.decision_as_of
            assert chain.draft.source is PlanSource.PRESET
            assert chain.draft.universe == ()          # the sealed graph is code-free
            assert tuple(sorted({n.worker_id for n in chain.draft.nodes})) == tuple(
                sorted(DEEP_DECIDE_WORKER_IDS))
            # the executed graph IS the sealed record — not a look-alike.
            assert chain.draft.nodes == record.nodes
            assert chain.draft.debates == record.debates
            assert chain.draft.reducers == record.reducers
            assert chain.draft.sink_node_ids == record.sink_node_ids

    def test_every_materialization_badges_itself_honestly(self, replay):
        """Task 6's badges survive: the run-scoped-subject badge always, and NO
        stale-snapshot badge (each point's snapshot IS that point's session)."""
        for point in replay.world.points:
            chain = replay.coordinator.chains[point.point_ordinal]
            assert "subject_run_scoped_v1" in chain.badges
            assert not any(b.startswith("context_snapshot_stale")
                           for b in chain.badges), chain.badges

    def test_the_run_subject_is_the_only_carrier_of_the_code(self, replay):
        """Task 6's invariant survives the replay: the code enters through the
        committed ``RunSubject@1`` and the per-run assembler, never the draft."""
        stored_by_schema: dict[str, list] = {}
        for stores in replay.coordinator.stores_by_point.values():
            for stored in dict(stores._shared.backend.payloads).values():
                stored_by_schema.setdefault(stored.schema_key, []).append(stored)
        assert stored_by_schema.get("Plan@1")
        assert stored_by_schema.get("RunSubject@1")
        # no six-digit code-shaped run survives ANYWHERE in the plan-side evidence
        # — not as a field value, not embedded in a goal/rationale string.
        for schema_key in _PLAN_SIDE_SCHEMAS:
            for stored in stored_by_schema.get(schema_key, ()):
                found = _code_free(stored.model.model_dump_json())
                assert found is None, (schema_key, found)
        # ...and the subject really IS committed — carried, not implied.
        subjects = stored_by_schema.get("RunSubject@1", ())
        assert subjects and all(
            f'"{CODE}"' in s.model.model_dump_json() for s in subjects)

    def test_the_real_path_multi_point_runner_still_refuses(self, replay):
        """The upstream refusal this task routes around, pinned so nobody
        silently 'fixes' it: ONE approved candidate digest may execute for ONE
        decision point (launcher.py:381)."""
        binding = LaneExecutionBinding(
            lane="main", candidate_plan_digest="d" * 64, reservation_id="res-1",
            approval_event_id="ev-1")
        frozen = SimpleNamespace(sink_node_ids=("trader",), plan_digest="p" * 64)
        admission = SimpleNamespace(
            freeze_and_admit_candidate=lambda *a, **k: (frozen, None))
        runner = build_admitted_plan_runner(
            admission=admission, lane_bindings={"main": binding},
            run_context_factory=lambda **k: SimpleNamespace(),
            request_id="req-pin",
            plan_executor=lambda **k: SimpleNamespace(terminal_status="completed"),
            sink_artifact_resolver=lambda **k: SimpleNamespace(artifact_id="a"))
        first = runner(lane="main", point=SimpleNamespace(point_ordinal=1),
                       approval=None, data_context=None, memory_binding=None,
                       candidate_plan_digest="d" * 64)
        assert first.artifact_id == "a"
        with pytest.raises(MultiPointPlanExecutionRefused):
            runner(lane="main", point=SimpleNamespace(point_ordinal=2),
                   approval=None, data_context=None, memory_binding=None,
                   candidate_plan_digest="d" * 64)


# =========================================================================== #
# 2. both curves under ONE ShadowExecutionConfig attestation                    #
# =========================================================================== #
class TestBothCurvesUnderOneAttestation:
    def test_the_two_curves_bind_one_config_digest(self, replay):
        attestation = replay.world.config.semantic_digest()
        report = replay.report
        # the DRIVER's own returned head binds the same attestation, so the ONE
        # config runs end to end: driver state → both curves → the report.
        assert replay.state.execution_config_digest == attestation
        assert isinstance(report, DualCurveReport)
        assert report.llm_shadow.execution_config_digest == attestation
        assert report.deterministic.execution_config_digest == attestation
        assert report.execution_config.semantic_digest() == attestation
        assert report.decision_point_count == len(replay.world.points)
        assert report.not_causal_attribution is True

    def test_both_curves_are_honest_nav_paths(self, replay):
        report = replay.report
        for series in (report.llm_shadow, report.deterministic):
            assert series.points
            ats = [p.at for p in series.points]
            assert ats == sorted(ats) and len(set(ats)) == len(ats)
        assert report.llm_shadow.applied_intent_digests
        assert report.llm_shadow.rule_id is None
        assert report.deterministic.applied_intent_digests == ()
        assert report.deterministic.rule_id == "deterministic-target-rule-v1"
        assert report.interval_start < report.interval_end

    def test_the_llm_curve_names_exactly_the_deep_runs_intents(self, replay):
        assert replay.report.llm_shadow.applied_intent_digests == tuple(
            sorted({i.semantic_digest() for i in replay.intents}))
        assert len(replay.intents) == len(replay.world.points)


# =========================================================================== #
# 3. the preset linkage — the candidate-plan → preset digest chain              #
# =========================================================================== #
class TestPresetLinkageChain:
    def test_the_report_carries_no_preset_field_so_the_chain_is_the_linkage(self):
        """The brief's fallback, pinned: ``DualCurveReport`` has no preset-linkage
        field today, so the chain below IS the attribution. When Phase 9 grows a
        real field this reddens and the linkage is upgraded to a direct assertion
        instead of being left as a chain forever."""
        fields = set(DualCurveReport.model_fields)
        assert fields == {
            "schema_version", "execution_config", "deterministic", "llm_shadow",
            "interval_start", "interval_end", "decision_point_count",
            "delta_total_return", "not_causal_attribution", "badges"}
        assert not any("preset" in name for name in fields)

    def test_the_lease_binds_the_sealed_preset_record(self, replay):
        lease = replay.replay_lease
        assert lease.preset_id == DEEP_DECIDE_PRESET_ID
        assert lease.preset_record_digest == replay.record_digest
        assert lease.catalog_digest == replay.coordinator._heavy.snapshot.catalog_digest

    def test_every_points_card_carries_the_exact_preset_provenance(self, replay):
        for point in replay.world.points:
            chain = replay.coordinator.chains[point.point_ordinal]
            card = chain.pending
            assert card.preset_id == DEEP_DECIDE_PRESET_ID
            assert card.preset_record_digest == replay.record_digest
            assert card.candidate_plan_digest == chain.candidate_plan_digest
            assert chain.lease_id == replay.replay_lease.lease_id

    def test_the_chain_runs_card_to_plan_to_artifact_to_intent_to_curve(self, replay):
        """preset digest → card → candidate digest → approval event → frozen plan
        → sink artifact → intent → the llm_shadow curve's applied digests."""
        applied = set(replay.report.llm_shadow.applied_intent_digests)
        by_as_of = {i.decision_as_of: i for i in replay.intents}
        assert len(by_as_of) == len(replay.intents)     # one intent per instant
        for point in replay.world.points:
            chain = replay.coordinator.chains[point.point_ordinal]
            # card → the plan that actually froze
            assert chain.plan.plan_digest == chain.candidate_plan_digest
            # approval event → the journal decision the lease minted
            stores = replay.coordinator.stores_by_point[point.point_ordinal]
            approvals = [
                e for e in stores.events.journal(chain.run_id, "main")
                if e.event_id == chain.approval_event_id]
            assert len(approvals) == 1, chain.run_id
            assert approvals[0].event_type is EventType.PLAN_APPROVED
            # frozen plan → the committed sink artifact
            assert chain.artifact.payload_schema_ref == SchemaRef(
                name="PortfolioTargetProposal", version="1")
            assert chain.artifact.run_id == chain.plan.run_id
            # artifact → intent
            intent = by_as_of[point.decision_as_of]
            assert intent.proposal_artifact_id == chain.artifact.artifact_id
            assert intent.proposal_digest == chain.artifact.content_digest
            assert intent.origin == "LLM" and intent.execution_scope == "SHADOW_ONLY"
            assert intent.decision_schedule_digest == replay.world.schedule.content_digest
            # intent → the curve
            assert intent.semantic_digest() in applied

    def test_the_preset_record_digest_is_the_registrys_own(self, replay):
        """No second source of truth for the digest the chain hangs from."""
        assert replay.record_digest == replay.record.semantic_digest()
        assert replay.record.preset_id == DEEP_DECIDE_PRESET_ID


# =========================================================================== #
# 4. zero LLM in the deterministic lane; scripted gateways only; zero network    #
# =========================================================================== #
class TestZeroLlmDeterministicLane:
    def test_the_deterministic_lane_invokes_no_model_at_all(self, replay):
        """Measured, not asserted-about: the model-invocation counter's delta
        around every ``deterministic_targets`` call is zero."""
        coordinator = replay.coordinator
        assert coordinator.det_lane_llm_deltas == [0] * len(replay.world.points)
        assert all(d > 0 for d in coordinator.llm_lane_llm_deltas)

    def test_the_deterministic_budget_nodes_reserve_zero_llm_invocations(self, replay):
        """The driver's own ledger: the ``det.rule#N`` node reserves 0 invocations."""
        det, llm = [], []
        for event in replay.sink.budget_events():
            if event.command.operation != "reserve_node":
                continue
            node_id = event.command.semantic_args.node_id
            (det if node_id.startswith("det.rule#") else llm).append(
                event.command.semantic_args.reserved_llm_invocations)
        assert det == [0] * len(replay.world.points)
        assert len(llm) == len(replay.world.points)

    def test_the_deterministic_curve_is_envelope_free_and_rule_named(self, replay):
        assert replay.report.deterministic.applied_intent_digests == ()
        assert all(t.rule_id == "deterministic-target-rule-v1" for t in replay.targets)
        # the τ=0.15 rule really produced a long book from the sourced scores.
        assert all(t.positions and t.cash_weight == 0.0 for t in replay.targets)

    def test_the_deterministic_curve_is_the_drivers_own_book(self, replay):
        """The curve is built from what the RUN recorded, not a fixture recompute.

        ``replay.targets`` IS ``bindings.intent_ledger.deterministic_targets`` —
        the books the driver appended per point through the coordinator's
        ``deterministic_targets`` port (luozi.py:2318-2320) — and it is what
        ``build_dual_curves`` consumed. The independent recompute is kept ONLY as
        the equality witness: if the two ever diverge, the curve stops describing
        the run and this reddens.
        """
        # (the ledger property mints a fresh tuple per read, so this is content
        # equality by construction — the source claim is carried by the fixture
        # handing THIS tuple to build_dual_curves, and by the recompute below.)
        ledger_books = replay.bindings.intent_ledger.deterministic_targets
        assert replay.targets == ledger_books
        assert len(ledger_books) == len(replay.world.points)
        assert ledger_books == replay.recomputed
        assert [t.point_ordinal for t in ledger_books] == [
            p.point_ordinal for p in replay.world.points]

    def test_every_model_invocation_was_a_scripted_deep_worker(self, replay):
        coordinator = replay.coordinator
        expected = len(_LLM_WORKERS) * len(replay.world.points)
        assert len(coordinator.llm_invocations) == expected
        assert set(coordinator.llm_invocations) == set(_LLM_WORKERS)
        for worker_id in _DETERMINISTIC_WORKERS:
            assert worker_id not in coordinator.llm_invocations
        for factory in coordinator.gateway_factories.values():
            assert factory.gateways
            for gateway in factory.gateways:
                assert isinstance(gateway, _ReplayGateway)

    def test_every_prompt_record_cites_the_points_own_committed_subject(self, replay):
        """The per-run subject-closed assembler ran for every point (E2b), and the
        subject digest it cited is that point's own committed artifact."""
        for point in replay.world.points:
            chain = replay.coordinator.chains[point.point_ordinal]
            factory = replay.coordinator.gateway_factories[point.point_ordinal]
            records = [r for gw in factory.gateways for r in gw.records]
            assert len(records) == len(_LLM_WORKERS)
            cited = set()
            for record in records:
                assert record.assembler_id == live_decide.SUBJECT_ASSEMBLER_ID
                named = [e for e in record.trusted_input_digests
                         if e.name == live_decide.SUBJECT_TRUSTED_INPUT_NAME]
                assert len(named) == 1
                cited.add(named[0].digest)
            assert cited == {chain.subject_ref.payload_ref.content_digest}

    def test_the_whole_run_completed_with_the_network_sealed(self, replay):
        """Not a claim about the seal — a recording of it.

        Every deep run recorded the open-seal depth it entered at, so deleting the
        ``with _network_sealed()`` from the fixture reddens this (a test that only
        exercised the guard in isolation would still pass, which is exactly the
        false-passable shape being avoided).
        """
        depths = replay.coordinator.seal_depth_at_run
        assert len(depths) == len(replay.world.points)
        assert all(d >= 1 for d in depths), depths
        assert _SEAL_DEPTH["n"] == 0            # and the seal was fully unwound
        with pytest.raises(AssertionError, match="network connection"):
            with _network_sealed():
                socket.socket().connect(("example.invalid", 443))
        # ...and the seal is scoped, not blanket: it lets the event loop's own
        # loopback self-pipe through (the only reason it is scoped at all).
        assert _is_loopback(("127.0.0.1", 9)) and not _is_loopback(("10.0.0.1", 9))


# =========================================================================== #
# 5. the evidence convention: a lease reason that cites the report digest        #
# =========================================================================== #
class TestLeaseEvidenceConvention:
    """有证据才签租约 — the curves exist FIRST, the intraday lease cites them.

    Procedural and human-judged by construction (see the module docstring): the
    only structural guarantees are that the reason is a ``NonEmptyStr``, that an
    empty one is refused at issue, and that whatever a human wrote survives to the
    console lease card. Nothing here claims the human actually read the curves.
    """

    @staticmethod
    def _intraday_lease(replay, *, reason: str):
        """Issued from the clock that is genuinely PAST the replayed interval."""
        return replay.issuing_after.issue_lease(
            purpose="盘中 · deep-decide intraday lease (人工执行,观澜绝不下单)",
            preset_id=DEEP_DECIDE_PRESET_ID,
            preset_record_digest=replay.record_digest,
            catalog_digest=replay.coordinator._heavy.snapshot.catalog_digest,
            registry_digest=replay.coordinator._heavy.registry.registry_digest,
            valid_from=replay.report.interval_end,
            valid_until=replay.report.interval_end + timedelta(days=3),
            max_admissions=4, budget_cap_llm_invocations=64, actor=GOOD_CRED,
            reason=reason)

    def test_the_intraday_lease_reason_can_cite_the_report_digest(self, replay):
        digest = replay.report.semantic_digest()
        reason = f"已审阅双曲线回放证据 DualCurveReport@1 digest={digest};签发盘中租约"
        lease = self._intraday_lease(replay, reason=reason)
        assert lease.reason == reason
        assert lease.reason.strip()                      # NonEmptyStr, structurally
        assert digest in lease.reason                    # the convention, as written
        # the evidence really predates the lease that cites it — non-vacuously:
        # the issuing clock is a full day PAST the end of the replayed interval.
        assert lease.issued_at == replay.after_now
        assert lease.issued_at > replay.report.interval_end
        assert replay.report.interval_start < replay.report.interval_end

    def test_the_console_lease_card_displays_the_reason_verbatim(self, replay):
        """``list_leases`` is exactly what ``/plan/approvals/leases`` projects
        (``console/api.py`` → ``"reason": v.lease.reason``)."""
        digest = replay.report.semantic_digest()
        reason = f"已审阅双曲线回放证据 DualCurveReport@1 digest={digest};签发盘中租约"
        self._intraday_lease(replay, reason=reason)
        now = replay.report.interval_end + timedelta(hours=1)
        views = replay.issuing_after.list_leases(now=now)
        reasons = {v.lease.reason for v in views}
        assert reason in reasons
        cited = [v for v in views if v.lease.reason == reason]
        assert len(cited) == 1
        assert cited[0].lease.preset_id == DEEP_DECIDE_PRESET_ID

    def test_an_empty_reason_is_refused_at_issue(self, replay):
        with pytest.raises(ValidationError):
            self._intraday_lease(replay, reason="")
        with pytest.raises(ValidationError):
            self._intraday_lease(replay, reason="   ")

    def test_the_reason_field_is_a_non_empty_str_on_the_contract(self):
        """Phase 7 already enforces it — pinned so the convention has a floor."""
        assert "reason" in ApprovalLease.model_fields
        with pytest.raises(ValidationError):
            ApprovalLease.issue(
                purpose="p", preset_id=DEEP_DECIDE_PRESET_ID,
                preset_record_digest="a" * 64, catalog_digest="b" * 64,
                registry_digest="c" * 64,
                valid_from=datetime(2026, 7, 6, tzinfo=UTC),
                valid_until=datetime(2026, 7, 9, tzinfo=UTC),
                max_admissions=1, budget_cap_llm_invocations=1,
                issued_by="human", issued_at=datetime(2026, 7, 6, tzinfo=UTC),
                reason="")


# =========================================================================== #
# 6. honest gaps (recorded, never dressed up) + the flipped Task 8 pin          #
# =========================================================================== #
class TestHonestGaps:
    def test_two_deep_runs_on_one_store_both_complete(self, replay):
        """FIXED DEFECT — the conscious flip of Task 8's strict-xfail pin.

        Task 8 pinned: the prompt recovery cell key was
        ``content_digest({"node_id", "attempt", "kind"})`` with NO run identity,
        cells are process-global per store backend (eventstore.py:737-740), and
        the sealed deep preset pins its node ids — so run 2 of the same preset
        against the same store recovered run 1's prompt record,
        ``verify_model_request_binding`` refused (``persisted prompt record does
        not equal the request's bound record``, worker.py:1151) and EVERY LLM
        node failed. Task 8b run-scoped the key
        (``worker._persist_prompt_record`` folds ``ctx.run_id`` into the
        digest), and the fixture now runs every decision point on ONE shared
        ``RuntimeStores`` — the de-forced natural shape production's shared
        durable store (``process_durable_stores``) always had.

        This test is the end-to-end proof: a FOURTH deep run of the same sealed
        preset against that same store completes, and its prompt records are
        per-run distinct from the fixture run that executed the same node ids.
        The run-scoped key shape is recomputed here on purpose — it is the pin;
        change the shape in worker.py and this test reddens consciously.
        """
        point = replay.world.points[1]
        coordinator = replay.coordinator
        shared = coordinator.stores_by_point[replay.world.points[0].point_ordinal]
        # the fixture itself already ran all three points on this ONE store.
        assert all(s is shared for s in coordinator.stores_by_point.values())

        chains_before = dict(coordinator.chains)
        factories_before = dict(coordinator.gateway_factories)
        llm_before = len(coordinator.llm_invocations)
        try:
            artifact = coordinator.run_point(
                point, stores=shared, run_id="replay-deep.collision-probe")
            probe_chain = coordinator.chains[point.point_ordinal]
        finally:
            # the probe must not clobber the module-scoped fixture's state for
            # tests that run after this one. ``llm_invocations`` is truncated
            # IN PLACE (the gateway factories hold a reference to this exact
            # list), keeping the exact-count assertion in
            # ``test_every_model_invocation_was_a_scripted_deep_worker``
            # order-independent.
            coordinator.chains.clear()
            coordinator.chains.update(chains_before)
            coordinator.gateway_factories.clear()
            coordinator.gateway_factories.update(factories_before)
            del coordinator.llm_invocations[llm_before:]
        assert artifact is not None

        # both runs completed on the ONE store: the fixture's own run of this
        # very point (the collision victim of the pinned defect) and the probe.
        victim_chain = chains_before[point.point_ordinal]
        for rid in (victim_chain.run_id, "replay-deep.collision-probe"):
            kinds = [e.event_type for e in shared.events.journal(rid, "main")]
            assert EventType.RUN_COMPLETED in kinds, rid
        failed = [
            r.model for r in dict(shared._shared.backend.payloads).values()
            if r.schema_key == "NodeRun@1"
            and r.model.run_id == "replay-deep.collision-probe"
            and r.model.status == "failed"]
        assert failed == []

        # per-run DISTINCT prompt records: the two runs executed the SAME node
        # ids (the sealed preset pins them) but each run has its OWN recovery
        # cell holding its OWN record (the plan digests differ per run).
        assert probe_chain.plan.plan_digest != victim_chain.plan.plan_digest
        llm_nodes = [n.id for n in probe_chain.draft.nodes
                     if n.worker_id in _LLM_WORKERS]
        assert llm_nodes

        def _prompt_cell(run_id: str, node_id: str):
            key = content_digest({"run_id": run_id, "node_id": node_id,
                                  "attempt": 1, "kind": "runtime.prompt"})
            return shared.cells.load(W.PROMPT_CELL_NAMESPACE, key)

        for nid in llm_nodes:
            probe_ref = _prompt_cell("replay-deep.collision-probe", nid)
            victim_ref = _prompt_cell(victim_chain.run_id, nid)
            assert probe_ref is not None and victim_ref is not None, nid
            assert (probe_ref.payload_ref.content_digest
                    != victim_ref.payload_ref.content_digest), nid

    def test_same_run_resume_recovers_the_same_prompt_record(self, replay):
        """Run-scoping the key must NOT break the cell's purpose: crash
        recovery WITHIN one run. Re-executing the persist step for the same
        ``(run, node, attempt)`` against the same store must return the SAME
        record ref through the recovery branch — no second put, no new cell.

        The real same-run re-entry path exists: ``dag.run_plan`` resumes at
        LAYER granularity (dag.py:558-572) — committed layers replay their
        durable node records, but every node in the first UNCOMMITTED layer
        re-executes normally at attempt=1 under ``run_id = plan.run_id``
        (dag.py:510; both RunContext constructors carry it). A crash between
        the prompt-record commit and that layer's barrier therefore re-enters
        ``_persist_prompt_record`` with the same (run, node, attempt), and the
        cell serves exactly that window. This test probes the seam directly —
        and hands the function a stores facade that can ONLY recover: without
        the raiser, the unit-of-work's whole-batch idempotency short-circuit
        (eventstore.py:802-812) would return the stored batch result and
        satisfy every assertion below even with the recovery branch deleted.
        """
        coordinator = replay.coordinator
        shared = coordinator.stores_by_point[replay.world.points[0].point_ordinal]
        chain = coordinator.chains[replay.world.points[0].point_ordinal]
        rid = chain.run_id
        llm_node = next(n.id for n in chain.draft.nodes
                        if n.worker_id in _LLM_WORKERS)
        key = content_digest({"run_id": rid, "node_id": llm_node,
                              "attempt": 1, "kind": "runtime.prompt"})
        original = shared.cells.load(W.PROMPT_CELL_NAMESPACE, key)
        assert original is not None
        stored = dict(shared._shared.backend.payloads)[
            original.payload_ref.object_id]
        assert stored.schema_key == "PromptAssemblyRecord@1"

        payloads_before = len(dict(shared._shared.backend.payloads))
        cells_before = len(dict(shared._shared.backend.cells))

        def _refuse_commit(batch):
            raise AssertionError(
                "recovery branch bypassed: _persist_prompt_record attempted a "
                "second commit for an already-persisted (run, node, attempt)")

        recovery_only = SimpleNamespace(
            cells=shared.cells,
            unit_of_work=SimpleNamespace(commit=_refuse_commit))
        recovered = W._persist_prompt_record(
            stored.model, writer=None, stores=recovery_only,
            ctx=SimpleNamespace(run_id=rid), plan=None,
            node=SimpleNamespace(id=llm_node),
            runtime=SimpleNamespace(
                runtime_registry_digest=coordinator._heavy.registry.registry_digest),
            prompt_token=SimpleNamespace(attempt=1, call_ordinal=1), clock=None)
        assert recovered == original                 # the SAME record, recovered
        assert len(dict(shared._shared.backend.payloads)) == payloads_before
        assert len(dict(shared._shared.backend.cells)) == cells_before

    def test_the_deep_plan_budget_is_not_yet_a_child_of_the_interval_reservation(
            self, replay):
        """RECORDED GAP (Task 12 must read this).

        The driver's invariant 4 — ONE ``RunBudget`` per interval, per-point node
        reservations as its children — holds for the driver's OWN lanes: exactly
        one plan reservation, every node its child. But the deep preset's kernel
        run needs a ``PlanAdmissionService``, and ``persist_and_reserve_candidate``
        mints its OWN plan reservation in its OWN ledger, so today the deep plan's
        spend is a second pool the interval reservation does not parent. This is
        the same "never a second plan pool" rule the production coordinator
        enforces for the Bootstrap node (adapters/api.py:1298) — for the MAIN plan
        it is unresolved. Asserted as it is, not as it should be.
        """
        state = replay.bindings.budget.replay()
        plans = [r for r in state.reservations.values() if r.scope_type == "plan"]
        assert len(plans) == 1                       # the interval's ONE reservation
        for reservation in state.reservations.values():
            if reservation.scope_type == "node":
                assert state.parent_of[reservation.reservation_id] == (
                    plans[0].reservation_id)
        # ...and the deep runs' own ledgers are separate, by ledger id.
        deep_ledgers = {f"led-{c.run_id}" for c in replay.coordinator.chains.values()}
        assert len(deep_ledgers) == len(replay.world.points)
        assert replay.bindings.run_budget.ledger_id not in deep_ledgers

    def test_the_data_context_is_the_fixture_one_not_the_production_pit_context(
            self, replay):
        """RECORDED SCOPE (brief: 'fake data context').

        Each point's context is the reviewed ``pilot_data_context`` frozen at that
        point's ``decision_as_of`` — the driver's invariant-2 binding is therefore
        real, but the PIT manifest / feed-floor degradation half of the production
        ``ProductionReplayPlanCoordinator.bootstrap_context`` is NOT exercised here.
        Task 12's whole-pipeline e2e owns that composition.
        """
        for point in replay.world.points:
            context, _ref = replay.coordinator._contexts[point.point_ordinal]
            assert context.data_context.as_of == point.decision_as_of
            assert context.data_context.data_snapshot_id == "pilot-snap-1"
        assert replay.state.status is ExperimentStatus.RUNNING  # curves are Task 5's
