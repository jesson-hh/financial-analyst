"""Event-sourced :class:`BudgetLedger` over the Phase 1 budget contracts (Task 1).

Phase 1 froze the budget *values* — :class:`RunBudget` (a run's maxima + immutable
running reserved totals) and :class:`BudgetReservation` (one atomic allocation
binding request / candidate-plan digest / ledger identity + exact amounts). Phase 1
implements **no** ledger, mutation or accounting behavior. This module adds it, and
does so as an *event-sourced* ledger: the authority is the fold over an append-only
stream of budget events, never a mutable reservation dict.

Design (load-bearing — Task 2 and Task 6 build on it):

* a **closed** :class:`BudgetTransitionCommand` (``reserve_plan | reserve_node |
  settle | release``) carries no caller-selected reservation id, cursor or
  timestamp — only the semantic intent + an idempotency key;
* an injected append-only :class:`BudgetEventSink` assigns the service reservation
  id / event id / seq and stamps time via an :class:`AuthoritativeClock`, then
  commits the typed :class:`BudgetEvent`. A fake sink drives this task; Task 2
  supplies the production registry-validated Payload/Event store and may validate
  and commit the *exact same* command inside a wider ``RuntimeUnitOfWork`` — it
  reuses :func:`fold_budget_events` / :func:`validate_budget_command` and the
  command types, never an arbitrary callback;
* every :class:`BudgetReservation` the ledger returns is built through its Phase 1
  validated constructor — the fold never mutates a frozen reservation with an
  unvalidated ``model_copy(update=...)``.

Accounting model (two-level pool): a **plan** reservation draws from the run
budget; **node** reservations are children that draw from *within* their plan's
pool (so nodes never double-count against the run). A reservation's *effective
hold* is its reserved amount while ``reserved``, its settled *actual* while
``settled`` (concurrency is a transient slot, freed on settle) and zero while
``released``. Outstanding + settled-actual holds may never exceed the run budget
(plan scope) or the plan reservation (node scope).

This module owns **no** :class:`~guanlan_v2.orchestration.digest.ContractModel`:
its strict models are plain runtime value/command records, deliberately not part of
the Phase 1 registered-contract surface and not exported from the Phase 1
``__init__``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from guanlan_v2.orchestration.context import BudgetReservation, RunBudget
from guanlan_v2.orchestration.digest import (
    DigestHex,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
)

__all__ = [
    "BudgetError",
    "BudgetExceeded",
    "InvalidBudgetTransition",
    "IdempotencyConflict",
    "BudgetRequest",
    "AvailableBudget",
    "ReservePlanArgs",
    "ReserveNodeArgs",
    "SettleArgs",
    "ReleaseArgs",
    "BudgetOperation",
    "BudgetTransitionCommand",
    "BudgetEvent",
    "BudgetEventSink",
    "LedgerState",
    "fold_budget_events",
    "validate_budget_command",
    "BudgetLedger",
]


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #
class BudgetError(Exception):
    """Base for every budget-ledger failure."""


class BudgetExceeded(BudgetError):
    """A reservation or settlement would exceed the run or plan budget."""


class InvalidBudgetTransition(BudgetError):
    """A transition is illegal for the folded state (unknown/settled/released
    target, inactive parent plan, duplicate active plan…)."""


class IdempotencyConflict(BudgetError):
    """The same idempotency key was reused with different semantic content.

    Task 2's production event store owns the canonical persist-then-publish
    idempotency contract; this ledger enforces the same rule over the injected
    sink so the fake and production paths agree.
    """


# --------------------------------------------------------------------------- #
# Strict runtime base (NOT a ContractModel — see module docstring)            #
# --------------------------------------------------------------------------- #
class _StrictModel(BaseModel):
    """Strict, frozen, closed runtime value base.

    Mirrors the Phase 1 strict config (``extra='forbid'``, ``strict=True``,
    ``frozen=True``) — rejecting unknown fields and lax coercion (``bool`` is not a
    number, ``"5"`` is not an int) — without inheriting the registered-contract
    identity of :class:`~guanlan_v2.orchestration.digest.ContractModel`.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- #
