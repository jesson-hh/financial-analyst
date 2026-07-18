# -*- coding: utf-8 -*-
"""Trial / study / candidate strict contracts (Phase 1 Task 11 landing, part 1).

This module freezes the *identity and evaluation-fact* value layer of the Phase 4
Evaluator-Optimizer: the study identity (:class:`StudySpec` / :class:`StudyFamily`),
the split policy (:class:`SplitSpec`), the optimizer candidate
(:class:`OptimizeCandidate`) and the four evaluation-fact records the four-layer
evaluator produces (:class:`HonestyGateReport` L0, :class:`ValidationMetrics` L1,
:class:`GovernanceReport` L2, :class:`Feedback` L3), plus the append-only
:class:`OptimizeRound` archive and its nested :class:`GateResult`. Every model is a
strict, frozen, versioned :class:`~guanlan_v2.orchestration.digest.DigestModel`;
**no service code** (no governor derivation, no ledger, no state machine, no sealed
store) lives here — later Phase 4 modules construct behavior *from* these frozen
values.

Reviewed identity partitions
----------------------------
* **Family identity is display-free.** :meth:`StudySpec.identity_projection`
  emits only the domain tag plus the five registry-resolved handles
  (``objective_digest`` / ``label_digest`` / ``universe_digest`` / ``frequency`` /
  ``split_policy_digest``); the human ``objective`` / ``label_definition`` text is
  deliberately excluded, so free-text edits can never perturb which study family a
  spec belongs to. Successive revisions of one governed asset (a
  ``factor_id`` / ``pattern_id`` lineage) therefore construct the *same* family —
  the revision expression rides the :class:`OptimizeCandidate`, never study
  identity; a genuine identity change routes through ``parent_family_id`` +
  ``change_reason`` (AMEND-5 red line ①).
* **Candidate identity is position-free.** :meth:`OptimizeCandidate.build`
  canonicalizes the workflow graph — nodes reduced to ``{"id", "type", "params"}``
  sorted by id, edges sorted, layout ``x`` / ``y`` and any unknown node keys
  dropped — before sealing the domain-tagged ``candidate_hash``, preserving the
  "positions don't count" property of ``workflow.executor.graph_signature`` under
  the Phase 1 sha256 digest. ``display_name`` is audit-only and never enters the
  hash or the semantic digest.
* **Audit wall-clock.** :class:`OptimizeRound` excludes ``created_at`` from the
  semantic digest — re-archiving byte-identical round content a minute later leaves
  the semantic identity stable.

Honesty red lines (spec §7)
---------------------------
:class:`ValidationMetrics` uses ``None`` for honestly-absent values (never
zero-filled by any builder); :class:`GovernanceReport` returns
``status="unavailable"`` with reasons rather than fabricating governance numbers;
:class:`Feedback` returns ``ambiguous=True`` (with no targets) rather than forcing
an attribution.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import field_validator, model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import Confidence, ExperimentStatus
from guanlan_v2.orchestration.refs import LogicalId, TypedPayloadRef

__all__ = [
    "STUDY_FAMILY_DOMAIN",
    "CANDIDATE_HASH_DOMAIN",
    "StudySpec",
    "StudyFamily",
    "SplitSpec",
    "OptimizeCandidate",
    "ValidationMetrics",
    "GateResult",
    "Feedback",
    "GovernanceReport",
    "HonestyGateReport",
    "OptimizeRound",
    "HoldoutWindow",
    "TrialRecord",
    "OptimizeRunState",
    "OptimizeResult",
    "HoldoutReceipt",
    "HoldoutLease",
    "SealedEvaluationRecord",
    "SealedCapability",
    # -- Task 9 cumulative Phase-4 registry / catalog chain ----------------- #
    "PHASE4_CATALOG_VERSION",
    "JOINT_GATE_MATERIAL_ID",
    "JOINT_GATE_METRIC_KIND",
    "PHASE4_PUBLIC_MODELS",
    "PHASE4_INTERNAL_MODELS",
    "PHASE4_INTERNAL_SURFACE",
    "Phase4RegistryError",
    "build_phase4_registry",
    "joint_gate_material",
    "build_phase4_catalog_snapshot",
    "phase4_catalog_snapshot",
    "PHASE4_BASE_REGISTRY_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE4_REGISTRY_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE4_BASE_CATALOG_DIGEST",  # noqa: F822 — lazy via module __getattr__
    "PHASE4_CATALOG_DIGEST",  # noqa: F822 — lazy via module __getattr__
]

#: domain tag that separates the study-family identity space from any other
#: digest namespace; :meth:`StudySpec.identity_projection` and (Task 4)
#: ``governor.derive_study_family`` derive family identity from it.
STUDY_FAMILY_DOMAIN = "study-family-v1"
#: domain tag for the optimizer-candidate content hash.
CANDIDATE_HASH_DOMAIN = "optimize-candidate-v1"

#: Well-formed but deliberately-wrong digest used by :meth:`OptimizeCandidate.build`
#: when field values are malformed: it forces the validating constructor to surface
#: the real ``ValidationError`` and can never seal a valid record.
_DIGEST_PLACEHOLDER = "0" * 64


def _ensure_json_shaped(value: Any) -> Any:
    """Reject any non-JSON-native value inside a strict graph/params config.

    Mirrors :func:`guanlan_v2.orchestration.spec._ensure_json_shaped` (the exact
    ``PlanNode.params`` acceptance rule) so an :class:`OptimizeCandidate` graph and
    params can never smuggle a callable, path or non-string dict key past the
    canonical digest.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("config float must be finite (NaN/Inf rejected)")
        return value
    if isinstance(value, (list, tuple)):
        return [_ensure_json_shaped(v) for v in value]
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise ValueError("config dict keys must be strings")
        return {k: _ensure_json_shaped(v) for k, v in value.items()}
    raise ValueError(
        f"config must be strict JSON-shaped; got {type(value).__name__}"
    )


