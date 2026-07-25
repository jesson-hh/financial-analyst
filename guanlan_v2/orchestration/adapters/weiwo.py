# -*- coding: utf-8 -*-
"""Phase 9 · Task 7 — the 帷幄 ONLINE research adapter.

This module binds 帷幄 (the console research agent) to the orchestration framework as
the ONLINE research driver — the ONLINE twin of Task 4's PIT_REPLAY interval driver
(:mod:`guanlan_v2.orchestration.adapters.luozi`). It composes the already-reviewed
upstream surfaces and owns exactly the run's red lines; it invents no new machinery:

* the frozen ONLINE :class:`~guanlan_v2.orchestration.context.DataContext` comes from
  Task 3's :func:`~guanlan_v2.orchestration.adapters.live_data.build_online_data_context`
  (``as_of`` read from the run clock **exactly once**, at start, and frozen);
* memory comes from the ONLINE memory facade twin ``prepare_online``
  (:mod:`guanlan_v2.orchestration.memory.store`) → the ``ContextSnapshot`` digest;
* the research MainPlan is opened via the Phase-7 dynamic planner (DYNAMIC with a
  REQUIRED human approval) or, on a planner failure, an explicit
  ``fallback_preset_id`` — which STILL goes through the full REQUIRED-approval path;
* the optimizer loop is the Phase-4 Evaluator-Optimizer (``run_optimize``) with its
  ``evaluate_validation`` bound to :func:`evaluate_validation_via_run_graph` — a thin,
  transparent wrapper over ``workflow.executor.run_graph`` (clause C2 of the handoff);
* every product lands DRAFT-ONLY through the factorlib save path, exactly like the
  research loop's ``_save_draft`` (``status="draft"``; promotion stays the existing
  human ``/factorlib/promote`` gate). Failing candidates land **nothing** in factorlib
  — their rejection lives only in run events / the ``TrialLedger`` (append-only), and
  no "rejected" status is ever invented (factorlib has no such status).

Five red lines (the brief's invariants 1–5) are enforced HERE, not by any port:

1.  **start-frozen ``as_of``** everywhere — the memory ``ContextSnapshot``, every
    ``DataRequest``, every provenance record. An advancing clock during the run changes
    nothing semantic; the run clock is read once (by ``build_online_data_context``) and
    every downstream instant is ``ctx.as_of``, never a second wall-clock read.
2.  **draft-only products** — :func:`save_draft_to_factorlib` rejects any non-draft
    status with :class:`WeiwoDraftStatusError` BEFORE factorlib, and factorlib's own
    ``_VALID_SAVE_STATUS`` regression is the second gate.
3.  **run_graph is untouched** — :func:`evaluate_validation_via_run_graph` never
    monkeypatches ``run_graph`` / edits ``_DISPATCH``; it is口径-identical to a direct
    call; and it **refuses synchronous invocation from a running event loop**. This is
    the console 仓级红线 (协程内严禁同步堵事件循环 — a synchronous ``run_graph`` on the
    9999 event-loop thread has killed the server before); the intended dispatch is
    ``asyncio.to_thread`` from async contexts (the worker thread has no running loop).
4.  **approval discipline** — an unapproved DYNAMIC plan NEVER executes (zero
    reservations); a planner failure with ``fallback_preset_id=None`` is an honest
    terminal failure with NO silent preset fallback; an explicit fallback still goes
    through the FULL REQUIRED-approval path.
5.  **prompt-injection firewall** — the adapter never interprets fetched live text as
    an instruction. Fetched text stays in the Phase-2/3 untrusted-DATA channel (it
    becomes a ``RawRowCandidate``); no capability call and no approval event is ever
    derived from it. The weiwo capability binding is a pure function of the reviewed
    Phase-3 surface, independent of any data.

Capability discipline: :func:`resolve_weiwo_capability_binding` grants only the Task-3
ONLINE read-only data method refs (``verified_snapshot`` / ``news`` / ``ohlcv``, all
``read_only=True``) plus ``memory.propose`` (proposal-only; human-reviewed). It grants
**no** order/signal write, **no** memory-accept, **no** code/skill write.

Internal carriers (:class:`WeiwoRuntimeBindings` / :class:`WeiwoRunReceipt` /
:class:`WeiwoCapabilityBinding`) are frozen dataclasses — private, unregistered, never
a schema payload (Task 9 owns any cumulative-registry classification). They are the
ONLINE symmetric twins of Task 4's ``ReplayRuntimeBindings`` / ``ShadowReplayRunState``.
"""
from __future__ import annotations

