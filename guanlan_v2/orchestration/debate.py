# -*- coding: utf-8 -*-
"""Phase 8 * Task 9 — the Lane-D bounded-debate runtime.

Phase 1 owns the debate **contracts** (``DebateCfg`` / the ``PlanNode`` debate
identity fields / the six frozen debate structural issue codes — spec.py). Task 9
builds the **runtime** on top of them, adding nothing to the frozen surface:

* :class:`DebateMessage` / :class:`DebateTranscript` — the immutable typed payloads.
  A ``DebateMessage`` is the immutable event of one seat's turn: its semantic
  identity is ``(debate_id, round, turn, role, artifact_id)`` and ``created_at`` is
  audit-only (wall-clock never perturbs the transcript digest). A
  ``DebateTranscript`` is the reducer output for a debate slot — strictly ordered by
  ``(round, turn)``, unique in both ``(round, turn)`` and ``(round, role)``, every
  round ``<= max_rounds`` — so it is a *frozen deterministic input* the Phase-4
  attribution hook (:func:`honesty.attribution_candidates`) consumes alongside the
  citation chains, with a stable :meth:`semantic_digest`.

* :func:`verify_debate_expansion` — the "wan quan zhan kai" (full-expansion) rule
  on top of Phase 1's structural checks: the debate's seat nodes must cover exactly
  rounds ``1..R`` for some ``R <= max_rounds``, each round covering ``turn_order``
  completely and in order (``debate_turn`` = 1-based index in ``turn_order``,
  ``round_role`` = the seat at that index). It emits only the three Task-9 codes
  (``debate_incomplete_round`` / ``debate_turn_order_mismatch`` /
  ``debate_rounds_exceed_max``); Phase 1's own ``duplicate_debate_turn`` /
  ``debate_round_out_of_range`` fire first on the normal construction path, so Task 9
  never duplicates them (its ``debate_rounds_exceed_max`` is a defensive mirror
  reachable only when Phase 1 is bypassed via ``model_construct``).

* :func:`count_debate_invocations` / :func:`analyze_debates` — the pre-reservation
  budget + roster analyzer. Every seat x round is one LLM invocation reservation;
  the expanded invocation count is validated against
  ``draft.budget_request_llm_invocations`` BEFORE any reservation. Debate seats must
  be LLM workers, the judge must be a plan node that is **not** a seat of the same
  debate, and ``max_rounds`` must be ``<= DEBATE_MAX_ROUNDS`` (the reviewed Lane-D
  cap of 2, spec §0 决定 2).

* :func:`fold_debate_messages` + :func:`debate_transcript_reducer_handler` — the
  deterministic Plan-order fold. Message order derives **only** from the Plan node
  debate fields sorted by ``(debate_round, debate_turn)``, never thread/commit
  completion order; ``artifact_id`` / ``created_at`` are read from each committed
  Artifact. The handler is the concrete debate instance of the Task-8
  ``pool.fold_reducer_producers`` seam (registered as the trusted reducer
  ``DEBATE_TRANSCRIPT_REDUCER_ID``): it re-derives the transcript from Plan order and
  ignores the seam's producer-node-id arrival order, so a shuffled completion order
  yields a byte-identical transcript.

* :func:`publish_debate_message` / :func:`replay_debate_transcript` — the
  persist-then-publish seam over the REAL Phase-2 ``RuntimeStores``. One committed
  debate seat Artifact becomes one ``DebateMessage`` payload + one public
  ``EventType.DEBATE_MESSAGE_PUBLISHED`` event in a single unit of work, keyed
  idempotently by the domain-tagged :func:`debate_message_key` over
  ``(run_id, plan_digest, debate_id, round, turn, role)`` — a second, different
  message under the same identity is an ``IdempotencyConflict``, never an update.
  Replaying the visible events reconstructs exactly the pool fold's transcript.

Nothing here is registered in any schema registry (``DebateMessage`` stays a
Phase-1-absence deferral until Task 11) and no worker roster / capability changes
(Task 10 owns the debate rosters).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import model_validator

from guanlan_v2.orchestration.digest import (
    DigestHex,
    DigestModel,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    content_digest,
)
from guanlan_v2.orchestration.enums import ExecutionKind
from guanlan_v2.orchestration.eventstore import (
    EventAppendCommand,
    PayloadPutCommand,
    RuntimeBatch,
    RuntimeStores,
    StagedPayloadKey,
)
from guanlan_v2.orchestration.refs import LogicalId, SchemaRef
from guanlan_v2.orchestration.runtime_contracts import RuntimeSupportIssue
from guanlan_v2.orchestration.spec import DebateCfg, PlanDraft, PlanNode, PlanValidationIssue

if TYPE_CHECKING:
    from guanlan_v2.orchestration.catalog_runtime import CatalogRuntime
    from guanlan_v2.orchestration.events import RunEvent
    from guanlan_v2.orchestration.runtime_support import StaticRuntimeProfileV2
    from guanlan_v2.orchestration.schemas import Artifact

__all__ = [
    "DEBATE_MAX_ROUNDS",
    "DEBATE_TRANSCRIPT_REDUCER_ID",
    "DEBATE_MESSAGE_SCHEMA_REF",
    "DEBATE_TRANSCRIPT_SCHEMA_REF",
    "DEBATE_MESSAGE_KEY_DOMAIN",
    "DEBATE_MESSAGE_EVENT_TYPE",
    "DebateMessage",
    "DebateTranscript",
    "verify_debate_expansion",
    "count_debate_invocations",
    "analyze_debates",
    "fold_debate_messages",
    "debate_transcript_reducer_handler",
    "debate_message_key",
    "publish_debate_message",
    "replay_debate_transcript",
]

#: The reviewed Lane-D bounded-debate round cap (spec §0 决定 2). Both the bull/bear
#: and the three-seat risk debates run at most this many rounds. It is a named
#: Lane-D constant, NOT a field of the sealed ``StaticRuntimeProfileV2`` — adding a
#: field there would perturb that Task-8 profile's self-sealed ``profile_digest``.
DEBATE_MAX_ROUNDS: int = 2

#: The trusted reducer handler id a Plan's ``ReducerCfg.reducer_ref`` wires for a
#: debate slot; the catalog carries a ``kind="reducer"`` material at this id.
DEBATE_TRANSCRIPT_REDUCER_ID: LogicalId = "debate.transcript_reducer"

#: The registered payload schema refs (registration itself is Task 11; these are the
#: canonical identities the runtime persists / reduces under).
DEBATE_MESSAGE_SCHEMA_REF: SchemaRef = SchemaRef(name="DebateMessage", version="1")
DEBATE_TRANSCRIPT_SCHEMA_REF: SchemaRef = SchemaRef(name="DebateTranscript", version="1")

#: The additive public event type value (mirrors ``EventType.DEBATE_MESSAGE_PUBLISHED``).
DEBATE_MESSAGE_EVENT_TYPE: str = "DebateMessagePublished"

#: The idempotency-key family domain tag (house pattern: a domain-tagged content
#: digest so a cross-family key collision is structurally impossible).
DEBATE_MESSAGE_KEY_DOMAIN: str = "debate-message-published-v1"


# --------------------------------------------------------------------------- #
# Immutable payloads                                                          #
# --------------------------------------------------------------------------- #
class DebateMessage(DigestModel):
    """One immutable debate turn: seat ``role`` speaking at ``(round, turn)``.

    The semantic identity is ``(debate_id, round, turn, role, artifact_id)`` — the
    exact immutable event of one seat's contribution. ``created_at`` is the wall
    clock and is audit-only (excluded from the semantic digest), so re-observing the
    same turn never changes the message identity. A second, different message under
    the same ``(debate_id, round, turn, role)`` is an idempotency conflict at the
    event store, never an in-place update.
    """

    schema_version: Literal["1"] = "1"
    debate_id: LogicalId
    round: PositiveInt
    turn: PositiveInt
    role: LogicalId
    artifact_id: NonEmptyStr
    created_at: UtcDateTime

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"created_at"})


class DebateTranscript(DigestModel):
    """The deterministic reduced view of one debate slot's committed turns.

    ``messages`` is strictly ordered by ``(round, turn)``, unique in both
    ``(round, turn)`` and ``(round, role)``, every message belongs to ``debate_id``
    and every ``round <= max_rounds``. Because the message order is derived from Plan
    order (never thread completion) and ``created_at`` is audit-only, the transcript's
    ``semantic_digest`` is a stable, order-independent input the Phase-4 attribution
    hook consumes.
    """

    schema_version: Literal["1"] = "1"
    debate_id: LogicalId
    max_rounds: PositiveInt
    messages: tuple[DebateMessage, ...] = ()

    @model_validator(mode="after")
    def _coherent(self) -> "DebateTranscript":
        seen_turn: set[tuple[int, int]] = set()
        seen_role: set[tuple[int, str]] = set()
        prev: tuple[int, int] | None = None
        for m in self.messages:
            if m.debate_id != self.debate_id:
                raise ValueError(
                    f"transcript message debate_id {m.debate_id!r} != {self.debate_id!r}"
                )
            if m.round > self.max_rounds:
                raise ValueError(
                    f"transcript message round {m.round} exceeds max_rounds {self.max_rounds}"
                )
            key = (m.round, m.turn)
            if prev is not None and key <= prev:
                raise ValueError(
                    "transcript messages must be strictly ordered by (round, turn)"
                )
            prev = key
            if key in seen_turn:
                raise ValueError(f"duplicate transcript (round, turn) {key}")
            seen_turn.add(key)
            role_key = (m.round, m.role)
            if role_key in seen_role:
                raise ValueError(f"duplicate transcript (round, role) {role_key}")
            seen_role.add(role_key)
        return self


# --------------------------------------------------------------------------- #
# Expansion verifier ("wan quan zhan kai")                                     #
# --------------------------------------------------------------------------- #
def _plan_issue(
    code: str, message: str, *, node_id: str | None = None, pointer: str | None = None
) -> PlanValidationIssue:
    return PlanValidationIssue(code=code, message=message, node_id=node_id, pointer=pointer)


def _debate_seat_nodes(draft: PlanDraft, debate_id: str) -> tuple[PlanNode, ...]:
    return tuple(n for n in draft.nodes if n.debate_id == debate_id)


def verify_debate_expansion(
    draft: PlanDraft, debate: DebateCfg
) -> tuple[PlanValidationIssue, ...]:
    """The Task-9 full-expansion rule over Phase 1's structural checks.

    Requires the debate's seat nodes to cover exactly rounds ``1..R`` (``R <=
    max_rounds``), each round covering ``turn_order`` completely and in order. Emits
    only ``debate_incomplete_round`` (a round is missing a ``turn_order`` position),
    ``debate_turn_order_mismatch`` (a ``(round, turn)`` carries a role that is not
    ``turn_order[turn-1]``) and the defensive ``debate_rounds_exceed_max`` (a round
    beyond ``max_rounds`` — normally caught first by Phase 1's per-node
    ``debate_round_out_of_range``, so this only fires when Phase 1 is bypassed). Never
    re-checks duplicate ``(round, turn)`` / ``(round, role)`` — those are Phase 1's.
    """
    issues: list[PlanValidationIssue] = []
    seats = _debate_seat_nodes(draft, debate.id)
    turn_order = tuple(debate.turn_order)
    n_turns = len(turn_order)

    by_round: dict[int, dict[int, PlanNode]] = {}
    rounds_present: set[int] = set()
    for node in seats:
        r = node.debate_round or 0
        rounds_present.add(r)
        by_round.setdefault(r, {})[node.debate_turn or 0] = node

    # -- defensive: a round beyond the cap (Phase 1 owns this on the normal path) --
    over_rounds = sorted(r for r in rounds_present if r > debate.max_rounds)
    for r in over_rounds:
        issues.append(_plan_issue(
            "debate_rounds_exceed_max",
            f"debate {debate.id!r} declares round {r} beyond max_rounds {debate.max_rounds}",
            pointer=f"debates[{debate.id}].round[{r}]"))

    # the contiguous span 1..R the expansion must cover (ignoring the defensive
    # over-cap rounds, which Phase 1 already rejects).
    in_cap = [r for r in rounds_present if 1 <= r <= debate.max_rounds]
    if not in_cap:
        return tuple(sorted(issues, key=lambda i: i.sort_key))
    R = max(in_cap)

    for r in range(1, R + 1):
        turns = by_round.get(r, {})
        # every turn_order position must be present, in order, with the right seat.
        for i in range(1, n_turns + 1):
            node = turns.get(i)
            expected_role = turn_order[i - 1]
            if node is None:
                issues.append(_plan_issue(
                    "debate_incomplete_round",
                    f"debate {debate.id!r} round {r} is missing turn {i} "
                    f"(seat {expected_role!r})",
                    pointer=f"debates[{debate.id}].round[{r}].turn[{i}]"))
            elif node.round_role != expected_role:
                issues.append(_plan_issue(
                    "debate_turn_order_mismatch",
                    f"debate {debate.id!r} round {r} turn {i} carries role "
                    f"{node.round_role!r}, not turn_order seat {expected_role!r}",
                    node_id=node.id,
                    pointer=f"debates[{debate.id}].round[{r}].turn[{i}]"))
        # a turn index beyond turn_order is an out-of-order extra seat.
        for i in sorted(turns):
            if i < 1 or i > n_turns:
                issues.append(_plan_issue(
                    "debate_turn_order_mismatch",
                    f"debate {debate.id!r} round {r} has turn {i} outside turn_order "
                    f"(1..{n_turns})",
                    node_id=turns[i].id,
                    pointer=f"debates[{debate.id}].round[{r}].turn[{i}]"))

    return tuple(sorted(issues, key=lambda i: i.sort_key))


# --------------------------------------------------------------------------- #
# Invocation counting + runtime-support analyzer                              #
# --------------------------------------------------------------------------- #
def count_debate_invocations(draft: PlanDraft) -> NonNegativeInt:
    """The fully-expanded LLM invocation count over all debate seat nodes.

    Every seat x round is one LLM invocation reservation, so the count is simply the
    number of Plan nodes carrying a ``debate_id`` (each such node is one seat's turn).
    """
    return sum(1 for n in draft.nodes if n.debate_id is not None)


def _support_issue(
    code: str, model_path: str, explanation: str, *, node_id: str | None = None
) -> RuntimeSupportIssue:
    return RuntimeSupportIssue(
        code=code, model_path=model_path, explanation=explanation, node_id=node_id)


def _worker_execution_kind(catalog: "CatalogRuntime", worker_id: str) -> ExecutionKind | None:
    from guanlan_v2.orchestration.catalog_runtime import CatalogMaterialError

    try:
        return catalog.worker(worker_id).execution.kind
    except CatalogMaterialError:
        return None  # an unknown worker is a Phase-1 concern (report already invalid)


def analyze_debates(
    draft: PlanDraft, *, catalog: "CatalogRuntime", profile: "StaticRuntimeProfileV2"
) -> tuple[RuntimeSupportIssue, ...]:
    """Pure, pre-reservation runtime support for Lane-D debates.

    Validates, without touching any store or reserving budget: every debate seat
    node's worker is an LLM worker (``debate_seat_not_llm``); every debate's
    ``max_rounds`` is ``<= DEBATE_MAX_ROUNDS`` (``debate_rounds_exceed_profile_max``);
    the ``judge_node_id`` is a plan node that is **not** itself a seat of the same
    debate (``debate_judge_is_seat`` — Phase 1 already owns judge existence via
    ``debate_missing_judge``); and the requested LLM-invocation budget covers the
    fully expanded debate invocation count plus the non-debate LLM node count
    (``debate_budget_under_requested``) — the "budget validated against the fully
    expanded invocation count" rule, checked BEFORE reservation. ``profile`` is
    accepted for analyzer-signature parity and the ``supports_debates`` admission
    gate; the round cap is the reviewed Lane-D constant.
    """
    issues: list[RuntimeSupportIssue] = []
    node_by_id = {n.id: n for n in draft.nodes}

    # -- seat rosters must be LLM workers ---------------------------------- #
    for node in draft.nodes:
        if node.debate_id is None:
            continue
        kind = _worker_execution_kind(catalog, node.worker_id)
        if kind is not None and kind is not ExecutionKind.LLM:
            issues.append(_support_issue(
                "debate_seat_not_llm", "PlanNode.worker_id",
                f"debate seat node {node.id!r} worker {node.worker_id!r} execution kind "
                f"{kind.value!r} is not LLM; every debate seat is an LLM invocation",
                node_id=node.id))

    # -- per-debate: round cap + judge-not-a-seat -------------------------- #
    for debate in draft.debates:
        if debate.max_rounds > DEBATE_MAX_ROUNDS:
            issues.append(_support_issue(
                "debate_rounds_exceed_profile_max", "DebateCfg.max_rounds",
                f"debate {debate.id!r} max_rounds={debate.max_rounds} exceeds the reviewed "
                f"Lane-D cap DEBATE_MAX_ROUNDS={DEBATE_MAX_ROUNDS}"))
        judge = node_by_id.get(debate.judge_node_id)
        if judge is not None and judge.debate_id == debate.id:
            issues.append(_support_issue(
                "debate_judge_is_seat", "DebateCfg.judge_node_id",
                f"debate {debate.id!r} judge_node_id {debate.judge_node_id!r} is itself a seat "
                "node of the same debate; the judge must be a distinct plan node",
                node_id=debate.judge_node_id))

    # -- budget: request >= expanded debate invocations + non-debate LLM nodes -- #
    debate_invocations = count_debate_invocations(draft)
    non_debate_llm = 0
    for node in draft.nodes:
        if node.debate_id is not None:
            continue
        if _worker_execution_kind(catalog, node.worker_id) is ExecutionKind.LLM:
            non_debate_llm += 1
    required = debate_invocations + non_debate_llm
    if draft.budget_request_llm_invocations < required:
        issues.append(_support_issue(
            "debate_budget_under_requested", "PlanDraft.budget_request_llm_invocations",
            f"budget_request_llm_invocations={draft.budget_request_llm_invocations} is below the "
            f"fully expanded requirement {required} ({debate_invocations} debate seat invocations "
            f"+ {non_debate_llm} non-debate LLM nodes); rejected before reservation"))

    return tuple(sorted(issues, key=lambda i: i.sort_key))


# --------------------------------------------------------------------------- #
# Deterministic Plan-order fold reducer                                        #
# --------------------------------------------------------------------------- #
def fold_debate_messages(
    *,
    debate: DebateCfg,
    nodes: tuple[PlanNode, ...],
    committed: Mapping[LogicalId, "Artifact"],
) -> DebateTranscript:
    """Fold committed debate seat artifacts into a :class:`DebateTranscript`.

    Message order derives **only** from the Plan node debate fields sorted by
    ``(debate_round, debate_turn)`` — never thread/commit completion order. Each
    seat node's committed Artifact (keyed by node id in ``committed``) supplies the
    ``artifact_id`` / ``created_at``. A node absent from ``committed`` contributes no
    message (the fold is over the actually-committed producers the barrier hands it).
    """
    seats = [n for n in nodes if n.debate_id == debate.id and n.id in committed]
    seats.sort(key=lambda n: (n.debate_round or 0, n.debate_turn or 0))
    messages = tuple(
        DebateMessage(
            debate_id=debate.id,
            round=n.debate_round,
            turn=n.debate_turn,
            role=n.round_role,
            artifact_id=committed[n.id].artifact_id,
            created_at=committed[n.id].created_at,
        )
        for n in seats
    )
    return DebateTranscript(
        debate_id=debate.id, max_rounds=debate.max_rounds, messages=messages
    )


def debate_transcript_reducer_handler(
    *, debate: DebateCfg, nodes: tuple[PlanNode, ...]
):
    """The concrete ``DEBATE_TRANSCRIPT_REDUCER_ID`` handler for the Task-8
    :func:`pool.fold_reducer_producers` seam.

    Returns a pure ``handler(ordered_producers) -> DebateTranscript`` bound to this
    debate + its seat nodes. The Task-8 seam sorts the producers by
    ``producer_node_id`` before calling this handler; the handler re-derives the
    transcript from **Plan order** and therefore ignores the incoming arrival order,
    so a shuffled completion order yields a byte-identical transcript (the exit-gate
    determinism property).
    """
    seat_nodes = tuple(nodes)

    def handler(ordered_producers: tuple["Artifact", ...]) -> DebateTranscript:
        committed = {a.producer_node_id: a for a in ordered_producers}
        return fold_debate_messages(debate=debate, nodes=seat_nodes, committed=committed)

    return handler


# --------------------------------------------------------------------------- #
# Persist-then-publish over the real Phase-2 EventStore                         #
# --------------------------------------------------------------------------- #
def debate_message_key(
    *,
    run_id: str,
    plan_digest: str,
    debate_id: str,
    round_: int,
    turn: int,
    role: str,
) -> DigestHex:
    """The domain-tagged idempotency key over exactly
    ``{domain, run_id, plan_digest, debate_id, round, turn, role}``.

    In a Phase-1-valid expansion ``role`` is functionally determined by
    ``(round, turn)`` (``turn_order`` is a fixed permutation), so this enforces the
    "one immutable message per ``(debate_id, round, turn)``" rule; a second, different
    message under the same identity collides at the event store.
    """
    return content_digest(
        {
            "domain": DEBATE_MESSAGE_KEY_DOMAIN,
            "run_id": run_id,
            "plan_digest": plan_digest,
            "debate_id": debate_id,
            "round": round_,
            "turn": turn,
            "role": role,
        }
    )


def publish_debate_message(
    stores: RuntimeStores,
    *,
    registry_digest: str,
    run_id: str,
    plan_digest: str,
    debate_id: str,
    node: PlanNode,
    artifact: "Artifact",
) -> "RunEvent":
    """Persist one committed debate seat as a ``DebateMessage`` payload + public
    ``DEBATE_MESSAGE_PUBLISHED`` event in a single unit of work (persist-then-publish).

    Idempotent on an identical retry (same key + same content returns the same event);
    a different message under the same ``(debate_id, round, turn, role)`` raises
    :class:`~guanlan_v2.orchestration.eventstore.IdempotencyConflict`. The event is
    public (``partition="main"``) so the store assigns a ``visible_seq``.
    """
    message = DebateMessage(
        debate_id=debate_id,
        round=node.debate_round,
        turn=node.debate_turn,
        role=node.round_role,
        artifact_id=artifact.artifact_id,
        created_at=artifact.created_at,
    )
    key = debate_message_key(
        run_id=run_id, plan_digest=plan_digest, debate_id=debate_id,
        round_=node.debate_round, turn=node.debate_turn, role=node.round_role,
    )
    batch = RuntimeBatch(
        idempotency_key=f"debate-msg:{key}",
        payload_puts=(
            PayloadPutCommand(
                staged_key=StagedPayloadKey(key="debate_message"),
                schema_ref=DEBATE_MESSAGE_SCHEMA_REF,
                namespace="main",
                payload_template=dict(message),
                registry_digest=registry_digest,
                idempotency_key=f"{key}:payload",
            ),
        ),
        event_appends=(
            EventAppendCommand(
                run_id=run_id,
                partition="main",
                event_type=DEBATE_MESSAGE_EVENT_TYPE,
                payload_schema_ref=DEBATE_MESSAGE_SCHEMA_REF,
                payload_target=StagedPayloadKey(key="debate_message"),
                registry_digest=registry_digest,
                idempotency_key=key,
                plan_digest=plan_digest,
                correlation_id=artifact.artifact_id,
            ),
        ),
    )
    result = stores.unit_of_work.commit(batch)
    return result.events[0]


def replay_debate_transcript(
    stores: RuntimeStores, *, run_id: str, debate: DebateCfg, max_rounds: int
) -> DebateTranscript:
    """Rebuild a debate's :class:`DebateTranscript` from its persisted, visible
    ``DEBATE_MESSAGE_PUBLISHED`` events only.

    Reads the run's ``main`` visible stream, loads each ``DebateMessage`` payload,
    keeps the ones for ``debate.id`` and folds them by ``(round, turn)`` — yielding a
    transcript byte-identical to :func:`fold_debate_messages` over the same committed
    artifacts (event-sourced view consistency), regardless of the publish order.
    """
    from guanlan_v2.orchestration.events import EventType

    messages: list[DebateMessage] = []
    for event in stores.events.visible(run_id, "main"):
        if event.event_type is not EventType.DEBATE_MESSAGE_PUBLISHED:
            continue
        message = stores.payloads.get(
            event.payload_ref, expected_schema_ref=DEBATE_MESSAGE_SCHEMA_REF
        )
        if message.debate_id == debate.id:
            messages.append(message)
    messages.sort(key=lambda m: (m.round, m.turn))
    return DebateTranscript(
        debate_id=debate.id, max_rounds=max_rounds, messages=tuple(messages)
    )