def _canonical_graph(graph: Any) -> dict[str, Any]:
    """Canonicalize a workflow graph for content hashing.

    Reduces each node to ``{"id", "type", "params"}`` sorted by id (layout ``x`` /
    ``y`` and any unknown node keys dropped) and sorts edges by their canonical
    JSON, exactly matching ``workflow.executor.graph_signature``'s
    positions-don't-count property. Non-dict nodes/edges are ignored the same way.
    """
    if not isinstance(graph, dict):
        raise ValueError("graph must be a dict")
    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    edges = raw_edges if isinstance(raw_edges, list) else []
    cn = sorted(
        (
            {
                "id": str(n.get("id")),
                "type": str(n.get("type")),
                "params": (n.get("params") or {}),
            }
            for n in nodes
            if isinstance(n, dict)
        ),
        key=lambda d: d["id"],
    )
    ce = sorted(
        (e for e in edges if isinstance(e, dict)),
        key=lambda e: json.dumps(e, sort_keys=True, ensure_ascii=False),
    )
    return {"nodes": cn, "edges": ce}


def _candidate_hash(
    *, candidate_kind: str, canonical_graph: dict[str, Any], params: Any
) -> DigestHex:
    """Domain-tagged content hash of a canonicalized candidate."""
    return content_digest(
        {
            "domain": CANDIDATE_HASH_DOMAIN,
            "candidate_kind": candidate_kind,
            "graph": canonical_graph,
            "params": params,
        }
    )


# --------------------------------------------------------------------------- #
# Study identity                                                              #
# --------------------------------------------------------------------------- #
class StudySpec(DigestModel):
    """The identity of a study a family is derived from.

    ``objective`` / ``label_definition`` are human display text; the five
    digest/frequency fields (``objective_digest`` / ``label_digest`` /
    ``universe_digest`` / ``frequency`` / ``split_policy_digest``) are the
    registry-resolved identity handles the study family is keyed on. Lineage is
    all-or-none: ``parent_family_id`` and ``change_reason`` are set together (a
    genuine identity change) or both absent. :meth:`identity_projection`
    deliberately excludes the display text so free-text edits never move family
    identity.
    """

    schema_version: Literal["1"] = "1"
    objective: NonEmptyStr
    objective_digest: DigestHex
    label_definition: NonEmptyStr
    label_digest: DigestHex
    universe_digest: DigestHex
    frequency: NonEmptyStr
    split_policy_digest: DigestHex
    parent_family_id: LogicalId | None = None
    change_reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _lineage_pair(self) -> "StudySpec":
        if (self.parent_family_id is None) != (self.change_reason is None):
            raise ValueError(
                "parent_family_id and change_reason are all-set-or-all-none"
            )
        return self

    def identity_projection(self) -> dict[str, Any]:
        """The domain-tagged family-identity handles — display text excluded."""
        return {
            "domain": STUDY_FAMILY_DOMAIN,
            "objective_digest": self.objective_digest,
            "label_digest": self.label_digest,
            "universe_digest": self.universe_digest,
            "frequency": self.frequency,
            "split_policy_digest": self.split_policy_digest,
        }


class StudyFamily(DigestModel):
    """A governor-derived study family: a stable identity over its handles.

    ``family_id`` must equal ``"fam." + identity_digest[:16]`` (satisfying the
    :data:`~guanlan_v2.orchestration.refs.LogicalId` grammar), so construction
    outside ``governor.derive_study_family`` with a mismatched id fails.
    ``governor_attestation`` binds the deriving governor's evidence.
    """

    schema_version: Literal["1"] = "1"
    family_id: LogicalId
    identity_digest: DigestHex
    objective_digest: DigestHex
    label_digest: DigestHex
    universe_digest: DigestHex
    frequency: NonEmptyStr
    split_policy_digest: DigestHex
    parent_family_id: LogicalId | None = None
    change_reason: NonEmptyStr | None = None
    governor_attestation: DigestHex

    @model_validator(mode="after")
    def _id_binding(self) -> "StudyFamily":
        expected = "fam." + self.identity_digest[:16]
        if self.family_id != expected:
            raise ValueError(
                f"family_id must be {expected!r} (fam. + identity_digest[:16]); "
                f"got {self.family_id!r}"
            )
        return self


# --------------------------------------------------------------------------- #
# Split policy                                                                #
# --------------------------------------------------------------------------- #
class SplitSpec(DigestModel):
    """A cross-validation split policy for a study.

    Scheme matrix: ``cpcv`` requires ``n_groups`` and ``k`` (with ``k <
    n_groups``) and forbids ``oos_frac``; ``oos_fraction`` requires ``oos_frac``
    (open interval ``0 < oos_frac < 1``) and forbids ``n_groups`` / ``k``;
    ``walk_forward`` forbids ``oos_frac``. :attr:`effective_purge` encodes the
    ``strict_validate`` widening rule (``strategy/compute/cpcv.py:293``:
    ``max(purge, label_horizon + 1)``) so a split can never under-purge overlapping
    labels.
    """

    schema_version: Literal["1"] = "1"
    scheme: Literal["cpcv", "walk_forward", "oos_fraction"]
    n_groups: PositiveInt | None = None
    k: PositiveInt | None = None
    purge: NonNegativeInt = 0
    embargo: NonNegativeInt = 0
    label_horizon: PositiveInt
    oos_frac: FiniteFloat | None = None
    nested: bool = False

    @model_validator(mode="after")
    def _scheme_matrix(self) -> "SplitSpec":
        if self.oos_frac is not None and not (0.0 < self.oos_frac < 1.0):
            raise ValueError("oos_frac must satisfy 0 < oos_frac < 1")
        if self.scheme == "cpcv":
            if self.n_groups is None or self.k is None:
                raise ValueError("scheme='cpcv' requires n_groups and k")
            if self.k >= self.n_groups:
                raise ValueError("scheme='cpcv' requires k < n_groups")
            if self.oos_frac is not None:
                raise ValueError("scheme='cpcv' forbids oos_frac")
        elif self.scheme == "oos_fraction":
            if self.oos_frac is None:
                raise ValueError("scheme='oos_fraction' requires oos_frac")
            if self.n_groups is not None or self.k is not None:
                raise ValueError("scheme='oos_fraction' forbids n_groups/k")
        else:  # walk_forward
            if self.oos_frac is not None:
                raise ValueError("scheme='walk_forward' forbids oos_frac")
        return self

    @property
    def effective_purge(self) -> int:
        """The widened purge: ``max(purge, label_horizon + 1)``."""
        return max(self.purge, self.label_horizon + 1)


