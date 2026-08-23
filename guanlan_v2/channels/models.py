"""Typed domain records for durable outbound conversation channels.

The records intentionally contain only SQLite-safe metadata.  Credentials stay
behind an opaque ``credential_id`` and never appear in these models.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class BotStatus(_StringEnum):
    DRAFT = "draft"
    AWAITING_PAIRING = "awaiting_pairing"
    ACTIVE = "active"
    PAUSED = "paused"
    POLL_CONFLICT = "poll_conflict"
    CREDENTIAL_INVALID = "credential_invalid"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    CREDENTIAL_CLEANUP_PENDING = "credential_cleanup_pending"
    DISCONNECTED = "disconnected"


class BindingStatus(_StringEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISCONNECTED = "disconnected"


class PairingStatus(_StringEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class OutboxState(_StringEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    RETRY_WAIT = "retry_wait"
    UNCERTAIN = "uncertain"
    TERMINAL = "terminal"
    CANCELLED = "cancelled"


class AutomationRunStatus(_StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    MISSED = "missed"
    CANCELLED = "cancelled"


class OutboundKind(_StringEnum):
    TEXT = "text"
    MEDIA = "media"


class ScheduleKind(_StringEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    TRADING_DAYS = "trading_days"


class MediaPolicy(_StringEnum):
    NONE = "none"
    EXPLICIT = "explicit"


@dataclass(frozen=True)
class BotAccount:
    id: str
    channel: str
    draft_session_id: Optional[str]
    display_name: str
    telegram_bot_id: Optional[str]
    telegram_username: Optional[str]
    credential_id: Optional[str]
    credential_revision: int
    status: BotStatus
    update_offset: int
    poll_owner: Optional[str]
    poll_lease_until: Optional[str]
    last_verified_at: Optional[str]
    last_error_code: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ChannelBinding:
    id: str
    channel: str
    session_id: str
    bot_account_id: str
    telegram_user_id: str
    telegram_chat_id: str
    telegram_username: Optional[str]
    telegram_display_name: Optional[str]
    status: BindingStatus
    pause_reason: Optional[str]
    paired_at: str
    sync_from_event_id: int
    projection_cursor_event_id: int
    last_sent_at: Optional[str]
    last_error_code: Optional[str]
    paused_at: Optional[str]
    disconnected_at: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PairingRequest:
    id: str
    bot_account_id: str
    session_id: str
    telegram_update_id: str
    telegram_user_id: str
    telegram_chat_id: str
    telegram_username: Optional[str]
    telegram_display_name: Optional[str]
    code_salt: bytes
    code_digest: bytes
    code_hint: str
    issue_credential_revision: int
    status: PairingStatus
    created_at: str
    expires_at: str
    last_seen_at: str
    approved_binding_id: Optional[str]


@dataclass(frozen=True)
class OutboundPart:
    kind: str
    payload_part: int
    text: Optional[str] = None
    media_id: Optional[str] = None
    caption: Optional[str] = None

    def __post_init__(self) -> None:
        kind = str(self.kind)
        if kind not in {OutboundKind.TEXT.value, OutboundKind.MEDIA.value}:
            raise ValueError(f"unsupported outbound kind: {kind}")
        if int(self.payload_part) < 0:
            raise ValueError("payload_part must be non-negative")
        if kind == OutboundKind.TEXT.value:
            if self.text is None or self.media_id is not None or self.caption is not None:
                raise ValueError("text outbound parts require only text")
        elif self.media_id is None or self.text is not None:
            raise ValueError("media outbound parts require media_id and no text")


@dataclass(frozen=True)
class OutboxItem:
    id: str
    binding_id: str
    source_session_id: str
    source_event_id: int
    payload_part: int
    batch_id: str
    sequence: int
    kind: str
    text: Optional[str]
    media_id: Optional[str]
    caption: Optional[str]
    state: OutboxState
    attempts: int
    next_attempt_at: Optional[str]
    lease_owner: Optional[str]
    lease_until: Optional[str]
    telegram_message_id: Optional[str]
    last_error_class: Optional[str]
    last_error_at: Optional[str]
    created_at: str
    sent_at: Optional[str]


@dataclass(frozen=True)
class Automation:
    id: str
    name: str
    session_id: str
    schedule_kind: str
    local_time: Optional[str]
    run_at: Optional[str]
    timezone: str
    prompt_text: str
    skill_refs: tuple[str, ...]
    media_policy: str
    enabled: bool
    pause_reason: Optional[str]
    next_run_at: Optional[str]
    last_run_at: Optional[str]
    last_status: Optional[str]
    misfire_grace_seconds: int
    created_by: Optional[str]
    confirmed_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AutomationRun:
    id: str
    automation_id: str
    scheduled_for: str
    status: AutomationRunStatus
    turn_id: Optional[str]
    final_event_id: Optional[int]
    lease_owner: Optional[str]
    lease_until: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]
    error_class: Optional[str]


# Descriptive aliases make call sites readable without duplicating enum types.
BotAccountStatus = BotStatus
ChannelBindingStatus = BindingStatus
PairingRequestStatus = PairingStatus
OutboxStatus = OutboxState
AutomationRunState = AutomationRunStatus


__all__ = [
    "Automation",
    "AutomationRun",
    "AutomationRunState",
    "AutomationRunStatus",
    "BindingStatus",
    "BotAccount",
    "BotAccountStatus",
    "BotStatus",
    "ChannelBinding",
    "ChannelBindingStatus",
    "MediaPolicy",
    "OutboundKind",
    "OutboundPart",
    "OutboxItem",
    "OutboxState",
    "OutboxStatus",
    "PairingRequest",
    "PairingRequestStatus",
    "PairingStatus",
    "ScheduleKind",
]
