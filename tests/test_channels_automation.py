from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from guanlan_v2.channels.automation import (
    AutomationInvocation,
    AutomationValidationError,
    AutomationWorker,
    ScheduleCalculator,
    render_automation_system_turn,
)
from guanlan_v2.channels.automation_skills import (
    UnknownAutomationSkill,
    list_automation_skills,
    registered_skill_ids,
    render_skill_block,
    resolve_skill_refs,
)
from guanlan_v2.channels.models import AutomationRunStatus
from guanlan_v2.channels.runtime import ChannelRuntime, ChannelRuntimeError
from guanlan_v2.channels.store import ChannelConflict, ChannelNotFound, ChannelStore
from guanlan_v2.channels import store as channel_store_module
from guanlan_v2.channels.trading_calendar import AShareTradingCalendar, CalendarUnavailable
from guanlan_v2.console.store import ConsoleStore


UTC = timezone.utc


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _store(tmp_path, clock: MutableClock) -> ChannelStore:
    return ChannelStore(tmp_path / "channels.sqlite3", clock=clock)


def test_schedule_calculator_covers_once_daily_weekdays_and_hong_kong():
    calc = ScheduleCalculator(clock=lambda: datetime(2026, 8, 23, tzinfo=UTC))
    once = calc.validate(
        schedule_kind="once",
        timezone_name="Asia/Hong_Kong",
        run_at="2026-08-24T08:30:00+08:00",
    )
    assert calc.next_run_at(once, after="2026-08-24T00:29:59Z") == datetime(
        2026, 8, 24, 0, 30, tzinfo=UTC
    )
    assert calc.next_run_at(once, after="2026-08-24T00:30:00Z") is None

    daily = calc.validate(
        schedule_kind="daily", timezone_name="Asia/Hong_Kong", local_time="08:30"
    )
    assert calc.next_run_at(daily, after="2026-08-23T00:00:00Z") == datetime(
        2026, 8, 23, 0, 30, tzinfo=UTC
    )
    assert calc.next_run_at(daily, after="2026-08-23T00:30:00Z") == datetime(
        2026, 8, 24, 0, 30, tzinfo=UTC
    )

    weekdays = calc.validate(
        schedule_kind="weekdays",
        timezone_name="Asia/Hong_Kong",
        local_time="08:30",
    )
    # 2026-08-21 is Friday; the next local weekday is Monday.
    assert calc.next_run_at(weekdays, after="2026-08-21T01:00:00Z") == datetime(
        2026, 8, 24, 0, 30, tzinfo=UTC
    )


def test_schedule_calculator_has_deterministic_dst_gap_and_fold_semantics():
    calc = ScheduleCalculator()
    spec = calc.validate(
        schedule_kind="daily", timezone_name="America/New_York", local_time="02:30"
    )
    # 02:30 does not exist on the 2026 spring-forward date; normalize to
    # 03:30 EDT, a single real instant.
    assert calc.next_run_at(spec, after="2026-03-07T08:00:00Z") == datetime(
        2026, 3, 8, 7, 30, tzinfo=UTC
    )

    fold = calc.validate(
        schedule_kind="daily", timezone_name="America/New_York", local_time="01:30"
    )
    # Ambiguous 01:30 uses fold=0 (the first, EDT occurrence).
    assert calc.next_run_at(fold, after="2026-10-31T06:00:00Z") == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"schedule_kind": "cron", "local_time": "08:30"}, "schedule_kind"),
        ({"schedule_kind": "daily", "local_time": "8:30"}, "HH:MM"),
        ({"schedule_kind": "daily", "local_time": "08:30", "run_at": "2026-08-24T00:00:00Z"}, "run_at"),
        ({"schedule_kind": "once", "run_at": "2026-08-24T00:00:00"}, "timezone-aware"),
        ({"schedule_kind": "daily", "local_time": "08:30", "timezone_name": "Mars/Olympus"}, "IANA"),
    ],
)
def test_schedule_validation_is_structured_and_strict(kwargs, match):
    with pytest.raises(AutomationValidationError, match=match):
        ScheduleCalculator().validate(**kwargs)


