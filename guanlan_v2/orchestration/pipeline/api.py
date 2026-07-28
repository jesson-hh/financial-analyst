# -*- coding: utf-8 -*-
"""Phase 10 · Task 9 — the pipeline thin router + D7 TA inbox.

Five additive ``/orchestration/pipeline/*`` JSON routes over the Task 2/3/6
surfaces, in the reviewed Phase-9 adapters-router shape (``adapters/api.py``):
an injectable frozen deps carrier whose every ``None`` field answers an honest
``*_unwired`` 503, a process-level binding seam for production wiring, and all
handler I/O through ``asyncio.to_thread`` (the coroutine sync-I/O red line).

**The door never decides.** ``POST /start`` opens exactly ONE of three
REQUIRED-approval candidate paths and STOPS at registration:

* ``source_kind`` — the Task-2 candidate builder produces a ``CandidateSlate``,
  Task 3's :func:`~guanlan_v2.orchestration.pipeline.screening.
  build_screening_batch` materializes one sealed lane per code, and
  :func:`~guanlan_v2.orchestration.pipeline.screening.admit_screening_batch`
  validates → reserves → registers the reviewer cards through the injected
  Phase-7 coordinator. A validation/support refusal is RESERVATION-FREE (the
  Task-3 pre-flight): typed 422, zero reservations, zero journal rows — the
  only writes that survive are the idempotent content-addressed input commits
  (candidate slate + per-code subjects) and the run-budget binding, both
  harmless to replay.
* ``preset_id`` — the Task-6 sealed deep-decide preset is materialized for one
  run-scoped subject (missing subject / context / naive clock → typed 4xx) and
  ONE provenance-true reviewer card is registered (the Task-7 live_decide card
  reconciliation: the sealed card vocabulary carries preset provenance only on
  ``PRESET_FALLBACK``).
* ``goal`` — the Phase-7 dynamic planner path through the injected
  ``planner_runner`` seam (the reviewed ``run_planner`` call shape); a
  ``candidate_ready`` draft becomes ONE pending DYNAMIC card. Production
  wiring binds the seam for real via
  ``assembly.build_production_planner_runner`` over the verified full-catalog
  runtime (Task 11 closed Task-0b concern 2; before that the seam was an
  honest ``None`` → 503).

**The two sanctioned card forms (Task 11 convergence ruling — DOCUMENTED, no
kernel enum widening).** ``PendingPlanApproval`` structurally refuses
``source=PRESET`` (plan_diff.py), so every Phase-10 preset-materialized
candidate cards as ``PRESET_FALLBACK`` with the exact ``(preset_id,
preset_record_digest)`` pair. Two sanctioned producers exist: (1) the Task-3
screening batch, whose request GENUINELY names ``fallback_preset_id`` (the
kernel's own definition of the label — selected-by-fallback, no planner-failure
story encoded); (2) the Task-7/9 deep-decide + preset doors, whose requests
carry ``fallback_preset_id=None`` and label the card ``PRESET_FALLBACK`` with
exact provenance. The controller ruled both HONEST and functionally equivalent:
both carry the exact preset digests, and lease matching
(``register_and_try_lease``) matches digests, NEVER the source label — so the
coherent Phase-10 rule is "card path ⇒ ``PRESET_FALLBACK``", and the kernel
vocabulary stays sealed.

Every mode answers ``status="awaiting_approval"``: the status names the DOOR's
posture — nothing this router did approved anything. A standing human lease may
admit a candidate downstream through the coordinator (reported verbatim in
``outcomes``/``outcome``), but this module NEVER calls ``admit_after_approval``,
never freezes a plan, never dispatches, and never writes an order/signal/trade.
The door is idempotent by derived request identity: a re-POST of the same body
answers the recorded receipt instead of re-reserving (the live_decide re-tick
idiom — the durable record IS the receipt).

``GET /screening/latest`` serves the latest committed ``RecommendationSlate@1``
with the SERVER-SIDE degraded-code join: the contract keeps ``degraded_lanes``
index-only, so the projection joins slate→batch through the router's own durable
request record to NAME the degraded codes (an unjoinable batch stays honestly
``code: null``, never invented). The archive id is day-scoped by the CANDIDATE
slate's ``as_of`` (the Task-4 pinned semantics — the data's day, info not alarm),
and the slate's badges plus the resolved candidate slate's badges
(``stale_ranking:*`` / ``unmappable_codes:<n>`` — examined rows only) surface
verbatim as info.

``POST /ta_ingest`` is the D7 inbox: author-mandatory (blank → 422, never
anonymized), append-only (``var/ta_inbox/<utc-ts>_<digest8>.json`` + one
``index.jsonl`` line), idempotent by content (the ``TaSubmission`` semantic
digest — author+title+text), and FSI-disciplined: the submitted text is
untrusted data, stored VERBATIM as data, never executed, never prompt-injected,
and never echoed in the receipt (only its digest). No LLM, no processing —
pv.curator (#27) consumes the inbox in its own later phase.

The module defines **no** ContractModel subclass (frozen dataclasses + functions
only), so the Phase-1 completeness walk and the classification firewall stay
inert over it — the adapters-router precedent.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from guanlan_v2.orchestration.budget import BudgetLedger
from guanlan_v2.orchestration.context import RunBudget
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.events import EventType
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PendingPlanApproval,
    build_pending_plan_approval,
    build_plan_diff,
    render_plan_diff_md,
)
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import SchemaRegistry
from guanlan_v2.orchestration.spec import OrchestrationRequest

from guanlan_v2.orchestration.pipeline.candidates import (
    CandidateParamsError,
    CandidatePorts,
    CandLane0Params,
    CandModelParams,
    CandV4Params,
    Lane0LeadersUnavailable,
    RankingSourceUnavailable,
    build_lane0_slate,
    build_model_variant_slate,
    build_v4_slate,
)
from guanlan_v2.orchestration.pipeline.contracts import (
    CandidateSlate,
    RunSubject,
    TaSubmission,
)
from guanlan_v2.orchestration.pipeline.deep_decide import (
    DEEP_DECIDE_PRESET_ID,
    DeepDecideError,
    materialize_deep_decide_draft,
)
from guanlan_v2.orchestration.pipeline.screening import (
    CANDIDATE_SLATE_SCHEMA_REF,
    RECOMMENDATION_ADVISORY_BANNER,
    RUN_SUBJECT_SCHEMA_REF,
    SCREENING_LANE_PRESET_ID,
    BatchAdmissionRefused,
    ScreeningError,
    admit_screening_batch,
    build_screening_batch,
    recommendation_archive_id,
    session_date_of,
)

__all__ = [
    # router
    "PIPELINE_ROUTER_PREFIX",
    "PIPELINE_ROUTE_PATHS",
    "PipelineRouterDeps",
    "build_pipeline_router",
    "set_pipeline_router_deps",
    "process_pipeline_router_deps",
    # durable request records
    "JsonlPipelineRequestStore",
    # stores-backed ports
    "build_stores_subject_committer",
    # production wiring
    "build_production_pipeline_deps",
    "bind_production_pipeline_deps",
]

_LOG = logging.getLogger("guanlan.orchestration.pipeline.api")

#: one additive mount in ``server.py`` (the D8 lazy-import + loud-skip idiom).
PIPELINE_ROUTER_PREFIX = "/orchestration/pipeline"

#: the EXACT five route paths this router adds — a reviewed closed surface
#: (the route-table snapshot test asserts nothing else appears).
PIPELINE_ROUTE_PATHS: tuple[str, ...] = (
    "/orchestration/pipeline/start",
    "/orchestration/pipeline/state",
    "/orchestration/pipeline/runs",
    "/orchestration/pipeline/screening/latest",
    "/orchestration/pipeline/ta_ingest",
)

#: the closed source-kind vocabulary (Task 1's ``CandidateSourceKind``).
_SOURCE_KINDS = ("v4", "lane0", "model_variant")

#: reviewed defaults for the source_kind door (inside the Task-2 envelopes).
_DEFAULT_TOP_N = 10
_DEFAULT_MAINLINE_LIMIT = 3

#: the deliberately CODE-FREE goals — the subject enters only through the
#: committed ``RunSubject@1`` / the slate lanes' run identity, never free text.
_SCREEN_GOAL = "观澜 · 编排选股批量研判(run-scoped subjects)"
_DEEP_GOAL = "观澜 · 编排深度研判(run-scoped subject)"

#: the +08:00 session zone the deep subject's deterministic ``as_of`` anchors to.
_SESSION_TZ = timezone(timedelta(hours=8))

#: run journal terminal event types (the honest run-status fold for /state).
_TERMINAL_EVENTS = {
    EventType.RUN_COMPLETED: "completed",
    EventType.RUN_FAILED: "failed",
    EventType.RUN_CANCELLED: "cancelled",
}


# =========================================================================== #
# deps carrier + process binding (the reviewed AdaptersRouterDeps idiom)        #
# =========================================================================== #
@dataclasses.dataclass(frozen=True)
class PipelineRouterDeps:
    """The injected bindings the five routes read.

    Every field defaults to ``None`` ⇒ the depending route answers an honest 503
    (``{ok: false, reason: "<field>_unwired"}``), never a fake empty success.
    Production wiring binds the process durable stores + the sealed chain via
    :func:`build_production_pipeline_deps`; tests inject tmp / in-memory
    equivalents (invariant 4).
    """

    #: the Phase-9 ``RuntimeStores`` (durable in production, in-memory in tests).
    stores: Any = None
    #: the sealed Phase-10 preset registry (v1 baseline + both v2 presets).
    preset_registry: Any = None
    #: the sealed ``WorkerCatalogSnapshot`` the drafts bind.
    catalog: Any = None
    #: the sealed cumulative schema registry the drafts bind.
    schema_registry: Any = None
    #: the authoritative clock port (aware datetimes; naive → honest 503).
    clock: Any = None
    #: the Task-2 ``RankingReader`` port (production: the ratified-D5 reader).
    ranking_reader: Any = None
    #: the Task-3 ``SubjectCommitter`` port (production: stores-backed).
    subject_committer: Any = None
    #: ``() -> (ContextSnapshot, PayloadRef) | None`` — latest committed Lane-0
    #: snapshot (the Task-7 port; ``None`` result ⇒ typed refusal).
    latest_snapshot_fn: Callable[[], Any] | None = None
    #: per-run admission factory ``(*, run_id, requests, drafts, context,
    #: approvals, run_budget) -> PlanAdmissionService`` (the Task-7 ABI, widened
    #: to N requests/drafts for the screening batch).
    admission_factory: Callable[..., Any] | None = None
    #: per-run coordinator factory ``(*, admission, approvals_sink)`` over the
    #: ONE durable lease journal (the Task-7 ABI verbatim).
    coordinator_factory: Callable[..., Any] | None = None
    #: the goal-mode planner seam ``(*, request, context, context_snapshot_ref,
    #: run_id, draft_id) -> PlannerResult`` (the reviewed ``run_planner`` shape).
    #: Production is honestly ``None`` until Task 11 lands the catalog-runtime
    #: materials the planner assembly needs.
    planner_runner: Callable[..., Any] | None = None
    #: the durable pipeline request-record store (jsonl under the store root).
    request_store: Any = None
    #: the D7 TA inbox directory (production: ``var/ta_inbox``; tests: tmp).
    ta_inbox_dir: Path | str | None = None


_PROCESS_ROUTER_DEPS = PipelineRouterDeps()


def set_pipeline_router_deps(deps: PipelineRouterDeps) -> None:
    """Bind the process-level router dependencies (the production wiring seam)."""
    global _PROCESS_ROUTER_DEPS
    if not isinstance(deps, PipelineRouterDeps):
        raise TypeError("set_pipeline_router_deps takes a PipelineRouterDeps")
    _PROCESS_ROUTER_DEPS = deps


def process_pipeline_router_deps() -> PipelineRouterDeps:
    """The process-level deps (an all-``None`` carrier until bound)."""
    return _PROCESS_ROUTER_DEPS


class _DepsView:
    """Explicit deps freeze at build time; ``None`` resolves per request, so one
    production ``set_pipeline_router_deps`` call takes effect without a re-mount
    (the reviewed adapters-router ``_DepsView`` idiom)."""

    __slots__ = ("_fixed",)

    def __init__(self, deps: PipelineRouterDeps | None) -> None:
        self._fixed = deps

    def __getattr__(self, name: str) -> Any:
        source = self._fixed if self._fixed is not None else _PROCESS_ROUTER_DEPS
        return getattr(source, name)


# =========================================================================== #
# small pure helpers                                                           #
# =========================================================================== #
def _fail(reason: str, status: int, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"ok": False, "reason": reason}
    body.update(extra)
    return JSONResponse(body, status_code=status)


def _unwired(d: Any, *names: str) -> JSONResponse | None:
    for name in names:
        if getattr(d, name) is None:
            return _fail(f"{name}_unwired", 503)
    return None


def _aware_now(clock: Any) -> datetime | None:
    """The clock's instant, or ``None`` for a naive one (refused, never coerced:
    a naive datetime silently shifts the +08:00 session date)."""
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or (
            now.utcoffset() is None):
        return None
    return now


def _derive_request_id(prefix: str, payload: Mapping[str, Any]) -> str:
    """A deterministic request id: the same semantic door input always names the
    same request (the adapters-router idiom) — the idempotent-door key."""
    return f"{prefix}-{content_digest(dict(payload))[:16]}"


# =========================================================================== #
# durable request records (append-only jsonl; last row per id wins)             #
# =========================================================================== #
class JsonlPipelineRequestStore:
    """The router's durable request-record store: one append-only jsonl file.

    Production binds it under the durable store root
    (``<store root>/pipeline_requests.jsonl``); tests bind a tmp path — the same
    class either way, so the durability behaviour under test IS the production
    behaviour. Append-only: an update is a new row for the same ``request_id``
    and the LAST row wins on read; nothing is ever rewritten in place.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def record(self, entry: Mapping[str, Any]) -> None:
        row = dict(entry)
        if not str(row.get("request_id") or "").strip():
            raise ValueError("a pipeline request record needs a request_id")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _fold(self) -> dict[str, dict[str, Any]]:
        folded: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return folded
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001 — a bad line is skipped, never repaired
                continue
            if isinstance(row, dict) and row.get("request_id"):
                rid = str(row["request_id"])
                folded.pop(rid, None)  # re-insert → recency order = last write
                folded[rid] = row
        return folded

    def get(self, request_id: str) -> dict[str, Any] | None:
        return self._fold().get(str(request_id))

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = list(self._fold().values())
        rows.reverse()  # most recently written first
        return rows[: max(0, int(limit))]

    def by_batch(self, batch_id: str) -> dict[str, Any] | None:
        for row in self._fold().values():
            if str(row.get("batch_id") or "") == str(batch_id):
                return row
        return None


