"""Durable SQLite fact store for outbound conversation channels.

Every mutating domain decision opens its own connection.  Decisions that can
race (pairing approval, claims, projection, and state resolution) take a
``BEGIN IMMEDIATE`` transaction so uniqueness is decided by SQLite, not by a
stale Python pre-check.
"""
from __future__ import annotations

import json
import hashlib
import hmac
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

from guanlan_v2.channels.models import (
    Automation,
    AutomationRun,
    AutomationRunStatus,
    BindingStatus,
    BotAccount,
    BotStatus,
    ChannelBinding,
    OutboundPart,
    OutboxItem,
    OutboxState,
    PairingRequest,
    PairingStatus,
)
from guanlan_v2.channels.media_policy import (
    MEDIA_ID_RE,
    MEDIA_PERMIT_ID_RE,
    SHA256_HEX_RE,
    caption_contains_location,
)


UTC = timezone.utc


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=FULL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS bot_accounts (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    draft_session_id TEXT,
    display_name TEXT NOT NULL,
    telegram_bot_id TEXT,
    telegram_username TEXT,
    credential_id TEXT,
    credential_revision INTEGER NOT NULL DEFAULT 0 CHECK (credential_revision >= 0),
    status TEXT NOT NULL CHECK (status IN (
        'draft', 'awaiting_pairing', 'active', 'paused', 'poll_conflict',
        'credential_invalid', 'credential_unavailable',
        'credential_cleanup_pending', 'disconnected'
    )),
    update_offset INTEGER NOT NULL DEFAULT 0 CHECK (update_offset >= 0),
    poll_owner TEXT,
    poll_lease_until TEXT,
    last_verified_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_bindings (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    session_id TEXT NOT NULL,
    bot_account_id TEXT NOT NULL REFERENCES bot_accounts(id),
    telegram_user_id TEXT NOT NULL,
    telegram_chat_id TEXT NOT NULL,
    telegram_username TEXT,
    telegram_display_name TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'disconnected')),
    pause_reason TEXT,
    paired_at TEXT NOT NULL,
    sync_from_event_id INTEGER NOT NULL CHECK (sync_from_event_id >= 0),
    projection_cursor_event_id INTEGER NOT NULL CHECK (projection_cursor_event_id >= 0),
    last_sent_at TEXT,
    last_error_code TEXT,
    paused_at TEXT,
    disconnected_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairing_requests (
    id TEXT PRIMARY KEY,
    bot_account_id TEXT NOT NULL REFERENCES bot_accounts(id),
    session_id TEXT NOT NULL,
    telegram_update_id TEXT NOT NULL,
    telegram_user_id TEXT NOT NULL,
    telegram_chat_id TEXT NOT NULL,
    telegram_username TEXT,
    telegram_display_name TEXT,
    code_salt BLOB NOT NULL,
    code_digest BLOB NOT NULL,
    code_hint TEXT NOT NULL,
    issue_credential_revision INTEGER NOT NULL DEFAULT 0
        CHECK (issue_credential_revision >= 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'dismissed', 'expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    approved_binding_id TEXT REFERENCES channel_bindings(id)
);

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL REFERENCES channel_bindings(id),
    source_session_id TEXT NOT NULL,
    source_event_id INTEGER NOT NULL CHECK (source_event_id >= 0),
    payload_part INTEGER NOT NULL CHECK (payload_part >= 0),
    batch_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    kind TEXT NOT NULL CHECK (kind IN ('text', 'media')),
    text TEXT,
    media_id TEXT,
    caption TEXT,
    media_authorization_version INTEGER CHECK (
        media_authorization_version IS NULL OR media_authorization_version = 1
    ),
    state TEXT NOT NULL CHECK (state IN (
        'pending', 'sending', 'sent', 'retry_wait', 'uncertain', 'terminal', 'cancelled'
    )),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    telegram_message_id TEXT,
    last_error_class TEXT,
    last_error_at TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    CHECK (
        (kind = 'text' AND text IS NOT NULL AND media_id IS NULL AND caption IS NULL
            AND media_authorization_version IS NULL)
        OR (kind = 'media' AND text IS NULL AND media_id IS NOT NULL
            AND media_authorization_version = 1)
    )
);

-- Recipient authority is pinned when an event is projected.  Re-pairing may
-- update a binding row in place, so unresolved deliveries must retain the old
-- generation and may only be dropped/assumed-sent, never silently redirected.
CREATE TABLE IF NOT EXISTS outbox_route_fences (
    outbox_id TEXT PRIMARY KEY REFERENCES outbox(id) ON DELETE CASCADE,
    bot_account_id TEXT NOT NULL REFERENCES bot_accounts(id),
    telegram_chat_id TEXT NOT NULL,
    binding_paired_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projection_leases (
    binding_id TEXT PRIMARY KEY REFERENCES channel_bindings(id) ON DELETE CASCADE,
    lease_owner TEXT NOT NULL,
    lease_until TEXT NOT NULL
);

-- One-use authority created only after an approved Agent confirmation.  The
-- caption itself remains in Console until projection; Channel stores only its
-- digest here, and removes the permit in the same transaction as Outbox insert.
CREATE TABLE IF NOT EXISTS media_publish_permits (
    permit_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    binding_id TEXT NOT NULL REFERENCES channel_bindings(id) ON DELETE CASCADE,
    binding_paired_at TEXT NOT NULL,
    media_id TEXT NOT NULL,
    caption_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    session_id TEXT NOT NULL,
    schedule_kind TEXT NOT NULL CHECK (schedule_kind IN (
        'once', 'daily', 'weekdays', 'trading_days'
    )),
    local_time TEXT,
    run_at TEXT,
    timezone TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    skill_refs_json TEXT NOT NULL,
    media_policy TEXT NOT NULL CHECK (media_policy IN ('none', 'explicit')),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    pause_reason TEXT,
    next_run_at TEXT,
    last_run_at TEXT,
    last_status TEXT,
    misfire_grace_seconds INTEGER NOT NULL CHECK (misfire_grace_seconds >= 0),
    created_by TEXT,
    confirmed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES automations(id),
    scheduled_for TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'done', 'error', 'missed', 'cancelled'
    )),
    turn_id TEXT,
    final_event_id INTEGER,
    lease_owner TEXT,
    lease_until TEXT,
    started_at TEXT,
    ended_at TEXT,
    error_class TEXT
);

CREATE TABLE IF NOT EXISTS channel_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    session_id TEXT,
    bot_account_id TEXT,
    binding_id TEXT,
    pairing_request_id TEXT,
    automation_id TEXT,
    outbox_id TEXT,
    error_class TEXT,
    safe_details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credential_cleanup_queue (
    credential_id TEXT PRIMARY KEY,
    bot_account_id TEXT,
    reason TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TEXT,
    last_error_class TEXT,
    created_at TEXT NOT NULL
);

