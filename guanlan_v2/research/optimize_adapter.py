# -*- coding: utf-8 -*-
"""Factor-research adapter over ``optimize.run_optimize`` (Phase 4 Task 10, closing).

Proves — as a **regression, not a migration** — that the existing
``research.loop.run_research_loop`` behaviour is preserved through the Phase 4
Evaluator-Optimizer state machine (``optimize.run_optimize`` + the four-layer
evaluator + the Governor + the TrialLedger). The old loop is **not touched**: its
sha1 ``workflow.executor.graph_signature`` stall key stays in ``research.loop`` while
this adapter's ``OptimizeCandidate.candidate_hash`` equality is proven
decision-equivalent by test vectors, never by patching the old loop.

Honesty inventory — what stays on the old path (explicit, reviewed)
-------------------------------------------------------------------
* ``POST /research/loop/start`` and its ``1..5`` / ``0..0.2`` clamps continue to call
  ``run_research_loop`` unchanged; the production cutover to this adapter is a later
  reviewed change with its own plan (Phase 9-adjacent), gated on live parity evidence.
  This adapter is reachable only by direct import.
* The LLM propose (``loop._call_generate``) remains a pre-loop step this adapter also
  calls before seeding: a propose failure is an honest termination with a lesson and
  **no template fallback** — strictly the loop's red line.
* Products route **draft-only** through the unchanged ``loop._route_product`` (ML fidelity
  guard, the ``draft`` / ``draft_compose`` / ``draft_model`` three channels and the
  ``save_failed: 因子名已存在`` duplicate-name expectation all preserved); there is no
  auto-promotion and no ``"rejected"`` factorlib status is assumed or introduced
  (``factorlib`` has none — holdout-consumed candidates live in the ledger, not as a
  factorlib state).
* The LLM holds **zero trading authority**; the critique/propose seam is sync self-HTTP
  and therefore runs only inside a daemon thread, never a coroutine (loop.py:4-8).
* Degradations are badged: the compatibility-grade :class:`DataContext` this adapter
  binds over ``content_digest({"universe","freq","start","end"})`` is marked
  ``legacy_data_binding`` (a full Phase 3 ``build_data_context`` binding is deferred to
  the Phase 9 帷幄 adapter); a rule-critique fallback keeps its visible
  ``(规则兜底·非 LLM)`` badge; ``correlation_unadjusted`` is surfaced by the governor.

Behaviour-equivalence seams (both paths script ONE behaviour)
-------------------------------------------------------------
The equivalence tests monkeypatch the shared ``research.loop`` seams (``_call_generate``
/ ``_run_graph_eval`` / ``_call_critique`` / ``_route_product`` → ``_save_draft`` /
``_save_compose_expr`` / ``_call_train_promote`` / ``_save_graph`` / ``_write_lesson``)
which *both* ``run_research_loop`` and :func:`run_research_optimize` delegate to, then
assert pairwise-equal decisions (round counts, gate verdicts, product channel, lesson
writes, honest-stop errors). This adapter's evaluate/gate/improve bindings therefore go
through the exact same ``research.loop`` seams, so the equivalence tests script one
behaviour and observe two paths.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import guanlan_v2.research.loop as rl
from guanlan_v2.research import store as rstore

from guanlan_v2.orchestration.context import ClockSpec, DataContext
from guanlan_v2.orchestration.digest import DigestHex, content_digest
from guanlan_v2.orchestration.enums import DataBackend, DataMode, ExperimentStatus
from guanlan_v2.orchestration.eventstore import RuntimeStores, SchemaRegistryResolver
from guanlan_v2.orchestration.evaluator import (
    RULE_FALLBACK_BADGE,
    l0_run_gate,
    l1_normalize_run_graph_metrics,
)
from guanlan_v2.orchestration.governor import Governor
from guanlan_v2.orchestration.optimize import (
    OPTIMIZE_MAX_ROUNDS,
    ExperimentStateStore,
    PayloadRoundStore,
    run_optimize,
)
from guanlan_v2.orchestration.runtime_clock import AuthoritativeClock, SystemClock, clock_now
from guanlan_v2.orchestration.trial import (
    GateResult,
    OptimizeCandidate,
    SplitSpec,
    StudySpec,
    ValidationMetrics,
)
from guanlan_v2.orchestration.trial_ledger import PHASE4_STATE_CELL_NAMESPACES, TrialLedger

__all__ = [
    "candidate_from_graph",
    "research_study",
    "research_split_spec",
    "research_evaluate_validation",
    "research_gate",
    "research_improve",
    "run_research_optimize",
]

# ── frozen adapter identity / display constants ──────────────────────────────
#: the badged compatibility data-binding domain (see the honesty inventory).
LEGACY_DATA_BINDING = "legacy_data_binding"
#: the propose round's diag text (byte-identical to loop.py:326).
_PROPOSE_DIAG = "初始生成(LLM propose)"
#: the substring a stall :class:`~guanlan_v2.orchestration.trial.Feedback` carries in its
#: reason (optimize.py ``_stall_feedback``); research_improve maps it to a stall suffix.
_STALL_MARKER = "【停滞警告】"
#: the stall-warning suffix appended to the critique constraints on a stall retry
#: (carries the visible 停滞 marker, parity with loop.py:377-380).
_STALL_SUFFIX = (
    "\n【停滞警告】你上一份改进与上一轮完全相同(签名一致),指标必然与本轮完全相同。"
    "这次必须对图做出实质修改(换表达式/换模型类型或超参/改合成方式或回测参数皆可)。"
)
#: the honest stall-abort error text (parity with loop.py:389-390).
_STALL_ERROR = (
    "批判环停滞: 两次改进产出的图与上一轮完全相同(签名一致,节点参数亦未变),指标不会变,诚实中断"
)


# ── small exceptions the run_optimize continue/terminate paths catch ─────────
class ResearchEvalError(Exception):
    """A ``run_graph`` outcome L0 refused (``ok=False`` / no terminal).

    Raised by :func:`research_evaluate_validation` so ``run_optimize`` treats the round
    as an eval-failure-continue (metrics stay unrevealed, the round archives an error)
    — parity with the loop's ``failed = not ex.get("ok")`` continue path.
    """


class ResearchImproveError(Exception):
    """The critique bridge failed to produce a graph — honest improve termination."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Phase 4 registry / stores (Task 9 cumulative chain — imported, not redefined) #