# =========================================================================== #
# stores-backed ports (the live_decide side-registry commit idiom)              #
# =========================================================================== #
_PIPELINE_REGISTRY: SchemaRegistry | None = None


def _pipeline_registry() -> SchemaRegistry:
    """A minimal sealed side registry validating the router's own commits.

    The cumulative chain registry does not hold ``RunSubject@1`` /
    ``CandidateSlate@1`` yet (their registration is Task 11's); the commits
    still go through the registry-validated payload store — under this explicit
    side registry — rather than around it (the live_decide precedent).
    """
    global _PIPELINE_REGISTRY
    if _PIPELINE_REGISTRY is None:
        registry = SchemaRegistry()
        registry.register(RunSubject)
        registry.register(CandidateSlate)
        registry.seal()
        _PIPELINE_REGISTRY = registry
    return _PIPELINE_REGISTRY


def build_stores_subject_committer(stores: Any) -> Callable[[RunSubject], TypedPayloadRef]:
    """The production ``SubjectCommitter``: persist one ``RunSubject@1`` through
    the registry-validated payload store and return its digest-bound typed ref."""

    def commit(subject: RunSubject) -> TypedPayloadRef:
        digest = stores.resolver.register(_pipeline_registry())
        ref = stores.payloads.put(
            RUN_SUBJECT_SCHEMA_REF, subject, registry_digest=digest,
            namespace="main",
            idempotency_key=f"pipeline-subject:{subject.semantic_digest()[:32]}")
        return TypedPayloadRef(schema_ref=RUN_SUBJECT_SCHEMA_REF, payload_ref=ref)

    return commit


