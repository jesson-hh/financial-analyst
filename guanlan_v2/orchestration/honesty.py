# -*- coding: utf-8 -*-
"""Task 2 (Phase 8) — the honesty spine.

This module is the red-line-sensitive fabrication / incompleteness classifier the
two 跨切 (``xcut``) workers of Phase 8 (``x.number_critic`` / ``x.quality_gate``,
Task 7) run as **deterministic** handlers. It freezes three things:

* :class:`HonestyIssue` / :class:`HonestyReport` — the registered (Task 11)
  verdict record with a **closed** issue-code vocabulary. The vocabulary is
  closed on purpose: :func:`attribution_candidates` exposes these reports as the
  frozen, deterministic input the Phase 4 evaluator's attribution consumes
  *before* any LLM judging (spec §6.3), so a new code can never silently appear.
* :func:`scan_unsourced_numbers` — a pure anchor↔leaf reconciliation of a
  produced :class:`~guanlan_v2.orchestration.schemas.Artifact`.
* :func:`classify_worker` — the closed rule matrix that turns a
  :class:`~guanlan_v2.orchestration.catalog.WorkerSpec` + its
  :class:`~guanlan_v2.orchestration.schemas.NodeRun` + produced ``Artifact`` into
  a verdict, implementing spec 红线 "worker 编数 / 违反其 EvidencePolicy / 空产出
  ⇒ incomplete;合法无工具 worker 不因零调用被误杀".

**Determinism.** Every function here is a *pure* function of its frozen inputs:
no LLM, no I/O, no wall-clock. The same inputs always produce an identical report
(a stable semantic digest), so the two ``xcut`` workers can bind them as
deterministic ``handler_ref`` computations, exactly like the clause-(h)
``compute_pa_features`` / ``compute_market_factors`` precedents.

The load-bearing-number definition (:func:`scan_unsourced_numbers`)
--------------------------------------------------------------------
A *load-bearing number* is a numeric leaf of the payload's **semantic canonical
JSON** — i.e. a value that is an ``int`` or ``float`` (``bool`` is **not** a
number) reachable by a dotted path through the projected structure — with two
documented exemptions: a leaf whose final path segment is ``schema_version`` or
ends in ``_digest`` is structural metadata, never a surfaced figure, and is
skipped. Every load-bearing number must be backed by a
:class:`~guanlan_v2.orchestration.schemas.NumberAnchor`; an unbacked one is
``unsourced_number``. Working from the semantic canonical projection (not the raw
model) means audit-only fields already dropped by each nested model's projection
can never be mistaken for surfaced numbers, and ``-0.0`` / enum values / datetimes
are normalized once, killing the false-positive storm at the source.

The scan reconciles in **both** directions:

* anchor → leaf: an anchor whose ``payload_path`` resolves to no numeric leaf ⇒
  ``anchor_path_unresolved``; one whose ``value`` disagrees with the resolved
  leaf ⇒ ``fabricated_number`` (编数); an anchor flagged ``is_unsourced=True`` ⇒
  ``unsourced_number``;
* leaf → anchor: a load-bearing number that no anchor points at ⇒
  ``unsourced_number``.

Output is sorted by ``(pointer, code, number_label)`` so the result is
order-independent in the anchors' input order and byte-stable across runs.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

from pydantic import model_validator

from guanlan_v2.orchestration.catalog import WorkerSpec
from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    canonical_json,
)
from guanlan_v2.orchestration.enums import NodeStatus, ToolCallRequirement
from guanlan_v2.orchestration.refs import LogicalId
from guanlan_v2.orchestration.schemas import Artifact, NodeRun

__all__ = [
    "UNSOURCED_BADGE",
    "DEGRADED_BADGE",
    "IssueCode",
    "HonestyIssue",
    "HonestyReport",
    "scan_unsourced_numbers",
    "classify_worker",
    "attribution_candidates",
]

#: Appended to an artifact/report that surfaces a number lacking a source.
UNSOURCED_BADGE: str = "[UNSOURCED]"
#: Appended whenever a run degraded — degradation is *always* badged (spec §0).
DEGRADED_BADGE: str = "degraded"

#: The closed issue-code vocabulary (spec §6.3 attribution hook depends on it).
IssueCode = Literal[
    "empty_output",
    "required_tools_zero_calls",
    "forbidden_tools_called",
    "missing_input_refs",
    "unsourced_number",
    "anchor_path_unresolved",
    "fabricated_number",
    "degraded_input",
]

#: Codes that unconditionally mean the worker did not honestly complete its job:
#: 空产出 (empty_output), a REQUIRED/FORBIDDEN tool-policy breach, an uncited
#: required dependency, and the two number-integrity failures (a fabricated value
#: = 编数, and an anchor pointing nowhere). ``unsourced_number`` is deliberately
#: **not** here: whether it forces incomplete depends on the worker's
#: ``allow_unsourced_numbers`` policy, which the report does not carry.
_HARD_INCOMPLETE_CODES: frozenset[str] = frozenset(
    {
        "empty_output",
        "required_tools_zero_calls",
        "forbidden_tools_called",
        "missing_input_refs",
        "anchor_path_unresolved",
        "fabricated_number",
    }
)


# --------------------------------------------------------------------------- #
# Records                                                                      #
# --------------------------------------------------------------------------- #
class HonestyIssue(DigestModel):
    """One honesty finding against a produced artifact.

    ``code`` is drawn from the closed :data:`IssueCode` vocabulary; ``message``
    is the non-blank human explanation; ``pointer`` names the offending payload
    path (a dotted leaf path) when the finding is number-scoped; ``number_label``
    carries the anchor's label when the finding came from a :class:`NumberAnchor`.
    """

    schema_version: Literal["1"] = "1"
    code: IssueCode
    message: NonEmptyStr
    pointer: NonEmptyStr | None = None
    number_label: NonEmptyStr | None = None


class HonestyReport(DigestModel):
    """The verdict on one worker's produced artifact.

    ``verdict`` is one of ``ok`` / ``degraded`` / ``incomplete``.
    ``subject_content_digest`` binds the report to the exact artifact graded
    (``None`` when there was no artifact at all — the 空产出 case). This is a
    registered ``@1`` payload (Phase 8 Task 11).

    Consistency validator (self-contained on the report):

    * ``verdict="ok"`` may carry **no** incomplete-class issue and **no**
      ``degraded_input`` issue (an allowed ``unsourced_number`` may ride along);
    * ``verdict="degraded"`` requires a ``degraded_input`` issue and forbids any
      incomplete-class issue;
    * ``verdict="incomplete"`` must be justified by at least one incomplete-class
      issue **or** an ``unsourced_number`` issue (the disallowed-unsourced case);
    * any ``unsourced_number`` issue forces :data:`UNSOURCED_BADGE` into
      ``badges`` and any ``degraded_input`` issue forces :data:`DEGRADED_BADGE`
      (degradation is always badged).
    """

    schema_version: Literal["1"] = "1"
    worker_id: LogicalId
    node_id: LogicalId
    subject_content_digest: DigestHex | None
    verdict: Literal["ok", "degraded", "incomplete"]
    issues: tuple[HonestyIssue, ...] = ()
    badges: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _verdict_consistent(self) -> "HonestyReport":
        codes = {issue.code for issue in self.issues}
        has_hard = bool(codes & _HARD_INCOMPLETE_CODES)
        has_unsourced = "unsourced_number" in codes
        has_degraded = "degraded_input" in codes

        if self.verdict == "ok":
            if has_hard:
                raise ValueError("verdict 'ok' cannot carry an incomplete-class issue")
            if has_degraded:
                raise ValueError("verdict 'ok' cannot carry a degraded_input issue")
        elif self.verdict == "degraded":
            if has_hard:
                raise ValueError("verdict 'degraded' cannot carry an incomplete-class issue")
            if not has_degraded:
                raise ValueError("verdict 'degraded' requires a degraded_input issue")
        else:  # incomplete
            if not (has_hard or has_unsourced):
                raise ValueError(
                    "verdict 'incomplete' requires an incomplete-class or "
                    "unsourced_number issue to justify it"
                )

        if has_unsourced and UNSOURCED_BADGE not in self.badges:
            raise ValueError("an unsourced_number issue requires the [UNSOURCED] badge")
        if has_degraded and DEGRADED_BADGE not in self.badges:
            raise ValueError("a degraded_input issue requires the 'degraded' badge")
        return self


# --------------------------------------------------------------------------- #
# Number-provenance scan                                                       #
# --------------------------------------------------------------------------- #
def _is_number(value: Any) -> bool:
    """A JSON number (``int``/``float``) — a ``bool`` is explicitly not one."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _leaf_exempt(path: str) -> bool:
    """A leaf whose final path segment is structural metadata, never a figure."""
    last = path.rsplit(".", 1)[-1]
    return last == "schema_version" or last.endswith("_digest")