# --------------------------------------------------------------------------- #
_SHARED_REGISTRY: Optional[Tuple[Any, SchemaRegistryResolver]] = None


def _shared_registry() -> Tuple[Any, SchemaRegistryResolver]:
    """The sealed cumulative Phase-4 registry + its resolver (built once, cached).

    Uses ``trial.build_phase4_registry`` pinned to the exact sealed Phase-3 full base
    digest (Task 9); the registry is immutable/sealed, so a single instance is safely
    shared across every per-run :class:`RuntimeStores`.
    """
    global _SHARED_REGISTRY
    if _SHARED_REGISTRY is None:
        from guanlan_v2.orchestration import trial as _trial

        resolver = SchemaRegistryResolver()
        registry = _trial.build_phase4_registry(_trial.PHASE4_BASE_REGISTRY_DIGEST)
        resolver.register(registry)
        _SHARED_REGISTRY = (registry, resolver)
    return _SHARED_REGISTRY


def _adapter_code_prompt_model_hash() -> DigestHex:
    """The frozen code/prompt/model identity of this adapter's evaluation path.

    Binds the adapter tag, the exact critique constraints digest and the two self-POST
    seam paths, so a change to any of them moves the trial-triple key (a different
    prompt is a different trial, never a silent cache hit).
    """
    return content_digest(
        {
            "adapter": "research_loop",
            "constraints_digest": content_digest(rl._CRITIQUE_CONSTRAINTS),
            "generate_path": "/workflow/generate",
            "critique_path": "/workflow/critique",
        }
    )