def _commit_candidate_slate(stores: Any, slate: CandidateSlate) -> TypedPayloadRef:
    digest = stores.resolver.register(_pipeline_registry())
    ref = stores.payloads.put(
        CANDIDATE_SLATE_SCHEMA_REF, slate, registry_digest=digest,
        namespace="main",
        idempotency_key=f"pipeline-slate:{slate.semantic_digest()[:32]}")
    return TypedPayloadRef(schema_ref=CANDIDATE_SLATE_SCHEMA_REF, payload_ref=ref)


# =========================================================================== #
# start — shared admission plumbing                                             #
# =========================================================================== #
def _resolve_snapshot(d: Any):
    """The latest committed Lane-0 snapshot pair, or ``None`` (typed refusal —
    a start never re-runs Lane 0 and never fabricates a context)."""
    try:
        pair = d.latest_snapshot_fn()
    except Exception as exc:  # noqa: BLE001 — a failing port is an absence
        _LOG.warning("latest-snapshot port failed: %s", exc)
        return None
    if not pair:
        return None
    return pair


def _deep_pending_card(*, draft, request, candidate_plan_digest, diff, diff_ref,
                       preset_record_digest, run_id, requested_at) -> PendingPlanApproval:
    """The deep-decide reviewer card (the Task-7 live_decide reconciliation).

    The deep draft is ``source=PRESET`` (Task 6), but the sealed card vocabulary
    carries preset provenance only on ``PRESET_FALLBACK`` (plan_diff.py:261-293)
    — the provenance pair itself (preset id + record digest) is exact and is what
    a lease matches on. Unlike live_decide's in-memory card, the diff ref here is
    REALLY committed (the Task-3 committed-ref idiom), so the card resolves.
    """
    return PendingPlanApproval(
        request_id=request.request_id,
        candidate_plan_digest=candidate_plan_digest,
        goal=request.goal,
        source=PlanSource.PRESET_FALLBACK,
        approval_policy=draft.approval_policy,
        node_count=len(draft.nodes),
        worker_ids=tuple(sorted({n.worker_id for n in draft.nodes})),
        budget_request_tokens=draft.budget_request_tokens,
        budget_request_llm_invocations=draft.budget_request_llm_invocations,
        plan_diff_ref=diff_ref,
        rendered_md=render_plan_diff_md(diff),
        rendered_from_diff_digest=diff.semantic_digest(),
        planner_rationale=None,
        candidate_id=f"pipeline-card.{candidate_plan_digest[:16]}",
        requested_at=requested_at,
        preset_id=DEEP_DECIDE_PRESET_ID,
        preset_record_digest=preset_record_digest)