def test_trading_days_use_only_injected_calendar_and_never_weekday_fallback():
    sessions = {date(2026, 8, 24), date(2026, 8, 26)}
    calc = ScheduleCalculator(calendar=sessions)
    spec = calc.validate(
        schedule_kind="trading_days",
        timezone_name="Asia/Hong_Kong",
        local_time="08:30",
    )
    assert calc.next_run_at(spec, after="2026-08-24T00:31:00Z") == datetime(
        2026, 8, 26, 0, 30, tzinfo=UTC
    )

    no_calendar = ScheduleCalculator()
    with pytest.raises(CalendarUnavailable, match="calendar"):
        no_calendar.next_run_at(spec, after="2026-08-24T00:31:00Z")


def test_a_share_calendar_is_lazy_normalized_cached_and_strict():
    calls = []

    class Loader:
        def _load_calendar(self, freq):
            calls.append(freq)
            return ["2026-08-21 00:00:00", datetime(2026, 8, 24, tzinfo=UTC)]

    provider = AShareTradingCalendar(loader_factory=lambda: Loader())
    assert calls == []
    assert provider.is_trading_day("2026-08-21")
    assert provider.trading_days("2026-08-22", "2026-08-25") == {
        date(2026, 8, 24)
    }
    assert calls == ["day"]

    failed = AShareTradingCalendar(loader_factory=lambda: (_ for _ in ()).throw(OSError()))
    with pytest.raises(CalendarUnavailable, match="calendar"):
        failed.is_trading_day(date(2026, 8, 24))


def test_automation_skill_registry_is_closed_and_renders_reviewed_rules():
    assert registered_skill_ids() == ("morning_brief_v1", "closing_review_v1")
    assert tuple(skill.skill_id for skill in list_automation_skills()) \
        == registered_skill_ids()
    assert [item.skill_id for item in resolve_skill_refs(["morning_brief_v1"])] \
        == ["morning_brief_v1"]
    block = render_skill_block(["morning_brief_v1"])
    assert "数据优先级" in block
    assert "失败降级" in block
    for unsafe in ("../morning.md", "https://example.com/skill", "unknown_v1"):
        with pytest.raises(UnknownAutomationSkill, match="unknown|registered"):
            resolve_skill_refs([unsafe])


def test_worker_upsert_preserves_exact_confirmed_prompt_and_calculates_next_run(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, 0, 0, tzinfo=UTC))
    worker = AutomationWorker(_store(tmp_path, clock), clock=clock)
    prompt = "  第一行\n第二行  "
    item = worker.upsert_automation(
        session_id="cs_a",
        name="晨报",
        schedule_kind="daily",
        local_time="08:30",
        prompt_text=prompt,
        skill_refs=["morning_brief_v1"],
        confirmed_at=clock(),
    )
    assert item.prompt_text == prompt
    assert item.confirmed_at == "2026-08-23T00:00:00.000000+00:00"
    assert item.next_run_at == "2026-08-23T00:30:00.000000+00:00"

    run = worker.store.create_automation_run(item.id, datetime(2026, 8, 23, 0, 30, tzinfo=UTC))
    rendered = render_automation_system_turn(item, run)
    assert prompt in rendered
    assert "morning_brief_v1" in rendered
    assert "chat_id" not in rendered.lower()


@pytest.mark.parametrize(
    "prompt",
    [
        "Token 是 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_123456",
        "telegram_chat_id=-1001234567890",
        "请读取 C:/Users/me/secret.txt",
        "含有\x00空字符",
    ],
)
def test_worker_rejects_channel_secrets_and_local_paths_in_confirmed_prompt(tmp_path, prompt):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    worker = AutomationWorker(_store(tmp_path, clock), clock=clock)
    with pytest.raises(AutomationValidationError):
        worker.upsert_automation(
            session_id="cs_a",
            name="不安全",
            schedule_kind="daily",
            local_time="08:30",
            prompt_text=prompt,
            confirmed_at=clock(),
        )