def _numeric_leaves(node: Any, prefix: str, out: dict[str, float]) -> None:
    """Collect every numeric leaf's dotted path → value from a JSON-native tree.

    Dict keys become path segments; list indices become integer segments. Sorted
    dict iteration keeps traversal deterministic (the final result is re-sorted
    anyway). Exempt leaves (:func:`_leaf_exempt`) are skipped.
    """
    if isinstance(node, dict):
        for key in sorted(node):
            child = f"{prefix}.{key}" if prefix else str(key)
            _numeric_leaves(node[key], child, out)
    elif isinstance(node, list):
        for idx, elem in enumerate(node):
            child = f"{prefix}.{idx}" if prefix else str(idx)
            _numeric_leaves(elem, child, out)
    elif _is_number(node):
        if prefix and not _leaf_exempt(prefix):
            out[prefix] = float(node)


def _resolve_path(structure: Any, path: str) -> tuple[bool, Any]:
    """Resolve a dotted ``path`` against a JSON-native ``structure``.

    Returns ``(found, value)``. Descends dicts by key and lists/tuples by integer
    index; a segment that cannot descend (missing key, bad/out-of-range index, or
    a scalar mid-path) yields ``(False, None)``.
    """
    node = structure
    for seg in path.split("."):
        if isinstance(node, dict):
            if seg not in node:
                return (False, None)
            node = node[seg]
        elif isinstance(node, list):
            try:
                idx = int(seg)
            except ValueError:
                return (False, None)
            if idx < 0 or idx >= len(node):
                return (False, None)
            node = node[idx]
        else:
            return (False, None)
    return (True, node)