# Value objects                                                               #
# --------------------------------------------------------------------------- #
class BudgetRequest(_StrictModel):
    """The exact token / invocation / concurrency amounts a plan reserves."""

    tokens: NonNegativeInt
    llm_invocations: NonNegativeInt
    concurrency: PositiveInt


class AvailableBudget(_StrictModel):
    """Remaining run-level budget after outstanding + settled plan holds."""

    tokens: int
    llm_invocations: int
    concurrency: int


# --------------------------------------------------------------------------- #
# Closed transition command                                                   #
# --------------------------------------------------------------------------- #
# NOTE: none of the arg models carry a reservation id for the reservation being
# *created* (reserve_*), a cursor/seq or a timestamp — those are service-assigned
# by the sink. ``reserve_node`` / ``settle`` / ``release`` carry a *reference* to an
# already service-assigned reservation id, which is not a caller-selected new id.
class ReservePlanArgs(_StrictModel):
    request_id: NonEmptyStr
    candidate_plan_digest: DigestHex
    reserved_tokens: NonNegativeInt
    reserved_llm_invocations: NonNegativeInt
    reserved_concurrency: PositiveInt


class ReserveNodeArgs(_StrictModel):
    plan_reservation_id: NonEmptyStr  # reference to the parent plan reservation
    node_id: NonEmptyStr
    attempt: PositiveInt
    reserved_tokens: NonNegativeInt
    reserved_llm_invocations: NonNegativeInt
    reserved_concurrency: PositiveInt


class SettleArgs(_StrictModel):
    reservation_id: NonEmptyStr  # reference to the reservation being settled
    actual_tokens: NonNegativeInt
    actual_llm_invocations: NonNegativeInt


class ReleaseArgs(_StrictModel):
    reservation_id: NonEmptyStr  # reference to the reservation being released
    reason: NonEmptyStr


BudgetOperation = Literal["reserve_plan", "reserve_node", "settle", "release"]

#: The closed operation -> args-type matrix.
_OP_ARGS: dict[str, type[_StrictModel]] = {
    "reserve_plan": ReservePlanArgs,
    "reserve_node": ReserveNodeArgs,
    "settle": SettleArgs,
    "release": ReleaseArgs,
}


class BudgetTransitionCommand(_StrictModel):
    """A pure, closed budget-transition intent.

    ``semantic_args`` is one of the four arg records, matched to ``operation`` by a
    validator so an operation can never carry the wrong payload. It is validated
    *against a folded state* (see :func:`validate_budget_command`) with no side
    effect; the sink assigns identity/time and commits the resulting event.
    """

    operation: BudgetOperation
    semantic_args: ReservePlanArgs | ReserveNodeArgs | SettleArgs | ReleaseArgs
    idempotency_key: NonEmptyStr

    @model_validator(mode="after")
    def _operation_matches_args(self) -> "BudgetTransitionCommand":
        expected = _OP_ARGS[self.operation]
        if type(self.semantic_args) is not expected:
            raise ValueError(
                f"operation {self.operation!r} requires {expected.__name__}, "
                f"got {type(self.semantic_args).__name__}"
            )
        return self


# --------------------------------------------------------------------------- #
# The typed budget event (what the sink commits and the ledger folds)         #
# --------------------------------------------------------------------------- #
class BudgetEvent(_StrictModel):
    """One committed budget transition: the command plus the sink-assigned service
    identity (``event_id`` / ``seq`` / ``reservation_id``), the run/ledger identity
    and the stamped ``occurred_at`` time."""

    seq: PositiveInt
    event_id: NonEmptyStr
    run_id: NonEmptyStr
    ledger_id: NonEmptyStr
    reservation_id: NonEmptyStr
    occurred_at: UtcDateTime
    command: BudgetTransitionCommand