@pytest.mark.parametrize(
    "name",
    [
        "晨报\n伪造字段",
        "telegram_chat_id=-1001234567890",
        r"\\server\share\report",
        "file:///tmp/report.png",
    ],
)
def test_worker_rejects_unsafe_automation_names(tmp_path, name):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    worker = AutomationWorker(_store(tmp_path, clock), clock=clock)
    with pytest.raises(AutomationValidationError, match="name"):
        worker.upsert_automation(
            session_id="cs_a",
            name=name,
            schedule_kind="daily",
            local_time="08:30",
            prompt_text="执行",
            confirmed_at=clock(),
        )


def test_worker_accepts_pairing_shaped_ordinary_name_and_prompt(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    worker = AutomationWorker(_store(tmp_path, clock), clock=clock)
    prompt = "比较 TELEGRAM 与 ABCDEFGH，并运行 23456789 号 BACKTEST。"

    item = worker.upsert_automation(
        session_id="cs_a",
        name="BACKTEST",
        schedule_kind="daily",
        local_time="08:30",
        prompt_text=prompt,
        confirmed_at=clock(),
    )
    run = worker.store.create_automation_run(item.id, item.next_run_at)
    rendered = render_automation_system_turn(item, run)

    assert item.name == "BACKTEST"
    assert item.prompt_text == prompt
    assert prompt in rendered


def test_trading_day_upsert_is_durably_blocked_when_calendar_is_unavailable(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    worker = AutomationWorker(_store(tmp_path, clock), clock=clock, calendar=None)
    item = worker.upsert_automation(
        session_id="cs_a",
        name="交易日晨报",
        schedule_kind="trading_days",
        local_time="08:30",
        prompt_text="执行交易日晨报",
        confirmed_at=clock(),
    )
    assert item.enabled is False
    assert item.last_status == "blocked"
    assert item.pause_reason == "calendar_unavailable"
    assert item.next_run_at is None


def test_once_upsert_rejects_past_run_instead_of_creating_ghost_task(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, 1, 0, tzinfo=UTC))
    worker = AutomationWorker(_store(tmp_path, clock), clock=clock)
    with pytest.raises(AutomationValidationError, match="past"):
        worker.upsert_automation(
            session_id="cs_a",
            name="已过期",
            schedule_kind="once",
            run_at="2026-08-23T00:59:59Z",
            prompt_text="执行",
            confirmed_at=clock(),
        )
    immediate = worker.upsert_automation(
        session_id="cs_a",
        name="立即执行",
        schedule_kind="once",
        run_at=clock(),
        prompt_text="执行",
        confirmed_at=clock(),
    )
    assert immediate.enabled is True
    assert immediate.next_run_at == "2026-08-23T01:00:00.000000+00:00"


def test_materializer_keeps_only_latest_misfire_and_applies_six_hour_grace(tmp_path):
    clock = MutableClock(datetime(2026, 8, 20, 0, 0, tzinfo=UTC))
    store = _store(tmp_path, clock)
    worker = AutomationWorker(store, clock=clock)
    item = worker.upsert_automation(
        session_id="cs_a",
        name="晨报",
        schedule_kind="daily",
        local_time="08:30",
        prompt_text="生成晨报",
        confirmed_at=clock(),
    )
    clock.value = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)  # 10:00 HK
    stats = worker.materialize_due()
    runs = store.automation_runs_for(item.id)
    assert stats.materialized == 1
    assert len(runs) == 1
    assert runs[0].scheduled_for == "2026-08-23T00:30:00.000000+00:00"
    assert runs[0].status == AutomationRunStatus.QUEUED
    assert store.automation(item.id).next_run_at == "2026-08-24T00:30:00.000000+00:00"

    late = store.create_automation(
        name="过期复盘",
        session_id="cs_b",
        schedule_kind="daily",
        local_time="08:30",
        timezone_name="Asia/Hong_Kong",
        prompt_text="生成复盘",
        next_run_at="2026-08-20T00:30:00Z",
        confirmed_at="2026-08-20T00:00:00Z",
    )
    clock.value = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)  # 16:00 HK, 7.5h late
    stats = worker.materialize_due()
    late_runs = store.automation_runs_for(late.id)
    assert stats.missed == 1
    assert len(late_runs) == 1
    assert late_runs[0].scheduled_for == "2026-08-23T00:30:00.000000+00:00"
    assert late_runs[0].status == AutomationRunStatus.MISSED
    assert late_runs[0].error_class == "misfire_grace_exceeded"