import asyncio
import dataclasses
from typing import Any, Callable, Mapping, Protocol

from guanlan_v2.orchestration.adapters.contracts import ShadowReplayRunState
from guanlan_v2.orchestration.adapters.live_data import (
    build_online_data_context,
    build_online_live_descriptor,
)
from guanlan_v2.orchestration.context import DataContext
from guanlan_v2.orchestration.data.source import build_data_request
from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import ApprovalDecision
from guanlan_v2.orchestration.refs import ContentRef
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock
from guanlan_v2.orchestration.trial import ValidationMetrics

__all__ = [
    "evaluate_validation_via_run_graph",
    "run_weiwo_research",
    "save_draft_to_factorlib",
    "resolve_weiwo_capability_binding",
    "WeiwoRuntimeBindings",
    "WeiwoRunReceipt",
    "WeiwoCapabilityBinding",
    "WeiwoMemoryPreparer",
    "WeiwoResearchPlanner",
    "WeiwoApprovalGate",
    "WeiwoFallbackMaterializer",
    "WeiwoOptimizer",
    "WeiwoRejectionSink",
    "WeiwoDraftStatusError",
    "WeiwoEventLoopGuardError",
    "WeiwoSnapshotBindingError",
    "WEIWO_PRODUCT_SOURCE",
    "WEIWO_MEMORY_PROPOSE",
]

#: the reviewed ``source`` tag every weiwo draft product carries.
WEIWO_PRODUCT_SOURCE = "orchestration_weiwo"
#: the ONLY memory capability the weiwo binding grants: proposal-only (human-reviewed),
#: never ``memory.accept``.
WEIWO_MEMORY_PROPOSE = "memory.propose"
#: the reviewed factorlib draft status (the single legal product status; promotion is
#: the existing human ``/factorlib/promote`` gate).
_DRAFT_STATUS = "draft"
#: a handler ref used only to derive the read-only ONLINE method refs for the capability
#: binding (never dispatched — the binding is a pure surface projection).
_CAP_HANDLER = ContentRef(id="weiwo.capability.binding", version="1", content_digest="0" * 64)
#: capability-name substrings that would mean an actuation / accept / code write.
_WRITE_CAP_PATTERNS = (
    "order", "signal", "trade", "submit", "buy", "sell", "place", "cancel",
    "execute", "accept", "skill", "code", "write",
)


# --------------------------------------------------------------------------- #
# exceptions                                                                   #
# --------------------------------------------------------------------------- #
class WeiwoDraftStatusError(Exception):
    """A product save was attempted with a status other than ``draft``.

    The adapter's own guard (before factorlib) — factorlib's ``_VALID_SAVE_STATUS`` is
    the second gate. 帷幄 lands products draft-only; promotion is the human gate.
    """


class WeiwoEventLoopGuardError(RuntimeError):
    """:func:`evaluate_validation_via_run_graph` was called synchronously from a running
    event loop.

    ``run_graph`` is a heavy synchronous call; running it on the event-loop thread
    blocks the loop and has crashed the 9999 server before (the console 仓级红线:
    协程内严禁同步堵事件循环). Dispatch it via ``asyncio.to_thread`` from async contexts —
    the worker thread has no running loop, so the guard passes there.
    """


class WeiwoSnapshotBindingError(Exception):
    """A downstream instant (memory ``ContextSnapshot`` / ``DataRequest``) drifted from
    the run's start-frozen ``as_of`` — invariant 1 refuses it loudly (no silent reframe).
    """


