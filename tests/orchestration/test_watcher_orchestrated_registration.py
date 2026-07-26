# -*- coding: utf-8 -*-
"""R17 — the watcher's orchestrated-code REGISTRATION half.

Phase 9 Task 4 shipped only the CONSUMPTION half of the anti-double-判 seam:
``watcher._ORCHESTRATED_CODES`` was defined, read by ``watcher.orchestrated_codes()``
and consulted by ``watcher.tick`` — but nothing in the repository ever WROTE it, so the
skip was a dead seam and its only proof monkeypatched ``orchestrated_codes`` itself.

This module proves the real thing:

* the watcher exposes a real registration API (``register_orchestrated_codes`` /
  ``release_orchestrated_codes``, the returned handle doubling as a context manager);
* a REAL ``tick`` skips a REALLY registered code with ``skipped[code] == "orchestrated"``
  while judging every other watched code exactly as before (no monkeypatch of
  ``orchestrated_codes`` anywhere in this file);
* ownership is REFCOUNTED — two owners of the same code, one release, still owned;
* a release handle is single-use, so a double release can never free another owner;
* the interval-replay driver registers its universe for the duration of a live run and
  releases it on the success path AND on the exception path;
* a historical (``is_live_session=None``) replay registers nothing — Task-4 bit-identity;
* the registry is process-local and never persisted (a hard crash self-heals).

Run: ``python -m pytest tests/orchestration/test_watcher_orchestrated_registration.py -v``
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from guanlan_v2.orchestration.adapters.contracts import ShadowExecutionConfig
from guanlan_v2.orchestration.adapters.luozi import (
    DeterministicBook,
    ReplayIntentLedger,
    ReplayPointSnapshot,
    ReplayRuntimeBindings,
    run_interval_replay,
)
from guanlan_v2.orchestration.budget import BudgetEvent, BudgetLedger, BudgetTransitionCommand
from guanlan_v2.orchestration.context import ClockSpec, RunBudget
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.enums import ApprovalPolicy, Confidence
from guanlan_v2.orchestration.presets import pilot_data_context
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.runtime_clock import clock_now
from guanlan_v2.orchestration.shadow import (
    DecisionSchedule,
    DecisionScheduleRegistry,
    PortfolioTargetProposal,
    TargetPosition,
)
from guanlan_v2.orchestration.spec import OrchestrationRequest
from guanlan_v2.seats import watcher

UTC = timezone.utc
TZ = "Asia/Shanghai"
CAL_ID = "cn_a_share"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: a weekday inside the A-share afternoon session — the instant every real ``tick``
#: below is driven at (the same instant the pre-existing watcher tests use).
TICK_NOW = datetime(2026, 7, 10, 10, 0)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Suite isolation: the orchestrated registry is a module global — snapshot/restore.

    A leaked registration would silently silence the watcher for every later test in
    the process, which is precisely the failure mode this feature must not have.
    """
    yield
    watcher._ORCHESTRATED_REFS.clear()
    watcher._ORCHESTRATED_CODES = set()


@pytest.fixture()
def watch_state(tmp_path, monkeypatch):
    """A private watcher state file with the watcher enabled and budget to spare."""
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "seats_watch.json")
    watcher.save_state({"enabled": True, "daily_budget": 24, "counts": {}})
    return tmp_path / "seats_watch.json"


def _watching(*codes):
    return [
        {"code": c, "strategy_id": f"s-{c}", "name": "n", "clock": {"decisionFreq": "hourly"},
         "creed": "", "w": 0, "pa": False, "pa_method": "", "refs": []}
        for c in codes
    ]


def _tick(judged_sink=None):
    """One real ``watcher.tick`` with everything else stubbed (zero network, zero LLM)."""
    return watcher.tick(
        now=TICK_NOW,
        decide_fn=lambda p: (judged_sink.append(p["code"]) if judged_sink is not None else None)
        or {"ok": True},
        quote_fn=lambda c: {"fresh": True},
        decisions_tail_fn=lambda c: None,
    )