def test_occurrence_insert_is_idempotent_and_two_schedulers_cannot_claim_same_run(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, 0, 30, tzinfo=UTC))
    first_store = _store(tmp_path, clock)
    second_store = ChannelStore(first_store.path, clock=clock)
    automation = first_store.create_automation(
        name="晨报",
        session_id="cs_a",
        schedule_kind="daily",
        local_time="08:30",
        timezone_name="Asia/Hong_Kong",
        prompt_text="生成晨报",
        confirmed_at=clock(),
    )
    occurrence = datetime(2026, 8, 23, 0, 30, tzinfo=UTC)
    one = first_store.create_automation_run(automation.id, occurrence)
    two = second_store.create_automation_run(automation.id, occurrence)
    assert one.id == two.id

    claimed_one = first_store.claim_automation_runs(
        lease_owner="scheduler-a", now=clock(), lease_seconds=60
    )
    claimed_two = second_store.claim_automation_runs(
        lease_owner="scheduler-b", now=clock(), lease_seconds=60
    )
    assert [run.id for run in claimed_one] == [one.id]
    assert claimed_two == []

    clock.value += timedelta(seconds=61)
    reclaimed = second_store.claim_automation_runs(
        lease_owner="scheduler-b", now=clock(), lease_seconds=60
    )
    assert [run.id for run in reclaimed] == [one.id]
    assert reclaimed[0].lease_owner == "scheduler-b"


def test_automation_ids_cannot_accidentally_form_pairing_codes(tmp_path, monkeypatch):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    monkeypatch.setattr(
        channel_store_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="2" * 32),
    )
    store = _store(tmp_path, clock)
    automation = store.create_automation(
        name="安全 ID",
        session_id="cs_a",
        schedule_kind="once",
        run_at=clock(),
        timezone_name="Asia/Hong_Kong",
        prompt_text="执行",
        confirmed_at=clock(),
    )
    run = store.create_automation_run(automation.id, clock())
    assert automation.id == "auto_222222x222222x222222x222222x222222x22"
    assert run.id == "arun_222222x222222x222222x222222x222222x22"
    assert "22222222" not in automation.id
    assert "22222222" not in run.id


def test_busy_session_stays_queued_then_submits_through_single_hook(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, 0, 30, tzinfo=UTC))
    store = _store(tmp_path, clock)
    worker = AutomationWorker(store, clock=clock, owner="scheduler-a")
    item = worker.upsert_automation(
        session_id="cs_a",
        name="晨报",
        schedule_kind="daily",
        local_time="08:30",
        prompt_text="生成晨报",
        confirmed_at=clock(),
    )
    busy = {"value": True}
    submitted: list[AutomationInvocation] = []

    async def submit(invocation):
        submitted.append(invocation)
        return {"status": "started", "turn_id": "turn_001"}

    worker.bind_hooks(
        is_busy=lambda sid: busy["value"],
        submit_system_turn=submit,
        is_archived=lambda sid: False,
    )
    first = asyncio.run(worker.tick())
    run = store.automation_runs_for(item.id)[0]
    assert first.busy == 1
    assert submitted == []
    assert run.status == AutomationRunStatus.QUEUED
    assert run.lease_owner is None

    busy["value"] = False
    second = asyncio.run(worker.tick())
    running = store.automation_runs_for(item.id)[0]
    assert second.started == 1
    assert len(submitted) == 1
    assert submitted[0].session_id == "cs_a"
    assert submitted[0].automation_run_id == run.id
    assert running.status == AutomationRunStatus.RUNNING
    assert running.turn_id == "turn_001"

    done = worker.on_turn_terminal(run.id, "turn_001", True, final_event_id=41)
    duplicate = worker.on_turn_terminal(run.id, "turn_001", True, final_event_id=42)
    stale = worker.on_turn_terminal(run.id, "turn_other", False, error_class="wrong")
    assert done is not None and done.status == AutomationRunStatus.DONE
    assert duplicate is not None and duplicate.final_event_id == 41
    assert stale is None
    assert store.automation(item.id).last_status == "done"


