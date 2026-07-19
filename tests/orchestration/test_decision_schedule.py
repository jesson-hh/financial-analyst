# -*- coding: utf-8 -*-
"""Phase 6 · Task 2 — DecisionSchedule + registry + unique schedule-time tests.

Written test-first (RED until ``DecisionSchedule`` / ``DecisionScheduleRegistry``
and the four pure schedule-time functions exist in
``guanlan_v2.orchestration.shadow``).

These pin, per the Task-2 brief:

* the field surface + validation matrix (timezone resolvability, ``cutoff <=
  decision``, weekday bounds/unique/sorted, rebalance-date format/unique/sorted);
* the kind matrix (``daily``/``weekly``/``rebalance_dates``/``manual`` pairing of
  ``weekdays`` / ``rebalance_dates``);
* the ``execution_policy`` ⇔ ``execution_price_field`` pairing;
* digest sensitivity vectors (intrabar_exit_priority / timezone / cutoff /
  matching_engine_version / execution_policy each move ``content_digest``);
* the self-seal (``build`` produces a verifying digest, a tampered digest fails);
* deterministic, reproducible time computation against a REAL calendar built via
  ``build_trading_calendar`` (fixed ``Asia/Shanghai`` session list);
* the registry conflict / idempotency / seal / full-triple resolve verification;
* the honest ``UnsupportedBarFrequencyError`` refusal under ``shadow-match-v1``.

Run from repo root: ``pytest tests/orchestration/test_decision_schedule.py -v``
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.refs import ContentRef

_CST = ZoneInfo("Asia/Shanghai")
_CAL_ID = "ashare.xshg"
_WEEK = ("2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24")  # Mon..Fri


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _fields(**overrides):
    base = dict(
        id="shadow.daily.ashare",
        version="1",
        calendar_id=_CAL_ID,
        timezone="Asia/Shanghai",
        kind="daily",
        decision_local_time="09:30",
        cutoff_local_time="09:00",
        bar_frequency="1d",
        execution_policy="next_open",
        execution_price_field="open",
        matching_engine_version=shadow.SHADOW_MATCHING_ENGINE_VERSION,
    )
    base.update(overrides)
    return base


def _schedule(**overrides):
    return shadow.DecisionSchedule.build(**_fields(**overrides))


def _calendar(sessions_iso=_WEEK, *, calendar_id=_CAL_ID):
    return build_trading_calendar(
        calendar_id=calendar_id,
        sessions=[date.fromisoformat(s) for s in sessions_iso],
        material_id="cal.ashare.2026",
        material_version="1",
    )


# --------------------------------------------------------------------------- #
# module constants + error hierarchy                                          #
# --------------------------------------------------------------------------- #
def test_module_constants_pin_shadow_match_v1_and_ashare_anchors():
    assert shadow.SHADOW_MATCHING_ENGINE_VERSION == "shadow-match-v1"
    assert shadow.ASHARE_SESSION_OPEN == "09:30"
    assert shadow.ASHARE_SESSION_CLOSE == "15:00"


def test_error_classes_extend_shadow_contract_error():
    assert issubclass(shadow.ScheduleComputationError, shadow.ShadowContractError)
    assert issubclass(shadow.UnsupportedBarFrequencyError, shadow.ScheduleComputationError)
    for exc in (
        shadow.ScheduleConflictError,
        shadow.UnknownScheduleError,
        shadow.ScheduleRegistrySealedError,
    ):
        assert issubclass(exc, shadow.ShadowContractError)


# --------------------------------------------------------------------------- #
# field surface + valid construction per kind                                 #
# --------------------------------------------------------------------------- #
def test_valid_daily_schedule_constructs_and_seals():
    s = _schedule()
    assert s.schema_version == "1"
    assert s.kind == "daily"
    assert s.weekdays == () and s.rebalance_dates == ()
    assert s.intrabar_exit_priority == "worst_case"
    assert s.content_digest == s.semantic_digest()


def test_valid_weekly_and_rebalance_and_manual_construct():
    wk = _schedule(kind="weekly", weekdays=(1, 3))
    assert wk.weekdays == (1, 3)
    rb = _schedule(kind="rebalance_dates", rebalance_dates=("2026-07-20", "2026-07-27"))
    assert rb.rebalance_dates == ("2026-07-20", "2026-07-27")
    mn = _schedule(kind="manual")
    assert mn.kind == "manual"


# --------------------------------------------------------------------------- #
# validation matrix                                                           #
# --------------------------------------------------------------------------- #
def test_timezone_must_resolve_via_zoneinfo():
    with pytest.raises(ValidationError):
        _schedule(timezone="Not/AZone")


def test_bad_local_time_pattern_rejected():
    with pytest.raises(ValidationError):
        _schedule(decision_local_time="9:30")  # not zero-padded
    with pytest.raises(ValidationError):
        _schedule(cutoff_local_time="24:00")  # out of range


def test_cutoff_must_not_exceed_decision():
    with pytest.raises(ValidationError):
        _schedule(cutoff_local_time="09:31", decision_local_time="09:30")
    # equal + earlier are both accepted
    assert _schedule(cutoff_local_time="09:30", decision_local_time="09:30")
    assert _schedule(cutoff_local_time="08:00", decision_local_time="09:30")


def test_weekday_bounds_unique_sorted():
    with pytest.raises(ValidationError):
        _schedule(kind="weekly", weekdays=(0,))  # < 1
    with pytest.raises(ValidationError):
        _schedule(kind="weekly", weekdays=(8,))  # > 7
    with pytest.raises(ValidationError):
        _schedule(kind="weekly", weekdays=(3, 1))  # unsorted
    with pytest.raises(ValidationError):
        _schedule(kind="weekly", weekdays=(1, 1))  # duplicate


def test_rebalance_dates_format_unique_sorted():
    with pytest.raises(ValidationError):
        _schedule(kind="rebalance_dates", rebalance_dates=("2026/07/20",))  # bad format
    with pytest.raises(ValidationError):
        _schedule(kind="rebalance_dates", rebalance_dates=("2026-07-27", "2026-07-20"))  # unsorted
    with pytest.raises(ValidationError):
        _schedule(kind="rebalance_dates", rebalance_dates=("2026-07-20", "2026-07-20"))  # dup


# --------------------------------------------------------------------------- #
# kind matrix                                                                 #
# --------------------------------------------------------------------------- #
def test_kind_matrix_rejects_wrong_pairings():
    # daily / manual require BOTH empty
    with pytest.raises(ValidationError):
        _schedule(kind="daily", weekdays=(1,))
    with pytest.raises(ValidationError):
        _schedule(kind="daily", rebalance_dates=("2026-07-20",))
    with pytest.raises(ValidationError):
        _schedule(kind="manual", weekdays=(1,))
    # weekly requires non-empty weekdays + empty rebalance_dates
    with pytest.raises(ValidationError):
        _schedule(kind="weekly")  # empty weekdays
    with pytest.raises(ValidationError):
        _schedule(kind="weekly", weekdays=(1,), rebalance_dates=("2026-07-20",))
    # rebalance_dates requires non-empty rebalance_dates + empty weekdays
    with pytest.raises(ValidationError):
        _schedule(kind="rebalance_dates")  # empty rebalance_dates
    with pytest.raises(ValidationError):
        _schedule(kind="rebalance_dates", rebalance_dates=("2026-07-20",), weekdays=(1,))


# --------------------------------------------------------------------------- #
# execution pairing                                                           #
# --------------------------------------------------------------------------- #
def test_execution_policy_price_field_pairing():
    with pytest.raises(ValidationError):
        _schedule(execution_policy="next_open", execution_price_field="close")
    with pytest.raises(ValidationError):
        _schedule(execution_policy="next_bar_close", execution_price_field="open")
    # both valid pairings construct
    assert _schedule(execution_policy="next_open", execution_price_field="open")
    assert _schedule(execution_policy="next_bar_close", execution_price_field="close")


# --------------------------------------------------------------------------- #
# digest sensitivity vectors (invariant 1)                                    #
# --------------------------------------------------------------------------- #
def test_digest_moves_on_intrabar_exit_priority():
    a = _schedule(intrabar_exit_priority="worst_case")
    b = _schedule(intrabar_exit_priority="stop_first")
    assert a.content_digest != b.content_digest


def test_digest_moves_on_timezone():
    a = _schedule(timezone="Asia/Shanghai")
    b = _schedule(timezone="America/New_York")
    assert a.content_digest != b.content_digest


def test_digest_moves_on_cutoff():
    a = _schedule(cutoff_local_time="09:00")
    b = _schedule(cutoff_local_time="08:30")
    assert a.content_digest != b.content_digest


def test_digest_moves_on_matching_engine_version():
    a = _schedule(matching_engine_version="shadow-match-v1")
    b = _schedule(matching_engine_version="shadow-match-v2")
    assert a.content_digest != b.content_digest


def test_digest_moves_on_execution_policy():
    # execution_policy cannot move alone (pairing), so the paired field moves too;
    # the point is that changing the policy pair changes the digest.
    a = _schedule(execution_policy="next_open", execution_price_field="open")
    b = _schedule(execution_policy="next_bar_close", execution_price_field="close")
    assert a.content_digest != b.content_digest


# --------------------------------------------------------------------------- #
# self-seal                                                                   #
# --------------------------------------------------------------------------- #
def test_tampered_content_digest_is_rejected():
    fields = _fields()
    with pytest.raises(ValidationError):
        shadow.DecisionSchedule(**fields, content_digest="0" * 64)


def test_build_is_deterministic_across_calls():
    assert _schedule().content_digest == _schedule().content_digest


# --------------------------------------------------------------------------- #
# is_decision_point                                                           #
# --------------------------------------------------------------------------- #
def test_is_decision_point_daily():
    s = _schedule(kind="daily")
    cal = _calendar()
    assert shadow.is_decision_point(s, session_date="2026-07-20", calendar=cal) is True
    # non-session date is never a decision point
    assert shadow.is_decision_point(s, session_date="2026-07-25", calendar=cal) is False


def test_is_decision_point_weekly():
    s = _schedule(kind="weekly", weekdays=(1, 3))
    cal = _calendar()
    for iso in _WEEK:
        expect = date.fromisoformat(iso).isoweekday() in (1, 3)
        assert shadow.is_decision_point(s, session_date=iso, calendar=cal) is expect


def test_is_decision_point_rebalance_dates():
    s = _schedule(kind="rebalance_dates", rebalance_dates=("2026-07-21", "2026-07-23"))
    cal = _calendar()
    assert shadow.is_decision_point(s, session_date="2026-07-21", calendar=cal) is True
    assert shadow.is_decision_point(s, session_date="2026-07-22", calendar=cal) is False


def test_is_decision_point_manual_is_every_session():
    s = _schedule(kind="manual")
    cal = _calendar()
    assert shadow.is_decision_point(s, session_date="2026-07-22", calendar=cal) is True
    assert shadow.is_decision_point(s, session_date="2026-07-25", calendar=cal) is False


def test_is_decision_point_calendar_id_mismatch_raises():
    s = _schedule()
    cal = _calendar(calendar_id="other.calendar")
    with pytest.raises(shadow.ScheduleComputationError):
        shadow.is_decision_point(s, session_date="2026-07-20", calendar=cal)


# --------------------------------------------------------------------------- #
# compute_scheduled_for / compute_cutoff_at                                   #
# --------------------------------------------------------------------------- #
def test_compute_scheduled_for_is_utc_instant_of_local_decision_time():
    s = _schedule(decision_local_time="09:30", cutoff_local_time="09:00")
    cal = _calendar()
    got = shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)
    assert got == datetime(2026, 7, 20, 1, 30, tzinfo=timezone.utc)  # 09:30 CST -> 01:30Z
    # reproducible across calls
    again = shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)
    assert got == again


def test_compute_scheduled_for_refuses_non_decision_point():
    s = _schedule(kind="weekly", weekdays=(3,))  # Wednesday only
    cal = _calendar()
    with pytest.raises(shadow.ScheduleComputationError):
        shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)  # Monday


def test_compute_scheduled_for_refuses_non_session_date():
    s = _schedule(kind="daily")
    cal = _calendar()
    with pytest.raises(shadow.ScheduleComputationError):
        shadow.compute_scheduled_for(s, session_date="2026-07-25", calendar=cal)  # not a session


def test_compute_cutoff_at_precedes_decision():
    s = _schedule(decision_local_time="09:30", cutoff_local_time="09:00")
    cal = _calendar()
    cutoff = shadow.compute_cutoff_at(s, session_date="2026-07-20")
    decision = shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)
    assert cutoff == datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)  # 09:00 CST -> 01:00Z
    assert cutoff <= decision


# --------------------------------------------------------------------------- #
# compute_eligible_execution_at                                               #
# --------------------------------------------------------------------------- #
def test_eligible_execution_next_open_is_next_session_open():
    s = _schedule(execution_policy="next_open", execution_price_field="open")
    cal = _calendar()
    sched = shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)
    exec_at = shadow.compute_eligible_execution_at(s, scheduled_for=sched, calendar=cal)
    # first session strictly after 2026-07-20 is 2026-07-21, open 09:30 CST -> 01:30Z
    assert exec_at == datetime(2026, 7, 21, 1, 30, tzinfo=timezone.utc)
    assert exec_at > sched  # invariant 3


def test_eligible_execution_next_bar_close_is_next_session_close():
    s = _schedule(execution_policy="next_bar_close", execution_price_field="close")
    cal = _calendar()
    sched = shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)
    exec_at = shadow.compute_eligible_execution_at(s, scheduled_for=sched, calendar=cal)
    # next session 2026-07-21, close 15:00 CST -> 07:00Z
    assert exec_at == datetime(2026, 7, 21, 7, 0, tzinfo=timezone.utc)
    assert exec_at > sched


def test_eligible_execution_uses_local_date_not_utc_date():
    # decision 07:00 CST on 07-20 = 2026-07-19T23:00Z: the UTC date (07-19) differs
    # from the local date (07-20). The next session must be derived from the LOCAL
    # date 07-20 -> 07-21, never from the UTC date 07-19 -> 07-20.
    s = _schedule(decision_local_time="07:00", cutoff_local_time="06:30")
    cal = _calendar()
    sched = shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)
    assert sched == datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc)
    exec_at = shadow.compute_eligible_execution_at(s, scheduled_for=sched, calendar=cal)
    assert exec_at.astimezone(_CST).date() == date(2026, 7, 21)
    assert exec_at == datetime(2026, 7, 21, 1, 30, tzinfo=timezone.utc)


def test_eligible_execution_refuses_unsupported_frequency():
    s = _schedule(bar_frequency="60m")  # constructs fine (invariant 6)
    assert s.bar_frequency == "60m"
    cal = _calendar()
    sched = shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)
    with pytest.raises(shadow.UnsupportedBarFrequencyError):
        shadow.compute_eligible_execution_at(s, scheduled_for=sched, calendar=cal)


def test_eligible_execution_calendar_id_mismatch_raises():
    s = _schedule()
    cal = _calendar()
    sched = shadow.compute_scheduled_for(s, session_date="2026-07-20", calendar=cal)
    other = _calendar(calendar_id="other.calendar")
    with pytest.raises(shadow.ScheduleComputationError):
        shadow.compute_eligible_execution_at(s, scheduled_for=sched, calendar=other)


def test_eligible_execution_no_session_after_raises():
    s = _schedule()
    cal = _calendar()
    # scheduled on the LAST session of coverage -> no strictly-after session exists
    sched = shadow.compute_scheduled_for(s, session_date="2026-07-24", calendar=cal)
    with pytest.raises(shadow.ScheduleComputationError):
        shadow.compute_eligible_execution_at(s, scheduled_for=sched, calendar=cal)


def test_bar_frequency_non_1d_constructs_for_all_variants():
    for freq in ("60m", "30m", "15m", "5m", "1m"):
        assert _schedule(bar_frequency=freq).bar_frequency == freq


# --------------------------------------------------------------------------- #
# registry                                                                    #
# --------------------------------------------------------------------------- #
def test_register_returns_matching_content_ref_triple():
    s = _schedule()
    reg = shadow.DecisionScheduleRegistry()
    ref = reg.register(s)
    assert isinstance(ref, ContentRef)
    assert ref.id == s.id
    assert ref.version == s.version
    assert ref.content_digest == s.content_digest


def test_register_is_idempotent_for_identical_digest():
    reg = shadow.DecisionScheduleRegistry()
    ref1 = reg.register(_schedule())
    ref2 = reg.register(_schedule())  # separately built, identical content
    assert ref1 == ref2
    assert len(reg.manifest()) == 1


def test_register_same_key_different_digest_conflicts():
    reg = shadow.DecisionScheduleRegistry()
    reg.register(_schedule(decision_local_time="09:30"))
    with pytest.raises(shadow.ScheduleConflictError):
        reg.register(_schedule(decision_local_time="09:15"))


def test_resolve_returns_registered_schedule():
    s = _schedule()
    reg = shadow.DecisionScheduleRegistry()
    ref = reg.register(s)
    assert reg.resolve(ref) is s


def test_resolve_refuses_stale_digest():
    s = _schedule()
    reg = shadow.DecisionScheduleRegistry()
    reg.register(s)
    stale = ContentRef(id=s.id, version=s.version, content_digest="0" * 64)
    with pytest.raises(shadow.UnknownScheduleError):
        reg.resolve(stale)


def test_resolve_refuses_unknown_key():
    s = _schedule()
    reg = shadow.DecisionScheduleRegistry()
    reg.register(s)
    unknown = ContentRef(id="shadow.daily.zzz", version="1", content_digest=s.content_digest)
    with pytest.raises(shadow.UnknownScheduleError):
        reg.resolve(unknown)


def test_seal_blocks_register_but_allows_reads():
    s1 = _schedule(id="shadow.daily.a")
    s2 = _schedule(id="shadow.daily.b")
    reg = shadow.DecisionScheduleRegistry()
    ref1 = reg.register(s1)
    assert reg.sealed is False
    reg.seal()
    assert reg.sealed is True
    with pytest.raises(shadow.ScheduleRegistrySealedError):
        reg.register(s2)
    # reads still work
    assert reg.resolve(ref1) is s1
    assert len(reg.manifest()) == 1
    assert reg.registry_digest


def test_manifest_sorted_by_id_version():
    reg = shadow.DecisionScheduleRegistry()
    reg.register(_schedule(id="shadow.daily.b"))
    reg.register(_schedule(id="shadow.daily.a"))
    ids = [r.id for r in reg.manifest()]
    assert ids == sorted(ids)
    assert ids == ["shadow.daily.a", "shadow.daily.b"]


def test_registry_digest_is_registration_order_independent():
    s1 = _schedule(id="shadow.daily.a")
    s2 = _schedule(id="shadow.daily.b")
    reg_a = shadow.DecisionScheduleRegistry()
    reg_a.register(s1)
    reg_a.register(s2)
    reg_b = shadow.DecisionScheduleRegistry()
    reg_b.register(s2)
    reg_b.register(s1)
    assert reg_a.registry_digest == reg_b.registry_digest


def test_registry_digest_changes_with_content():
    reg_a = shadow.DecisionScheduleRegistry()
    reg_a.register(_schedule(decision_local_time="09:30"))
    reg_b = shadow.DecisionScheduleRegistry()
    reg_b.register(_schedule(decision_local_time="09:15"))
    assert reg_a.registry_digest != reg_b.registry_digest
