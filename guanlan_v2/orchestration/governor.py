# -*- coding: utf-8 -*-
"""Governor — pure overfitting statistics + study-family identity (Phase 4 Task 4).

This module is **pure**: every function is a deterministic transform over its inputs
with no network / file / store I/O (asserted by an import-surface inspection in
``tests/orchestration/test_governor.py``). It carries three responsibilities:

1. **Extracted overfitting statistics.** :func:`cscv_pbo` and :func:`oos_verdict` are
   the *verbatim* bodies of ``workflow.api._cscv_pbo`` / ``_oos_verdict`` — the
   originals became one-line delegates importing back into here, so every existing
   caller and test keeps exact behavior (brief invariant 1). :func:`dsr` wraps
   ``strategy.compute.cpcv.deflated_sharpe`` with a **function-local** import, keeping
   the orchestration module-level import graph light. No algorithm is re-derived: the
   deflated-Sharpe / PBO / OOS-decay math lives in its existing home and is only
   relocated (PBO/OOS) or wrapped (DSR) here.

   The ``oos_verdict`` return shape is the original 3-tuple ``(verdict, decay_ratio,
   label)`` — delegate equivalence (existing callers unpack all three) outranks the
   brief's abbreviated ``-> str`` signature note; the *verdict* is element ``[0]``.

2. **Study-family identity.** :func:`derive_study_family` derives a stable
   ``family_id`` from the domain-tagged digest of ``StudySpec.identity_projection()``
   (the five identity handles, display text excluded). Every revision of the same
   governed asset therefore derives the **same** ``family_id`` (AMEND-5 red line ①):
   trial/holdout budgets can never be reset by re-proposing a revision as a "new"
   study or by renaming one. :func:`verify_family_attestation` recomputes both digests
   and rejects any forged / renamed / caller-minted family. The v1 attestation is a
   domain-tagged *recomputable* digest, not a cryptographic secret — it defends
   against accidental drift and structural tampering, not a determined forger who also
   recomputes the digest; that honesty is documented rather than hidden.

3. **Trial statistics + the L2 governor.** :func:`effective_n_trials` clusters
   correlated candidates (or, absent evidence, returns the conservative
   ``max(2, raw)``); :func:`complexity_score` is informational only (surfaced, never
   subtracted from real returns — spec §7 "不改写真实收益"); :class:`Governor`
   composes them into a :class:`~guanlan_v2.orchestration.trial.GovernanceReport` that
   returns ``status="unavailable"`` with reasons rather than fabricating a governance
   number (brief invariant 5).

4. **D6 revision-throttle.** :data:`REVISION_THROTTLE_MIN_MATURED` freezes the AMEND-5
   overfitting red line ③ (spec:289) once at the governor, and
   :func:`revision_throttle_check` is the pure admission pre-check on a revision
   proposal (a revision = a new study in the same family): the previous
   ``definition_version`` must carry at least N matured observations at the family's own
   frequency before a fresh revision is admitted. An **unknown frequency is rejected**
   (never a silent default-allow) — the same honesty construction as L2
   ``status="unavailable"``. The matured observation count is supplied by the caller
   under PIT matured semantics (data source = the Phase 5 matured-case grader); this
   module ships the rule only and never binds the data. It is deliberately **not** wired
   into :meth:`Governor.should_stop` or ``run_optimize``.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.trial import (
    GovernanceReport,
    OptimizeCandidate,
    SplitSpec,
    StudyFamily,
    StudySpec,
)

__all__ = [
    "GOVERNOR_VERSION",
    "STUDY_FAMILY_DOMAIN",
    "FAMILY_ATTESTATION_DOMAIN",
    "REVISION_THROTTLE_MIN_MATURED",
    "FamilyAttestationError",
    "cscv_pbo",
    "oos_verdict",
    "dsr",
    "derive_study_family",
    "verify_family_attestation",
    "effective_n_trials",
    "complexity_score",
    "revision_throttle_check",
    "Governor",
]

#: Frozen governor identity version — participates in the family attestation digest.
GOVERNOR_VERSION = "governor-v1"
#: Domain tag separating the study-family identity space (mirrors
#: ``trial.STUDY_FAMILY_DOMAIN``; :meth:`StudySpec.identity_projection` embeds it).
STUDY_FAMILY_DOMAIN = "study-family-v1"
#: Domain tag for the governor's family attestation digest.
FAMILY_ATTESTATION_DOMAIN = "study-family-attestation-v1"
#: D6 revision-throttle minimum matured-observation counts, keyed by the family's own
#: frequency (AMEND-5 red line ③, spec:289). The unit is that frequency's matured
#: observation span — ``monthly`` = periods, ``daily`` = trading days. Frozen in the
#: same batch as :data:`GOVERNOR_VERSION`; any frequency absent from this mapping is a
#: hard reject in :func:`revision_throttle_check` (never a silent default-allow).
REVISION_THROTTLE_MIN_MATURED = {"monthly": 3, "daily": 20}


class FamilyAttestationError(ValueError):
    """Raised when a :class:`StudyFamily`'s digests do not recompute.

    A forged, renamed or caller-minted family (one whose ``family_id`` /
    ``identity_digest`` / ``governor_attestation`` disagree with a fresh derivation
    from its own handles) is rejected deterministically.
    """


def _num(v: Any) -> float | None:
    """float 化并把 NaN/Inf/无法解析 → None(verbatim from ``workflow.api._num``)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# --------------------------------------------------------------------------- #