def _single_plan_admit(d: Any, *, request, draft, context, now, run_id,
                       expected_digest: str | None, card_builder):
    """Prepare → (typed 422 on refusal, WRITE-FREE) → reserve → register ONE card.

    The single-plan mirror of the Task-3 batch pre-flight: ``prepare_candidate``
    runs the unmodified Phase-1 validator + the runtime-support check BEFORE any
    report payload, reservation or journal row exists, so an invalid draft
    refuses with zero reservations (invariant 2). Never freezes, never runs.
    Returns ``(response | None, result dict)``.
    """
    from guanlan_v2.orchestration.admission import AdmissionRejected

    approvals: dict = {}
    run_budget = RunBudget(
        ledger_id=f"led-{run_id}",
        max_tokens=draft.budget_request_tokens,
        max_llm_invocations=draft.budget_request_llm_invocations,
        max_concurrency=draft.max_concurrency)
    d.stores.bind_run_budget(run_id=run_id, run_budget=run_budget)
    service = d.admission_factory(
        run_id=run_id, requests={request.request_id: request},
        drafts={draft.id: draft}, context=context, approvals=approvals,
        run_budget=run_budget)
    try:
        prep = service.prepare_candidate(draft.id, request_id=request.request_id)
    except AdmissionRejected as exc:
        return _fail("admission_refused", 422,
                     detail=f"[{exc.code}] {exc}"), None
    if expected_digest is not None and prep.candidate_plan_digest != expected_digest:
        return _fail("candidate_digest_drift", 422, detail=(
            "the prepared candidate digest does not recompute to the one the "
            "planner reported; an approval that does not bind the plan the "
            "reviewer saw is worthless")), None
    if not prep.phase1_report.valid:
        return _fail("invalid_draft", 422, issue_codes=sorted(
            {i.code for i in prep.phase1_report.issues})), None
    if not prep.support_report.supported:
        return _fail("unsupported_draft", 422, issue_codes=sorted(
            {i.code for i in prep.support_report.issues})), None
    digest = prep.candidate_plan_digest

    service.persist_and_reserve_candidate(
        prep, idempotency_key=f"pipeline-reserve:{run_id}")
    diff = build_plan_diff(
        draft, request=request, candidate_plan_digest=digest,
        baseline=None, baseline_kind="none")
    payload_ref = d.stores.payloads.put(
        PLAN_DIFF_SCHEMA_REF, diff,
        registry_digest=d.schema_registry.registry_digest, namespace="main",
        idempotency_key=f"pipeline-plan-diff:{run_id}")
    diff_ref = TypedPayloadRef(schema_ref=PLAN_DIFF_SCHEMA_REF,
                               payload_ref=payload_ref)
    pending = card_builder(
        draft=draft, request=request, candidate_plan_digest=digest, diff=diff,
        diff_ref=diff_ref, now=now)
    coordinator = d.coordinator_factory(
        admission=service,
        approvals_sink=lambda ap: approvals.__setitem__(
            (ap.request_id, ap.candidate_plan_digest), ap))
    outcome = coordinator.register_and_try_lease(
        pending, idempotency_key=f"pipeline-card:{run_id}", now=now,
        candidate_catalog_digest=draft.catalog_digest,
        candidate_registry_digest=draft.schema_registry_digest)
    # THE STOP LINE: no admit_after_approval, no freeze, no dispatch — even a
    # lease-admitted candidate awaits the downstream driver.
    return None, {"digest": digest, "outcome": outcome.outcome,
                  "run_budget": run_budget}


# =========================================================================== #
# start — the three modes                                                       #
# =========================================================================== #
_SOURCE_KIND_DEPS = (
    "stores", "preset_registry", "catalog", "schema_registry", "clock",
    "ranking_reader", "subject_committer", "latest_snapshot_fn",
    "admission_factory", "coordinator_factory", "request_store")

_PRESET_DEPS = (
    "stores", "preset_registry", "catalog", "schema_registry", "clock",
    "subject_committer", "latest_snapshot_fn", "admission_factory",
    "coordinator_factory", "request_store")

_GOAL_DEPS = (
    "stores", "schema_registry", "clock", "planner_runner",
    "latest_snapshot_fn", "admission_factory", "coordinator_factory",
    "request_store")


def _replay_receipt(d: Any, request_id: str) -> JSONResponse | None:
    """The idempotent door: a re-POST answers the durable receipt, re-reserving
    nothing (the live_decide re-tick idiom — the prior record IS the receipt)."""
    existing = d.request_store.get(request_id)
    if existing is None or not isinstance(existing.get("response"), dict):
        return None
    return JSONResponse({**existing["response"], "replayed": True})


def _record_and_respond(d: Any, *, request_id: str, mode: str, as_of: str,
                        now: datetime, response: dict[str, Any],
                        extra_record: dict[str, Any] | None = None) -> JSONResponse:
    record = {
        "request_id": request_id, "mode": mode,
        "status": response.get("status"), "as_of": as_of,
        "created_at": now.isoformat(), "response": response,
    }
    record.update(extra_record or {})
    d.request_store.record(record)
    return JSONResponse(response)


