from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from guanlan_v2.seats.watch_deep_authorization import (
    HKT,
    WatchDeepAuthorizationConflict,
    WatchDeepAuthorizationStore,
    WatchDeepReservationConflict,
)


NOW = datetime(2026, 8, 17, 9, 30, tzinfo=HKT)
SESSION = "2026-08-17"
PRESET = "pipeline.luozi_deep_decide"
DIGEST = "a" * 64


def _store(path: Path, *, global_cap: int = 20,
           concurrency: int = 1) -> WatchDeepAuthorizationStore:
    return WatchDeepAuthorizationStore(
        path, global_daily_max_runs=global_cap,
        global_max_concurrent_runs=concurrency, clock=lambda: NOW)


def _issue(store: WatchDeepAuthorizationStore, *, code: str = "300750",
           revision: int = 3, max_runs: int = 1, max_llm: int = 8,
           digest: str = DIGEST):
    return store.issue(
        code=code,
        assignment_revision=revision,
        session_date=SESSION,
        preset_id=PRESET,
        preset_record_digest=digest,
        valid_from=datetime(2026, 8, 17, 9, 25, tzinfo=HKT),
        valid_until=datetime(2026, 8, 17, 15, 0, tzinfo=HKT),
        max_runs=max_runs,
        max_llm_invocations=max_llm,
        issued_by="human:ops",
        reason="test grant",
    )


def _consume(store: WatchDeepAuthorizationStore, run_id: str, *,
             code: str = "300750", revision: int = 3,
             session: str = SESSION, preset: str = PRESET,
             digest: str = DIGEST, requested: int = 8):
    return store.consume(
        run_id=run_id,
        code=code,
        assignment_revision=revision,
        session_date=session,
        preset_id=preset,
        preset_record_digest=digest,
        requested_llm_invocations=requested,
        now=NOW,
    )


def test_consume_requires_the_exact_code_revision_session_and_preset(tmp_path):
    store = _store(tmp_path / "auth.jsonl")
    _issue(store)

    assert _consume(store, "wrong-code", code="600519") is None
    assert _consume(store, "wrong-revision", revision=4) is None
    assert _consume(store, "wrong-session", session="2026-08-18") is None
    assert _consume(store, "wrong-preset", preset="pipeline.other") is None
    assert _consume(store, "wrong-digest", digest="b" * 64) is None

    reservation = _consume(store, "exact")
    assert reservation is not None and reservation.newly_reserved is True
    view = store.get(code="300750", assignment_revision=3,
                     session_date=SESSION, now=NOW)
    assert view is not None
    assert view.status == "exhausted"
    assert view.runs_used == 1 and view.llm_invocations_used == 8