def _build_env(clock: AuthoritativeClock):
    """A fresh per-run Phase-2 in-memory store set + Task 5/8 services over the registry."""
    registry, resolver = _shared_registry()
    stores = RuntimeStores(
        resolver=resolver, clock=clock,
        allowed_cell_namespaces=PHASE4_STATE_CELL_NAMESPACES)
    ledger = TrialLedger(
        event_store=stores.events, payload_store=stores.payloads,
        state_cells=stores.cells, registry=registry, clock=clock,
        uow_factory=lambda: stores.unit_of_work)
    states = ExperimentStateStore(
        payload_store=stores.payloads, state_cells=stores.cells, registry=registry,
        clock=clock, uow_factory=lambda: stores.unit_of_work)
    rounds = PayloadRoundStore(payload_store=stores.payloads, registry=registry)
    return registry, stores, ledger, states, rounds


# --------------------------------------------------------------------------- #
# Produces — the seven public helper contracts                                 #
# --------------------------------------------------------------------------- #
def candidate_from_graph(
    graph: Dict[str, Any], *, params: Dict[str, Any], parent_trial_id: Optional[str] = None
) -> OptimizeCandidate:
    """Seal a workflow graph as an :class:`OptimizeCandidate` (positions/order ignored).

    Wraps ``OptimizeCandidate.build``: two graphs with the same
    ``workflow.executor.graph_signature`` (layout / unknown keys / order dropped) seal
    the **same** ``candidate_hash`` under the same ``params``, and a param/structure
    change moves it — the decision-equivalent of the old loop's sha1 stall key.
    ``parent_trial_id`` is audit lineage only (excluded from ``candidate_hash``); kept
    ``None`` so identical graph+params candidates stay byte-identical for idempotent
    payload puts.
    """
    return OptimizeCandidate.build(
        candidate_kind="workflow_graph", graph=graph, params=params,
        parent_trial_id=parent_trial_id)


def research_study(
    goal: str, *, universe: str, freq: str, min_rank_ic: float
) -> StudySpec:
    """The research study identity — display ``objective=goal``, digests display-free.

    ``objective`` / ``label_definition`` are human display text; the identity handles
    (``objective_digest`` / ``label_digest`` / ``universe_digest`` / ``frequency`` /
    ``split_policy_digest``) are canonical digests of the research parameters, so the
    universe/split handles are stable across goal re-phrasings while the objective text
    (the goal itself) drives ``objective_digest``.
    """
    objective_digest = content_digest({"domain": "research-objective-v1", "goal": goal})
    label_definition = (
        "研究回路样本外联合门:rank_ic ≥ 门槛 且 oos_verdict == robust 且 sharpe > 0")
    label_digest = content_digest(
        {"domain": "research-label-v1", "min_rank_ic": float(min_rank_ic),
         "oos_required": rl.GATE_OOS_OK, "sharpe_required": True})
    universe_digest = content_digest({"domain": "research-universe-v1", "universe": universe})
    split_policy_digest = content_digest(
        {"domain": "research-split-v1", "scheme": "oos_fraction", "oos_frac": rl._EVAL_OOS_FRAC,
         "label_horizon": 5, "freq": freq})
    return StudySpec(
        objective=goal, objective_digest=objective_digest,
        label_definition=label_definition, label_digest=label_digest,
        universe_digest=universe_digest, frequency=freq,
        split_policy_digest=split_policy_digest)


def research_split_spec(freq: str) -> SplitSpec:
    """The forced ``oos_fraction`` split — ``oos_frac=0.3`` (loop's forced ``_EVAL_OOS_FRAC``).

    Without the forced OOS fraction ``oos_verdict`` never exists and the joint gate can
    never pass; ``label_horizon=5`` mirrors the loop's 5-day forward label.
    """
    return SplitSpec(scheme="oos_fraction", oos_frac=rl._EVAL_OOS_FRAC, label_horizon=5)