def _start_source_kind(d: Any, body: Mapping[str, Any]) -> JSONResponse:
    kind = str(body.get("source_kind") or "").strip()
    if kind not in _SOURCE_KINDS:
        return _fail("bad_source_kind", 422, accepted=list(_SOURCE_KINDS))
    refused = _unwired(d, *_SOURCE_KIND_DEPS)
    if refused is not None:
        return refused
    now = _aware_now(d.clock)
    if now is None:
        return _fail("naive_clock", 503)

    top_n = body.get("top_n", _DEFAULT_TOP_N)
    ports = CandidatePorts(as_of=now, ranking_reader=d.ranking_reader)
    try:
        if kind == "v4":
            slate = build_v4_slate(
                params=CandV4Params.from_mapping({"top_n": top_n}), ports=ports)
        elif kind == "model_variant":
            variant_id = str(body.get("variant_id") or "").strip()
            if not variant_id:
                return _fail("missing_variant_id", 422)
            slate = build_model_variant_slate(
                params=CandModelParams.from_mapping(
                    {"top_n": top_n, "variant_id": variant_id}), ports=ports)
        else:  # lane0 — the ratified-D4 honest refusal in v1
            slate = build_lane0_slate(
                params=CandLane0Params.from_mapping(
                    {"top_n": top_n, "mainline_limit": _DEFAULT_MAINLINE_LIMIT}),
                ports=ports)
    except Lane0LeadersUnavailable as exc:
        return _fail("lane0_leaders_unavailable", 422, detail=str(exc))
    except CandidateParamsError as exc:
        return _fail("bad_params", 422, detail=str(exc))
    except RankingSourceUnavailable as exc:
        return _fail("ranking_source_unavailable", 503, detail=str(exc))
    if not slate.entries:
        # an empty slate is an honest product; a zero-candidate batch is not.
        return _fail("no_candidates", 422, slate_badges=list(slate.badges))

    pair = _resolve_snapshot(d)
    if pair is None:
        return _fail("context_snapshot_unavailable", 422, detail=(
            "no committed Lane-0 ContextSnapshot is available; refusing rather "
            "than fabricating a context"))
    context, ctx_ref = pair

    request_id = _derive_request_id("pipeline-screen", {
        "source_kind": kind, "slate": slate.semantic_digest(),
        "context": context.content_digest})
    replay = _replay_receipt(d, request_id)
    if replay is not None:
        return replay

    slate_ref = _commit_candidate_slate(d.stores, slate)
    base_request = OrchestrationRequest(
        request_id=request_id, goal=_SCREEN_GOAL, workflow="orchestrate_only",
        fallback_preset_id=SCREENING_LANE_PRESET_ID,
        approval_policy=ApprovalPolicy.REQUIRED)
    try:
        build = build_screening_batch(
            slate,
            preset_registry=d.preset_registry, clock=d.clock,
            base_request=base_request, slate_ref=slate_ref,
            subject_committer=d.subject_committer, context=context,
            context_snapshot_ref=ctx_ref, catalog=d.catalog,
            schema_registry=d.schema_registry)
    except ScreeningError as exc:
        return _fail("screening_refused", 422,
                     detail=f"{type(exc).__name__}: {exc}")
    batch = build.batch
    preview = batch.cost_preview

    run_id = f"pipeline-screen-{batch.batch_id[:16]}"
    # the run budget is EXACTLY the human's whole picture (the preview totals);
    # concurrency is a per-lane slot cap the preview deliberately does not sum,
    # but the ledger's reserve arithmetic holds each lane's cap simultaneously,
    # so the run-level bound is n_codes × per-lane (a capacity ceiling, not a
    # parallelism promise).
    run_budget = RunBudget(
        ledger_id=f"led-{batch.batch_id[:16]}",
        max_tokens=preview.total_budget_tokens,
        max_llm_invocations=preview.total_budget_llm_invocations,
        max_concurrency=preview.n_codes * preview.per_lane_max_concurrency)
    d.stores.bind_run_budget(run_id=run_id, run_budget=run_budget)
    ledger = BudgetLedger(
        sink=d.stores.budget_event_sink(
            run_id=run_id, ledger_id=run_budget.ledger_id),
        run_budget=run_budget)
    approvals: dict = {}
    service = d.admission_factory(
        run_id=run_id,
        requests={lane.request.request_id: lane.request for lane in build.lanes},
        drafts={lane.draft.id: lane.draft for lane in build.lanes},
        context=context, approvals=approvals, run_budget=run_budget)
    coordinator = d.coordinator_factory(
        admission=service,
        approvals_sink=lambda ap: approvals.__setitem__(
            (ap.request_id, ap.candidate_plan_digest), ap))
    try:
        outcomes = admit_screening_batch(
            batch, coordinator=coordinator, admission=service, now=now,
            payloads=d.stores.payloads,
            registry_digest=d.schema_registry.registry_digest, budget=ledger)
    except BatchAdmissionRefused as exc:
        # invariant 2: the Task-3 pre-flight refused WRITE-FREE — zero
        # reservations, zero journal rows, zero report payloads, no record.
        return _fail("batch_admission_refused", 422, detail=str(exc))

    lanes = [
        {"lane_index": lane.lane_index, "code": lane.code,
         "request_id": lane.request.request_id, "draft_id": lane.draft.id,
         "run_id": lane.draft.run_id,
         "candidate_plan_digest": lane.candidate_plan_digest}
        for lane in build.lanes
    ]
    response = {
        "ok": True, "request_id": request_id, "mode": "source_kind",
        "source_kind": kind, "status": "awaiting_approval",
        "batch_id": batch.batch_id,
        # the batch id IS the digest identity binding all N per-lane candidate
        # digests (slate ref + request semantics + context + preset + chain);
        # the true per-lane digests travel on ``lanes``.
        "candidate_plan_digest": batch.batch_id,
        "cost_preview": json.loads(preview.model_dump_json()),
        "lanes": lanes,
        "outcomes": [o.outcome for o in outcomes],
        "badges": list(batch.badges),
        "slate_badges": list(slate.badges),
        "slate_ref": {
            "object_id": slate_ref.payload_ref.object_id,
            "content_digest": slate_ref.payload_ref.content_digest},
        "approval_policy": ApprovalPolicy.REQUIRED.value,
    }
    return _record_and_respond(
        d, request_id=request_id, mode="source_kind",
        as_of=slate.as_of.isoformat(), now=now, response=response,
        extra_record={"batch_id": batch.batch_id, "lanes": lanes})


def _start_preset(d: Any, body: Mapping[str, Any]) -> JSONResponse:
    preset_id = str(body.get("preset_id") or "").strip()
    if preset_id != DEEP_DECIDE_PRESET_ID:
        return _fail("unsupported_preset", 422, detail=(
            f"only {DEEP_DECIDE_PRESET_ID!r} starts through the preset door "
            "(the screening lane starts through source_kind)"))
    refused = _unwired(d, *_PRESET_DEPS)
    if refused is not None:
        return refused
    now = _aware_now(d.clock)
    if now is None:
        return _fail("naive_clock", 503)
    code = str(body.get("code") or "").strip()
    if not code:
        return _fail("missing_subject_code", 422, detail=(
            "a deep-decide run needs a run-scoped subject; supply body.code — "
            "the sealed graph carries no code (SubjectRefused semantics)"))

    session_date = session_date_of(now)
    session_dt = datetime.strptime(session_date, "%Y-%m-%d").replace(
        tzinfo=_SESSION_TZ)
    try:
        subject = RunSubject(code=code, as_of=session_dt)
    except Exception:  # noqa: BLE001 — outside the Phase-3 grammar, never guessed
        return _fail("bad_code", 422, detail=(
            f"{code!r} is outside the Phase-3 symbol grammar"))

    pair = _resolve_snapshot(d)
    if pair is None:
        return _fail("context_snapshot_unavailable", 422, detail=(
            "no committed Lane-0 ContextSnapshot is available "
            "(ContextSnapshotRefused semantics)"))
    context, ctx_ref = pair

    request_id = _derive_request_id("pipeline-deep", {
        "preset_id": preset_id, "code": subject.code, "session": session_date,
        "context": context.content_digest})
    replay = _replay_receipt(d, request_id)
    if replay is not None:
        return replay
    run_id = f"pipeline-deep-{content_digest([subject.code, session_date])[:12]}"

    subject_ref = d.subject_committer(subject)
    request = OrchestrationRequest(
        request_id=request_id, goal=_DEEP_GOAL, workflow="orchestrate_only",
        fallback_preset_id=None, approval_policy=ApprovalPolicy.REQUIRED)
    try:
        materialized = materialize_deep_decide_draft(
            request=request, preset_registry=d.preset_registry,
            context_snapshot_ref=ctx_ref, subject_ref=subject_ref,
            clock=d.clock, context=context, catalog=d.catalog,
            schema_registry=d.schema_registry, draft_id=f"plan-{run_id}",
            run_id=run_id)
    except DeepDecideError as exc:
        return _fail("deep_decide_refused", 422,
                     detail=f"{type(exc).__name__}: {exc}")
    draft = materialized.draft
    record_digest = d.preset_registry.get(DEEP_DECIDE_PRESET_ID).semantic_digest()

    def card_builder(*, draft, request, candidate_plan_digest, diff, diff_ref, now):
        return _deep_pending_card(
            draft=draft, request=request,
            candidate_plan_digest=candidate_plan_digest, diff=diff,
            diff_ref=diff_ref, preset_record_digest=record_digest,
            run_id=run_id, requested_at=now)

    refusal, result = _single_plan_admit(
        d, request=request, draft=draft, context=context, now=now,
        run_id=run_id, expected_digest=None, card_builder=card_builder)
    if refusal is not None:
        return refusal

    response = {
        "ok": True, "request_id": request_id, "mode": "preset",
        "preset_id": preset_id, "status": "awaiting_approval",
        "run_id": run_id, "candidate_plan_digest": result["digest"],
        "cost_preview": {
            "n_plans": 1,
            "budget_request_tokens": draft.budget_request_tokens,
            "budget_request_llm_invocations": draft.budget_request_llm_invocations,
            "max_concurrency": draft.max_concurrency,
            "node_count": len(draft.nodes)},
        "outcome": result["outcome"],
        "badges": list(materialized.badges),
        "approval_policy": ApprovalPolicy.REQUIRED.value,
    }
    return _record_and_respond(
        d, request_id=request_id, mode="preset",
        as_of=context.data_context.as_of.isoformat(), now=now,
        response=response,
        extra_record={"run_id": run_id, "code": subject.code,
                      "lanes": [{"lane_index": 0, "code": subject.code,
                                 "run_id": run_id,
                                 "candidate_plan_digest": result["digest"]}]})