# --------------------------------------------------------------------------- #
# evaluate_validation_via_run_graph — the thin, transparent run_graph binding   #
# --------------------------------------------------------------------------- #
def _refuse_if_on_running_event_loop() -> None:
    """Raise the guard error iff this thread currently has a RUNNING event loop.

    A worker thread spawned by ``asyncio.to_thread`` has no running loop, so
    ``get_running_loop()`` raises ``RuntimeError`` and the guard passes; a coroutine on
    the main thread has one, so the guard fires (仓级红线).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop in this thread — safe (the to_thread worker case)
    raise WeiwoEventLoopGuardError(
        "evaluate_validation_via_run_graph refuses synchronous invocation from a "
        "running event loop (a synchronous run_graph would block the loop and can "
        "crash 9999); dispatch it via asyncio.to_thread from async contexts "
        "(仓级红线:协程内严禁同步堵事件循环)"
    )


def evaluate_validation_via_run_graph(
    candidate_graph: Mapping[str, Any],
    *,
    overrides: Mapping[str, Any] | None = None,
    on_node: Callable[[str, str, str], None] | None = None,
) -> Mapping[str, Any]:
    """A thin, transparent wrapper over ``workflow.executor.run_graph``.

    It adds nothing to and removes nothing from ``run_graph`` semantics: it forwards
    ``graph`` / ``overrides`` / ``on_node`` verbatim and always requests
    ``prefer_model_terminal=True`` (the research-loop precedent ``_run_graph_eval`` —
    ML 图过门指标须取模型真实成绩,不吃特征等权回测口径). It never monkeypatches
    ``run_graph`` nor edits ``_DISPATCH``; the module surface is left byte-identical
    (invariant 3). The Phase-4 ``evaluate_validation`` binding (clause C2) adapts this
    callable's mapping result to the reviewed ``ValidationMetrics`` signature.

    It **refuses** synchronous invocation from a running event loop (invariant 3 /
    matrix row 4): the intended dispatch from an async context is
    ``await asyncio.to_thread(evaluate_validation_via_run_graph, graph, ...)``, which
    runs it in a worker thread with no running loop.
    """
    _refuse_if_on_running_event_loop()
    # lazy import keeps this module free of the workflow package at import time and makes
    # the run_graph identity comparison in invariant-3 tests observe the SAME object.
    from guanlan_v2.workflow import executor as wex

    return wex.run_graph(
        dict(candidate_graph),
        overrides=(dict(overrides) if overrides is not None else None),
        on_node=on_node,
        prefer_model_terminal=True,
    )


# --------------------------------------------------------------------------- #
# save_draft_to_factorlib — the draft-only landing (reuses the factorlib path)  #
# --------------------------------------------------------------------------- #
def _candidate_str(candidate: Mapping[str, Any], key: str, default: str = "") -> str:
    v = candidate.get(key)
    return str(v).strip() if isinstance(v, (str, int, float)) else default


def save_draft_to_factorlib(
    candidate: Mapping[str, Any],
    *,
    source: str = WEIWO_PRODUCT_SOURCE,
    provenance_digest: str,
    store: Any = None,
) -> Mapping[str, Any]:
    """Land one passing candidate DRAFT-ONLY via the factorlib save path.

    Reuses the factorlib save kernel with ``status="draft"`` exactly like the research
    loop's ``_save_draft`` (``research/loop.py``). The adapter's OWN guard rejects any
    non-draft status BEFORE factorlib (invariant 2, first gate); factorlib's
    ``_VALID_SAVE_STATUS`` regression is the second gate. Promotion stays the existing
    human ``/factorlib/promote`` gate — this never writes any other status.

    ``candidate`` carries ``name`` / ``expr`` (+ optional ``description``); a
    ``status`` key other than ``draft`` is refused loudly. ``provenance_digest`` is the
    run's ``ContextSnapshot`` digest, recorded in the product ``meta`` for lineage.
    """
    status = _candidate_str(candidate, "status", _DRAFT_STATUS) or _DRAFT_STATUS
    if status != _DRAFT_STATUS:
        raise WeiwoDraftStatusError(
            f"帷幄 lands products draft-only; refused status={status!r} before factorlib "
            "(promotion is the human /factorlib/promote gate)"
        )
    name = _candidate_str(candidate, "name")
    expr = _candidate_str(candidate, "expr")
    description = _candidate_str(candidate, "description")

    # imported lazily (import purity; factorlib is a heavy leaf package).
    from guanlan_v2.factorlib.api import SaveIn, _save_factor
    from guanlan_v2.factorlib.store import LibraryFactorStore

    body = SaveIn(
        name=name, expr=expr, family="library_mined",
        description=description or f"帷幄 ONLINE 研究产物 · {name}",
        source=source or WEIWO_PRODUCT_SOURCE, status=_DRAFT_STATUS,
        meta={"provenance_digest": provenance_digest, "orchestrated": True},
    )
    return _save_factor(body, store if store is not None else LibraryFactorStore())


# --------------------------------------------------------------------------- #
# capability binding — the reviewed read-only-data + memory.propose surface      #
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class WeiwoCapabilityBinding:
    """The resolved weiwo capability surface (a pure projection of the Phase-3 surface).

    ``data_method_refs`` are the Task-3 ONLINE method refs (``verified_snapshot`` /
    ``news`` / ``ohlcv`` — all ``read_only=True``); ``memory_capabilities`` is exactly
    ``{"memory.propose"}`` (proposal-only). It grants NO order/signal write, NO
    memory-accept, NO code/skill write — provable by scanning
    :meth:`granted_capability_names`. It is a pure function of ``method_specs``: no
    fetched data can widen it (invariant 5).
    """

    data_method_refs: tuple[ContentRef, ...]
    data_all_read_only: bool
    memory_capabilities: frozenset[str]

    def granted_capability_names(self) -> tuple[str, ...]:
        """Every capability name granted (data method-ref ids + memory capabilities)."""
        return tuple(r.id for r in self.data_method_refs) + tuple(
            sorted(self.memory_capabilities))

    def write_capability_names(self) -> tuple[str, ...]:
        """The granted names that would be a write/actuate/accept capability (must be ())."""
        return tuple(
            n for n in self.granted_capability_names()
            if any(p in n.lower() for p in _WRITE_CAP_PATTERNS)
        )


def resolve_weiwo_capability_binding(*, method_specs: tuple[Any, ...]) -> WeiwoCapabilityBinding:
    """Resolve the weiwo capability binding from the Phase-3 data method specs.

    Binds ONLY the existing Task-3 ONLINE read-only method refs (no new id minted) plus
    ``memory.propose``. The result is a pure projection of the reviewed surface —
    identical whatever data is later fetched (invariant 5).
    """
    desc = build_online_live_descriptor(method_specs=method_specs, handler_ref=_CAP_HANDLER)
    bound_refs = set(desc.method_refs)
    bound = tuple(s for s in method_specs if s.method_ref in bound_refs)
    all_read_only = bool(bound) and all(getattr(s, "read_only", False) for s in bound)
    return WeiwoCapabilityBinding(
        data_method_refs=tuple(desc.method_refs),
        data_all_read_only=all_read_only,
        memory_capabilities=frozenset({WEIWO_MEMORY_PROPOSE}),
    )


# --------------------------------------------------------------------------- #
# service ports (Protocols) — production wires real Phase-3/4/5/7 machinery     #
# --------------------------------------------------------------------------- #
class WeiwoMemoryPreparer(Protocol):
    """The ONLINE memory-prep port (production: ``prepare_online`` → ``ContextSnapshot``
    over a Bootstrap authority). Its result must carry ``context_snapshot_digest`` and
    the frozen ``as_of`` (never a wall clock)."""

    def prepare(self, data_context: DataContext) -> Any: ...


class WeiwoResearchPlanner(Protocol):
    """The Phase-7 dynamic-planner port. Returns the proposed research MainPlan/seed
    candidate, or ``None`` on an honest planner failure (no template fallback)."""

    def propose(self, request: Any, *, data_context: DataContext,
                context_snapshot_digest: str) -> Any | None: ...


class WeiwoApprovalGate(Protocol):
    """The Phase-7 REQUIRED-approval port. Returns the human decision; only
    ``ApprovalDecision.APPROVED`` admits the plan for execution."""

    def decide(self, request: Any, plan: Any) -> ApprovalDecision: ...


class WeiwoFallbackMaterializer(Protocol):
    """Materialize the explicit ``fallback_preset_id`` into an executable seed candidate
    (production: the Phase-5/7 preset admission). Only reached when the planner failed
    AND the request carries a ``fallback_preset_id``; it STILL goes through approval."""

    def materialize(self, preset_id: str, request: Any, *,
                    data_context: DataContext) -> Any: ...


class WeiwoOptimizer(Protocol):
    """The Phase-4 Evaluator-Optimizer port (production: ``run_optimize``). Receives the
    approved ``seed`` + the frozen ``data_context`` + the ``evaluate_validation`` binding
    and returns an outcome carrying ``passing`` / ``failing`` candidate collections."""

    def run(self, *, seed: Any, data_context: DataContext,
            evaluate_validation: Callable[[Any, DataContext], ValidationMetrics]) -> Any: ...


class WeiwoRejectionSink(Protocol):
    """The append-only run-events / ``TrialLedger`` sink a failing candidate is recorded
    in (NOTHING is written to factorlib for it; no "rejected" status is invented)."""

    def record_rejection(self, candidate: Any, *, reason: str) -> None: ...


# --------------------------------------------------------------------------- #
# WeiwoRuntimeBindings — the internal frozen service-port carrier (unregistered) #
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class WeiwoRuntimeBindings:
    """The frozen service-port carrier the ONLINE research driver runs against.

    An internal, unregistered carrier (never a schema payload) — the ONLINE symmetric
    twin of Task 4's ``ReplayRuntimeBindings``. ``clock`` is the run's start-frozen
    clock (read once by ``build_online_data_context``); the four Phase-3 inputs build
    the frozen ONLINE context; ``schema_registry`` + ``request_method_spec`` +
    ``request_params`` anchor a representative ``DataRequest`` (invariant 1). The service
    ports (memory / planner / approval / optimizer / rejection sink) are production-wired
    to the real Phase-3/4/5/7 machinery and faked with real-contract objects in tests.
    ``factor_saver`` defaults to :func:`save_draft_to_factorlib` (the real draft-only
    landing); ``factor_store`` lets a test point it at a temp library.
    """

    clock: AuthoritativeClock
    source_config: Any
    source_registry: Any
    routing: Any
    manifest: Any
    memory_preparer: WeiwoMemoryPreparer
    planner: WeiwoResearchPlanner
    approval: WeiwoApprovalGate
    optimizer: WeiwoOptimizer
    rejection_sink: WeiwoRejectionSink
    capability_binding: WeiwoCapabilityBinding
    schema_registry: Any = None
    request_method_spec: Any = None
    request_params: Mapping[str, Any] | None = None
    fallback_materializer: WeiwoFallbackMaterializer | None = None
    factor_saver: Callable[..., Mapping[str, Any]] = save_draft_to_factorlib
    factor_store: Any = None
    product_source: str = WEIWO_PRODUCT_SOURCE
    data_context: DataContext | None = None
    eval_overrides: Mapping[str, Any] | None = None


# --------------------------------------------------------------------------- #
# WeiwoRunReceipt — the internal (unregistered) result carrier                  #
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class WeiwoRunReceipt:
    """The internal result carrier of one ONLINE research run (never a schema payload).

    ``stop_reason`` ∈ {``completed``, ``completed_no_draft``, ``unapproved``,
    ``halted_no_fallback``}. ``draft_ids`` are the landed draft product ids (empty when
    nothing passed or the run was refused before execution). ``context_snapshot_digest``
    binds the run's start-frozen memory snapshot.
    """

    run_id: str
    context_snapshot_digest: str
    draft_ids: tuple[str, ...]
    stop_reason: str


# --------------------------------------------------------------------------- #
# evaluate_validation binding — adapt the run_graph mapping → ValidationMetrics  #
# --------------------------------------------------------------------------- #
def _metrics_to_validation_metrics(metrics: Any) -> ValidationMetrics:
    """Adapt a ``run_graph`` metrics mapping to the reviewed L1 ``ValidationMetrics``.

    Honest absence: every field is ``None`` when the graph produced no metric (never
    zero-filled). ``source`` is always ``"run_graph"`` (the deterministic evaluator that
    produced them). Mirrors the research-loop metric口径 (``rank_ic`` / ``sharpe`` /
    ``ann_return`` / ``oos_verdict`` / ``n_dates`` / ``factor``).
    """
    m = metrics if isinstance(metrics, Mapping) else {}

    def _f(key: str) -> float | None:
        v = m.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    verdict = m.get("oos_verdict")
    if verdict not in ("robust", "degraded", "overfit", "insufficient", "na"):
        verdict = None
    n_dates = m.get("n_dates")
    n_dates = int(n_dates) if isinstance(n_dates, int) and not isinstance(n_dates, bool) and n_dates >= 0 else None
    factor = m.get("factor")
    factor = str(factor) if isinstance(factor, str) and factor.strip() else None
    return ValidationMetrics(
        rank_ic=_f("rank_ic"), sharpe=_f("sharpe"), ann_return=_f("ann_return"),
        oos_verdict=verdict, n_dates=n_dates, factor=factor, source="run_graph",
    )


def _candidate_graph(candidate: Any) -> Mapping[str, Any]:
    """Extract the workflow graph from a candidate (mapping ``graph`` / ``.graph`` attr /
    the candidate itself if it is already a graph mapping)."""
    if isinstance(candidate, Mapping):
        g = candidate.get("graph")
        if isinstance(g, Mapping):
            return g
        if "nodes" in candidate:
            return candidate
    g = getattr(candidate, "graph", None)
    if isinstance(g, Mapping):
        return g
    raise ValueError("weiwo candidate carries no workflow graph to evaluate")


def _make_evaluate_validation(
    bindings: WeiwoRuntimeBindings,
) -> Callable[[Any, DataContext], ValidationMetrics]:
    """Build the Phase-4 ``evaluate_validation`` binding over the run_graph wrapper
    (clause C2). The optimizer calls it synchronously; in production the whole
    ``run_optimize`` loop runs in a worker thread (via ``asyncio.to_thread``), so the
    wrapper's event-loop guard passes."""
    overrides = dict(bindings.eval_overrides) if bindings.eval_overrides else None

    def _evaluate_validation(candidate: Any, data_context: DataContext) -> ValidationMetrics:
        result = evaluate_validation_via_run_graph(
            _candidate_graph(candidate), overrides=overrides)
        return _metrics_to_validation_metrics(result.get("metrics"))

    return _evaluate_validation