def _legacy_data_context(
    clock: AuthoritativeClock, *, universe: str, freq: str,
    start: Optional[str], end: Optional[str],
) -> DataContext:
    """A compatibility-grade :class:`DataContext` badged ``legacy_data_binding``.

    Its ``data_snapshot_content_digest`` is ``content_digest`` over the four research
    data parameters (``universe`` / ``freq`` / ``start`` / ``end``) — so identical
    parameters share one snapshot digest (and therefore one trial-reuse triple) even as
    the audit-only ``as_of`` / ``built_at`` advance. A full Phase 3 ``build_data_context``
    binding is deferred to the Phase 9 帷幄 adapter.
    """
    snap = content_digest(
        {"domain": LEGACY_DATA_BINDING, "universe": universe, "freq": freq,
         "start": start, "end": end})
    as_of = clock_now(clock)
    clock_spec = ClockSpec(as_of=as_of, timezone="Asia/Shanghai", calendar_id="XSHG")
    return DataContext(
        as_of=as_of, clock=clock_spec, mode=DataMode.ONLINE, backend=DataBackend.CACHE,
        strict_pit=False, calendar_id="XSHG", resolved_vendor_chains={},
        source_config_digest=snap, source_registry_digest=snap, routing_snapshot_digest=snap,
        data_snapshot_id=LEGACY_DATA_BINDING, data_snapshot_content_digest=snap,
        built_at=as_of)


def research_evaluate_validation(
    cand: OptimizeCandidate, ctx: Any, *, params: Dict[str, Any], round_k: int = 0,
    max_rounds: int = OPTIMIZE_MAX_ROUNDS, sink: Optional[Dict[str, Any]] = None,
    progress: Optional[Callable[..., None]] = None,
) -> ValidationMetrics:
    """Run the candidate graph through the loop's ``_run_graph_eval`` seam, then L0+L1.

    Calls ``loop._run_graph_eval`` (which invokes ``wex.run_graph(..., overrides={...,
    "oos_frac": 0.3}, prefer_model_terminal=True)`` — canvas ``/workflow/run`` keeps
    ``prefer_model_terminal=False`` so there is zero canvas behaviour change), stashes the
    raw outcome in ``sink`` keyed by ``candidate_hash`` (so the product/display step can
    read its terminal / node_errors / exprs), applies ``evaluator.l0_run_gate`` and — on
    acceptance — ``l1_normalize_run_graph_metrics``. An L0 refusal (``ok=False`` / no
    terminal) raises :class:`ResearchEvalError` so ``run_optimize`` continues to the next
    round without revealing a trial — parity with the loop's failed-eval continue.
    """
    graph = cand.graph if isinstance(cand.graph, dict) else {}
    pr = progress if progress is not None else (lambda **kw: None)
    outcome = rl._run_graph_eval(graph, params, pr, round_k, max_rounds)
    if sink is not None:
        sink[cand.candidate_hash] = outcome
    # required_chain_node_ids=() keeps the fidelity guard where the loop keeps it
    # (loop._route_product reroutes ML→compose on a required-chain error but never fails
    # the gate); L0 here refuses only ok=False / terminal-absent, matching the loop.
    l0 = l0_run_gate(outcome, required_chain_node_ids=())
    if not l0.passed:
        raise ResearchEvalError("; ".join(l0.reasons) or "求值 L0 拒绝")
    return l1_normalize_run_graph_metrics(outcome)