# Extracted overfitting statistics (verbatim bodies)                          #
# --------------------------------------------------------------------------- #
def oos_verdict(is_v, oos_v, n_oos, min_n: int = 6):
    """IS/OOS 同指标 → 过拟合判定。返回 ``(verdict, decay_ratio, label)``.

    Relocated verbatim from ``workflow.api._oos_verdict`` (whose body is now a
    delegate into here). decay_ratio = OOS/IS(仅 IS>0 有意义);门槛 0.6/0.2:≥0.6
    稳健、≥0.2 衰减、<0.2(含反号)疑似过拟合。OOS 期数 < min_n → insufficient(不下
    结论);IS 无正信号 → na(无从判)。
    """
    if n_oos is None or n_oos < min_n:
        return "insufficient", None, "样本外期数不足,暂不下结论"
    if is_v is None or is_v != is_v or oos_v is None or oos_v != oos_v:
        return "na", None, "指标缺失"
    if is_v <= 0:
        return "na", None, "样本内本身无正信号(无从判过拟合)"
    ratio = float(oos_v) / float(is_v)
    if ratio >= 0.6:
        return "robust", ratio, "稳健 · 样本外保持"
    if ratio >= 0.2:
        return "degraded", ratio, "衰减 · 样本外走弱"
    return "overfit", ratio, "疑似过拟合 · 样本外塌缩/反号"