# =========================================================================== #
# the registration primitive                                                    #
# =========================================================================== #
def test_register_publishes_the_code_and_release_takes_it_back():
    assert watcher.orchestrated_codes() == set()
    reg = watcher.register_orchestrated_codes(["SZ300750"])
    owned = watcher.orchestrated_codes()
    # an exchange-qualified registration owns every equivalent written form, because the
    # watched code comes from a strategy `bind` list whose form we do not control.
    assert {"SZ300750", "300750", "300750.SZ"} <= owned
    watcher.release_orchestrated_codes(reg)
    assert watcher.orchestrated_codes() == set()


def test_a_bare_code_owns_only_itself():
    # syntactic only: with no exchange in the string the module refuses to GUESS one.
    reg = watcher.register_orchestrated_codes(["300750"])
    assert watcher.orchestrated_codes() == {"300750"}
    watcher.release_orchestrated_codes(reg)


def test_registration_handle_is_a_context_manager_and_releases_on_exception():
    with pytest.raises(RuntimeError):
        with watcher.register_orchestrated_codes(["SZ300750"]):
            assert "300750" in watcher.orchestrated_codes()
            raise RuntimeError("boom inside the owned window")
    assert watcher.orchestrated_codes() == set()  # released on the exception path


# =========================================================================== #
# overlap semantics — refcount, single-use handles                              #
# =========================================================================== #
def test_two_owners_of_the_same_code_are_refcounted():
    a = watcher.register_orchestrated_codes(["SZ300750"])
    b = watcher.register_orchestrated_codes(["300750.SZ"])
    assert "300750" in watcher.orchestrated_codes()
    watcher.release_orchestrated_codes(a)
    # the OTHER run still owns it — a naive set.discard would have freed it here.
    assert "300750" in watcher.orchestrated_codes()
    watcher.release_orchestrated_codes(b)
    assert watcher.orchestrated_codes() == set()


def test_a_double_release_cannot_free_another_owner():
    a = watcher.register_orchestrated_codes(["SZ300750"])
    b = watcher.register_orchestrated_codes(["SZ300750"])
    watcher.release_orchestrated_codes(a)
    watcher.release_orchestrated_codes(a)  # single-use handle → the second is a no-op
    watcher.release_orchestrated_codes(a)
    assert "300750" in watcher.orchestrated_codes()  # b's ownership survived
    watcher.release_orchestrated_codes(b)
    assert watcher.orchestrated_codes() == set()


def test_duplicates_inside_one_registration_collapse():
    reg = watcher.register_orchestrated_codes(["SZ300750", "300750.SZ", "300750"])
    watcher.release_orchestrated_codes(reg)
    assert watcher.orchestrated_codes() == set()  # one release frees what one call took


# =========================================================================== #
# empty / None / bad input                                                      #
# =========================================================================== #
def test_empty_registration_is_legal_and_a_noop_both_ways():
    reg = watcher.register_orchestrated_codes([])
    assert watcher.orchestrated_codes() == set()
    watcher.release_orchestrated_codes(reg)  # must not raise
    assert watcher.orchestrated_codes() == set()


def test_release_of_none_is_a_noop():
    watcher.release_orchestrated_codes(None)  # the caller's `finally` may hold nothing


@pytest.mark.parametrize("bad", [None, "300750", b"300750", 300750])
def test_a_non_iterable_of_str_fails_loudly(bad):
    # a bare str is the classic trap (iterating it yields characters) → refused, never
    # silently registered as six one-character codes.
    with pytest.raises(TypeError):
        watcher.register_orchestrated_codes(bad)


@pytest.mark.parametrize("bad", [[300750], [None], [True], [b"300750"]])
def test_a_non_string_code_fails_loudly(bad):
    with pytest.raises(TypeError):
        watcher.register_orchestrated_codes(bad)
    assert watcher.orchestrated_codes() == set()  # nothing partially registered


@pytest.mark.parametrize("bad", [[""], ["   "], ["600519", ""]])
def test_an_empty_code_fails_loudly(bad):
    with pytest.raises(ValueError):
        watcher.register_orchestrated_codes(bad)
    assert watcher.orchestrated_codes() == set()


def test_releasing_a_foreign_object_fails_loudly():
    with pytest.raises(TypeError):
        watcher.release_orchestrated_codes("SZ300750")