def research_gate(metrics: ValidationMetrics, *, min_rank_ic: float) -> GateResult:
    """The Phase 4 joint gate — parity with ``loop._gate`` (loop.py:284-292).

    A candidate passes only when ``rank_ic >= min_rank_ic`` (NaN-checked) **and**
    ``oos_verdict == robust`` **and** ``sharpe > 0``. All-absent metrics return
    ``status="unavailable"`` (the loop gate's "not passed"); any partial-but-failing set
    returns ``status="failed"``.
    """
    ric = metrics.rank_ic
    shp = metrics.sharpe
    oos = metrics.oos_verdict
    if ric is None and shp is None and oos is None:
        return GateResult(
            status="unavailable", min_rank_ic=float(min_rank_ic),
            reason="指标缺失: 联合门无法判定(样本外/rank_ic/sharpe 全缺)")
    ok_ric = isinstance(ric, (int, float)) and ric == ric
    ok_shp = isinstance(shp, (int, float)) and shp == shp and shp > 0
    passed = bool(
        ok_ric and float(ric) >= float(min_rank_ic)
        and oos == rl.GATE_OOS_OK and ok_shp)
    return GateResult(
        status="passed" if passed else "failed", min_rank_ic=float(min_rank_ic),
        observed_rank_ic=ric if ok_ric else None,
        observed_sharpe=shp if isinstance(shp, (int, float)) and shp == shp else None,
        observed_oos_verdict=oos,
        reason="联合门通过" if passed else "联合门未通过(rank_ic/oos=robust/sharpe>0 有一不满足)")


def _metrics_to_dict(metrics: Optional[ValidationMetrics]) -> Dict[str, Any]:
    if metrics is None:
        return {}
    return {"rank_ic": metrics.rank_ic, "sharpe": metrics.sharpe,
            "ann_return": metrics.ann_return, "oos_verdict": metrics.oos_verdict,
            "n_dates": metrics.n_dates, "factor": metrics.factor}


def research_improve(
    cand: OptimizeCandidate, metrics: ValidationMetrics, feedback: Any, *,
    goal: str, params: Dict[str, Any], state: Dict[str, Any],
) -> OptimizeCandidate:
    """The critique bridge — ``loop._call_critique`` fed with ``_CRITIQUE_CONSTRAINTS``.

    Calls the loop's sync self-POST critique seam (daemon-thread only, never a
    coroutine) with the shared ``_CRITIQUE_CONSTRAINTS``; when ``feedback.reason`` carries
    the stall marker a stall-warning suffix is appended (parity with the loop's stall
    re批). A critique that fails to return a graph is an honest termination
    (:class:`ResearchImproveError` → ``run_optimize`` ``improve_failed``), never a template
    fallback. The rule-fallback ``(规则兜底·非 LLM)`` badge is preserved in the recorded
    diag. ``state`` accumulates per-candidate ``diag_by_hash`` / ``source_by_hash`` for the
    display rows; the new candidate's graph rides ``OptimizeCandidate``, never study
    identity.
    """
    graph = cand.graph if isinstance(cand.graph, dict) else {}
    is_stall = _STALL_MARKER in (getattr(feedback, "reason", "") or "")
    constraints = rl._CRITIQUE_CONSTRAINTS + (_STALL_SUFFIX if is_stall else "")
    try:
        cr = rl._call_critique(goal, _metrics_to_dict(metrics), graph, constraints=constraints)
    except Exception as exc:  # noqa: BLE001 — critique failure is honest termination
        cr = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not cr.get("ok") or not isinstance(cr.get("graph"), dict):
        state["error"] = f"批判环失败: {cr.get('reason')}"
        raise ResearchImproveError(state["error"])

    new_cand = candidate_from_graph(cr["graph"], params=params)
    source = str(cr.get("source") or "?")
    diag = (
        ("(停滞重批) " if is_stall else "")
        + ((RULE_FALLBACK_BADGE + " ") if source == "rule" else "")
        + str(cr.get("diagnosis") or ""))
    state.setdefault("diag_by_hash", {})[new_cand.candidate_hash] = diag
    state.setdefault("source_by_hash", {})[new_cand.candidate_hash] = source
    return new_cand


# --------------------------------------------------------------------------- #
# run_research_optimize — signature-identical to loop.run_research_loop         #
# --------------------------------------------------------------------------- #
def _display_meta(
    round_index: int, cand_hash: str, diag_by_hash: Dict[str, Any],
    source_by_hash: Dict[str, Any],
) -> Tuple[str, Optional[str]]:
    """Round 0 always shows the propose diag; later rounds show the critique diag/source.

    Guards the propose round against a critique that reproduced the seed graph (identical
    ``candidate_hash``) overwriting the seed's diag entry.
    """
    if round_index == 0:
        return _PROPOSE_DIAG, None
    return str(diag_by_hash.get(cand_hash) or ""), source_by_hash.get(cand_hash)