def _start_goal(d: Any, body: Mapping[str, Any]) -> JSONResponse:
    goal = str(body.get("goal") or "").strip()
    refused = _unwired(d, *_GOAL_DEPS)
    if refused is not None:
        return refused
    now = _aware_now(d.clock)
    if now is None:
        return _fail("naive_clock", 503)
    pair = _resolve_snapshot(d)
    if pair is None:
        return _fail("context_snapshot_unavailable", 422)
    context, ctx_ref = pair

    request_id = _derive_request_id("pipeline-goal", {
        "goal": goal, "context": context.content_digest})
    replay = _replay_receipt(d, request_id)
    if replay is not None:
        return replay
    run_id = f"pipeline-goal-{content_digest([goal, context.content_digest])[:12]}"
    request = OrchestrationRequest(
        request_id=request_id, goal=goal, workflow="orchestrate_only",
        fallback_preset_id=None, approval_policy=ApprovalPolicy.REQUIRED)

    result = d.planner_runner(
        request=request, context=context, context_snapshot_ref=ctx_ref,
        run_id=run_id, draft_id=f"plan-{run_id}")
    outcome_name = str(getattr(result.record, "terminal_outcome", "unknown"))
    if result.draft is None or outcome_name != "candidate_ready":
        # the planner's honest halt — never a substitute plan minted here.
        return _fail(f"planner_{outcome_name}", 422)
    draft = result.draft
    reported = str(result.report.candidate_plan_digest)

    def card_builder(*, draft, request, candidate_plan_digest, diff, diff_ref, now):
        return build_pending_plan_approval(
            draft=draft, request=request,
            candidate_plan_digest=candidate_plan_digest, diff=diff,
            plan_diff_ref=diff_ref, planner_rationale=None,
            candidate_id=f"pipeline-card.{candidate_plan_digest[:16]}",
            requested_at=now)

    refusal, result_admit = _single_plan_admit(
        d, request=request, draft=draft, context=context, now=now,
        run_id=run_id, expected_digest=reported, card_builder=card_builder)
    if refusal is not None:
        return refusal

    response = {
        "ok": True, "request_id": request_id, "mode": "goal",
        "status": "awaiting_approval", "run_id": run_id,
        "candidate_plan_digest": result_admit["digest"],
        "cost_preview": {
            "n_plans": 1,
            "budget_request_tokens": draft.budget_request_tokens,
            "budget_request_llm_invocations": draft.budget_request_llm_invocations,
            "max_concurrency": draft.max_concurrency,
            "node_count": len(draft.nodes)},
        "outcome": result_admit["outcome"],
        "approval_policy": ApprovalPolicy.REQUIRED.value,
    }
    return _record_and_respond(
        d, request_id=request_id, mode="goal",
        as_of=context.data_context.as_of.isoformat(), now=now,
        response=response,
        extra_record={"run_id": run_id,
                      "lanes": [{"lane_index": 0, "run_id": run_id,
                                 "candidate_plan_digest": result_admit["digest"]}]})


def _start_impl(d: Any, body: Mapping[str, Any]) -> JSONResponse:
    modes = [name for name in ("goal", "preset_id", "source_kind")
             if str(body.get(name) or "").strip()]
    if len(modes) != 1:
        return _fail("exactly_one_mode_required", 422, modes=modes)
    if modes[0] == "source_kind":
        return _start_source_kind(d, body)
    if modes[0] == "preset_id":
        return _start_preset(d, body)
    return _start_goal(d, body)


# =========================================================================== #
# read-only projections                                                        #
# =========================================================================== #
def _run_status(stores: Any, run_id: str) -> str:
    """The honest run-status fold over the journal: terminal event value, else
    ``in_progress`` when events exist, else ``not_started`` — never invented."""
    try:
        journal = stores.events.journal(run_id, "main")
    except Exception:  # noqa: BLE001 — an unreadable journal is an honest unknown
        return "unknown"
    status = "not_started"
    for event in journal:
        terminal = _TERMINAL_EVENTS.get(event.event_type)
        status = terminal if terminal is not None else "in_progress"
    return status


def _state_impl(d: Any, request_id: str) -> JSONResponse:
    rid = str(request_id or "").strip()
    if not rid:
        return _fail("missing_request_id", 422)
    refused = _unwired(d, "request_store")
    if refused is not None:
        return refused
    record = d.request_store.get(rid)
    if record is None:
        return _fail("unknown_request", 404)
    state = {k: v for k, v in record.items() if k != "response"}
    runs = []
    if d.stores is not None:
        for lane in record.get("lanes") or []:
            lane_run = str(lane.get("run_id") or "")
            if lane_run:
                runs.append({"run_id": lane_run,
                             "status": _run_status(d.stores, lane_run)})
    return JSONResponse({"ok": True, "state": state, "runs": runs})


def _runs_impl(d: Any, limit: int) -> JSONResponse:
    # NOTE: ``status`` here is the door-time snapshot (e.g. awaiting_approval)
    # and is never updated afterwards — ``GET /state`` is the live view.
    refused = _unwired(d, "request_store")
    if refused is not None:
        return refused
    rows = [
        {"request_id": row.get("request_id"), "mode": row.get("mode"),
         "status": row.get("status"), "as_of": row.get("as_of")}
        for row in d.request_store.recent(limit)
    ]
    return JSONResponse({"ok": True, "runs": rows})