-- A replacement intent is deliberately metadata-only.  It makes the secure
-- Token card restart-safe without putting either a raw Token or a credential
-- reference into Console events, URL parameters, or the public status DTO.
CREATE TABLE IF NOT EXISTS token_replacement_intents (
    session_id TEXT PRIMARY KEY,
    bot_account_id TEXT NOT NULL REFERENCES bot_accounts(id),
    expected_credential_revision INTEGER,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_live_binding_session
ON channel_bindings(session_id)
WHERE status IN ('active', 'paused');

CREATE UNIQUE INDEX IF NOT EXISTS uq_live_binding_bot
ON channel_bindings(bot_account_id)
WHERE status IN ('active', 'paused');

CREATE UNIQUE INDEX IF NOT EXISTS uq_live_telegram_bot
ON bot_accounts(telegram_bot_id)
WHERE telegram_bot_id IS NOT NULL AND status <> 'disconnected';

CREATE UNIQUE INDEX IF NOT EXISTS uq_open_draft_session
ON bot_accounts(draft_session_id)
WHERE draft_session_id IS NOT NULL AND status <> 'disconnected';

CREATE UNIQUE INDEX IF NOT EXISTS uq_outbox_source_part
ON outbox(binding_id, source_event_id, payload_part);

CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_occurrence
ON automation_runs(automation_id, scheduled_for);

CREATE UNIQUE INDEX IF NOT EXISTS uq_binding_sequence
ON outbox(binding_id, sequence);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pairing_bot_update
ON pairing_requests(bot_account_id, telegram_update_id);

CREATE INDEX IF NOT EXISTS idx_pairing_pending
ON pairing_requests(bot_account_id, status, expires_at);

CREATE INDEX IF NOT EXISTS idx_outbox_delivery
ON outbox(binding_id, sequence, state, next_attempt_at);

CREATE INDEX IF NOT EXISTS idx_automation_due
ON automations(enabled, next_run_at);

CREATE INDEX IF NOT EXISTS idx_automation_run_claim
ON automation_runs(status, lease_until, scheduled_for);

CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_turn
ON automation_runs(turn_id)
WHERE turn_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_token_replacement_bot
ON token_replacement_intents(bot_account_id);

CREATE INDEX IF NOT EXISTS idx_media_publish_permits_expiry
ON media_publish_permits(expires_at);
"""


class ChannelStoreError(RuntimeError):
    """Base class for safe channel-store failures."""


class ChannelNotFound(ChannelStoreError):
    """A referenced local entity does not exist."""


class ChannelConflict(ChannelStoreError):
    """A durable uniqueness or compare-and-swap rule rejected a decision."""


class InvalidTransition(ChannelStoreError):
    """A domain state transition is not in the frozen state machine."""


class UnsafeAuditDetails(ChannelStoreError, ValueError):
    """Audit input is not on the metadata-only allowlist."""


_BOT_TRANSITIONS: dict[BotStatus, frozenset[BotStatus]] = {
    BotStatus.DRAFT: frozenset(
        {
            BotStatus.AWAITING_PAIRING,
            BotStatus.CREDENTIAL_INVALID,
            BotStatus.CREDENTIAL_UNAVAILABLE,
            BotStatus.CREDENTIAL_CLEANUP_PENDING,
            BotStatus.DISCONNECTED,
        }
    ),
    BotStatus.AWAITING_PAIRING: frozenset(
        {
            BotStatus.ACTIVE,
            BotStatus.POLL_CONFLICT,
            BotStatus.CREDENTIAL_INVALID,
            BotStatus.CREDENTIAL_UNAVAILABLE,
            BotStatus.CREDENTIAL_CLEANUP_PENDING,
            BotStatus.DISCONNECTED,
        }
    ),
    BotStatus.ACTIVE: frozenset(
        {
            BotStatus.PAUSED,
            BotStatus.POLL_CONFLICT,
            BotStatus.CREDENTIAL_INVALID,
            BotStatus.CREDENTIAL_UNAVAILABLE,
            BotStatus.CREDENTIAL_CLEANUP_PENDING,
            BotStatus.DISCONNECTED,
        }
    ),
    BotStatus.PAUSED: frozenset(
        {
            BotStatus.ACTIVE,
            BotStatus.POLL_CONFLICT,
            BotStatus.CREDENTIAL_INVALID,
            BotStatus.CREDENTIAL_UNAVAILABLE,
            BotStatus.CREDENTIAL_CLEANUP_PENDING,
            BotStatus.DISCONNECTED,
        }
    ),
    BotStatus.POLL_CONFLICT: frozenset(
        {
            BotStatus.CREDENTIAL_INVALID,
            BotStatus.CREDENTIAL_UNAVAILABLE,
            BotStatus.CREDENTIAL_CLEANUP_PENDING,
            BotStatus.DISCONNECTED,
        }
    ),
    BotStatus.CREDENTIAL_INVALID: frozenset(),
    BotStatus.CREDENTIAL_UNAVAILABLE: frozenset(),
    BotStatus.CREDENTIAL_CLEANUP_PENDING: frozenset(),
    BotStatus.DISCONNECTED: frozenset(),
}

_BINDING_TRANSITIONS: dict[BindingStatus, frozenset[BindingStatus]] = {
    BindingStatus.ACTIVE: frozenset({BindingStatus.PAUSED, BindingStatus.DISCONNECTED}),
    BindingStatus.PAUSED: frozenset({BindingStatus.ACTIVE, BindingStatus.DISCONNECTED}),
    BindingStatus.DISCONNECTED: frozenset(),
}

_PAIRING_TRANSITIONS: dict[PairingStatus, frozenset[PairingStatus]] = {
    PairingStatus.PENDING: frozenset(
        {PairingStatus.APPROVED, PairingStatus.DISMISSED, PairingStatus.EXPIRED}
    ),
    PairingStatus.APPROVED: frozenset(),
    PairingStatus.DISMISSED: frozenset(),
    PairingStatus.EXPIRED: frozenset(),
}

_OUTBOX_TRANSITIONS: dict[OutboxState, frozenset[OutboxState]] = {
    OutboxState.PENDING: frozenset({OutboxState.SENDING, OutboxState.CANCELLED}),
    OutboxState.SENDING: frozenset(
        {
            OutboxState.SENT,
            OutboxState.RETRY_WAIT,
            OutboxState.UNCERTAIN,
            OutboxState.TERMINAL,
        }
    ),
    OutboxState.RETRY_WAIT: frozenset({OutboxState.SENDING, OutboxState.CANCELLED}),
    OutboxState.UNCERTAIN: frozenset(
        {OutboxState.SENT, OutboxState.PENDING, OutboxState.CANCELLED}
    ),
    OutboxState.TERMINAL: frozenset({OutboxState.PENDING, OutboxState.CANCELLED}),
    OutboxState.SENT: frozenset(),
    OutboxState.CANCELLED: frozenset(),
}

_AUTOMATION_RUN_TRANSITIONS: dict[AutomationRunStatus, frozenset[AutomationRunStatus]] = {
    AutomationRunStatus.QUEUED: frozenset(
        {
            AutomationRunStatus.RUNNING,
            AutomationRunStatus.MISSED,
            AutomationRunStatus.CANCELLED,
        }
    ),
    AutomationRunStatus.RUNNING: frozenset(
        {
            AutomationRunStatus.DONE,
            AutomationRunStatus.ERROR,
            AutomationRunStatus.MISSED,
            AutomationRunStatus.CANCELLED,
        }
    ),
    AutomationRunStatus.DONE: frozenset(),
    AutomationRunStatus.ERROR: frozenset(),
    AutomationRunStatus.MISSED: frozenset(),
    AutomationRunStatus.CANCELLED: frozenset(),
}


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_STATUS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SAFE_ERROR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
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
_PROMPT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CREDENTIAL_ID_RE = re.compile(r"^cred_[0-9a-f]{32}$")
_AUDIT_STATE_KEYS = {
    "status",
    "previous_status",
    "new_status",
    "from_status",
    "to_status",
    "state",
    "previous_state",
    "new_state",
    "from_state",
    "to_state",
}
_AUDIT_ERROR_KEYS = {"error_class", "previous_error_class", "new_error_class"}
_FORBIDDEN_AUDIT_KEY_PARTS = ("token", "code", "text", "caption", "path", "url")
_RECOVERABLE_FAULT_PAUSE_REASONS = frozenset(
    {"credential_invalid", "credential_unavailable", "poll_conflict"}
)
_UNSET = object()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _new_automation_id(prefix: str) -> str:
    """Keep opaque schedule IDs outside the eight-character pairing shape.

    Automation IDs are frequently serialized inside generic Agent tool-result
    text, where key-aware redaction is unavailable.  Six-character entropy
    groups separated by a lowercase marker ensure even adversarial UUID bytes
    cannot accidentally form one contiguous eight-character pairing code.
    """

    entropy = uuid.uuid4().hex.lower()
    grouped = "x".join(entropy[index:index + 6] for index in range(0, len(entropy), 6))
    return f"{prefix}_{grouped}"


@dataclass(frozen=True)
class OutboxClaim(OutboxItem):
    """One fenced delivery lease plus its immutable routing snapshot.

    The credential remains an opaque reference.  Capturing its revision in the
    same transaction as the claim prevents a worker from later treating a
    credential replacement as if it were the generation it originally used.
    """

    bot_account_id: str
    telegram_chat_id: str
    credential_id: str
    credential_revision: int


def _entity_id(value: Any) -> str:
    if isinstance(value, str):
        return value
    entity_id = getattr(value, "id", None)
    if isinstance(entity_id, str):
        return entity_id
    raise TypeError("entity reference must be an opaque string id or record")


def _enum_value(value: Any) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def _aware_utc_iso(value: datetime | str, *, field: str = "timestamp") -> str:
    if isinstance(value, str):
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValueError(f"{field} must be a timezone-aware ISO datetime") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError(f"{field} must be a datetime or ISO string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def _optional_time(value: datetime | str | None, *, field: str) -> Optional[str]:
    return None if value is None else _aware_utc_iso(value, field=field)


def _check_transition(
    domain: str,
    current: Enum,
    target: Enum,
    transitions: Mapping[Enum, frozenset[Enum]],
) -> None:
    if current == target:
        return
    if target not in transitions[current]:
        raise InvalidTransition(
            f"invalid {domain} transition: {_enum_value(current)} -> {_enum_value(target)}"
        )


def _bot_from_row(row: sqlite3.Row) -> BotAccount:
    data = dict(row)
    data["status"] = BotStatus(data["status"])
    return BotAccount(**data)


def _binding_from_row(row: sqlite3.Row) -> ChannelBinding:
    data = dict(row)
    data["status"] = BindingStatus(data["status"])
    return ChannelBinding(**data)


def _pairing_from_row(row: sqlite3.Row) -> PairingRequest:
    data = dict(row)
    data["code_salt"] = bytes(data["code_salt"])
    data["code_digest"] = bytes(data["code_digest"])
    data["status"] = PairingStatus(data["status"])
    return PairingRequest(**data)


def _outbox_from_row(row: sqlite3.Row) -> OutboxItem:
    data = dict(row)
    data["state"] = OutboxState(data["state"])
    return OutboxItem(**data)


def _outbox_claim_from_row(row: sqlite3.Row) -> OutboxClaim:
    data = dict(row)
    data["state"] = OutboxState(data["state"])
    data["bot_account_id"] = data.pop("claim_bot_account_id")
    data["telegram_chat_id"] = data.pop("claim_telegram_chat_id")
    data["credential_id"] = data.pop("claim_credential_id")
    data["credential_revision"] = int(data.pop("claim_credential_revision"))
    return OutboxClaim(**data)


def _automation_from_row(row: sqlite3.Row) -> Automation:
    data = dict(row)
    raw_refs = data.pop("skill_refs_json")
    try:
        refs = json.loads(raw_refs)
    except (TypeError, ValueError):
        refs = []
    data["skill_refs"] = tuple(str(ref) for ref in refs if isinstance(ref, str))
    data["enabled"] = bool(data["enabled"])
    return Automation(**data)


def _automation_run_from_row(row: sqlite3.Row) -> AutomationRun:
    data = dict(row)
    data["status"] = AutomationRunStatus(data["status"])
    return AutomationRun(**data)


class ChannelStore:
    """SQLite-backed registry, pairing, projection, outbox, and schedule facts."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(UTC))
        self.init_db()

    def init_db(self) -> None:
        """Idempotently install the complete schema and durability pragmas."""
        conn = self.connect()
        try:
            conn.executescript(SCHEMA_SQL)
            # Development/early Task 4 databases may have been created before
            # replacement intents gained an optimistic revision and TTL.  Keep
            # the migration additive; null legacy rows are treated as expired.
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(token_replacement_intents)").fetchall()
            }
            if "expected_credential_revision" not in columns:
                conn.execute(
                    "ALTER TABLE token_replacement_intents "
                    "ADD COLUMN expected_credential_revision INTEGER"
                )
            if "expires_at" not in columns:
                conn.execute(
                    "ALTER TABLE token_replacement_intents ADD COLUMN expires_at TEXT"
                )
            pairing_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(pairing_requests)").fetchall()
            }
            if "issue_credential_revision" not in pairing_columns:
                # Legacy challenges cannot be proven to belong to the current
                # credential generation.  Revision zero keeps old test/empty
                # databases readable; any later Token CAS dismisses them.
                conn.execute(
                    "ALTER TABLE pairing_requests ADD COLUMN "
                    "issue_credential_revision INTEGER NOT NULL DEFAULT 0"
                )
            outbox_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(outbox)").fetchall()
            }
            if "media_authorization_version" not in outbox_columns:
                # A route fence authenticates only the recipient generation;
                # it does not prove that a pre-Task8 image was confirmed.
                conn.execute(
                    "ALTER TABLE outbox ADD COLUMN media_authorization_version "
                    "INTEGER CHECK (media_authorization_version IS NULL "
                    "OR media_authorization_version = 1)"
                )
            # Rows created before recipient-generation fences cannot be proven
            # safe to route.  Never infer their historic recipient from the
            # binding's current mutable value.
            migration_now = self._now()
            conn.execute(
                "UPDATE outbox SET state = 'cancelled', lease_owner = NULL, "
                "lease_until = NULL WHERE state IN ('pending', 'retry_wait') "
                "AND NOT EXISTS (SELECT 1 FROM outbox_route_fences rf "
                "WHERE rf.outbox_id = outbox.id)"
            )
            conn.execute(
                "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, "
                "lease_until = NULL, last_error_class = 'legacy_route_unknown', "
                "last_error_at = ? WHERE state = 'sending' "
                "AND NOT EXISTS (SELECT 1 FROM outbox_route_fences rf "
                "WHERE rf.outbox_id = outbox.id)",
                (migration_now,),
            )
            # Task7 understood the media kind but deliberately never sent it.
            # Even a legacy row with a valid recipient route fence lacks the
            # Task8 one-use publication proof.  Mark such rows before the new
            # worker can claim them; rows inserted by Task8 carry version 1.
            conn.execute(
                "UPDATE outbox SET state = 'cancelled', lease_owner = NULL, "
                "lease_until = NULL, last_error_class = 'legacy_media_unauthorized', "
                "last_error_at = ? WHERE batch_id IN ("
                "SELECT legacy.batch_id FROM outbox legacy WHERE legacy.kind = 'media' "
                "AND legacy.media_authorization_version IS NOT 1) "
                "AND state IN ('pending', 'retry_wait')",
                (migration_now,),
            )
            conn.execute(
                "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, "
                "lease_until = NULL, last_error_class = 'legacy_media_unauthorized', "
                "last_error_at = ? WHERE batch_id IN ("
                "SELECT legacy.batch_id FROM outbox legacy WHERE legacy.kind = 'media' "
                "AND legacy.media_authorization_version IS NOT 1) "
                "AND state = 'sending'",
                (migration_now,),
            )
        finally:
            conn.close()

    initialize = init_db

    def connect(self) -> sqlite3.Connection:
        """Return a configured connection; callers own and must close it."""
        conn = sqlite3.connect(
            str(self.path),
            timeout=5.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _now(self) -> str:
        return _aware_utc_iso(self._clock(), field="clock")

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        text = value.strip()
        if _TOKEN_SHAPE_RE.search(text):
            raise ValueError(f"{field} must not contain a token-like secret")
        return text

    @classmethod
    def _required_channel_text(cls, value: Any, field: str) -> str:
        text = cls._required_text(value, field)
        if _TOKEN_SHAPE_RE.search(text):
            raise ValueError(f"{field} must not contain a token-like secret")
        return text

    @staticmethod
    def _required_prompt_text(value: Any) -> str:
        """Validate without rewriting the exact confirmed prompt bytes."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("prompt_text must be non-empty text")
        if _PROMPT_CONTROL_RE.search(value) or len(value) > 50000:
            raise ValueError("prompt_text length or content is invalid")
        if _TOKEN_SHAPE_RE.search(value):
            raise ValueError("prompt_text must not contain a token-like secret")
        if (
            _PAIRING_CODE_SHAPE_RE.search(value)
            or _CHANNEL_ID_LABEL_RE.search(value)
            or _LOCAL_PATH_RE.search(value)
        ):
            raise ValueError("prompt_text contains forbidden channel or local metadata")
        return value

    @staticmethod
    def _optional_channel_text(value: Any, field: str) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be text when provided")
        text = value.strip()
        if _TOKEN_SHAPE_RE.search(text):
            raise ValueError(f"{field} must not contain a token-like secret")
        return text or None

    @classmethod
    def _optional_safe_error(cls, value: Any, field: str) -> Optional[str]:
        """Persist only a structured, Token-free status/error marker."""
        if value is None:
            return None
        text = cls._required_text(value, field)
        if not _SAFE_ERROR_RE.fullmatch(text):
            raise ValueError(f"{field} must be a safe structured error code")
        return text

    # -- Bot accounts -------------------------------------------------

    def create_bot(
        self,
        display_name: str,
        *,
        draft_session_id: Optional[str] = None,
        channel: str = "telegram",
    ) -> str:
        bot_account_id = _new_id("bot")
        now = self._now()
        name = self._required_channel_text(display_name, "display_name")
        draft = None if draft_session_id is None else self._required_text(
            draft_session_id, "draft_session_id"
        )
        try:
            with self._immediate() as conn:
                conn.execute(
                    "INSERT INTO bot_accounts "
                    "(id, channel, draft_session_id, display_name, status, "
                    "credential_revision, update_offset, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'draft', 0, 0, ?, ?)",
                    (bot_account_id, channel, draft, name, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("bot account conflicts with an existing live draft") from exc
        return bot_account_id

    create_bot_account = create_bot

    def bot(self, bot_account_id: Any) -> BotAccount:
        entity_id = _entity_id(bot_account_id)
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM bot_accounts WHERE id = ?", (entity_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ChannelNotFound(f"unknown bot account: {entity_id}")
        return _bot_from_row(row)

    bot_account = bot

    def bots(self) -> list[BotAccount]:
        conn = self.connect()
        try:
            rows = conn.execute("SELECT * FROM bot_accounts ORDER BY created_at, id").fetchall()
        finally:
            conn.close()
        return [_bot_from_row(row) for row in rows]

    def current_draft(self, session_id: str) -> Optional[BotAccount]:
        """Return the session-owned, not-yet-bound Bot account if one exists.

        ``draft_session_id`` is the authority.  Pairing requests are deliberately
        absent from this query: a valid draft exists before Telegram sends any
        update, and the association remains useful audit metadata after approval.
        """
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT b.* FROM bot_accounts b WHERE b.draft_session_id = ? "
                "AND b.status <> 'disconnected' AND NOT EXISTS ("
                "SELECT 1 FROM channel_bindings cb WHERE cb.bot_account_id = b.id "
                "AND cb.status IN ('active', 'paused')) ORDER BY b.created_at DESC LIMIT 1",
                (self._required_text(session_id, "session_id"),),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _bot_from_row(row)

    def referenced_credential_ids(self) -> set[str]:
        """Return opaque credential references without reading any secret bytes."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT credential_id FROM bot_accounts "
                "WHERE credential_id IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        return {str(row[0]) for row in rows}

    def enqueue_credential_cleanup(
        self,
        credential_id: str,
        *,
        bot_account_id: str | None = None,
        reason: str,
    ) -> None:
        """Durably retain an opaque credential deletion obligation.

        This queue is deliberately metadata-only: callers must never pass a
        plaintext token, filesystem path, or provider error into it.  Repeated
        compensation attempts coalesce by credential ID rather than silently
        dropping an orphaned ciphertext.
        """
        if not isinstance(credential_id, str) or not _CREDENTIAL_ID_RE.fullmatch(credential_id):
            raise ValueError("credential_id must be an opaque cred_<32 hex> id")
        safe_bot = None if bot_account_id is None else _entity_id(bot_account_id)
        if not isinstance(reason, str) or not _SAFE_ERROR_RE.fullmatch(reason):
            raise ValueError("cleanup reason must be a safe error code")
        now = self._now()
        with self._immediate() as conn:
            conn.execute(
                "INSERT INTO credential_cleanup_queue "
                "(credential_id, bot_account_id, reason, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(credential_id) DO UPDATE SET "
                "bot_account_id = COALESCE(credential_cleanup_queue.bot_account_id, excluded.bot_account_id), "
                "reason = credential_cleanup_queue.reason",
                (credential_id, safe_bot, reason, now),
            )

    def credential_cleanup_for_bot(self, bot_account_id: Any) -> list[str]:
        """Return opaque pending deletion IDs for one account, never secrets."""
        entity_id = _entity_id(bot_account_id)
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT credential_id FROM credential_cleanup_queue "
                "WHERE bot_account_id = ? ORDER BY created_at, credential_id",
                (entity_id,),
            ).fetchall()
        finally:
            conn.close()
        return [str(row[0]) for row in rows]

    def credential_cleanup_items(self) -> list[tuple[str, str | None, str]]:
        """Metadata-only cleanup worklist for an eventual background worker."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT credential_id, bot_account_id, reason FROM credential_cleanup_queue "
                "ORDER BY created_at, credential_id"
            ).fetchall()
        finally:
            conn.close()
        return [(str(row[0]), None if row[1] is None else str(row[1]), str(row[2])) for row in rows]

    def complete_credential_cleanup(self, credential_id: str) -> None:
        """Remove a durable obligation only after the opaque secret is gone."""
        if not isinstance(credential_id, str) or not _CREDENTIAL_ID_RE.fullmatch(credential_id):
            raise ValueError("credential_id must be an opaque cred_<32 hex> id")
        with self._immediate() as conn:
            conn.execute(
                "DELETE FROM credential_cleanup_queue WHERE credential_id = ?", (credential_id,)
            )

    def replacement_intent_for_session(self, session_id: Any) -> Optional[str]:
        """Return the opaque target account for a prepared secure replacement."""
        sid = self._required_text(session_id, "session_id")
        details = self.replacement_intent_details(sid)
        return None if details is None else details[0]

    def replacement_intent_details(self, session_id: Any) -> Optional[tuple[str, int]]:
        """Return an unexpired opaque target plus its prepared revision."""
        sid = self._required_text(session_id, "session_id")
        now = self._now()
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT i.bot_account_id, i.expected_credential_revision, i.expires_at, "
                "b.credential_revision AS current_credential_revision "
                "FROM token_replacement_intents i JOIN bot_accounts b ON b.id = i.bot_account_id "
                "WHERE i.session_id = ?",
                (sid,),
            ).fetchone()
            if row is None:
                return None
            if (
                row["expected_credential_revision"] is None
                or row["expires_at"] is None
                or str(row["expires_at"]) <= now
                or int(row["expected_credential_revision"]) != int(row["current_credential_revision"])
            ):
                conn.execute(
                    "DELETE FROM token_replacement_intents WHERE session_id = ?", (sid,)
                )
                return None
            return str(row["bot_account_id"]), int(row["expected_credential_revision"])

    def prepare_token_replacement(self, session_id: Any, bot_account_id: Any) -> BotAccount:
        """Durably open a Token-card intent for the session's actual Bot.

        This deliberately leaves the old credential and live binding untouched.
        A failed verification can therefore be retried without interrupting
        delivery, while a successful different-identity replacement later
        fault-pauses the binding in the credential CAS transaction.
        """
        sid = self._required_text(session_id, "session_id")
        entity_id = _entity_id(bot_account_id)
        now = self._now()
        expires_at = _aware_utc_iso(self._clock() + timedelta(hours=1), field="intent_expiry")
        with self._immediate() as conn:
            bot = conn.execute(
                "SELECT * FROM bot_accounts WHERE id = ?", (entity_id,)
            ).fetchone()
            if bot is None or BotStatus(bot["status"]) is BotStatus.DISCONNECTED:
                raise ChannelNotFound("replacement target is not connected")
            owns_live = conn.execute(
                "SELECT 1 FROM channel_bindings WHERE session_id = ? AND bot_account_id = ? "
                "AND status IN ('active', 'paused')",
                (sid, entity_id),
            ).fetchone()
            owns_draft = bot["draft_session_id"] == sid
            if not owns_live and not owns_draft:
                raise ChannelConflict("replacement target does not belong to session")
            if not bot["credential_id"]:
                raise ChannelConflict("replacement target has no credential")
            conn.execute(
                "INSERT INTO token_replacement_intents "
                "(session_id, bot_account_id, expected_credential_revision, expires_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET bot_account_id = excluded.bot_account_id, "
                "expected_credential_revision = excluded.expected_credential_revision, "
                "expires_at = excluded.expires_at, updated_at = excluded.updated_at",
                (sid, entity_id, int(bot["credential_revision"]), expires_at, now, now),
            )
        return self.bot(entity_id)

    def clear_token_replacement_intent(self, session_id: Any, bot_account_id: Any) -> None:
        """Clear only the matching completed replacement intent."""
        sid = self._required_text(session_id, "session_id")
        entity_id = _entity_id(bot_account_id)
        with self._immediate() as conn:
            conn.execute(
                "DELETE FROM token_replacement_intents WHERE session_id = ? AND bot_account_id = ?",
                (sid, entity_id),
            )

    def cas_bot_credential(
        self,
        bot_account_id: Any,
        *,
        expected_revision: int,
        credential_id: str,
        telegram_bot_id: str | int,
        telegram_username: Optional[str] = None,
        status: BotStatus | str | None = None,
        verified_at: datetime | str | None = None,
        pause_live_binding_if_identity_changed: bool = False,
        expected_replacement_session_id: str | None = None,
    ) -> BotAccount:
        """Atomically install a verified opaque credential using revision CAS.

        Network verification and secret-file creation must happen before this
        method.  A successful switch increments ``credential_revision`` exactly
        once; stale callers cannot overwrite a newer credential.
        """
        entity_id = _entity_id(bot_account_id)
        if not isinstance(credential_id, str) or not _CREDENTIAL_ID_RE.fullmatch(
            credential_id
        ):
            raise ValueError("credential_id must be an opaque cred_<32 hex> id")
        verified = self._now() if verified_at is None else _aware_utc_iso(
            verified_at, field="verified_at"
        )
        safe_username = self._optional_channel_text(telegram_username, "telegram_username")
        try:
            with self._immediate() as conn:
                row = conn.execute(
                    "SELECT credential_revision, status, telegram_bot_id, credential_id "
                    "FROM bot_accounts WHERE id = ?",
                    (entity_id,),
                ).fetchone()
                if row is None:
                    raise ChannelNotFound(f"unknown bot account: {entity_id}")
                if int(row["credential_revision"]) != int(expected_revision):
                    raise ChannelConflict("bot credential revision changed")
                if expected_replacement_session_id is not None:
                    intent = conn.execute(
                        "SELECT bot_account_id, expected_credential_revision, expires_at "
                        "FROM token_replacement_intents WHERE session_id = ?",
                        (self._required_text(expected_replacement_session_id, "session_id"),),
                    ).fetchone()
                    if (
                        intent is None
                        or str(intent["bot_account_id"]) != entity_id
                        or intent["expected_credential_revision"] is None
                        or int(intent["expected_credential_revision"]) != int(expected_revision)
                        or intent["expires_at"] is None
                        or str(intent["expires_at"]) <= verified
                    ):
                        raise ChannelConflict("replacement intent expired or changed")
                current = BotStatus(row["status"])
                target = (
                    BotStatus.AWAITING_PAIRING
                    if status is None and current is BotStatus.DRAFT
                    else current
                    if status is None
                    else BotStatus(_enum_value(status))
                )
                identity_changed = (
                    row["telegram_bot_id"] is not None
                    and str(row["telegram_bot_id"]) != str(telegram_bot_id)
                )
                fault_identity_change = bool(
                    pause_live_binding_if_identity_changed and identity_changed
                )
                old_credential_id = row["credential_id"]
                if old_credential_id is not None and str(old_credential_id) != credential_id:
                    # The old opaque reference becomes a deletion obligation
                    # *inside the same transaction* as the new credential
                    # reference.  A crash after the CAS can therefore never
                    # strand a superseded ciphertext without durable recovery
                    # metadata, while cleanup remains orthogonal to Bot state.
                    conn.execute(
                        "INSERT INTO credential_cleanup_queue "
                        "(credential_id, bot_account_id, reason, created_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(credential_id) DO UPDATE SET "
                        "bot_account_id = COALESCE(credential_cleanup_queue.bot_account_id, excluded.bot_account_id)",
                        (str(old_credential_id), entity_id, "replace_cleanup_failed", verified),
                    )
                if fault_identity_change:
                    # A recipient is bound to a Telegram *bot identity*, not
                    # merely an opaque local account.  Atomically fault-pause
                    # that binding before publishing a new verified identity;
                    # Task 5 can later re-approve the same durable binding.
                    conn.execute(
                        "UPDATE channel_bindings SET status = 'paused', "
                        "pause_reason = CASE WHEN status = 'paused' "
                        "AND pause_reason IN ('manual', 'archive', 'workspace_archived') "
                        "THEN pause_reason ELSE 'bot_identity_changed' END, "
                        "paused_at = CASE WHEN status = 'paused' "
                        "AND pause_reason IN ('manual', 'archive', 'workspace_archived') "
                        "THEN paused_at ELSE ? END, updated_at = ? WHERE bot_account_id = ? "
                        "AND status IN ('active', 'paused')",
                        (verified, verified, entity_id),
                    )
                    target = BotStatus.AWAITING_PAIRING
                # A pairing challenge authenticates one verified credential
                # generation.  Rotating the Token invalidates every challenge
                # issued by the previous generation, even when getMe reports
                # the same Telegram Bot ID.
                conn.execute(
                    "UPDATE pairing_requests SET status = 'dismissed', last_seen_at = ? "
                    "WHERE bot_account_id = ? AND status = 'pending'",
                    (verified, entity_id),
                )
                updated = conn.execute(
                    "UPDATE bot_accounts SET credential_id = ?, telegram_bot_id = ?, "
                    "telegram_username = ?, credential_revision = credential_revision + 1, "
                    "status = ?, poll_owner = NULL, poll_lease_until = NULL, "
                    "update_offset = CASE WHEN ? THEN 0 ELSE update_offset END, "
                    "last_verified_at = ?, last_error_code = ?, updated_at = ? "
                    "WHERE id = ? AND credential_revision = ?",
                    (
                        credential_id,
                        str(telegram_bot_id),
                        safe_username,
                        target.value,
                        1 if identity_changed else 0,
                        verified,
                        "telegram_identity_changed" if fault_identity_change else None,
                        verified,
                        entity_id,
                        int(expected_revision),
                    ),
                )
                if updated.rowcount != 1:
                    raise ChannelConflict("bot credential revision changed")
                if expected_replacement_session_id is not None:
                    conn.execute(
                        "DELETE FROM token_replacement_intents WHERE session_id = ? AND bot_account_id = ?",
                        (expected_replacement_session_id, entity_id),
                    )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("telegram bot identity already belongs to a live account") from exc
        return self.bot(entity_id)

    compare_and_swap_credential = cas_bot_credential
    commit_bot_credential = cas_bot_credential

    def activate_test_bot(
        self,
        bot_account_id: Any,
        *,
        telegram_bot_id: str | int,
        telegram_username: Optional[str] = None,
    ) -> BotAccount:
        """Seed verified identity for isolated store tests.

        Production credential verification is deliberately implemented by the
        runtime layer; this helper never accepts or stores a credential.
        """
        entity_id = _entity_id(bot_account_id)
        now = self._now()
        safe_username = self._optional_channel_text(telegram_username, "telegram_username")
        try:
            with self._immediate() as conn:
                cur = conn.execute(
                    "UPDATE bot_accounts SET telegram_bot_id = ?, telegram_username = ?, "
                    "status = 'active', last_verified_at = ?, last_error_code = NULL, "
                    "updated_at = ? WHERE id = ? AND status <> 'disconnected'",
                    (str(telegram_bot_id), safe_username, now, now, entity_id),
                )
                if cur.rowcount != 1:
                    raise ChannelNotFound(f"unknown live bot account: {entity_id}")
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("telegram bot identity already belongs to a live account") from exc
        return self.bot(entity_id)

    def transition_bot(
        self,
        bot_account_id: Any,
        status: BotStatus | str,
        *,
        error_code: Optional[str] = None,
    ) -> BotAccount:
        entity_id = _entity_id(bot_account_id)
        target = BotStatus(_enum_value(status))
        safe_error = self._optional_safe_error(error_code, "error_code")
        now = self._now()
        try:
            with self._immediate() as conn:
                row = conn.execute(
                    "SELECT status FROM bot_accounts WHERE id = ?", (entity_id,)
                ).fetchone()
                if row is None:
                    raise ChannelNotFound(f"unknown bot account: {entity_id}")
                current = BotStatus(row["status"])
                _check_transition("bot", current, target, _BOT_TRANSITIONS)
                conn.execute(
                    "UPDATE bot_accounts SET status = ?, last_error_code = ?, updated_at = ? "
                    "WHERE id = ?",
                    (target.value, safe_error, now, entity_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("bot status change violates a live identity constraint") from exc
        return self.bot(entity_id)

    def fault_pause_bot(
        self,
        bot_account_id: Any,
        *,
        status: BotStatus | str,
        error_code: str,
        expected_credential_revision: int | None = None,
        expected_telegram_bot_id: str | None = None,
    ) -> BotAccount:
        """Atomically persist a credential/identity fault and stop delivery.

        Reverification must never report a transient HTTP result while leaving
        a live binding visibly active.  This single transaction records the
        safe fault marker and fault-pauses the existing one-to-one recipient.
        Optional revision/identity expectations prevent a stale verifier from
        overwriting a concurrent credential replacement.
        """
        entity_id = _entity_id(bot_account_id)
        target = BotStatus(_enum_value(status))
        if target not in {
            BotStatus.PAUSED,
            BotStatus.POLL_CONFLICT,
            BotStatus.CREDENTIAL_INVALID,
            BotStatus.CREDENTIAL_UNAVAILABLE,
        }:
            raise ValueError("fault pause requires a fault-safe bot status")
        if not isinstance(error_code, str) or not _SAFE_ERROR_RE.fullmatch(error_code):
            raise ValueError("error_code must be a safe error code")
        now = self._now()
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT credential_revision, telegram_bot_id, status FROM bot_accounts WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown bot account: {entity_id}")
            if (
                expected_credential_revision is not None
                and int(row["credential_revision"]) != int(expected_credential_revision)
            ):
                raise ChannelConflict("bot credential revision changed during fault pause")
            if (
                expected_telegram_bot_id is not None
                and row["telegram_bot_id"] != str(expected_telegram_bot_id)
            ):
                raise ChannelConflict("telegram bot identity changed during fault pause")
            if BotStatus(row["status"]) is BotStatus.DISCONNECTED:
                raise ChannelConflict("cannot fault-pause a disconnected bot")
            # A user-selected manual pause must survive an intervening
            # credential/poll fault.  It is still safe to record the Bot fault,
            # but later recovery must not silently resume delivery that the user
            # intentionally stopped.
            conn.execute(
                "UPDATE channel_bindings SET status = 'paused', "
                "pause_reason = CASE WHEN status = 'paused' "
                "AND pause_reason IN ('manual', 'archive', 'workspace_archived') "
                "THEN pause_reason ELSE ? END, "
                "paused_at = CASE WHEN status = 'paused' "
                "AND pause_reason IN ('manual', 'archive', 'workspace_archived') "
                "THEN paused_at ELSE ? END, updated_at = ? "
                "WHERE bot_account_id = ? AND status IN ('active', 'paused')",
                (error_code, now, now, entity_id),
            )
            conn.execute(
                "UPDATE bot_accounts SET status = ?, last_error_code = ?, updated_at = ? WHERE id = ?",
                (target.value, error_code, now, entity_id),
            )
        return self.bot(entity_id)

    def recover_bot_after_verification(
        self,
        bot_account_id: Any,
        *,
        expected_credential_revision: int,
        expected_telegram_bot_id: str | None,
    ) -> BotAccount:
        """Clear a recoverable verification fault after a matching getMe.

        A manual/archive pause is deliberately preserved.  Only pauses written
        by :meth:`fault_pause_bot` for a retryable credential/poll condition
        resume delivery.  ``bot_identity_changed`` remains paused and needs a
        fresh pairing approval.
        """
        entity_id = _entity_id(bot_account_id)
        now = self._now()
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT credential_revision, telegram_bot_id, status FROM bot_accounts WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown bot account: {entity_id}")
            if int(row["credential_revision"]) != int(expected_credential_revision):
                raise ChannelConflict("bot credential revision changed during recovery")
            if (
                expected_telegram_bot_id is not None
                and row["telegram_bot_id"] != str(expected_telegram_bot_id)
            ):
                raise ChannelConflict("telegram bot identity changed during recovery")
            current = BotStatus(row["status"])
            if current in {
                BotStatus.CREDENTIAL_INVALID,
                BotStatus.CREDENTIAL_UNAVAILABLE,
                BotStatus.POLL_CONFLICT,
            }:
                binding = conn.execute(
                    "SELECT id, status, pause_reason FROM channel_bindings WHERE bot_account_id = ? "
                    "AND status IN ('active', 'paused') ORDER BY created_at DESC, id DESC LIMIT 1",
                    (entity_id,),
                ).fetchone()
                target = BotStatus.AWAITING_PAIRING
                if binding is not None:
                    binding_status = BindingStatus(binding["status"])
                    reason = binding["pause_reason"]
                    if (
                        binding_status is BindingStatus.PAUSED
                        and reason in _RECOVERABLE_FAULT_PAUSE_REASONS
                    ):
                        conn.execute(
                            "UPDATE channel_bindings SET status = 'active', pause_reason = NULL, "
                            "paused_at = NULL, updated_at = ? WHERE id = ?",
                            (now, binding["id"]),
                        )
                        target = BotStatus.ACTIVE
                    elif binding_status is BindingStatus.ACTIVE:
                        target = BotStatus.ACTIVE
                    else:
                        target = BotStatus.PAUSED
                conn.execute(
                    "UPDATE bot_accounts SET status = ?, last_error_code = NULL, updated_at = ? WHERE id = ?",
                    (target.value, now, entity_id),
                )
        return self.bot(entity_id)

    def set_session_manual_pause(
        self,
        session_id: Any,
        *,
        paused: bool,
        resume_from_event_id: int | None = None,
    ) -> BotAccount:
        """Atomically apply or lift an intentional session pause.

        Fault pauses are never resumable through this path.  In particular a
        visible ``paused`` binding is not enough authority to resume a Bot with
        credential, identity, or polling fault state.
        """
        sid = self._required_text(session_id, "session_id")
        resume_cursor: int | None = None
        if not paused:
            if isinstance(resume_from_event_id, bool) or resume_from_event_id is None:
                raise ValueError("resume_from_event_id is required when resuming")
            resume_cursor = int(resume_from_event_id)
            if resume_cursor < 0:
                raise ValueError("resume_from_event_id must be non-negative")
        now = self._now()
        with self._immediate() as conn:
            binding = conn.execute(
                "SELECT * FROM channel_bindings WHERE session_id = ? "
                "AND status IN ('active', 'paused') ORDER BY created_at DESC, id DESC LIMIT 1",
                (sid,),
            ).fetchone()
            if binding is None:
                raise ChannelNotFound("session has no live channel binding")
            bot = conn.execute(
                "SELECT * FROM bot_accounts WHERE id = ?", (binding["bot_account_id"],)
            ).fetchone()
            if bot is None:
                raise ChannelNotFound("binding references an unknown bot")
            binding_status = BindingStatus(binding["status"])
            bot_status = BotStatus(bot["status"])
            if paused:
                # Do not relabel a fault pause as manual just because a UI
                # click races it; preserving provenance is what makes resume
                # safe after a later reverify.
                if binding_status is BindingStatus.ACTIVE:
                    # Intentional pause has an explicit delivery boundary:
                    # work that has not entered Telegram is cancelled, while
                    # a leased send remains uncertain for human resolution.
                    conn.execute(
                        "UPDATE outbox SET state = 'cancelled', lease_owner = NULL, lease_until = NULL "
                        "WHERE binding_id = ? AND state IN ('pending', 'retry_wait')",
                        (binding["id"],),
                    )
                    conn.execute(
                        "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, lease_until = NULL, "
                        "last_error_class = 'pause_inflight', last_error_at = ? "
                        "WHERE binding_id = ? AND state = 'sending'",
                        (now, binding["id"]),
                    )
                    conn.execute(
                        "UPDATE channel_bindings SET status = 'paused', pause_reason = 'manual', "
                        "paused_at = ?, updated_at = ? WHERE id = ?",
                        (now, now, binding["id"]),
                    )
                    if bot_status is BotStatus.ACTIVE:
                        conn.execute(
                            "UPDATE bot_accounts SET status = 'paused', last_error_code = NULL, "
                            "updated_at = ? WHERE id = ?",
                            (now, bot["id"]),
                        )
            else:
                reason = binding["pause_reason"]
                if (
                    binding_status is not BindingStatus.PAUSED
                    or reason not in {"manual", "archive", "workspace_archived"}
                    or bot_status is not BotStatus.PAUSED
                ):
                    raise ChannelConflict("only an intentional manual pause can resume")
                conn.execute(
                    "UPDATE channel_bindings SET status = 'active', pause_reason = NULL, "
                    "paused_at = NULL, sync_from_event_id = ?, "
                    "projection_cursor_event_id = ?, updated_at = ? WHERE id = ?",
                    (resume_cursor, resume_cursor, now, binding["id"]),
                )
                conn.execute(
                    "UPDATE bot_accounts SET status = 'active', last_error_code = NULL, "
                    "updated_at = ? WHERE id = ?",
                    (now, bot["id"]),
                )
        return self.bot(str(binding["bot_account_id"]))

    def disconnect_session(self, session_id: Any) -> tuple[BotAccount, Optional[str]]:
        """Detach delivery and credential reference in one durable transaction.

        The opaque old credential is first made a durable cleanup obligation.
        The caller may then attempt deletion outside SQLite; a crash or DPAPI
        failure leaves the queue entry intact while the disconnected account can
        no longer falsely claim to reference the old secret.
        """
        sid = self._required_text(session_id, "session_id")
        now = self._now()
        old_credential: Optional[str] = None
        bot_id: Optional[str] = None
        with self._immediate() as conn:
            binding = conn.execute(
                "SELECT * FROM channel_bindings WHERE session_id = ? "
                "AND status IN ('active', 'paused') ORDER BY created_at DESC, id DESC LIMIT 1",
                (sid,),
            ).fetchone()
            if binding is not None:
                bot = conn.execute(
                    "SELECT * FROM bot_accounts WHERE id = ?", (binding["bot_account_id"],)
                ).fetchone()
            else:
                bot = conn.execute(
                    "SELECT * FROM bot_accounts WHERE draft_session_id = ? AND status <> 'disconnected' "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (sid,),
                ).fetchone()
            if bot is None:
                raise ChannelNotFound("session has no channel account")
            bot_id = str(bot["id"])
            old_credential = bot["credential_id"]
            if old_credential is not None:
                conn.execute(
                    "INSERT INTO credential_cleanup_queue "
                    "(credential_id, bot_account_id, reason, created_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(credential_id) DO UPDATE SET "
                    "bot_account_id = COALESCE(credential_cleanup_queue.bot_account_id, excluded.bot_account_id)",
                    (str(old_credential), bot_id, "disconnect_cleanup_failed", now),
                )
            if binding is not None:
                conn.execute(
                    "UPDATE outbox SET state = 'cancelled', lease_owner = NULL, lease_until = NULL "
                    "WHERE binding_id = ? AND state IN ('pending', 'retry_wait')",
                    (binding["id"],),
                )
                conn.execute(
                    "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, lease_until = NULL, "
                    "last_error_class = 'disconnect_inflight', last_error_at = ? "
                    "WHERE binding_id = ? AND state = 'sending'",
                    (now, binding["id"]),
                )
                conn.execute(
                    "UPDATE channel_bindings SET status = 'disconnected', pause_reason = NULL, "
                    "disconnected_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, binding["id"]),
                )
            conn.execute(
                "UPDATE bot_accounts SET credential_id = NULL, credential_revision = credential_revision + 1, "
                "status = 'disconnected', last_error_code = NULL, updated_at = ? WHERE id = ?",
                (now, bot_id),
            )
            conn.execute(
                "DELETE FROM token_replacement_intents WHERE session_id = ?", (sid,)
            )
        assert bot_id is not None
        return self.bot(bot_id), None if old_credential is None else str(old_credential)

    # -- Telegram polling fences ------------------------------------

    @staticmethod
    def _poll_revision(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("credential_revision must be a non-negative integer")
        revision = int(value)
        if revision < 0:
            raise ValueError("credential_revision must be a non-negative integer")
        return revision

    def acquire_poll_lease(
        self,
        bot_account_id: Any,
        *,
        owner: str,
        credential_revision: int,
        lease_seconds: int = 60,
    ) -> bool:
        """Acquire one Bot poller lease, fenced by credential generation."""
        entity_id = _entity_id(bot_account_id)
        safe_owner = self._required_text(owner, "owner")
        revision = self._poll_revision(credential_revision)
        if isinstance(lease_seconds, bool) or not 1 <= int(lease_seconds) <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        now_dt = self._clock()
        now = _aware_utc_iso(now_dt, field="clock")
        until = _aware_utc_iso(
            now_dt + timedelta(seconds=int(lease_seconds)), field="poll_lease_until"
        )
        with self._immediate() as conn:
            updated = conn.execute(
                "UPDATE bot_accounts SET poll_owner = ?, poll_lease_until = ?, updated_at = ? "
                "WHERE id = ? AND credential_revision = ? AND credential_id IS NOT NULL "
                "AND status IN ('awaiting_pairing', 'active', 'paused') "
                "AND (poll_owner IS NULL OR poll_owner = ? OR poll_lease_until IS NULL "
                "OR poll_lease_until <= ?)",
                (safe_owner, until, now, entity_id, revision, safe_owner, now),
            )
            return updated.rowcount == 1

    def renew_poll_lease(
        self,
        bot_account_id: Any,
        *,
        owner: str,
        credential_revision: int,
        lease_seconds: int = 60,
    ) -> bool:
        """Renew only the still-live lease owned by this exact poller."""
        entity_id = _entity_id(bot_account_id)
        safe_owner = self._required_text(owner, "owner")
        revision = self._poll_revision(credential_revision)
        if isinstance(lease_seconds, bool) or not 1 <= int(lease_seconds) <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        now_dt = self._clock()
        now = _aware_utc_iso(now_dt, field="clock")
        until = _aware_utc_iso(
            now_dt + timedelta(seconds=int(lease_seconds)), field="poll_lease_until"
        )
        with self._immediate() as conn:
            updated = conn.execute(
                "UPDATE bot_accounts SET poll_lease_until = ?, updated_at = ? "
                "WHERE id = ? AND credential_revision = ? AND poll_owner = ? "
                "AND poll_lease_until > ? AND credential_id IS NOT NULL "
                "AND status IN ('awaiting_pairing', 'active', 'paused')",
                (until, now, entity_id, revision, safe_owner, now),
            )
            return updated.rowcount == 1

    def release_poll_lease(
        self,
        bot_account_id: Any,
        *,
        owner: str,
        credential_revision: int,
    ) -> bool:
        """Release only the caller's generation-fenced lease."""
        entity_id = _entity_id(bot_account_id)
        safe_owner = self._required_text(owner, "owner")
        revision = self._poll_revision(credential_revision)
        now = self._now()
        with self._immediate() as conn:
            updated = conn.execute(
                "UPDATE bot_accounts SET poll_owner = NULL, poll_lease_until = NULL, "
                "updated_at = ? WHERE id = ? AND credential_revision = ? AND poll_owner = ?",
                (now, entity_id, revision, safe_owner),
            )
            return updated.rowcount == 1

    @staticmethod
    def _telegram_update_id(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("telegram_update_id must be a non-negative integer")
        update_id = int(value)
        if update_id < 0:
            raise ValueError("telegram_update_id must be a non-negative integer")
        return update_id

    def pairing_update_preflight(
        self,
        bot_account_id: Any,
        *,
        telegram_update_id: int,
        telegram_chat_id: str | int,
        owner: str,
        credential_revision: int,
    ) -> str:
        """Return ``issue``, ``replace``, ``full`` or ``consumed``.

        This is advisory only; the post-send commit repeats every capacity and
        fence check under ``BEGIN IMMEDIATE``.  Its purpose is to avoid sending
        a challenge when three other private recipients already occupy the
        bounded pending set.
        """
        entity_id = _entity_id(bot_account_id)
        update_id = self._telegram_update_id(telegram_update_id)
        chat_id = str(telegram_chat_id)
        safe_owner = self._required_text(owner, "owner")
        revision = self._poll_revision(credential_revision)
        now = self._now()
        with self._immediate() as conn:
            bot = conn.execute(
                "SELECT status, update_offset, credential_revision, poll_owner, "
                "poll_lease_until FROM bot_accounts WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if (
                bot is None
                or int(bot["credential_revision"]) != revision
                or bot["poll_owner"] != safe_owner
                or bot["poll_lease_until"] is None
                or str(bot["poll_lease_until"]) <= now
                or BotStatus(bot["status"]) is not BotStatus.AWAITING_PAIRING
            ):
                raise ChannelConflict("poll lease or credential generation changed")
            if update_id < int(bot["update_offset"]):
                return "consumed"
            conn.execute(
                "UPDATE pairing_requests SET status = 'expired', last_seen_at = ? "
                "WHERE bot_account_id = ? AND status = 'pending' AND expires_at <= ?",
                (now, entity_id, now),
            )
            existing_update = conn.execute(
                "SELECT 1 FROM pairing_requests WHERE bot_account_id = ? "
                "AND telegram_update_id = ?",
                (entity_id, str(update_id)),
            ).fetchone()
            if existing_update is not None:
                conn.execute(
                    "UPDATE bot_accounts SET update_offset = MAX(update_offset, ?), "
                    "updated_at = ? WHERE id = ? AND credential_revision = ? "
                    "AND poll_owner = ? AND poll_lease_until > ?",
                    (update_id + 1, now, entity_id, revision, safe_owner, now),
                )
                return "consumed"
            same_chat = conn.execute(
                "SELECT 1 FROM pairing_requests WHERE bot_account_id = ? "
                "AND telegram_chat_id = ? AND status = 'pending' LIMIT 1",
                (entity_id, chat_id),
            ).fetchone()
            if same_chat is not None:
                return "replace"
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM pairing_requests WHERE bot_account_id = ? "
                    "AND status = 'pending'",
                    (entity_id,),
                ).fetchone()[0]
            )
            return "full" if count >= 3 else "issue"

    def advance_poll_update(
        self,
        bot_account_id: Any,
        *,
        next_offset: int,
        owner: str,
        credential_revision: int,
    ) -> bool:
        """Advance a consumed/rejected update under owner+revision fencing."""
        entity_id = _entity_id(bot_account_id)
        offset = self._telegram_update_id(next_offset)
        safe_owner = self._required_text(owner, "owner")
        revision = self._poll_revision(credential_revision)
        now = self._now()
        with self._immediate() as conn:
            updated = conn.execute(
                "UPDATE bot_accounts SET update_offset = MAX(update_offset, ?), updated_at = ? "
                "WHERE id = ? AND credential_revision = ? AND poll_owner = ? "
                "AND poll_lease_until > ? AND status IN "
                "('awaiting_pairing', 'active', 'paused')",
                (offset, now, entity_id, revision, safe_owner, now),
            )
            return updated.rowcount == 1

    def commit_sent_pairing_update(
        self,
        *,
        bot_account_id: Any,
        session_id: str,
        telegram_update_id: int,
        telegram_user_id: str | int,
        telegram_chat_id: str | int,
        telegram_username: str | None,
        telegram_display_name: str | None,
        code_salt: bytes,
        code_digest: bytes,
        code_hint: str,
        owner: str,
        credential_revision: int,
    ) -> PairingRequest:
        """Persist the challenge and offset only after send acceptance.

        The caller may arrive here after either an explicit Bot API success or
        an ambiguous send result.  No network call is made in this method.
        """
        entity_id = _entity_id(bot_account_id)
        sid = self._required_text(session_id, "session_id")
        update_id = self._telegram_update_id(telegram_update_id)
        user_id = str(telegram_user_id)
        chat_id = str(telegram_chat_id)
        if user_id != chat_id:
            raise ChannelConflict("pairing request must come from a private user chat")
        safe_owner = self._required_text(owner, "owner")
        revision = self._poll_revision(credential_revision)
        salt = bytes(code_salt)
        digest = bytes(code_digest)
        if len(salt) != 16 or len(digest) != 32:
            raise ValueError("pairing digest material has an invalid size")
        hint = self._required_channel_text(code_hint, "code_hint")
        if len(hint) != 2:
            raise ValueError("code_hint must contain exactly two characters")
        username = self._optional_channel_text(telegram_username, "telegram_username")
        display_name = self._optional_channel_text(
            telegram_display_name, "telegram_display_name"
        )
        now_dt = self._clock()
        now = _aware_utc_iso(now_dt, field="clock")
        expires = _aware_utc_iso(now_dt + timedelta(hours=1), field="expires_at")
        request_id = _new_id("pair")
        with self._immediate() as conn:
            bot = conn.execute(
                "SELECT * FROM bot_accounts WHERE id = ?", (entity_id,)
            ).fetchone()
            if (
                bot is None
                or int(bot["credential_revision"]) != revision
                or bot["poll_owner"] != safe_owner
                or bot["poll_lease_until"] is None
                or str(bot["poll_lease_until"]) <= now
                or BotStatus(bot["status"]) is not BotStatus.AWAITING_PAIRING
                or bot["draft_session_id"] != sid
            ):
                raise ChannelConflict("poll lease, draft, or credential generation changed")
            if update_id < int(bot["update_offset"]):
                existing = conn.execute(
                    "SELECT * FROM pairing_requests WHERE bot_account_id = ? "
                    "AND telegram_update_id = ?",
                    (entity_id, str(update_id)),
                ).fetchone()
                if existing is None:
                    raise ChannelConflict("telegram update was already consumed")
                return _pairing_from_row(existing)
            conn.execute(
                "UPDATE pairing_requests SET status = 'expired', last_seen_at = ? "
                "WHERE bot_account_id = ? AND status = 'pending' AND expires_at <= ?",
                (now, entity_id, now),
            )
            existing = conn.execute(
                "SELECT * FROM pairing_requests WHERE bot_account_id = ? "
                "AND telegram_update_id = ?",
                (entity_id, str(update_id)),
            ).fetchone()
            if existing is not None:
                # If a damaged/stale offset replays an already committed
                # update, make the *latest* delivered code authoritative.
                conn.execute(
                    "UPDATE pairing_requests SET code_salt = ?, code_digest = ?, code_hint = ?, "
                    "issue_credential_revision = ?, status = 'pending', created_at = ?, "
                    "expires_at = ?, last_seen_at = ? WHERE id = ?",
                    (
                        sqlite3.Binary(salt),
                        sqlite3.Binary(digest),
                        hint,
                        revision,
                        now,
                        expires,
                        now,
                        existing["id"],
                    ),
                )
                request_id = str(existing["id"])
            else:
                other_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM pairing_requests WHERE bot_account_id = ? "
                        "AND status = 'pending' AND telegram_chat_id <> ?",
                        (entity_id, chat_id),
                    ).fetchone()[0]
                )
                if other_count >= 3:
                    raise ChannelConflict("pairing request capacity changed")
                # The old challenge remains valid until this transaction sees
                # proof that the replacement message was accepted.
                conn.execute(
                    "UPDATE pairing_requests SET status = 'dismissed', last_seen_at = ? "
                    "WHERE bot_account_id = ? AND telegram_chat_id = ? AND status = 'pending'",
                    (now, entity_id, chat_id),
                )
                conn.execute(
                    "INSERT INTO pairing_requests "
                    "(id, bot_account_id, session_id, telegram_update_id, telegram_user_id, "
                    "telegram_chat_id, telegram_username, telegram_display_name, code_salt, "
                    "code_digest, code_hint, issue_credential_revision, status, created_at, "
                    "expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "'pending', ?, ?, ?)",
                    (
                        request_id,
                        entity_id,
                        sid,
                        str(update_id),
                        user_id,
                        chat_id,
                        username,
                        display_name,
                        sqlite3.Binary(salt),
                        sqlite3.Binary(digest),
                        hint,
                        revision,
                        now,
                        expires,
                        now,
                    ),
                )
            advanced = conn.execute(
                "UPDATE bot_accounts SET update_offset = MAX(update_offset, ?), updated_at = ? "
                "WHERE id = ? AND credential_revision = ? AND poll_owner = ? "
                "AND poll_lease_until > ?",
                (update_id + 1, now, entity_id, revision, safe_owner, now),
            )
            if advanced.rowcount != 1:
                raise ChannelConflict("poll lease changed before pairing commit")
            conn.execute(
                "INSERT INTO channel_audit (ts, event_kind, session_id, bot_account_id, "
                "pairing_request_id, safe_details_json) VALUES (?, 'pairing_requested', ?, ?, ?, '{}')",
                (now, sid, entity_id, request_id),
            )
        return self.pairing_request(request_id)

    def fault_poll_bot(
        self,
        bot_account_id: Any,
        *,
        owner: str,
        credential_revision: int,
        status: BotStatus | str,
        error_code: str,
    ) -> bool:
        """Fault-pause one Bot only if this poller still owns its generation."""
        entity_id = _entity_id(bot_account_id)
        safe_owner = self._required_text(owner, "owner")
        revision = self._poll_revision(credential_revision)
        target = BotStatus(_enum_value(status))
        if target not in {
            BotStatus.POLL_CONFLICT,
            BotStatus.CREDENTIAL_INVALID,
            BotStatus.CREDENTIAL_UNAVAILABLE,
        }:
            raise ValueError("unsupported poll fault status")
        if not isinstance(error_code, str) or not _SAFE_ERROR_RE.fullmatch(error_code):
            raise ValueError("error_code must be a safe error code")
        now = self._now()
        with self._immediate() as conn:
            bot = conn.execute(
                "SELECT status, credential_revision, poll_owner, poll_lease_until "
                "FROM bot_accounts WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if (
                bot is None
                or int(bot["credential_revision"]) != revision
                or bot["poll_owner"] != safe_owner
                or bot["poll_lease_until"] is None
                or str(bot["poll_lease_until"]) <= now
                or BotStatus(bot["status"]) not in {
                    BotStatus.AWAITING_PAIRING,
                    BotStatus.ACTIVE,
                    BotStatus.PAUSED,
                }
            ):
                return False
            conn.execute(
                "UPDATE channel_bindings SET status = 'paused', "
                "pause_reason = CASE WHEN status = 'paused' AND pause_reason IN "
                "('manual', 'archive', 'workspace_archived') THEN pause_reason ELSE ? END, "
                "paused_at = CASE WHEN status = 'paused' AND pause_reason IN "
                "('manual', 'archive', 'workspace_archived') THEN paused_at ELSE ? END, "
                "updated_at = ? WHERE bot_account_id = ? AND status IN ('active', 'paused')",
                (error_code, now, now, entity_id),
            )
            conn.execute(
                "UPDATE bot_accounts SET status = ?, last_error_code = ?, poll_owner = NULL, "
                "poll_lease_until = NULL, updated_at = ? WHERE id = ?",
                (target.value, error_code, now, entity_id),
            )
            return True

    # -- Bindings and pairing ----------------------------------------

    def bind_for_test(
        self,
        session_id: str,
        bot_account_id: Any,
        *,
        user_id: str | int,
        chat_id: str | int,
        sync_from_event_id: int,
        telegram_username: Optional[str] = None,
        telegram_display_name: Optional[str] = None,
        status: BindingStatus | str = BindingStatus.ACTIVE,
    ) -> ChannelBinding:
        entity_id = _entity_id(bot_account_id)
        target = BindingStatus(_enum_value(status))
        if target not in {BindingStatus.ACTIVE, BindingStatus.PAUSED}:
            raise ValueError("test bindings must start active or paused")
        cursor = int(sync_from_event_id)
        if cursor < 0:
            raise ValueError("sync_from_event_id must be non-negative")
        binding_id = _new_id("bind")
        now = self._now()
        safe_username = self._optional_channel_text(telegram_username, "telegram_username")
        safe_display_name = self._optional_channel_text(
            telegram_display_name, "telegram_display_name"
        )
        try:
            with self._immediate() as conn:
                if conn.execute(
                    "SELECT 1 FROM bot_accounts WHERE id = ?", (entity_id,)
                ).fetchone() is None:
                    raise ChannelNotFound(f"unknown bot account: {entity_id}")
                conn.execute(
                    "INSERT INTO channel_bindings "
                    "(id, channel, session_id, bot_account_id, telegram_user_id, "
                    "telegram_chat_id, telegram_username, telegram_display_name, status, "
                    "pause_reason, paired_at, sync_from_event_id, projection_cursor_event_id, "
                    "paused_at, created_at, updated_at) "
                    "VALUES (?, 'telegram', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                    (
                        binding_id,
                        self._required_text(session_id, "session_id"),
                        entity_id,
                        str(user_id),
                        str(chat_id),
                        safe_username,
                        safe_display_name,
                        target.value,
                        now,
                        cursor,
                        cursor,
                        now if target is BindingStatus.PAUSED else None,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE bot_accounts SET status = ?, updated_at = ? WHERE id = ?",
                    (
                        BotStatus.PAUSED.value
                        if target is BindingStatus.PAUSED
                        else BotStatus.ACTIVE.value,
                        now,
                        entity_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("live channel binding must be one-to-one") from exc
        return self.binding(binding_id)

    def binding(self, binding_id: Any) -> ChannelBinding:
        entity_id = _entity_id(binding_id)
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM channel_bindings WHERE id = ?", (entity_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ChannelNotFound(f"unknown channel binding: {entity_id}")
        return _binding_from_row(row)

    def binding_for_session(
        self, session_id: str, *, live_only: bool = True
    ) -> Optional[ChannelBinding]:
        sql = "SELECT * FROM channel_bindings WHERE session_id = ?"
        if live_only:
            sql += " AND status IN ('active', 'paused')"
        sql += " ORDER BY created_at DESC, id DESC LIMIT 1"
        conn = self.connect()
        try:
            row = conn.execute(sql, (session_id,)).fetchone()
        finally:
            conn.close()
        return None if row is None else _binding_from_row(row)

    def binding_for_bot(
        self, bot_account_id: Any, *, live_only: bool = True
    ) -> Optional[ChannelBinding]:
        entity_id = _entity_id(bot_account_id)
        sql = "SELECT * FROM channel_bindings WHERE bot_account_id = ?"
        if live_only:
            sql += " AND status IN ('active', 'paused')"
        sql += " ORDER BY created_at DESC, id DESC LIMIT 1"
        conn = self.connect()
        try:
            row = conn.execute(sql, (entity_id,)).fetchone()
        finally:
            conn.close()
        return None if row is None else _binding_from_row(row)

    def live_bindings(self) -> list[ChannelBinding]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM channel_bindings WHERE status IN ('active', 'paused') "
                "ORDER BY created_at, id"
            ).fetchall()
        finally:
            conn.close()
        return [_binding_from_row(row) for row in rows]

    def transition_binding(
        self,
        binding_id: Any,
        status: BindingStatus | str,
        *,
        pause_reason: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> ChannelBinding:
        entity_id = _entity_id(binding_id)
        target = BindingStatus(_enum_value(status))
        safe_reason = self._optional_safe_error(pause_reason, "pause_reason")
        safe_error = self._optional_safe_error(error_code, "error_code")
        now = self._now()
        try:
            with self._immediate() as conn:
                row = conn.execute(
                    "SELECT status FROM channel_bindings WHERE id = ?", (entity_id,)
                ).fetchone()
                if row is None:
                    raise ChannelNotFound(f"unknown channel binding: {entity_id}")
                current = BindingStatus(row["status"])
                _check_transition("binding", current, target, _BINDING_TRANSITIONS)
                paused_at = now if target is BindingStatus.PAUSED else None
                disconnected_at = now if target is BindingStatus.DISCONNECTED else None
                reason = safe_reason if target is BindingStatus.PAUSED else None
                conn.execute(
                    "UPDATE channel_bindings SET status = ?, pause_reason = ?, paused_at = ?, "
                    "disconnected_at = COALESCE(?, disconnected_at), last_error_code = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        target.value,
                        reason,
                        paused_at,
                        disconnected_at,
                        safe_error,
                        now,
                        entity_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("binding status change violates one-to-one ownership") from exc
        return self.binding(entity_id)

    def create_pairing_request(
        self,
        *,
        bot_account_id: Any,
        session_id: str,
        telegram_update_id: str | int,
        telegram_user_id: str | int,
        telegram_chat_id: str | int,
        code_salt: bytes,
        code_digest: bytes,
        code_hint: str,
        expires_at: datetime | str,
        issue_credential_revision: int | None = None,
        telegram_username: Optional[str] = None,
        telegram_display_name: Optional[str] = None,
        last_seen_at: datetime | str | None = None,
    ) -> PairingRequest:
        entity_id = _entity_id(bot_account_id)
        request_id = _new_id("pair")
        now = self._now()
        seen = now if last_seen_at is None else _aware_utc_iso(
            last_seen_at, field="last_seen_at"
        )
        expiry = _aware_utc_iso(expires_at, field="expires_at")
        update_id = str(telegram_update_id)
        safe_username = self._optional_channel_text(telegram_username, "telegram_username")
        safe_display_name = self._optional_channel_text(
            telegram_display_name, "telegram_display_name"
        )
        try:
            with self._immediate() as conn:
                bot = conn.execute(
                    "SELECT credential_revision FROM bot_accounts WHERE id = ?",
                    (entity_id,),
                ).fetchone()
                if bot is None:
                    raise ChannelNotFound(f"unknown bot account: {entity_id}")
                issue_revision = (
                    int(bot["credential_revision"])
                    if issue_credential_revision is None
                    else int(issue_credential_revision)
                )
                if issue_revision < 0:
                    raise ValueError("issue_credential_revision must be non-negative")
                if issue_revision != int(bot["credential_revision"]):
                    raise ChannelConflict("pairing challenge credential revision is stale")
                conn.execute(
                    "INSERT INTO pairing_requests "
                    "(id, bot_account_id, session_id, telegram_update_id, telegram_user_id, "
                    "telegram_chat_id, telegram_username, telegram_display_name, code_salt, "
                    "code_digest, code_hint, issue_credential_revision, status, created_at, "
                    "expires_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                    (
                        request_id,
                        entity_id,
                        self._required_text(session_id, "session_id"),
                        update_id,
                        str(telegram_user_id),
                        str(telegram_chat_id),
                        safe_username,
                        safe_display_name,
                        sqlite3.Binary(bytes(code_salt)),
                        sqlite3.Binary(bytes(code_digest)),
                        self._required_channel_text(code_hint, "code_hint"),
                        issue_revision,
                        now,
                        expiry,
                        seen,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            conn = self.connect()
            try:
                row = conn.execute(
                    "SELECT * FROM pairing_requests WHERE bot_account_id = ? "
                    "AND telegram_update_id = ?",
                    (entity_id, update_id),
                ).fetchone()
            finally:
                conn.close()
            if row is not None:
                return _pairing_from_row(row)
            raise ChannelConflict("pairing request conflicts with durable channel state") from exc
        return self.pairing_request(request_id)

    def pairing_request(self, pairing_request_id: Any) -> PairingRequest:
        entity_id = _entity_id(pairing_request_id)
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM pairing_requests WHERE id = ?", (entity_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ChannelNotFound(f"unknown pairing request: {entity_id}")
        return _pairing_from_row(row)

    def pairing_requests_for(
        self,
        bot_account_id: Any,
        *,
        status: PairingStatus | str | None = None,
    ) -> list[PairingRequest]:
        entity_id = _entity_id(bot_account_id)
        sql = "SELECT * FROM pairing_requests WHERE bot_account_id = ?"
        params: list[Any] = [entity_id]
        if status is not None:
            sql += " AND status = ?"
            requested_status = PairingStatus(_enum_value(status))
            params.append(requested_status.value)
            if requested_status is PairingStatus.PENDING:
                # Status DTOs must not keep presenting expired challenges just
                # because no new Telegram update arrived to run preflight.
                sql += " AND expires_at > ?"
                params.append(self._now())
        sql += " ORDER BY created_at, id"
        conn = self.connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [_pairing_from_row(row) for row in rows]

    def approve_pairing_request(
        self,
        pairing_request_id: Any,
        *,
        session_id: str,
        sync_from_event_id: int,
        expected_credential_revision: Optional[int] = None,
        expected_telegram_bot_id: Optional[str | int] = None,
        expected_code_digest: bytes | None = None,
        require_private: bool = False,
        now: datetime | str | None = None,
    ) -> ChannelBinding:
        request_id = _entity_id(pairing_request_id)
        caller_session_id = self._required_text(session_id, "session_id")
        cursor = int(sync_from_event_id)
        if cursor < 0:
            raise ValueError("sync_from_event_id must be non-negative")
        decided_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        binding_id = _new_id("bind")
        expired = False
        approved_binding: Optional[ChannelBinding] = None
        try:
            with self._immediate() as conn:
                request = conn.execute(
                    "SELECT * FROM pairing_requests WHERE id = ?", (request_id,)
                ).fetchone()
                if request is None:
                    raise ChannelNotFound(f"unknown pairing request: {request_id}")

                bot = conn.execute(
                    "SELECT * FROM bot_accounts WHERE id = ?", (request["bot_account_id"],)
                ).fetchone()
                if bot is None:
                    raise ChannelNotFound("pairing request references an unknown bot")
                if (
                    request["session_id"] != caller_session_id
                    or bot["draft_session_id"] != caller_session_id
                ):
                    raise ChannelConflict(
                        "pairing request, bot draft, and caller session do not match"
                    )

                request_status = PairingStatus(request["status"])
                if request_status is PairingStatus.APPROVED:
                    # The public idempotency path still requires the same
                    # proof when secure expectations are supplied.  Knowing a
                    # request ID alone must not disclose/confirm ownership.
                    if expected_credential_revision is not None and int(
                        bot["credential_revision"]
                    ) != int(expected_credential_revision):
                        raise ChannelConflict("bot credential revision changed during approval")
                    if int(request["issue_credential_revision"]) != int(
                        bot["credential_revision"]
                    ):
                        raise ChannelConflict("pairing challenge belongs to an old credential")
                    if expected_telegram_bot_id is not None and bot[
                        "telegram_bot_id"
                    ] != str(expected_telegram_bot_id):
                        raise ChannelConflict("telegram bot identity changed during approval")
                    if require_private and str(request["telegram_user_id"]) != str(
                        request["telegram_chat_id"]
                    ):
                        raise ChannelConflict("pairing request is not a private user chat")
                    if expected_code_digest is not None:
                        supplied_digest = bytes(expected_code_digest)
                        if len(supplied_digest) != len(bytes(request["code_digest"])) or not hmac.compare_digest(
                            supplied_digest, bytes(request["code_digest"])
                        ):
                            raise ChannelConflict("pairing code does not match")
                    approved_id = request["approved_binding_id"]
                    row = conn.execute(
                        "SELECT * FROM channel_bindings WHERE id = ?", (approved_id,)
                    ).fetchone()
                    if row is None:
                        raise ChannelConflict("approved pairing has no durable binding")
                    if (
                        row["session_id"] != caller_session_id
                        or row["bot_account_id"] != request["bot_account_id"]
                    ):
                        raise ChannelConflict(
                            "approved binding does not belong to the caller session"
                        )
                    approved_binding = _binding_from_row(row)
                elif request_status is not PairingStatus.PENDING:
                    raise ChannelConflict("pairing request is no longer pending")
                elif decided_at >= request["expires_at"]:
                    conn.execute(
                        "UPDATE pairing_requests SET status = 'expired', last_seen_at = ? "
                        "WHERE id = ? AND status = 'pending'",
                        (decided_at, request_id),
                    )
                    expired = True
                else:
                    if int(request["issue_credential_revision"]) != int(
                        bot["credential_revision"]
                    ):
                        raise ChannelConflict("pairing challenge belongs to an old credential")
                    if expected_credential_revision is not None and int(
                        bot["credential_revision"]
                    ) != int(expected_credential_revision):
                        raise ChannelConflict("bot credential revision changed during approval")
                    if expected_telegram_bot_id is not None and bot[
                        "telegram_bot_id"
                    ] != str(expected_telegram_bot_id):
                        raise ChannelConflict("telegram bot identity changed during approval")
                    if require_private and str(request["telegram_user_id"]) != str(
                        request["telegram_chat_id"]
                    ):
                        raise ChannelConflict("pairing request is not a private user chat")
                    if expected_code_digest is not None:
                        supplied_digest = bytes(expected_code_digest)
                        if len(supplied_digest) != len(bytes(request["code_digest"])) or not hmac.compare_digest(
                            supplied_digest, bytes(request["code_digest"])
                        ):
                            raise ChannelConflict("pairing code does not match")

                    live_for_session = conn.execute(
                        "SELECT * FROM channel_bindings WHERE session_id = ? "
                        "AND status IN ('active', 'paused') ORDER BY created_at DESC, id DESC LIMIT 1",
                        (caller_session_id,),
                    ).fetchone()
                    live_for_bot = conn.execute(
                        "SELECT * FROM channel_bindings WHERE bot_account_id = ? "
                        "AND status IN ('active', 'paused') ORDER BY created_at DESC, id DESC LIMIT 1",
                        (request["bot_account_id"],),
                    ).fetchone()
                    existing = live_for_session or live_for_bot
                    if live_for_session is not None and live_for_bot is not None and (
                        live_for_session["id"] != live_for_bot["id"]
                    ):
                        raise ChannelConflict("pairing approval conflicts with one-to-one ownership")
                    if existing is not None and (
                        existing["session_id"] != caller_session_id
                        or existing["bot_account_id"] != request["bot_account_id"]
                    ):
                        raise ChannelConflict("pairing approval conflicts with one-to-one ownership")

                    binding_status = BindingStatus.ACTIVE
                    pause_reason: str | None = None
                    paused_at: str | None = None
                    if existing is not None:
                        previous_reason = existing["pause_reason"]
                        # Approval repairs an identity-fault pause.  It must not
                        # silently override a user/archive pause that happened
                        # to coexist with the Token identity change.
                        if (
                            BindingStatus(existing["status"]) is BindingStatus.PAUSED
                            and previous_reason not in {
                                "bot_identity_changed",
                                "telegram_identity_changed",
                            }
                        ):
                            binding_status = BindingStatus.PAUSED
                            pause_reason = previous_reason or "pairing_reapproved_paused"
                            paused_at = existing["paused_at"] or decided_at
                        same_recipient = (
                            str(existing["telegram_user_id"])
                            == str(request["telegram_user_id"])
                            and str(existing["telegram_chat_id"])
                            == str(request["telegram_chat_id"])
                        )
                        if same_recipient:
                            binding_id = str(existing["id"])
                            # The recipient generation did not change.  Preserve
                            # cursor and queued work; only an in-flight HTTP call
                            # is ambiguous across the repair boundary.
                            conn.execute(
                                "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, "
                                "lease_until = NULL, last_error_class = 'repair_inflight', "
                                "last_error_at = ? WHERE binding_id = ? AND state = 'sending'",
                                (decided_at, binding_id),
                            )
                            conn.execute(
                                "UPDATE channel_bindings SET telegram_username = ?, "
                                "telegram_display_name = ?, status = ?, pause_reason = ?, "
                                "paused_at = ?, last_error_code = NULL, updated_at = ? "
                                "WHERE id = ?",
                                (
                                    request["telegram_username"],
                                    request["telegram_display_name"],
                                    binding_status.value,
                                    pause_reason,
                                    paused_at,
                                    decided_at,
                                    binding_id,
                                ),
                            )
                        else:
                            old_binding_id = str(existing["id"])
                            conn.execute(
                                "UPDATE outbox SET state = 'cancelled', lease_owner = NULL, "
                                "lease_until = NULL WHERE binding_id = ? "
                                "AND state IN ('pending', 'retry_wait')",
                                (old_binding_id,),
                            )
                            conn.execute(
                                "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, "
                                "lease_until = NULL, last_error_class = 'recipient_changed', "
                                "last_error_at = ? WHERE binding_id = ? AND state = 'sending'",
                                (decided_at, old_binding_id),
                            )
                            conn.execute(
                                "UPDATE channel_bindings SET status = 'disconnected', "
                                "pause_reason = NULL, disconnected_at = ?, updated_at = ? "
                                "WHERE id = ?",
                                (decided_at, decided_at, old_binding_id),
                            )
                            conn.execute(
                                "INSERT INTO channel_bindings "
                                "(id, channel, session_id, bot_account_id, telegram_user_id, "
                                "telegram_chat_id, telegram_username, telegram_display_name, "
                                "status, pause_reason, paired_at, sync_from_event_id, "
                                "projection_cursor_event_id, paused_at, created_at, updated_at) "
                                "VALUES (?, 'telegram', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    binding_id,
                                    caller_session_id,
                                    request["bot_account_id"],
                                    str(request["telegram_user_id"]),
                                    str(request["telegram_chat_id"]),
                                    request["telegram_username"],
                                    request["telegram_display_name"],
                                    binding_status.value,
                                    pause_reason,
                                    decided_at,
                                    cursor,
                                    cursor,
                                    paused_at,
                                    decided_at,
                                    decided_at,
                                ),
                            )
                    else:
                        conn.execute(
                            "INSERT INTO channel_bindings "
                            "(id, channel, session_id, bot_account_id, telegram_user_id, "
                            "telegram_chat_id, telegram_username, telegram_display_name, status, "
                            "paired_at, sync_from_event_id, projection_cursor_event_id, created_at, "
                            "updated_at) VALUES (?, 'telegram', ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                            (
                                binding_id,
                                caller_session_id,
                                request["bot_account_id"],
                                str(request["telegram_user_id"]),
                                str(request["telegram_chat_id"]),
                                request["telegram_username"],
                                request["telegram_display_name"],
                                decided_at,
                                cursor,
                                cursor,
                                decided_at,
                                decided_at,
                            ),
                        )
                    updated = conn.execute(
                        "UPDATE pairing_requests SET status = 'approved', "
                        "approved_binding_id = ?, last_seen_at = ? "
                        "WHERE id = ? AND status = 'pending'",
                        (binding_id, decided_at, request_id),
                    )
                    if updated.rowcount != 1:
                        raise ChannelConflict(
                            "pairing request decision raced with another writer"
                        )
                    conn.execute(
                        "UPDATE pairing_requests SET status = 'dismissed', last_seen_at = ? "
                        "WHERE bot_account_id = ? AND id <> ? AND status = 'pending'",
                        (decided_at, request["bot_account_id"], request_id),
                    )
                    conn.execute(
                        "UPDATE bot_accounts SET status = ?, last_error_code = NULL, updated_at = ? "
                        "WHERE id = ?",
                        (
                            BotStatus.ACTIVE.value
                            if binding_status is BindingStatus.ACTIVE
                            else BotStatus.PAUSED.value,
                            decided_at,
                            request["bot_account_id"],
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("pairing approval conflicts with a live one-to-one binding") from exc
        if expired:
            raise ChannelConflict("pairing request has expired")
        if approved_binding is not None:
            return approved_binding
        return self.binding(binding_id)

    approve_pairing = approve_pairing_request

    def dismiss_pairing_for_session(
        self, session_id: str, pairing_request_id: Any
    ) -> PairingRequest:
        """Dismiss only the current session's current Bot challenge."""
        sid = self._required_text(session_id, "session_id")
        request_id = _entity_id(pairing_request_id)
        now = self._now()
        with self._immediate() as conn:
            request = conn.execute(
                "SELECT * FROM pairing_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise ChannelNotFound("pairing request does not exist")
            bot = conn.execute(
                "SELECT * FROM bot_accounts WHERE id = ?", (request["bot_account_id"],)
            ).fetchone()
            if (
                bot is None
                or request["session_id"] != sid
                or bot["draft_session_id"] != sid
                or BotStatus(bot["status"]) is BotStatus.DISCONNECTED
            ):
                raise ChannelConflict("pairing request does not belong to current session")
            status = PairingStatus(request["status"])
            if status is PairingStatus.DISMISSED:
                return _pairing_from_row(request)
            if status is not PairingStatus.PENDING:
                raise ChannelConflict("pairing request is no longer pending")
            conn.execute(
                "UPDATE pairing_requests SET status = 'dismissed', last_seen_at = ? WHERE id = ?",
                (now, request_id),
            )
            conn.execute(
                "INSERT INTO channel_audit (ts, event_kind, session_id, bot_account_id, "
                "pairing_request_id, safe_details_json) VALUES "
                "(?, 'pairing_dismissed', ?, ?, ?, '{}')",
                (now, sid, request["bot_account_id"], request_id),
            )
        return self.pairing_request(request_id)

    def transition_pairing_request(
        self,
        pairing_request_id: Any,
        status: PairingStatus | str,
    ) -> PairingRequest:
        request_id = _entity_id(pairing_request_id)
        target = PairingStatus(_enum_value(status))
        if target is PairingStatus.APPROVED:
            raise InvalidTransition(
                "pairing approval requires explicit approve_pairing_request validation"
            )
        now = self._now()
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT status FROM pairing_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown pairing request: {request_id}")
            current = PairingStatus(row["status"])
            _check_transition("pairing", current, target, _PAIRING_TRANSITIONS)
            conn.execute(
                "UPDATE pairing_requests SET status = ?, last_seen_at = ? WHERE id = ?",
                (target.value, now, request_id),
            )
        return self.pairing_request(request_id)

    # -- Projection and outbox ---------------------------------------

    def acquire_projection_lease(
        self,
        binding_id: Any,
        *,
        lease_seconds: int = 30,
        now: datetime | str | None = None,
    ) -> str | None:
        """Acquire one oldest-first projector generation for a binding."""
        entity_id = _entity_id(binding_id)
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) \
                or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        now_dt = (
            self._clock()
            if now is None
            else datetime.fromisoformat(_aware_utc_iso(now, field="now"))
        )
        acquired_at = _aware_utc_iso(now_dt, field="now")
        lease_until = _aware_utc_iso(
            now_dt + timedelta(seconds=lease_seconds), field="lease_until"
        )
        owner = "projection_" + uuid.uuid4().hex
        with self._immediate() as conn:
            binding = conn.execute(
                "SELECT status FROM channel_bindings WHERE id = ?", (entity_id,)
            ).fetchone()
            if binding is None:
                raise ChannelNotFound(f"unknown channel binding: {entity_id}")
            if BindingStatus(binding["status"]) is not BindingStatus.ACTIVE:
                return None
            current = conn.execute(
                "SELECT lease_until FROM projection_leases WHERE binding_id = ?",
                (entity_id,),
            ).fetchone()
            if current is not None and str(current["lease_until"]) > acquired_at:
                return None
            conn.execute(
                "INSERT INTO projection_leases (binding_id, lease_owner, lease_until) "
                "VALUES (?, ?, ?) ON CONFLICT(binding_id) DO UPDATE SET "
                "lease_owner = excluded.lease_owner, lease_until = excluded.lease_until",
                (entity_id, owner, lease_until),
            )
            return owner

    def renew_projection_lease(
        self,
        binding_id: Any,
        *,
        lease_owner: str,
        lease_seconds: int = 30,
        now: datetime | str | None = None,
    ) -> bool:
        entity_id = _entity_id(binding_id)
        safe_owner = self._required_text(lease_owner, "lease_owner")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) \
                or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        now_dt = (
            self._clock()
            if now is None
            else datetime.fromisoformat(_aware_utc_iso(now, field="now"))
        )
        current_time = _aware_utc_iso(now_dt, field="now")
        lease_until = _aware_utc_iso(
            now_dt + timedelta(seconds=lease_seconds), field="lease_until"
        )
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE projection_leases SET lease_until = ? WHERE binding_id = ? "
                "AND lease_owner = ? AND lease_until > ?",
                (lease_until, entity_id, safe_owner, current_time),
            )
            return result.rowcount == 1

    def release_projection_lease(self, binding_id: Any, *, lease_owner: str) -> bool:
        entity_id = _entity_id(binding_id)
        safe_owner = self._required_text(lease_owner, "lease_owner")
        with self._immediate() as conn:
            result = conn.execute(
                "DELETE FROM projection_leases WHERE binding_id = ? AND lease_owner = ?",
                (entity_id, safe_owner),
            )
            return result.rowcount == 1

    def create_media_publish_permit(
        self,
        session_id: str,
        media_id: str,
        caption_digest: str,
        *,
        expected_binding_id: str,
        expected_paired_at: str,
        ttl_seconds: int = 600,
    ) -> str:
        """Pin one short-lived image publication to the active recipient generation."""
        safe_session_id = self._required_text(session_id, "session_id")
        if not isinstance(media_id, str) or MEDIA_ID_RE.fullmatch(media_id) is None:
            raise ValueError("media_id must be an opaque MediaStore id")
        if (
            not isinstance(caption_digest, str)
            or SHA256_HEX_RE.fullmatch(caption_digest) is None
        ):
            raise ValueError("caption_digest must be a SHA-256 hex digest")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or ttl_seconds <= 0
            or ttl_seconds > 3600
        ):
            raise ValueError("ttl_seconds must be between 1 and 3600")
        safe_binding_id = self._required_text(
            expected_binding_id, "expected_binding_id"
        )
        safe_paired_at = _aware_utc_iso(
            expected_paired_at, field="expected_paired_at"
        )
        now_dt = self._clock()
        created_at = _aware_utc_iso(now_dt, field="clock")
        expires_at = _aware_utc_iso(
            now_dt + timedelta(seconds=ttl_seconds), field="expires_at"
        )
        permit_id = "pub_" + uuid.uuid4().hex
        with self._immediate() as conn:
            conn.execute(
                "DELETE FROM media_publish_permits WHERE expires_at <= ?",
                (created_at,),
            )
            binding = conn.execute(
                "SELECT b.id, b.paired_at FROM channel_bindings b "
                "JOIN bot_accounts ba ON ba.id = b.bot_account_id "
                "WHERE b.session_id = ? AND b.status = 'active' "
                "AND b.id = ? AND b.paired_at = ? "
                "AND ba.status = 'active' AND ba.credential_id IS NOT NULL "
                "ORDER BY b.created_at DESC, b.id DESC LIMIT 1",
                (safe_session_id, safe_binding_id, safe_paired_at),
            ).fetchone()
            if binding is None:
                raise ChannelConflict("media publication requires an active binding")
            conn.execute(
                "INSERT INTO media_publish_permits "
                "(permit_id, session_id, binding_id, binding_paired_at, media_id, "
                "caption_sha256, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    permit_id,
                    safe_session_id,
                    binding["id"],
                    binding["paired_at"],
                    media_id,
                    caption_digest,
                    created_at,
                    expires_at,
                ),
            )
        return permit_id

    @staticmethod
    def _coerce_parts(parts: Iterable[OutboundPart | Mapping[str, Any]]) -> list[OutboundPart]:
        result: list[OutboundPart] = []
        seen: set[int] = set()
        for raw in parts:
            part = raw if isinstance(raw, OutboundPart) else OutboundPart(**dict(raw))
            for field, value in (("text", part.text), ("caption", part.caption)):
                if isinstance(value, str) and _TOKEN_SHAPE_RE.search(value):
                    raise ValueError(f"outbox {field} must not contain a token-like secret")
                if isinstance(value, str) and _PAIRING_CODE_SHAPE_RE.search(value):
                    raise ValueError(f"outbox {field} must not contain a pairing code")
            if isinstance(part.caption, str) and caption_contains_location(part.caption):
                raise ValueError("outbox caption must not contain a resource location")
            number = int(part.payload_part)
            if number in seen:
                raise ChannelConflict("payload_part must be unique within a projected event")
            seen.add(number)
            result.append(part)
        return sorted(result, key=lambda item: item.payload_part)

    @staticmethod
    def _media_batch_caption(parts: Sequence[OutboundPart]) -> tuple[str, str] | None:
        """Return ``(media_id, original_caption)`` for the one canonical media batch."""
        if not parts or [part.payload_part for part in parts] != list(range(len(parts))):
            return None
        media_positions = [
            index for index, part in enumerate(parts) if str(part.kind) == "media"
        ]
        if media_positions != [len(parts) - 1]:
            return None
        if any(str(part.kind) != "text" for part in parts[:-1]):
            return None
        media = parts[-1]
        if not isinstance(media.media_id, str) or MEDIA_ID_RE.fullmatch(media.media_id) is None:
            return None
        caption = "".join(str(part.text) for part in parts[:-1]) + str(
            media.caption or ""
        )
        if caption_contains_location(caption):
            return None
        return media.media_id, caption

    @staticmethod
    def _consume_media_permit(
        conn: sqlite3.Connection,
        *,
        permit_id: str | None,
        binding: sqlite3.Row,
        parts: Sequence[OutboundPart],
        now: str,
    ) -> bool:
        if (
            not isinstance(permit_id, str)
            or MEDIA_PERMIT_ID_RE.fullmatch(permit_id) is None
        ):
            return False
        batch = ChannelStore._media_batch_caption(parts)
        if batch is None:
            # A correctly shaped id still represents a one-use attempt.  Burn
            # it on a malformed batch rather than leaving confused authority.
            conn.execute(
                "DELETE FROM media_publish_permits WHERE permit_id = ?",
                (permit_id,),
            )
            return False
        media_id, caption = batch
        permit = conn.execute(
            "SELECT * FROM media_publish_permits WHERE permit_id = ?",
            (permit_id,),
        ).fetchone()
        if permit is None:
            return False
        # Delete before evaluating the match, inside the surrounding immediate
        # transaction.  A valid match commits together with Outbox rows; an
        # exception rolls both changes back.  A wrong attempt burns authority.
        conn.execute(
            "DELETE FROM media_publish_permits WHERE permit_id = ?",
            (permit_id,),
        )
        actual_digest = hashlib.sha256(caption.encode("utf-8")).hexdigest()
        return (
            str(permit["expires_at"]) > now
            and hmac.compare_digest(str(permit["session_id"]), str(binding["session_id"]))
            and hmac.compare_digest(str(permit["binding_id"]), str(binding["id"]))
            and hmac.compare_digest(
                str(permit["binding_paired_at"]), str(binding["paired_at"])
            )
            and hmac.compare_digest(str(permit["media_id"]), media_id)
            and hmac.compare_digest(str(permit["caption_sha256"]), actual_digest)
        )

    def project_event(
        self,
        binding_id: Any,
        *,
        source_event_id: int,
        parts: Iterable[OutboundPart | Mapping[str, Any]],
        projection_owner: str,
        media_permit_id: str | None = None,
    ) -> list[OutboxItem]:
        entity_id = _entity_id(binding_id)
        event_id = int(source_event_id)
        if event_id < 0:
            raise ValueError("source_event_id must be non-negative")
        projected_parts = self._coerce_parts(parts)
        now = self._now()
        safe_projection_owner = self._required_text(
            projection_owner, "projection_owner"
        )
        batch_id = "batch_" + uuid.uuid5(
            uuid.NAMESPACE_URL, f"guanlan-channel:{entity_id}:{event_id}"
        ).hex
        with self._immediate() as conn:
            binding = conn.execute(
                "SELECT b.id, b.session_id, b.bot_account_id, b.telegram_chat_id, b.paired_at, "
                "b.sync_from_event_id, b.projection_cursor_event_id, b.status, "
                "pl.lease_owner AS projection_lease_owner, "
                "pl.lease_until AS projection_lease_until "
                "FROM channel_bindings b JOIN bot_accounts ba ON ba.id = b.bot_account_id "
                "LEFT JOIN projection_leases pl ON pl.binding_id = b.id "
                "WHERE b.id = ?",
                (entity_id,),
            ).fetchone()
            if binding is None:
                raise ChannelNotFound(f"unknown channel binding: {entity_id}")
            if BindingStatus(binding["status"]) is not BindingStatus.ACTIVE:
                raise ChannelConflict("only an active binding may project events")
            live_projection_lease = (
                binding["projection_lease_owner"] is not None
                and binding["projection_lease_until"] is not None
                and str(binding["projection_lease_until"]) > now
            )
            if (
                not live_projection_lease
                or binding["projection_lease_owner"] != safe_projection_owner
            ):
                raise ChannelConflict("projection lease is not current")
            cursor = int(binding["projection_cursor_event_id"])
            boundary = max(cursor, int(binding["sync_from_event_id"]))
            if event_id < boundary:
                raise ChannelConflict("source event is behind the projection boundary")
            if event_id > boundary:
                parts_to_insert = projected_parts
                if any(str(part.kind) == "media" for part in projected_parts):
                    permitted = self._consume_media_permit(
                        conn,
                        permit_id=media_permit_id,
                        binding=binding,
                        parts=projected_parts,
                        now=now,
                    )
                    parts_to_insert = projected_parts if permitted else []
                next_sequence = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM outbox WHERE binding_id = ?",
                        (entity_id,),
                    ).fetchone()[0]
                )
                for part in parts_to_insert:
                    outbox_id = _new_id("out")
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO outbox "
                        "(id, binding_id, source_session_id, source_event_id, payload_part, "
                        "batch_id, sequence, kind, text, media_id, caption, "
                        "media_authorization_version, state, attempts, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)",
                        (
                            outbox_id,
                            entity_id,
                            binding["session_id"],
                            event_id,
                            int(part.payload_part),
                            batch_id,
                            next_sequence,
                            _enum_value(part.kind),
                            part.text,
                            part.media_id,
                            part.caption,
                            1 if str(part.kind) == "media" else None,
                            now,
                        ),
                    )
                    if cur.rowcount == 1:
                        conn.execute(
                            "INSERT INTO outbox_route_fences "
                            "(outbox_id, bot_account_id, telegram_chat_id, binding_paired_at) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                outbox_id,
                                binding["bot_account_id"],
                                binding["telegram_chat_id"],
                                binding["paired_at"],
                            ),
                        )
                        next_sequence += 1
                conn.execute(
                    "UPDATE channel_bindings SET projection_cursor_event_id = ?, "
                    "updated_at = ? WHERE id = ?",
                    (event_id, now, entity_id),
                )
            rows = conn.execute(
                "SELECT * FROM outbox WHERE binding_id = ? AND source_event_id = ? "
                "ORDER BY payload_part",
                (entity_id, event_id),
            ).fetchall()
            return [_outbox_from_row(row) for row in rows]

    def project_event_for_test(
        self,
        binding_id: Any,
        *,
        source_event_id: int,
        parts: Iterable[OutboundPart | Mapping[str, Any]],
        media_permit_id: str | None = None,
    ) -> list[OutboxItem]:
        """Explicit test-fixture helper; production projection requires a lease."""
        owner = self.acquire_projection_lease(binding_id)
        if owner is None:
            raise ChannelConflict("test projection could not acquire binding lease")
        try:
            return self.project_event(
                binding_id,
                source_event_id=source_event_id,
                parts=parts,
                projection_owner=owner,
                media_permit_id=media_permit_id,
            )
        finally:
            self.release_projection_lease(binding_id, lease_owner=owner)

    def outbox(self, outbox_id: Any) -> OutboxItem:
        entity_id = _entity_id(outbox_id)
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM outbox WHERE id = ?", (entity_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ChannelNotFound(f"unknown outbox item: {entity_id}")
        return _outbox_from_row(row)

    def outbox_for(self, binding_id: Any) -> list[OutboxItem]:
        entity_id = _entity_id(binding_id)
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE binding_id = ? ORDER BY sequence", (entity_id,)
            ).fetchall()
        finally:
            conn.close()
        return [_outbox_from_row(row) for row in rows]

    def outbox_for_session(self, session_id: Any) -> list[OutboxItem]:
        """Current deliveries plus unresolved items on historic bindings."""
        sid = self._required_text(session_id, "session_id")
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT o.* FROM outbox o JOIN channel_bindings b ON b.id = o.binding_id "
                "WHERE b.session_id = ? AND (b.status IN ('active', 'paused') "
                "OR o.state IN ('pending', 'sending', 'retry_wait', 'uncertain', 'terminal')) "
                "ORDER BY o.created_at, o.id",
                (sid,),
            ).fetchall()
        finally:
            conn.close()
        return [_outbox_from_row(row) for row in rows]

    def transition_outbox_for_test(
        self,
        outbox_id: Any,
        state: OutboxState | str,
        *,
        lease_owner: Optional[str] = None,
        lease_until: datetime | str | None = None,
        next_attempt_at: datetime | str | None = None,
        telegram_message_id: Optional[str | int] = None,
        error_class: Optional[str] = None,
        now: datetime | str | None = None,
    ) -> OutboxItem:
        """Fixture-only state builder; runtime delivery must use fenced claims."""
        entity_id = _entity_id(outbox_id)
        target = OutboxState(_enum_value(state))
        safe_owner = self._optional_channel_text(lease_owner, "lease_owner")
        safe_error = self._optional_safe_error(error_class, "error_class")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        lease_time = _optional_time(lease_until, field="lease_until")
        retry_time = _optional_time(next_attempt_at, field="next_attempt_at")
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT state, attempts FROM outbox WHERE id = ?", (entity_id,)
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown outbox item: {entity_id}")
            current = OutboxState(row["state"])
            _check_transition("outbox", current, target, _OUTBOX_TRANSITIONS)
            attempts = int(row["attempts"])
            if target is OutboxState.SENDING and current is not target:
                attempts += 1
            sent_at = changed_at if target is OutboxState.SENT else None
            last_error_at = changed_at if safe_error else None
            conn.execute(
                "UPDATE outbox SET state = ?, attempts = ?, next_attempt_at = ?, "
                "lease_owner = ?, lease_until = ?, telegram_message_id = COALESCE(?, "
                "telegram_message_id), last_error_class = ?, last_error_at = ?, "
                "sent_at = COALESCE(?, sent_at) WHERE id = ?",
                (
                    target.value,
                    attempts,
                    retry_time if target is OutboxState.RETRY_WAIT else None,
                    safe_owner if target is OutboxState.SENDING else None,
                    lease_time if target is OutboxState.SENDING else None,
                    None if telegram_message_id is None else str(telegram_message_id),
                    safe_error,
                    last_error_at,
                    sent_at,
                    entity_id,
                ),
            )
        return self.outbox(entity_id)

    def resolve_outbox(self, outbox_id: Any, action: str) -> OutboxItem:
        entity_id = _entity_id(outbox_id)
        resolutions: dict[tuple[OutboxState, str], OutboxState] = {
            (OutboxState.UNCERTAIN, "assume_sent"): OutboxState.SENT,
            (OutboxState.UNCERTAIN, "resend"): OutboxState.PENDING,
            (OutboxState.UNCERTAIN, "drop"): OutboxState.CANCELLED,
            (OutboxState.TERMINAL, "retry"): OutboxState.PENDING,
            (OutboxState.TERMINAL, "drop"): OutboxState.CANCELLED,
        }
        now = self._now()
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT o.state, b.status AS binding_status, "
                "CASE WHEN rf.bot_account_id = b.bot_account_id "
                "AND rf.telegram_chat_id = b.telegram_chat_id "
                "AND rf.binding_paired_at = b.paired_at THEN 1 ELSE 0 END AS route_current "
                "FROM outbox o JOIN channel_bindings b ON b.id = o.binding_id "
                "LEFT JOIN outbox_route_fences rf ON rf.outbox_id = o.id WHERE o.id = ?",
                (entity_id,),
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown outbox item: {entity_id}")
            current = OutboxState(row["state"])
            target = resolutions.get((current, action))
            if target is None:
                raise InvalidTransition(
                    f"invalid outbox resolution: {current.value} / {action}"
                )
            if action in {"resend", "retry"} and (
                BindingStatus(row["binding_status"]) is BindingStatus.DISCONNECTED
                or int(row["route_current"]) != 1
            ):
                raise ChannelConflict("delivery recipient generation is no longer current")
            conn.execute(
                "UPDATE outbox SET state = ?, next_attempt_at = NULL, lease_owner = NULL, "
                "lease_until = NULL, last_error_class = NULL, last_error_at = NULL, "
                "sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END WHERE id = ?",
                (target.value, target.value, now, entity_id),
            )
        return self.outbox(entity_id)

    def claim_next_outbox(
        self,
        binding_id: Any,
        *,
        owner: str | None = None,
        lease_until: datetime | str,
        now: datetime | str | None = None,
    ) -> Optional[OutboxClaim]:
        """Fence and return one binding head with its routing generation.

        ``owner`` is accepted as a diagnostic prefix for compatibility, but is
        never itself the lease token.  Every successful claim receives a fresh
        UUID so a delayed worker can never complete a later lease that happened
        to use the same process-wide worker name.
        """
        entity_id = _entity_id(binding_id)
        claimed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        lease_time = _aware_utc_iso(lease_until, field="lease_until")
        if lease_time <= claimed_at:
            raise ValueError("lease_until must be later than now")
        if owner is not None:
            self._required_text(owner, "owner")
        claim_owner = "claim_" + uuid.uuid4().hex
        claimed_id: Optional[str] = None
        claimed_row: sqlite3.Row | None = None
        with self._immediate() as conn:
            conn.execute(
                "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, lease_until = NULL, "
                "last_error_class = 'lease_expired', last_error_at = ? "
                "WHERE state = 'sending' AND (lease_until IS NULL OR lease_until <= ?)",
                (claimed_at, claimed_at),
            )
            head = conn.execute(
                "SELECT o.*, CASE WHEN rf.bot_account_id = b.bot_account_id "
                "AND rf.telegram_chat_id = b.telegram_chat_id "
                "AND rf.binding_paired_at = b.paired_at THEN 1 ELSE 0 END AS route_current "
                "FROM outbox o "
                "JOIN channel_bindings b ON b.id = o.binding_id "
                "JOIN bot_accounts ba ON ba.id = b.bot_account_id "
                "LEFT JOIN outbox_route_fences rf ON rf.outbox_id = o.id "
                "WHERE o.binding_id = ? AND b.status = 'active' "
                "AND ba.status = 'active' AND ba.credential_id IS NOT NULL "
                "AND o.state NOT IN ('sent', 'cancelled') ORDER BY o.sequence LIMIT 1",
                (entity_id,),
            ).fetchone()
            if head is None:
                return None
            if int(head["route_current"]) != 1:
                return None
            state = OutboxState(head["state"])
            due = head["next_attempt_at"] is None or head["next_attempt_at"] <= claimed_at
            if state not in {OutboxState.PENDING, OutboxState.RETRY_WAIT} or not due:
                return None
            claimed_id = head["id"]
            updated = conn.execute(
                "UPDATE outbox SET state = 'sending', attempts = attempts + 1, "
                "next_attempt_at = NULL, lease_owner = ?, lease_until = ? WHERE id = ?",
                (claim_owner, lease_time, claimed_id),
            )
            if updated.rowcount != 1:
                return None
            claimed_row = conn.execute(
                "SELECT o.*, b.bot_account_id AS claim_bot_account_id, "
                "b.telegram_chat_id AS claim_telegram_chat_id, "
                "ba.credential_id AS claim_credential_id, "
                "ba.credential_revision AS claim_credential_revision "
                "FROM outbox o JOIN channel_bindings b ON b.id = o.binding_id "
                "JOIN bot_accounts ba ON ba.id = b.bot_account_id "
                "JOIN outbox_route_fences rf ON rf.outbox_id = o.id "
                "WHERE o.id = ? AND o.state = 'sending' AND o.lease_owner = ? "
                "AND b.status = 'active' AND ba.status = 'active' "
                "AND ba.credential_id IS NOT NULL "
                "AND rf.bot_account_id = b.bot_account_id "
                "AND rf.telegram_chat_id = b.telegram_chat_id "
                "AND rf.binding_paired_at = b.paired_at",
                (claimed_id, claim_owner),
            ).fetchone()
            if claimed_row is None:
                raise ChannelConflict("delivery routing changed during claim")
        return _outbox_claim_from_row(claimed_row)

    def delivery_bindings(self) -> list[ChannelBinding]:
        """Return only bindings whose current Bot generation may be claimed."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT b.* FROM channel_bindings b "
                "JOIN bot_accounts ba ON ba.id = b.bot_account_id "
                "WHERE b.status = 'active' AND ba.status = 'active' "
                "AND ba.credential_id IS NOT NULL ORDER BY b.created_at, b.id"
            ).fetchall()
        finally:
            conn.close()
        return [_binding_from_row(row) for row in rows]

    def recover_sending_outbox(
        self,
        *,
        error_class: str = "startup_recovery",
        now: datetime | str | None = None,
    ) -> int:
        """Make every pre-existing in-flight send explicitly uncertain.

        Startup cannot know whether Telegram accepted any of these requests.
        This intentionally covers paused and disconnected bindings too.
        """
        safe_error = self._optional_safe_error(error_class, "error_class")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, "
                "lease_until = NULL, last_error_class = ?, last_error_at = ? "
                "WHERE state = 'sending'",
                (safe_error, changed_at),
            )
            return int(result.rowcount)

    def renew_outbox_claim(
        self,
        outbox_id: Any,
        *,
        lease_owner: str,
        lease_until: datetime | str,
        bot_account_id: str,
        telegram_chat_id: str,
        credential_id: str,
        credential_revision: int,
        now: datetime | str | None = None,
    ) -> bool:
        entity_id = _entity_id(outbox_id)
        safe_owner = self._required_text(lease_owner, "lease_owner")
        lease_time = _aware_utc_iso(lease_until, field="lease_until")
        current_time = self._now() if now is None else _aware_utc_iso(now, field="now")
        if lease_time <= current_time:
            raise ValueError("lease_until must be later than now")
        expected_bot = _entity_id(bot_account_id)
        expected_chat = self._required_text(telegram_chat_id, "telegram_chat_id")
        expected_credential = self._required_text(credential_id, "credential_id")
        revision = self._poll_revision(credential_revision)
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE outbox SET lease_until = ? WHERE id = ? AND state = 'sending' "
                "AND lease_owner = ? AND lease_until > ? AND EXISTS ("
                "SELECT 1 FROM channel_bindings b "
                "JOIN bot_accounts ba ON ba.id = b.bot_account_id "
                "JOIN outbox_route_fences rf ON rf.outbox_id = outbox.id "
                "WHERE b.id = outbox.binding_id AND b.status = 'active' "
                "AND ba.status = 'active' AND b.bot_account_id = ? "
                "AND b.telegram_chat_id = ? AND ba.credential_id = ? "
                "AND ba.credential_revision = ? AND rf.bot_account_id = b.bot_account_id "
                "AND rf.telegram_chat_id = b.telegram_chat_id "
                "AND rf.binding_paired_at = b.paired_at)",
                (
                    lease_time,
                    entity_id,
                    safe_owner,
                    current_time,
                    expected_bot,
                    expected_chat,
                    expected_credential,
                    revision,
                ),
            )
            return result.rowcount == 1

    def outbox_claim_is_current(
        self,
        claim: OutboxClaim,
        *,
        now: datetime | str | None = None,
    ) -> bool:
        current_time = self._now() if now is None else _aware_utc_iso(now, field="now")
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM outbox o JOIN channel_bindings b ON b.id = o.binding_id "
                "JOIN bot_accounts ba ON ba.id = b.bot_account_id "
                "JOIN outbox_route_fences rf ON rf.outbox_id = o.id "
                "WHERE o.id = ? AND o.state = 'sending' AND o.lease_owner = ? "
                "AND o.lease_until > ? AND b.status = 'active' AND ba.status = 'active' "
                "AND b.bot_account_id = ? AND b.telegram_chat_id = ? "
                "AND ba.credential_id = ? AND ba.credential_revision = ? "
                "AND rf.bot_account_id = b.bot_account_id "
                "AND rf.telegram_chat_id = b.telegram_chat_id "
                "AND rf.binding_paired_at = b.paired_at",
                (
                    claim.id,
                    claim.lease_owner,
                    current_time,
                    claim.bot_account_id,
                    claim.telegram_chat_id,
                    claim.credential_id,
                    int(claim.credential_revision),
                ),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def finish_outbox_claim(
        self,
        outbox_id: Any,
        *,
        lease_owner: str,
        state: OutboxState | str,
        next_attempt_at: datetime | str | None = None,
        telegram_message_id: str | int | None = None,
        error_class: str | None = None,
        bot_account_id: str,
        telegram_chat_id: str,
        credential_id: str,
        credential_revision: int,
        now: datetime | str | None = None,
    ) -> OutboxItem | None:
        """Complete exactly the lease identified by ``lease_owner``.

        A stale worker receives ``None`` and cannot mutate a re-leased item.
        """
        entity_id = _entity_id(outbox_id)
        safe_owner = self._required_text(lease_owner, "lease_owner")
        expected_bot = _entity_id(bot_account_id)
        expected_chat = self._required_text(telegram_chat_id, "telegram_chat_id")
        expected_credential = self._required_text(credential_id, "credential_id")
        revision = self._poll_revision(credential_revision)
        target = OutboxState(_enum_value(state))
        if target not in {
            OutboxState.SENT,
            OutboxState.RETRY_WAIT,
            OutboxState.UNCERTAIN,
            OutboxState.TERMINAL,
        }:
            raise ValueError("unsupported claimed outbox completion")
        retry_time = _optional_time(next_attempt_at, field="next_attempt_at")
        if target is OutboxState.RETRY_WAIT and retry_time is None:
            raise ValueError("retry_wait requires next_attempt_at")
        if target is not OutboxState.RETRY_WAIT and retry_time is not None:
            raise ValueError("next_attempt_at is only valid for retry_wait")
        safe_error = self._optional_safe_error(error_class, "error_class")
        if target is not OutboxState.SENT and safe_error is None:
            raise ValueError("failed delivery completion requires error_class")
        message_id = None if telegram_message_id is None else str(telegram_message_id)
        if target is OutboxState.SENT:
            if not message_id or not message_id.isdigit():
                raise ValueError("sent delivery requires a Telegram message id")
        elif message_id is not None:
            raise ValueError("Telegram message id is only valid for sent delivery")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE outbox SET state = ?, next_attempt_at = ?, lease_owner = NULL, "
                "lease_until = NULL, telegram_message_id = ?, last_error_class = ?, "
                "last_error_at = ?, sent_at = ? WHERE id = ? AND state = 'sending' "
                "AND lease_owner = ? AND lease_until > ? AND EXISTS ("
                "SELECT 1 FROM channel_bindings b "
                "JOIN bot_accounts ba ON ba.id = b.bot_account_id "
                "JOIN outbox_route_fences rf ON rf.outbox_id = outbox.id "
                "WHERE b.id = outbox.binding_id AND b.status = 'active' "
                "AND ba.status = 'active' AND b.bot_account_id = ? "
                "AND b.telegram_chat_id = ? AND ba.credential_id = ? "
                "AND ba.credential_revision = ? AND rf.bot_account_id = b.bot_account_id "
                "AND rf.telegram_chat_id = b.telegram_chat_id "
                "AND rf.binding_paired_at = b.paired_at)",
                (
                    target.value,
                    retry_time,
                    message_id,
                    safe_error,
                    None if target is OutboxState.SENT else changed_at,
                    changed_at if target is OutboxState.SENT else None,
                    entity_id,
                    safe_owner,
                    changed_at,
                    expected_bot,
                    expected_chat,
                    expected_credential,
                    revision,
                ),
            )
            if result.rowcount != 1:
                return None
            if target is OutboxState.SENT:
                conn.execute(
                    "UPDATE channel_bindings SET last_sent_at = ?, last_error_code = NULL, "
                    "updated_at = ? WHERE id = (SELECT binding_id FROM outbox WHERE id = ?)",
                    (changed_at, changed_at, entity_id),
                )
            elif safe_error is not None:
                conn.execute(
                    "UPDATE channel_bindings SET last_error_code = ?, updated_at = ? "
                    "WHERE id = (SELECT binding_id FROM outbox WHERE id = ?)",
                    (safe_error, changed_at, entity_id),
                )
        return self.outbox(entity_id)

    def mark_outbox_claim_uncertain(
        self,
        outbox_id: Any,
        *,
        lease_owner: str,
        error_class: str,
        now: datetime | str | None = None,
    ) -> OutboxItem | None:
        """Owner-only safe fallback when routing generation or time changed."""
        entity_id = _entity_id(outbox_id)
        safe_owner = self._required_text(lease_owner, "lease_owner")
        safe_error = self._optional_safe_error(error_class, "error_class")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, "
                "lease_until = NULL, next_attempt_at = NULL, last_error_class = ?, "
                "last_error_at = ? WHERE id = ? AND state = 'sending' AND lease_owner = ?",
                (safe_error, changed_at, entity_id, safe_owner),
            )
            if result.rowcount != 1:
                return None
        return self.outbox(entity_id)

    # -- Automations --------------------------------------------------

    def create_automation(
        self,
        *,
        name: str,
        session_id: str,
        schedule_kind: str,
        prompt_text: str,
        timezone_name: Optional[str] = None,
        timezone: Optional[str] = None,
        local_time: Optional[str] = None,
        run_at: datetime | str | None = None,
        skill_refs: Sequence[str] = (),
        media_policy: str = "none",
        enabled: bool = True,
        pause_reason: Optional[str] = None,
        next_run_at: datetime | str | None = None,
        last_run_at: datetime | str | None = None,
        last_status: Optional[str] = None,
        misfire_grace_seconds: int = 21600,
        created_by: Optional[str] = None,
        confirmed_at: datetime | str | None = None,
    ) -> Automation:
        zone = timezone_name or timezone or "Asia/Hong_Kong"
        automation_id = _new_automation_id("auto")
        now = self._now()
        confirmed = now if confirmed_at is None else _aware_utc_iso(
            confirmed_at, field="confirmed_at"
        )
        run_time = _optional_time(run_at, field="run_at")
        next_time = _optional_time(next_run_at, field="next_run_at")
        last_time = _optional_time(last_run_at, field="last_run_at")
        refs = tuple(self._required_channel_text(ref, "skill_ref") for ref in skill_refs)
        safe_local_time = self._optional_channel_text(local_time, "local_time")
        safe_pause_reason = self._optional_safe_error(pause_reason, "pause_reason")
        safe_last_status = self._optional_safe_error(last_status, "last_status")
        safe_created_by = self._optional_channel_text(created_by, "created_by")
        try:
            with self._immediate() as conn:
                conn.execute(
                    "INSERT INTO automations "
                    "(id, name, session_id, schedule_kind, local_time, run_at, timezone, "
                    "prompt_text, skill_refs_json, media_policy, enabled, pause_reason, "
                    "next_run_at, last_run_at, last_status, misfire_grace_seconds, created_by, "
                    "confirmed_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        automation_id,
                        self._required_channel_text(name, "name"),
                        self._required_text(session_id, "session_id"),
                        _enum_value(schedule_kind),
                        safe_local_time,
                        run_time,
                        self._required_text(zone, "timezone"),
                        self._required_prompt_text(prompt_text),
                        json.dumps(refs, ensure_ascii=False, separators=(",", ":")),
                        _enum_value(media_policy),
                        int(bool(enabled)),
                        safe_pause_reason,
                        next_time,
                        last_time,
                        safe_last_status,
                        int(misfire_grace_seconds),
                        safe_created_by,
                        confirmed,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("automation violates the durable schedule contract") from exc
        return self.automation(automation_id)

    def automation(self, automation_id: Any) -> Automation:
        entity_id = _entity_id(automation_id)
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM automations WHERE id = ?", (entity_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ChannelNotFound(f"unknown automation: {entity_id}")
        return _automation_from_row(row)

    def automation_for_session(
        self, automation_id: Any, session_id: str
    ) -> Automation:
        """Read one schedule only when it belongs to the expected session."""
        entity_id = _entity_id(automation_id)
        sid = self._required_text(session_id, "session_id")
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM automations WHERE id = ? AND session_id = ?",
                (entity_id, sid),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            # Do not disclose whether an ID exists in a different conversation.
            raise ChannelNotFound("unknown automation for session")
        return _automation_from_row(row)

    def automations_for_session(self, session_id: str) -> list[Automation]:
        sid = self._required_text(session_id, "session_id")
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM automations WHERE session_id = ? "
                "ORDER BY created_at, id",
                (sid,),
            ).fetchall()
        finally:
            conn.close()
        return [_automation_from_row(row) for row in rows]

    def update_automation(
        self,
        automation_id: Any,
        *,
        expected_session_id: str,
        name: str,
        session_id: str,
        schedule_kind: str,
        prompt_text: str,
        timezone_name: Optional[str] = None,
        timezone: Optional[str] = None,
        local_time: Optional[str] = None,
        run_at: datetime | str | None = None,
        skill_refs: Sequence[str] = (),
        media_policy: str = "none",
        enabled: bool = True,
        pause_reason: Optional[str] = None,
        next_run_at: datetime | str | None = None,
        last_run_at: datetime | str | None = None,
        last_status: Optional[str] = None,
        misfire_grace_seconds: int = 21600,
        created_by: Optional[str] = None,
        confirmed_at: datetime | str | None = None,
    ) -> Automation:
        """Replace a confirmed definition without allowing a session move."""
        entity_id = _entity_id(automation_id)
        expected_sid = self._required_text(expected_session_id, "expected_session_id")
        supplied_sid = self._required_text(session_id, "session_id")
        if supplied_sid != expected_sid:
            raise ChannelConflict("automation cannot move between sessions")
        zone = timezone_name or timezone or "Asia/Hong_Kong"
        now = self._now()
        confirmed = now if confirmed_at is None else _aware_utc_iso(
            confirmed_at, field="confirmed_at"
        )
        run_time = _optional_time(run_at, field="run_at")
        next_time = _optional_time(next_run_at, field="next_run_at")
        last_time = _optional_time(last_run_at, field="last_run_at")
        refs = tuple(self._required_channel_text(ref, "skill_ref") for ref in skill_refs)
        safe_local_time = self._optional_channel_text(local_time, "local_time")
        safe_pause_reason = self._optional_safe_error(pause_reason, "pause_reason")
        safe_last_status = self._optional_safe_error(last_status, "last_status")
        safe_created_by = self._optional_channel_text(created_by, "created_by")
        if isinstance(misfire_grace_seconds, bool) or int(misfire_grace_seconds) < 0:
            raise ValueError("misfire_grace_seconds must be non-negative")
        try:
            with self._immediate() as conn:
                current = conn.execute(
                    "SELECT 1 FROM automations WHERE id = ? AND session_id = ?",
                    (entity_id, expected_sid),
                ).fetchone()
                if current is None:
                    raise ChannelNotFound("unknown automation for session")
                # A newly confirmed definition supersedes queued occurrences of
                # the previous definition.  Running/terminal evidence remains.
                conn.execute(
                    "UPDATE automation_runs SET status = 'cancelled', lease_owner = NULL, "
                    "lease_until = NULL, ended_at = ?, error_class = 'definition_replaced' "
                    "WHERE automation_id = ? AND status = 'queued'",
                    (now, entity_id),
                )
                conn.execute(
                    "UPDATE automations SET name = ?, schedule_kind = ?, local_time = ?, "
                    "run_at = ?, timezone = ?, prompt_text = ?, skill_refs_json = ?, "
                    "media_policy = ?, enabled = ?, pause_reason = ?, next_run_at = ?, "
                    "last_run_at = ?, last_status = ?, misfire_grace_seconds = ?, "
                    "created_by = ?, confirmed_at = ?, updated_at = ? "
                    "WHERE id = ? AND session_id = ?",
                    (
                        self._required_channel_text(name, "name"),
                        _enum_value(schedule_kind),
                        safe_local_time,
                        run_time,
                        self._required_text(zone, "timezone"),
                        self._required_prompt_text(prompt_text),
                        json.dumps(refs, ensure_ascii=False, separators=(",", ":")),
                        _enum_value(media_policy),
                        int(bool(enabled)),
                        safe_pause_reason,
                        next_time,
                        last_time,
                        safe_last_status,
                        int(misfire_grace_seconds),
                        safe_created_by,
                        confirmed,
                        now,
                        entity_id,
                        expected_sid,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("automation violates the durable schedule contract") from exc
        return self.automation_for_session(entity_id, expected_sid)

    upsert_automation = update_automation

    def set_automation_enabled(
        self,
        automation_id: Any,
        *,
        expected_session_id: str,
        enabled: bool,
        pause_reason: Optional[str] = None,
        last_status: Optional[str] | object = _UNSET,
        next_run_at: datetime | str | None | object = _UNSET,
        now: datetime | str | None = None,
    ) -> Automation:
        entity_id = _entity_id(automation_id)
        sid = self._required_text(expected_session_id, "expected_session_id")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        safe_pause = self._optional_safe_error(pause_reason, "pause_reason")
        status_value = (
            _UNSET
            if last_status is _UNSET
            else self._optional_safe_error(last_status, "last_status")
        )
        next_value = (
            _UNSET
            if next_run_at is _UNSET
            else _optional_time(next_run_at, field="next_run_at")
        )
        assignments = ["enabled = ?", "pause_reason = ?", "updated_at = ?"]
        params: list[Any] = [int(bool(enabled)), safe_pause, changed_at]
        if status_value is not _UNSET:
            assignments.append("last_status = ?")
            params.append(status_value)
        if next_value is not _UNSET:
            assignments.append("next_run_at = ?")
            params.append(next_value)
        params.extend([entity_id, sid])
        with self._immediate() as conn:
            result = conn.execute(
                f"UPDATE automations SET {', '.join(assignments)} "
                "WHERE id = ? AND session_id = ?",
                tuple(params),
            )
            if result.rowcount != 1:
                raise ChannelNotFound("unknown automation for session")
            if not enabled:
                conn.execute(
                    "UPDATE automation_runs SET status = 'cancelled', lease_owner = NULL, "
                    "lease_until = NULL, ended_at = ?, error_class = 'automation_paused' "
                    "WHERE automation_id = ? AND status = 'queued'",
                    (changed_at, entity_id),
                )
        return self.automation_for_session(entity_id, sid)

    def delete_automation(
        self, automation_id: Any, *, expected_session_id: str
    ) -> bool:
        entity_id = _entity_id(automation_id)
        sid = self._required_text(expected_session_id, "expected_session_id")
        now = self._now()
        with self._immediate() as conn:
            parent = conn.execute(
                "SELECT 1 FROM automations WHERE id = ? AND session_id = ?",
                (entity_id, sid),
            ).fetchone()
            if parent is None:
                raise ChannelNotFound("unknown automation for session")
            running = conn.execute(
                "SELECT 1 FROM automation_runs WHERE automation_id = ? "
                "AND status = 'running' LIMIT 1",
                (entity_id,),
            ).fetchone()
            if running is not None:
                raise ChannelConflict("running automation cannot be deleted")
            conn.execute(
                "UPDATE automation_runs SET status = 'cancelled', lease_owner = NULL, "
                "lease_until = NULL, ended_at = ?, error_class = 'automation_deleted' "
                "WHERE automation_id = ? AND status = 'queued'",
                (now, entity_id),
            )
            conn.execute("DELETE FROM automation_runs WHERE automation_id = ?", (entity_id,))
            result = conn.execute(
                "DELETE FROM automations WHERE id = ? AND session_id = ?",
                (entity_id, sid),
            )
            return result.rowcount == 1

    def due_automations(
        self,
        now: datetime | str | None = None,
        *,
        limit: int = 256,
    ) -> list[Automation]:
        current = self._now() if now is None else _aware_utc_iso(now, field="now")
        if isinstance(limit, bool) or not 1 <= int(limit) <= 10000:
            raise ValueError("limit is out of range")
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM automations WHERE enabled = 1 AND next_run_at IS NOT NULL "
                "AND next_run_at <= ? ORDER BY next_run_at, id LIMIT ?",
                (current, int(limit)),
            ).fetchall()
        finally:
            conn.close()
        return [_automation_from_row(row) for row in rows]

    def advance_automation_schedule(
        self,
        automation_id: Any,
        *,
        expected_next_run_at: datetime | str,
        next_run_at: datetime | str | None,
        last_run_at: datetime | str | None = None,
        last_status: Optional[str] = None,
        disable: bool = False,
        now: datetime | str | None = None,
    ) -> Automation:
        """Optimistically advance exactly the due definition one worker read."""
        entity_id = _entity_id(automation_id)
        expected = _aware_utc_iso(expected_next_run_at, field="expected_next_run_at")
        following = _optional_time(next_run_at, field="next_run_at")
        last_time = _optional_time(last_run_at, field="last_run_at")
        safe_status = self._optional_safe_error(last_status, "last_status")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE automations SET next_run_at = ?, "
                "last_run_at = COALESCE(?, last_run_at), "
                "last_status = COALESCE(?, last_status), "
                "enabled = CASE WHEN ? THEN 0 ELSE enabled END, "
                "pause_reason = CASE WHEN ? THEN COALESCE(?, 'completed') ELSE pause_reason END, "
                "updated_at = ? WHERE id = ? AND enabled = 1 AND next_run_at = ?",
                (
                    following,
                    last_time,
                    safe_status,
                    int(bool(disable)),
                    int(bool(disable)),
                    safe_status,
                    changed_at,
                    entity_id,
                    expected,
                ),
            )
            if result.rowcount != 1:
                raise ChannelConflict("automation schedule was advanced concurrently")
        return self.automation(entity_id)

    def create_automation_run(
        self,
        automation_id: Any,
        scheduled_for: datetime | str,
    ) -> AutomationRun:
        parent_id = _entity_id(automation_id)
        occurrence = _aware_utc_iso(scheduled_for, field="scheduled_for")
        run_id = _new_automation_id("arun")
        with self._immediate() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO automation_runs "
                "(id, automation_id, scheduled_for, status) VALUES (?, ?, ?, 'queued')",
                (run_id, parent_id, occurrence),
            )
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_id = ? AND scheduled_for = ?",
                (parent_id, occurrence),
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown automation: {parent_id}")
            return _automation_run_from_row(row)

    enqueue_automation_run = create_automation_run

    def automation_run(self, automation_run_id: Any) -> AutomationRun:
        entity_id = _entity_id(automation_run_id)
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE id = ?", (entity_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ChannelNotFound(f"unknown automation run: {entity_id}")
        return _automation_run_from_row(row)

    def automation_runs_for(self, automation_id: Any) -> list[AutomationRun]:
        parent_id = _entity_id(automation_id)
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_id = ? "
                "ORDER BY scheduled_for, id",
                (parent_id,),
            ).fetchall()
        finally:
            conn.close()
        return [_automation_run_from_row(row) for row in rows]

    def automation_runs_for_session(
        self,
        session_id: str,
        *,
        statuses: Sequence[AutomationRunStatus | str] | None = None,
    ) -> list[AutomationRun]:
        sid = self._required_text(session_id, "session_id")
        values: tuple[str, ...] = ()
        sql = (
            "SELECT r.* FROM automation_runs r JOIN automations a "
            "ON a.id = r.automation_id WHERE a.session_id = ?"
        )
        params: list[Any] = [sid]
        if statuses is not None:
            values = tuple(AutomationRunStatus(_enum_value(item)).value for item in statuses)
            if not values:
                return []
            sql += f" AND r.status IN ({','.join('?' for _ in values)})"
            params.extend(values)
        sql += " ORDER BY r.scheduled_for, r.id"
        conn = self.connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
        return [_automation_run_from_row(row) for row in rows]

    def mark_automation_run_missed(
        self,
        automation_run_id: Any,
        *,
        error_class: str = "misfire_grace_exceeded",
        now: datetime | str | None = None,
    ) -> AutomationRun:
        entity_id = _entity_id(automation_run_id)
        safe_error = self._optional_safe_error(error_class, "error_class")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT status, automation_id, scheduled_for FROM automation_runs WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown automation run: {entity_id}")
            if row["status"] != AutomationRunStatus.MISSED.value \
                    and row["status"] != AutomationRunStatus.QUEUED.value:
                raise InvalidTransition(
                    f"invalid automation_run transition: {row['status']} -> missed"
                )
            if row["status"] == AutomationRunStatus.QUEUED.value:
                conn.execute(
                    "UPDATE automation_runs SET status = 'missed', lease_owner = NULL, "
                    "lease_until = NULL, ended_at = ?, error_class = ? WHERE id = ?",
                    (changed_at, safe_error, entity_id),
                )
                conn.execute(
                    "UPDATE automations SET last_run_at = ?, last_status = 'missed', "
                    "updated_at = ? WHERE id = ?",
                    (row["scheduled_for"], changed_at, row["automation_id"]),
                )
        return self.automation_run(entity_id)

    def claim_automation_runs(
        self,
        *,
        lease_owner: str,
        now: datetime | str | None = None,
        lease_seconds: int = 60,
        limit: int = 32,
    ) -> list[AutomationRun]:
        """Atomically lease queued occurrences to one scheduler."""
        owner = self._required_text(lease_owner, "lease_owner")
        if isinstance(lease_seconds, bool) or not 1 <= int(lease_seconds) <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        if isinstance(limit, bool) or not 1 <= int(limit) <= 1000:
            raise ValueError("limit is out of range")
        current_dt = (
            datetime.fromisoformat(self._now())
            if now is None
            else datetime.fromisoformat(_aware_utc_iso(now, field="now"))
        )
        current = current_dt.isoformat(timespec="microseconds")
        lease_until = (current_dt + timedelta(seconds=int(lease_seconds))).isoformat(
            timespec="microseconds"
        )
        claimed: list[AutomationRun] = []
        with self._immediate() as conn:
            rows = conn.execute(
                "SELECT id FROM automation_runs WHERE status = 'queued' "
                "AND (lease_owner IS NULL OR lease_until IS NULL OR lease_until <= ? "
                "OR lease_owner = ?) ORDER BY scheduled_for, id LIMIT ?",
                (current, owner, int(limit)),
            ).fetchall()
            for row in rows:
                result = conn.execute(
                    "UPDATE automation_runs SET lease_owner = ?, lease_until = ? "
                    "WHERE id = ? AND status = 'queued' "
                    "AND (lease_owner IS NULL OR lease_until IS NULL OR lease_until <= ? "
                    "OR lease_owner = ?)",
                    (owner, lease_until, row["id"], current, owner),
                )
                if result.rowcount != 1:
                    continue
                claimed_row = conn.execute(
                    "SELECT * FROM automation_runs WHERE id = ?", (row["id"],)
                ).fetchone()
                if claimed_row is not None:
                    claimed.append(_automation_run_from_row(claimed_row))
        return claimed

    claim_due_automation_runs = claim_automation_runs

    def renew_automation_run_lease(
        self,
        automation_run_id: Any,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
        now: datetime | str | None = None,
    ) -> bool:
        entity_id = _entity_id(automation_run_id)
        owner = self._required_text(lease_owner, "lease_owner")
        if isinstance(lease_seconds, bool) or not 1 <= int(lease_seconds) <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        current_dt = (
            datetime.fromisoformat(self._now())
            if now is None
            else datetime.fromisoformat(_aware_utc_iso(now, field="now"))
        )
        current = current_dt.isoformat(timespec="microseconds")
        lease_until = (current_dt + timedelta(seconds=int(lease_seconds))).isoformat(
            timespec="microseconds"
        )
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE automation_runs SET lease_until = ? WHERE id = ? "
                "AND status IN ('queued', 'running') AND lease_owner = ? "
                "AND lease_until > ?",
                (lease_until, entity_id, owner, current),
            )
            return result.rowcount == 1

    def renew_automation_run_leases(
        self,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
        now: datetime | str | None = None,
    ) -> int:
        owner = self._required_text(lease_owner, "lease_owner")
        if isinstance(lease_seconds, bool) or not 1 <= int(lease_seconds) <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        current_dt = (
            datetime.fromisoformat(self._now())
            if now is None
            else datetime.fromisoformat(_aware_utc_iso(now, field="now"))
        )
        current = current_dt.isoformat(timespec="microseconds")
        lease_until = (current_dt + timedelta(seconds=int(lease_seconds))).isoformat(
            timespec="microseconds"
        )
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE automation_runs SET lease_until = ? WHERE lease_owner = ? "
                "AND status IN ('queued', 'running') AND lease_until > ?",
                (lease_until, owner, current),
            )
            return int(result.rowcount)

    def release_automation_run_claim(
        self, automation_run_id: Any, *, lease_owner: str
    ) -> bool:
        entity_id = _entity_id(automation_run_id)
        owner = self._required_text(lease_owner, "lease_owner")
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE automation_runs SET lease_owner = NULL, lease_until = NULL "
                "WHERE id = ? AND status = 'queued' AND lease_owner = ?",
                (entity_id, owner),
            )
            return result.rowcount == 1

    def release_automation_run_claims(self, *, lease_owner: str) -> int:
        """Release this scheduler's leases without changing running evidence."""
        owner = self._required_text(lease_owner, "lease_owner")
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE automation_runs SET lease_owner = NULL, lease_until = NULL "
                "WHERE lease_owner = ? AND status IN ('queued', 'running')",
                (owner,),
            )
            return int(result.rowcount)

    def recover_expired_automation_claims(
        self, *, now: datetime | str | None = None
    ) -> int:
        current = self._now() if now is None else _aware_utc_iso(now, field="now")
        with self._immediate() as conn:
            result = conn.execute(
                "UPDATE automation_runs SET lease_owner = NULL, lease_until = NULL "
                "WHERE status = 'queued' AND lease_owner IS NOT NULL "
                "AND (lease_until IS NULL OR lease_until <= ?)",
                (current,),
            )
            return int(result.rowcount)

    def start_automation_run(
        self,
        automation_run_id: Any,
        *,
        lease_owner: str,
        turn_id: str,
        lease_seconds: int = 60,
        now: datetime | str | None = None,
    ) -> AutomationRun | None:
        entity_id = _entity_id(automation_run_id)
        owner = self._required_text(lease_owner, "lease_owner")
        safe_turn = self._required_text(turn_id, "turn_id")
        if isinstance(lease_seconds, bool) or not 1 <= int(lease_seconds) <= 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        current_dt = (
            datetime.fromisoformat(self._now())
            if now is None
            else datetime.fromisoformat(_aware_utc_iso(now, field="now"))
        )
        current = current_dt.isoformat(timespec="microseconds")
        lease_until = (current_dt + timedelta(seconds=int(lease_seconds))).isoformat(
            timespec="microseconds"
        )
        try:
            with self._immediate() as conn:
                result = conn.execute(
                    "UPDATE automation_runs SET status = 'running', turn_id = ?, "
                    "lease_until = ?, started_at = ?, error_class = NULL WHERE id = ? "
                    "AND status = 'queued' AND lease_owner = ? AND lease_until > ?",
                    (safe_turn, lease_until, current, entity_id, owner, current),
                )
                if result.rowcount != 1:
                    return None
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("turn already belongs to an automation run") from exc
        return self.automation_run(entity_id)

    def running_automation_runs(
        self,
        *,
        lease_owner: str | None = None,
        now: datetime | str | None = None,
        recoverable_only: bool = False,
        include_live_owner: bool = True,
    ) -> list[AutomationRun]:
        sql = "SELECT * FROM automation_runs WHERE status = 'running'"
        params: list[Any] = []
        if recoverable_only:
            if lease_owner is None:
                raise ValueError("recoverable_only requires lease_owner")
            owner = self._required_text(lease_owner, "lease_owner")
            current = self._now() if now is None else _aware_utc_iso(now, field="now")
            if include_live_owner:
                sql += (
                    " AND (lease_owner = ? OR lease_owner IS NULL OR lease_until IS NULL "
                    "OR lease_until <= ?)"
                )
                params.extend([owner, current])
            else:
                sql += (
                    " AND (lease_owner IS NULL OR lease_until IS NULL OR lease_until <= ?)"
                )
                params.append(current)
        elif lease_owner is not None:
            sql += " AND lease_owner = ?"
            params.append(self._required_text(lease_owner, "lease_owner"))
        sql += " ORDER BY scheduled_for, id"
        conn = self.connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
        return [_automation_run_from_row(row) for row in rows]

    def finish_automation_run(
        self,
        automation_run_id: Any,
        *,
        turn_id: str | None,
        ok: bool,
        final_event_id: int | None = None,
        error_class: str | None = None,
        now: datetime | str | None = None,
    ) -> AutomationRun | None:
        """Idempotently converge the exact Console turn to one terminal state."""
        entity_id = _entity_id(automation_run_id)
        safe_turn = self._optional_channel_text(turn_id, "turn_id")
        if final_event_id is not None and (
            isinstance(final_event_id, bool)
            or not isinstance(final_event_id, int)
            or final_event_id < 0
        ):
            raise ValueError("final_event_id must be a non-negative integer")
        target = AutomationRunStatus.DONE if bool(ok) else AutomationRunStatus.ERROR
        safe_error = None if ok else self._optional_safe_error(
            error_class or "automation_turn_failed", "error_class"
        )
        if ok and error_class is not None:
            raise ValueError("successful automation run cannot carry error_class")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE id = ?", (entity_id,)
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown automation run: {entity_id}")
            current = AutomationRunStatus(row["status"])
            if current in {
                AutomationRunStatus.DONE,
                AutomationRunStatus.ERROR,
                AutomationRunStatus.MISSED,
                AutomationRunStatus.CANCELLED,
            }:
                # Duplicate terminal callbacks are safe and cannot rewrite the
                # original decision or attach a different Console turn.
                if safe_turn is None or row["turn_id"] == safe_turn:
                    return _automation_run_from_row(row)
                return None
            if current is not AutomationRunStatus.RUNNING:
                return None
            if safe_turn is None or row["turn_id"] != safe_turn:
                return None
            conn.execute(
                "UPDATE automation_runs SET status = ?, final_event_id = ?, "
                "lease_owner = NULL, lease_until = NULL, ended_at = ?, error_class = ? "
                "WHERE id = ? AND status = 'running' AND turn_id = ?",
                (
                    target.value,
                    final_event_id,
                    changed_at,
                    safe_error,
                    entity_id,
                    safe_turn,
                ),
            )
            conn.execute(
                "UPDATE automations SET last_run_at = ?, last_status = ?, "
                "enabled = CASE WHEN schedule_kind = 'once' THEN 0 ELSE enabled END, "
                "pause_reason = CASE WHEN schedule_kind = 'once' THEN ? ELSE pause_reason END, "
                "updated_at = ? WHERE id = ?",
                (
                    row["scheduled_for"],
                    target.value,
                    "completed" if ok else "error",
                    changed_at,
                    row["automation_id"],
                ),
            )
        return self.automation_run(entity_id)

    def transition_automation_run(
        self,
        automation_run_id: Any,
        status: AutomationRunStatus | str,
        *,
        turn_id: Optional[str] = None,
        final_event_id: Optional[int] = None,
        lease_owner: Optional[str] = None,
        lease_until: datetime | str | None = None,
        error_class: Optional[str] = None,
        now: datetime | str | None = None,
    ) -> AutomationRun:
        entity_id = _entity_id(automation_run_id)
        target = AutomationRunStatus(_enum_value(status))
        safe_turn_id = self._optional_channel_text(turn_id, "turn_id")
        safe_owner = self._optional_channel_text(lease_owner, "lease_owner")
        safe_error = self._optional_safe_error(error_class, "error_class")
        changed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        lease_time = _optional_time(lease_until, field="lease_until")
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT status FROM automation_runs WHERE id = ?", (entity_id,)
            ).fetchone()
            if row is None:
                raise ChannelNotFound(f"unknown automation run: {entity_id}")
            current = AutomationRunStatus(row["status"])
            _check_transition(
                "automation_run", current, target, _AUTOMATION_RUN_TRANSITIONS
            )
            started_at = changed_at if target is AutomationRunStatus.RUNNING else None
            ended_at = (
                changed_at
                if target
                in {
                    AutomationRunStatus.DONE,
                    AutomationRunStatus.ERROR,
                    AutomationRunStatus.MISSED,
                    AutomationRunStatus.CANCELLED,
                }
                else None
            )
            conn.execute(
                "UPDATE automation_runs SET status = ?, turn_id = COALESCE(?, turn_id), "
                "final_event_id = COALESCE(?, final_event_id), lease_owner = ?, lease_until = ?, "
                "started_at = COALESCE(?, started_at), ended_at = COALESCE(?, ended_at), "
                "error_class = ? WHERE id = ?",
                (
                    target.value,
                    safe_turn_id,
                    final_event_id,
                    safe_owner if target is AutomationRunStatus.RUNNING else None,
                    lease_time if target is AutomationRunStatus.RUNNING else None,
                    started_at,
                    ended_at,
                    safe_error,
                    entity_id,
                ),
            )
        return self.automation_run(entity_id)

    # -- Safe audit ---------------------------------------------------

    @staticmethod
    def _safe_audit_value(key: str, value: Any) -> Any:
        lowered = key.lower()
        if any(part in lowered for part in _FORBIDDEN_AUDIT_KEY_PARTS):
            raise UnsafeAuditDetails(f"audit key is not allowed: {key}")
        if value is None:
            return None
        if isinstance(value, Enum):
            value = value.value
        if lowered == "ts" or lowered.endswith("_at") or lowered.endswith("_until") \
                or lowered.endswith("_for"):
            try:
                return _aware_utc_iso(value, field=key)
            except (TypeError, ValueError) as exc:
                raise UnsafeAuditDetails(f"audit time is not safe: {key}") from exc
        if lowered == "id" or lowered.endswith("_id"):
            encoded = str(value)
            if _TOKEN_SHAPE_RE.search(encoded) or not _SAFE_ID_RE.fullmatch(encoded):
                raise UnsafeAuditDetails(f"audit id is not safe: {key}")
            return encoded
        if lowered in _AUDIT_STATE_KEYS:
            encoded = str(value)
            if not _SAFE_STATUS_RE.fullmatch(encoded):
                raise UnsafeAuditDetails(f"audit status is not safe: {key}")
            return encoded
        if lowered in _AUDIT_ERROR_KEYS:
            encoded = str(value)
            if _TOKEN_SHAPE_RE.search(encoded) or not _SAFE_ERROR_RE.fullmatch(encoded):
                raise UnsafeAuditDetails(f"audit error class is not safe: {key}")
            return encoded
        raise UnsafeAuditDetails(f"audit key is not on the allowlist: {key}")

    def audit(
        self,
        event_kind: str,
        *,
        session_id: Optional[str] = None,
        bot_account_id: Optional[str] = None,
        binding_id: Optional[str] = None,
        pairing_request_id: Optional[str] = None,
        automation_id: Optional[str] = None,
        outbox_id: Optional[str] = None,
        error_class: Optional[str] = None,
        safe_details: Optional[Mapping[str, Any]] = None,
    ) -> int:
        event = self._required_text(event_kind, "event_kind")
        if not _SAFE_ERROR_RE.fullmatch(event) or _TOKEN_SHAPE_RE.search(event):
            raise UnsafeAuditDetails("event_kind must be a safe structured identifier")
        details = {
            key: self._safe_audit_value(str(key), value)
            for key, value in (safe_details or {}).items()
        }
        ids = {
            "session_id": session_id,
            "bot_account_id": bot_account_id,
            "binding_id": binding_id,
            "pairing_request_id": pairing_request_id,
            "automation_id": automation_id,
            "outbox_id": outbox_id,
        }
        safe_ids = {
            key: None if value is None else self._safe_audit_value(key, value)
            for key, value in ids.items()
        }
        safe_error = (
            None
            if error_class is None
            else self._safe_audit_value("error_class", error_class)
        )
        now = self._now()
        payload = json.dumps(details, sort_keys=True, separators=(",", ":"))
        with self._immediate() as conn:
            cur = conn.execute(
                "INSERT INTO channel_audit "
                "(ts, event_kind, session_id, bot_account_id, binding_id, "
                "pairing_request_id, automation_id, outbox_id, error_class, "
                "safe_details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    event,
                    safe_ids["session_id"],
                    safe_ids["bot_account_id"],
                    safe_ids["binding_id"],
                    safe_ids["pairing_request_id"],
                    safe_ids["automation_id"],
                    safe_ids["outbox_id"],
                    safe_error,
                    payload,
                ),
            )
            return int(cur.lastrowid)


__all__ = [
    "ChannelConflict",
    "ChannelNotFound",
    "ChannelStore",
    "ChannelStoreError",
    "InvalidTransition",
    "OutboxClaim",
    "SCHEMA_SQL",
    "UnsafeAuditDetails",
]