@runtime_checkable
class BudgetEventSink(Protocol):
    """Append-only sink the ledger commits budget events through.

    The sink owns service-id / seq / time assignment and idempotent storage; it
    understands nothing about budget semantics beyond command equality. Task 2's
    production Payload/Event store implements this same shape.
    """

    def budget_events(self) -> tuple[BudgetEvent, ...]:
        """Every committed budget event, in commit order."""
        ...

    def find_by_idempotency_key(self, key: str) -> BudgetEvent | None:
        """The event previously committed under ``key``, or ``None``."""
        ...

    def append(self, command: BudgetTransitionCommand) -> BudgetEvent:
        """Assign identity/time and commit ``command`` as a new event."""
        ...


# --------------------------------------------------------------------------- #
# Folded state                                                                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LedgerState:
    """The immutable projection produced by folding the budget-event stream.

    * ``reservations`` — reservation id -> current :class:`BudgetReservation`;
    * ``parent_of`` — node reservation id -> its plan reservation id (a
      :class:`BudgetReservation` carries no parent field, so the link lives here);
    * ``active_plan`` — ``(request_id, candidate_plan_digest)`` -> the active plan
      reservation id (present only while that plan is ``reserved``).
    """

    reservations: dict[str, BudgetReservation]
    parent_of: dict[str, str]
    active_plan: dict[tuple[str, str], str]


def _build_reservation(
    *,
    reservation_id: str,
    ledger_id: str,
    run_id: str,
    request_id: str,
    candidate_plan_digest: str,
    scope_type: str,
    scope_id: str,
    reserved: tuple[int, int, int],
    actual: tuple[int, int, int] = (0, 0, 0),
    status: str,
    reserved_at,
    settled_at=None,
) -> BudgetReservation:
    """Construct a Phase 1 :class:`BudgetReservation` through its validated path."""
    return BudgetReservation(
        reservation_id=reservation_id,
        ledger_id=ledger_id,
        run_id=run_id,
        request_id=request_id,
        candidate_plan_digest=candidate_plan_digest,
        scope_type=scope_type,
        scope_id=scope_id,
        reserved_tokens=reserved[0],
        reserved_llm_invocations=reserved[1],
        reserved_concurrency=reserved[2],
        actual_tokens=actual[0],
        actual_llm_invocations=actual[1],
        actual_concurrency=actual[2],
        status=status,
        reserved_at=reserved_at,
        settled_at=settled_at,
    )