def _payload_structure(artifact: Artifact[Any]) -> Any:
    """The payload's semantic canonical JSON, re-parsed into a native tree.

    Round-tripping through :func:`canonical_json` applies every nested model's
    own semantic projection (dropping audit-only fields), normalizes ``-0.0`` /
    enums / datetimes once, and yields an ordinary ``dict``/``list``/scalar tree
    to walk — using only the public digest surface.
    """
    return json.loads(canonical_json(artifact.payload, projection="semantic"))


def scan_unsourced_numbers(artifact: Artifact[Any]) -> tuple[HonestyIssue, ...]:
    """Reconcile the artifact's :class:`NumberAnchor`s against its numeric leaves.

    Pure and order-independent (see the module docstring for the closed rule set
    and the load-bearing-number definition). The returned tuple is sorted by
    ``(pointer, code, number_label)`` so anchor input order never changes it.
    """
    structure = _payload_structure(artifact)
    leaves: dict[str, float] = {}
    _numeric_leaves(structure, "", leaves)

    issues: list[HonestyIssue] = []
    anchored_paths: set[str] = set()

    for anchor in artifact.numbers:
        path = anchor.payload_path
        anchored_paths.add(path)
        found, value = _resolve_path(structure, path)
        if not found or not _is_number(value):
            issues.append(
                HonestyIssue(
                    code="anchor_path_unresolved",
                    message=(
                        f"anchor {anchor.label!r} points at payload path {path!r} "
                        "which resolves to no numeric leaf"
                    ),
                    pointer=path,
                    number_label=anchor.label,
                )
            )
            continue
        if float(anchor.value) != float(value):
            issues.append(
                HonestyIssue(
                    code="fabricated_number",
                    message=(
                        f"anchor {anchor.label!r} declares {anchor.value!r} but "
                        f"payload path {path!r} holds {value!r}"
                    ),
                    pointer=path,
                    number_label=anchor.label,
                )
            )
            continue
        if anchor.is_unsourced:
            issues.append(
                HonestyIssue(
                    code="unsourced_number",
                    message=(
                        f"anchor {anchor.label!r} at {path!r} names no source "
                        "(is_unsourced=True)"
                    ),
                    pointer=path,
                    number_label=anchor.label,
                )
            )

    # leaf → anchor: any load-bearing number no anchor points at is unsourced.
    for path in leaves:
        if path not in anchored_paths:
            issues.append(
                HonestyIssue(
                    code="unsourced_number",
                    message=(
                        f"load-bearing number at payload path {path!r} is not "
                        "backed by any NumberAnchor"
                    ),
                    pointer=path,
                )
            )

    return _sort_issues(issues)


