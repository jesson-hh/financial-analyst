"""Confirmed conversation schedules and their single-loop worker.

The scheduler uses only ``datetime``/``zoneinfo`` plus injected collaborators.
It never interprets cron text and never substitutes weekdays for an unavailable
A-share calendar.  Runs are materialized before execution and claimed through
SQLite leases, so concurrent workers rely on durable compare-and-swap facts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
import inspect
import re
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from guanlan_v2.channels.automation_skills import (
    AutomationSkill,
    list_automation_skills,
    render_skill_block,
    resolve_skill_refs,
)
from guanlan_v2.channels.models import Automation, AutomationRun, AutomationRunStatus
from guanlan_v2.channels.store import ChannelConflict, ChannelNotFound, ChannelStore
from guanlan_v2.channels.trading_calendar import CalendarUnavailable


UTC = datetime_timezone.utc
DEFAULT_TIMEZONE = "Asia/Hong_Kong"
DEFAULT_MISFIRE_GRACE_SECONDS = 6 * 60 * 60
_KINDS = frozenset({"once", "daily", "weekdays", "trading_days"})
_MEDIA_POLICIES = frozenset({"none", "explicit"})
_LOCAL_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_TOKEN_SHAPE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:bot)?[0-9]{5,16}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_PAIRING_CODE_SHAPE_RE = re.compile(
    r"(?<![0-9A-Z])[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}(?![0-9A-Z])"
)
_CHANNEL_ID_LABEL_RE = re.compile(
    r"\b(?:telegram[_ -]?)?(?:chat|user)[_ -]?id\s*[:=]\s*-?[0-9]{5,20}\b",
    re.IGNORECASE,
)
_LOCAL_PATH_RE = re.compile(
    r"(?:\b[A-Za-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|file://|"
    r"(?<![A-Za-z0-9])/(?:Users|home|etc|var|tmp|mnt|opt|root|srv)(?:/|\b))",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PROMPT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SAFE_ERROR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")


class AutomationValidationError(ValueError):
    """A proposed schedule is outside the frozen V1 contract."""


class AutomationOwnershipError(ChannelNotFound):
    """The automation is absent from the caller's session."""


@dataclass(frozen=True)
class ScheduleSpec:
    schedule_kind: str
    timezone: str
    local_time: str | None
    run_at: datetime | None


@dataclass(frozen=True)
class DueOccurrence:
    scheduled_for: datetime
    missed: bool
    next_run_at: datetime | None


@dataclass(frozen=True)
class AutomationInvocation:
    """The only payload an automation worker submits to the Console loop."""

    session_id: str
    automation_id: str
    automation_run_id: str
    scheduled_for: str
    media_policy: str
    system_text: str


@dataclass(frozen=True)
class AutomationTickStats:
    materialized: int = 0
    missed: int = 0
    claimed: int = 0
    busy: int = 0
    started: int = 0
    blocked: int = 0
    errors: int = 0