def cscv_pbo(perf_matrix, n_blocks: int = 8):
    """CSCV → PBO(回测过拟合概率,López de Prado / 华泰对抗"挑出来的虚高回测")。

    Relocated verbatim from ``workflow.api._cscv_pbo`` (whose body is now a delegate
    into here). ``perf_matrix``:候选 × 时间块 的表现矩阵,形状 (N候选, S块)。把 S 块
    枚举所有 C(S, S/2) 互补划分:每划分用 IS 选表现最优候选 → 看它在 OOS 的相对排名
    ω∈(0,1) → ω<0.5 记一次过拟合。PBO = 这类划分占比。需 N≥2 候选 & S≥4 偶数块。
    返回 {enabled, pbo, n_combos, ...}。
    """
    import numpy as np
    from itertools import combinations
    M = np.asarray(perf_matrix, dtype="float64")
    if M.ndim != 2 or M.shape[0] < 2 or M.shape[1] < 4:
        return {"enabled": False, "reason": f"PBO 需 ≥2 候选 & ≥4 时间块(得 {tuple(M.shape) if M.ndim == 2 else M.shape})"}
    N, S = M.shape
    if S % 2 == 1:   # 取偶数块
        S -= 1
        M = M[:, :S]
    M = np.nan_to_num(M, nan=0.0)
    half = S // 2
    n_below = 0
    n_tot = 0
    for is_idx in combinations(range(S), half):
        oos_idx = [b for b in range(S) if b not in is_idx]
        is_perf = M[:, list(is_idx)].mean(axis=1)
        oos_perf = M[:, oos_idx].mean(axis=1)
        best = int(np.argmax(is_perf))                       # IS 最优候选
        order = oos_perf.argsort()                            # OOS 升序
        rank = int(np.where(order == best)[0][0]) + 1         # 1=最差 .. N=最优
        omega = rank / (N + 1.0)
        if omega < 0.5:
            n_below += 1
        n_tot += 1
    pbo = (n_below / n_tot) if n_tot else float("nan")
    return {"enabled": True, "pbo": _num(pbo), "n_combos": int(n_tot),
            "n_candidates": int(N), "n_blocks": int(S),
            "note": "PBO = IS 最优候选在 OOS 落入后半的概率(CSCV);>0.5 提示选优过拟合,<0.3 较稳健。"}


def dsr(
    returns: Sequence[float], n_trials: int, sharpes_std: float | None = None
) -> float | None:
    """Deflated Sharpe wrapper — returns P(true Sharpe > noise benchmark) ∈ [0, 1].

    Imports ``deflated_sharpe`` **function-locally** from
    ``guanlan_v2.strategy.compute.cpcv`` so the orchestration package's module-level
    import graph stays light (no pandas/scipy pulled in at import time). The DSR math
    is not copied — it lives in its existing home; ``deflated_sharpe`` returns ``None``
    for fewer than 10 finite samples or zero variance (its own floor), which this
    wrapper preserves.
    """
    from guanlan_v2.strategy.compute.cpcv import deflated_sharpe

    return deflated_sharpe(list(returns), int(n_trials), sharpes_std=sharpes_std)


# --------------------------------------------------------------------------- #
# Study-family identity                                                       #
# --------------------------------------------------------------------------- #
def _identity_projection_of(family: StudyFamily) -> dict[str, Any]:
    """Reconstruct the family-identity projection from a family's own handles.

    Mirrors :meth:`StudySpec.identity_projection` exactly so a fresh
    ``content_digest`` over it must equal the family's stored ``identity_digest``.
    """
    return {
        "domain": STUDY_FAMILY_DOMAIN,
        "objective_digest": family.objective_digest,
        "label_digest": family.label_digest,
        "universe_digest": family.universe_digest,
        "frequency": family.frequency,
        "split_policy_digest": family.split_policy_digest,
    }


def _attestation_digest(identity_digest: str, governor_version: str) -> str:
    return content_digest(
        {
            "domain": FAMILY_ATTESTATION_DOMAIN,
            "identity_digest": identity_digest,
            "governor_version": governor_version,
        }
    )


def derive_study_family(
    study: StudySpec, *, governor_version: str = GOVERNOR_VERSION
) -> StudyFamily:
    """Derive the stable :class:`StudyFamily` of a :class:`StudySpec`.

    ``identity_digest = content_digest(study.identity_projection())``;
    ``family_id = "fam." + identity_digest[:16]``; ``governor_attestation`` is the
    domain-tagged digest binding the identity to this governor version.

    Revision-family identity convention (AMEND-5 red line ①): because
    :meth:`StudySpec.identity_projection` excludes the human ``objective`` /
    ``label_definition`` text, every revision of one governed asset (a
    ``factor_id`` / ``pattern_id`` lineage) derives the **same** ``family_id`` — the
    revision expression rides the :class:`OptimizeCandidate`, never study identity.
    Trial/holdout budgets therefore cannot be reset by re-proposing a revision as a
    "new" study or by renaming one; a genuine identity change routes through
    ``parent_family_id`` + ``change_reason`` (carried through unchanged here).
    """
    identity_digest = content_digest(study.identity_projection())
    family_id = "fam." + identity_digest[:16]
    return StudyFamily(
        family_id=family_id,
        identity_digest=identity_digest,
        objective_digest=study.objective_digest,
        label_digest=study.label_digest,
        universe_digest=study.universe_digest,
        frequency=study.frequency,
        split_policy_digest=study.split_policy_digest,
        parent_family_id=study.parent_family_id,
        change_reason=study.change_reason,
        governor_attestation=_attestation_digest(identity_digest, governor_version),
    )