# --------------------------------------------------------------------------- #
# Optimizer candidate                                                         #
# --------------------------------------------------------------------------- #
class OptimizeCandidate(DigestModel):
    """One optimizer candidate: a workflow graph or a pattern definition.

    ``candidate_kind`` admits ``pattern_definition`` candidates into v1 alongside
    ``workflow_graph`` (AMEND-6: pattern-replay before the Task 9 golden freeze, no
    ``@2`` bump). ``graph`` and ``params`` are strict JSON-shaped config (the exact
    ``PlanNode.params`` acceptance rule). ``candidate_hash`` self-seals the
    canonicalized identity via :meth:`build`; ``display_name`` is audit-only.
    """

    schema_version: Literal["1"] = "1"
    candidate_kind: Literal["workflow_graph", "pattern_definition"]
    graph: dict[str, Any]
    params: dict[str, Any]
    parent_trial_id: NonEmptyStr | None = None
    display_name: NonEmptyStr | None = None
    candidate_hash: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"display_name"})
    SELF_DIGEST_FIELDS: ClassVar[frozenset[str]] = frozenset({"candidate_hash"})

    @field_validator("graph", "params")
    @classmethod
    def _json_shaped(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _ensure_json_shaped(v)

    @model_validator(mode="after")
    def _verify(self) -> "OptimizeCandidate":
        expected = _candidate_hash(
            candidate_kind=self.candidate_kind,
            canonical_graph=self.graph,
            params=self.params,
        )
        if self.candidate_hash != expected:
            raise ValueError("declared candidate_hash does not match canonical digest")
        return self

    @classmethod
    def build(cls, **fields: Any) -> "OptimizeCandidate":
        """Seal a candidate: canonicalize ``graph`` (positions/order/unknown keys
        dropped) and compute the domain-tagged ``candidate_hash`` before
        construction. A malformed field falls through to the validating constructor
        with a placeholder digest so the real ``ValidationError`` surfaces.
        """
        try:
            canon = _canonical_graph(fields["graph"])
            digest = _candidate_hash(
                candidate_kind=fields["candidate_kind"],
                canonical_graph=canon,
                params=fields.get("params"),
            )
        except (ValueError, TypeError, AttributeError, KeyError):
            return cls(**fields, candidate_hash=_DIGEST_PLACEHOLDER)
        sealed = dict(fields)
        sealed["graph"] = canon
        return cls(**sealed, candidate_hash=digest)


# --------------------------------------------------------------------------- #
# Evaluation facts                                                            #
# --------------------------------------------------------------------------- #
class ValidationMetrics(DigestModel):
    """L1 deterministic validation metrics — honest absence, never zero-filled.

    Every metric is ``None`` when honestly unavailable; ``coverage`` is a fraction
    (``0 <= coverage <= 1`` when present); ``profit_loss_ratio`` / ``n_occurrences``
    carry pattern-replay evidence (hit / win rates ride the existing fields).
    ``source`` names the deterministic evaluator that produced them.
    """

    schema_version: Literal["1"] = "1"
    rank_ic: FiniteFloat | None = None
    sharpe: FiniteFloat | None = None
    ann_return: FiniteFloat | None = None
    max_drawdown: FiniteFloat | None = None
    turnover: FiniteFloat | None = None
    win_rate: FiniteFloat | None = None
    tail_ratio: FiniteFloat | None = None
    coverage: FiniteFloat | None = None
    oos_verdict: (
        Literal["robust", "degraded", "overfit", "insufficient", "na"] | None
    ) = None
    n_dates: NonNegativeInt | None = None
    factor: NonEmptyStr | None = None
    profit_loss_ratio: FiniteFloat | None = None
    n_occurrences: NonNegativeInt | None = None
    source: Literal["run_graph", "shadow_backend", "case_grader", "pattern_replay"]

    @model_validator(mode="after")
    def _coverage_range(self) -> "ValidationMetrics":
        if self.coverage is not None and not (0.0 <= self.coverage <= 1.0):
            raise ValueError("coverage must be within [0, 1] when present")
        return self


class GateResult(DigestModel):
    """The Phase 4 evaluation-gate outcome (nested in :class:`OptimizeRound`).

    Deliberately distinct from — and never imported into — the Phase 1 Plan-gate
    ``spec.GateResult``. Field names extend the existing research loop gate's
    return keys (``loop.py``: ``passed`` / ``min_rank_ic`` / ``oos_required`` /
    ``sharpe_required``). ``observed_*`` are ``None`` when the gate ran without an
    observation for that dimension.
    """

    schema_version: Literal["1"] = "1"
    status: Literal["passed", "failed", "unavailable"]
    min_rank_ic: FiniteFloat
    oos_required: Literal["robust"] = "robust"
    sharpe_required: bool = True
    observed_rank_ic: FiniteFloat | None = None
    observed_sharpe: FiniteFloat | None = None
    observed_oos_verdict: NonEmptyStr | None = None
    reason: NonEmptyStr


class Feedback(DigestModel):
    """L3 attribution feedback — honest ambiguity over forced attribution.

    Attribution narrows to 1–2 workers or honestly returns ``ambiguous=True`` with
    no targets (spec §7 L3). Matrix: ``ambiguous=True`` ⇒ ``target_worker_ids ==
    ()``; ``ambiguous=False`` ⇒ ``1 <= len(target_worker_ids) <= 2``.
    """

    schema_version: Literal["1"] = "1"
    target_worker_ids: tuple[LogicalId, ...] = ()
    evidence_artifact_ids: tuple[NonEmptyStr, ...] = ()
    allowed_changes: tuple[NonEmptyStr, ...] = ()
    reason: NonEmptyStr
    confidence: Confidence
    ambiguous: bool = False
    source: Literal["llm", "deterministic", "rule"]

    @model_validator(mode="after")
    def _attribution_matrix(self) -> "Feedback":
        if self.ambiguous:
            if self.target_worker_ids != ():
                raise ValueError("ambiguous=True requires an empty target_worker_ids")
        else:
            if not (1 <= len(self.target_worker_ids) <= 2):
                raise ValueError(
                    "ambiguous=False requires 1 or 2 target_worker_ids"
                )
        return self


class GovernanceReport(DigestModel):
    """L2 overfitting-governance report — honesty over fabricated scores.

    ``status="unavailable"`` (⇒ ``deflated_sharpe`` and ``pbo`` both ``None`` and a
    non-empty ``reasons``) is returned instead of fabricating governance numbers.
    ``effective_n_trials`` never exceeds ``raw_trial_count``. When PBO is disabled
    (``pbo_enabled=False``) ``pbo`` must be ``None`` and a ``pbo_reason`` explains
    why.
    """

    schema_version: Literal["1"] = "1"
    status: Literal["ok", "unavailable"]
    family_id: LogicalId
    raw_trial_count: NonNegativeInt
    effective_n_trials: NonNegativeInt | None = None
    deflated_sharpe: FiniteFloat | None = None
    pbo: FiniteFloat | None = None
    pbo_enabled: bool = False
    pbo_reason: NonEmptyStr | None = None
    complexity_score: FiniteFloat | None = None
    trial_budget_remaining: NonNegativeInt
    peek_budget_remaining: NonNegativeInt
    reasons: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _matrix(self) -> "GovernanceReport":
        if self.status == "unavailable":
            if self.deflated_sharpe is not None or self.pbo is not None:
                raise ValueError(
                    "status='unavailable' must not carry deflated_sharpe or pbo"
                )
            if not self.reasons:
                raise ValueError("status='unavailable' requires a non-empty reasons")
        if (
            self.effective_n_trials is not None
            and self.effective_n_trials > self.raw_trial_count
        ):
            raise ValueError("effective_n_trials cannot exceed raw_trial_count")
        if not self.pbo_enabled:
            if self.pbo is not None:
                raise ValueError("pbo_enabled=False requires pbo is None")
            if self.pbo_reason is None:
                raise ValueError("pbo_enabled=False requires a pbo_reason")
        return self


class HonestyGateReport(DigestModel):
    """L0 honesty-gate outcome (nested / embedded).

    ``checked`` is the canonically sorted, duplicate-free set of executed check
    names. A failed gate (``passed=False``) must carry a non-empty ``reasons``.
    """

    schema_version: Literal["1"] = "1"
    passed: bool
    reasons: tuple[NonEmptyStr, ...] = ()
    checked: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def _verify(self) -> "HonestyGateReport":
        checked = list(self.checked)
        if checked != sorted(checked):
            raise ValueError("checked must be canonically sorted")
        if len(set(checked)) != len(checked):
            raise ValueError("checked must be duplicate-free")
        if not self.passed and not self.reasons:
            raise ValueError("passed=False requires a non-empty reasons")
        return self


class OptimizeRound(DigestModel):
    """One append-only round archive of an optimize experiment.

    Records the round's candidate hash and whichever evaluation facts were produced
    (``l0`` / ``metrics`` / ``gate`` / ``governance`` / ``feedback``), plus reuse /
    stall / error bookkeeping. ``created_at`` is the archive wall-clock and is
    audit-only, so re-archiving byte-identical round content later leaves the
    semantic identity stable.
    """

    schema_version: Literal["1"] = "1"
    experiment_id: NonEmptyStr
    round_index: NonNegativeInt
    candidate_hash: DigestHex
    trial_id: NonEmptyStr | None = None
    reused_from_trial_id: NonEmptyStr | None = None
    l0: HonestyGateReport | None = None
    metrics: ValidationMetrics | None = None
    gate: GateResult | None = None
    governance: GovernanceReport | None = None
    feedback: Feedback | None = None
    stall_retry: bool = False
    error: NonEmptyStr | None = None
    created_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"created_at"})