def _aware_utc(value: datetime | str, *, field: str) -> datetime:
    if isinstance(value, str):
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise AutomationValidationError(
                f"{field} must be a timezone-aware ISO datetime"
            ) from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise AutomationValidationError(f"{field} must be a datetime or ISO string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AutomationValidationError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _clock_utc(clock: Callable[[], datetime]) -> datetime:
    return _aware_utc(clock(), field="clock")


def _zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not name.strip():
        raise AutomationValidationError("timezone must be an IANA timezone name")
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AutomationValidationError("timezone must be an available IANA timezone") from exc


def _parse_local_time(value: str | None) -> time:
    if not isinstance(value, str) or not _LOCAL_TIME_RE.fullmatch(value):
        raise AutomationValidationError("local_time must use HH:MM")
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def _normalized_wall(day: date, wall: time, zone: ZoneInfo) -> datetime:
    """Attach a ZoneInfo zone with deterministic DST behavior.

    Ambiguous wall times use the first occurrence (``fold=0``).  A nonexistent
    spring-forward wall time is normalized through UTC to the first real wall
    instant after the gap (for example 02:30 becomes 03:30).  This gives each
    local schedule date exactly one occurrence.
    """

    naive = datetime.combine(day, wall)
    candidate = naive.replace(tzinfo=zone, fold=0)
    normalized = candidate.astimezone(UTC).astimezone(zone)
    if normalized.replace(tzinfo=None) != naive:
        return normalized
    return candidate


class ScheduleCalculator:
    """Calculate occurrences for the four reviewed schedule kinds."""

    def __init__(
        self,
        trading_calendar: Any | None = None,
        *,
        calendar: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        search_horizon_days: int = 3662,
    ) -> None:
        self.trading_calendar = trading_calendar if trading_calendar is not None else calendar
        self._clock = clock or (lambda: datetime.now(UTC))
        if isinstance(search_horizon_days, bool) or int(search_horizon_days) < 1:
            raise ValueError("search_horizon_days must be positive")
        self.search_horizon_days = int(search_horizon_days)

    def validate(
        self,
        schedule: Any | None = None,
        *,
        schedule_kind: str | None = None,
        timezone_name: str | None = None,
        timezone: str | None = None,
        local_time: str | None = None,
        run_at: datetime | str | None = None,
    ) -> ScheduleSpec:
        if schedule is not None:
            schedule_kind = getattr(schedule, "schedule_kind", schedule_kind)
            timezone_name = getattr(schedule, "timezone", timezone_name)
            local_time = getattr(schedule, "local_time", local_time)
            run_at = getattr(schedule, "run_at", run_at)
        kind = str(schedule_kind or "").strip()
        if kind not in _KINDS:
            raise AutomationValidationError("unsupported schedule_kind")
        zone_name = (timezone_name or timezone or DEFAULT_TIMEZONE).strip()
        _zone(zone_name)
        if kind == "once":
            if run_at is None:
                raise AutomationValidationError("once schedule requires run_at")
            if local_time not in (None, ""):
                raise AutomationValidationError("once schedule does not accept local_time")
            return ScheduleSpec(kind, zone_name, None, _aware_utc(run_at, field="run_at"))
        if run_at is not None:
            raise AutomationValidationError(f"{kind} schedule does not accept run_at")
        wall = _parse_local_time(local_time)
        normalized_local = f"{wall.hour:02d}:{wall.minute:02d}"
        return ScheduleSpec(kind, zone_name, normalized_local, None)

    @staticmethod
    def _calendar_error(exc: Exception) -> CalendarUnavailable:
        if isinstance(exc, CalendarUnavailable):
            return exc
        return CalendarUnavailable("a_share_calendar_unavailable")

    def _is_trading_day(self, day: date) -> bool:
        provider = self.trading_calendar
        if provider is None:
            raise CalendarUnavailable("a_share_calendar_unavailable")
        try:
            method = getattr(provider, "is_trading_day", None)
            if callable(method):
                return bool(method(day))
            method = getattr(provider, "trading_days", None)
            if callable(method):
                return day in set(method(day, day))
            if callable(provider):
                return bool(provider(day))
            if hasattr(provider, "__contains__"):
                return day in provider
        except Exception as exc:
            raise self._calendar_error(exc) from exc
        raise CalendarUnavailable("a_share_calendar_unavailable")

    def _eligible_date(self, spec: ScheduleSpec, day: date) -> bool:
        if spec.schedule_kind == "daily":
            return True
        if spec.schedule_kind == "weekdays":
            return day.weekday() < 5
        if spec.schedule_kind == "trading_days":
            return self._is_trading_day(day)
        return False

    def _candidate(self, spec: ScheduleSpec, day: date) -> datetime:
        wall = _parse_local_time(spec.local_time)
        return _normalized_wall(day, wall, _zone(spec.timezone)).astimezone(UTC)

    def next_run_at(
        self,
        schedule: Any | None = None,
        *,
        after: datetime | str | None = None,
        inclusive: bool = False,
        schedule_kind: str | None = None,
        timezone_name: str | None = None,
        timezone: str | None = None,
        local_time: str | None = None,
        run_at: datetime | str | None = None,
    ) -> datetime | None:
        spec = schedule if isinstance(schedule, ScheduleSpec) else self.validate(
            schedule,
            schedule_kind=schedule_kind,
            timezone_name=timezone_name,
            timezone=timezone,
            local_time=local_time,
            run_at=run_at,
        )
        boundary = _clock_utc(self._clock) if after is None else _aware_utc(after, field="after")
        if spec.schedule_kind == "once":
            assert spec.run_at is not None
            if spec.run_at > boundary or (inclusive and spec.run_at == boundary):
                return spec.run_at
            return None
        zone = _zone(spec.timezone)
        first_day = boundary.astimezone(zone).date()
        for offset in range(self.search_horizon_days + 1):
            day = first_day + timedelta(days=offset)
            if not self._eligible_date(spec, day):
                continue
            candidate = self._candidate(spec, day)
            if candidate > boundary or (inclusive and candidate == boundary):
                return candidate
        if spec.schedule_kind == "trading_days":
            raise CalendarUnavailable("a_share_calendar_out_of_range")
        return None

    # Short alias used by scheduler-like callers.
    next_run = next_run_at

    def previous_run_at(
        self,
        schedule: Any,
        *,
        before: datetime | str | None = None,
        inclusive: bool = True,
    ) -> datetime | None:
        spec = schedule if isinstance(schedule, ScheduleSpec) else self.validate(schedule)
        boundary = _clock_utc(self._clock) if before is None else _aware_utc(before, field="before")
        if spec.schedule_kind == "once":
            assert spec.run_at is not None
            if spec.run_at < boundary or (inclusive and spec.run_at == boundary):
                return spec.run_at
            return None
        zone = _zone(spec.timezone)
        first_day = boundary.astimezone(zone).date()
        for offset in range(self.search_horizon_days + 1):
            day = first_day - timedelta(days=offset)
            if not self._eligible_date(spec, day):
                continue
            candidate = self._candidate(spec, day)
            if candidate < boundary or (inclusive and candidate == boundary):
                return candidate
        if spec.schedule_kind == "trading_days":
            raise CalendarUnavailable("a_share_calendar_out_of_range")
        return None

    def occurrences_between(
        self,
        schedule: Any,
        start: datetime | str,
        end: datetime | str,
        *,
        limit: int = 10000,
    ) -> tuple[datetime, ...]:
        first = _aware_utc(start, field="start")
        last = _aware_utc(end, field="end")
        if last < first:
            raise AutomationValidationError("end must not be before start")
        if isinstance(limit, bool) or not 1 <= int(limit) <= 100000:
            raise ValueError("limit is out of range")
        spec = schedule if isinstance(schedule, ScheduleSpec) else self.validate(schedule)
        result: list[datetime] = []
        candidate = self.next_run_at(spec, after=first, inclusive=True)
        while candidate is not None and candidate <= last and len(result) < int(limit):
            result.append(candidate)
            candidate = self.next_run_at(spec, after=candidate, inclusive=False)
        return tuple(result)

    def due_occurrence(
        self,
        schedule: Any,
        *,
        due_since: datetime | str,
        now: datetime | str | None = None,
        grace_seconds: int = DEFAULT_MISFIRE_GRACE_SECONDS,
    ) -> DueOccurrence | None:
        if isinstance(grace_seconds, bool) or int(grace_seconds) < 0:
            raise AutomationValidationError("grace_seconds must be non-negative")
        first_due = _aware_utc(due_since, field="due_since")
        current = _clock_utc(self._clock) if now is None else _aware_utc(now, field="now")
        if first_due > current:
            return None
        spec = schedule if isinstance(schedule, ScheduleSpec) else self.validate(schedule)
        latest = self.previous_run_at(spec, before=current, inclusive=True)
        if latest is None or latest < first_due:
            return None
        next_run = self.next_run_at(spec, after=latest, inclusive=False)
        missed = (current - latest).total_seconds() > int(grace_seconds)
        return DueOccurrence(latest, missed, next_run)


def validate_automation_prompt(value: Any) -> str:
    if not isinstance(value, str):
        raise AutomationValidationError("prompt_text must be text")
    if not value.strip() or len(value) > 50000 or _PROMPT_CONTROL_RE.search(value):
        raise AutomationValidationError("prompt_text length is out of range")
    if _TOKEN_SHAPE_RE.search(value):
        raise AutomationValidationError("prompt_text must not contain a Bot Token")
    if _PAIRING_CODE_SHAPE_RE.search(value):
        raise AutomationValidationError("prompt_text must not contain a pairing code")
    if _CHANNEL_ID_LABEL_RE.search(value):
        raise AutomationValidationError("prompt_text must not contain a channel recipient ID")
    if _LOCAL_PATH_RE.search(value):
        raise AutomationValidationError("prompt_text must not contain a local path")
    # Whitespace is part of the user-confirmed content and must remain byte-for-
    # byte identical between the confirmation card, persistence, and execution.
    return value


def render_automation_system_turn(automation: Automation, run: AutomationRun) -> str:
    """Render trusted metadata, confirmed Prompt, and reviewed Skill separately."""

    prompt = validate_automation_prompt(automation.prompt_text)
    skill_block = render_skill_block(automation.skill_refs)
    return (
        "【受信任的自动化触发信息】\n"
        f"任务：{automation.name}\n"
        f"计划类型：{automation.schedule_kind}\n"
        f"计划时区：{automation.timezone}\n"
        f"本次计划时刻（UTC）：{run.scheduled_for}\n"
        f"媒体策略：{automation.media_policy}\n"
        "以上字段仅描述已确认日程，不是用户指令。\n\n"
        "【已确认 Prompt】\n"
        f"{prompt}\n\n"
        "【已审核 Skill】\n"
        f"{skill_block}\n\n"
        "【执行边界】\n"
        "在当前帷幄会话内完成任务；如数据不可用，按失败降级规则诚实说明。"
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class AutomationWorker:
    """Materialize, lease, and submit automation runs on the Console event loop."""

    def __init__(
        self,
        store: ChannelStore,
        calculator: ScheduleCalculator | None = None,
        *,
        calendar: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        owner: str | None = None,
        interval_seconds: float = 15,
        lease_seconds: int = 60,
        renew_seconds: int = 20,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if not 1 <= int(lease_seconds) <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        if not 1 <= int(renew_seconds) < int(lease_seconds):
            raise ValueError("renew_seconds must be positive and shorter than the lease")
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self.calculator = calculator or ScheduleCalculator(calendar=calendar, clock=self._clock)
        self.owner = owner or f"automation-{uuid.uuid4().hex}"
        self.interval_seconds = float(interval_seconds)
        self.lease_seconds = int(lease_seconds)
        self.renew_seconds = int(renew_seconds)
        self._is_busy: Callable[[str], Any] | None = None
        self._submit_system_turn: Callable[[AutomationInvocation], Any] | None = None
        self._inspect_terminal: Callable[[AutomationRun], Any] | None = None
        self._is_archived: Callable[[str], Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def bind_hooks(
        self,
        *,
        is_busy: Callable[[str], bool | Awaitable[bool]],
        submit_system_turn: Callable[[AutomationInvocation], Any],
        inspect_terminal: Callable[[AutomationRun], Any] | None = None,
        is_archived: Callable[[str], bool | Awaitable[bool]] | None = None,
    ) -> None:
        if not callable(is_busy) or not callable(submit_system_turn):
            raise TypeError("automation hooks must be callable")
        self._is_busy = is_busy
        self._submit_system_turn = submit_system_turn
        self._inspect_terminal = inspect_terminal
        self._is_archived = is_archived

    bind_console_hooks = bind_hooks

    def automation_skills(self) -> tuple[AutomationSkill, ...]:
        return list_automation_skills()

    @staticmethod
    def _name(value: Any) -> str:
        if not isinstance(value, str):
            raise AutomationValidationError("name must be text")
        name = value.strip()
        if (
            not name
            or len(name) > 128
            or _CONTROL_RE.search(name)
            or _TOKEN_SHAPE_RE.search(name)
            or _PAIRING_CODE_SHAPE_RE.search(name)
            or _CHANNEL_ID_LABEL_RE.search(name)
            or _LOCAL_PATH_RE.search(name)
        ):
            raise AutomationValidationError("name is invalid")
        return name

    @staticmethod
    def _session(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise AutomationValidationError("session_id is invalid")
        return value.strip()

    def upsert_automation(
        self,
        *,
        session_id: str,
        name: str,
        schedule_kind: str,
        prompt_text: str,
        timezone_name: str = DEFAULT_TIMEZONE,
        timezone: str | None = None,
        local_time: str | None = None,
        run_at: datetime | str | None = None,
        skill_refs: Sequence[str] = (),
        media_policy: str = "none",
        confirmed_at: datetime | str | None = None,
        automation_id: str | None = None,
        created_by: str | None = None,
        misfire_grace_seconds: int = DEFAULT_MISFIRE_GRACE_SECONDS,
    ) -> Automation:
        sid = self._session(session_id)
        safe_name = self._name(name)
        prompt = validate_automation_prompt(prompt_text)
        refs = tuple(skill.skill_id for skill in resolve_skill_refs(skill_refs))
        policy = str(media_policy)
        if policy not in _MEDIA_POLICIES:
            raise AutomationValidationError("unsupported media_policy")
        if isinstance(misfire_grace_seconds, bool) or not 0 <= int(
            misfire_grace_seconds
        ) <= 7 * 24 * 60 * 60:
            raise AutomationValidationError("misfire_grace_seconds is out of range")
        zone_name = timezone or timezone_name
        spec = self.calculator.validate(
            schedule_kind=schedule_kind,
            timezone_name=zone_name,
            local_time=local_time,
            run_at=run_at,
        )
        now = _clock_utc(self._clock)
        confirmed = now if confirmed_at is None else _aware_utc(
            confirmed_at, field="confirmed_at"
        )
        enabled = True
        pause_reason: str | None = None
        last_status: str | None = None
        try:
            next_run = self.calculator.next_run_at(spec, after=now, inclusive=True)
        except CalendarUnavailable:
            if spec.schedule_kind != "trading_days":
                raise
            next_run = None
            enabled = False
            pause_reason = "calendar_unavailable"
            last_status = "blocked"
        if spec.schedule_kind == "once" and next_run is None:
            raise AutomationValidationError("once run_at must not be in the past")
        values = dict(
            name=safe_name,
            session_id=sid,
            schedule_kind=spec.schedule_kind,
            local_time=spec.local_time,
            run_at=spec.run_at,
            timezone_name=spec.timezone,
            prompt_text=prompt,
            skill_refs=refs,
            media_policy=policy,
            enabled=enabled,
            pause_reason=pause_reason,
            next_run_at=next_run,
            last_status=last_status,
            misfire_grace_seconds=int(misfire_grace_seconds),
            created_by=created_by,
            confirmed_at=confirmed,
        )
        if automation_id is None:
            return self.store.create_automation(**values)
        return self.store.update_automation(
            automation_id,
            expected_session_id=sid,
            **values,
        )

    # Runtime/tool-facing aliases.
    automation_upsert = upsert_automation

    def list_automations(self, session_id: str) -> list[Automation]:
        return self.store.automations_for_session(self._session(session_id))

    automation_list = list_automations

    def pause_automation(
        self, session_id: str, automation_id: str, reason: str = "manual"
    ) -> Automation:
        return self.store.set_automation_enabled(
            automation_id,
            expected_session_id=self._session(session_id),
            enabled=False,
            pause_reason=reason,
        )

    automation_pause = pause_automation

    def resume_automation(self, session_id: str, automation_id: str) -> Automation:
        sid = self._session(session_id)
        item = self.store.automation_for_session(automation_id, sid)
        spec = self.calculator.validate(item)
        try:
            next_run = self.calculator.next_run_at(
                spec, after=_clock_utc(self._clock), inclusive=True
            )
        except CalendarUnavailable:
            return self.store.set_automation_enabled(
                item.id,
                expected_session_id=sid,
                enabled=False,
                pause_reason="calendar_unavailable",
                last_status="blocked",
                next_run_at=None,
            )
        return self.store.set_automation_enabled(
            item.id,
            expected_session_id=sid,
            enabled=True,
            pause_reason=None,
            last_status=None,
            next_run_at=next_run,
        )

    automation_resume = resume_automation

    def delete_automation(self, session_id: str, automation_id: str) -> bool:
        return self.store.delete_automation(
            automation_id, expected_session_id=self._session(session_id)
        )

    automation_delete = delete_automation

    def _block_calendar(self, automation: Automation) -> None:
        self.store.set_automation_enabled(
            automation.id,
            expected_session_id=automation.session_id,
            enabled=False,
            pause_reason="calendar_unavailable",
            last_status="blocked",
            next_run_at=None,
        )

    def materialize_due(self, *, now: datetime | str | None = None) -> AutomationTickStats:
        current = _clock_utc(self._clock) if now is None else _aware_utc(now, field="now")
        materialized = missed = blocked = errors = 0
        for automation in self.store.due_automations(current):
            try:
                decision = self.calculator.due_occurrence(
                    automation,
                    due_since=automation.next_run_at,
                    now=current,
                    grace_seconds=automation.misfire_grace_seconds,
                )
                if decision is None:
                    continue
                run = self.store.create_automation_run(
                    automation.id, decision.scheduled_for
                )
                materialized += 1
                if decision.missed:
                    self.store.mark_automation_run_missed(
                        run.id, now=current, error_class="misfire_grace_exceeded"
                    )
                    missed += 1
                self.store.advance_automation_schedule(
                    automation.id,
                    expected_next_run_at=automation.next_run_at,
                    next_run_at=decision.next_run_at,
                    last_run_at=decision.scheduled_for if decision.missed else None,
                    last_status="missed" if decision.missed else None,
                    disable=decision.next_run_at is None,
                    now=current,
                )
            except CalendarUnavailable:
                self._block_calendar(automation)
                blocked += 1
            except (ChannelConflict, ChannelNotFound):
                # Another scheduler advanced the same due row first.
                continue
            except Exception:
                errors += 1
        return AutomationTickStats(
            materialized=materialized,
            missed=missed,
            blocked=blocked,
            errors=errors,
        )

    @staticmethod
    def _submission_result(value: Any) -> tuple[str, str | None]:
        if isinstance(value, str) and value.strip():
            return "started", value.strip()
        if isinstance(value, Mapping):
            status = str(value.get("status") or "").strip().lower()
            reason = str(value.get("reason_code") or value.get("reason") or "").strip().lower()
            turn_id = value.get("turn_id")
            safe_turn = turn_id.strip() if isinstance(turn_id, str) and turn_id.strip() else None
            if status in {"busy", "queued", "unavailable", "blocked"} or reason in {
                "busy",
                "session_busy",
                "session_read_only",
                "session_archived",
                "archived",
            }:
                return "busy", None
            if safe_turn is not None and status in {"", "started", "running", "ok"}:
                return "started", safe_turn
        if value is None or value is False:
            return "busy", None
        raise AutomationValidationError("submit_system_turn returned an invalid result")

    async def _archived(self, session_id: str) -> bool:
        if self._is_archived is None:
            return False
        return bool(await _maybe_await(self._is_archived(session_id)))

    async def _busy(self, session_id: str) -> bool:
        if self._is_busy is None:
            return True
        return bool(await _maybe_await(self._is_busy(session_id)))

    async def _submit_claim(self, run: AutomationRun) -> str:
        if self._submit_system_turn is None:
            return "busy"
        automation = self.store.automation(run.automation_id)
        if await self._archived(automation.session_id):
            self.store.set_automation_enabled(
                automation.id,
                expected_session_id=automation.session_id,
                enabled=False,
                pause_reason="session_archived",
                last_status="blocked",
            )
            self.store.release_automation_run_claim(run.id, lease_owner=self.owner)
            return "busy"
        if await self._busy(automation.session_id):
            self.store.release_automation_run_claim(run.id, lease_owner=self.owner)
            return "busy"
        invocation = AutomationInvocation(
            session_id=automation.session_id,
            automation_id=automation.id,
            automation_run_id=run.id,
            scheduled_for=run.scheduled_for,
            media_policy=automation.media_policy,
            system_text=render_automation_system_turn(automation, run),
        )
        try:
            raw = await _maybe_await(self._submit_system_turn(invocation))
            status, turn_id = self._submission_result(raw)
        except Exception:
            self.store.release_automation_run_claim(run.id, lease_owner=self.owner)
            return "error"
        if status == "busy" or turn_id is None:
            self.store.release_automation_run_claim(run.id, lease_owner=self.owner)
            return "busy"
        started = self.store.start_automation_run(
            run.id,
            lease_owner=self.owner,
            turn_id=turn_id,
            lease_seconds=self.lease_seconds,
            now=_clock_utc(self._clock),
        )
        return "started" if started is not None else "error"

    async def tick(self) -> AutomationTickStats:
        # A foreign lease may still have been live during startup and expire
        # later.  Re-check every tick so no interrupted run remains permanently
        # running; live leases are excluded by the store query.
        await self.recover_expired_running()
        current = _clock_utc(self._clock)
        self.store.renew_automation_run_leases(
            lease_owner=self.owner,
            lease_seconds=self.lease_seconds,
            now=current,
        )
        base = self.materialize_due(now=current)
        claimed = self.store.claim_automation_runs(
            lease_owner=self.owner,
            now=current,
            lease_seconds=self.lease_seconds,
            limit=32,
        )
        busy = started = errors = 0
        for run in claimed:
            outcome = await self._submit_claim(run)
            if outcome == "started":
                started += 1
            elif outcome == "busy":
                busy += 1
            else:
                errors += 1
        return AutomationTickStats(
            materialized=base.materialized,
            missed=base.missed,
            claimed=len(claimed),
            busy=busy,
            started=started,
            blocked=base.blocked,
            errors=base.errors + errors,
        )

    @staticmethod
    def _terminal_evidence(value: Any) -> tuple[bool, bool, int | None, str | None]:
        if not isinstance(value, Mapping) or value.get("terminal") is not True:
            return False, False, None, None
        ok = value.get("ok") is True
        event_id = value.get("final_event_id")
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 0:
            event_id = None
        error = value.get("error_class")
        if not isinstance(error, str) or not _SAFE_ERROR_RE.fullmatch(error):
            error = None
        return True, ok, event_id, error

    async def _recover_running(
        self, *, current: datetime, include_live_owner: bool
    ) -> int:
        recovered = 0
        for run in self.store.running_automation_runs(
            lease_owner=self.owner,
            now=current,
            recoverable_only=True,
            include_live_owner=include_live_owner,
        ):
            evidence = None
            if self._inspect_terminal is not None:
                try:
                    evidence = await _maybe_await(self._inspect_terminal(run))
                except Exception:
                    evidence = None
            terminal, ok, final_event_id, error = self._terminal_evidence(evidence)
            if terminal:
                self.store.finish_automation_run(
                    run.id,
                    turn_id=run.turn_id,
                    ok=ok,
                    final_event_id=final_event_id,
                    error_class=error if not ok else None,
                    now=current,
                )
            else:
                self.store.finish_automation_run(
                    run.id,
                    turn_id=run.turn_id,
                    ok=False,
                    error_class="service_restart_interrupted",
                    now=current,
                )
            recovered += 1
        return recovered

    async def recover_startup(self) -> int:
        current = _clock_utc(self._clock)
        self.store.recover_expired_automation_claims(now=current)
        # At startup this process cannot have a legitimately executing Console
        # turn yet, so an old lease carrying the same configured owner is also
        # recovery evidence.  Other owners remain fenced until expiry.
        return await self._recover_running(current=current, include_live_owner=True)

    async def recover_expired_running(self) -> int:
        """Periodically converge rows that became recoverable after startup."""

        return await self._recover_running(
            current=_clock_utc(self._clock),
            include_live_owner=False,
        )

    def on_turn_terminal(
        self,
        automation_run_id: str,
        turn_id: str,
        ok: bool,
        final_event_id: int | None = None,
        error_class: str | None = None,
    ) -> AutomationRun | None:
        return self.store.finish_automation_run(
            automation_run_id,
            turn_id=turn_id,
            ok=bool(ok),
            final_event_id=final_event_id,
            error_class=error_class,
            now=_clock_utc(self._clock),
        )

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        # Default materialization ticks (15s) already beat the 20s renewal
        # target.  If a caller configures a slower scan interval, use the lease
        # heartbeat as the upper bound so a live run is never abandoned.
        heartbeat_seconds = min(self.interval_seconds, float(self.renew_seconds))
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # One bad tick must not kill the local scheduler.  Domain errors
                # are reflected in durable run/automation states where possible.
                pass
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=heartbeat_seconds
                )
            except asyncio.TimeoutError:
                continue

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._is_busy is None or self._submit_system_turn is None:
            raise RuntimeError("automation worker hooks are not bound")
        await self.recover_startup()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run_loop(), name=f"guanlan-automation-{self.owner}"
        )

    async def stop(self) -> None:
        task = self._task
        stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None
        self.store.release_automation_run_claims(lease_owner=self.owner)


AutomationScheduler = AutomationWorker


__all__ = [
    "AutomationInvocation",
    "AutomationOwnershipError",
    "AutomationScheduler",
    "AutomationTickStats",
    "AutomationValidationError",
    "AutomationWorker",
    "DEFAULT_MISFIRE_GRACE_SECONDS",
    "DEFAULT_TIMEZONE",
    "DueOccurrence",
    "ScheduleCalculator",
    "ScheduleSpec",
    "render_automation_system_turn",
    "validate_automation_prompt",
]
