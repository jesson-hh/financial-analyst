# -*- coding: utf-8 -*-
"""The four-layer evaluator (Phase 4 Task 7).

This module composes the Phase 4 evaluation-fact contracts (:mod:`trial`) and the
pure L2 governor (:mod:`governor`) into the four honesty layers the Evaluator-Optimizer
runs around each candidate:

* **L0 — honesty gates (deterministic, cheap, run before/around the spend).**
  :func:`l0_candidate_gate` is a *pure function of the candidate* — it never touches an
  evaluation callable (invariant 1) — and refuses a structurally-broken candidate
  (empty nodes / an edge to an unknown node / a non-finite or non-JSON config value /
  an unclassifiable terminal) before any evaluation is scheduled. Its dish taxonomy is
  parity with ``research.loop._pick_dish`` (loop.py:258-281): backtest / compose(≥2
  exprs) / single-expr report; anything else is honestly unsupported.
  :func:`l0_run_gate` runs post-run, pre-reveal: the run must have ``ok=True`` with a
  present terminal, and **any** ``node_errors`` entry on the required chain (the
  terminal's ancestry, including the recipe's ML node — fidelity-guard parity with
  ``research.loop._route_product`` at loop.py:180-186) refuses metric acceptance even
  when the terminal parsed (invariant 2). An off-chain diagnostic error is accepted
  (照跑存档不过门).

* **L1 — deterministic metrics normalization.**
  :func:`l1_normalize_run_graph_metrics` maps the six ``workflow.executor.metrics_of_terminal``
  keys (``rank_ic`` / ``sharpe`` / ``ann_return`` / ``oos_verdict`` / ``n_dates`` /
  ``factor``) onto :class:`~guanlan_v2.orchestration.trial.ValidationMetrics` with
  ``source="run_graph"``. A missing or NaN input becomes ``None`` (honest absence),
  never zero-filled (invariant 3); an unrecognized ``oos_verdict`` string is coerced to
  ``None`` rather than smuggled past the strict contract; no field outside the six keys
  is ever invented, and extra diagnostic keys are ignored (照跑存档不过门).

* **L2 — governance delegate.**
  :func:`l2_govern` is a thin pass-through to :meth:`Governor.govern` so the four layers
  present one module surface. It contains **no additional logic**: it returns the exact
  object the governor returned (invariant 4).

* **L3 — attribution feedback (honest ambiguity over forced attribution).**
  :class:`AttributionPort` is the injectable LLM seam (its production binding arrives in
  Task 10's research adapter through the existing daemon-thread self-POST critique
  bridge; **nothing here performs HTTP**). :func:`l3_feedback` does deterministic
  pre-narrowing first: when the candidate graph cannot be narrowed to ≤2 responsible
  node ids from structure alone and no port is supplied, it returns
  ``Feedback(ambiguous=True, source="deterministic")`` honestly. With a port, the port's
  answer is validated against the contract matrix (a port returning >2 targets or
  unsourced evidence ids is coerced to ambiguous with a reason, never trusted blindly);
  a port failure falls back to ``source="rule"`` with the visible ``"(规则兜底·非 LLM)"``
  badge embedded in ``reason`` (parity with loop.py:392-395). L3 reads only validation
  metrics and the explicitly-supplied artifact refs — its signature admits no holdout
  detail type, and the Task 6 module-inspection guard pins that this file imports no
  holdout read path.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from guanlan_v2.orchestration.enums import Confidence
from guanlan_v2.orchestration.refs import LOGICAL_ID_PATTERN
from guanlan_v2.orchestration.trial import (
    Feedback,
    GovernanceReport,
    HonestyGateReport,
    OptimizeCandidate,
    SplitSpec,
    StudyFamily,
    ValidationMetrics,
)

__all__ = [
    "AttributionPort",
    "RULE_FALLBACK_BADGE",
    "l0_candidate_gate",
    "l0_run_gate",
    "l1_normalize_run_graph_metrics",
    "l2_govern",
    "l3_feedback",
]

#: Visible non-LLM badge text embedded in the ``reason`` of a rule-fallback attribution
#: (parity with ``research.loop`` at loop.py:392-395).
RULE_FALLBACK_BADGE = "(规则兜底·非 LLM)"

#: The set of ``oos_verdict`` values the strict :class:`ValidationMetrics` contract
#: admits; any other string from a run outcome is coerced to ``None``.
_OOS_VERDICTS = frozenset({"robust", "degraded", "overfit", "insufficient", "na"})

#: Terminal/harness node types that carry no editable signal — dish taxonomy parity
#: with ``research.loop._pick_dish``: only ``formula`` / ``factorlib`` nodes contribute
#: exprs, so a backtest node is a terminal, not a dish source.
_LOGICAL_ID_RE = re.compile(LOGICAL_ID_PATTERN)


# --------------------------------------------------------------------------- #
# shared deterministic helpers                                                #
# --------------------------------------------------------------------------- #
def _fnum(v: Any) -> float | None:
    """float 化并把 NaN/Inf/无法解析 → ``None`` (honest absence, never zero-filled)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _nonneg_int(v: Any) -> int | None:
    """Coerce to a non-negative int, else ``None`` (a negative or NaN count is absent)."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 0 or f != int(f):
        return None
    return int(f)


def _nonempty_str(v: Any) -> str | None:
    """A non-empty display string, else ``None``."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _find_nonfinite_or_nonjson(value: Any, path: str) -> str | None:
    """Return a reason string for the first non-finite float / non-JSON value, else None.

    Mirrors the strict ``PlanNode.params`` acceptance rule (see
    ``trial._ensure_json_shaped``) but as a *diagnostic* walk so L0 can name where a
    malformed config value sits rather than raising — the gate refuses honestly instead
    of crashing.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else f"non-finite float at {path}"
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            reason = _find_nonfinite_or_nonjson(item, f"{path}[{i}]")
            if reason is not None:
                return reason
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return f"non-string dict key at {path}"
            reason = _find_nonfinite_or_nonjson(item, f"{path}.{key}")
            if reason is not None:
                return reason
        return None
    return f"non-JSON value ({type(value).__name__}) at {path}"


def _edge_endpoints(edge: Mapping[str, Any]) -> list[str]:
    """The node ids an edge references, from its ``from`` / ``to`` ``[node_id, port]``
    endpoints (the ``workflow.executor`` edge shape)."""
    ids: list[str] = []
    for key in ("from", "to"):
        v = edge.get(key)
        if isinstance(v, (list, tuple)) and v:
            ids.append(str(v[0]))
    return ids


def _classify_dish(graph: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    """图形状 → (菜名, exprs) — parity with ``research.loop._pick_dish`` (loop.py:258-281).

    ``formula.params.expr`` + ``factorlib.params.name`` contribute exprs; a ``backtest``
    node → ``"backtest"``; ≥2 exprs → ``"compose"``; 1 → ``"report2"``; 0 exprs →
    ``(None, [])`` (honestly unsupported).
    """
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    exprs: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        p = n.get("params") or {}
        t = n.get("type")
        if t == "formula":
            e = str(p.get("expr") or "").strip()
            if e:
                exprs.append(e)
        elif t == "factorlib":
            e = str(p.get("name") or p.get("expr") or "").strip()
            if e:
                exprs.append(e)
    if not exprs:
        return None, []
    if any(isinstance(n, dict) and n.get("type") == "backtest" for n in nodes):
        return "backtest", exprs
    if len(exprs) >= 2:
        return "compose", exprs
    return "report2", exprs


def _responsible_node_ids(candidate: OptimizeCandidate) -> tuple[str, ...]:
    """The sorted, duplicate-free node ids of the candidate graph that are valid worker
    ids (``LogicalId`` grammar), used as the deterministic attribution unit.

    A node id that cannot be a ``LogicalId`` (uppercase / path-shaped) can never be a
    ``Feedback.target_worker_ids`` entry, so it is dropped — an all-invalid graph
    yields an empty set and L3 falls to honest ambiguity rather than a spurious target.
    """
    graph = candidate.graph if isinstance(candidate.graph, dict) else {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    ids = {
        str(n.get("id"))
        for n in nodes
        if isinstance(n, dict) and n.get("id") is not None
    }
    return tuple(sorted(i for i in ids if _LOGICAL_ID_RE.fullmatch(i)))


# --------------------------------------------------------------------------- #
# L0 — honesty gates                                                          #
# --------------------------------------------------------------------------- #
def l0_candidate_gate(candidate: OptimizeCandidate) -> HonestyGateReport:
    """L0 pre-evaluation structural refusal — a **pure function of the candidate**.

    Runs four deterministic checks (all executed, all listed in ``checked``): non-empty
    nodes; every edge references a known node id; the graph/params config is finite and
    JSON-shaped; a classifiable terminal exists (dish taxonomy parity with
    ``research.loop._pick_dish``). Any failure yields ``passed=False`` with a named
    reason; the callable is never touched — this refusal is strictly cheaper than the
    evaluation it guards (invariant 1).
    """
    checked = (
        "classifiable_terminal",
        "edges_reference_known_nodes",
        "nodes_nonempty",
        "params_finite_json",
    )
    reasons: list[str] = []

    graph = candidate.graph if isinstance(candidate.graph, dict) else {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    node_ids = {str(n.get("id")) for n in nodes if isinstance(n, dict)}

    # 1. non-empty nodes
    if not nodes:
        reasons.append("candidate graph has no nodes")

    # 2. edges reference known node ids
    unknown = sorted({
        eid
        for e in edges
        if isinstance(e, dict)
        for eid in _edge_endpoints(e)
        if eid not in node_ids
    })
    if unknown:
        reasons.append(f"edge references unknown node id(s): {', '.join(unknown)}")

    # 3. params / graph finite and JSON-shaped
    config_reason = _find_nonfinite_or_nonjson(
        candidate.graph, "graph"
    ) or _find_nonfinite_or_nonjson(candidate.params, "params")
    if config_reason is not None:
        reasons.append(f"non-finite/non-JSON config: {config_reason}")

    # 4. classifiable terminal (dish taxonomy)
    dish, _ = _classify_dish(graph)
    if dish is None:
        reasons.append(
            "no classifiable terminal (dish taxonomy: backtest / compose≥2 exprs / "
            "single-expr report)"
        )

    return HonestyGateReport(
        passed=not reasons, reasons=tuple(reasons), checked=tuple(sorted(set(checked)))
    )


def l0_run_gate(
    outcome: Mapping[str, Any], *, required_chain_node_ids: tuple[str, ...]
) -> HonestyGateReport:
    """L0 post-run, pre-reveal refusal over a ``run_graph`` outcome.

    Refuses metric acceptance unless the run is ``ok=True`` with a present terminal, and
    refuses whenever **any** ``node_errors`` entry names a node on the required chain
    (the terminal's ancestry, including the recipe's ML node — fidelity-guard parity),
    *even when the terminal parsed* (invariant 2): metrics from a fallback path are not
    silently scored. An off-chain diagnostic node error is accepted (照跑存档不过门).
    Refusal means the trial is revealed as failed with no metrics — never quietly scored.
    """
    checked = ("required_chain_fidelity", "run_outcome_ok", "terminal_present")
    reasons: list[str] = []

    if not outcome.get("ok"):
        reasons.append(f"run outcome ok=False: {outcome.get('reason')}")
    if outcome.get("terminal") is None:
        reasons.append("no main terminal produced (over-gate metrics absent)")

    node_errors = outcome.get("node_errors") or []
    required = {str(x) for x in required_chain_node_ids}
    errored = {
        str((e or {}).get("nid"))
        for e in node_errors
        if isinstance(e, dict)
    }
    hit = sorted(errored & required)
    if hit:
        reasons.append(
            f"required-chain node error(s) {', '.join(hit)}: over-gate metrics come "
            "from a fallback path — refused (fidelity guard)"
        )

    return HonestyGateReport(
        passed=not reasons, reasons=tuple(reasons), checked=tuple(sorted(set(checked)))
    )


# --------------------------------------------------------------------------- #
# L1 — deterministic metrics normalization                                    #
# --------------------------------------------------------------------------- #
def l1_normalize_run_graph_metrics(outcome: Mapping[str, Any]) -> ValidationMetrics:
    """Map the six ``metrics_of_terminal`` keys onto ``ValidationMetrics(source="run_graph")``.

    ``rank_ic`` / ``sharpe`` / ``ann_return`` are finite-coerced (NaN/Inf/absent →
    ``None``, never ``0.0``); ``oos_verdict`` is kept only when it is one of the strict
    contract's admitted verdicts (otherwise ``None``); ``n_dates`` is a non-negative int
    or ``None``; ``factor`` is a non-empty string or ``None``. No field outside the six
    keys is invented, and any extra diagnostic key on the metrics dict is ignored.
    """
    raw = outcome.get("metrics")
    raw = raw if isinstance(raw, Mapping) else {}

    verdict = raw.get("oos_verdict")
    verdict = verdict if verdict in _OOS_VERDICTS else None

    return ValidationMetrics(
        source="run_graph",
        rank_ic=_fnum(raw.get("rank_ic")),
        sharpe=_fnum(raw.get("sharpe")),
        ann_return=_fnum(raw.get("ann_return")),
        oos_verdict=verdict,
        n_dates=_nonneg_int(raw.get("n_dates")),
        factor=_nonempty_str(raw.get("factor")),
    )


# --------------------------------------------------------------------------- #
# L2 — governance delegate                                                     #
# --------------------------------------------------------------------------- #
class _GovernorLike(Protocol):
    def govern(
        self,
        *,
        family: StudyFamily,
        stats: Mapping[str, Any],
        returns: tuple[float, ...] | None,
        perf_matrix: Any,
        candidate: OptimizeCandidate,
        split_spec: SplitSpec,
    ) -> GovernanceReport: ...


def l2_govern(
    *,
    governor: _GovernorLike,
    family: StudyFamily,
    stats: Mapping[str, Any],
    returns: tuple[float, ...] | None,
    perf_matrix: Any,
    candidate: OptimizeCandidate,
    split_spec: SplitSpec,
) -> GovernanceReport:
    """L2 thin pass-through to :meth:`Governor.govern` — **no additional logic**.

    Presents the four layers as one module surface while keeping all governance math in
    the pure governor; the returned report is the *exact object* the governor produced
    (invariant 4).
    """
    return governor.govern(
        family=family,
        stats=stats,
        returns=returns,
        perf_matrix=perf_matrix,
        candidate=candidate,
        split_spec=split_spec,
    )


# --------------------------------------------------------------------------- #
# L3 — attribution feedback                                                    #
# --------------------------------------------------------------------------- #
@runtime_checkable
class AttributionPort(Protocol):
    """The injectable LLM attribution seam.

    Its production binding arrives in Task 10 (research adapter) through the existing
    daemon-thread self-POST critique bridge; **nothing in this module performs HTTP**.
    An implementation returns a :class:`Feedback`; :func:`l3_feedback` validates that
    answer against the contract matrix before trusting it.
    """

    def attribute(
        self,
        *,
        goal: str,
        metrics: ValidationMetrics,
        candidate: OptimizeCandidate,
        constraints: str,
    ) -> Feedback: ...


def _deterministic_feedback(
    candidate: OptimizeCandidate,
    evidence_artifact_ids: Sequence[str],
    *,
    source: str,
    reason_prefix: str = "",
    extra: str = "",
) -> Feedback:
    """Structural narrowing → a :class:`Feedback`.

    When the graph narrows to 1–2 valid worker ids the fault is attributed there;
    otherwise responsibility cannot be narrowed from structure alone and the feedback is
    honestly ``ambiguous=True`` with no targets. Used for the no-port deterministic path
    (``source="deterministic"``) and, badged, for the rule fallback (``source="rule"``).
    """
    evidence = tuple(evidence_artifact_ids or ())
    responsible = _responsible_node_ids(candidate)
    lead = reason_prefix + (f"{extra}; " if extra else "")
    if 1 <= len(responsible) <= 2:
        reason = (
            f"{lead}structural attribution: fault lies within the "
            f"{len(responsible)}-node graph ({', '.join(responsible)})"
        )
        return Feedback(
            target_worker_ids=responsible,
            evidence_artifact_ids=evidence,
            reason=reason,
            confidence=Confidence.LOW,
            ambiguous=False,
            source=source,
        )
    reason = (
        f"{lead}cannot narrow responsibility to ≤2 nodes from structure/metrics alone "
        f"({len(responsible)} candidate node(s))"
    )
    return Feedback(
        target_worker_ids=(),
        evidence_artifact_ids=evidence,
        reason=reason,
        confidence=Confidence.LOW,
        ambiguous=True,
        source=source,
    )


def _validate_port_feedback(
    proposed: Feedback, evidence_artifact_ids: Sequence[str]
) -> list[str]:
    """Contract-matrix problems with a port's answer — empty list means trustworthy."""
    problems: list[str] = []
    targets = tuple(proposed.target_worker_ids or ())
    if proposed.ambiguous:
        if targets:
            problems.append("port marked ambiguous but returned targets")
    elif not (1 <= len(targets) <= 2):
        problems.append(
            f"attribution returned {len(targets)} target worker id(s) (must be 1–2)"
        )
    allowed = set(evidence_artifact_ids or ())
    unsourced = sorted(set(proposed.evidence_artifact_ids or ()) - allowed)
    if unsourced:
        problems.append(f"unsourced evidence id(s): {', '.join(unsourced)}")
    return problems