def test_submit_busy_result_shape_releases_claim_without_error(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, 0, 30, tzinfo=UTC))
    store = _store(tmp_path, clock)
    worker = AutomationWorker(store, clock=clock, owner="scheduler-a")
    item = worker.upsert_automation(
        session_id="cs_a",
        name="晨报",
        schedule_kind="daily",
        local_time="08:30",
        prompt_text="生成晨报",
        confirmed_at=clock(),
    )
    worker.bind_hooks(
        is_busy=lambda sid: False,
        submit_system_turn=lambda invocation: {"ok": False, "reason_code": "busy"},
    )
    stats = asyncio.run(worker.tick())
    run = store.automation_runs_for(item.id)[0]
    assert stats.busy == 1
    assert stats.errors == 0
    assert run.status == AutomationRunStatus.QUEUED
    assert run.lease_owner is None


def _seed_running(store: ChannelStore, clock: MutableClock, *, owner: str, turn_id: str):
    automation = store.create_automation(
        name=turn_id,
        session_id=f"cs_{turn_id}",
        schedule_kind="once",
        run_at=clock(),
        timezone_name="Asia/Hong_Kong",
        prompt_text="执行",
        confirmed_at=clock(),
    )
    run = store.create_automation_run(automation.id, clock())
    assert store.claim_automation_runs(
        lease_owner=owner, now=clock(), lease_seconds=60
    )
    running = store.start_automation_run(
        run.id,
        lease_owner=owner,
        turn_id=turn_id,
        now=clock(),
        lease_seconds=60,
    )
    assert running is not None
    return running