def _sort_issues(issues: list[HonestyIssue]) -> tuple[HonestyIssue, ...]:
    """Stable canonical ordering: ``(pointer, code, number_label)``."""
    return tuple(
        sorted(
            issues,
            key=lambda i: (i.pointer or "", i.code, i.number_label or ""),
        )
    )


# --------------------------------------------------------------------------- #
# Worker classification                                                        #
# --------------------------------------------------------------------------- #
def _is_empty_payload(payload: Any) -> bool:
    """Whether a produced primary payload carries no content.

    ``None``, a blank string, and an empty container are empty; a validated typed
    model (which has at least its ``schema_version``) is not.
    """
    if payload is None:
        return True
    if isinstance(payload, str):
        return not payload.strip()
    if isinstance(payload, (dict, list, tuple, set, frozenset)):
        return len(payload) == 0
    return False


def _has_required_dependency(worker: WorkerSpec) -> bool:
    """Whether the worker declares ≥1 *required* upstream artifact input.

    The plan-node dependency graph is not in :func:`classify_worker`'s signature,
    so a worker's required :class:`InputBinding`s are the frozen deterministic
    proxy for "this node consumes an upstream artifact". Optional-only inputs are
    not treated as a dependency: they may legitimately be absent, so an empty
    ``input_refs`` must not kill such a worker.
    """
    return any(inp.required for inp in worker.inputs)