# --------------------------------------------------------------------------- #
# run_weiwo_research — the ONLINE research driver                               #
# --------------------------------------------------------------------------- #
def run_weiwo_research(
    request: Any,
    *,
    bindings: WeiwoRuntimeBindings,
) -> "ShadowReplayRunState | WeiwoRunReceipt":
    """Drive one ONLINE research run — the honest 帷幄 ONLINE binding.

    Flow: ① the frozen ONLINE ``DataContext`` (``build_online_data_context``, ``as_of``
    frozen at start); ② Bootstrap → ``ContextSnapshot`` via ``prepare_online``;
    ③ open the research MainPlan via the Phase-7 dynamic planner (DYNAMIC / REQUIRED
    approval), or an explicit ``fallback_preset_id`` on a planner failure; ④ the Phase-4
    ``run_optimize`` loop with ``evaluate_validation`` bound to
    :func:`evaluate_validation_via_run_graph`; ⑤ passing candidates land DRAFT-ONLY,
    failing ones are recorded only in run events / the ``TrialLedger``.

    Red lines (all enforced HERE): start-frozen ``as_of`` (invariant 1); an unapproved
    DYNAMIC plan NEVER executes and a missing-fallback planner failure is an honest
    terminal failure with no silent preset fallback (invariant 4); a prompt-injected
    instruction in fetched live text can never widen capabilities or fabricate an
    approval (invariant 5 — this driver never reads fetched data as an instruction).
    """
    run_id = f"weiwo-run.{getattr(request, 'request_id', 'req')}"

    # ① the start-frozen ONLINE data context (Task 3; reads the run clock exactly once).
    data_context = bindings.data_context
    if data_context is None:
        data_context = build_online_data_context(
            clock=bindings.clock, source_config=bindings.source_config,
            source_registry=bindings.source_registry, routing=bindings.routing,
            manifest=bindings.manifest)
    as_of = data_context.as_of

    # invariant 1 anchor — a representative DataRequest carries exactly the frozen as_of
    # (no second wall-clock read; the request's as-of is the frozen context's).
    if bindings.schema_registry is not None and bindings.request_method_spec is not None:
        probe_req = build_data_request(
            data_context, method_spec=bindings.request_method_spec,
            params=dict(bindings.request_params or {}), registry=bindings.schema_registry,
            request_id=f"{run_id}.probe")
        if probe_req.as_of != as_of:
            raise WeiwoSnapshotBindingError(
                "a weiwo DataRequest as_of drifted from the start-frozen ONLINE as_of")

    # ② Bootstrap → ContextSnapshot via the ONLINE memory facade (prepare_online).
    prep = bindings.memory_preparer.prepare(data_context)
    context_snapshot_digest = str(getattr(prep, "context_snapshot_digest"))
    prep_as_of = getattr(prep, "as_of", as_of)
    if prep_as_of != as_of:
        raise WeiwoSnapshotBindingError(
            "the memory ContextSnapshot as_of drifted from the start-frozen ONLINE as_of")

    def _halt(stop_reason: str) -> WeiwoRunReceipt:
        return WeiwoRunReceipt(
            run_id=run_id, context_snapshot_digest=context_snapshot_digest,
            draft_ids=(), stop_reason=stop_reason)

    # ③ open the research MainPlan (DYNAMIC), or the explicit fallback on planner failure.
    plan = bindings.planner.propose(
        request, data_context=data_context, context_snapshot_digest=context_snapshot_digest)
    if plan is None:
        fallback_id = getattr(request, "fallback_preset_id", None)
        if fallback_id is None or bindings.fallback_materializer is None:
            # honest terminal failure — NO silent preset fallback, nothing executes.
            return _halt("halted_no_fallback")
        plan = bindings.fallback_materializer.materialize(
            str(fallback_id), request, data_context=data_context)

    # approval RED LINE — DYNAMIC and the explicit fallback BOTH go through REQUIRED
    # human approval; an unapproved plan NEVER executes (zero reservations).
    decision = bindings.approval.decide(request, plan)
    if decision is not ApprovalDecision.APPROVED:
        return _halt("unapproved")

    # ④ the Phase-4 optimizer loop with evaluate_validation bound to run_graph.
    evaluate_validation = _make_evaluate_validation(bindings)
    outcome = bindings.optimizer.run(
        seed=plan, data_context=data_context, evaluate_validation=evaluate_validation)

    # ⑤ passing candidates land DRAFT-ONLY; failing ones → run events / TrialLedger only.
    draft_ids: list[str] = []
    for candidate in tuple(getattr(outcome, "passing", ()) or ()):
        saved = bindings.factor_saver(
            candidate, source=bindings.product_source,
            provenance_digest=context_snapshot_digest, store=bindings.factor_store)
        if isinstance(saved, Mapping) and saved.get("ok"):
            name = saved.get("name") or (
                candidate.get("name") if isinstance(candidate, Mapping) else None)
            if name:
                draft_ids.append(str(name))
        else:
            # a save failure is honest degradation → recorded, never a fabricated draft.
            reason = saved.get("reason") if isinstance(saved, Mapping) else "save_failed"
            bindings.rejection_sink.record_rejection(candidate, reason=f"save_failed:{reason}")
    for candidate in tuple(getattr(outcome, "failing", ()) or ()):
        # NOTHING to factorlib; rejection lives only in run events / TrialLedger.
        bindings.rejection_sink.record_rejection(candidate, reason="gate_failed")

    return WeiwoRunReceipt(
        run_id=run_id, context_snapshot_digest=context_snapshot_digest,
        draft_ids=tuple(draft_ids),
        stop_reason="completed" if draft_ids else "completed_no_draft")