# =========================================================================== #
# the REAL tick skip (no monkeypatch of orchestrated_codes)                     #
# =========================================================================== #
def test_real_tick_skips_a_really_registered_code(watch_state, monkeypatch):
    monkeypatch.setattr(watcher, "watching_codes", lambda: _watching("300750", "600519"))
    judged: list = []
    reg = watcher.register_orchestrated_codes(["SZ300750"])
    try:
        out = _tick(judged)
    finally:
        watcher.release_orchestrated_codes(reg)
    assert out["skipped"]["300750"] == "orchestrated"
    assert out["judged"] == ["600519"]        # every other code judged exactly as before
    assert judged == ["600519"]               # and the decide kernel saw only that one


def test_after_release_the_same_tick_judges_the_code_again(watch_state, monkeypatch):
    monkeypatch.setattr(watcher, "watching_codes", lambda: _watching("300750", "600519"))
    reg = watcher.register_orchestrated_codes(["SZ300750"])
    assert _tick()["skipped"].get("300750") == "orchestrated"
    watcher.release_orchestrated_codes(reg)
    out = _tick()
    assert out["judged"] == ["300750", "600519"] and out["skipped"] == {}


def test_tick_with_no_registration_is_bit_unchanged(watch_state, monkeypatch):
    monkeypatch.setattr(watcher, "watching_codes", lambda: _watching("300750", "600519"))
    out = _tick()
    assert out == {"judged": ["300750", "600519"], "skipped": {}}