def test_release_returns_capacity_but_settlement_keeps_a_run_consumed(tmp_path):
    store = _store(tmp_path / "auth.jsonl")
    _issue(store, max_runs=2, max_llm=8)

    preflight = _consume(store, "preflight", requested=5)
    assert preflight is not None
    released = store.release(
        preflight.reservation_id,
        reason="data preflight refused",
        released_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    assert released.status == "released"
    # Exact duplicate is idempotent; a different audit explanation is not.
    again = store.release(
        preflight.reservation_id,
        reason="data preflight refused",
        released_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    assert again.status == "released"
    with pytest.raises(WatchDeepReservationConflict):
        store.release(
            preflight.reservation_id,
            reason="different explanation",
            released_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
        )
    view = store.get(code="300750", assignment_revision=3,
                     session_date=SESSION, now=NOW)
    assert view is not None and view.runs_used == 0
    assert view.llm_invocations_used == 0

    first = _consume(store, "run-1", requested=5)
    assert first is not None
    store.settle(
        first.reservation_id,
        actual_llm_invocations=3,
        outcome="failed",
        settled_at=datetime(2026, 8, 17, 9, 32, tzinfo=HKT),
    )
    # Unused LLM reserve is returned, while the admitted run remains consumed.
    middle = store.get(code="300750", assignment_revision=3,
                       session_date=SESSION, now=NOW)
    assert middle is not None
    assert (middle.runs_used, middle.runs_remaining) == (1, 1)
    assert (middle.llm_invocations_used, middle.llm_invocations_remaining) == (3, 5)

    second = _consume(store, "run-2", requested=5)
    assert second is not None
    store.settle(
        second.reservation_id,
        actual_llm_invocations=5,
        outcome="completed",
        settled_at=datetime(2026, 8, 17, 9, 33, tzinfo=HKT),
    )
    exhausted = store.get(code="300750", assignment_revision=3,
                          session_date=SESSION, now=NOW)
    assert exhausted is not None and exhausted.status == "exhausted"
    assert _consume(store, "run-3", requested=1) is None


def test_revocation_is_durable_and_blocks_new_consumption(tmp_path):
    path = tmp_path / "auth.jsonl"
    issued = _issue(_store(path))
    auth_id = issued.authorization.authorization_id

    restarted = _store(path)
    active = restarted.get_by_id(auth_id, now=NOW)
    assert active is not None and active.status == "active"
    revoked = restarted.revoke(
        auth_id,
        revoked_by="human:ops",
        reason="operator stopped automation",
        revoked_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    assert revoked.status == "revoked"
    assert _consume(_store(path), "after-revoke") is None

    folded_again = _store(path).get_by_id(auth_id, now=NOW)
    assert folded_again is not None
    assert folded_again.status == "revoked"
    assert folded_again.revocation_reason == "operator stopped automation"


def test_restart_replays_a_run_id_without_double_consuming_or_rerunning(tmp_path):
    path = tmp_path / "auth.jsonl"
    first_store = _store(path)
    _issue(first_store, max_runs=2, max_llm=16)
    first = _consume(first_store, "deep-deterministic-id")
    assert first is not None and first.newly_reserved is True

    restarted = _store(path)
    replay = _consume(restarted, "deep-deterministic-id")
    assert replay is not None
    assert replay.reservation_id == first.reservation_id
    assert replay.newly_reserved is False
    assert restarted.global_usage(SESSION).runs_used == 1

    restarted.settle(
        replay.reservation_id,
        actual_llm_invocations=7,
        outcome="completed",
        settled_at=datetime(2026, 8, 17, 9, 35, tzinfo=HKT),
    )
    runs = _store(path).list_runs(code="300750")
    assert len(runs) == 1 and runs[0].status == "settled"
    assert runs[0].actual_llm_invocations == 7
    with pytest.raises(WatchDeepReservationConflict):
        _store(path).settle(
            replay.reservation_id,
            actual_llm_invocations=8,
            outcome="completed",
            settled_at=datetime(2026, 8, 17, 9, 36, tzinfo=HKT),
        )


def test_path_lock_makes_concurrent_store_instances_respect_caps(tmp_path):
    path = tmp_path / "auth.jsonl"
    _issue(_store(path, global_cap=7, concurrency=7), max_runs=7, max_llm=7)

    def try_one(index: int):
        return _consume(
            _store(path, global_cap=7, concurrency=7),
            f"concurrent-{index}", requested=1)

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(try_one, range(20)))

    admitted = [item for item in outcomes if item is not None]
    assert len(admitted) == 7
    assert len({item.reservation_id for item in admitted}) == 7
    restarted = _store(path, global_cap=7, concurrency=7)
    assert restarted.global_usage(SESSION).runs_used == 7
    assert len(restarted.list_runs(code="300750")) == 7


def test_global_daily_cap_is_shared_across_stocks(tmp_path):
    path = tmp_path / "auth.jsonl"
    store = _store(path, global_cap=1)
    _issue(store, code="300750", revision=1, max_runs=2, max_llm=16)
    _issue(store, code="600519", revision=9, max_runs=2, max_llm=16,
           digest="b" * 64)

    first = _consume(store, "stock-a", code="300750", revision=1)
    assert first is not None
    store.settle(
        first.reservation_id,
        actual_llm_invocations=8,
        outcome="completed",
        settled_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    second = _consume(
        store, "stock-b", code="600519", revision=9,
        digest="b" * 64,
    )
    assert second is None
    budget = store.global_usage(SESSION)
    assert budget.max_runs == 1
    assert (budget.runs_used, budget.runs_remaining) == (1, 0)


def test_settlement_preserves_and_flags_real_spend_above_the_reservation(tmp_path):
    store = _store(tmp_path / "auth.jsonl")
    _issue(store, max_runs=1, max_llm=8)
    reservation = _consume(store, "overrun", requested=8)
    assert reservation is not None
    settled = store.settle(
        reservation.reservation_id,
        actual_llm_invocations=10,
        outcome="failed",
        detail="runner exceeded its invocation envelope",
        settled_at=datetime(2026, 8, 17, 9, 40, tzinfo=HKT),
    )
    assert settled.actual_llm_invocations == 10
    assert settled.llm_overrun is True and settled.llm_overrun_by == 2
    restarted = _store(store.path)
    persisted = restarted.list_runs(code="300750")[0]
    assert persisted.actual_llm_invocations == 10
    assert persisted.llm_overrun is True and persisted.llm_overrun_by == 2
    view = restarted.get(code="300750", assignment_revision=3,
                         session_date=SESSION, now=NOW)
    assert view is not None and view.llm_invocations_used == 10


def test_only_one_new_run_can_be_in_flight_across_two_stocks(tmp_path):
    path = tmp_path / "auth.jsonl"
    store = _store(path, global_cap=2)
    _issue(store, code="300750", revision=1)
    _issue(store, code="600519", revision=9, digest="b" * 64)

    def stock_a():
        return _consume(
            _store(path, global_cap=2), "stock-a",
            code="300750", revision=1)

    def stock_b():
        return _consume(
            _store(path, global_cap=2), "stock-b",
            code="600519", revision=9, digest="b" * 64)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [pool.submit(stock_a), pool.submit(stock_b)]
        admitted = [future.result() for future in outcomes]
    admitted = [item for item in admitted if item is not None]
    assert len(admitted) == 1
    concurrency = _store(path, global_cap=2).global_concurrency()
    assert (concurrency.max_runs, concurrency.runs_in_flight,
            concurrency.runs_available) == (1, 1, 0)

    _store(path, global_cap=2).settle(
        admitted[0].reservation_id,
        actual_llm_invocations=8,
        outcome="completed",
        settled_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    assert _store(path, global_cap=2).global_concurrency().runs_available == 1


def test_exhausted_scope_cannot_be_reissued_for_a_fresh_run_id(tmp_path):
    store = _store(tmp_path / "auth.jsonl")
    _issue(store)
    reservation = _consume(store, "first-and-only")
    assert reservation is not None
    store.settle(
        reservation.reservation_id,
        actual_llm_invocations=8,
        outcome="completed",
        settled_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    assert store.scope_consumed(
        code="300750", assignment_revision=3, session_date=SESSION) is True
    with pytest.raises(WatchDeepAuthorizationConflict, match="permanently closed"):
        _issue(store)


def test_settled_then_revoked_scope_still_cannot_be_reissued(tmp_path):
    store = _store(tmp_path / "auth.jsonl")
    issued = _issue(store)
    reservation = _consume(store, "settled-before-revoke")
    assert reservation is not None
    store.settle(
        reservation.reservation_id,
        actual_llm_invocations=7,
        outcome="failed",
        settled_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    store.revoke(
        issued.authorization.authorization_id,
        revoked_by="human:ops",
        reason="stop later admissions",
        revoked_at=datetime(2026, 8, 17, 9, 32, tzinfo=HKT),
    )
    with pytest.raises(WatchDeepAuthorizationConflict, match="permanently closed"):
        _issue(store)


def test_pre_execution_release_allows_the_scope_to_run_once_later(tmp_path):
    store = _store(tmp_path / "auth.jsonl")
    issued = _issue(store)
    reservation = _consume(store, "preflight-refused")
    assert reservation is not None
    store.release(
        reservation.reservation_id,
        reason="data preflight refused",
        released_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    assert store.scope_consumed(
        code="300750", assignment_revision=3, session_date=SESSION) is False

    # The still-active original authorization is safely reused; no second
    # issuance row is needed, but a new run id may consume its restored quota.
    reissued = _issue(store)
    assert (reissued.authorization.authorization_id
            == issued.authorization.authorization_id)
    retry = _consume(store, "retry-after-release")
    assert retry is not None and retry.newly_reserved is True


def test_same_deterministic_run_can_retry_only_after_pre_execution_release(tmp_path):
    path = tmp_path / "auth.jsonl"
    store = _store(path)
    _issue(store)
    first = _consume(store, "same-deep-run")
    assert first is not None and first.newly_reserved is True
    # A concurrent/replayed caller cannot enter while the first reservation is
    # unresolved.
    held = _consume(_store(path), "same-deep-run")
    assert held is not None and held.newly_reserved is False

    store.release(
        first.reservation_id,
        reason="capture_failed",
        released_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    retry = _consume(_store(path), "same-deep-run")
    assert retry is not None and retry.newly_reserved is True
    assert retry.reservation_id != first.reservation_id

    _store(path).settle(
        retry.reservation_id,
        actual_llm_invocations=0,
        outcome="failed",
        settled_at=datetime(2026, 8, 17, 9, 32, tzinfo=HKT),
    )
    terminal = _consume(_store(path), "same-deep-run")
    assert terminal is not None and terminal.newly_reserved is False


def test_revoked_before_any_consumption_can_be_reissued_safely(tmp_path):
    store = _store(tmp_path / "auth.jsonl")
    issued = _issue(store)
    store.revoke(
        issued.authorization.authorization_id,
        revoked_by="human:ops",
        reason="changed mind before execution",
        revoked_at=datetime(2026, 8, 17, 9, 31, tzinfo=HKT),
    )
    replacement = _issue(store)
    assert (replacement.authorization.authorization_id
            != issued.authorization.authorization_id)
    assert replacement.status == "active"
    assert _consume(store, "after-safe-reissue") is not None