# --------------------------------------------------------------------------- #
# Holdout window                                                              #
# --------------------------------------------------------------------------- #
class HoldoutWindow(DigestModel):
    """A non-overlapping, already-matured OOT holdout window.

    Time discipline is structural: ``start_at < end_at <= matured_at`` (the window
    ends no later than it matured, so a not-yet-matured window is unconstructible),
    and ``prior_window_ids`` is duplicate-free and excludes this window's own id, so
    a window can never claim to descend from itself. ``data_snapshot_id`` is a
    storage locator (mirroring ``DataContext``) and is excluded from the semantic
    digest — re-snapshotting identical vintage content under a new id leaves the
    window's semantic identity stable; ``vintage_manifest_digest`` /
    ``non_overlap_attestation`` carry the content-bearing evidence.
    """

    schema_version: Literal["1"] = "1"
    holdout_window_id: NonEmptyStr
    family_identity_digest: DigestHex
    start_at: UtcDateTime
    end_at: UtcDateTime
    matured_at: UtcDateTime
    data_snapshot_id: NonEmptyStr
    vintage_manifest_digest: DigestHex
    prior_window_ids: tuple[NonEmptyStr, ...] = ()
    non_overlap_attestation: DigestHex

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"data_snapshot_id"})

    @model_validator(mode="after")
    def _verify(self) -> "HoldoutWindow":
        if not (self.start_at < self.end_at <= self.matured_at):
            raise ValueError(
                "HoldoutWindow requires start_at < end_at <= matured_at"
            )
        if self.holdout_window_id in self.prior_window_ids:
            raise ValueError("prior_window_ids must not contain holdout_window_id")
        if len(set(self.prior_window_ids)) != len(self.prior_window_ids):
            raise ValueError("prior_window_ids must be duplicate-free")
        return self