def test_restart_recovery_skips_other_live_owner_and_never_blindly_reruns(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    store = _store(tmp_path, clock)
    live = _seed_running(store, clock, owner="scheduler-live", turn_id="turn_live")
    inspected = []
    replacement = AutomationWorker(store, clock=clock, owner="scheduler-new")
    replacement.bind_hooks(
        is_busy=lambda sid: False,
        submit_system_turn=lambda invocation: "must_not_submit",
        inspect_terminal=lambda run: inspected.append(run.id),
    )
    assert asyncio.run(replacement.recover_startup()) == 0
    assert inspected == []
    assert store.automation_run(live.id).status == AutomationRunStatus.RUNNING

    clock.value += timedelta(seconds=61)
    # Recovery is periodic: a lease that was live at startup must converge once
    # it expires, without requiring another service restart or resubmitting.
    stats = asyncio.run(replacement.tick())
    assert stats.started == 0
    assert inspected == [live.id]
    recovered = store.automation_run(live.id)
    assert recovered.status == AutomationRunStatus.ERROR
    assert recovered.error_class == "service_restart_interrupted"


def test_restart_recovery_converges_from_console_terminal_evidence(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    store = _store(tmp_path, clock)
    running = _seed_running(store, clock, owner="scheduler-old", turn_id="turn_done")
    clock.value += timedelta(seconds=61)
    worker = AutomationWorker(store, clock=clock, owner="scheduler-new")
    worker.bind_hooks(
        is_busy=lambda sid: False,
        submit_system_turn=lambda invocation: None,
        inspect_terminal=lambda run: {
            "terminal": True,
            "ok": True,
            "final_event_id": 99,
            "error_class": None,
        },
    )
    assert asyncio.run(worker.recover_startup()) == 1
    done = store.automation_run(running.id)
    assert done.status == AutomationRunStatus.DONE
    assert done.final_event_id == 99


def test_management_is_session_owned_and_pause_resume_delete_are_durable(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    worker = AutomationWorker(_store(tmp_path, clock), clock=clock)
    item = worker.upsert_automation(
        session_id="cs_a",
        name="晨报",
        schedule_kind="daily",
        local_time="08:30",
        prompt_text="执行",
        confirmed_at=clock(),
    )
    assert [row.id for row in worker.list_automations("cs_a")] == [item.id]
    assert worker.list_automations("cs_b") == []
    with pytest.raises(ChannelNotFound, match="session"):
        worker.pause_automation("cs_b", item.id)

    paused = worker.pause_automation("cs_a", item.id)
    assert paused.enabled is False and paused.pause_reason == "manual"
    resumed = worker.resume_automation("cs_a", item.id)
    assert resumed.enabled is True and resumed.pause_reason is None
    assert resumed.next_run_at is not None
    assert worker.delete_automation("cs_a", item.id)
    assert worker.list_automations("cs_a") == []


def test_running_automation_cannot_be_deleted(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
    store = _store(tmp_path, clock)
    running = _seed_running(store, clock, owner="scheduler-a", turn_id="turn_a")
    with pytest.raises(ChannelConflict, match="running"):
        store.delete_automation(running.automation_id, expected_session_id="cs_turn_a")


def test_start_stop_are_idempotent_and_stop_releases_owned_lease(tmp_path):
    async def scenario():
        clock = MutableClock(datetime(2026, 8, 23, tzinfo=UTC))
        store = _store(tmp_path, clock)
        worker = AutomationWorker(
            store,
            clock=clock,
            owner="scheduler-a",
            interval_seconds=3600,
        )
        worker.bind_hooks(
            is_busy=lambda sid: True,
            submit_system_turn=lambda invocation: None,
        )
        await worker.start()
        await worker.start()
        await worker.stop()
        await worker.stop()
        assert store.running_automation_runs(lease_owner="scheduler-a") == []

    asyncio.run(scenario())


class _RuntimeSecrets:
    """No credential method may be reached by automation-only tests."""


class _RuntimeTelegram:
    """No Bot API method may be reached by automation-only tests."""


def _runtime(tmp_path, clock: MutableClock):
    console = ConsoleStore(root=tmp_path / "console")
    channel_store = ChannelStore(tmp_path / "runtime-channels.sqlite3", clock=clock)
    runtime = ChannelRuntime(
        console_store=console,
        channel_store=channel_store,
        secret_store=_RuntimeSecrets(),
        telegram_adapter=_RuntimeTelegram(),
        automation_clock=clock,
    )
    return runtime, console, channel_store


def test_real_runtime_automation_facade_is_exact_session_owned_and_secret_safe(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, 0, 0, tzinfo=UTC))
    runtime, console, channel_store = _runtime(tmp_path, clock)
    sid_a = console.create_session("A")['id']
    sid_b = console.create_session("B")['id']
    prompt = "  TELEGRAM 与 ABCDEFGH 因子 BACKTEST。\n保留这两端空格。  "
    spec = {
        "automation_id": "",
        "name": "BACKTEST",
        "schedule_kind": "daily",
        "run_at": "",
        "local_time": "08:30",
        "timezone": "Asia/Hong_Kong",
        "prompt_text": prompt,
        "skill_refs": ["morning_brief_v1"],
        "media_policy": "explicit",
    }

    skills = runtime.automation_skills()
    created = runtime.automation_upsert(sid_a, spec)
    automation_id = created["automation_id"]
    listed = runtime.automation_list(sid_a)

    assert skills == {
        "ok": True,
        "skills": [
            {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "description": skill.description,
            }
            for skill in list_automation_skills()
        ],
    }
    assert listed["ok"] is True
    assert listed["automations"][0]["automation_id"] == automation_id
    assert listed["automations"][0]["prompt_text"] == prompt
    assert channel_store.automation(automation_id).prompt_text == prompt
    assert runtime.automation_list(sid_b)["automations"] == []
    with pytest.raises(ChannelRuntimeError) as foreign:
        runtime.automation_pause(sid_b, automation_id)
    assert foreign.value.reason_code == "automation_not_found"

    for sensitive in (
        r"读取 C:\Users\Alice\secret.csv",
        "telegram user id=123456789",
        "Token 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_123456",
    ):
        rejected = dict(spec, prompt_text=sensitive, automation_id="")
        with pytest.raises(ChannelRuntimeError) as caught:
            runtime.automation_upsert(sid_a, rejected)
        assert caught.value.reason_code == "automation_sensitive_content_rejected"
    assert len(channel_store.automations_for_session(sid_a)) == 1
    assert console.read_events(sid_a) == []


def test_real_runtime_worker_uses_console_hook_and_terminal_turn_identity(tmp_path):
    clock = MutableClock(datetime(2026, 8, 23, 0, 30, tzinfo=UTC))
    runtime, console, channel_store = _runtime(tmp_path, clock)
    sid = console.create_session("晨报")['id']
    created = runtime.automation_upsert(sid, {
        "automation_id": "",
        "name": "晨报",
        "schedule_kind": "daily",
        "run_at": "",
        "local_time": "08:30",
        "timezone": "Asia/Hong_Kong",
        "prompt_text": "生成晨报",
        "skill_refs": [],
        "media_policy": "none",
    })
    submissions = []

    async def submit_system_turn(session_id, run_id, system_text, media_policy):
        submissions.append((session_id, run_id, system_text, media_policy))
        return {"ok": True, "turn_id": "at_safe_turn"}

    runtime.bind_console_hooks(
        emit=lambda *args, **kwargs: None,
        is_busy=lambda session_id: False,
        submit_system_turn=submit_system_turn,
        on_turn_terminal=lambda *args, **kwargs: None,
    )
    stats = asyncio.run(runtime.automation_worker.tick())
    run = channel_store.automation_runs_for(created["automation_id"])[0]

    assert stats.started == 1
    assert len(submissions) == 1
    assert submissions[0][0] == sid
    assert submissions[0][1] == run.id
    assert "【已确认 Prompt】\n生成晨报" in submissions[0][2]
    assert submissions[0][3] == "none"
    assert run.status == AutomationRunStatus.RUNNING
    assert run.turn_id == "at_safe_turn"

    runtime.automation_turn_terminal(run.id, "at_wrong_turn", False)
    assert channel_store.automation_run(run.id).status == AutomationRunStatus.RUNNING
    runtime.automation_turn_terminal(run.id, "at_safe_turn", True, final_event_id=17)
    done = channel_store.automation_run(run.id)
    assert done.status == AutomationRunStatus.DONE
    assert done.final_event_id == 17


def test_real_runtime_start_stop_owns_one_bound_automation_worker(tmp_path):
    async def scenario():
        clock = MutableClock(datetime(2026, 8, 23, 0, 0, tzinfo=UTC))
        runtime, _console, _channel_store = _runtime(tmp_path, clock)
        runtime.bind_console_hooks(
            emit=lambda *args, **kwargs: None,
            is_busy=lambda session_id: False,
            submit_system_turn=lambda *args, **kwargs: {
                "ok": False, "reason_code": "busy",
            },
            on_turn_terminal=lambda *args, **kwargs: None,
        )

        await runtime.start()
        first_worker_task = runtime.automation_worker._task
        assert first_worker_task is not None and not first_worker_task.done()
        assert len(runtime._background_tasks) == 2

        await runtime.start()
        assert runtime.automation_worker._task is first_worker_task

        await runtime.stop()
        assert runtime.automation_worker._task is None
        assert runtime._background_tasks == ()

    asyncio.run(scenario())