def verify_family_attestation(
    family: StudyFamily, *, governor_version: str = GOVERNOR_VERSION
) -> None:
    """Recompute a family's digests; raise :class:`FamilyAttestationError` on mismatch.

    Independently re-derives ``identity_digest`` (from the family's own five handles),
    the ``family_id`` binding and the ``governor_attestation`` — so a forged, renamed
    or caller-minted family (including one built via ``model_construct`` that bypassed
    the :class:`StudyFamily` constructor) is rejected deterministically. v1
    attestations are domain-tagged *recomputable* digests, not cryptographic secrets:
    this catches drift and structural tampering, not a forger who recomputes the digest
    end-to-end — documented honestly rather than overclaimed.
    """
    identity = content_digest(_identity_projection_of(family))
    if identity != family.identity_digest:
        raise FamilyAttestationError(
            "identity_digest does not recompute from the family handles: "
            f"stored {family.identity_digest!r}, computed {identity!r}"
        )
    expected_id = "fam." + identity[:16]
    if family.family_id != expected_id:
        raise FamilyAttestationError(
            f"family_id {family.family_id!r} does not bind identity_digest "
            f"(expected {expected_id!r})"
        )
    attestation = _attestation_digest(identity, governor_version)
    if attestation != family.governor_attestation:
        raise FamilyAttestationError(
            "governor_attestation does not recompute: "
            f"stored {family.governor_attestation!r}, computed {attestation!r}"
        )


# --------------------------------------------------------------------------- #
# Trial statistics                                                            #
# --------------------------------------------------------------------------- #
def _pearson(a: "np.ndarray", b: "np.ndarray") -> float | None:
    """Pearson correlation of two paired vectors, or ``None`` when undefined.

    Vectors are truncated to their common length; fewer than 2 paired points or a
    zero-variance vector yields ``None`` (treated as "not correlated"), never a
    spurious cluster edge.
    """
    m = min(len(a), len(b))
    if m < 2:
        return None
    a = a[:m]
    b = b[:m]
    if a.std() == 0.0 or b.std() == 0.0:
        return None
    c = float(np.corrcoef(a, b)[0, 1])
    return c if math.isfinite(c) else None


def effective_n_trials(
    raw_trial_count: int,
    distinct_candidate_count: int,
    block_rank_ic: Mapping[str, Sequence[float]] | None = None,
    *,
    corr_threshold: float = 0.9,
) -> int:
    """Effective number of independent trials for DSR deflation.

    When per-candidate rank-IC block vectors are supplied, candidates whose pairwise
    Pearson correlation ≥ ``corr_threshold`` are unioned into clusters and the cluster
    count is returned (floored at 2, clamped to ``max(2, raw_trial_count)``). When no
    correlation evidence is supplied, returns ``max(2, raw_trial_count)`` — the most
    conservative choice (more trials ⇒ more deflation), and the caller badges
    ``"correlation_unadjusted"``. The result always satisfies
    ``2 <= result <= max(2, raw_trial_count)``.
    """
    upper = max(2, int(raw_trial_count))
    if not block_rank_ic:
        return upper

    keys = list(block_rank_ic.keys())
    n = len(keys)
    if n == 0:
        return upper
    vecs = [np.asarray(block_rank_ic[k], dtype="float64") for k in keys]

    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(n):
        for j in range(i + 1, n):
            r = _pearson(vecs[i], vecs[j])
            if r is not None and r >= corr_threshold:
                _union(i, j)

    clusters = len({_find(i) for i in range(n)})
    return max(2, min(clusters, upper))