# --------------------------------------------------------------------------- #
# Trial record                                                                #
# --------------------------------------------------------------------------- #
class TrialRecord(DigestModel):
    """The cross-run record of one trial — validation or sealed holdout.

    The stage/status/lease matrix is enforced at the model so no code path can
    publish an ill-formed trial (spec §7 public-record emptiness rule):

    * ``stage="validation"`` carries no holdout locators and ``lease_state ==
      "none"``;
    * ``stage="sealed_holdout"`` carries the holdout window + lease and a live
      ``lease_state``, and can **never** carry a ``validation_result_artifact_id``
      or any ``metrics_revealed`` name — a metrics-bearing holdout record is
      structurally unconstructible;
    * ``status="reserved"`` carries no result/reveal evidence (and a holdout
      reservation holds a ``reserved`` lease);
    * a revealed validation trial carries its artifact id, ``result_digest``,
      ``revealed_at`` and a non-empty ``metrics_revealed``; a revealed holdout
      trial has ``lease_state == "consumed"``; a failed / timed-out / inconclusive
      holdout trial has ``lease_state == "exhausted"`` (spec 运行不变量 line 937).

    ``trial_id`` / ``created_at`` / ``revealed_at`` are audit identity and excluded
    from the semantic digest, so the same governed evaluation fact digests
    identically across re-issued ids and wall-clocks.
    """

    schema_version: Literal["1"] = "1"
    trial_id: NonEmptyStr
    family_id: LogicalId
    candidate_hash: DigestHex
    parent_trial_id: NonEmptyStr | None = None
    data_snapshot_hash: DigestHex
    split_spec_hash: DigestHex
    code_prompt_model_hash: DigestHex
    metrics_revealed: tuple[NonEmptyStr, ...] = ()
    stage: Literal["validation", "sealed_holdout"]
    status: Literal["reserved", "revealed", "failed", "timed_out", "inconclusive"]
    validation_result_artifact_id: NonEmptyStr | None = None
    result_digest: DigestHex | None = None
    holdout_window_id: NonEmptyStr | None = None
    holdout_lease_id: NonEmptyStr | None = None
    lease_state: Literal["none", "reserved", "consumed", "exhausted"] = "none"
    revealed_at: UtcDateTime | None = None
    idempotency_key: NonEmptyStr
    reused_from_trial_id: NonEmptyStr | None = None
    created_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset(
        {"trial_id", "created_at", "revealed_at"}
    )

    @model_validator(mode="after")
    def _matrix(self) -> "TrialRecord":
        holdout = self.stage == "sealed_holdout"
        # --- stage rules -------------------------------------------------- #
        if not holdout:  # validation
            if self.holdout_window_id is not None or self.holdout_lease_id is not None:
                raise ValueError(
                    "stage='validation' forbids holdout_window_id/holdout_lease_id"
                )
            if self.lease_state != "none":
                raise ValueError("stage='validation' requires lease_state == 'none'")
        else:  # sealed_holdout — the public-record emptiness rule
            if self.validation_result_artifact_id is not None:
                raise ValueError(
                    "stage='sealed_holdout' forbids validation_result_artifact_id"
                )
            if self.metrics_revealed != ():
                raise ValueError(
                    "stage='sealed_holdout' forbids metrics_revealed "
                    "(public-record emptiness)"
                )
            if self.holdout_window_id is None or self.holdout_lease_id is None:
                raise ValueError(
                    "stage='sealed_holdout' requires holdout_window_id and "
                    "holdout_lease_id"
                )
            if self.lease_state == "none":
                raise ValueError("stage='sealed_holdout' requires lease_state != 'none'")
        # --- status rules ------------------------------------------------- #
        if self.status == "reserved":
            if self.result_digest is not None:
                raise ValueError("status='reserved' forbids result_digest")
            if self.revealed_at is not None:
                raise ValueError("status='reserved' forbids revealed_at")
            if self.metrics_revealed != ():
                raise ValueError("status='reserved' forbids metrics_revealed")
            if holdout and self.lease_state != "reserved":
                raise ValueError(
                    "reserved holdout trial requires lease_state == 'reserved'"
                )
        elif self.status == "revealed":
            if not holdout:  # validation reveal
                if self.validation_result_artifact_id is None:
                    raise ValueError(
                        "revealed validation trial requires "
                        "validation_result_artifact_id"
                    )
                if self.result_digest is None:
                    raise ValueError("revealed validation trial requires result_digest")
                if self.revealed_at is None:
                    raise ValueError("revealed validation trial requires revealed_at")
                if not self.metrics_revealed:
                    raise ValueError(
                        "revealed validation trial requires a non-empty metrics_revealed"
                    )
            elif self.lease_state != "consumed":  # holdout reveal
                raise ValueError("revealed holdout trial requires lease_state == 'consumed'")
        else:  # failed / timed_out / inconclusive
            if holdout and self.lease_state != "exhausted":
                raise ValueError(
                    f"holdout status={self.status!r} requires lease_state == 'exhausted'"
                )
        return self


# --------------------------------------------------------------------------- #
# Optimize run state + result                                                 #
# --------------------------------------------------------------------------- #
class OptimizeRunState(DigestModel):
    """The persisted state of one optimize experiment.

    ``updated_at`` is the audit wall-clock and is excluded from the semantic
    digest. The maturity biconditional is structural: ``resume_after`` and
    ``wakeup_key`` are present **iff** ``status ==
    ExperimentStatus.WAITING_FOR_MATURITY`` — any other status carries neither, so
    a persisted wakeup can only exist for a genuinely waiting experiment.
    """

    schema_version: Literal["1"] = "1"
    experiment_id: NonEmptyStr
    family_id: LogicalId
    status: ExperimentStatus
    candidate_hash: DigestHex | None = None
    resume_after: UtcDateTime | None = None
    wakeup_key: NonEmptyStr | None = None
    updated_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"updated_at"})

    @model_validator(mode="after")
    def _maturity_matrix(self) -> "OptimizeRunState":
        waiting = self.status == ExperimentStatus.WAITING_FOR_MATURITY
        has_resume = self.resume_after is not None
        has_wakeup = self.wakeup_key is not None
        if waiting:
            if not (has_resume and has_wakeup):
                raise ValueError(
                    "status=WAITING_FOR_MATURITY requires resume_after and wakeup_key"
                )
        elif has_resume or has_wakeup:
            raise ValueError(
                "only status=WAITING_FOR_MATURITY may carry resume_after/wakeup_key"
            )
        return self


class OptimizeResult(DigestModel):
    """The terminal result of an optimize run over its :class:`OptimizeRunState`.

    A ``PASSED_VALIDATION`` result names the winning ``best_candidate_artifact_id``;
    a ``FAILED`` result carries an honest ``stop_reason`` (never a silent terminal).
    """

    schema_version: Literal["1"] = "1"
    state: OptimizeRunState
    best_candidate_artifact_id: NonEmptyStr | None = None
    validation_trial_ids: tuple[NonEmptyStr, ...] = ()
    stop_reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _status_matrix(self) -> "OptimizeResult":
        if (
            self.state.status == ExperimentStatus.PASSED_VALIDATION
            and self.best_candidate_artifact_id is None
        ):
            raise ValueError(
                "state.status=PASSED_VALIDATION requires best_candidate_artifact_id"
            )
        if self.state.status == ExperimentStatus.FAILED and self.stop_reason is None:
            raise ValueError("state.status=FAILED requires a stop_reason")
        return self


