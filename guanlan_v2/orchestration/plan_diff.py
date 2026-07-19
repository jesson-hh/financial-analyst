# -*- coding: utf-8 -*-
"""Phase 7 - Task 6: typed plan diff + deterministic reviewer rendering.

The dynamic Planner emits a Phase 1 :class:`~guanlan_v2.orchestration.spec.PlanDraft`
candidate that a human must approve. To review it a person needs to see *what
this candidate changes* relative to the baseline it displaces (the request-level
fallback preset, a prior plan, or nothing) — expressed as an immutable, digestable
payload rather than a free-form message, so the reviewer-facing markdown can be
**verifiably** derived from (and rebound to) that payload on the load path.

This module is pure and I/O-free. It owns three registered-to-be contracts and
three pure functions:

* :class:`PlanDiffEntry` — one pointer-scoped change (``added`` / ``removed`` /
  ``changed``) carrying the two sides' canonical JSON strings;
* :class:`PlanDiff` — the full field-by-field diff over
  :meth:`PlanDraft.executable_projection` plus per-node membership tuples, bound
  to the request + candidate digests and the baseline's identity;
* :class:`PendingPlanApproval` — the durable reviewer card: the candidate's
  human-facing summary, a typed reference to the ``PlanDiff@1`` payload, the
  deterministically ``rendered_md`` and the sealed ``rendered_from_diff_digest``
  that binds it. A pending human card is structurally unconstructible for
  ``ApprovalPolicy.AUTO`` and only carries preset provenance for a
  preset-derived source.

Security / determinism spine
----------------------------
* **Only the executable projection is diffed.** :func:`build_plan_diff` reads
  :meth:`PlanDraft.executable_projection` — the exact 23 executable fields the
  single candidate digest binds. Plan/run identity (``id`` / ``run_id`` /
  ``request_id``) and the audit ``context_snapshot_ref`` locator are already
  excluded by that projection, so they can never surface in a diff or its
  rendering.
* **Pure functions of frozen inputs.** Diff and render read only immutable
  models, never mutate or re-validate a draft, and produce byte-identical output
  for identical inputs (stable field/entry/node ordering; sorted-key JSON cells;
  digests rendered in full).
* **The card is rebindable to its diff.** :func:`build_pending_plan_approval`
  computes ``rendered_md = render_plan_diff_md(diff)`` and seals
  ``rendered_from_diff_digest = diff.semantic_digest()``, after verifying the
  supplied ``plan_diff_ref`` resolves ``PlanDiff@1`` and its payload digest
  equals that diff digest. A consumer (Task 7) re-dereferences the ref, recomputes
  the render and compares — a tampered ``rendered_md`` cannot survive.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from pydantic import field_validator, model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    canonical_json,
)
from guanlan_v2.orchestration.enums import ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.refs import LogicalId, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.spec import OrchestrationRequest, PlanDraft

__all__ = [
    "PLAN_DIFF_SCHEMA_REF",
    "BaselineKind",
    "PlanDiffEntry",
    "PlanDiff",
    "PendingPlanApproval",
    "build_plan_diff",
    "render_plan_diff_md",
    "build_pending_plan_approval",
]

#: closed baseline-provenance vocabulary of a :class:`PlanDiff`.
BaselineKind = Literal["fallback_preset", "prior_plan", "none"]

#: the registered schema identity every :class:`PendingPlanApproval` ``plan_diff_ref``
#: must resolve (a ``TypedPayloadRef`` naming ``PlanDiff@1``).
PLAN_DIFF_SCHEMA_REF = SchemaRef(name="PlanDiff", version="1")

#: pointer prefix marking a per-node entry; the suffix is the node's logical id.
_NODE_POINTER_PREFIX = "nodes."
#: the one executable field diffed per node id rather than as a single scalar.
_NODES_FIELD = "nodes"


# --------------------------------------------------------------------------- #
# PlanDiffEntry                                                                #
# --------------------------------------------------------------------------- #
class PlanDiffEntry(DigestModel):
    """One pointer-scoped change between a baseline and a candidate plan.

    ``pointer`` is an executable field name (e.g. ``goal``) or a per-node pointer
    ``nodes.<node_id>``. The change matrix is enforced structurally: an ``added``
    entry has ``baseline_json is None`` and ``candidate_json`` set; ``removed`` is
    the inverse; ``changed`` carries both canonical JSON sides, which must differ.
    """

    schema_version: Literal["1"] = "1"
    pointer: NonEmptyStr
    change: Literal["added", "removed", "changed"]
    baseline_json: NonEmptyStr | None = None
    candidate_json: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _matrix(self) -> "PlanDiffEntry":
        if self.change == "added":
            if self.baseline_json is not None or self.candidate_json is None:
                raise ValueError(
                    "an 'added' entry requires baseline_json=None and candidate_json set"
                )
        elif self.change == "removed":
            if self.candidate_json is not None or self.baseline_json is None:
                raise ValueError(
                    "a 'removed' entry requires candidate_json=None and baseline_json set"
                )
        else:  # changed
            if self.baseline_json is None or self.candidate_json is None:
                raise ValueError("a 'changed' entry requires both JSON sides set")
            if self.baseline_json == self.candidate_json:
                raise ValueError("a 'changed' entry requires baseline_json != candidate_json")
        return self

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.pointer, self.change)


# --------------------------------------------------------------------------- #
# PlanDiff                                                                     #
# --------------------------------------------------------------------------- #
class PlanDiff(DigestModel):
    """The immutable field-by-field diff of a candidate plan against a baseline.

    Entries cover the executable projection (audit locators excluded by the
    projection itself); the ``nodes`` field is diffed per node id, so each node
    change is one ``nodes.<node_id>`` entry mirrored in ``nodes_added`` /
    ``nodes_removed`` / ``nodes_changed``. The ``baseline_kind`` matrix pins the
    baseline identity: ``none`` carries no baseline digest/preset id and every
    entry is ``added``; ``fallback_preset`` names a ``baseline_preset_id``;
    ``prior_plan`` names a ``baseline_plan_digest``.
    """

    schema_version: Literal["1"] = "1"
    baseline_kind: BaselineKind
    request_digest: DigestHex
    candidate_plan_digest: DigestHex
    baseline_plan_digest: DigestHex | None = None
    baseline_preset_id: LogicalId | None = None
    entries: tuple[PlanDiffEntry, ...] = ()
    nodes_added: tuple[LogicalId, ...] = ()
    nodes_removed: tuple[LogicalId, ...] = ()
    nodes_changed: tuple[LogicalId, ...] = ()

    @model_validator(mode="after")
    def _matrix(self) -> "PlanDiff":
        # -- entries canonically ordered by (pointer, change) ----------------- #
        keys = [e.sort_key for e in self.entries]
        if keys != sorted(keys):
            raise ValueError("entries must be sorted by (pointer, change)")

        # -- node membership tuples sorted + duplicate-free ------------------- #
        for name, tup in (
            ("nodes_added", self.nodes_added),
            ("nodes_removed", self.nodes_removed),
            ("nodes_changed", self.nodes_changed),
        ):
            if list(tup) != sorted(tup):
                raise ValueError(f"{name} must be sorted")
            if len(set(tup)) != len(tup):
                raise ValueError(f"{name} must be duplicate-free")

        # -- membership tuples must agree with the per-node entries ----------- #
        def _node_ids(kind: str) -> tuple[str, ...]:
            return tuple(sorted(
                e.pointer[len(_NODE_POINTER_PREFIX):]
                for e in self.entries
                if e.pointer.startswith(_NODE_POINTER_PREFIX) and e.change == kind
            ))

        if _node_ids("added") != self.nodes_added:
            raise ValueError("nodes_added must equal the added node entries")
        if _node_ids("removed") != self.nodes_removed:
            raise ValueError("nodes_removed must equal the removed node entries")
        if _node_ids("changed") != self.nodes_changed:
            raise ValueError("nodes_changed must equal the changed node entries")

        # -- baseline_kind matrix --------------------------------------------- #
        if self.baseline_kind == "none":
            if self.baseline_plan_digest is not None or self.baseline_preset_id is not None:
                raise ValueError(
                    "baseline_kind='none' forbids a baseline_plan_digest or baseline_preset_id"
                )
            if any(e.change != "added" for e in self.entries):
                raise ValueError("baseline_kind='none' requires every entry to be 'added'")
            if self.nodes_removed or self.nodes_changed:
                raise ValueError("baseline_kind='none' forbids removed/changed nodes")
        elif self.baseline_kind == "fallback_preset":
            if self.baseline_preset_id is None:
                raise ValueError("baseline_kind='fallback_preset' requires a baseline_preset_id")
            if self.baseline_plan_digest is not None:
                raise ValueError("baseline_kind='fallback_preset' forbids a baseline_plan_digest")
        else:  # prior_plan
            if self.baseline_plan_digest is None:
                raise ValueError("baseline_kind='prior_plan' requires a baseline_plan_digest")
            if self.baseline_preset_id is not None:
                raise ValueError("baseline_kind='prior_plan' forbids a baseline_preset_id")
        return self


# --------------------------------------------------------------------------- #
# PendingPlanApproval                                                          #
# --------------------------------------------------------------------------- #
class PendingPlanApproval(DigestModel):
    """The durable reviewer card for one candidate plan awaiting human approval.

    Summarizes the candidate for a person (goal, source, node/worker counts,
    budget request), references the ``PlanDiff@1`` payload through a typed
    ``plan_diff_ref``, and carries the deterministically ``rendered_md`` bound to
    that payload by ``rendered_from_diff_digest``. Structurally, a pending human
    card exists only for a ``REQUIRED`` policy (``AUTO`` is unconstructible) and
    only for a ``DYNAMIC`` or ``PRESET_FALLBACK`` source; preset provenance
    (``preset_id`` / ``preset_record_digest``, consumed by Task 7b's ApprovalLease
    matching) is present iff the source is ``PRESET_FALLBACK`` and absent for
    ``DYNAMIC`` — both directions enforced. ``candidate_id`` and ``requested_at``
    are audit identity, excluded from the semantic digest.
    """

    schema_version: Literal["1"] = "1"
    request_id: NonEmptyStr
    candidate_plan_digest: DigestHex
    goal: NonEmptyStr
    source: PlanSource
    approval_policy: ApprovalPolicy
    node_count: PositiveInt
    worker_ids: tuple[LogicalId, ...]
    budget_request_tokens: NonNegativeInt = 0
    budget_request_llm_invocations: NonNegativeInt = 0
    plan_diff_ref: TypedPayloadRef
    rendered_md: str
    rendered_from_diff_digest: DigestHex
    planner_rationale: NonEmptyStr | None = None
    candidate_id: NonEmptyStr
    requested_at: UtcDateTime
    preset_id: LogicalId | None = None
    preset_record_digest: DigestHex | None = None

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"candidate_id", "requested_at"})

    @field_validator("rendered_md")
    @classmethod
    def _rendered_md_non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("rendered_md must not be blank")
        return v

    @model_validator(mode="after")
    def _verify(self) -> "PendingPlanApproval":
        if self.source not in (PlanSource.DYNAMIC, PlanSource.PRESET_FALLBACK):
            raise ValueError(
                "a pending plan-approval card is only for a DYNAMIC or "
                "PRESET_FALLBACK source"
            )
        if self.approval_policy is not ApprovalPolicy.REQUIRED:
            raise ValueError(
                "a pending human-approval card is unconstructible for a "
                "non-REQUIRED (AUTO) approval_policy"
            )
        if not self.worker_ids:
            raise ValueError("worker_ids must be non-empty")
        if list(self.worker_ids) != sorted(self.worker_ids):
            raise ValueError("worker_ids must be sorted")
        if len(set(self.worker_ids)) != len(self.worker_ids):
            raise ValueError("worker_ids must be unique")
        if self.plan_diff_ref.schema_ref != PLAN_DIFF_SCHEMA_REF:
            raise ValueError("plan_diff_ref must resolve PlanDiff@1")
        if self.plan_diff_ref.payload_ref.content_digest != self.rendered_from_diff_digest:
            raise ValueError(
                "plan_diff_ref payload content_digest must equal rendered_from_diff_digest"
            )
        # -- preset provenance both-direction rule ---------------------------- #
        prov = (self.preset_id, self.preset_record_digest)
        if self.source is PlanSource.DYNAMIC:
            if any(x is not None for x in prov):
                raise ValueError("a DYNAMIC pending card must not carry preset provenance")
        else:  # PRESET_FALLBACK
            if any(x is None for x in prov):
                raise ValueError(
                    "a PRESET_FALLBACK pending card requires both preset_id and "
                    "preset_record_digest"
                )
        return self


# --------------------------------------------------------------------------- #
# build_plan_diff                                                             #
# --------------------------------------------------------------------------- #
def build_plan_diff(
    candidate: PlanDraft,
    *,
    request: OrchestrationRequest,
    candidate_plan_digest: DigestHex,
    baseline: PlanDraft | None,
    baseline_kind: BaselineKind,
    baseline_plan_digest: DigestHex | None = None,
    baseline_preset_id: LogicalId | None = None,
) -> PlanDiff:
    """Diff ``candidate`` against ``baseline`` over the executable projection.

    Pure: reads only :meth:`PlanDraft.executable_projection` (which already
    excludes plan/run identity and the audit ``context_snapshot_ref`` locator),
    never mutates or re-validates a draft. Every non-``nodes`` executable field is
    compared field-by-field over its canonical JSON; the ``nodes`` field is diffed
    per node id. A ``none`` baseline (``baseline is None``) yields an all-``added``
    diff. The returned :class:`PlanDiff` enforces its own baseline-kind matrix.
    """
    if baseline_kind == "none":
        if baseline is not None:
            raise ValueError("baseline_kind='none' requires baseline=None")
    else:
        if baseline is None:
            raise ValueError(f"baseline_kind={baseline_kind!r} requires a baseline draft")

    cand_proj = candidate.executable_projection()
    base_proj = baseline.executable_projection() if baseline is not None else None

    entries: list[PlanDiffEntry] = []

    # -- scalar (non-node) executable fields ------------------------------- #
    for field, cand_value in cand_proj.items():
        if field == _NODES_FIELD:
            continue
        cand_json = canonical_json(cand_value)
        if base_proj is None:
            entries.append(PlanDiffEntry(
                pointer=field, change="added",
                baseline_json=None, candidate_json=cand_json))
            continue
        base_json = canonical_json(base_proj[field])
        if base_json != cand_json:
            entries.append(PlanDiffEntry(
                pointer=field, change="changed",
                baseline_json=base_json, candidate_json=cand_json))

    # -- the nodes field, diffed per node id ------------------------------- #
    cand_nodes = {n.id: canonical_json(n) for n in candidate.nodes}
    base_nodes = (
        {n.id: canonical_json(n) for n in baseline.nodes}
        if baseline is not None else {}
    )
    nodes_added: list[str] = []
    nodes_removed: list[str] = []
    nodes_changed: list[str] = []
    for nid in set(cand_nodes) | set(base_nodes):
        pointer = f"{_NODE_POINTER_PREFIX}{nid}"
        in_base = nid in base_nodes
        in_cand = nid in cand_nodes
        if in_cand and not in_base:
            entries.append(PlanDiffEntry(
                pointer=pointer, change="added",
                baseline_json=None, candidate_json=cand_nodes[nid]))
            nodes_added.append(nid)
        elif in_base and not in_cand:
            entries.append(PlanDiffEntry(
                pointer=pointer, change="removed",
                baseline_json=base_nodes[nid], candidate_json=None))
            nodes_removed.append(nid)
        elif base_nodes[nid] != cand_nodes[nid]:
            entries.append(PlanDiffEntry(
                pointer=pointer, change="changed",
                baseline_json=base_nodes[nid], candidate_json=cand_nodes[nid]))
            nodes_changed.append(nid)

    entries.sort(key=lambda e: e.sort_key)
    return PlanDiff(
        baseline_kind=baseline_kind,
        request_digest=request.semantic_digest(),
        candidate_plan_digest=candidate_plan_digest,
        baseline_plan_digest=baseline_plan_digest,
        baseline_preset_id=baseline_preset_id,
        entries=tuple(entries),
        nodes_added=tuple(sorted(nodes_added)),
        nodes_removed=tuple(sorted(nodes_removed)),
        nodes_changed=tuple(sorted(nodes_changed)),
    )


# --------------------------------------------------------------------------- #
# render_plan_diff_md                                                         #
# --------------------------------------------------------------------------- #
def _md_cell(text: str | None) -> str:
    """Render one table cell: escape ``|`` and flatten newlines; ``-`` for absent."""
    if text is None:
        return "-"
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _json_value(mapping: dict[str, Any], key: str) -> str:
    """Deterministic compact JSON of ``mapping[key]``, or ``-`` when absent."""
    if key not in mapping:
        return "-"
    return json.dumps(mapping[key], sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def render_plan_diff_md(diff: PlanDiff) -> str:
    """Render ``diff`` as deterministic, plain (no-HTML) reviewer markdown.

    Pure function of the frozen :class:`PlanDiff`: stable section order, entries
    consumed in their already-canonical ``(pointer, change)`` order, per-node
    field tables with sorted keys and sorted-key JSON cells, and every digest
    printed in full. Two calls over an equal diff produce byte-identical output.
    """
    lines: list[str] = []
    lines.append("# Plan Diff")
    lines.append("")
    lines.append(f"- baseline_kind: `{diff.baseline_kind}`")
    lines.append(f"- request_digest: `{diff.request_digest}`")
    lines.append(f"- candidate_plan_digest: `{diff.candidate_plan_digest}`")
    lines.append(
        f"- baseline_plan_digest: `{diff.baseline_plan_digest or '(none)'}`")
    lines.append(
        f"- baseline_preset_id: `{diff.baseline_preset_id or '(none)'}`")
    lines.append("")

    # -- node membership --------------------------------------------------- #
    def _ids(tup: tuple[str, ...]) -> str:
        return ", ".join(tup) if tup else "(none)"

    lines.append("## Nodes")
    lines.append(f"- added: {_ids(diff.nodes_added)}")
    lines.append(f"- removed: {_ids(diff.nodes_removed)}")
    lines.append(f"- changed: {_ids(diff.nodes_changed)}")
    lines.append("")

    # -- scalar field changes ---------------------------------------------- #
    field_entries = [e for e in diff.entries if not e.pointer.startswith(_NODE_POINTER_PREFIX)]
    lines.append("## Field changes")
    if not field_entries:
        lines.append("(none)")
    else:
        lines.append("| pointer | change | baseline | candidate |")
        lines.append("| --- | --- | --- | --- |")
        for e in field_entries:
            lines.append(
                f"| {_md_cell(e.pointer)} | {e.change} | "
                f"{_md_cell(e.baseline_json)} | {_md_cell(e.candidate_json)} |")
    lines.append("")

    # -- per-node detail tables -------------------------------------------- #
    node_entries = [e for e in diff.entries if e.pointer.startswith(_NODE_POINTER_PREFIX)]
    lines.append("## Node details")
    if not node_entries:
        lines.append("(none)")
    else:
        for e in node_entries:
            lines.append(f"### {e.pointer} ({e.change})")
            base = json.loads(e.baseline_json) if e.baseline_json is not None else {}
            cand = json.loads(e.candidate_json) if e.candidate_json is not None else {}
            lines.append("| field | baseline | candidate |")
            lines.append("| --- | --- | --- |")
            for key in sorted(set(base) | set(cand)):
                lines.append(
                    f"| {_md_cell(key)} | {_md_cell(_json_value(base, key))} | "
                    f"{_md_cell(_json_value(cand, key))} |")
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# --------------------------------------------------------------------------- #
# build_pending_plan_approval                                                 #
# --------------------------------------------------------------------------- #
def build_pending_plan_approval(
    *,
    draft: PlanDraft,
    request: OrchestrationRequest,
    candidate_plan_digest: DigestHex,
    diff: PlanDiff,
    plan_diff_ref: TypedPayloadRef,
    planner_rationale: str | None,
    candidate_id: NonEmptyStr,
    requested_at: UtcDateTime,
    preset_id: LogicalId | None = None,
    preset_record_digest: DigestHex | None = None,
) -> PendingPlanApproval:
    """Build the reviewer card, sealing ``rendered_md`` to the diff payload.

    Verifies ``plan_diff_ref`` resolves ``PlanDiff@1`` and its payload digest
    equals ``diff.semantic_digest()`` (and that the supplied
    ``candidate_plan_digest`` matches the diff's), then computes
    ``rendered_md = render_plan_diff_md(diff)`` and seals
    ``rendered_from_diff_digest = diff.semantic_digest()``. ``preset_id`` /
    ``preset_record_digest`` are pass-through provenance for a preset-derived
    source (Task 7b lease matching); the model enforces the both-direction rule.
    """
    diff_digest = diff.semantic_digest()
    if plan_diff_ref.schema_ref != PLAN_DIFF_SCHEMA_REF:
        raise ValueError("plan_diff_ref must resolve PlanDiff@1")
    if plan_diff_ref.payload_ref.content_digest != diff_digest:
        raise ValueError(
            "plan_diff_ref does not reference the supplied diff (payload content "
            "digest != diff semantic digest)"
        )
    if candidate_plan_digest != diff.candidate_plan_digest:
        raise ValueError("candidate_plan_digest must equal the diff's candidate_plan_digest")

    rendered_md = render_plan_diff_md(diff)
    worker_ids = tuple(sorted({n.worker_id for n in draft.nodes}))
    return PendingPlanApproval(
        request_id=request.request_id,
        candidate_plan_digest=candidate_plan_digest,
        goal=request.goal,
        source=draft.source,
        approval_policy=draft.approval_policy,
        node_count=len(draft.nodes),
        worker_ids=worker_ids,
        budget_request_tokens=draft.budget_request_tokens,
        budget_request_llm_invocations=draft.budget_request_llm_invocations,
        plan_diff_ref=plan_diff_ref,
        rendered_md=rendered_md,
        rendered_from_diff_digest=diff_digest,
        planner_rationale=planner_rationale,
        candidate_id=candidate_id,
        requested_at=requested_at,
        preset_id=preset_id,
        preset_record_digest=preset_record_digest,
    )