def classify_worker(
    *,
    worker: WorkerSpec,
    node_run: NodeRun,
    artifact: Artifact[Any] | None,
) -> HonestyReport:
    """Classify one worker's run into an :class:`HonestyReport` (pure).

    Applies the closed rule matrix (see the module docstring). Never mutates the
    inputs; runtime consumers map an ``incomplete`` verdict to
    :attr:`NodeStatus.INCOMPLETE` themselves. Callable from a typed payload as a
    deterministic ``handler_ref`` (Phase 8 Task 7's ``x.number_critic``).
    """
    policy = worker.evidence_policy
    status = node_run.status
    issues: list[HonestyIssue] = []
    badges: set[str] = set()

    # -- Rule 1: 空产出 (empty output) on a terminal success -------------- #
    if status in (NodeStatus.COMPLETED, NodeStatus.DEGRADED):
        if artifact is None or _is_empty_payload(artifact.payload):
            issues.append(
                HonestyIssue(
                    code="empty_output",
                    message=(
                        f"worker {worker.id!r} reported {status.value} but produced "
                        "no primary payload"
                    ),
                )
            )

    # -- Rule 2: tool-call requirement vs actual calls -------------------- #
    n_calls = len(node_run.tool_call_records)
    if policy.tool_calls is ToolCallRequirement.REQUIRED and n_calls == 0:
        issues.append(
            HonestyIssue(
                code="required_tools_zero_calls",
                message=(
                    f"worker {worker.id!r} has tool_calls=REQUIRED but made zero "
                    "tool calls"
                ),
            )
        )
    elif policy.tool_calls is ToolCallRequirement.FORBIDDEN and n_calls > 0:
        issues.append(
            HonestyIssue(
                code="forbidden_tools_called",
                message=(
                    f"worker {worker.id!r} has tool_calls=FORBIDDEN but made "
                    f"{n_calls} tool call(s)"
                ),
            )
        )
    # OPTIONAL, or FORBIDDEN/OPTIONAL with zero calls, is never an issue —
    # a legal no-tool worker is never killed for not calling a tool.

    # -- Rule 3: uncited required dependency ------------------------------ #
    if (
        artifact is not None
        and policy.require_input_refs
        and _has_required_dependency(worker)
        and artifact.input_refs == ()
    ):
        issues.append(
            HonestyIssue(
                code="missing_input_refs",
                message=(
                    f"worker {worker.id!r} requires input refs and consumes a "
                    "required upstream input but cites none"
                ),
            )
        )

    # -- Rule 4: number provenance (scan) -------------------------------- #
    if artifact is not None:
        issues.extend(scan_unsourced_numbers(artifact))

    # -- Rule 5: degradation is always badged ---------------------------- #
    if status is NodeStatus.DEGRADED and policy.optional_data_may_degrade:
        issues.append(
            HonestyIssue(
                code="degraded_input",
                message=f"worker {worker.id!r} ran on degraded optional data",
            )
        )
        badges.add(DEGRADED_BADGE)

    # -- Verdict ---------------------------------------------------------- #
    codes = {issue.code for issue in issues}
    has_hard = bool(codes & _HARD_INCOMPLETE_CODES)
    has_unsourced = "unsourced_number" in codes
    has_degraded = "degraded_input" in codes

    if has_unsourced:
        badges.add(UNSOURCED_BADGE)

    incomplete = has_hard or (has_unsourced and not policy.allow_unsourced_numbers)
    if incomplete:
        verdict: Literal["ok", "degraded", "incomplete"] = "incomplete"
    elif has_degraded:
        verdict = "degraded"
    else:
        verdict = "ok"

    return HonestyReport(
        worker_id=worker.id,
        node_id=node_run.node_id,
        subject_content_digest=artifact.content_digest if artifact is not None else None,
        verdict=verdict,
        issues=_sort_issues(issues),
        badges=tuple(sorted(badges)),
    )


# --------------------------------------------------------------------------- #
# Attribution hook surface (spec §6.3)                                         #
# --------------------------------------------------------------------------- #
def attribution_candidates(
    reports: tuple[HonestyReport, ...],
) -> tuple[HonestyReport, ...]:
    """The deterministic, node_id-ordered subset of reports with verdict ≠ ``ok``.

    Together with :attr:`Artifact.input_refs` citation chains and Task 9's
    ``DebateTranscript``, this tuple is the frozen deterministic input the Phase 4
    evaluator's attribution consumes before any LLM judging (spec §6.3). The
    ordering (by ``node_id``) makes it order-independent in the input order; the
    closed :data:`IssueCode` vocabulary keeps the surface stable.
    """
    return tuple(
        sorted(
            (r for r in reports if r.verdict != "ok"),
            key=lambda r: r.node_id,
        )
    )
