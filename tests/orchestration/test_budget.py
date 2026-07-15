# -*- coding: utf-8 -*-
"""Task 8 — ``RunBudget`` / ``BudgetReservation`` contracts (``context.py``).

Written test-first (RED until ``context.py`` exists). Locks the reviewed budget
invariants — **models + validators only; no BudgetLedger, reservation-mutation
or ledger-summing behavior is implemented**:

* maxima / reserved / actual are strict non-negative ints (``bool`` rejected);
  ``max_concurrency`` and a reservation's ``reserved_concurrency`` are
  ``PositiveInt`` (zero rejected);
* a ``RunBudget``'s running reserved totals may not exceed its maxima;
* a ``BudgetReservation`` binds ``request_id`` / ``candidate_plan_digest`` /
  ledger identity (``ledger_id`` + scope) and the exact requested
  token / invocation / concurrency amounts; its random ``reservation_id`` and the
  ``reserved_at`` / ``settled_at`` wall-clock are audit-only;
* actual use may not exceed the reservation (Phase 1);
* ``status="reserved"`` carries no settled time; ``settled`` / ``released``
  require a settled time;
* a malformed declared ``candidate_plan_digest`` is rejected; every model is
  frozen and forbids extra fields.

Run from repo root: ``pytest tests/orchestration/test_budget.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.digest import DigestModel
from guanlan_v2.orchestration.context import BudgetReservation, RunBudget

UTC = timezone.utc
DA = "a" * 64
DB = "b" * 64


def _dt(hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=UTC)


def _budget(**over) -> RunBudget:
    base = dict(
        ledger_id="ledger-1",
        max_tokens=10_000,
        max_llm_invocations=20,
        max_concurrency=4,
        reserved_tokens=0,
        reserved_llm_invocations=0,
        reserved_concurrency=0,
    )
    base.update(over)
    return RunBudget(**base)


def _reservation(**over) -> BudgetReservation:
    base = dict(
        reservation_id="res-1",
        ledger_id="ledger-1",
        run_id="run-1",
        request_id="req-1",
        candidate_plan_digest=DA,
        scope_type="node",
        scope_id="node.research",
        reserved_tokens=1000,
        reserved_llm_invocations=3,
        reserved_concurrency=2,
        actual_tokens=0,
        actual_llm_invocations=0,
        actual_concurrency=0,
        status="reserved",
        reserved_at=_dt(9, 0),
        settled_at=None,
    )
    base.update(over)
    return BudgetReservation(**base)


# --------------------------------------------------------------------------- #
# base contract                                                               #
# --------------------------------------------------------------------------- #
def test_budget_models_are_digest_models():
    assert issubclass(RunBudget, DigestModel)
    assert issubclass(BudgetReservation, DigestModel)


def test_happy_budget_and_reservation():
    assert _budget().max_concurrency == 4
    assert _reservation().status == "reserved"


# --------------------------------------------------------------------------- #
# 8. negative / bool rejection                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field", ["max_tokens", "max_llm_invocations", "reserved_tokens",
              "reserved_llm_invocations", "reserved_concurrency"],
)
def test_budget_rejects_negative(field):
    with pytest.raises(ValidationError):
        _budget(**{field: -1})


@pytest.mark.parametrize(
    "field", ["max_tokens", "max_llm_invocations", "max_concurrency",
              "reserved_tokens", "reserved_llm_invocations", "reserved_concurrency"],
)
def test_budget_rejects_bool(field):
    with pytest.raises(ValidationError):
        _budget(**{field: True})


@pytest.mark.parametrize(
    "field", ["reserved_tokens", "reserved_llm_invocations", "reserved_concurrency",
              "actual_tokens", "actual_llm_invocations", "actual_concurrency"],
)
def test_reservation_rejects_negative(field):
    with pytest.raises(ValidationError):
        _reservation(**{field: -1})


@pytest.mark.parametrize(
    "field", ["reserved_tokens", "reserved_llm_invocations", "reserved_concurrency",
              "actual_tokens", "actual_llm_invocations", "actual_concurrency"],
)
def test_reservation_rejects_bool(field):
    with pytest.raises(ValidationError):
        _reservation(**{field: True})


# --------------------------------------------------------------------------- #
# 9. reserved > max  and  actual > reserved rejection                         #
# --------------------------------------------------------------------------- #
def test_reserved_tokens_may_not_exceed_max():
    with pytest.raises(ValidationError):
        _budget(max_tokens=1000, reserved_tokens=1001)


def test_reserved_invocations_may_not_exceed_max():
    with pytest.raises(ValidationError):
        _budget(max_llm_invocations=5, reserved_llm_invocations=6)


def test_reserved_concurrency_may_not_exceed_max():
    with pytest.raises(ValidationError):
        _budget(max_concurrency=2, reserved_concurrency=3)


def test_reserved_totals_at_maxima_are_allowed():
    b = _budget(max_tokens=1000, reserved_tokens=1000,
                max_llm_invocations=5, reserved_llm_invocations=5,
                max_concurrency=4, reserved_concurrency=4)
    assert b.reserved_tokens == 1000


@pytest.mark.parametrize(
    "field,res_field",
    [("actual_tokens", "reserved_tokens"),
     ("actual_llm_invocations", "reserved_llm_invocations"),
     ("actual_concurrency", "reserved_concurrency")],
)
def test_actual_may_not_exceed_reservation(field, res_field):
    with pytest.raises(ValidationError):
        _reservation(**{res_field: 2, field: 3})


def test_actual_at_reservation_is_allowed():
    r = _reservation(reserved_tokens=1000, actual_tokens=1000,
                     status="settled", settled_at=_dt(9, 5))
    assert r.actual_tokens == 1000


# --------------------------------------------------------------------------- #
# 10. zero concurrency rejection                                              #
# --------------------------------------------------------------------------- #
def test_budget_zero_concurrency_rejected():
    with pytest.raises(ValidationError):
        _budget(max_concurrency=0)


def test_reservation_zero_concurrency_rejected():
    with pytest.raises(ValidationError):
        _reservation(reserved_concurrency=0)


# --------------------------------------------------------------------------- #
# 11. reservation status / settled-time matrix                               #
# --------------------------------------------------------------------------- #
def test_reserved_status_has_no_settled_time():
    r = _reservation(status="reserved", settled_at=None)
    assert r.settled_at is None


def test_reserved_status_with_settled_time_rejected():
    with pytest.raises(ValidationError):
        _reservation(status="reserved", settled_at=_dt(9, 5))


@pytest.mark.parametrize("status", ["settled", "released"])
def test_settled_or_released_requires_settled_time(status):
    ok = _reservation(status=status, settled_at=_dt(9, 5))
    assert ok.settled_at is not None
    with pytest.raises(ValidationError):
        _reservation(status=status, settled_at=None)


def test_reservation_rejects_unknown_status():
    with pytest.raises(ValidationError):
        _reservation(status="closed")


def test_reservation_settled_at_naive_rejected():
    with pytest.raises(ValidationError):
        _reservation(status="settled", settled_at=datetime(2026, 7, 15, 9, 5))


# --------------------------------------------------------------------------- #
# 12. reservation / request / candidate-plan-digest binding + declared digest #
# --------------------------------------------------------------------------- #
def test_reservation_random_id_and_wall_clock_are_audit_only():
    a = _reservation(reservation_id="res-1", reserved_at=_dt(9, 0))
    b = _reservation(reservation_id="res-9", reserved_at=_dt(10, 0))
    assert a.semantic_digest() == b.semantic_digest()
    assert a.audit_digest_value() != b.audit_digest_value()


@pytest.mark.parametrize(
    "over",
    [
        {"request_id": "req-2"},
        {"candidate_plan_digest": DB},
        {"ledger_id": "ledger-2"},
        {"scope_type": "planner"},
        {"scope_id": "node.other"},
        {"reserved_tokens": 999},
        {"reserved_concurrency": 1},
    ],
)
def test_reservation_binds_semantic_fields(over):
    a = _reservation()
    b = _reservation(**over)
    assert a.semantic_digest() != b.semantic_digest()


def test_reservation_rejects_malformed_candidate_plan_digest():
    with pytest.raises(ValidationError):
        _reservation(candidate_plan_digest="not-a-real-digest")


def test_reservation_rejects_uppercase_candidate_plan_digest():
    with pytest.raises(ValidationError):
        _reservation(candidate_plan_digest="A" * 64)


# --------------------------------------------------------------------------- #
# 13. extra-field rejection + immutability                                    #
# --------------------------------------------------------------------------- #
def test_budget_rejects_extra_field():
    with pytest.raises(ValidationError):
        _budget(bogus=1)


def test_reservation_rejects_extra_field():
    with pytest.raises(ValidationError):
        _reservation(parent_reservation_id="res-0")


def test_budget_is_frozen():
    b = _budget()
    with pytest.raises(ValidationError):
        b.max_tokens = 1


def test_reservation_is_frozen():
    r = _reservation()
    with pytest.raises(ValidationError):
        r.status = "settled"