# --------------------------------------------------------------------------- #
# Public holdout receipt                                                       #
# --------------------------------------------------------------------------- #
class HoldoutReceipt(DigestModel):
    """The only public artifact of a sealed-holdout evaluation.

    The receipt carries an opaque ``result_digest`` (when revealed) but **no**
    ``PayloadRef`` / ``TypedPayloadRef`` / artifact-ref field — it can never
    dereference the sealed metrics or curve. Its six-field surface is frozen so the
    public information exposed by a holdout run cannot grow silently. A revealed
    receipt carries the ``result_digest`` and ``revealed_at``; a terminal-failure
    receipt carries neither.
    """

    schema_version: Literal["1"] = "1"
    trial_id: NonEmptyStr
    family_id: LogicalId
    holdout_window_id: NonEmptyStr
    status: Literal["revealed", "failed", "timed_out", "inconclusive"]
    result_digest: DigestHex | None = None
    revealed_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _revealed_matrix(self) -> "HoldoutReceipt":
        if self.status == "revealed" and (
            self.result_digest is None or self.revealed_at is None
        ):
            raise ValueError(
                "status='revealed' requires result_digest and revealed_at"
            )
        return self


# --------------------------------------------------------------------------- #
# One-shot holdout lease                                                       #
# --------------------------------------------------------------------------- #
class HoldoutLease(DigestModel):
    """A one-shot lease binding a candidate to a holdout window.

    ``expires_at`` must be strictly after ``issued_at`` (a zero/negative-lived lease
    is unconstructible). ``nonce`` + ``signature`` are the issuing gateway's
    verifiable evidence; the lease is consumed / exhausted exactly once by the
    ledger.
    """

    schema_version: Literal["1"] = "1"
    lease_id: NonEmptyStr
    trial_id: NonEmptyStr
    candidate_hash: DigestHex
    holdout_window_id: NonEmptyStr
    issued_at: UtcDateTime
    expires_at: UtcDateTime
    nonce: NonEmptyStr
    signature: DigestHex

    @model_validator(mode="after")
    def _expiry(self) -> "HoldoutLease":
        if self.expires_at <= self.issued_at:
            raise ValueError("HoldoutLease expires_at must be > issued_at")
        return self