def _latest_recommendation_slate(stores: Any):
    """Best-effort latest committed ``RecommendationSlate@1`` scan (read-only).

    No reviewed listing accessor exists on the payload store (the live_decide
    ``_latest_snapshot_production`` precedent, mirrored) — any doubt returns
    ``None``, which the route serves as the honest empty."""
    try:
        backend = stores._shared.backend  # noqa: SLF001 — no public listing exists
        best = None
        for _object_id, stored in dict(backend.payloads).items():
            if getattr(stored, "schema_key", None) != "RecommendationSlate@1":
                continue
            model = stored.model
            stamp = getattr(model, "as_of", None)
            if best is None or (stamp is not None and stamp > best[0]):
                best = (stamp, model)
        return None if best is None else best[1]
    except Exception as exc:  # noqa: BLE001 — any doubt is an honest empty
        _LOG.warning("recommendation-slate scan failed: %s", exc)
        return None


def _screening_latest_impl(d: Any) -> JSONResponse:
    refused = _unwired(d, "stores")
    if refused is not None:
        return refused
    slate = _latest_recommendation_slate(d.stores)
    if slate is None:
        return JSONResponse({"ok": True, "slate": None})

    # -- the SERVER-SIDE join: degraded lanes are index-only on the contract, so
    #    the batch record (the router's own durable receipt) names the codes.
    code_by_lane: dict[int, str] = {}
    if d.request_store is not None:
        record = d.request_store.by_batch(slate.batch_id)
        if record is not None:
            for lane in record.get("lanes") or []:
                try:
                    code_by_lane[int(lane["lane_index"])] = str(lane["code"])
                except (KeyError, TypeError, ValueError):
                    continue
    degraded = [
        {"lane_index": int(idx), "code": code_by_lane.get(int(idx))}
        for idx in slate.degraded_lanes
    ]

    # -- the candidate slate's own badges (stale_ranking:* / unmappable_codes:<n>
    #    — the examined-rows-only count), verbatim as info; an unresolvable ref
    #    is badged, never silently dropped (the adapters-router precedent).
    badges = list(slate.badges)
    try:
        candidate = d.stores.payloads.get(
            slate.candidate_slate_ref.payload_ref,
            expected_schema_ref=slate.candidate_slate_ref.schema_ref)
        for badge in candidate.badges:
            if badge not in badges:
                badges.append(badge)
    except Exception:  # noqa: BLE001 — honest info badge, never a fake resolve
        badges.append("candidate_slate:unresolvable")

    projection = {
        "as_of": slate.as_of.isoformat(),
        "batch_id": slate.batch_id,
        # day-scoped by the CANDIDATE slate's as_of (Task-4 pinned semantics —
        # the data's day; rendered as info, not alarm).
        "archive_id": recommendation_archive_id(slate),
        "advisory_banner": RECOMMENDATION_ADVISORY_BANNER,
        "entries": [
            {"code": entry.code, "lane_index": entry.lane_index,
             "rating": entry.rating,
             "research_plan_digest":
                 entry.research_plan_ref.payload_ref.content_digest}
            for entry in slate.entries
        ],
        "degraded": degraded,
        "badges": badges,
    }
    return JSONResponse({"ok": True, "slate": projection})