def fold_budget_events(events: tuple[BudgetEvent, ...]) -> LedgerState:
    """Rebuild the authoritative ledger state from the ordered budget-event stream.

    Pure and deterministic: the same event sequence always folds to the same
    reservations, parent links and active-plan index. Task 2 reuses this exact
    fold when replaying its production store.
    """
    reservations: dict[str, BudgetReservation] = {}
    parent_of: dict[str, str] = {}
    active_plan: dict[tuple[str, str], str] = {}

    for ev in sorted(events, key=lambda e: e.seq):
        cmd = ev.command
        args = cmd.semantic_args
        if cmd.operation == "reserve_plan":
            res = _build_reservation(
                reservation_id=ev.reservation_id,
                ledger_id=ev.ledger_id,
                run_id=ev.run_id,
                request_id=args.request_id,
                candidate_plan_digest=args.candidate_plan_digest,
                scope_type="plan",
                scope_id=args.candidate_plan_digest,
                reserved=(
                    args.reserved_tokens,
                    args.reserved_llm_invocations,
                    args.reserved_concurrency,
                ),
                status="reserved",
                reserved_at=ev.occurred_at,
            )
            reservations[res.reservation_id] = res
            active_plan[(args.request_id, args.candidate_plan_digest)] = res.reservation_id
        elif cmd.operation == "reserve_node":
            parent = reservations[args.plan_reservation_id]
            res = _build_reservation(
                reservation_id=ev.reservation_id,
                ledger_id=ev.ledger_id,
                run_id=ev.run_id,
                request_id=parent.request_id,
                candidate_plan_digest=parent.candidate_plan_digest,
                scope_type="node",
                scope_id=args.node_id,
                reserved=(
                    args.reserved_tokens,
                    args.reserved_llm_invocations,
                    args.reserved_concurrency,
                ),
                status="reserved",
                reserved_at=ev.occurred_at,
            )
            reservations[res.reservation_id] = res
            parent_of[res.reservation_id] = args.plan_reservation_id
        elif cmd.operation == "settle":
            prev = reservations[args.reservation_id]
            reservations[prev.reservation_id] = _build_reservation(
                reservation_id=prev.reservation_id,
                ledger_id=prev.ledger_id,
                run_id=prev.run_id,
                request_id=prev.request_id,
                candidate_plan_digest=prev.candidate_plan_digest,
                scope_type=prev.scope_type,
                scope_id=prev.scope_id,
                reserved=(
                    prev.reserved_tokens,
                    prev.reserved_llm_invocations,
                    prev.reserved_concurrency,
                ),
                actual=(args.actual_tokens, args.actual_llm_invocations, 0),
                status="settled",
                reserved_at=prev.reserved_at,
                settled_at=ev.occurred_at,
            )
            if prev.scope_type == "plan":
                active_plan.pop((prev.request_id, prev.candidate_plan_digest), None)
        elif cmd.operation == "release":
            prev = reservations[args.reservation_id]
            reservations[prev.reservation_id] = _build_reservation(
                reservation_id=prev.reservation_id,
                ledger_id=prev.ledger_id,
                run_id=prev.run_id,
                request_id=prev.request_id,
                candidate_plan_digest=prev.candidate_plan_digest,
                scope_type=prev.scope_type,
                scope_id=prev.scope_id,
                reserved=(
                    prev.reserved_tokens,
                    prev.reserved_llm_invocations,
                    prev.reserved_concurrency,
                ),
                actual=(0, 0, 0),
                status="released",
                reserved_at=prev.reserved_at,
                settled_at=ev.occurred_at,
            )
            if prev.scope_type == "plan":
                active_plan.pop((prev.request_id, prev.candidate_plan_digest), None)

    return LedgerState(
        reservations=reservations, parent_of=parent_of, active_plan=active_plan
    )


# --------------------------------------------------------------------------- #
# Availability + pure validation                                              #
# --------------------------------------------------------------------------- #
def _hold(res: BudgetReservation) -> tuple[int, int, int]:
    """A reservation's effective (tokens, llm, concurrency) hold for its status."""
    if res.status == "reserved":
        return (res.reserved_tokens, res.reserved_llm_invocations, res.reserved_concurrency)
    if res.status == "settled":
        # settled reservations hold their actual usage; a concurrency slot is
        # transient and freed the moment the node finishes.
        return (res.actual_tokens, res.actual_llm_invocations, 0)
    return (0, 0, 0)  # released


def compute_available(state: LedgerState, run_budget: RunBudget) -> AvailableBudget:
    """Run-level availability = run maxima minus every *plan* reservation's hold.

    Node reservations draw from within their plan's pool, so they never subtract
    twice at the run level.
    """
    ut = ul = uc = 0
    for res in state.reservations.values():
        if res.scope_type == "plan":
            t, l, c = _hold(res)
            ut += t
            ul += l
            uc += c
    return AvailableBudget(
        tokens=run_budget.max_tokens - ut,
        llm_invocations=run_budget.max_llm_invocations - ul,
        concurrency=run_budget.max_concurrency - uc,
    )


def _plan_available(state: LedgerState, plan_reservation_id: str) -> tuple[int, int, int]:
    """A plan reservation's remaining pool for its child node reservations."""
    plan = state.reservations[plan_reservation_id]
    ut = ul = uc = 0
    for res_id, parent_id in state.parent_of.items():
        if parent_id == plan_reservation_id:
            t, l, c = _hold(state.reservations[res_id])
            ut += t
            ul += l
            uc += c
    return (
        plan.reserved_tokens - ut,
        plan.reserved_llm_invocations - ul,
        plan.reserved_concurrency - uc,
    )


