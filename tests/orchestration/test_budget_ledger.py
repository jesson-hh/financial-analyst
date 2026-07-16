# -*- coding: utf-8 -*-
"""Phase 2 · Task 1 — event-sourced ``BudgetLedger`` (``budget.py``).

Written test-first (RED until ``budget.py`` exists). The ledger's authority is the
*fold over an append-only stream of budget events*, never a mutable reservation
dict; a closed :class:`BudgetTransitionCommand` (``reserve_plan | reserve_node |
settle | release``) carries no caller-selected reservation id / cursor / timestamp;
an injected :class:`BudgetEventSink` assigns those service ids/time and commits the
typed event; each new Phase 1 :class:`BudgetReservation` is built through its
validated constructor (never an unvalidated ``model_copy``).

Reviewed invariants under test (brief Step 1):

1. a plan reservation binds ledger id / request id / candidate plan digest and the
   exact token/invocation/concurrency request;
2. every node reservation is a child of the active plan reservation and carries the
   same request/candidate/ledger/run identity;
3. total outstanding + settled actual never exceeds the run or plan reservation;
4. actual values cannot exceed the reservation; bool/negative fail through Phase 1
   strict types;
5. deterministic nodes reserve zero LLM invocations; LLM nodes reserve per attempt;
6. reserve/settle/release is atomic with exactly one typed budget event;
7. identical idempotent calls return the same record; conflicting calls fail;
8. invalid transitions (double settle, settle after release, child of inactive
   plan) fail without appending an accepted event;
9. approval rejection / cancellation releases unused plan/node reservations;
10. replay reproduces availability and the active-reservation index;
plus: callers cannot select reservation id/cursor/time, the closed command
validator is deterministic over the same folded state, and same-head concurrent
commands cannot over-reserve.

Run: ``python -m pytest tests/orchestration/test_budget_ledger.py -v``
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.context import BudgetReservation, RunBudget
from guanlan_v2.orchestration.runtime_clock import clock_now
from guanlan_v2.orchestration.budget import (
    AvailableBudget,
    BudgetEvent,
    BudgetEventSink,
    BudgetExceeded,
    BudgetLedger,
    BudgetRequest,
    BudgetTransitionCommand,
    IdempotencyConflict,
    InvalidBudgetTransition,
    LedgerState,
    ReleaseArgs,
    ReserveNodeArgs,
    ReservePlanArgs,
    SettleArgs,
    fold_budget_events,
    validate_budget_command,
)

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64


# --------------------------------------------------------------------------- #
# deterministic test doubles                                                  #
# --------------------------------------------------------------------------- #
class AdvancingClock:
    """Aware-UTC clock advancing one second per read (the brief's fixed/advancing
    deterministic implementation — no wall clock in a runtime test)."""

    def __init__(self, start: datetime | None = None, step: timedelta = timedelta(seconds=1)):
        self._next = start or datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
        self._step = step

    def now(self) -> datetime:
        cur = self._next
        self._next = cur + self._step
        return cur


class NaiveClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 16, 9, 0)  # naive — rejected at stamp time


class FakeBudgetEventSink:
    """In-memory append-only budget-event sink (Task 2 supplies the production
    store). Assigns the service reservation id, event id, seq and stamps time via
    the injected :class:`AuthoritativeClock` — the ledger never selects any of them.
    """

    def __init__(self, *, run_id: str, ledger_id: str, clock):
        self._run_id = run_id
        self._ledger_id = ledger_id
        self._clock = clock
        self._events: list[BudgetEvent] = []
        self._by_key: dict[str, BudgetEvent] = {}
        self._seq = 0
        self._res_seq = 0

    def budget_events(self) -> tuple[BudgetEvent, ...]:
        return tuple(self._events)

    def find_by_idempotency_key(self, key: str) -> BudgetEvent | None:
        return self._by_key.get(key)

    def append(self, command: BudgetTransitionCommand) -> BudgetEvent:
        self._seq += 1
        occurred_at = clock_now(self._clock)  # rejects a naive-returning clock
        if command.operation in ("reserve_plan", "reserve_node"):
            self._res_seq += 1
            reservation_id = f"res-{self._res_seq}"
        else:
            reservation_id = command.semantic_args.reservation_id
        event = BudgetEvent(
            seq=self._seq,
            event_id=f"be-{self._seq}",
            run_id=self._run_id,
            ledger_id=self._ledger_id,
            reservation_id=reservation_id,
            occurred_at=occurred_at,
            command=command,
        )
        self._events.append(event)
        self._by_key[command.idempotency_key] = event
        return event


def _run_budget(**over) -> RunBudget:
    base = dict(
        ledger_id="ledger-1",
        max_tokens=5000,
        max_llm_invocations=40,
        max_concurrency=8,
    )
    base.update(over)
    return RunBudget(**base)


def _ledger(run_budget: RunBudget | None = None, clock=None):
    rb = run_budget or _run_budget()
    clk = clock or AdvancingClock()
    sink = FakeBudgetEventSink(run_id="run-1", ledger_id=rb.ledger_id, clock=clk)
    return BudgetLedger(sink=sink, run_budget=rb), sink


def _reserve_plan(ledger, *, request_id="req-1", candidate=DA, tokens=1000,
                  llm=10, concurrency=2, idem="idem-plan-1") -> BudgetReservation:
    return ledger.reserve_plan(
        request_id=request_id,
        candidate_plan_digest=candidate,
        budget_request=BudgetRequest(tokens=tokens, llm_invocations=llm, concurrency=concurrency),
        idempotency_key=idem,
    )


# --------------------------------------------------------------------------- #
# 1. plan reservation binding                                                 #
# --------------------------------------------------------------------------- #
def test_reserve_plan_binds_identity_and_exact_amounts():
    ledger, sink = _ledger()
    r = _reserve_plan(ledger, tokens=1200, llm=7, concurrency=3)
    assert isinstance(r, BudgetReservation)
    assert r.ledger_id == "ledger-1"
    assert r.run_id == "run-1"
    assert r.request_id == "req-1"
    assert r.candidate_plan_digest == DA
    assert r.scope_type == "plan"
    assert (r.reserved_tokens, r.reserved_llm_invocations, r.reserved_concurrency) == (1200, 7, 3)
    assert r.status == "reserved"
    # exactly one budget event was appended atomically.
    assert len(sink.budget_events()) == 1


def test_reserve_plan_reservation_id_and_time_are_service_assigned():
    ledger, sink = _ledger()
    r = _reserve_plan(ledger)
    ev = sink.budget_events()[0]
    assert r.reservation_id == ev.reservation_id  # the sink assigned it
    assert r.reserved_at == ev.occurred_at        # the sink stamped it


# --------------------------------------------------------------------------- #
# 2. node reservation is a child carrying the plan identity                   #
# --------------------------------------------------------------------------- #
def test_reserve_node_is_child_of_active_plan_with_same_identity():
    ledger, sink = _ledger()
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=4)
    node = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id,
        node_id="node.research",
        attempt=1,
        tokens=400,
        llm_invocations=2,
        concurrency=1,
        idempotency_key="idem-node-1",
    )
    assert node.scope_type == "node"
    assert node.scope_id == "node.research"
    # inherits the plan's request / candidate / ledger / run identity (invariant 2).
    assert node.request_id == plan.request_id
    assert node.candidate_plan_digest == plan.candidate_plan_digest
    assert node.ledger_id == plan.ledger_id
    assert node.run_id == plan.run_id
    # the fold records the parent link (BudgetReservation itself carries no parent field).
    assert ledger.replay().parent_of[node.reservation_id] == plan.reservation_id
    assert len(sink.budget_events()) == 2


def test_reserve_node_under_unknown_plan_is_invalid():
    ledger, _ = _ledger()
    with pytest.raises(InvalidBudgetTransition):
        ledger.reserve_node(
            plan_reservation_id="res-999", node_id="n", attempt=1,
            tokens=1, llm_invocations=0, concurrency=1, idempotency_key="idem-x",
        )


# --------------------------------------------------------------------------- #
# 3. run + plan availability never exceeded                                   #
# --------------------------------------------------------------------------- #
def test_available_reflects_outstanding_plan_reservations():
    ledger, _ = _ledger(_run_budget(max_tokens=5000, max_llm_invocations=40, max_concurrency=8))
    assert ledger.available() == AvailableBudget(tokens=5000, llm_invocations=40, concurrency=8)
    _reserve_plan(ledger, tokens=1000, llm=10, concurrency=3)
    assert ledger.available() == AvailableBudget(tokens=4000, llm_invocations=30, concurrency=5)


def test_plan_over_reservation_against_run_budget_is_rejected():
    ledger, sink = _ledger(_run_budget(max_tokens=1000))
    with pytest.raises(BudgetExceeded):
        _reserve_plan(ledger, tokens=1001, llm=1, concurrency=1)
    assert sink.budget_events() == ()  # no event appended on a rejected reservation


def test_second_plan_that_would_exceed_the_run_is_rejected():
    ledger, _ = _ledger(_run_budget(max_tokens=1000))
    _reserve_plan(ledger, request_id="req-1", candidate=DA, tokens=600, llm=1, concurrency=1,
                  idem="p1")
    with pytest.raises(BudgetExceeded):
        _reserve_plan(ledger, request_id="req-2", candidate=DB, tokens=500, llm=1, concurrency=1,
                      idem="p2")


def test_node_over_reservation_against_plan_is_rejected_then_freed_by_release():
    ledger, _ = _ledger()
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=4)
    n1 = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n1", attempt=1,
        tokens=600, llm_invocations=3, concurrency=1, idempotency_key="n1",
    )
    with pytest.raises(BudgetExceeded):
        ledger.reserve_node(
            plan_reservation_id=plan.reservation_id, node_id="n2", attempt=1,
            tokens=600, llm_invocations=3, concurrency=1, idempotency_key="n2",
        )
    # releasing the unused node frees the plan pool so n2 now fits (invariant 9).
    ledger.release(n1.reservation_id, reason="cancelled", idempotency_key="rel-n1")
    n2 = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n2", attempt=1,
        tokens=600, llm_invocations=3, concurrency=1, idempotency_key="n2b",
    )
    assert n2.reserved_tokens == 600


def test_settling_a_node_below_reservation_frees_plan_budget():
    ledger, _ = _ledger()
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=4)
    n1 = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n1", attempt=1,
        tokens=600, llm_invocations=3, concurrency=1, idempotency_key="n1",
    )
    ledger.settle(n1.reservation_id, actual_tokens=100, actual_llm_invocations=1,
                  idempotency_key="s-n1")
    # only 100 of n1's 600 is still held against the plan → a 600-token n2 now fits.
    n2 = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n2", attempt=1,
        tokens=600, llm_invocations=3, concurrency=1, idempotency_key="n2",
    )
    assert n2.reserved_tokens == 600


def test_settling_a_plan_frees_run_budget():
    ledger, _ = _ledger(_run_budget(max_tokens=5000, max_llm_invocations=40))
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=2)
    assert ledger.available().tokens == 4000
    ledger.settle(plan.reservation_id, actual_tokens=300, actual_llm_invocations=4,
                  idempotency_key="s-plan")
    # settled plan holds only its actual usage; concurrency is freed on settle.
    assert ledger.available() == AvailableBudget(tokens=4700, llm_invocations=36, concurrency=8)


# --------------------------------------------------------------------------- #
# 4. actual over reserved + strict-type rejection                             #
# --------------------------------------------------------------------------- #
def test_settle_actual_over_reserved_is_rejected_without_appending():
    ledger, sink = _ledger()
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=2)
    n = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n1", attempt=1,
        tokens=500, llm_invocations=2, concurrency=1, idempotency_key="n1",
    )
    before = len(sink.budget_events())
    with pytest.raises(BudgetExceeded):
        ledger.settle(n.reservation_id, actual_tokens=600, actual_llm_invocations=1,
                      idempotency_key="s-bad")
    assert len(sink.budget_events()) == before  # nothing appended


@pytest.mark.parametrize("bad", [True, -1])
def test_budget_request_rejects_bool_and_negative(bad):
    with pytest.raises(ValidationError):
        BudgetRequest(tokens=bad, llm_invocations=0, concurrency=1)


def test_reserve_node_rejects_negative_amount():
    ledger, _ = _ledger()
    plan = _reserve_plan(ledger)
    with pytest.raises(ValidationError):
        ledger.reserve_node(
            plan_reservation_id=plan.reservation_id, node_id="n", attempt=1,
            tokens=-5, llm_invocations=0, concurrency=1, idempotency_key="n",
        )


# --------------------------------------------------------------------------- #
# 5. deterministic (zero-LLM) vs LLM node reservations                        #
# --------------------------------------------------------------------------- #
def test_deterministic_node_reserves_zero_llm_invocations():
    ledger, _ = _ledger()
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=4)
    det = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="det", attempt=1,
        tokens=0, llm_invocations=0, concurrency=1, idempotency_key="det",
    )
    assert det.reserved_llm_invocations == 0
    llm = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="llm", attempt=3,
        tokens=500, llm_invocations=3, concurrency=1, idempotency_key="llm",
    )
    assert llm.reserved_llm_invocations == 3  # per allowed attempt


# --------------------------------------------------------------------------- #
# 6. atomicity — exactly one event per accepted transition                    #
# --------------------------------------------------------------------------- #
def test_each_accepted_transition_appends_exactly_one_event():
    ledger, sink = _ledger()
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=4)
    assert len(sink.budget_events()) == 1
    n = ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n", attempt=1,
        tokens=100, llm_invocations=1, concurrency=1, idempotency_key="n",
    )
    assert len(sink.budget_events()) == 2
    ledger.settle(n.reservation_id, actual_tokens=50, actual_llm_invocations=1,
                  idempotency_key="s")
    assert len(sink.budget_events()) == 3
    ledger.release(plan.reservation_id, reason="done", idempotency_key="r")
    assert len(sink.budget_events()) == 4


# --------------------------------------------------------------------------- #
# 7. idempotency: identical -> same record; conflicting -> fail               #
# --------------------------------------------------------------------------- #
def test_identical_idempotent_reserve_returns_the_same_record():
    ledger, sink = _ledger()
    r1 = _reserve_plan(ledger, tokens=1000, idem="k1")
    r2 = _reserve_plan(ledger, tokens=1000, idem="k1")
    assert r1.reservation_id == r2.reservation_id
    assert r1.semantic_digest() == r2.semantic_digest()
    assert len(sink.budget_events()) == 1  # not re-appended


def test_conflicting_idempotency_key_is_rejected():
    ledger, _ = _ledger()
    _reserve_plan(ledger, tokens=1000, idem="k1")
    with pytest.raises(IdempotencyConflict):
        _reserve_plan(ledger, tokens=2000, idem="k1")  # same key, different amounts


# --------------------------------------------------------------------------- #
# 8. invalid transitions fail without appending                               #
# --------------------------------------------------------------------------- #
def test_double_settle_is_invalid_without_appending():
    ledger, sink = _ledger()
    plan = _reserve_plan(ledger, tokens=1000)
    ledger.settle(plan.reservation_id, actual_tokens=10, actual_llm_invocations=1,
                  idempotency_key="s1")
    before = len(sink.budget_events())
    with pytest.raises(InvalidBudgetTransition):
        ledger.settle(plan.reservation_id, actual_tokens=10, actual_llm_invocations=1,
                      idempotency_key="s2")
    assert len(sink.budget_events()) == before


def test_settle_after_release_is_invalid():
    ledger, _ = _ledger()
    plan = _reserve_plan(ledger, tokens=1000)
    ledger.release(plan.reservation_id, reason="cancel", idempotency_key="r1")
    with pytest.raises(InvalidBudgetTransition):
        ledger.settle(plan.reservation_id, actual_tokens=10, actual_llm_invocations=1,
                      idempotency_key="s1")


def test_node_under_inactive_plan_is_invalid():
    ledger, _ = _ledger()
    plan = _reserve_plan(ledger, tokens=1000)
    ledger.release(plan.reservation_id, reason="cancel", idempotency_key="r1")
    with pytest.raises(InvalidBudgetTransition):
        ledger.reserve_node(
            plan_reservation_id=plan.reservation_id, node_id="n", attempt=1,
            tokens=1, llm_invocations=0, concurrency=1, idempotency_key="n",
        )


def test_second_active_plan_for_same_request_candidate_is_invalid():
    ledger, _ = _ledger()
    _reserve_plan(ledger, request_id="req-1", candidate=DA, tokens=100, idem="p1")
    with pytest.raises(InvalidBudgetTransition):
        _reserve_plan(ledger, request_id="req-1", candidate=DA, tokens=100, idem="p2")


# --------------------------------------------------------------------------- #
# 9. release restores availability + active-plan index                        #
# --------------------------------------------------------------------------- #
def test_release_restores_run_availability_and_clears_active_plan():
    ledger, _ = _ledger(_run_budget(max_tokens=1000))
    plan = _reserve_plan(ledger, tokens=800)
    assert ledger.available().tokens == 200
    assert ledger.get_active_plan("req-1", DA) is not None
    ledger.release(plan.reservation_id, reason="approval_rejected", idempotency_key="r1")
    assert ledger.available().tokens == 1000
    assert ledger.get_active_plan("req-1", DA) is None


# --------------------------------------------------------------------------- #
# 10. replay reproduces availability + active-reservation index               #
# --------------------------------------------------------------------------- #
def test_replay_reproduces_availability_and_active_index():
    ledger, sink = _ledger()
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=4)
    ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n", attempt=1,
        tokens=100, llm_invocations=1, concurrency=1, idempotency_key="n",
    )
    avail = ledger.available()
    active = ledger.get_active_plan("req-1", DA)

    # a fresh ledger folded over the same event stream is identical.
    replay_ledger = BudgetLedger(sink=sink, run_budget=_run_budget())
    assert replay_ledger.available() == avail
    assert replay_ledger.get_active_plan("req-1", DA).semantic_digest() == active.semantic_digest()
    # replay() returns the folded LedgerState.
    state = ledger.replay()
    assert isinstance(state, LedgerState)
    assert set(state.reservations) == {plan.reservation_id} | {
        r.reservation_id for r in state.reservations.values() if r.scope_type == "node"
    }


# --------------------------------------------------------------------------- #
# design: callers cannot select reservation id / cursor / time                #
# --------------------------------------------------------------------------- #
def test_reserve_args_reject_caller_selected_service_fields():
    with pytest.raises(ValidationError):
        ReservePlanArgs(request_id="r", candidate_plan_digest=DA, reserved_tokens=1,
                        reserved_llm_invocations=0, reserved_concurrency=1,
                        reservation_id="res-1")  # caller cannot pick the new id
    with pytest.raises(ValidationError):
        ReserveNodeArgs(plan_reservation_id="res-1", node_id="n", attempt=1,
                        reserved_tokens=1, reserved_llm_invocations=0, reserved_concurrency=1,
                        seq=1)  # no cursor


def test_command_has_no_timestamp_field():
    with pytest.raises(ValidationError):
        BudgetTransitionCommand(
            operation="reserve_plan",
            semantic_args=ReservePlanArgs(
                request_id="r", candidate_plan_digest=DA, reserved_tokens=1,
                reserved_llm_invocations=0, reserved_concurrency=1),
            idempotency_key="k",
            occurred_at=datetime(2026, 7, 16, tzinfo=UTC),  # not accepted
        )


def test_public_reserve_methods_expose_no_id_or_time_parameters():
    for name in ("reserve_plan", "reserve_node"):
        params = set(inspect.signature(getattr(BudgetLedger, name)).parameters)
        assert "reservation_id" not in params
        assert "reserved_at" not in params and "occurred_at" not in params and "seq" not in params


# --------------------------------------------------------------------------- #
# design: the pure validator is deterministic + same-head cannot over-reserve #
# --------------------------------------------------------------------------- #
def test_validator_is_deterministic_and_same_head_cannot_over_reserve():
    rb = _run_budget(max_tokens=5000)
    ledger, sink = _ledger(rb)
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=4)

    # fold the head *before* any node is reserved.
    head = fold_budget_events(sink.budget_events())

    def node_cmd(node_id: str, idem: str) -> BudgetTransitionCommand:
        return BudgetTransitionCommand(
            operation="reserve_node",
            semantic_args=ReserveNodeArgs(
                plan_reservation_id=plan.reservation_id, node_id=node_id, attempt=1,
                reserved_tokens=600, reserved_llm_invocations=3, reserved_concurrency=1),
            idempotency_key=idem,
        )

    cmd_a = node_cmd("n_a", "a")
    cmd_b = node_cmd("n_b", "b")
    # both individually validate against the SAME head (600 <= 1000 plan pool) —
    # and re-running the pure validator is deterministic (no exception both times).
    for _ in range(2):
        validate_budget_command(cmd_a, head, run_budget=rb)
        validate_budget_command(cmd_b, head, run_budget=rb)

    # but the ledger re-folds the live head before each commit, so the two cannot
    # both be accepted (600 + 600 > 1000): the second over-reserves.
    ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n_a", attempt=1,
        tokens=600, llm_invocations=3, concurrency=1, idempotency_key="a2",
    )
    with pytest.raises(BudgetExceeded):
        ledger.reserve_node(
            plan_reservation_id=plan.reservation_id, node_id="n_b", attempt=1,
            tokens=600, llm_invocations=3, concurrency=1, idempotency_key="b2",
        )


def test_validate_budget_command_raises_on_over_reserve_against_advanced_head():
    rb = _run_budget(max_tokens=1000)
    ledger, sink = _ledger(rb)
    plan = _reserve_plan(ledger, tokens=1000, llm=10, concurrency=4)
    ledger.reserve_node(
        plan_reservation_id=plan.reservation_id, node_id="n_a", attempt=1,
        tokens=600, llm_invocations=3, concurrency=1, idempotency_key="a",
    )
    advanced = fold_budget_events(sink.budget_events())
    cmd_b = BudgetTransitionCommand(
        operation="reserve_node",
        semantic_args=ReserveNodeArgs(
            plan_reservation_id=plan.reservation_id, node_id="n_b", attempt=1,
            reserved_tokens=600, reserved_llm_invocations=3, reserved_concurrency=1),
        idempotency_key="b",
    )
    with pytest.raises(BudgetExceeded):
        validate_budget_command(cmd_b, advanced, run_budget=rb)


# --------------------------------------------------------------------------- #
# sink protocol + naive-clock rejection at stamp time                         #
# --------------------------------------------------------------------------- #
def test_fake_sink_satisfies_the_budget_event_sink_protocol():
    sink = FakeBudgetEventSink(run_id="run-1", ledger_id="ledger-1", clock=AdvancingClock())
    assert isinstance(sink, BudgetEventSink)


def test_naive_clock_is_rejected_at_stamp_time():
    ledger, _ = _ledger(clock=NaiveClock())
    with pytest.raises(ValueError):
        _reserve_plan(ledger, tokens=100)