def l3_feedback(
    *,
    goal: str,
    metrics: ValidationMetrics,
    candidate: OptimizeCandidate,
    evidence_artifact_ids: tuple[str, ...],
    port: AttributionPort | None,
    constraints: str = "",
) -> Feedback:
    """L3 attribution feedback — honest ambiguity over forced attribution.

    Without a ``port``: deterministic pre-narrowing. If the graph cannot be narrowed to
    ≤2 responsible node ids the result is ``Feedback(ambiguous=True,
    source="deterministic")``; a narrowable graph attributes those ids deterministically.

    With a ``port``: the port is asked, then its answer is validated against the contract
    matrix — a port returning >2 targets or unsourced evidence ids is coerced to
    ambiguous with a reason (never trusted blindly), while a valid 1–2 target answer is
    passed through unmodified. A port that raises falls back to a rule attribution with
    the visible ``"(规则兜底·非 LLM)"`` badge in ``reason``. Only validation metrics and
    the explicitly-supplied ``evidence_artifact_ids`` are read — no holdout detail.
    """
    if port is None:
        return _deterministic_feedback(
            candidate, evidence_artifact_ids, source="deterministic"
        )

    try:
        proposed = port.attribute(
            goal=goal, metrics=metrics, candidate=candidate, constraints=constraints
        )
    except Exception as exc:  # noqa: BLE001 — any port failure is a visible rule fallback
        return _deterministic_feedback(
            candidate,
            evidence_artifact_ids,
            source="rule",
            reason_prefix=f"{RULE_FALLBACK_BADGE} ",
            extra=f"attribution port failed ({type(exc).__name__}: {exc})",
        )

    problems = _validate_port_feedback(proposed, evidence_artifact_ids)
    if problems:
        src = proposed.source if proposed.source in ("llm", "deterministic", "rule") else "llm"
        return Feedback(
            target_worker_ids=(),
            evidence_artifact_ids=tuple(evidence_artifact_ids or ()),
            reason="coerced ambiguous (port answer failed contract matrix): "
            + "; ".join(problems),
            confidence=Confidence.LOW,
            ambiguous=True,
            source=src,
        )
    return proposed