def _ex_from_metrics(metrics: Optional[ValidationMetrics], error: Optional[str]) -> Dict[str, Any]:
    """A minimal raw-outcome shim for a reused round whose eval seam never ran."""
    return {"ok": error is None, "reason": error, "metrics": _metrics_to_dict(metrics),
            "exprs": [], "terminal": None, "node_errors": []}


def _best_k(counted_rounds: List[Any]) -> Optional[int]:
    """Best archived round index — gate-passed wins, else max ``rank_ic`` (first on tie).

    Parity with ``loop.run_research_loop``'s wrap-up best selection (loop.py:399-408): a
    failed-eval round (rank_ic absent) is skipped; equal rank_ic keeps the earlier round.
    """
    best_k: Optional[int] = None
    best_ric: Optional[float] = None
    for r in counted_rounds:
        if r.metrics is None or r.gate is None:
            continue
        ric = r.metrics.rank_ic
        if ric is None:
            continue
        if r.gate.status == "passed":
            return r.round_index
        if best_k is None or ric > best_ric:
            best_k, best_ric = r.round_index, ric
    return best_k


def run_research_optimize(
    run_id: str, goal: str, max_rounds: int, min_rank_ic: float, universe: str,
    freq: str, start: Optional[str], end: Optional[str], progress: Callable[..., None],
) -> Dict[str, Any]:
    """Drive the research loop over ``run_optimize`` — signature-identical to
    ``loop.run_research_loop`` and behaviour-equivalent, returning the same terminal row.

    Propose (honest termination on failure) seeds a candidate; ``run_optimize`` runs the
    contract-governed Evaluator-Optimizer loop over an in-memory Phase-2 store set with a
    per-run :class:`TrialLedger` and ``Governor(trial_budget=OPTIMIZE_MAX_ROUNDS,
    peek_budget=OPTIMIZE_MAX_ROUNDS)``; on a gate pass the winning graph routes through the
    unchanged ``loop._route_product`` (draft-only) and the best graph + a lesson are
    written exactly as the loop's wrap-up. Display rows are appended to the same
    ``research.store`` jsonl for UI parity (best-effort — the authoritative record is the
    ledger).
    """
    params = {"max_rounds": max_rounds, "min_rank_ic": min_rank_ic,
              "universe": universe, "freq": freq, "start": start, "end": end}
    cand_params = {"universe": universe, "freq": freq, "start": start, "end": end}
    rounds_ok = rstore.append_run({"run_id": run_id, "kind": "start", "goal": goal,
                                   "params": params, "ts": _now()})

    # ── 提案(失败诚实终止,绝不降级模板)── parity with loop.py:309-323 ──
    progress(phase="propose", label="① LLM 生成初始工作流…", round_k=0)
    try:
        gen = rl._call_generate(goal)
    except Exception as exc:  # noqa: BLE001
        gen = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not gen.get("ok") or not isinstance(gen.get("graph"), dict):
        error = f"提案失败(诚实终止,不降级模板): {gen.get('reason')}"
        mem = rl._write_lesson(
            goal, f"研究「{goal[:40]}」提案即失败:{str(gen.get('reason'))[:120]}")
        end_row = {"run_id": run_id, "kind": "end", "ok": False, "error": error,
                   "n_rounds": 0, "best_k": None, "best_metrics": None, "promoted": None,
                   "workflow_saved": None, "memory_written": mem,
                   "rounds_recorded": rounds_ok, "ts": _now()}
        rstore.append_run(end_row)
        return end_row

    seed = candidate_from_graph(gen["graph"], params=cand_params)

    # ── Phase-4 machine + per-run side tables ──
    clock = SystemClock()
    registry, stores, ledger, states, rounds_store = _build_env(clock)
    study = research_study(goal, universe=universe, freq=freq, min_rank_ic=min_rank_ic)
    ctx = _legacy_data_context(clock, universe=universe, freq=freq, start=start, end=end)
    governor = Governor(trial_budget=OPTIMIZE_MAX_ROUNDS, peek_budget=OPTIMIZE_MAX_ROUNDS)

    cand_by_hash: Dict[str, OptimizeCandidate] = {seed.candidate_hash: seed}
    ex_by_hash: Dict[str, Any] = {}
    diag_by_hash: Dict[str, Any] = {seed.candidate_hash: _PROPOSE_DIAG}
    source_by_hash: Dict[str, Any] = {seed.candidate_hash: None}
    imp_state = {"diag_by_hash": diag_by_hash, "source_by_hash": source_by_hash, "error": None}
    eval_n = [0]

    def _eval(cand: OptimizeCandidate, _ctx: Any) -> ValidationMetrics:
        k = eval_n[0]
        eval_n[0] += 1
        return research_evaluate_validation(
            cand, _ctx, params=cand_params, round_k=k, max_rounds=max_rounds,
            sink=ex_by_hash, progress=progress)

    def _gate(m: ValidationMetrics) -> GateResult:
        return research_gate(m, min_rank_ic=min_rank_ic)

    def _improve(cand: OptimizeCandidate, m: ValidationMetrics, fb: Any) -> OptimizeCandidate:
        new_cand = research_improve(cand, m, fb, goal=goal, params=cand_params, state=imp_state)
        cand_by_hash[new_cand.candidate_hash] = new_cand
        return new_cand

    result = run_optimize(
        seed=seed, ctx=ctx, study=study, split_spec=research_split_spec(freq),
        max_rounds=max_rounds, governor=governor, evaluate_validation=_eval, gate=_gate,
        improve=_improve, ledger=ledger, rounds=rounds_store, states=states,
        payload_store=stores.payloads, experiment_id=run_id, clock=clock, registry=registry,
        code_prompt_model_hash=_adapter_code_prompt_model_hash(), attribution=None, goal=goal)

    archived = list(rounds_store.rounds(run_id))
    # a round counts (loop parity) iff it reached evaluation — metrics is set (empty on an
    # eval failure); a stall-abort / L0-refusal round (metrics is None) never counts.
    counted = [r for r in archived if r.metrics is not None]
    n = len(counted)

    # ── display rows (UI parity; authoritative record = the ledger) ──
    for r in counted:
        cand = cand_by_hash.get(r.candidate_hash)
        graph = cand.graph if cand is not None else {}
        ex = ex_by_hash.get(r.candidate_hash) or _ex_from_metrics(r.metrics, r.error)
        failed = not ex.get("ok")
        metrics_d = ex.get("metrics") or {}
        exprs = list(ex.get("exprs") or [])
        dish, _ = rl._pick_dish(graph)
        gate = (rl._gate(metrics_d, min_rank_ic) if not failed
                else {"passed": False, "min_rank_ic": float(min_rank_ic),
                      "oos_required": rl.GATE_OOS_OK, "sharpe_required": True})
        diag, crit_source = _display_meta(r.round_index, r.candidate_hash, diag_by_hash,
                                          source_by_hash)
        row = {"run_id": run_id, "k": r.round_index, "ts": _now(),
               "stage": ("propose" if r.round_index == 0 else "improve"),
               "diag": diag, "critique_source": crit_source, "exprs": exprs, "dish": dish,
               "metrics": metrics_d, "gate": gate, "failed": failed,
               "error": (ex.get("reason") if failed else None), "graph": graph,
               "terminal_kind": ((ex.get("terminal") or {}) or {}).get("kind"),
               "node_errors": (ex.get("node_errors") or [])[:5]}
        if not rstore.append_round(row):
            rounds_ok = False

    # ── terminal translation → the loop's end-row contract ──
    status = result.state.status
    stop = result.stop_reason
    promoted: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    if status == ExperimentStatus.PASSED_VALIDATION:
        ok = True
        pass_round = next(
            (r for r in archived if r.gate is not None and r.gate.status == "passed"), None)
        best_k = pass_round.round_index if pass_round is not None else _best_k(counted)
        if pass_round is not None:
            ph = pass_round.candidate_hash
            pcand = cand_by_hash.get(ph)
            pgraph = pcand.graph if pcand is not None else {}
            pex = ex_by_hash.get(ph) or {}
            pdiag, _ = _display_meta(pass_round.round_index, ph, diag_by_hash, source_by_hash)
            progress(phase="promote", label="③ 达标 · 产物入库(draft 待人审)…", round_k=best_k)
            promoted = rl._route_product(
                run_id, best_k, pgraph, goal, pdiag, pex.get("metrics") or {},
                pex.get("terminal"), universe, node_errors=pex.get("node_errors"))
    else:  # FAILED / WAITING — honest termination or clean exhaustion
        best_k = _best_k(counted)
        if stop == "exhausted_no_validation_pass":
            ok = True                                     # rounds exhausted, no pass (loop parity)
        elif stop == "stalled":
            ok, error = False, _STALL_ERROR
        elif stop == "improve_failed":
            ok, error = False, (imp_state.get("error") or "批判环失败(诚实终止)")
        elif stop in ("trial_budget_exhausted", "peek_budget_exhausted"):
            ok = False
            error = f"研究回路预算耗尽({stop}); governor 上限 {OPTIMIZE_MAX_ROUNDS} 轮"
        elif status == ExperimentStatus.WAITING_FOR_MATURITY:
            ok = False
            error = "等待数据成熟(legacy_data_binding 兼容层默认不触发,honest 显形)"
        else:  # no_revealed_rounds / unexpected
            ok = False
            error = f"研究回路异常终止({stop})"

    # ── best graph → workflow library + lesson (loop wrap-up parity) ──
    best_graph: Optional[Dict[str, Any]] = None
    best_metrics: Optional[Dict[str, Any]] = None
    final_diag: Any = _PROPOSE_DIAG
    if best_k is not None:
        best_round = next((r for r in archived if r.round_index == best_k), None)
        if best_round is not None:
            bh = best_round.candidate_hash
            bcand = cand_by_hash.get(bh)
            best_graph = bcand.graph if bcand is not None else None
            best_metrics = (ex_by_hash.get(bh) or {}).get("metrics")
            final_diag, _ = _display_meta(best_round.round_index, bh, diag_by_hash,
                                          source_by_hash)
    ws = rl._save_graph(goal, run_id, best_graph) if best_graph is not None else None

    bm = best_metrics or {}
    if promoted and promoted.get("status") in ("draft", "draft_compose", "draft_model"):
        lesson = (f"研究「{goal[:40]}」{n}轮达标:{promoted['name']}(status={promoted['status']}) "
                  f"rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')} 已入draft待人审;"
                  f"诊断:{str(final_diag)[:80]}")
    elif promoted and promoted.get("status") == "save_failed":
        lesson = (f"研究「{goal[:40]}」{n}轮达标但入库失败:{str(promoted.get('reason'))[:80]};"
                  f"rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')}")
    elif error:
        lesson = f"研究「{goal[:40]}」{n}轮中断:{str(error)[:120]}"
    else:
        lesson = (f"研究「{goal[:40]}」{n}轮未达标(门 rank_ic≥{min_rank_ic} 且 oos=robust):"
                  f"最佳 rank_ic={bm.get('rank_ic')} oos={bm.get('oos_verdict')};"
                  f"诊断:{str(final_diag)[:80]}")
    mem_ok = rl._write_lesson(goal, lesson)

    end_row = {"run_id": run_id, "kind": "end", "ok": ok, "error": error,
               "n_rounds": n, "best_k": best_k, "best_metrics": (bm or None),
               "promoted": promoted, "workflow_saved": ws, "memory_written": mem_ok,
               "rounds_recorded": rounds_ok, "stop_reason": stop, "ts": _now()}
    rstore.append_run(end_row)
    return end_row
