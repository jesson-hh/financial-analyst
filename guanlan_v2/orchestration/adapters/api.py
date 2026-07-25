# -*- coding: utf-8 -*-
"""Phase 9 · Task 10 — the adapters thin router + seats-compatible persistence.

This module is the HTTP 承载面 of the Phase-9 adapters and nothing more: six additive
``/orchestration/*`` JSON routes (shadow / draft only), the seats-shaped append that
lets the EXISTING 落子 RunPicker / 复盘 UI list and replay an orchestrated run with
**zero frontend changes**, and the production bindings the earlier tasks left behind
ports.

Red lines this module owns (each proven in ``tests/orchestration/test_adapters_api.py``):

* **no endpoint executes an unapproved plan** and **no endpoint writes orders/signals** —
  ``/replay/start`` and ``/weiwo/start`` mint a REQUIRED-approval request and stop; the
  approval itself flows through the Phase-7 console surface;
* ``start`` never self-approves (``AUTO`` is not reachable through the door) and leaves
  ZERO budget reservations and ZERO runs;
* the two GET routes are strictly read-only (zero writes, spy-proven);
* a pre-maturity curve request answers with an explicit ``status`` / ``resume_after`` and
  ``report: null`` — **never a fabricated curve**;
* the seats appends are append-only and idempotent by ``run_id`` presence, they use the
  seats module's IN-PROCESS helpers (never an HTTP self-call from a coroutine — the house
  red line that once blocked the event loop and got 9999 killed), and they never mutate a
  legacy record shape (only the grounded optional keys, written only when non-empty).

Naming corrections consumed here (source wins over the brief; see
``.superpowers/sdd/p9-task-10-report.md``): the Task-6 maturity surface is TOKEN-FREE
(``mature_shadow_replay`` / ``ReplayStateStore`` / ``derive_replay_maturity_key`` /
``ReplayMaturityKeyUnknown``), and the Task-5 handoff is
``hand_off_dual_curves_to_feedback``.

The module defines **no** ``ContractModel`` subclass (only frozen dataclasses, functions
and typing ``Protocol``s), so the Phase-1 completeness walk and the Phase-9
classification firewall stay inert over it — exactly the ``adapters.weiwo`` precedent.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Protocol
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from guanlan_v2.orchestration.adapters.contracts import (
    DualCurveReport,
    ReplayDecisionPoint,
    ShadowReplayRunState,
)
from guanlan_v2.orchestration.adapters.luozi import (
    DeterministicBook,
    ReplayMaturityKeyUnknown,
    ReplayPointSnapshot,
    derive_deterministic_targets,
    mature_shadow_replay,
)
from guanlan_v2.orchestration.adapters.replay_data import (
    ArchiveFeedFloor,
    archive_feed_floor_from_read,
    build_replay_data_context,
    build_replay_manifest,
    derive_replay_feasible_window,
    macro_feed_floor_from_snapshots,
)
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import ApprovalPolicy, ExperimentStatus
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import clock_now, ensure_aware_utc
from guanlan_v2.orchestration.shadow import ShadowContractError
from guanlan_v2.orchestration.spec import OrchestrationRequest

__all__ = [
    # -- router ------------------------------------------------------------- #
    "AdaptersRouterDeps",
    "ORCHESTRATION_ROUTER_PREFIX",
    "ORCHESTRATION_ROUTE_PATHS",
    "build_adapters_router",
    "set_adapters_router_deps",
    "process_adapters_router_deps",
    # -- identities (shared with the Task-4 driver) ------------------------- #
    "derive_replay_experiment_id",
    "derive_replay_run_id",
    "derive_replay_start_candidate_digest",
    "derive_replay_plan_candidate_digest",
    # -- seats compatibility ------------------------------------------------ #
    "COMPAT_SOURCE",
    "persist_replay_run_compat",
    # -- C-A: the production per-point plan coordinator --------------------- #
    "ProductionReplayPlanCoordinator",
    "ReplayCoordinatorApprovalRefused",
    "build_replay_feed_floors",
    # -- C-C: the Phase-7 production approval binding (server-side half) ---- #
    "build_plan_approval_coordinator",
    "bind_process_plan_approval_coordinator",
    "process_plan_approval_coordinator",
    "set_plan_approval_admission_provider",
    "plan_approval_console_kwargs",
]

#: the router prefix (one additive mount in ``server.py``, same shape as the sixteen
#: existing ``build_*_router`` blocks).
ORCHESTRATION_ROUTER_PREFIX = "/orchestration"

#: the EXACT six route paths this router adds — a reviewed closed surface (the
#: route-table snapshot test asserts nothing else appears).
ORCHESTRATION_ROUTE_PATHS: tuple[str, ...] = (
    "/orchestration/replay/start",
    "/orchestration/replay/state",
    "/orchestration/replay/curves",
    "/orchestration/replay/wakeup",
    "/orchestration/weiwo/start",
    "/orchestration/weiwo/state",
)

#: the honesty provenance tag every orchestrated row / response carries.
COMPAT_SOURCE = "orchestrated"

#: the A-share session timezone the seats stores key their ``asof`` strings in.
_DEFAULT_SESSION_TZ = "Asia/Shanghai"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# =========================================================================== #
# identities — shared with the Task-4 driver (never re-invented downstream)     #
# =========================================================================== #
def derive_replay_experiment_id(*, request_id: str, schedule_id: str) -> str:
    """``replay.{request_id}.{schedule_id}`` — byte-identical to the Task-4 driver.

    The driver (``run_interval_replay``) mints this id on the returned
    ``ShadowReplayRunState``; ``/replay/start`` must publish the SAME id so
    ``/replay/state`` can find the head the driver later persists.
    """
    return f"replay.{request_id}.{schedule_id}"


def derive_replay_run_id(*, request_id: str) -> str:
    """``replay-run.{request_id}`` — byte-identical to the Task-4 driver."""
    return f"replay-run.{request_id}"


def derive_replay_plan_candidate_digest(
    *, request_id: str, schedule_digest: str, execution_config_digest: str
) -> str:
    """The Task-4 driver's ONE interval plan-reservation candidate digest.

    Byte-identical to the digest ``run_interval_replay`` reserves the interval plan
    under, so the production coordinator can resolve the SAME reservation
    (``BudgetLedger.get_active_plan``) instead of minting a second plan pool.
    """
    return content_digest(
        {
            "domain": "shadow-replay-plan-v1",
            "schedule_digest": schedule_digest,
            "execution_config_digest": execution_config_digest,
            "request_id": request_id,
        }
    )


def derive_replay_start_candidate_digest(
    *,
    request_id: str,
    schedule_digest: str,
    code: str,
    start_date: str,
    end_date: str,
    strategy_id: str | None,
) -> str:
    """The candidate identity ``/replay/start`` publishes for the approval card.

    A DISTINCT, earlier identity from :func:`derive_replay_plan_candidate_digest`: at
    start time no ``ShadowExecutionConfig`` exists yet (it is sealed when the interval
    actually runs), so the start candidate binds only what the door was given. Recorded
    as correction N10-2.
    """
    return content_digest(
        {
            "domain": "adapters-replay-start-v1",
            "request_id": request_id,
            "schedule_digest": schedule_digest,
            "code": code,
            "start_date": start_date,
            "end_date": end_date,
            "strategy_id": strategy_id or "",
        }
    )


def _derive_request_id(prefix: str, payload: Mapping[str, Any]) -> str:
    """A deterministic request id: the same body always names the same request."""
    return f"{prefix}-{content_digest(dict(payload))[:16]}"


# =========================================================================== #
# router                                                                       #
# =========================================================================== #
@dataclasses.dataclass(frozen=True)
class AdaptersRouterDeps:
    """The injected production bindings the six routes read.

    Every field defaults to ``None`` ⇒ the corresponding route answers an honest 503
    (``{ok: false, reason: "<what>_unwired"}``), never a fake empty success. Production
    wiring (Task 12) binds the shared durable stores / ``ReplayStateStore`` /
    ``ReplayRuntimeBindings``; tests inject tmp / in-memory equivalents.
    """

    schedule_registry: Any = None
    replay_state_store: Any = None
    replay_bindings: Any = None
    clock: Any = None
    replay_requests: MutableMapping[str, Any] | None = None
    weiwo_requests: MutableMapping[str, Any] | None = None
    weiwo_receipts: Mapping[str, Any] | None = None


#: the process-level bindings the server mount resolves LAZILY (per request), so Task 12
#: wires production by calling :func:`set_adapters_router_deps` once — never by editing
#: the server mount again. Unset ⇒ every route answers its honest ``*_unwired`` 503.
_PROCESS_ROUTER_DEPS = AdaptersRouterDeps()


def set_adapters_router_deps(deps: AdaptersRouterDeps) -> None:
    """Bind the process-level router dependencies (Task-12 production wiring seam)."""
    global _PROCESS_ROUTER_DEPS
    if not isinstance(deps, AdaptersRouterDeps):
        raise TypeError("set_adapters_router_deps takes an AdaptersRouterDeps")
    _PROCESS_ROUTER_DEPS = deps


def process_adapters_router_deps() -> AdaptersRouterDeps:
    """The process-level router dependencies (an all-``None`` carrier until bound)."""
    return _PROCESS_ROUTER_DEPS


def _fail(reason: str, status: int, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"ok": False, "reason": reason}
    body.update(extra)
    return JSONResponse(body, status_code=status)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _state_badges(state: ShadowReplayRunState) -> list[str]:
    """Honest provenance / degradation badges derived ONLY from the head itself."""
    badges = [f"source:{COMPAT_SOURCE}", f"status:{state.status.value}"]
    if state.status is ExperimentStatus.WAITING_FOR_MATURITY:
        badges.append("waiting_for_maturity")
    if state.curve_report_ref is None:
        badges.append("curve_report:pending")
    if state.completed_points < state.total_points:
        badges.append("points:partial")
    return badges


def _state_projection(state: ShadowReplayRunState) -> dict[str, Any]:
    return {
        "experiment_id": state.experiment_id,
        "run_id": state.run_id,
        "request_id": state.request_id,
        "status": state.status.value,
        "completed_points": state.completed_points,
        "total_points": state.total_points,
        "schedule_digest": state.schedule_digest,
        "execution_config_digest": state.execution_config_digest,
        "last_scheduled_for": _iso(state.last_scheduled_for),
        "resume_after": _iso(state.resume_after),
        "wakeup_key": state.wakeup_key,
        "has_curve_report": state.curve_report_ref is not None,
        "updated_at": _iso(state.updated_at),
        "source": COMPAT_SOURCE,
    }


def _series_projection(series: Any) -> dict[str, Any]:
    return {
        "curve_kind": series.curve_kind,
        "execution_config_digest": series.execution_config_digest,
        "points": [{"at": _iso(p.at), "nav": p.nav} for p in series.points],
        "trade_count": series.trade_count,
        "applied_intent_digests": list(series.applied_intent_digests),
        "rule_id": series.rule_id,
        "badges": list(series.badges),
    }


def _report_projection(report: DualCurveReport) -> dict[str, Any]:
    cfg = report.execution_config
    return {
        "execution_config_digest": cfg.semantic_digest(),
        "execution_config": {
            "universe": [s.code for s in cfg.universe],
            "init_cash": cfg.init_cash,
            "calendar_id": cfg.calendar_id,
            "cost_model_digest": cfg.cost_model_digest,
            "matching_engine_version": cfg.matching_engine_version,
            "schedule_digest": cfg.schedule_digest,
            "intrabar_exit_priority": cfg.intrabar_exit_priority,
            "data_snapshot_content_digest": cfg.data_snapshot_content_digest,
            "vintage_manifest_digest": cfg.vintage_manifest_digest,
        },
        "deterministic": _series_projection(report.deterministic),
        "llm_shadow": _series_projection(report.llm_shadow),
        "interval_start": _iso(report.interval_start),
        "interval_end": _iso(report.interval_end),
        "decision_point_count": report.decision_point_count,
        "delta_total_return": report.delta_total_return,
        "not_causal_attribution": report.not_causal_attribution,
        "badges": list(report.badges),
        "source": COMPAT_SOURCE,
    }


def _receipt_projection(receipt: Any) -> dict[str, Any]:
    return {
        "wakeup_key": receipt.wakeup_key,
        "experiment_id": receipt.experiment_id,
        "outcome": receipt.outcome,
        "matured_points": receipt.matured_points,
        "state_digest_after": receipt.state_digest_after,
        "processed_at": _iso(receipt.processed_at),
        "source": COMPAT_SOURCE,
    }


def _run_weiwo_research(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - seam
    """Indirection seam over ``adapters.weiwo.run_weiwo_research``.

    The router NEVER calls it (``/weiwo/start`` is an approval door, not an executor);
    the seam exists so a test can spy that no route reaches the research driver.
    """
    from guanlan_v2.orchestration.adapters.weiwo import run_weiwo_research

    return run_weiwo_research(*args, **kwargs)


class _DepsView:
    """Resolves the router's bindings — explicit at build time, else per-request.

    An explicit ``deps`` (tests, an injected production build) is frozen at build time;
    ``None`` (the server mount) resolves :func:`process_adapters_router_deps` on EVERY
    request, so Task 12's one ``set_adapters_router_deps`` call takes effect without
    re-mounting or editing the server.
    """

    __slots__ = ("_fixed",)

    def __init__(self, deps: AdaptersRouterDeps | None) -> None:
        self._fixed = deps

    def __getattr__(self, name: str) -> Any:
        source = self._fixed if self._fixed is not None else _PROCESS_ROUTER_DEPS
        return getattr(source, name)


def build_adapters_router(deps: AdaptersRouterDeps | None = None) -> APIRouter:
    """The six additive ``/orchestration/*`` routes (shadow / draft only)."""
    d = _DepsView(deps)
    router = APIRouter(prefix=ORCHESTRATION_ROUTER_PREFIX, tags=["orchestration"])

    def _store():
        return d.replay_state_store

    def _now() -> datetime:
        """The authoritative instant. An unbound clock REFUSES — it never falls back.

        This value is the ``now=`` a maturity gate decides on; a wall-clock fallback would
        silently gate a holdout window on machine local time (the Phase-8 console 墙钟
        production bug). The caller must 503 ``clock_unwired`` before reaching here.
        """
        if d.clock is None:
            raise ShadowContractError("no authoritative clock is bound")
        return clock_now(d.clock)

    # ── POST /orchestration/replay/start ─────────────────────────────────── #
    @router.post("/replay/start")
    async def replay_start(payload: dict = Body(default={})):
        """Open a shadow-replay request. Never admits, never approves, never runs."""
        body = dict(payload or {})
        code = str(body.get("code") or "").strip()
        if not code:
            return _fail("missing_code", 422)
        schedule_id = str(body.get("schedule_id") or "").strip()
        schedule_version = str(body.get("schedule_version") or "").strip()
        if not schedule_id or not schedule_version:
            return _fail("missing_schedule", 422)
        start_date = str(body.get("start_date") or "").strip()
        end_date = str(body.get("end_date") or "").strip()
        if not _ISO_DATE.match(start_date) or not _ISO_DATE.match(end_date):
            return _fail("bad_date", 422)
        if start_date > end_date:
            return _fail("bad_interval", 422)
        strategy_id = str(body.get("strategy_id") or "").strip() or None

        registry = d.schedule_registry
        if registry is None:
            return _fail("schedule_registry_unwired", 503)
        # refuse BEFORE minting an identity nothing will record: a 200 whose request_id
        # is then unknown to every read endpoint is exactly the fake empty success this
        # module forbids.
        if d.replay_requests is None:
            return _fail("replay_request_store_unwired", 503)
        ref = _resolve_schedule_ref(registry, schedule_id, schedule_version)
        if ref is None:
            return _fail("unknown_schedule", 422,
                         schedule_id=schedule_id, schedule_version=schedule_version)
        schedule = registry.resolve(ref)

        request_id = _derive_request_id("replay", {
            "code": code, "schedule_id": schedule_id,
            "schedule_version": schedule_version, "start_date": start_date,
            "end_date": end_date, "strategy_id": strategy_id or "",
        })
        # The route mints its OWN request: REQUIRED is not negotiable and no body field
        # can widen it (an ``approval_policy`` in the body is ignored outright).
        request = OrchestrationRequest(
            request_id=request_id,
            goal=f"shadow replay {code} {start_date}~{end_date}",
            workflow="orchestrate_only",
            approval_policy=ApprovalPolicy.REQUIRED,
            decision_schedule_ref=ref,
        )
        experiment_id = derive_replay_experiment_id(
            request_id=request_id, schedule_id=schedule.id)
        candidate_plan_digest = derive_replay_start_candidate_digest(
            request_id=request_id, schedule_digest=schedule.content_digest, code=code,
            start_date=start_date, end_date=end_date, strategy_id=strategy_id)

        # the store is bound (refused above), so the record is NEVER skipped: a 200 always
        # means /replay/state and the approval surface can find this request.
        d.replay_requests[request_id] = {
            "request": request,
            "experiment_id": experiment_id,
            "run_id": derive_replay_run_id(request_id=request_id),
            "candidate_plan_digest": candidate_plan_digest,
            "code": code,
            "strategy_id": strategy_id,
            "start_date": start_date,
            "end_date": end_date,
            "schedule_ref": ref,
            "status": "awaiting_approval",
        }
        return JSONResponse({
            "ok": True,
            "request_id": request_id,
            "experiment_id": experiment_id,
            "run_id": derive_replay_run_id(request_id=request_id),
            "status": "awaiting_approval",
            "approval_policy": ApprovalPolicy.REQUIRED.value,
            "candidate_plan_digest": candidate_plan_digest,
            "source": COMPAT_SOURCE,
        })

    # ── GET /orchestration/replay/state ──────────────────────────────────── #
    @router.get("/replay/state")
    async def replay_state(experiment_id: str = ""):
        """The semantic projection of the durable ``ShadowReplayRunState`` head."""
        eid = str(experiment_id or "").strip()
        if not eid:
            return _fail("missing_experiment_id", 422)
        store = _store()
        if store is None:
            return _fail("replay_store_unwired", 503)
        state = await asyncio.to_thread(store.load_head, eid)
        if state is None:
            return _fail("unknown_experiment", 404)
        return JSONResponse({
            "ok": True,
            "state": _state_projection(state),
            "badges": _state_badges(state),
        })

    # ── GET /orchestration/replay/curves ─────────────────────────────────── #
    @router.get("/replay/curves")
    async def replay_curves(experiment_id: str = ""):
        """The dual-curve report when COMPLETED; an explicit honest gap before that."""
        eid = str(experiment_id or "").strip()
        if not eid:
            return _fail("missing_experiment_id", 422)
        store = _store()
        if store is None:
            return _fail("replay_store_unwired", 503)
        state = await asyncio.to_thread(store.load_head, eid)
        if state is None:
            return _fail("unknown_experiment", 404)
        if state.status is not ExperimentStatus.COMPLETED or state.curve_report_ref is None:
            # honest degradation: an explicit status + resume_after, NEVER a curve.
            return JSONResponse({
                "ok": True,
                "experiment_id": eid,
                "report": None,
                "status": state.status.value,
                "resume_after": _iso(state.resume_after),
                "badges": _state_badges(state),
            })
        report = await asyncio.to_thread(store.resolve_report, state.curve_report_ref)
        return JSONResponse({
            "ok": True,
            "experiment_id": eid,
            "status": state.status.value,
            "report": _report_projection(report),
            "badges": _state_badges(state),
        })

    # ── POST /orchestration/replay/wakeup ────────────────────────────────── #
    @router.post("/replay/wakeup")
    async def replay_wakeup(payload: dict = Body(default={})):
        """Consume matured maturity for one parked head — idempotent by construction."""
        key = str((payload or {}).get("wakeup_key") or "").strip()
        if not key:
            return _fail("missing_wakeup_key", 422)
        bindings = d.replay_bindings
        if bindings is None or getattr(bindings, "replay_state_store", None) is None:
            return _fail("replay_bindings_unwired", 503)
        # the maturity gate decides on this instant — an unbound clock REFUSES rather than
        # silently gating a holdout window on machine local time.
        if d.clock is None:
            return _fail("clock_unwired", 503)
        now = _now()
        try:
            receipt = await asyncio.to_thread(
                mature_shadow_replay, key, bindings=bindings, now=now)
        except ReplayMaturityKeyUnknown:
            return _fail("unknown_wakeup_key", 404)
        except ShadowContractError as exc:
            return _fail(f"{type(exc).__name__}: {exc}", 503)
        return JSONResponse({"ok": True, "receipt": _receipt_projection(receipt)})

    # ── POST /orchestration/weiwo/start ──────────────────────────────────── #
    @router.post("/weiwo/start")
    async def weiwo_start(payload: dict = Body(default={})):
        """Open a 帷幄 ONLINE research request. Draft-only, and it never executes here."""
        body = dict(payload or {})
        goal = str(body.get("goal") or "").strip()
        if not goal:
            return _fail("missing_goal", 422)
        # refuse BEFORE minting a run_id nothing will record: a 200 whose run_id then
        # 404s forever on /weiwo/state is a fabricated success, not an honest degradation.
        if d.weiwo_requests is None:
            return _fail("weiwo_store_unwired", 503)
        fallback_preset_id = str(body.get("fallback_preset_id") or "").strip() or None
        request_id = _derive_request_id(
            "weiwo", {"goal": goal, "fallback_preset_id": fallback_preset_id or ""})
        run_id = f"weiwo-run.{request_id}"
        request = OrchestrationRequest(
            request_id=request_id, goal=goal, workflow="orchestrate_and_optimize",
            approval_policy=ApprovalPolicy.REQUIRED,
            fallback_preset_id=fallback_preset_id,
        )
        # the store is bound (refused above) — the record is NEVER skipped.
        d.weiwo_requests[run_id] = {
            "request": request, "run_id": run_id, "request_id": request_id,
            "goal": goal, "fallback_preset_id": fallback_preset_id,
            "status": "awaiting_approval",
        }
        return JSONResponse({
            "ok": True, "request_id": request_id, "run_id": run_id,
            "status": "awaiting_approval",
            "approval_policy": ApprovalPolicy.REQUIRED.value,
            "source": COMPAT_SOURCE,
        })

    # ── GET /orchestration/weiwo/state ───────────────────────────────────── #
    @router.get("/weiwo/state")
    async def weiwo_state(run_id: str = ""):
        """The receipt projection of one research run (draft ids only, never a product)."""
        rid = str(run_id or "").strip()
        if not rid:
            return _fail("missing_run_id", 422)
        # with no store bound there is no basis for "unknown" — that would be a fabricated
        # verdict about a run we simply cannot look up.
        if d.weiwo_requests is None and d.weiwo_receipts is None:
            return _fail("weiwo_store_unwired", 503)
        receipts = d.weiwo_receipts or {}
        receipt = receipts.get(rid)
        if receipt is not None:
            stop_reason = str(getattr(receipt, "stop_reason", "") or "")
            status = ("completed" if stop_reason in ("completed", "completed_no_draft")
                      else stop_reason or "unknown")
            return JSONResponse({
                "ok": True, "run_id": rid, "status": status,
                "draft_ids": list(getattr(receipt, "draft_ids", ()) or ()),
                "stop_reason": stop_reason or None,
                "source": COMPAT_SOURCE,
            })
        pending = (d.weiwo_requests or {}).get(rid)
        if pending is not None:
            return JSONResponse({
                "ok": True, "run_id": rid, "status": "awaiting_approval",
                "draft_ids": [], "stop_reason": None, "source": COMPAT_SOURCE,
            })
        return _fail("unknown_run", 404)

    return router


def _resolve_schedule_ref(registry: Any, schedule_id: str, version: str) -> ContentRef | None:
    """Locate ``(id, version)`` in the Phase-6 registry manifest (unknown ⇒ ``None``)."""
    try:
        manifest = registry.manifest()
    except Exception:  # noqa: BLE001 — an unusable registry is an honest unknown
        return None
    for ref in manifest:
        if ref.id == schedule_id and ref.version == version:
            return ref
    return None


# =========================================================================== #
# seats compatibility — UI 只填充不重建                                          #
# =========================================================================== #
def _asof_string(dec: Mapping[str, Any], *, tf: str) -> str:
    """The seats store's ``asof`` convention for one decision point.

    Daily runs key on ``YYYY-MM-DD``; 30-minute runs key on ``YYYY-MM-DD HH:MM`` in the
    SESSION timezone (``ui/seats/luozi-app.jsx`` slices ``asof`` to 10 / 16 chars and
    matches it against the bar dates, which are local session times). A UTC instant is
    therefore converted, never truncated — 14:00 CST is 06:00 UTC and the raw UTC string
    would never match a bar.
    """
    given = str(dec.get("asof") or "").strip()
    if given:
        return given
    raw = dec.get("decision_as_of")
    if not isinstance(raw, datetime):
        raise ValueError(
            "each decision must carry an aware 'decision_as_of' datetime (or a "
            "pre-formatted 'asof' string) — a decision row is never fabricated")
    tz = str(dec.get("timezone") or _DEFAULT_SESSION_TZ)
    local = ensure_aware_utc(raw).astimezone(ZoneInfo(tz))
    intraday = str(tf).strip().lower() not in ("d", "1d", "day", "daily", "")
    return local.strftime("%Y-%m-%d %H:%M") if intraday else local.strftime("%Y-%m-%d")


def _compat_decision_row(
    dec: Mapping[str, Any], *, run_id: str, run_head: Mapping[str, Any]
) -> dict[str, Any]:
    """One legacy-shaped decision row (only grounded keys; optional keys when non-empty)."""
    tf = str(run_head.get("tf") or "D")
    direction = str(dec.get("direction") or "").strip()
    if not direction:
        raise ValueError(
            "a compat decision row needs a non-empty 'direction' (the seats direction "
            "vocabulary 买入/卖出/观望) — never a fabricated row")
    row: dict[str, Any] = {
        "code": str(dec.get("code") or run_head.get("code") or "").strip(),
        "direction": direction,
        "confidence": dec.get("confidence"),
        "rationale": str(dec.get("rationale") or ""),
        "asof": _asof_string(dec, tf=tf),
        "run_id": run_id,
        "source": COMPAT_SOURCE,
    }
    if not row["code"]:
        raise ValueError("a compat decision row needs a non-empty 'code'")
    if not row["rationale"]:
        raise ValueError("a compat decision row needs a non-empty 'rationale'")
    # the grounded optional keys — written ONLY when non-empty (seats/api.py:949).
    optional = {
        "name": dec.get("name") or run_head.get("name"),
        "strategy_id": dec.get("strategy_id") or run_head.get("strategy_id"),
        "strategy_name": dec.get("strategy_name") or run_head.get("strategy_name"),
        "freq": "30min" if str(tf).strip().lower() == "30min" else "day",
        "model_name": dec.get("model_name") or run_head.get("model"),
        "key_evidence": list(dec.get("key_evidence") or ()),
        "reasoning": dec.get("reasoning"),
    }
    for key, value in optional.items():
        if value:
            row[key] = value
    return row


#: the idempotency scan's tail window (bytes / lines). ``var/seats_decisions.jsonl`` grows
#: without bound, so the guard reads a bounded tail like the rest of the house
#: (``read_picks``' 500-line tail is the precedent) instead of the whole file on every
#: persist. Honest limit: a run whose rows have already scrolled out of the window would
#: not be detected — irrelevant for the real caller, which persists an interval once,
#: right after it completes.
_COMPAT_SCAN_BYTES = 4 * 1024 * 1024
_COMPAT_SCAN_LINES = 20_000


def _tail_lines(path: Path, *, max_bytes: int, max_lines: int) -> list[str]:
    """The last ``max_lines`` complete lines of ``path`` within a ``max_bytes`` window."""
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        start = max(0, size - max_bytes)
        fh.seek(start)
        chunk = fh.read()
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]        # the window may have cut the first line in half
    return lines[-max_lines:]


def _jsonl_has_run_id(path: Path, run_id: str) -> bool:
    """``True`` iff ``path``'s tail window carries a row for ``run_id`` (idempotency)."""
    try:
        if not path.exists():
            return False
        for line in _tail_lines(path, max_bytes=_COMPAT_SCAN_BYTES,
                                max_lines=_COMPAT_SCAN_LINES):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001 — a bad line is skipped, exactly like the routes
                continue
            if isinstance(row, dict) and str(row.get("run_id") or "") == run_id:
                return True
    except Exception:  # noqa: BLE001 — an unreadable log is treated as "not present"
        return False
    return False


def persist_replay_run_compat(
    state: ShadowReplayRunState,
    *,
    decisions: tuple[Mapping[str, Any], ...],
    run_head: Mapping[str, Any],
) -> None:
    """Append one orchestrated run into the EXISTING seats append-only stores.

    After an interval completes, one run head lands in ``var/seats_runs.jsonl`` (the
    shape ``POST /seats/runs`` writes: ``run_id`` / ``code`` / ``strategy_id`` / ``tf`` /
    ``start_date`` / ``end_date`` / counts / ``model``) and one row per decision point
    lands in ``var/seats_decisions.jsonl`` through the seats module's own in-process
    helper ``_persist_decision`` (never an HTTP self-call from a coroutine). The rows
    carry ``run_id`` + ``source="orchestrated"`` and an ``asof`` in the store's timestamp
    convention, so the existing RunPicker (`GET /seats/runs`) and decision flow
    (`GET /seats/decisions?run_id=`) list and replay the run with **zero frontend
    changes** — and never a frontend-``idx`` key, which is a display coordinate the UI
    recomputes from ``asof``.

    Append-only and idempotent: the head is appended only when no row for ``run_id``
    exists in the runs log, the decision rows only when none exists in the decisions
    log, so a replay of the same completed state appends nothing. Existing rows are
    never rewritten. A decision missing a direction / rationale / timestamp raises —
    an honest refusal, never a fabricated row.
    """
    from guanlan_v2.seats import api as seats_api

    run_id = str(run_head.get("run_id") or state.run_id or "").strip()
    if not run_id:
        raise ValueError("run_head must carry a non-empty 'run_id'")
    code = str(run_head.get("code") or "").strip()
    if not code:
        raise ValueError("run_head must carry a non-empty 'code'")

    # build EVERY row first: a refusal must leave both logs untouched.
    rows = [_compat_decision_row(dec, run_id=run_id, run_head=run_head)
            for dec in decisions]

    if not _jsonl_has_run_id(seats_api._DEC_LOG, run_id):
        for row in rows:
            seats_api._persist_decision("decide", row)

    if not _jsonl_has_run_id(seats_api._RUNS_LOG, run_id):
        head = {k: v for k, v in dict(run_head).items() if v not in (None, "")}
        head["run_id"] = run_id
        head["code"] = code
        head.setdefault("source", COMPAT_SOURCE)
        head.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        path = seats_api._RUNS_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:      # append-only, never rewritten
            fh.write(json.dumps(head, ensure_ascii=False) + "\n")


# =========================================================================== #
# C-A — the PRODUCTION ReplayPlanCoordinator                                    #
# =========================================================================== #
class ReplayCoordinatorApprovalRefused(ShadowContractError):
    """A per-point plan was asked to run without a REQUIRED, APPROVED human decision.

    Raised BEFORE any plan execution: an ``AUTO`` approval policy is refused at the door
    and a missing / REJECTED decision refuses the lane, so an unapproved plan can never
    execute (invariant 7b).
    """


class _ReplayApprovalSource(Protocol):
    """The Phase-7 durable decision surface (``PlanApprovalCoordinator.load_decision``)."""

    def load_decision(self, request_id: str, candidate_plan_digest: str) -> Any: ...


class _ReplayMemoryService(Protocol):
    """The Phase-3 memory facade (``MemoryContextPreparationService``), typed ref form."""

    def prepare_pit_replay(
        self, data_context: Any, authority: Any, *, prior_context_ref: Any
    ) -> Any: ...


def build_replay_feed_floors(
    *,
    read_archive: Callable[..., Mapping[str, Any]] | None = None,
    archive_root: str | None = None,
    macro_snapshots_path: str | Path | None = None,
    kinds: Iterable[str] | None = None,
) -> tuple[ArchiveFeedFloor, ...]:
    """The ONE ``feed_floors`` tuple, built from the TWO distinct floor builders.

    The three ``snapshot_archive.KINDS`` come from ``read_archive`` →
    :func:`archive_feed_floor_from_read`; the ``macro`` feed comes from
    :func:`macro_feed_floor_from_snapshots` (the macro-pulse append-only jsonl) and is
    **NEVER** read through ``read_archive`` — that surface serves only the three KINDS
    and would answer an empty archive for ``macro``, fabricating a missing floor.
    """
    from guanlan_v2.datafeed import snapshot_archive as sa

    reader = read_archive if read_archive is not None else sa.read_archive
    kind_names = tuple(kinds) if kinds is not None else tuple(sa.KINDS)
    # resolve through the module's OWN resolver so the ``GUANLAN_SNAPSHOT_ARCHIVE_DIR``
    # override (snapshot_archive.py:52-55 — the override a 9998 verification run uses) is
    # honoured. Reading `_ARCHIVE_DIR_DEFAULT` directly would record, in the floors and
    # therefore in the sealed PIT manifest attestation, a root that was never read.
    root = archive_root if archive_root is not None else str(sa._archive_dir(None))
    floors = [
        archive_feed_floor_from_read(kind, reader(kind), archive_root=root)
        for kind in kind_names
    ]
    floors.append(macro_feed_floor_from_snapshots(snapshots_path=macro_snapshots_path))
    return tuple(floors)


class ProductionReplayPlanCoordinator:
    """The REAL per-point plan coordinator the Task-4 driver's port describes.

    Task 4 shipped ``run_interval_replay`` behind the ``ReplayPlanCoordinator`` port with
    a recorded fake; this is the production adapter. Per decision point it composes the
    reviewed upstream surfaces:

    ① :func:`build_replay_manifest` seals the PIT snapshot manifest at the point's
       ``decision_as_of`` from the ONE ``feed_floors`` tuple, and
       :func:`build_replay_data_context` (Task 2) turns it into the frozen PIT_REPLAY
       ``DataContext`` whose ``as_of`` the driver then binds structurally;
    ② :func:`derive_replay_feasible_window` (Task 2b) derives the honest degradation
       badges from the **SAME** ``feed_floors`` tuple — one seal, one feasibility fact;
    ③ the per-point Bootstrap node is reserved as a CHILD of the ONE interval plan
       reservation the driver minted (resolved via
       :func:`derive_replay_plan_candidate_digest` +
       ``BudgetLedger.get_active_plan`` — a second plan pool is never minted), reserving
       ZERO LLM invocations;
    ④ **every** admitted plan passes the REQUIRED approval gate (an ``AUTO`` policy is
       refused at the door; a missing / REJECTED decision refuses the lane) before the
       memory facade's typed ``prepare_pit_replay`` projection and the plan runner run;
    ⑤ the deterministic lane is the pure Task-5 rule (``derive_deterministic_targets``),
       zero-LLM and envelope-free.

    ``plan_runner`` is the injected sync execution seam whose production binding wraps
    the async ``dag.run_plan`` (the async→sync bridge and the full admission
    prepare/persist/freeze chain are Task-12 assembly — see the task report).
    """

    def __init__(
        self,
        *,
        request: Any,
        schedule: Any,
        execution_config: Any,
        budget: Any,
        source_config: Any,
        source_registry: Any,
        routing: Any,
        store_meta: Mapping[str, Any],
        data_snapshot_id: str,
        calendar_id: str,
        timezone: str,
        routing_snapshot_digest: str,
        schema_registry_digest: str,
        feed_floors: tuple[ArchiveFeedFloor, ...],
        memory_service: _ReplayMemoryService,
        memory_authority: Any,
        prior_context_ref: Any,
        approval: _ReplayApprovalSource,
        plan_runner: Callable[..., Any],
        factor_scores: Callable[[ReplayDecisionPoint], Mapping[str, float]],
        universe: tuple[Any, ...],
        provenance_digest: str,
        bootstrap_candidate_plan_digest: str | None = None,
        main_candidate_plan_digest: str | None = None,
        bootstrap_node_tokens: int = 1000,
    ) -> None:
        self._request = request
        self._schedule = schedule
        self._execution_config = execution_config
        self._budget = budget
        self._source_config = source_config
        self._source_registry = source_registry
        self._routing = routing
        self._store_meta = dict(store_meta)
        self._data_snapshot_id = data_snapshot_id
        self._calendar_id = calendar_id
        self._timezone = timezone
        self._routing_snapshot_digest = routing_snapshot_digest
        self._schema_registry_digest = schema_registry_digest
        # the ONE tuple — passed by identity into BOTH the manifest seal and the
        # feasibility derivation (never rebuilt per call, never two tuples).
        self._feed_floors = feed_floors
        self._memory = memory_service
        self._authority = memory_authority
        self._prior_context_ref = prior_context_ref
        self._approval = approval
        self._plan_runner = plan_runner
        self._factor_scores = factor_scores
        self._universe = tuple(universe)
        self._provenance_digest = provenance_digest
        self._request_id = str(getattr(request, "request_id", "req"))
        self._plan_candidate_digest = derive_replay_plan_candidate_digest(
            request_id=self._request_id,
            schedule_digest=schedule.content_digest,
            execution_config_digest=execution_config.semantic_digest(),
        )
        self._bootstrap_candidate = (
            bootstrap_candidate_plan_digest
            or content_digest({"domain": "shadow-replay-bootstrap-candidate-v1",
                               "plan_candidate_digest": self._plan_candidate_digest})
        )
        self._main_candidate = (
            main_candidate_plan_digest
            or content_digest({"domain": "shadow-replay-main-candidate-v1",
                               "plan_candidate_digest": self._plan_candidate_digest})
        )
        self._bootstrap_node_tokens = int(bootstrap_node_tokens)

    # -- red lines ---------------------------------------------------------- #
    def _require_approval(self, candidate_plan_digest: str, *, lane: str) -> Any:
        """Invariant 7b: every admitted plan goes through REQUIRED approval."""
        if getattr(self._request, "approval_policy", None) is ApprovalPolicy.AUTO:
            raise ReplayCoordinatorApprovalRefused(
                f"AUTO approval is refused for the {lane} lane; a shadow replay plan "
                "may only run under a REQUIRED human decision")
        decision = self._approval.load_decision(
            self._request_id, candidate_plan_digest)
        if decision is None:
            raise ReplayCoordinatorApprovalRefused(
                f"the {lane} lane candidate {candidate_plan_digest} has no terminal "
                "approval decision; an unapproved plan never executes")
        if not decision.authorizes_freeze(
            request_id=self._request_id, candidate_plan_digest=candidate_plan_digest
        ):
            raise ReplayCoordinatorApprovalRefused(
                f"the {lane} lane candidate {candidate_plan_digest} is not APPROVED "
                f"(decision={getattr(decision, 'decision', None)!r})")
        return decision

    def _reserve_bootstrap_child(self, point: ReplayDecisionPoint) -> Any:
        """Reserve the point's Bootstrap node as a CHILD of the ONE plan reservation."""
        plan_reservation = self._budget.get_active_plan(
            self._request_id, self._plan_candidate_digest)
        if plan_reservation is None:
            raise ShadowContractError(
                "no active interval plan reservation for request "
                f"{self._request_id!r}; the coordinator reserves Bootstrap nodes as "
                "children of the driver's ONE plan reservation, never a second plan pool")
        return self._budget.reserve_node(
            plan_reservation_id=plan_reservation.reservation_id,
            node_id=f"bootstrap#{point.point_ordinal}",
            attempt=1,
            tokens=self._bootstrap_node_tokens,
            llm_invocations=0,          # the Bootstrap lane is zero-LLM
            concurrency=1,
            idempotency_key=(
                f"replay-node-bootstrap:{self._request_id}:{point.point_ordinal}"),
        )

    # -- ReplayPlanCoordinator port ----------------------------------------- #
    def bootstrap_context(self, point: ReplayDecisionPoint) -> ReplayPointSnapshot:
        floors = self._feed_floors      # the ONE tuple, by identity
        manifest = build_replay_manifest(
            data_snapshot_id=self._data_snapshot_id,
            store_meta=self._store_meta,
            as_of=point.decision_as_of,
            timezone=self._timezone,
            calendar_id=self._calendar_id,
            routing_snapshot_digest=self._routing_snapshot_digest,
            schema_registry_digest=self._schema_registry_digest,
            feed_floors=floors,
        )
        data_context = build_replay_data_context(
            decision_point=point,
            source_config=self._source_config,
            source_registry=self._source_registry,
            routing=self._routing,
            manifest=manifest,
        )
        window = derive_replay_feasible_window(
            as_of=point.decision_as_of, feed_floors=floors)
        badges = tuple(v.badge for v in window.verdicts if v.badge)

        approval = self._require_approval(self._bootstrap_candidate, lane="bootstrap")
        self._reserve_bootstrap_child(point)
        memory_binding = self._memory.prepare_pit_replay(
            data_context, self._authority, prior_context_ref=self._prior_context_ref)
        self._plan_runner(
            lane="bootstrap", point=point, approval=approval,
            data_context=data_context, memory_binding=memory_binding,
            candidate_plan_digest=self._bootstrap_candidate)
        return ReplayPointSnapshot(
            data_context=data_context, degradation_badges=badges)

    def llm_proposal(self, point: ReplayDecisionPoint, snapshot: ReplayPointSnapshot) -> Any:
        approval = self._require_approval(self._main_candidate, lane="main")
        return self._plan_runner(
            lane="llm", point=point, approval=approval,
            data_context=snapshot.data_context, memory_binding=None,
            candidate_plan_digest=self._main_candidate)

    def deterministic_targets(
        self, point: ReplayDecisionPoint, snapshot: ReplayPointSnapshot
    ) -> DeterministicBook:
        target_set = derive_deterministic_targets(
            point,
            factor_scores=dict(self._factor_scores(point)),
            universe=self._universe,
            provenance_digest=self._provenance_digest,
        )
        return DeterministicBook(
            rule_id=target_set.rule_id,
            positions=tuple(target_set.positions),
            cash_weight=target_set.cash_weight,
        )


# =========================================================================== #
# C-C — the Phase-7 production approval binding (server-side half)              #
# =========================================================================== #
#: the durable plan-approval journal (env-overridable for a 9998 verification run).
_PLAN_APPROVAL_JOURNAL = (
    Path(__file__).resolve().parents[3] / "var" / "orchestration" / "plan_approvals.jsonl"
)

#: the injectable admission provider. ``None`` ⇒ HONEST SKIP: no coordinator is bound and
#: the console plan-approval endpoints keep answering their existing 503 (production is
#: byte-unchanged until Task 12 assembles the real admission service).
_ADMISSION_PROVIDER: Callable[[], Any] | None = None

_PROCESS_COORDINATOR: Any = None


def set_plan_approval_admission_provider(provider: Callable[[], Any] | None) -> None:
    """Bind (or clear) the provider that supplies the production admission service.

    The provider returns ``None`` (or raises) ⇒ honest skip. It must return an object
    exposing the reviewed ``PlanAdmissionService`` surface plus the MUTABLE approvals
    mapping the service loads its authority from (``(request_id, candidate_id) ->
    PlanApproval``), either as ``.approvals`` or via the explicit ``approvals=`` argument
    of :func:`build_plan_approval_coordinator`.
    """
    global _ADMISSION_PROVIDER
    _ADMISSION_PROVIDER = provider


def build_plan_approval_coordinator(
    *,
    admission: Any,
    clock: Any,
    verifier: Any,
    approvals: MutableMapping[tuple[str, str], Any] | None = None,
    journal_path: Path | str | None = None,
    console_emit: Callable[[str, dict], None] | None = None,
    preset_registry: Any = None,
    catalog_digest: str | None = None,
    registry_digest: str | None = None,
) -> Any:
    """Build the process ``PlanApprovalCoordinator` through its DURABLE replay path.

    Always :meth:`PlanApprovalCoordinator.replay` — never the bare constructor — so a
    decision row written before its admission call completed (a crash cut) heals to one
    terminal admission effect at startup.

    The ``approvals_sink`` is bridged: the coordinator constructs the ONE authoritative
    ``PlanApproval`` and the sink deposits it into the admission service's own approvals
    store, so the authority ``record_approval`` LOADS equals the durable decision (the
    reviewed Task-8 bridge). Without this bridge a durable approval never reaches the
    orchestration event surface.
    """
    from guanlan_v2.orchestration.approval import PlanApprovalCoordinator

    store = approvals if approvals is not None else getattr(admission, "approvals", None)
    if store is None:
        raise ValueError(
            "build_plan_approval_coordinator needs the admission service's mutable "
            "approvals mapping to bridge the approvals_sink into")

    def _approvals_sink(approval: Any) -> None:
        store[(approval.request_id, approval.candidate_plan_digest)] = approval

    path = Path(journal_path) if journal_path is not None else _PLAN_APPROVAL_JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    return PlanApprovalCoordinator.replay(
        path,
        admission=admission,
        clock=clock,
        verifier=verifier,
        console_emit=console_emit,
        approvals_sink=_approvals_sink,
        preset_registry=preset_registry,
        catalog_digest=catalog_digest,
        registry_digest=registry_digest,
    )


def bind_process_plan_approval_coordinator() -> Any:
    """Bind the once-per-process coordinator at server startup (honest skip if unwired).

    Mirrors Task-1b's ``bind_process_durable_stores_and_scan`` idiom. With no admission
    provider bound (production today — no orchestration run exists at boot) this returns
    ``None`` and production behaviour is byte-unchanged; Task 12 binds the provider and
    the durable replay heals on the next restart.
    """
    global _PROCESS_COORDINATOR
    if _PROCESS_COORDINATOR is not None:
        return _PROCESS_COORDINATOR
    provider = _ADMISSION_PROVIDER
    if provider is None:
        return None
    binding = provider()
    if binding is None:
        return None
    _PROCESS_COORDINATOR = build_plan_approval_coordinator(
        admission=getattr(binding, "admission", binding),
        clock=binding.clock,
        verifier=binding.verifier,
        approvals=getattr(binding, "approvals", None),
        journal_path=getattr(binding, "journal_path", None),
        preset_registry=getattr(binding, "preset_registry", None),
        catalog_digest=getattr(binding, "catalog_digest", None),
        registry_digest=getattr(binding, "registry_digest", None),
    )
    return _PROCESS_COORDINATOR


def process_plan_approval_coordinator() -> Any:
    """The once-per-process coordinator bound by the server lifespan (or ``None``)."""
    return _PROCESS_COORDINATOR


def plan_approval_console_kwargs(*, coordinator: Any = None) -> dict[str, Any]:
    """The console-router kwargs that carry the P7 coordinator (empty when unbound).

    An empty mapping keeps ``build_console_router()`` byte-identical to today's call, so
    an unbound coordinator changes nothing (the console plan-approval endpoints keep
    their honest 503).
    """
    if coordinator is None:
        return {}
    return {"plan_approval_coordinator": coordinator}