def complexity_score(candidate: OptimizeCandidate) -> float:
    """Deterministic ``n_nodes + n_edges + 0.1 * total_param_count`` over the graph.

    Counts nodes, edges and the total number of node-``params`` keys in the candidate's
    canonical graph. **Informational only** in v1 — surfaced in
    ``GovernanceReport.complexity_score`` and never subtracted from real returns
    (spec §7 "不改写真实收益").
    """
    graph = candidate.graph if isinstance(candidate.graph, dict) else {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    n_nodes = len(nodes)
    n_edges = len(edges)
    total_params = 0
    for node in nodes:
        if isinstance(node, dict):
            params = node.get("params") or {}
            if isinstance(params, dict):
                total_params += len(params)
    return float(n_nodes + n_edges + 0.1 * total_params)


# --------------------------------------------------------------------------- #
# D6 revision-throttle governance primitive                                   #
# --------------------------------------------------------------------------- #
def revision_throttle_check(
    *, frequency: str, matured_observation_count: int
) -> tuple[bool, str]:
    """D6 admission pre-check on a revision proposal — ``(allowed, reason)``.

    A revision is a **new study in the same family**; before one is admitted the
    previous ``definition_version`` must carry at least
    ``REVISION_THROTTLE_MIN_MATURED[frequency]`` matured observations at the family's own
    frequency (AMEND-5 red line ③, spec:289). ``matured_observation_count`` is supplied
    by the caller under PIT matured semantics (data source = the Phase 5 matured-case
    grader); this function ships the rule only and never binds the data.

    An **unknown ``frequency``** (any key absent from
    :data:`REVISION_THROTTLE_MIN_MATURED`, e.g. ``"weekly"`` or ``""``) is **rejected**
    with a named reason — never a silent default-allow, the same honesty construction as
    the L2 governor's ``status="unavailable"``. Every returned path (allowed or blocked)
    carries a non-empty ``reason``; every blocked path names the threshold ``N``.

    Deliberately not wired into :meth:`Governor.should_stop` or ``run_optimize``: the
    throttle is a proposal-admission check, not an optimize-round predicate.
    """
    threshold = REVISION_THROTTLE_MIN_MATURED.get(frequency)
    if threshold is None:
        known = ", ".join(sorted(REVISION_THROTTLE_MIN_MATURED))
        return (
            False,
            f"revision throttle: unknown frequency {frequency!r}; no default-allow "
            f"(governed frequencies: {known})",
        )
    count = int(matured_observation_count)
    if count < threshold:
        return (
            False,
            f"revision throttle: prior definition_version has {count} matured "
            f"{frequency} observations (< {threshold} required)",
        )
    return (
        True,
        f"revision throttle: {count} matured {frequency} observations "
        f"(>= {threshold} required)",
    )


# --------------------------------------------------------------------------- #
# L2 governor                                                                 #
# --------------------------------------------------------------------------- #
class Governor:
    """The L2 overfitting governor — pure over its inputs, honest over scores.

    Composes the extracted statistics into a
    :class:`~guanlan_v2.orchestration.trial.GovernanceReport`. It computes a deflated
    Sharpe only when ``returns`` has ≥10 samples (mirroring ``deflated_sharpe``'s own
    floor) and a PBO only when the performance matrix meets ``cscv_pbo``'s
    preconditions; whenever no governance number can be computed it returns
    ``status="unavailable"`` with reasons rather than a fabricated score.
    """

    def __init__(
        self,
        *,
        trial_budget: int,
        peek_budget: int,
        governor_version: str = GOVERNOR_VERSION,
    ) -> None:
        if trial_budget <= 0 or peek_budget <= 0:
            raise ValueError("trial_budget and peek_budget must be positive")
        self.trial_budget = int(trial_budget)
        self.peek_budget = int(peek_budget)
        self.governor_version = governor_version

    def resolve_family(self, study: StudySpec) -> StudyFamily:
        """Derive the study's family (delegates to :func:`derive_study_family`)."""
        return derive_study_family(study, governor_version=self.governor_version)

    def govern(
        self,
        *,
        family: StudyFamily,
        stats: Mapping[str, Any],
        returns: tuple[float, ...] | None,
        perf_matrix: "np.ndarray | None",
        candidate: OptimizeCandidate,
        split_spec: SplitSpec,
    ) -> GovernanceReport:
        """Produce the governance report — pure over inputs, no I/O.

        ``stats`` is the ledger's ``effective_trial_stats`` dict: ``raw_trial_count``
        is the audit upper bound from the global TrialLedger and ``reveal_count`` the
        holdout peeks consumed. ``status="ok"`` requires at least one of DSR / PBO
        present; otherwise ``status="unavailable"`` with a non-empty ``reasons``.
        """
        raw = int(stats["raw_trial_count"])
        reveal_count = int(stats.get("reveal_count", 0))
        distinct = int(stats.get("distinct_candidate_count", raw))
        block_rank_ic = stats.get("block_rank_ic")
        reasons: list[str] = []

        # --- effective trials (surfaced only when it respects the raw bound) ---- #
        eff = effective_n_trials(raw, distinct, block_rank_ic)
        report_eff = eff if eff <= raw else None
        if block_rank_ic is None:
            reasons.append("correlation_unadjusted")

        # --- deflated Sharpe (≥10 samples, mirroring deflated_sharpe's floor) --- #
        deflated: float | None = None
        finite_returns = (
            [x for x in returns if isinstance(x, (int, float)) and x == x]
            if returns is not None
            else []
        )
        if len(finite_returns) >= 10:
            deflated = dsr(finite_returns, n_trials=eff)
            if deflated is None:
                reasons.append("DSR unavailable: zero-variance return series")
        else:
            reasons.append(
                f"DSR unavailable: {len(finite_returns)} finite return samples (< 10)"
            )

        # --- PBO (only when perf_matrix meets cscv_pbo preconditions) ----------- #
        pbo_val: float | None = None
        pbo_enabled = False
        pbo_reason: str | None = None
        if perf_matrix is not None:
            res = cscv_pbo(perf_matrix)
            if res.get("enabled"):
                pbo_enabled = True
                pbo_val = res.get("pbo")
                if pbo_val is None:  # degenerate (empty split enumeration)
                    pbo_enabled = False
                    pbo_reason = "PBO precondition not met: no valid CSCV combinations"
                    reasons.append(pbo_reason)
            else:
                pbo_reason = res.get("reason") or "PBO precondition not met"
                reasons.append(f"PBO unavailable: {pbo_reason}")
        else:
            pbo_reason = "no performance matrix supplied"
            reasons.append(f"PBO unavailable: {pbo_reason}")

        # --- complexity (informational only) ----------------------------------- #
        complexity = complexity_score(candidate)

        # --- budgets ------------------------------------------------------------ #
        trial_remaining = max(0, self.trial_budget - raw)
        peek_remaining = max(0, self.peek_budget - reveal_count)

        has_number = deflated is not None or pbo_val is not None
        status = "ok" if has_number else "unavailable"

        return GovernanceReport(
            status=status,
            family_id=family.family_id,
            raw_trial_count=raw,
            effective_n_trials=report_eff,
            deflated_sharpe=deflated,
            pbo=pbo_val,
            pbo_enabled=pbo_enabled,
            pbo_reason=pbo_reason,
            complexity_score=complexity,
            trial_budget_remaining=trial_remaining,
            peek_budget_remaining=peek_remaining,
            reasons=tuple(reasons),
        )

    def should_stop(self, report: GovernanceReport) -> str | None:
        """Budget stop signal — ``"trial_budget_exhausted"`` /
        ``"peek_budget_exhausted"`` when the respective remaining hits 0, else ``None``.
        """
        if report.trial_budget_remaining == 0:
            return "trial_budget_exhausted"
        if report.peek_budget_remaining == 0:
            return "peek_budget_exhausted"
        return None
