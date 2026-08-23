"""Durable SQLite fact store for outbound conversation channels.

Every mutating domain decision opens its own connection.  Decisions that can
race (pairing approval, claims, projection, and state resolution) take a
``BEGIN IMMEDIATE`` transaction so uniqueness is decided by SQLite, not by a
stale Python pre-check.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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
        (kind = 'text' AND text IS NOT NULL AND media_id IS NULL AND caption IS NULL)
        OR (kind = 'media' AND text IS NULL AND media_id IS NOT NULL)
    )
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
_TOKEN_SHAPE_RE = re.compile(r"(?<!\d)\d{5,}:[A-Za-z0-9_-]{20,}")
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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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
        return value.strip()

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
        name = self._required_text(display_name, "display_name")
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
        try:
            with self._immediate() as conn:
                row = conn.execute(
                    "SELECT credential_revision, status FROM bot_accounts WHERE id = ?",
                    (entity_id,),
                ).fetchone()
                if row is None:
                    raise ChannelNotFound(f"unknown bot account: {entity_id}")
                if int(row["credential_revision"]) != int(expected_revision):
                    raise ChannelConflict("bot credential revision changed")
                current = BotStatus(row["status"])
                target = (
                    BotStatus.AWAITING_PAIRING
                    if status is None and current is BotStatus.DRAFT
                    else current
                    if status is None
                    else BotStatus(_enum_value(status))
                )
                updated = conn.execute(
                    "UPDATE bot_accounts SET credential_id = ?, telegram_bot_id = ?, "
                    "telegram_username = ?, credential_revision = credential_revision + 1, "
                    "status = ?, last_verified_at = ?, last_error_code = NULL, updated_at = ? "
                    "WHERE id = ? AND credential_revision = ?",
                    (
                        credential_id,
                        str(telegram_bot_id),
                        telegram_username,
                        target.value,
                        verified,
                        verified,
                        entity_id,
                        int(expected_revision),
                    ),
                )
                if updated.rowcount != 1:
                    raise ChannelConflict("bot credential revision changed")
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
        try:
            with self._immediate() as conn:
                cur = conn.execute(
                    "UPDATE bot_accounts SET telegram_bot_id = ?, telegram_username = ?, "
                    "status = 'active', last_verified_at = ?, last_error_code = NULL, "
                    "updated_at = ? WHERE id = ? AND status <> 'disconnected'",
                    (str(telegram_bot_id), telegram_username, now, now, entity_id),
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
                    (target.value, error_code, now, entity_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("bot status change violates a live identity constraint") from exc
        return self.bot(entity_id)

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
                        telegram_username,
                        telegram_display_name,
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
                reason = pause_reason if target is BindingStatus.PAUSED else None
                conn.execute(
                    "UPDATE channel_bindings SET status = ?, pause_reason = ?, paused_at = ?, "
                    "disconnected_at = COALESCE(?, disconnected_at), last_error_code = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        target.value,
                        reason,
                        paused_at,
                        disconnected_at,
                        error_code,
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
        try:
            with self._immediate() as conn:
                conn.execute(
                    "INSERT INTO pairing_requests "
                    "(id, bot_account_id, session_id, telegram_update_id, telegram_user_id, "
                    "telegram_chat_id, telegram_username, telegram_display_name, code_salt, "
                    "code_digest, code_hint, status, created_at, expires_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                    (
                        request_id,
                        entity_id,
                        self._required_text(session_id, "session_id"),
                        update_id,
                        str(telegram_user_id),
                        str(telegram_chat_id),
                        telegram_username,
                        telegram_display_name,
                        sqlite3.Binary(bytes(code_salt)),
                        sqlite3.Binary(bytes(code_digest)),
                        self._required_text(code_hint, "code_hint"),
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
            params.append(PairingStatus(_enum_value(status)).value)
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
                    if expected_credential_revision is not None and int(
                        bot["credential_revision"]
                    ) != int(expected_credential_revision):
                        raise ChannelConflict("bot credential revision changed during approval")
                    if expected_telegram_bot_id is not None and bot[
                        "telegram_bot_id"
                    ] != str(expected_telegram_bot_id):
                        raise ChannelConflict("telegram bot identity changed during approval")

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
                        "UPDATE bot_accounts SET status = 'active', updated_at = ? "
                        "WHERE id = ?",
                        (decided_at, request["bot_account_id"]),
                    )
        except sqlite3.IntegrityError as exc:
            raise ChannelConflict("pairing approval conflicts with a live one-to-one binding") from exc
        if expired:
            raise ChannelConflict("pairing request has expired")
        if approved_binding is not None:
            return approved_binding
        return self.binding(binding_id)

    approve_pairing = approve_pairing_request

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

    @staticmethod
    def _coerce_parts(parts: Iterable[OutboundPart | Mapping[str, Any]]) -> list[OutboundPart]:
        result: list[OutboundPart] = []
        seen: set[int] = set()
        for raw in parts:
            part = raw if isinstance(raw, OutboundPart) else OutboundPart(**dict(raw))
            number = int(part.payload_part)
            if number in seen:
                raise ChannelConflict("payload_part must be unique within a projected event")
            seen.add(number)
            result.append(part)
        return sorted(result, key=lambda item: item.payload_part)

    def project_event(
        self,
        binding_id: Any,
        *,
        source_event_id: int,
        parts: Iterable[OutboundPart | Mapping[str, Any]],
    ) -> list[OutboxItem]:
        entity_id = _entity_id(binding_id)
        event_id = int(source_event_id)
        if event_id < 0:
            raise ValueError("source_event_id must be non-negative")
        projected_parts = self._coerce_parts(parts)
        now = self._now()
        batch_id = "batch_" + uuid.uuid5(
            uuid.NAMESPACE_URL, f"guanlan-channel:{entity_id}:{event_id}"
        ).hex
        with self._immediate() as conn:
            binding = conn.execute(
                "SELECT session_id, projection_cursor_event_id FROM channel_bindings "
                "WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if binding is None:
                raise ChannelNotFound(f"unknown channel binding: {entity_id}")
            cursor = int(binding["projection_cursor_event_id"])
            if event_id >= cursor:
                next_sequence = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM outbox WHERE binding_id = ?",
                        (entity_id,),
                    ).fetchone()[0]
                )
                for part in projected_parts:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO outbox "
                        "(id, binding_id, source_session_id, source_event_id, payload_part, "
                        "batch_id, sequence, kind, text, media_id, caption, state, attempts, "
                        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)",
                        (
                            _new_id("out"),
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
                            now,
                        ),
                    )
                    if cur.rowcount == 1:
                        next_sequence += 1
                conn.execute(
                    "UPDATE channel_bindings SET projection_cursor_event_id = MAX("
                    "projection_cursor_event_id, ?), updated_at = ? WHERE id = ?",
                    (event_id, now, entity_id),
                )
            rows = conn.execute(
                "SELECT * FROM outbox WHERE binding_id = ? AND source_event_id = ? "
                "ORDER BY payload_part",
                (entity_id, event_id),
            ).fetchall()
            return [_outbox_from_row(row) for row in rows]

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

    def transition_outbox(
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
        entity_id = _entity_id(outbox_id)
        target = OutboxState(_enum_value(state))
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
            last_error_at = changed_at if error_class else None
            conn.execute(
                "UPDATE outbox SET state = ?, attempts = ?, next_attempt_at = ?, "
                "lease_owner = ?, lease_until = ?, telegram_message_id = COALESCE(?, "
                "telegram_message_id), last_error_class = ?, last_error_at = ?, "
                "sent_at = COALESCE(?, sent_at) WHERE id = ?",
                (
                    target.value,
                    attempts,
                    retry_time if target is OutboxState.RETRY_WAIT else None,
                    lease_owner if target is OutboxState.SENDING else None,
                    lease_time if target is OutboxState.SENDING else None,
                    None if telegram_message_id is None else str(telegram_message_id),
                    error_class,
                    last_error_at,
                    sent_at,
                    entity_id,
                ),
            )
        return self.outbox(entity_id)

    def resolve_outbox(self, outbox_id: Any, action: str) -> OutboxItem:
        current = self.outbox(outbox_id)
        resolutions: dict[tuple[OutboxState, str], OutboxState] = {
            (OutboxState.UNCERTAIN, "assume_sent"): OutboxState.SENT,
            (OutboxState.UNCERTAIN, "resend"): OutboxState.PENDING,
            (OutboxState.UNCERTAIN, "drop"): OutboxState.CANCELLED,
            (OutboxState.TERMINAL, "retry"): OutboxState.PENDING,
            (OutboxState.TERMINAL, "drop"): OutboxState.CANCELLED,
        }
        target = resolutions.get((current.state, action))
        if target is None:
            raise InvalidTransition(f"invalid outbox resolution: {current.state.value} / {action}")
        return self.transition_outbox(current.id, target)

    def claim_next_outbox(
        self,
        binding_id: Any,
        *,
        owner: str,
        lease_until: datetime | str,
        now: datetime | str | None = None,
    ) -> Optional[OutboxItem]:
        entity_id = _entity_id(binding_id)
        claimed_at = self._now() if now is None else _aware_utc_iso(now, field="now")
        lease_time = _aware_utc_iso(lease_until, field="lease_until")
        claimed_id: Optional[str] = None
        with self._immediate() as conn:
            conn.execute(
                "UPDATE outbox SET state = 'uncertain', lease_owner = NULL, lease_until = NULL, "
                "last_error_class = 'lease_expired', last_error_at = ? "
                "WHERE binding_id = ? AND state = 'sending' AND lease_until <= ?",
                (claimed_at, entity_id, claimed_at),
            )
            head = conn.execute(
                "SELECT * FROM outbox WHERE binding_id = ? AND state NOT IN ('sent', 'cancelled') "
                "ORDER BY sequence LIMIT 1",
                (entity_id,),
            ).fetchone()
            if head is None:
                return None
            state = OutboxState(head["state"])
            due = head["next_attempt_at"] is None or head["next_attempt_at"] <= claimed_at
            if state not in {OutboxState.PENDING, OutboxState.RETRY_WAIT} or not due:
                return None
            claimed_id = head["id"]
            conn.execute(
                "UPDATE outbox SET state = 'sending', attempts = attempts + 1, "
                "next_attempt_at = NULL, lease_owner = ?, lease_until = ? WHERE id = ?",
                (self._required_text(owner, "owner"), lease_time, claimed_id),
            )
        return None if claimed_id is None else self.outbox(claimed_id)

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
        automation_id = _new_id("auto")
        now = self._now()
        confirmed = now if confirmed_at is None else _aware_utc_iso(
            confirmed_at, field="confirmed_at"
        )
        run_time = _optional_time(run_at, field="run_at")
        next_time = _optional_time(next_run_at, field="next_run_at")
        last_time = _optional_time(last_run_at, field="last_run_at")
        refs = tuple(self._required_text(ref, "skill_ref") for ref in skill_refs)
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
                        self._required_text(name, "name"),
                        self._required_text(session_id, "session_id"),
                        _enum_value(schedule_kind),
                        local_time,
                        run_time,
                        self._required_text(zone, "timezone"),
                        self._required_text(prompt_text, "prompt_text"),
                        json.dumps(refs, ensure_ascii=False, separators=(",", ":")),
                        _enum_value(media_policy),
                        int(bool(enabled)),
                        pause_reason,
                        next_time,
                        last_time,
                        last_status,
                        int(misfire_grace_seconds),
                        created_by,
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

    def create_automation_run(
        self,
        automation_id: Any,
        scheduled_for: datetime | str,
    ) -> AutomationRun:
        parent_id = _entity_id(automation_id)
        occurrence = _aware_utc_iso(scheduled_for, field="scheduled_for")
        run_id = _new_id("arun")
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
                    turn_id,
                    final_event_id,
                    lease_owner if target is AutomationRunStatus.RUNNING else None,
                    lease_time if target is AutomationRunStatus.RUNNING else None,
                    started_at,
                    ended_at,
                    error_class,
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
    "SCHEMA_SQL",
    "UnsafeAuditDetails",
]