def _has_active_children(state: LedgerState, plan_reservation_id: str) -> bool:
    return any(
        parent_id == plan_reservation_id
        and state.reservations[res_id].status == "reserved"
        for res_id, parent_id in state.parent_of.items()
    )


def validate_budget_command(
    command: BudgetTransitionCommand,
    state: LedgerState,
    *,
    run_budget: RunBudget,
) -> None:
    """Purely validate ``command`` against a folded ``state`` — no side effect.

    Deterministic over the same ``(command, state, run_budget)`` triple. Raises
    :class:`BudgetExceeded` when a reservation/settlement would breach a limit and
    :class:`InvalidBudgetTransition` for an illegal transition, *before* any event
    is appended. Task 2 calls this same validator inside its unit of work.
    """
    op = command.operation
    args = command.semantic_args

    if op == "reserve_plan":
        if (args.request_id, args.candidate_plan_digest) in state.active_plan:
            raise InvalidBudgetTransition(
                "an active plan reservation already exists for this "
                "request_id/candidate_plan_digest"
            )
        avail = compute_available(state, run_budget)
        if (
            args.reserved_tokens > avail.tokens
            or args.reserved_llm_invocations > avail.llm_invocations
            or args.reserved_concurrency > avail.concurrency
        ):
            raise BudgetExceeded("plan reservation exceeds the run budget")

    elif op == "reserve_node":
        plan = state.reservations.get(args.plan_reservation_id)
        if plan is None or plan.scope_type != "plan":
            raise InvalidBudgetTransition(
                "reserve_node references an unknown plan reservation"
            )
        if plan.status != "reserved":
            raise InvalidBudgetTransition(
                "reserve_node parent plan is not active (child of inactive plan)"
            )
        at, al, ac = _plan_available(state, args.plan_reservation_id)
        if (
            args.reserved_tokens > at
            or args.reserved_llm_invocations > al
            or args.reserved_concurrency > ac
        ):
            raise BudgetExceeded("node reservation exceeds the plan reservation")

    elif op == "settle":
        res = state.reservations.get(args.reservation_id)
        if res is None:
            raise InvalidBudgetTransition("settle references an unknown reservation")
        if res.status != "reserved":
            raise InvalidBudgetTransition(f"cannot settle a {res.status!r} reservation")
        if (
            args.actual_tokens > res.reserved_tokens
            or args.actual_llm_invocations > res.reserved_llm_invocations
        ):
            raise BudgetExceeded("settled actual usage exceeds the reservation")

    elif op == "release":
        res = state.reservations.get(args.reservation_id)
        if res is None:
            raise InvalidBudgetTransition("release references an unknown reservation")
        if res.status != "reserved":
            raise InvalidBudgetTransition(f"cannot release a {res.status!r} reservation")
        if res.scope_type == "plan" and _has_active_children(state, args.reservation_id):
            raise InvalidBudgetTransition(
                "cannot release a plan with active child reservations"
            )

    else:  # pragma: no cover - the Literal type already closes the operation set
        raise InvalidBudgetTransition(f"unknown budget operation {op!r}")


def _same_semantics(a: BudgetTransitionCommand, b: BudgetTransitionCommand) -> bool:
    return a.operation == b.operation and a.semantic_args == b.semantic_args