# --------------------------------------------------------------------------- #
# Sealed evaluation record + capability                                        #
# --------------------------------------------------------------------------- #
class SealedEvaluationRecord(DigestModel):
    """The sealed detail of a holdout evaluation — readable only through a
    capability-gated sealed store, never through the public :class:`HoldoutReceipt`.

    ``metrics_payload`` is strict JSON-shaped detail (the exact ``PlanNode.params``
    acceptance rule). ``curve_ref``, when present, must point into the ``sealed``
    payload namespace — a ``main`` (public) namespace curve ref is rejected, so
    sealed content cannot masquerade as a public payload. ``created_at`` is
    audit-only.
    """

    schema_version: Literal["1"] = "1"
    trial_id: NonEmptyStr
    result_artifact_id: NonEmptyStr
    result_digest: DigestHex
    metrics_payload: dict[str, Any]
    curve_ref: TypedPayloadRef | None = None
    created_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"created_at"})

    @field_validator("metrics_payload")
    @classmethod
    def _json_shaped(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _ensure_json_shaped(v)

    @model_validator(mode="after")
    def _curve_namespace(self) -> "SealedEvaluationRecord":
        if self.curve_ref is not None and self.curve_ref.payload_ref.namespace != "sealed":
            raise ValueError(
                "curve_ref must reference a 'sealed'-namespace payload; got "
                f"{self.curve_ref.payload_ref.namespace!r}"
            )
        return self


class SealedCapability(DigestModel):
    """A capability token authorizing a principal to read sealed holdout content.

    ``scope`` is a closed literal (``final_report`` / ``human_review``): the sealed
    store admits no other reader scope. ``signature`` binds the issuing authority.
    """

    schema_version: Literal["1"] = "1"
    token_id: NonEmptyStr
    scope: Literal["final_report", "human_review"]
    principal_id: NonEmptyStr
    expires_at: UtcDateTime
    signature: DigestHex


# =========================================================================== #
# Task 9 — cumulative Phase-4 registry / catalog chain                         #
# =========================================================================== #
# Phase 4 extends *only* the sealed Phase-3 FULL (data + memory) cumulative
# registry/catalog. It never mutates or regenerates any upstream sealed
# registry/golden: old Plans keep resolving their exact Phase-1/2/3 digest through
# ``SchemaRegistryResolver`` while an Evaluator-Optimizer Plan binds the Phase-4
# digest. There is deliberately no global mutable "latest" registry/catalog.
#
# The implemented Phase-3 chain builders return a
# :class:`~guanlan_v2.orchestration.runtime_contracts.Phase2RuntimeRegistry` (which
# accepts the Phase-2 strict ``_StrictModel`` facts the plain Phase-1
# ``SchemaRegistry`` cannot), so — per the plan's Task 0 correction clause —
# :func:`build_phase4_registry` returns that same reviewed registry type and inherits
# its exact ``SchemaManifestEntry`` + ``content_digest`` formula, keeping every
# inherited schema byte-identical to the Phase-3 full golden.

#: catalog version tag for the cumulative Phase-4 catalog snapshot.
PHASE4_CATALOG_VERSION = "phase4-full-v1"
#: the sole reviewed gate-metric material id / version the Phase-4 catalog adds.
JOINT_GATE_MATERIAL_ID = "optimize.joint_gate"
JOINT_GATE_MATERIAL_VERSION = "1"
JOINT_GATE_METRIC_KIND = "gate_metric"
#: the reviewed research joint-gate material file (UTF-8, no BOM). Resolved relative
#: to the repo root so a tampered byte moves ``catalog_material_digest`` and trips
#: both the catalog golden and the material-digest verification.
_JOINT_GATE_MATERIAL_PATH = (
    Path(__file__).resolve().parents[2]
    / "config" / "orchestration" / "materials" / "optimize" / "joint_gate_v1.md"
)


class Phase4RegistryError(Exception):
    """A Phase-4 cumulative-registry construction invariant was violated."""


#: The exactly-16 reviewed Evaluator-Optimizer payload/fact contracts the Phase-4
#: cumulative registry resolves a :class:`SchemaRef` to (spec §8 verbatim names).
#: Frozen at ``@1`` — the AMEND-6 pattern-replay extension
#: (``candidate_kind="pattern_definition"`` / ``source="pattern_replay"`` /
#: ``profit_loss_ratio`` / ``n_occurrences``) is already part of
#: ``OptimizeCandidate@1`` / ``ValidationMetrics@1`` at this freeze, so the curator
#: phase needs no ``@2`` bump.
PHASE4_PUBLIC_MODELS: tuple[type[DigestModel], ...] = (
    StudySpec,
    StudyFamily,
    SplitSpec,
    OptimizeCandidate,
    ValidationMetrics,
    GovernanceReport,
    Feedback,
    OptimizeRound,
    HoldoutWindow,
    TrialRecord,
    OptimizeRunState,
    OptimizeResult,
    HoldoutReceipt,
    HoldoutLease,
    SealedEvaluationRecord,
    SealedCapability,
)

_R_NESTED_ROUND_COMPONENT = (
    "nested evaluation-gate component only ever embedded in an OptimizeRound record; "
    "never independently resolved by a SchemaRef payload ref (distinct from the "
    "Phase-1 Plan-gate spec.GateResult, which stays internal in spec.py — neither is "
    "registered, so no SchemaConflictError is possible; a later phase registering "
    "either requires a reviewed rename decision recorded here)"
)
_R_NESTED_L0_COMPONENT = (
    "nested L0 honesty-gate report component only ever embedded in an OptimizeRound / "
    "evaluator result; never a standalone SchemaRef-addressed payload"
)

#: The reviewed ``ContractModel -> reason`` map of the two Phase-4 contract models
#: that are deliberately NOT registered (nested evaluation components). The Phase-4
#: completeness firewall (``tests/orchestration/test_phase4_registry.py``) proves
#: ``PHASE4_PUBLIC_MODELS`` ∪ ``PHASE4_INTERNAL_MODELS`` partitions every public
#: ``ContractModel`` defined across the six Phase-4 modules exactly.
PHASE4_INTERNAL_MODELS: dict[type[DigestModel], str] = {
    GateResult: _R_NESTED_ROUND_COMPONENT,
    HonestyGateReport: _R_NESTED_L0_COMPONENT,
}

#: The reviewed reason map for the deliberately-unregistered NON-contract Phase-4
#: surface (services / ports / exception). These are not ``ContractModel``
#: subclasses at all — they never reach the schema registry — but are documented
#: here so the full unregistered Phase-4 surface is reviewed in one place. They are
#: named (not imported) to keep ``trial.py`` free of any service-module import
#: (``governor`` / ``trial_ledger`` / ``sealed`` / ``optimize`` / ``evaluator`` all
#: import *this* module; importing them back would be circular).
PHASE4_INTERNAL_SURFACE: dict[str, str] = {
    "Governor": "L2 governance service (governor.py); a stateful service, not a payload",
    "TrialLedger": "event-sourced cross-run ledger service (trial_ledger.py); not a payload",
    "SealedResultStore": "capability-gated sealed store service (sealed.py); not a payload",
    "SealedEvaluatorGateway": "process-separated sealed evaluator gateway (sealed.py); not a payload",
    "ExperimentStateStore": "optimize experiment-state persistence service (optimize.py); not a payload",
    "AttributionPort": "injectable L3 attribution Protocol (evaluator.py); not a concrete payload",
    "OptimizeRoundStore": "append-only round-store Protocol (optimize.py); not a concrete payload",
    "MaturityPending": "maturity-wakeup control exception (optimize.py); not a payload",
}


# --------------------------------------------------------------------------- #
# The cumulative Phase-4 schema registry                                       #
# --------------------------------------------------------------------------- #
def _phase3_full_registry_digest() -> str:
    from guanlan_v2.orchestration.memory import schema_registry as _reg

    return _reg.PHASE3_FULL_REGISTRY_DIGEST


def build_phase4_registry(expected_phase3_full_digest: DigestHex):
    """Build + seal the cumulative Phase-4 registry, pinned to the Phase-3 full base.

    Verifies ``expected_phase3_full_digest`` against the actual sealed Phase-3 FULL
    (data + memory) registry digest first — any other base digest is rejected
    *before any registration* — then registers the complete inherited cumulative set
    (Phase-1 public + Phase-2 runtime facts + Phase-3 data + Phase-3 memory)
    followed by :data:`PHASE4_PUBLIC_MODELS` into a *fresh*
    :class:`~guanlan_v2.orchestration.runtime_contracts.Phase2RuntimeRegistry` and
    seals it. No upstream registry is mutated; a fresh sealed instance is returned
    per call.
    """
    from guanlan_v2.orchestration.data.schema_registry import (
        PHASE3_PUBLIC_MODELS as _DATA_PUBLIC,
    )
    from guanlan_v2.orchestration.memory.schema_registry import (
        PHASE3_MEMORY_PUBLIC_MODELS as _MEM_PUBLIC,
    )
    from guanlan_v2.orchestration.runtime_contracts import (
        Phase2RuntimeRegistry,
        phase2_public_models,
    )

    actual = _phase3_full_registry_digest()
    if expected_phase3_full_digest != actual:
        raise Phase4RegistryError(
            "build_phase4_registry requires the exact Phase-3 full registry digest "
            f"{actual!r}; got {expected_phase3_full_digest!r}"
        )
    reg = Phase2RuntimeRegistry()
    for model in (
        tuple(phase2_public_models())
        + tuple(_DATA_PUBLIC)
        + tuple(_MEM_PUBLIC)
        + PHASE4_PUBLIC_MODELS
    ):
        reg.register(model)
    reg.seal()
    return reg


# --------------------------------------------------------------------------- #
# The cumulative Phase-4 catalog (one reviewed gate-metric material)           #
# --------------------------------------------------------------------------- #
def joint_gate_material():
    """The reviewed ``optimize.joint_gate`` gate-metric material (ref + resolved bytes).

    Loads the reviewed research joint-gate documentation from
    ``config/orchestration/materials/optimize/joint_gate_v1.md`` (strict UTF-8, no
    BOM) and seals its content-digest ``ContentRef`` through the single reviewed
    ``build_text_material`` path — a caller never pins the digest by hand, and a
    tampered byte moves the digest.
    """
    from guanlan_v2.orchestration.catalog_runtime import build_text_material

    raw = _JOINT_GATE_MATERIAL_PATH.read_bytes()
    return build_text_material(
        id=JOINT_GATE_MATERIAL_ID,
        version=JOINT_GATE_MATERIAL_VERSION,
        kind=JOINT_GATE_METRIC_KIND,
        raw=raw,
    )


def build_phase4_catalog_snapshot(
    phase3_full_snapshot,
    *,
    joint_gate_material,
    resolved_materials,
):
    """Extend the immutable Phase-3 full catalog with the one reviewed gate-metric.

    Rejects any base whose ``catalog_digest`` differs from the canonical Phase-3
    full catalog digest, verifies the joint-gate material is the exact reviewed
    ``gate_metric`` material (``optimize.joint_gate@1``) whose declared digest
    matches its bytes, adds exactly one ``ContentManifestEntry`` of
    ``kind="gate_metric"`` and performs **no** WorkerSpec / capability / skill update
    — every inherited manifest entry passes through byte-identical.
    """
    from guanlan_v2.orchestration.catalog import (
        CatalogError,
        ContentManifestEntry,
        ResolvedTextMaterial,
        build_catalog_snapshot,
        catalog_material_digest,
    )

    base_catalog_digest = _phase3_full_catalog_digest()
    if phase3_full_snapshot.catalog_digest != base_catalog_digest:
        raise CatalogError(
            "build_phase4_catalog_snapshot requires the canonical immutable Phase-3 "
            f"full catalog ({base_catalog_digest}); got base "
            f"{phase3_full_snapshot.catalog_digest}"
        )
    if not isinstance(joint_gate_material, ResolvedTextMaterial):
        raise CatalogError("joint_gate_material must be a ResolvedTextMaterial")
    ref = joint_gate_material.ref
    if joint_gate_material.kind != JOINT_GATE_METRIC_KIND:
        raise CatalogError(
            f"joint_gate_material.kind must be {JOINT_GATE_METRIC_KIND!r}; "
            f"got {joint_gate_material.kind!r}"
        )
    if ref.id != JOINT_GATE_MATERIAL_ID or ref.version != JOINT_GATE_MATERIAL_VERSION:
        raise CatalogError(
            f"joint_gate_material must be {JOINT_GATE_MATERIAL_ID}@"
            f"{JOINT_GATE_MATERIAL_VERSION}; got {ref.id}@{ref.version}"
        )
    if ref.content_digest != catalog_material_digest(joint_gate_material):
        raise CatalogError("joint-gate material digest does not match its bytes")
    base_content_keys = {
        (e.ref.id, e.ref.version) for e in phase3_full_snapshot.content_manifest
    }
    if (ref.id, ref.version) in base_content_keys:
        raise CatalogError(
            f"the base already carries {ref.id}@{ref.version}; the gate-metric is new"
        )

    new_entry = ContentManifestEntry(
        ref=ref,
        kind=JOINT_GATE_METRIC_KIND,
        name=JOINT_GATE_MATERIAL_ID,
        description=(
            "Reviewed research joint-gate metric: a candidate passes only when "
            "rank_ic >= min_rank_ic AND oos_verdict == 'robust' AND sharpe > 0."
        ),
        source_identity=JOINT_GATE_MATERIAL_ID,
    )
    return build_catalog_snapshot(
        catalog_version=PHASE4_CATALOG_VERSION,
        content_manifest=tuple(phase3_full_snapshot.content_manifest) + (new_entry,),
        skill_manifest=tuple(phase3_full_snapshot.skill_manifest),
        capability_manifest=tuple(phase3_full_snapshot.capability_manifest),
        workers=tuple(phase3_full_snapshot.workers),
        resolved_material=tuple(resolved_materials) + (joint_gate_material,),
    )


def _phase3_full_catalog_digest() -> str:
    from guanlan_v2.orchestration.memory import catalog as _cat

    return _cat.PHASE3_FULL_CATALOG_DIGEST


def phase4_catalog_snapshot():
    """The canonical immutable cumulative Phase-4 catalog snapshot.

    Rebuilds the reviewed Phase-3 full catalog and its exact resolved-material set
    the same way :func:`memory.catalog.phase3_full_catalog_snapshot` assembles them,
    then extends it with the single reviewed ``optimize.joint_gate`` gate-metric.
    """
    from guanlan_v2.orchestration.data.catalog import phase3_data_surface
    from guanlan_v2.orchestration.memory.catalog import (
        phase3_full_catalog_snapshot,
        phase3_memory_surface,
    )
    from guanlan_v2.orchestration.presets import load_compat_catalog

    base = phase3_full_catalog_snapshot()
    runtime = load_compat_catalog()
    p_text, p_caps = runtime.resolved_materials()
    data_surface = phase3_data_surface()
    mem_surface = phase3_memory_surface()
    phase3_materials = (
        tuple(p_text) + tuple(p_caps)
        + data_surface.text_materials() + data_surface.capability_materials
        + mem_surface.text_materials() + (mem_surface.proposal_capability_material,)
    )
    _ref, material = joint_gate_material()
    return build_phase4_catalog_snapshot(
        base, joint_gate_material=material, resolved_materials=phase3_materials
    )


# --------------------------------------------------------------------------- #
# Lazy canonical digests (PEP 562) — computed once, never a mutable "latest"    #
# --------------------------------------------------------------------------- #
_PHASE4_REGISTRY_DIGEST: str | None = None
_PHASE4_CATALOG_DIGEST: str | None = None


def __getattr__(name: str) -> Any:
    global _PHASE4_REGISTRY_DIGEST, _PHASE4_CATALOG_DIGEST
    if name == "PHASE4_BASE_REGISTRY_DIGEST":
        return _phase3_full_registry_digest()
    if name == "PHASE4_REGISTRY_DIGEST":
        if _PHASE4_REGISTRY_DIGEST is None:
            _PHASE4_REGISTRY_DIGEST = build_phase4_registry(
                _phase3_full_registry_digest()
            ).registry_digest
        return _PHASE4_REGISTRY_DIGEST
    if name == "PHASE4_BASE_CATALOG_DIGEST":
        return _phase3_full_catalog_digest()
    if name == "PHASE4_CATALOG_DIGEST":
        if _PHASE4_CATALOG_DIGEST is None:
            _PHASE4_CATALOG_DIGEST = phase4_catalog_snapshot().catalog_digest
        return _PHASE4_CATALOG_DIGEST
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