# =========================================================================== #
# crash safety — process-local, never persisted                                 #
# =========================================================================== #
def test_the_registry_is_never_persisted_and_dies_with_the_process(watch_state):
    reg = watcher.register_orchestrated_codes(["SZ300750"])
    try:
        assert "300750" in watcher.orchestrated_codes()
        # (i) nothing about the ownership reaches the watcher's state file …
        watcher.save_state(watcher.load_state())
        assert "300750" not in watch_state.read_text(encoding="utf-8")
        assert set(watcher.load_state()) <= {"enabled", "daily_budget", "counts", "last_tick"}
        # (ii) … and a FRESH process sees an empty registry — a hard crash self-heals
        # (the skip fails OPEN: the watcher resumes judging, it is never wedged shut).
        out = subprocess.run(
            [sys.executable, "-c",
             "from guanlan_v2.seats import watcher as w; print(sorted(w.orchestrated_codes()))"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "[]"
    finally:
        watcher.release_orchestrated_codes(reg)


# =========================================================================== #
# driver harness (self-contained mirror of tests/orchestration/test_luozi_replay) #
# =========================================================================== #
class _FixedClock:
    def __init__(self, at: datetime):
        self._at = at

    def now(self) -> datetime:
        return self._at


def _utc(iso_day: str, hh: int, mm: int) -> datetime:
    y, m, d = (int(x) for x in iso_day.split("-"))
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(TZ)).astimezone(UTC)


def _schedule() -> DecisionSchedule:
    return DecisionSchedule.build(
        id="sched-r17", version="1", calendar_id=CAL_ID, timezone=TZ, kind="daily",
        decision_local_time="14:00", cutoff_local_time="09:00", bar_frequency="1d",
        execution_policy="next_open", execution_price_field="open",
        matching_engine_version="shadow-match-v1", weekdays=(), rebalance_dates=(),
        intrabar_exit_priority="worst_case",
    )


def _calendar():
    return build_trading_calendar(
        calendar_id=CAL_ID,
        sessions=[date.fromisoformat(s) for s in
                  ("2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09")],
        material_id="mat", material_version="1",
    )


def _proposal_artifact(aid: str):
    prop = PortfolioTargetProposal(
        positions=(TargetPosition(symbol=normalize_symbol("600519"), target_weight=1.0),),
        cash_weight=0.0,
        rationale="shadow advisory only; zero trading authority",
        confidence=Confidence.MEDIUM,
    )
    return SimpleNamespace(
        payload_schema_ref=SchemaRef(name="PortfolioTargetProposal", version="1"),
        payload=prop.model_dump(), artifact_id=aid, content_digest=prop.semantic_digest(),
    )


class _ObservingCoordinator:
    """A recorded fake coordinator that also OBSERVES the watcher from inside the run.

    ``observe`` is called at the moment the LLM lane would judge the point — exactly the
    window a concurrent 盯盘 tick must not judge the same code in.
    """

    def __init__(self, *, observe=None, raise_in_det=False):
        self.observations: list = []
        self.points: list = []
        self._observe = observe
        self._raise_in_det = raise_in_det

    def bootstrap_context(self, point):
        return ReplayPointSnapshot(data_context=pilot_data_context(as_of=point.decision_as_of))

    def llm_proposal(self, point, snapshot):
        self.points.append(point.point_ordinal)
        if self._observe is not None:
            self.observations.append(self._observe())
        return _proposal_artifact(f"art-{point.point_ordinal}")

    def deterministic_targets(self, point, snapshot):
        if self._raise_in_det:
            raise RuntimeError("deterministic lane blew up mid-run")
        return DeterministicBook(
            rule_id="rule-eqw",
            positions=(TargetPosition(symbol=normalize_symbol("600519"), target_weight=1.0),),
            cash_weight=0.0,
        )


class _FakeBudgetEventSink:
    def __init__(self, *, run_id, ledger_id, clock):
        self._run_id, self._ledger_id, self._clock = run_id, ledger_id, clock
        self._events: list[BudgetEvent] = []
        self._by_key: dict[str, BudgetEvent] = {}
        self._seq = 0
        self._res_seq = 0

    def budget_events(self):
        return tuple(self._events)

    def find_by_idempotency_key(self, key):
        return self._by_key.get(key)

    def append(self, command: BudgetTransitionCommand) -> BudgetEvent:
        self._seq += 1
        if command.operation in ("reserve_plan", "reserve_node"):
            self._res_seq += 1
            reservation_id = f"res-{self._res_seq}"
        else:
            reservation_id = command.semantic_args.reservation_id
        ev = BudgetEvent(
            seq=self._seq, event_id=f"be-{self._seq}", run_id=self._run_id,
            ledger_id=self._ledger_id, reservation_id=reservation_id,
            occurred_at=clock_now(self._clock), command=command,
        )
        self._events.append(ev)
        self._by_key[command.idempotency_key] = ev
        return ev


def _exec_config(schedule):
    return ShadowExecutionConfig(
        universe=(normalize_symbol("600519"),),
        init_cash=1_000_000.0,
        data_snapshot_content_digest="a" * 64,
        vintage_manifest_digest="b" * 64,
        calendar_id=CAL_ID,
        cost_model_digest="c" * 64,
        matching_engine_version="shadow-match-v1",
        clock=ClockSpec(as_of=_utc("2026-07-06", 14, 0), timezone=TZ, calendar_id=CAL_ID),
        schedule_digest=schedule.content_digest,
        intrabar_exit_priority="worst_case",
    )


def _request(schedule):
    return OrchestrationRequest(
        request_id="req-r17", goal="shadow replay", workflow="orchestrate_only",
        approval_policy=ApprovalPolicy.REQUIRED,
        decision_schedule_ref=ContentRef(
            id=schedule.id, version=schedule.version,
            content_digest=schedule.content_digest),
    )


def _bindings(schedule, cal, coordinator, *, seats_seam):
    clk = _FixedClock(_utc("2026-07-06", 14, 0))
    sink = _FakeBudgetEventSink(run_id="replay-run.req-r17", ledger_id="led-r17", clock=clk)
    rb = RunBudget(ledger_id="led-r17", max_tokens=10_000_000,
                   max_llm_invocations=100, max_concurrency=64)
    reg = DecisionScheduleRegistry()
    reg.register(schedule)
    return ReplayRuntimeBindings(
        admission=coordinator, budget=BudgetLedger(sink=sink, run_budget=rb), run_budget=rb,
        schedule_registry=reg, calendar=cal,
        clock_factory=lambda point: _FixedClock(point.decision_as_of),
        seats_budget_seam=seats_seam, intent_ledger=ReplayIntentLedger(),
    )


def _run(coordinator, *, seats_seam, is_live_session):
    sch, cal = _schedule(), _calendar()
    return run_interval_replay(
        request=_request(sch), schedule=sch, execution_config=_exec_config(sch),
        interval_start=_utc("2026-07-06", 0, 0), interval_end=_utc("2026-07-08", 23, 59),
        bindings=_bindings(sch, cal, coordinator, seats_seam=seats_seam),
        is_live_session=is_live_session,
    )


# =========================================================================== #
# the driver owns its universe — end to end, real seam, real tick               #
# =========================================================================== #
def test_a_live_run_owns_its_universe_and_a_real_tick_skips_it(watch_state, monkeypatch):
    """The acceptance case: a real driver run + a real tick, nothing monkeypatched."""
    monkeypatch.setattr(watcher, "watching_codes", lambda: _watching("600519", "300750"))
    coord = _ObservingCoordinator(observe=_tick)   # a REAL tick, mid-run
    state = _run(coord, seats_seam=watcher, is_live_session=lambda point: True)

    assert len(coord.observations) == 3           # one tick per decision point
    # every tick: 600519 is this run's universe so the watcher must NOT judge it, while
    # the other watched code is judged exactly as before. Asserted on the WHOLE return
    # value, strictly — no disjunction that could pass for the wrong reason.
    for out in coord.observations:
        assert out == {"judged": ["300750"], "skipped": {"600519": "orchestrated"}}
    # released on the success path: after the run the watcher owns nothing again.
    assert watcher.orchestrated_codes() == set()
    assert state.completed_points == 3
    out = _tick()
    assert "600519" in out["judged"]              # and it judges the code again


def test_a_live_run_releases_its_universe_on_the_exception_path(watch_state):
    seen: list = []
    coord = _ObservingCoordinator(
        observe=lambda: seen.append(watcher.orchestrated_codes()), raise_in_det=True)
    with pytest.raises(RuntimeError):
        _run(coord, seats_seam=watcher, is_live_session=lambda point: True)
    assert seen and "600519" in seen[0]           # it really was owned mid-run …
    assert watcher.orchestrated_codes() == set()  # … and the raise released it


def test_a_historical_replay_registers_nothing(watch_state):
    # `is_live_session=None` is the documented Task-4 default: a purely historical
    # PIT_REPLAY run must NOT silence the live watcher for its universe.
    seen: list = []
    coord = _ObservingCoordinator(observe=lambda: seen.append(watcher.orchestrated_codes()))
    _run(coord, seats_seam=watcher, is_live_session=None)
    assert seen == [set(), set(), set()]
    assert watcher.orchestrated_codes() == set()


def test_a_predicate_false_for_every_point_registers_nothing(watch_state):
    """The false-skip case: a real predicate over an all-historical interval.

    This is the shape a launcher produces on every backfill (a real
    ``is_live_session`` predicate + an interval whose points are all in the past). Owning
    the universe here would stop 盯盘 judging live positions for the whole backtest — a
    MISSED-研判 harm — so it must register nothing.
    """
    seen: list = []
    coord = _ObservingCoordinator(observe=lambda: seen.append(watcher.orchestrated_codes()))
    _run(coord, seats_seam=watcher, is_live_session=lambda point: False)
    assert seen == [set(), set(), set()]
    assert watcher.orchestrated_codes() == set()


def test_ownership_starts_at_the_first_live_point(watch_state):
    # points 1 historical, 2-3 live (the real shape: live points are the TAIL, because
    # points ascend in time). Nothing is owned until the first live point.
    seen: list = []
    coord = _ObservingCoordinator(observe=lambda: seen.append(watcher.orchestrated_codes()))
    _run(coord, seats_seam=watcher, is_live_session=lambda point: point.point_ordinal >= 2)
    assert seen[0] == set()
    assert all("600519" in s for s in seen[1:])
    assert watcher.orchestrated_codes() == set()


def test_ownership_is_held_to_the_end_of_the_run_not_dropped_per_point(watch_state):
    # only point 1 is live → ownership is taken there and NOT released between points
    # (dropping it per point would only re-open the stale-tick-snapshot race at each gap).
    seen: list = []
    coord = _ObservingCoordinator(observe=lambda: seen.append(watcher.orchestrated_codes()))
    _run(coord, seats_seam=watcher, is_live_session=lambda point: point.point_ordinal == 1)
    assert all("600519" in s for s in seen)      # still owned at points 2 and 3
    assert watcher.orchestrated_codes() == set()  # released once, at the end


def test_many_live_points_take_exactly_one_registration(watch_state, monkeypatch):
    # `ensure_owned` is idempotent: three live points must not stack three refcounts,
    # because `__exit__` releases exactly one handle.
    calls: list = []
    real = watcher.register_orchestrated_codes
    monkeypatch.setattr(watcher, "register_orchestrated_codes",
                        lambda codes: calls.append(tuple(codes)) or real(codes))
    _run(_ObservingCoordinator(), seats_seam=watcher, is_live_session=lambda point: True)
    assert calls == [("SH600519",)]               # once for the whole run
    assert watcher.orchestrated_codes() == set()


def test_a_seam_without_the_registration_half_does_not_break_the_run(watch_state, caplog):
    # a partial/fake seam (no `register_orchestrated_codes`) must degrade LOUDLY, never
    # crash the run and never silently pretend the code is owned.
    seam = SimpleNamespace(
        load_state=lambda: {"counts": {}, "daily_budget": 24},
        note_external_llm_use=lambda n, now=None: None,
    )
    coord = _ObservingCoordinator()
    with caplog.at_level("WARNING"):
        state = _run(coord, seats_seam=seam, is_live_session=lambda point: True)
    assert state.completed_points == 3
    assert watcher.orchestrated_codes() == set()
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("register_orchestrated_codes" in m and "NOT registered" in m
               for m in warnings), warnings
    assert len(warnings) == 1, warnings   # once per run, not once per live point


def test_a_missing_seam_on_a_live_run_warns_instead_of_going_silent(watch_state, caplog):
    # `seats_budget_seam=None` on a LIVE run is an unwired live run: it must leave a
    # trace, not silently skip registration.
    with caplog.at_level("WARNING"):
        state = _run(_ObservingCoordinator(), seats_seam=None, is_live_session=lambda p: True)
    assert state.completed_points == 3
    msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("NOT registered" in m and "seam=None" in m for m in msgs), msgs


def test_a_seam_that_cannot_release_logs_the_leak(watch_state, caplog):
    # the WORSE branch: ownership taken, no way to hand it back → the watcher stays
    # silenced for the life of the process. That must be an ERROR, never silent.
    seam = SimpleNamespace(
        load_state=lambda: {"counts": {}, "daily_budget": 24},
        note_external_llm_use=lambda n, now=None: None,
        register_orchestrated_codes=watcher.register_orchestrated_codes,
    )
    with caplog.at_level("ERROR"):
        _run(_ObservingCoordinator(), seats_seam=seam, is_live_session=lambda p: True)
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("LEAKED" in m for m in errors), errors
    assert "600519" in watcher.orchestrated_codes()   # honestly still owned — that IS the leak
    # (the autouse fixture clears the registry so the leak cannot escape this test)


def test_the_ownership_context_manager_is_single_use():
    from guanlan_v2.orchestration.adapters.luozi import _OrchestratedUniverseOwnership

    own = _OrchestratedUniverseOwnership(watcher, ("SH600519",))
    with own:
        pass
    with pytest.raises(RuntimeError, match="single-use"):
        with own:
            pass  # pragma: no cover


def test_ensure_owned_is_refused_outside_the_with_block():
    """Symmetric with ``__enter__``'s guard: registering with nobody left to release is
    the same permanent-silencing leak arriving by a different door."""
    from guanlan_v2.orchestration.adapters.luozi import _OrchestratedUniverseOwnership

    own = _OrchestratedUniverseOwnership(watcher, ("SH600519",))
    with pytest.raises(RuntimeError, match="before entry"):
        own.ensure_owned()                     # never entered
    assert watcher.orchestrated_codes() == set()
    with own:
        own.ensure_owned()
        assert "600519" in watcher.orchestrated_codes()
    assert watcher.orchestrated_codes() == set()
    with pytest.raises(RuntimeError, match="after exit"):
        own.ensure_owned()                     # already released
    assert watcher.orchestrated_codes() == set()