# --------------------------------------------------------------------------- #
# The ledger                                                                  #
# --------------------------------------------------------------------------- #
class BudgetLedger:
    """Event-sourced budget ledger over an injected append-only sink.

    Holds no mutable reservation dict: every read folds the sink's event stream,
    and every write validates a closed command against the freshly folded head
    before the sink commits it — so two commands validated against the same head
    can never both over-reserve (the second re-folds and sees the first's effect).
    """

    def __init__(self, *, sink: BudgetEventSink, run_budget: RunBudget) -> None:
        self._sink = sink
        self._run_budget = run_budget

    # -- fold-on-read helpers ------------------------------------------------ #
    def _state(self) -> LedgerState:
        return fold_budget_events(self._sink.budget_events())

    def _apply(self, command: BudgetTransitionCommand) -> BudgetEvent:
        """Idempotency -> validate-against-live-head -> append, atomically."""
        existing = self._sink.find_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            if not _same_semantics(existing.command, command):
                raise IdempotencyConflict(
                    f"idempotency key {command.idempotency_key!r} was reused with "
                    "different semantic content"
                )
            return existing  # idempotent replay: no re-validation, no new event
        validate_budget_command(command, self._state(), run_budget=self._run_budget)
        return self._sink.append(command)

    # -- public reservation API --------------------------------------------- #
    def reserve_plan(
        self,
        *,
        request_id: str,
        candidate_plan_digest: str,
        budget_request: BudgetRequest,
        idempotency_key: str,
    ) -> BudgetReservation:
        command = BudgetTransitionCommand(
            operation="reserve_plan",
            semantic_args=ReservePlanArgs(
                request_id=request_id,
                candidate_plan_digest=candidate_plan_digest,
                reserved_tokens=budget_request.tokens,
                reserved_llm_invocations=budget_request.llm_invocations,
                reserved_concurrency=budget_request.concurrency,
            ),
            idempotency_key=idempotency_key,
        )
        event = self._apply(command)
        return self._state().reservations[event.reservation_id]

    def reserve_node(
        self,
        *,
        plan_reservation_id: str,
        node_id: str,
        attempt: int,
        tokens: int,
        llm_invocations: int,
        concurrency: int,
        idempotency_key: str,
    ) -> BudgetReservation:
        command = BudgetTransitionCommand(
            operation="reserve_node",
            semantic_args=ReserveNodeArgs(
                plan_reservation_id=plan_reservation_id,
                node_id=node_id,
                attempt=attempt,
                reserved_tokens=tokens,
                reserved_llm_invocations=llm_invocations,
                reserved_concurrency=concurrency,
            ),
            idempotency_key=idempotency_key,
        )
        event = self._apply(command)
        return self._state().reservations[event.reservation_id]

    def settle(
        self,
        reservation_id: str,
        *,
        actual_tokens: int,
        actual_llm_invocations: int,
        idempotency_key: str,
    ) -> BudgetReservation:
        command = BudgetTransitionCommand(
            operation="settle",
            semantic_args=SettleArgs(
                reservation_id=reservation_id,
                actual_tokens=actual_tokens,
                actual_llm_invocations=actual_llm_invocations,
            ),
            idempotency_key=idempotency_key,
        )
        event = self._apply(command)
        return self._state().reservations[event.reservation_id]

    def release(
        self,
        reservation_id: str,
        *,
        reason: str,
        idempotency_key: str,
    ) -> BudgetReservation:
        command = BudgetTransitionCommand(
            operation="release",
            semantic_args=ReleaseArgs(reservation_id=reservation_id, reason=reason),
            idempotency_key=idempotency_key,
        )
        event = self._apply(command)
        return self._state().reservations[event.reservation_id]

    # -- reads --------------------------------------------------------------- #
    def get(self, reservation_id: str) -> BudgetReservation | None:
        return self._state().reservations.get(reservation_id)

    def get_active_plan(
        self, request_id: str, candidate_plan_digest: str
    ) -> BudgetReservation | None:
        state = self._state()
        res_id = state.active_plan.get((request_id, candidate_plan_digest))
        return state.reservations.get(res_id) if res_id is not None else None

    def available(self) -> AvailableBudget:
        return compute_available(self._state(), self._run_budget)

    def replay(self) -> LedgerState:
        """Return the folded :class:`LedgerState` — the same fold any consumer gets,
        proving replay reproduces availability and the active-reservation index."""
        return self._state()