# =========================================================================== #
# ta_ingest — the D7 inbox                                                     #
# =========================================================================== #
def _ta_index_rows(index_path: Path) -> list[dict[str, Any]]:
    if not index_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 — a bad line is skipped, never repaired
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ta_ingest_impl(d: Any, body: Mapping[str, Any]) -> JSONResponse:
    refused = _unwired(d, "ta_inbox_dir", "clock")
    if refused is not None:
        return refused
    author = str(body.get("author") or "").strip()
    if not author:
        # D7: attribution is mandatory — refused, never anonymized.
        return _fail("missing_author", 422)
    raw_text = body.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    if not text.strip():
        return _fail("missing_text", 422)
    title = str(body.get("title") or "").strip() or None
    now = _aware_now(d.clock)
    if now is None:
        return _fail("naive_clock", 503)

    submission = TaSubmission(
        author=author, title=title, submitted_at=now,
        text_digest=content_digest(text))
    semantic = submission.semantic_digest()
    projection = json.loads(submission.model_dump_json())

    inbox = Path(d.ta_inbox_dir)
    index_path = inbox / "index.jsonl"
    # idempotent by content: the semantic digest (author+title+text) is the
    # identity — a resubmission answers the EXISTING receipt, writes nothing.
    for row in _ta_index_rows(index_path):
        if row.get("semantic_digest") == semantic:
            return JSONResponse({
                "ok": True, "deduplicated": True, "file": row.get("file"),
                "submission": projection})

    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    file_name = f"{stamp}_{semantic[:8]}.json"
    inbox.mkdir(parents=True, exist_ok=True)
    # FSI: the text is untrusted DATA — stored verbatim beside its receipt,
    # never executed, never templated into any prompt, never echoed back.
    (inbox / file_name).write_text(
        json.dumps({"submission": projection, "text": text},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    with index_path.open("a", encoding="utf-8") as fh:  # append-only, one line
        fh.write(json.dumps({
            "semantic_digest": semantic, "file": file_name, "author": author,
            "title": title, "submitted_at": now.isoformat(),
            "text_digest": submission.text_digest, "status": submission.status,
        }, ensure_ascii=False) + "\n")
    return JSONResponse({
        "ok": True, "deduplicated": False, "file": file_name,
        "submission": projection})


# =========================================================================== #
# the router                                                                   #
# =========================================================================== #
def build_pipeline_router(deps: PipelineRouterDeps | None = None) -> APIRouter:
    """The five additive ``/orchestration/pipeline/*`` routes (approval doors +
    read-only projections + the D7 inbox; never an execution — see module doc)."""
    d = _DepsView(deps)
    router = APIRouter(prefix=PIPELINE_ROUTER_PREFIX, tags=["orchestration-pipeline"])

    @router.post("/start")
    async def pipeline_start(payload: dict = Body(default={})):
        """Open ONE REQUIRED-approval candidate (goal | preset_id | source_kind)."""
        return await asyncio.to_thread(_start_impl, d, dict(payload or {}))

    @router.get("/state")
    async def pipeline_state(request_id: str = ""):
        """The admission/run/terminal projection of one pipeline request."""
        return await asyncio.to_thread(_state_impl, d, request_id)

    @router.get("/runs")
    async def pipeline_runs(limit: int = 20):
        """Recent pipeline requests (id, mode, status, as_of) — durable records."""
        return await asyncio.to_thread(_runs_impl, d, limit)

    @router.get("/screening/latest")
    async def pipeline_screening_latest():
        """The latest committed RecommendationSlate projection (honest empty)."""
        return await asyncio.to_thread(_screening_latest_impl, d)

    @router.post("/ta_ingest")
    async def pipeline_ta_ingest(payload: dict = Body(default={})):
        """The D7 external-TA inbox (author-mandatory, append-only, idempotent)."""
        return await asyncio.to_thread(_ta_ingest_impl, d, dict(payload or {}))

    return router


# =========================================================================== #
# production wiring (invariant 4 — durable stores; never a self-bind)           #
# =========================================================================== #
def build_production_pipeline_deps() -> PipelineRouterDeps:
    """Assemble the production :class:`PipelineRouterDeps` over the R23/R24-bound
    process durable stores (:func:`~guanlan_v2.orchestration.adapters.durable.
    build_durable_runtime_stores` product).

    READ-ONLY on the health posture (the Task-7 review-I-3 rule, reused
    verbatim): when the process binding is absent or unhealthy this raises the
    live_decide :class:`~guanlan_v2.orchestration.pipeline.live_decide.
    ProductionStoresUnbound` — the router never self-binds (a rebind would
    freeze a one-namespace allowlist and violate the server's 全进程唯一绑定
    invariant).

    FULLY WIRED (Task 11): ``build_production_catalog_runtime`` now resolves
    the full Phase-9 catalog through the promoted nine-loader material universe
    (Task-0b concern 2 closed), so the admission half (``admission_factory`` /
    ``coordinator_factory`` over the full three-analyzer
    ``production_bridge_view``) and the goal-mode ``planner_runner``
    (``build_production_planner_runner``) bind for real. HONEST RESIDUE
    (recorded): the sealed Phase-3 data-prefetch grants still cover ``dec.pm``
    only, so a draft scheduling ``pv.technical`` / ``text.news`` prepares but
    support-refuses with ``tool_calls_required_unmet`` (the Task-11
    permanent-honest grant-gap ruling) — a 422 naming the issue codes, never a
    fake admit.
    """
    from guanlan_v2 import orch_store_status as _orch_status
    from guanlan_v2.orchestration.adapters import chain as _chain
    from guanlan_v2.orchestration.adapters import durable as _durable
    from guanlan_v2.orchestration.runtime_clock import SystemClock
    from guanlan_v2.orchestration.pipeline.assembly import (
        PRODUCTION_PRESETS_DIR,
        load_phase10_preset_registry,
    )
    from guanlan_v2.orchestration.pipeline.candidates import (
        build_production_ranking_reader,
    )
    from guanlan_v2.orchestration.pipeline.live_decide import (
        ProductionStoresUnbound,
        _latest_snapshot_production,
    )

    stores = _durable.process_durable_stores()
    if stores is None or not _orch_status.orchestration_store_bound():
        raise ProductionStoresUnbound(
            "the process orchestration durable-store binding is "
            f"{_orch_status.orchestration_store_state()!r} (only 'bound' is "
            "healthy); the pipeline router never self-binds — fix the startup "
            "binding (guanlan_v2.orchestration.startup / GET "
            "/orchestration/store_status) and restart. Pipeline routes stay "
            "honest 503s; nothing else is affected.")

    registry = _chain.build_phase9_registry(_chain.PHASE9_BASE_REGISTRY_DIGEST)
    stores.resolver.register(registry)
    root = Path(getattr(stores, "root", Path("var") / "orchestration"))
    clock = SystemClock()
    catalog = _chain.phase9_catalog_snapshot()
    presets = load_phase10_preset_registry(PRODUCTION_PRESETS_DIR)

    # -- Task 11: the verified production catalog runtime + full bridge view --- #
    from guanlan_v2.orchestration.adapters.api import (
        build_plan_approval_coordinator,
    )
    from guanlan_v2.orchestration.admission import PlanAdmissionService
    from guanlan_v2.orchestration.runtime_support import STATIC_RUNTIME_PROFILE_V2
    from guanlan_v2.orchestration.pipeline.assembly import (
        build_production_catalog_runtime,
        build_production_planner_runner,
        production_bridge_view,
    )
    from guanlan_v2.orchestration.pipeline.live_decide import _ApprovalsBridge

    bundle = build_production_catalog_runtime(catalog)
    view = production_bridge_view(bundle.runtime)

    def admission_factory(*, run_id, requests, drafts, context, approvals,
                          run_budget):
        return PlanAdmissionService(
            run_id=run_id, requests=dict(requests), drafts=dict(drafts),
            contexts={context.content_digest: context}, attestations={},
            approvals=approvals, catalog=bundle.runtime, bridge_view=view,
            phase1_registry=registry,
            runtime_registry_digest=registry.registry_digest,
            profile=STATIC_RUNTIME_PROFILE_V2, stores=stores,
            run_budget=run_budget, clock=clock)

    def coordinator_factory(*, admission, approvals_sink):
        # the reviewed durable builder (replay path) over the ONE production
        # lease journal; verifier-free — the start routes only REGISTER cards
        # (register_pending / register_and_try_lease need no verifier; human
        # decisions are recorded by the approval console with the production
        # verifier). The reviewed live_decide idiom, verbatim.
        return build_plan_approval_coordinator(
            admission=admission, clock=clock, verifier=None,
            approvals=_ApprovalsBridge(approvals_sink),
            preset_registry=presets,
            catalog_digest=catalog.catalog_digest,
            registry_digest=registry.registry_digest)

    return PipelineRouterDeps(
        stores=stores,
        preset_registry=presets,
        catalog=catalog,
        schema_registry=registry,
        clock=clock,
        ranking_reader=build_production_ranking_reader(),
        subject_committer=build_stores_subject_committer(stores),
        latest_snapshot_fn=lambda: _latest_snapshot_production(stores),
        admission_factory=admission_factory,
        coordinator_factory=coordinator_factory,
        planner_runner=build_production_planner_runner(
            stores=stores, catalog=bundle, schema_registry=registry,
            preset_registry=presets, clock=clock),
        request_store=JsonlPipelineRequestStore(root / "pipeline_requests.jsonl"),
        ta_inbox_dir=Path("var") / "ta_inbox",
    )


def bind_production_pipeline_deps() -> dict[str, Any]:
    """Bind production deps to the process router seam — GUARDED, never raises.

    The server mount calls this beside ``include_router``; a refusal (unbound
    stores, a chain failure) is recorded and printed loudly while every route
    keeps its honest ``*_unwired`` 503 — the additive-mount contract.
    """
    try:
        set_pipeline_router_deps(build_production_pipeline_deps())
        return {"state": "bound"}
    except Exception as exc:  # noqa: BLE001 — binding is additive, never fatal
        print(f"[guanlan_v2][PIPELINE-DEPS-UNAVAILABLE] pipeline router deps "
              f"binding skipped ({type(exc).__name__}: {exc}); "
              f"/orchestration/pipeline/* keeps its honest *_unwired 503s",
              file=sys.stderr)
        return {"state": "unavailable", "error_type": type(exc).__name__,
                "error": str(exc)}
