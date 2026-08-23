"""Synchronous domain façade for the outbound Channel subsystem.

The façade is deliberately the only layer that joins Console facts, the
Channel SQLite registry, the DPAPI credential store, and the Telegram adapter.
Its SQLite/file methods are synchronous; ASGI callers run those methods in a
worker thread, while Bot API calls remain async.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from guanlan_v2.channels.models import (
    BindingStatus,
    BotStatus,
    PairingStatus,
)
from guanlan_v2.channels.media_policy import (
    MEDIA_ID_RE,
    caption_contains_location,
    caption_sha256,
    media_confirmation_subject,
)
from guanlan_v2.channels.secrets import SecretUnavailable
from guanlan_v2.channels.store import (
    ChannelConflict,
    ChannelNotFound,
    ChannelStoreError,
    InvalidTransition,
)
from guanlan_v2.channels.telegram import (
    TelegramCredentialInvalid,
    TelegramError,
    TelegramPollConflict,
    TelegramTargetUnavailable,
)
from guanlan_v2.console.workspaces import GENERAL_WORKSPACE_ID


_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:bot)?([0-9]{5,16}:[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_SAFE_SID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SAFE_ACTION_RE = re.compile(r"^[a-z][a-z0-9:_-]{0,95}$")
_SAFE_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_PAIRING_CODE_RE = re.compile(
    r"(?<![0-9A-Z])[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}(?![0-9A-Z])"
)
_AUTOMATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_CREDENTIAL_ID_RE = re.compile(r"^cred_[0-9a-f]{32}$")
_AUTOMATION_CHAT_ID_RE = re.compile(
    r"(?i)\b(?:telegram[ _-]?)?(?:chat|user)[ _-]?id\s*[:=]\s*-?[0-9]{4,20}\b"
)
_AUTOMATION_LOCAL_PATH_RE = re.compile(
    r"(?i)(?:\b[A-Za-z]:[\\/]|file://|\\\\[^\\\s]+\\[^\\\s]+|"
    r"(?:^|[\s\"'(])/(?:Users|home|etc|var|tmp|mnt)(?:/|\b))"
)
_AUTOMATION_SPEC_KEYS = frozenset(
    {
        "automation_id",
        "name",
        "schedule_kind",
        "run_at",
        "local_time",
        "timezone",
        "prompt_text",
        "skill_refs",
        "media_policy",
    }
)
_AUTOMATION_REQUIRED_KEYS = frozenset(
    {"name", "schedule_kind", "timezone", "prompt_text", "media_policy"}
)
_MAX_MEDIA_CAPTION_CHARS = 50_000
_MEDIA_PERMIT_TTL_SECONDS = 600
_MEDIA_CONFIRM_ACTION = "telegram:publish-media"
_STATUS_DELIVERY_LIMIT = 20
_ACTION_REQUIRED_DELIVERY_STATES = frozenset({"uncertain", "terminal"})
_STARTUP_REVERIFY_STATUSES = frozenset(
    {
        BotStatus.AWAITING_PAIRING,
        BotStatus.ACTIVE,
        BotStatus.PAUSED,
        BotStatus.POLL_CONFLICT,
        BotStatus.CREDENTIAL_INVALID,
        BotStatus.CREDENTIAL_UNAVAILABLE,
    }
)


def _status_delivery_window(deliveries: list[Any]) -> list[Any]:
    """Keep human-resolvable rows visible, then fill with the newest rows.

    Projection can continue appending pending rows while the outbox worker is
    stopped at an older ``uncertain``/``terminal`` item.  A plain ``[-20:]``
    window can therefore report a non-zero action-required count while hiding
    the only row the user can resolve.  Preserve source order, prioritise the
    oldest actionable rows (the effective queue heads), and use any remaining
    slots for the newest non-actionable deliveries.
    """

    if len(deliveries) <= _STATUS_DELIVERY_LIMIT:
        return list(deliveries)

    def state_of(item: Any) -> object:
        raw = item.get("state") if isinstance(item, Mapping) else getattr(item, "state", None)
        return getattr(raw, "value", raw)

    chosen = {
        index
        for index, item in enumerate(deliveries)
        if state_of(item) in _ACTION_REQUIRED_DELIVERY_STATES
    }
    if len(chosen) > _STATUS_DELIVERY_LIMIT:
        chosen = set(sorted(chosen)[:_STATUS_DELIVERY_LIMIT])

    for index in range(len(deliveries) - 1, -1, -1):
        if len(chosen) >= _STATUS_DELIVERY_LIMIT:
            break
        chosen.add(index)

    return [item for index, item in enumerate(deliveries) if index in chosen]


class ChannelRuntimeError(RuntimeError):
    """Safe public runtime failure; it deliberately never retains input text."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ChannelSecretShapeError(ChannelRuntimeError):
    def __init__(self) -> None:
        super().__init__("telegram_token_shape_rejected")


@dataclass(frozen=True, slots=True)
class _TicketRecord:
    digest: bytes
    sid: str
    action: str
    subject_id: str
    expires_at: float


class ActionTicketStore:
    """In-memory, one-use, narrowly scoped action tickets.

    Only a SHA-256 digest is retained.  The raw random value exists in a
    response and then in the caller's local request body; it is never written
    to Console or Channel storage.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 120.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._records: dict[bytes, _TicketRecord] = {}

    @staticmethod
    def _validate_scope(sid: object, action: object, subject_id: object) -> tuple[str, str, str]:
        values = (sid, action, subject_id)
        if not all(isinstance(value, str) for value in values):
            raise ValueError("ticket scope must be text")
        safe_sid, safe_action, safe_subject = (str(value) for value in values)
        if (
            not _SAFE_SID_RE.fullmatch(safe_sid)
            or not _SAFE_ACTION_RE.fullmatch(safe_action)
            or ChannelRuntime.token_matches(safe_sid)
            or ChannelRuntime.token_matches(safe_action)
            or ChannelRuntime.token_matches(safe_subject)
        ):
            raise ValueError("invalid ticket scope")
        if not _SAFE_SUBJECT_RE.fullmatch(safe_subject):
            raise ValueError("invalid ticket subject")
        return safe_sid, safe_action, safe_subject

    @staticmethod
    def _digest(raw: str) -> bytes:
        return hashlib.sha256(raw.encode("ascii")).digest()

    def _purge_expired_locked(self, now: float) -> None:
        for digest, record in tuple(self._records.items()):
            if record.expires_at <= now:
                self._records.pop(digest, None)

    def issue(self, sid: str, action: str, subject_id: str) -> str:
        safe_sid, safe_action, safe_subject = self._validate_scope(sid, action, subject_id)
        raw = secrets.token_urlsafe(32)
        digest = self._digest(raw)
        now = float(self._clock())
        record = _TicketRecord(
            digest=digest,
            sid=safe_sid,
            action=safe_action,
            subject_id=safe_subject,
            expires_at=now + self._ttl_seconds,
        )
        with self._lock:
            self._purge_expired_locked(now)
            self._records[digest] = record
        return raw

    def consume(self, ticket: object, sid: str, action: str, subject_id: str) -> bool:
        try:
            safe_sid, safe_action, safe_subject = self._validate_scope(sid, action, subject_id)
        except ValueError:
            return False
        if not isinstance(ticket, str) or not ticket or len(ticket) > 512:
            return False
        try:
            digest = self._digest(ticket)
        except Exception:
            return False
        now = float(self._clock())
        with self._lock:
            self._purge_expired_locked(now)
            record = self._records.get(digest)
            if record is None or record.expires_at <= now:
                return False
            # A bad scope must not burn a valid ticket for its actual narrowly
            # scoped action.  Successful consumption deletes it while still
            # holding the lock, so concurrent callers cannot both win.
            matches = (
                hmac.compare_digest(record.digest, digest)
                and hmac.compare_digest(record.sid, safe_sid)
                and hmac.compare_digest(record.action, safe_action)
                and hmac.compare_digest(record.subject_id, safe_subject)
            )
            if matches:
                self._records.pop(digest, None)
            return matches


class ChannelRuntime:
    """Safe, session-scoped Channel domain façade.

    ``console_store`` is read only here except through the existing Console
    writer supplied by ``build_console_router``.  Runtime methods never accept
    a Telegram chat recipient from the Agent/API layer.
    """

    def __init__(
        self,
        *,
        console_store: Any,
        channel_store: Any,
        secret_store: Any,
        telegram_adapter: Any,
        media_store: Any | None = None,
        ticket_store: ActionTicketStore | None = None,
        automation_worker: Any | None = None,
        automation_calendar: Any | None = None,
        automation_clock: Callable[[], Any] | None = None,
        credential_orphan_grace_seconds: float = 3600.0,
        maintenance_interval_seconds: float = 900.0,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self.console_store = console_store
        self.channel_store = channel_store
        self.secret_store = secret_store
        self.telegram_adapter = telegram_adapter
        self.media_store = media_store
        self.ticket_store = ticket_store or ActionTicketStore()
        # Shared mutation reservation is observed by Console archive/delete
        # guards.  It covers Token submission and pairing approval alike.
        self._secret_reservation_lock = threading.Lock()
        self._secret_reservations: set[str] = set()
        self._lifecycle_frozen_sessions: set[str] = set()
        # Pairing owns Telegram inbound and deliberately terminates it before
        # Console/Agent.  Construction is side-effect free; application
        # lifespan wiring calls ``start`` explicitly when the feature is on.
        from guanlan_v2.channels.pairing import TelegramPairingService

        self.pairing = TelegramPairingService(
            console_store=console_store,
            channel_store=channel_store,
            secret_store=secret_store,
            telegram_adapter=telegram_adapter,
            reserve_mutation=self.reserve_secret_submission,
            release_mutation=self.release_secret_submission,
        )
        from guanlan_v2.channels.outbox import OutboxWorker
        from guanlan_v2.channels.projector import ConsoleMessageProjector

        self.projector = ConsoleMessageProjector(console_store, channel_store)
        self.outbox_worker = OutboxWorker(
            channel_store=channel_store,
            secret_store=secret_store,
            telegram_adapter=telegram_adapter,
            media_store=media_store,
        )
        if automation_worker is None:
            from guanlan_v2.channels.automation import AutomationWorker
            from guanlan_v2.channels.trading_calendar import AShareTradingCalendar

            calendar = (
                automation_calendar
                if automation_calendar is not None
                else AShareTradingCalendar()
            )
            automation_worker = AutomationWorker(
                channel_store,
                calendar=calendar,
                clock=automation_clock,
            )
        self.automation_worker = automation_worker
        self._automation_hooks_bound = False
        self._console_channel_emit: Callable[..., Any] | None = None
        self._console_is_busy: Callable[[str], Any] | None = None
        self._console_submit_system_turn: Callable[..., Any] | None = None
        # Kept only as the Console-side terminal endpoint named by the frozen
        # hook contract.  Runtime never invokes it (that would recurse back
        # into ``automation_turn_terminal``).
        self._console_on_turn_terminal: Callable[..., Any] | None = None
        self._background_stop: asyncio.Event | None = None
        self._background_tasks: tuple[asyncio.Task[Any], ...] = ()
        self._maintenance_task: asyncio.Task[Any] | None = None
        self._background_lock: asyncio.Lock | None = None
        try:
            orphan_grace = float(credential_orphan_grace_seconds)
            maintenance_interval = float(maintenance_interval_seconds)
        except (TypeError, ValueError):
            raise ValueError("channel maintenance intervals must be numeric") from None
        if orphan_grace < 0 or maintenance_interval <= 0:
            raise ValueError("channel maintenance intervals are out of range")
        self._credential_orphan_grace_seconds = orphan_grace
        self._maintenance_interval_seconds = maintenance_interval
        self._wall_clock = wall_clock or time.time
        self._pairing_started = False
        self._automation_started = False
        self._adapter_closed = False
        # A reservation is deliberately just durable-process ownership state,
        # not a lock held across getMe/DPAPI/SQLite awaits.  It lets Console
        # archive/delete and the dedicated Token endpoint see the same narrow
        # in-flight handoff without serialising unrelated sessions.

    # -- narrow input boundary ---------------------------------------
    @staticmethod
    def token_matches(text: object) -> tuple[str, ...]:
        if not isinstance(text, str):
            return ()
        return tuple(match.group(1) for match in _TOKEN_RE.finditer(text))

    def extract_telegram_tokens(self, text: object) -> tuple[str, ...]:
        return self.token_matches(text)

    def looks_like_telegram_token(self, text: object) -> bool:
        return bool(self.token_matches(text))

    def reject_secret_shape(self, value: object) -> None:
        if isinstance(value, str) and self.looks_like_telegram_token(value):
            raise ChannelSecretShapeError()

    # -- action ticket façade ----------------------------------------
    def issue_action_ticket(self, sid: str, action: str, subject_id: str) -> str:
        return self.ticket_store.issue(sid, action, subject_id)

    def consume_action_ticket(
        self, ticket: object, sid: str, action: str, subject_id: str
    ) -> bool:
        return self.ticket_store.consume(ticket, sid, action, subject_id)

    issue_ticket = issue_action_ticket
    consume_ticket = consume_action_ticket

    # -- synchronous registry methods --------------------------------
    def _require_session(self, session_id: object, *, writable: bool = False) -> str:
        if (
            not isinstance(session_id, str)
            or not _SAFE_SID_RE.fullmatch(session_id)
            or self.token_matches(session_id)
        ):
            raise ChannelRuntimeError("channel_session_not_found")
        meta = self.console_store.get_meta(session_id)
        if not isinstance(meta, Mapping):
            raise ChannelRuntimeError("channel_session_not_found")
        if writable:
            if bool(meta.get("archived")):
                raise ChannelRuntimeError("channel_session_read_only")
            try:
                workspace_store = getattr(self.console_store, "workspace_store", None)
                require = getattr(workspace_store, "require", None)
                if not callable(require):
                    raise RuntimeError("workspace store unavailable")
                # ConsoleStore's public default is deliberately repeated here
                # rather than accepting a writable workspace from any request.
                workspace_id = str(meta.get("workspace_id") or GENERAL_WORKSPACE_ID)
                require(workspace_id, active_only=True)
            except ChannelRuntimeError:
                raise
            except Exception:
                raise ChannelRuntimeError("channel_session_read_only") from None
        return session_id

    def assert_session_mutable(self, session_id: object) -> str:
        """Shared pre-ticket/pre-network archive and workspace gate."""
        return self._require_session(session_id, writable=True)

    def reserve_secret_submission(self, session_id: object) -> bool:
        """Claim one in-flight secure Token handoff for a writable session.

        This only acquires a short in-process mutex to mutate a set.  The
        returned reservation survives subsequent async getMe/DPAPI work, but
        no blocking lock is held while that work awaits.
        """
        sid = self._require_session(session_id, writable=True)
        with self._secret_reservation_lock:
            if (
                sid in self._secret_reservations
                or sid in self._lifecycle_frozen_sessions
            ):
                return False
            self._secret_reservations.add(sid)
            return True

    def release_secret_submission(self, session_id: object) -> None:
        """Release an owned handoff without rechecking archival state."""
        if (
            not isinstance(session_id, str)
            or not _SAFE_SID_RE.fullmatch(session_id)
            or self.token_matches(session_id)
        ):
            return
        with self._secret_reservation_lock:
            self._secret_reservations.discard(session_id)

    def reserved_secret_session_ids(self) -> frozenset[str]:
        """Snapshot for Console archive/delete guards; exposes no secrets."""
        with self._secret_reservation_lock:
            return frozenset(self._secret_reservations)

    def _reserve_owned_mutation(self, session_id: object) -> str:
        sid = self._require_session(session_id, writable=True)
        if not self.reserve_secret_submission(sid):
            raise ChannelRuntimeError("channel_lifecycle_busy")
        return sid

    def current_draft(self, session_id: str):
        self._require_session(session_id)
        return self.channel_store.current_draft(session_id)

    def _session_target(self, session_id: str, *, writable: bool = False):
        """Choose live, then draft, then historical state deterministically."""
        sid = self._require_session(session_id, writable=writable)
        live = self.channel_store.binding_for_session(sid, live_only=True)
        if live is not None:
            return self.channel_store.bot(live.bot_account_id), live
        draft = self.channel_store.current_draft(sid)
        if draft is not None:
            return draft, None
        historical = self.channel_store.binding_for_session(sid, live_only=False)
        if historical is not None:
            return self.channel_store.bot(historical.bot_account_id), historical
        raise ChannelRuntimeError("channel_not_connected")

    def action_subject_for_session(self, session_id: str, action: str) -> str:
        """Resolve a ticket subject from durable ownership, never browser input."""
        sid = self._require_session(session_id, writable=True)
        if action == "telegram:submit-token":
            bot, _replacing, _intent = self.token_submission_target(sid)
            return bot.id
        bot, _binding = self._session_target(sid, writable=True)
        return bot.id

    def token_submission_target(self, session_id: str):
        """Return ``(BotAccount, replacing, replacement_intent_session)``."""
        sid = self._require_session(session_id, writable=True)
        intent = self.channel_store.replacement_intent_details(sid)
        if intent is not None:
            intent_id, expected_revision = intent
            try:
                bot = self.channel_store.bot(intent_id)
            except ChannelStoreError:
                raise ChannelRuntimeError("telegram_token_needs_draft") from None
            if int(bot.credential_revision) != int(expected_revision):
                raise ChannelRuntimeError("telegram_token_needs_draft")
            return bot, True, sid
        draft = self.channel_store.current_draft(sid)
        if draft is None:
            raise ChannelRuntimeError("telegram_token_needs_draft")
        # A credential-bearing awaiting-pairing account is a rotation retry,
        # not a fresh install.  Preserve its old credential until the new one
        # is verified and durably CAS-installed.
        return draft, bool(getattr(draft, "credential_id", None)), None

    def prepare_replacement(self, session_id: str):
        sid = self._reserve_owned_mutation(session_id)
        try:
            return self._prepare_replacement_reserved(sid)
        finally:
            self.release_secret_submission(sid)

    def _prepare_replacement_reserved(self, session_id: str):
        sid = self._require_session(session_id, writable=True)
        bot, _binding = self._session_target(sid, writable=True)
        try:
            return self.channel_store.prepare_token_replacement(sid, bot.id)
        except (ChannelConflict, ChannelNotFound, ChannelStoreError, ValueError):
            raise ChannelRuntimeError("telegram_replace_unavailable") from None

    def delivery_belongs_to_session(self, session_id: str, item_id: str) -> bool:
        sid = self._require_session(session_id, writable=True)
        try:
            item = self.channel_store.outbox(item_id)
            binding = self.channel_store.binding(item.binding_id)
            return binding.session_id == sid
        except (ChannelStoreError, ValueError):
            return False

    def record_secret_placeholder(self, session_id: str):
        """Append the only durable Console trace of a direct token submit.

        This method accepts neither a token nor arbitrary text.  The action
        ticket makes a successful endpoint replay impossible, so the append is
        naturally single-use without storing a digest of either secret.
        """
        sid = self._require_session(session_id, writable=True)
        return self.console_store.append_event(
            sid,
            "user_msg",
            text="[Telegram Bot Token 已安全提交]",
            channel_projection="never",
            secret_placeholder=True,
        )

    def create_draft(self, session_id: str, display_name: str):
        sid = self._reserve_owned_mutation(session_id)
        try:
            return self._create_draft_reserved(sid, display_name)
        finally:
            self.release_secret_submission(sid)

    def _create_draft_reserved(self, session_id: str, display_name: str):
        sid = self._require_session(session_id, writable=True)
        if not isinstance(display_name, str) or not display_name.strip():
            raise ChannelRuntimeError("invalid_display_name")
        self.reject_secret_shape(display_name)
        existing = self.channel_store.current_draft(sid)
        if existing is not None:
            return existing
        try:
            bot_id = self.channel_store.create_bot(display_name.strip(), draft_session_id=sid)
            return self.channel_store.bot(bot_id)
        except ChannelConflict:
            raise ChannelRuntimeError("channel_draft_conflict") from None
        except ChannelStoreError:
            raise ChannelRuntimeError("channel_draft_failed") from None

    def _draft_for_token(self, session_id: str, draft_id: str):
        sid = self._require_session(session_id, writable=True)
        if not isinstance(draft_id, str) or not draft_id:
            raise ChannelRuntimeError("telegram_token_needs_draft")
        draft = self.channel_store.current_draft(sid)
        if draft is None or draft.id != draft_id:
            raise ChannelRuntimeError("telegram_token_needs_draft")
        return draft

    def _bot_for_session(self, session_id: str, *, writable: bool = False):
        return self._session_target(session_id, writable=writable)

    async def _get_me(self, token: str):
        result = self.telegram_adapter.get_me(token)
        return await result if inspect.isawaitable(result) else result

    @staticmethod
    def _safe_identity(identity: Any) -> tuple[str, str | None]:
        bot_id = getattr(identity, "bot_id", None)
        username = getattr(identity, "username", None)
        if not isinstance(bot_id, (str, int)) or not str(bot_id):
            raise ChannelRuntimeError("telegram_protocol_error")
        if username is not None and not isinstance(username, str):
            username = None
        if isinstance(username, str) and ChannelRuntime.token_matches(username):
            username = None
        return str(bot_id), username

    def _queue_credential_cleanup(
        self, credential_id: str, *, bot_account_id: str, reason: str
    ) -> bool:
        queue = getattr(self.channel_store, "enqueue_credential_cleanup", None)
        if not callable(queue):
            return False
        try:
            queue(credential_id, bot_account_id=bot_account_id, reason=reason)
            return True
        except Exception:
            return False

    def _complete_credential_cleanup(self, credential_id: str) -> bool:
        complete = getattr(self.channel_store, "complete_credential_cleanup", None)
        if not callable(complete):
            return False
        try:
            complete(credential_id)
            return True
        except Exception:
            return False

    def _cleanup_credential(
        self, credential_id: str, *, bot_account_id: str, reason: str
    ) -> str:
        """Return ``deleted``, ``queued``, or ``failed`` for compensation.

        A queued retry is recoverable for an uncommitted *new* credential, but
        it is still not a completed deletion for a replaced/disconnected
        credential.  Callers must preserve that distinction in the status DTO.
        """
        # Queue first.  Once a Bot stops referencing an old credential there
        # must already be a durable recovery record even if this process dies
        # between DPAPI calls.  The queue stores only an opaque credential ID.
        queued = self._queue_credential_cleanup(
            credential_id, bot_account_id=bot_account_id, reason=reason
        )
        referenced_ids = getattr(self.channel_store, "referenced_credential_ids", None)
        if not callable(referenced_ids):
            return "queued" if queued else "failed"
        try:
            # A stale queue row must never delete the credential currently
            # referenced by a Bot.  Keep the durable obligation visible until
            # its reference is removed by the owning SQLite transaction.
            if credential_id in referenced_ids():
                return "queued" if queued else "failed"
        except Exception:
            return "queued" if queued else "failed"
        try:
            self.secret_store.delete(credential_id)
        except Exception:
            return "queued" if queued else "failed"
        # A stale queue after a successful delete is safe but misleading.  It
        # is removed only after the provider deletion has completed.
        return "deleted" if self._complete_credential_cleanup(credential_id) else "queued"

    def _mark_cleanup_pending(self, bot_account_id: str) -> None:
        # Kept as a compatibility no-op for callers in older integrations.
        # Cleanup is an independent durable obligation in
        # credential_cleanup_queue, never a Bot business status that could
        # overwrite active/awaiting-pairing delivery semantics.
        del bot_account_id

    async def _shield_critical(self, operation: Any) -> Any:
        """Finish secret persistence/compensation even if the request cancels.

        Cancellation remains observable by the caller afterwards, but cannot
        strand a just-created DPAPI ciphertext between ``put`` and its CAS or
        cleanup queue.  The operation itself contains no raw Token in errors.
        """
        task = asyncio.create_task(operation)
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                cancelled = True
                # Keep awaiting the shielded critical operation.  A second
                # cancellation is handled by this loop as well.
                continue
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _run_before_commit(self, callback: Any) -> None:
        if callback is None:
            return
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
        except ChannelRuntimeError:
            raise
        except Exception:
            raise ChannelRuntimeError("channel_session_read_only") from None

    async def _persist_verified_token(
        self,
        session_id: str,
        bot: Any,
        *,
        token: str,
        telegram_bot_id: str,
        username: str | None,
        replacing: bool,
        replacement_intent_session: str | None,
    ):
        """Run put → CAS → compensation as one shielded side-effect unit."""
        try:
            credential_id = await asyncio.to_thread(self.secret_store.put, token)
        except SecretUnavailable:
            raise ChannelRuntimeError("credential_unavailable") from None
        except Exception:
            raise ChannelRuntimeError("credential_unavailable") from None

        old_credential_id = getattr(bot, "credential_id", None)
        try:
            installed = await asyncio.to_thread(
                self.channel_store.cas_bot_credential,
                bot.id,
                expected_revision=bot.credential_revision,
                credential_id=credential_id,
                telegram_bot_id=telegram_bot_id,
                telegram_username=username,
                pause_live_binding_if_identity_changed=bool(replacing),
                expected_replacement_session_id=replacement_intent_session,
            )
        except (ChannelConflict, ChannelNotFound, ChannelStoreError, ValueError):
            cleanup_result = await asyncio.to_thread(
                self._cleanup_credential,
                credential_id,
                bot_account_id=bot.id,
                reason="db_commit_failed",
            )
            if cleanup_result == "failed":
                raise ChannelRuntimeError("credential_cleanup_pending") from None
            raise ChannelRuntimeError("telegram_token_commit_failed") from None
        except Exception:
            cleanup_result = await asyncio.to_thread(
                self._cleanup_credential,
                credential_id,
                bot_account_id=bot.id,
                reason="db_commit_failed",
            )
            if cleanup_result == "failed":
                raise ChannelRuntimeError("credential_cleanup_pending") from None
            raise ChannelRuntimeError("telegram_token_commit_failed") from None

        if (
            replacing
            and bot.telegram_bot_id is not None
            and str(bot.telegram_bot_id) == telegram_bot_id
        ):
            try:
                installed = await asyncio.to_thread(
                    self.channel_store.recover_bot_after_verification,
                    installed.id,
                    expected_credential_revision=installed.credential_revision,
                    expected_telegram_bot_id=telegram_bot_id,
                )
            except Exception:
                # A concurrent replacement wins over a stale recovery.  The
                # credential CAS itself is still durable and safe.
                pass

        if (
            replacing
            and isinstance(old_credential_id, str)
            and old_credential_id
            and old_credential_id != credential_id
        ):
            # Old credential deletion is intentionally after the new CAS.  A
            # failure leaves the new business state intact and a queue-derived
            # warning visible to the user.
            await asyncio.to_thread(
                self._cleanup_credential,
                old_credential_id,
                bot_account_id=installed.id,
                reason="replace_cleanup_failed",
            )
        if replacing and replacement_intent_session is None:
            try:
                await asyncio.to_thread(
                    self.channel_store.clear_token_replacement_intent,
                    session_id,
                    installed.id,
                )
            except Exception:
                # A harmless stale intent is safer than surfacing a concurrent
                # store failure after the credential has already been changed.
                pass
        return installed

    async def _install_verified_token(
        self,
        session_id: str,
        bot: Any,
        token: str,
        *,
        replacing: bool,
        before_commit: Any = None,
        replacement_intent_session: str | None = None,
    ):
        """Verify, CAS-install, then compensate credential lifecycle edges."""
        if not isinstance(token, str) or len(self.token_matches(token)) != 1:
            raise ChannelRuntimeError("telegram_token_invalid")
        exact = self.token_matches(token)[0]
        if token != exact:
            raise ChannelRuntimeError("telegram_token_invalid")
        try:
            identity = await self._get_me(exact)
            telegram_bot_id, username = self._safe_identity(identity)
        except TelegramCredentialInvalid:
            raise ChannelRuntimeError("telegram_credential_invalid") from None
        except TelegramPollConflict:
            raise ChannelRuntimeError("telegram_poll_conflict") from None
        except TelegramTargetUnavailable:
            raise ChannelRuntimeError("telegram_target_unavailable") from None
        except TelegramError:
            raise ChannelRuntimeError("telegram_verification_failed") from None
        except ChannelRuntimeError:
            raise
        except Exception:
            raise ChannelRuntimeError("telegram_verification_failed") from None

        # The Console's reservation/archival recheck lives exactly between
        # remote getMe and local secret persistence.  Runtime callers without a
        # callback still get the same durable writable check here.
        await self._run_before_commit(before_commit)
        await asyncio.to_thread(self.assert_session_mutable, session_id)
        try:
            installed = await self._shield_critical(
                self._persist_verified_token(
                    session_id,
                    bot,
                    token=exact,
                    telegram_bot_id=telegram_bot_id,
                    username=username,
                    replacing=replacing,
                    replacement_intent_session=replacement_intent_session,
                )
            )
        finally:
            # Even a client disconnect can occur after the shielded credential
            # CAS completed.  Reconcile from durable state in every outcome.
            self.pairing.signal()
        return installed

    async def submit_token(
        self, session_id: str, draft_id: str, token: str, *, before_commit: Any = None
    ):
        """Verify first, then persist opaque credential and CAS-install it.

        This is the one deliberate async runtime method: the Bot API call is
        network I/O, while every SQLite/DPAPI operation is isolated in
        ``asyncio.to_thread``.
        """
        def target() -> tuple[Any, bool, str | None]:
            sid = self._require_session(session_id, writable=True)
            bot, replacing, replacement_intent_session = self.token_submission_target(sid)
            if not isinstance(draft_id, str) or bot.id != draft_id:
                raise ChannelRuntimeError("telegram_token_needs_draft")
            return bot, replacing, replacement_intent_session

        draft, replacing, replacement_intent_session = await asyncio.to_thread(target)
        return await self._install_verified_token(
            session_id,
            draft,
            token,
            replacing=replacing,
            before_commit=before_commit,
            replacement_intent_session=replacement_intent_session,
        )

    async def reverify(self, session_id: str):
        # Reservation is a short local metadata read plus in-memory mutex.  Do
        # it synchronously so request cancellation cannot land between a
        # worker-thread acquisition and the finally block that releases it.
        sid = self._reserve_owned_mutation(session_id)
        try:
            return await self._reverify_reserved(sid)
        finally:
            self.release_secret_submission(sid)

    async def _reverify_reserved(self, session_id: str):
        bot, _binding = await asyncio.to_thread(self._bot_for_session, session_id, writable=True)
        if not bot.credential_id:
            await asyncio.to_thread(
                self.channel_store.fault_pause_bot,
                bot.id,
                status=BotStatus.CREDENTIAL_UNAVAILABLE,
                error_code="credential_unavailable",
                expected_credential_revision=bot.credential_revision,
                expected_telegram_bot_id=bot.telegram_bot_id,
            )
            raise ChannelRuntimeError("credential_unavailable")
        try:
            token = await asyncio.to_thread(self.secret_store.get, bot.credential_id)
            identity = await self._get_me(token)
            bot_id, _username = self._safe_identity(identity)
        except SecretUnavailable:
            await asyncio.to_thread(
                self.channel_store.fault_pause_bot,
                bot.id,
                status=BotStatus.CREDENTIAL_UNAVAILABLE,
                error_code="credential_unavailable",
                expected_credential_revision=bot.credential_revision,
                expected_telegram_bot_id=bot.telegram_bot_id,
            )
            raise ChannelRuntimeError("credential_unavailable") from None
        except TelegramCredentialInvalid:
            await asyncio.to_thread(
                self.channel_store.fault_pause_bot,
                bot.id,
                status=BotStatus.CREDENTIAL_INVALID,
                error_code="credential_invalid",
                expected_credential_revision=bot.credential_revision,
                expected_telegram_bot_id=bot.telegram_bot_id,
            )
            raise ChannelRuntimeError("telegram_credential_invalid") from None
        except TelegramPollConflict:
            await asyncio.to_thread(
                self.channel_store.fault_pause_bot,
                bot.id,
                status=BotStatus.POLL_CONFLICT,
                error_code="poll_conflict",
                expected_credential_revision=bot.credential_revision,
                expected_telegram_bot_id=bot.telegram_bot_id,
            )
            raise ChannelRuntimeError("telegram_poll_conflict") from None
        except TelegramTargetUnavailable:
            # A recipient-level condition does not prove a credential fault;
            # retain the durable delivery state for a later retry.
            raise ChannelRuntimeError("telegram_target_unavailable") from None
        except TelegramError:
            raise ChannelRuntimeError("telegram_verification_failed") from None
        except Exception:
            raise ChannelRuntimeError("telegram_verification_failed") from None
        finally:
            token = ""  # best-effort reference shortening only
        if bot.telegram_bot_id is not None and bot.telegram_bot_id != bot_id:
            await asyncio.to_thread(
                self.channel_store.fault_pause_bot,
                bot.id,
                status=BotStatus.PAUSED,
                error_code="telegram_identity_changed",
                expected_credential_revision=bot.credential_revision,
                expected_telegram_bot_id=bot.telegram_bot_id,
            )
            raise ChannelRuntimeError("telegram_identity_changed")
        try:
            await asyncio.to_thread(
                self.channel_store.recover_bot_after_verification,
                bot.id,
                expected_credential_revision=bot.credential_revision,
                expected_telegram_bot_id=bot.telegram_bot_id,
            )
        except Exception:
            # A concurrent replace wins over a stale verifier.  Do not turn a
            # successful getMe into an unsafe broad status mutation.
            pass
        self.pairing.signal()
        return await asyncio.to_thread(self.status_for_session, session_id)

    async def approve_pairing(
        self, session_id: str, pairing_request_id: str, code: str
    ):
        """Approve one current-session private recipient without broad inputs."""
        from guanlan_v2.channels.pairing import PairingError

        self._require_session(session_id, writable=True)
        try:
            return await self.pairing.approve_pairing(
                session_id, pairing_request_id, code
            )
        except PairingError as error:
            raise ChannelRuntimeError(error.reason_code) from None

    async def resolve_pairing_code(self, session_id: str, code: str) -> dict[str, Any]:
        """Return a secret-free preview for the exact scrypt-matched request.

        This performs no approval and persists nothing.  It exists so the
        Console confirmation card names the precise private recipient instead
        of guessing from the two-character display hint when several requests
        are pending.
        """
        from guanlan_v2.channels.pairing import PairingError

        self._require_session(session_id, writable=True)
        try:
            request = await self.pairing.resolve_pairing_code(session_id, code)
        except PairingError as error:
            raise ChannelRuntimeError(error.reason_code) from None
        return {
            "request_id": request.id,
            "user_id": str(request.telegram_user_id),
            "username": self._text(request.telegram_username, limit=64),
            "display_name": self._text(request.telegram_display_name, limit=256),
            "code_hint": self._text(request.code_hint, limit=8),
            "created_at": request.created_at,
            "expires_at": request.expires_at,
        }

    async def approve_pairing_code(self, session_id: str, code: str):
        """Approve by current-session code; Agent supplies no recipient IDs."""
        from guanlan_v2.channels.pairing import PairingError

        self._require_session(session_id, writable=True)
        try:
            return await self.pairing.approve_pairing_code(session_id, code)
        except PairingError as error:
            raise ChannelRuntimeError(error.reason_code) from None

    def dismiss_pairing(self, session_id: str, pairing_request_id: str):
        """Dismiss one pending request scoped by durable session ownership."""
        from guanlan_v2.channels.pairing import PairingError

        self._require_session(session_id, writable=True)
        try:
            return self.pairing.dismiss_pairing(session_id, pairing_request_id)
        except PairingError as error:
            raise ChannelRuntimeError(error.reason_code) from None

    # -- confirmed conversation automations -------------------------
    @staticmethod
    def _automation_safe_text(
        value: object,
        *,
        field: str,
        maximum: int,
        allow_empty: bool = False,
        single_line: bool = False,
    ) -> str:
        """Validate text before it can enter schedule storage or system text."""

        if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
            raise ChannelRuntimeError("automation_spec_invalid")
        if not allow_empty and not value.strip():
            raise ChannelRuntimeError("automation_spec_invalid")
        if single_line and any(char in value for char in ("\r", "\n")):
            raise ChannelRuntimeError("automation_spec_invalid")
        if (
            ChannelRuntime.token_matches(value)
            or _PAIRING_CODE_RE.search(value)
            or _AUTOMATION_CHAT_ID_RE.search(value)
            or _AUTOMATION_LOCAL_PATH_RE.search(value)
        ):
            raise ChannelRuntimeError("automation_sensitive_content_rejected")
        return value

    @staticmethod
    def _automation_id(value: object, *, optional: bool = False) -> str | None:
        if optional and value in (None, ""):
            return None
        if (
            not isinstance(value, str)
            or _AUTOMATION_ID_RE.fullmatch(value) is None
            or ChannelRuntime.token_matches(value)
        ):
            raise ChannelRuntimeError("automation_not_found")
        return value

    @staticmethod
    def _automation_status(item: Any) -> str:
        last_status = str(getattr(item, "last_status", "") or "")
        if not bool(getattr(item, "enabled", False)):
            return "blocked" if last_status == "blocked" else "paused"
        return "error" if last_status == "error" else "enabled"

    def _automation_dto(self, item: Any, *, detailed: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "automation_id": str(item.id),
            "name": str(item.name),
            "schedule_kind": str(item.schedule_kind),
            "run_at": item.run_at,
            "local_time": item.local_time,
            "timezone": str(item.timezone),
            "media_policy": str(item.media_policy),
            "enabled": bool(item.enabled),
            "status": self._automation_status(item),
            "next_run_at": item.next_run_at,
            "last_run_at": item.last_run_at,
            "last_status": self._text(item.last_status, limit=32),
            "pause_reason": self._text(item.pause_reason, limit=96),
        }
        if detailed:
            result.update(
                {
                    "prompt_text": str(item.prompt_text),
                    "skill_refs": [str(ref) for ref in item.skill_refs],
                }
            )
        return result

    def _raise_automation_error(self, error: BaseException) -> None:
        from guanlan_v2.channels.automation import (
            AutomationOwnershipError,
            AutomationValidationError,
        )
        from guanlan_v2.channels.automation_skills import UnknownAutomationSkill
        from guanlan_v2.channels.trading_calendar import CalendarUnavailable

        if isinstance(error, ChannelRuntimeError):
            raise error
        if isinstance(error, AutomationOwnershipError):
            raise ChannelRuntimeError("automation_not_found") from None
        if isinstance(error, UnknownAutomationSkill):
            raise ChannelRuntimeError("automation_skill_unknown") from None
        if isinstance(error, CalendarUnavailable):
            raise ChannelRuntimeError("automation_calendar_unavailable") from None
        if isinstance(error, (AutomationValidationError, ValueError, TypeError)):
            raise ChannelRuntimeError("automation_spec_invalid") from None
        if isinstance(error, (ChannelNotFound, ChannelConflict)):
            raise ChannelRuntimeError("automation_not_found") from None
        if isinstance(error, ChannelStoreError):
            raise ChannelRuntimeError("automation_operation_failed") from None
        raise ChannelRuntimeError("automation_operation_failed") from None

    def automation_skills(self, session_id: str | None = None) -> dict[str, Any]:
        """Expose only stable registry metadata, never executable Skill text."""

        del session_id  # Backwards-compatible tolerance for older tool adapters.
        try:
            skills = self.automation_worker.automation_skills()
            return {
                "ok": True,
                "skills": [
                    {
                        "skill_id": str(skill.skill_id),
                        "name": str(skill.name),
                        "description": str(skill.description),
                    }
                    for skill in skills
                ],
            }
        except Exception as error:
            self._raise_automation_error(error)
            raise AssertionError("unreachable")

    def automation_list(self, session_id: str) -> dict[str, Any]:
        sid = self._require_session(session_id)
        try:
            items = self.automation_worker.list_automations(sid)
            return {
                "ok": True,
                "automations": [
                    self._automation_dto(item, detailed=True) for item in items
                ],
            }
        except Exception as error:
            self._raise_automation_error(error)
            raise AssertionError("unreachable")

    def automation_upsert(
        self, session_id: str, spec: Mapping[str, Any]
    ) -> dict[str, Any]:
        sid = self._reserve_owned_mutation(session_id)
        try:
            return self._automation_upsert_reserved(sid, spec)
        finally:
            self.release_secret_submission(sid)

    def _automation_upsert_reserved(
        self, session_id: str, spec: Mapping[str, Any]
    ) -> dict[str, Any]:
        sid = self._require_session(session_id, writable=True)
        if (
            not isinstance(spec, Mapping)
            or set(spec) - _AUTOMATION_SPEC_KEYS
            or not _AUTOMATION_REQUIRED_KEYS.issubset(spec)
        ):
            raise ChannelRuntimeError("automation_spec_invalid")

        name = self._automation_safe_text(
            spec.get("name"), field="name", maximum=128, single_line=True
        ).strip()
        prompt_text = self._automation_safe_text(
            spec.get("prompt_text"), field="prompt_text", maximum=50_000
        )
        schedule_kind = self._automation_safe_text(
            spec.get("schedule_kind"),
            field="schedule_kind",
            maximum=32,
            single_line=True,
        ).strip()
        timezone_name = self._automation_safe_text(
            spec.get("timezone"), field="timezone", maximum=64, single_line=True
        ).strip()
        run_at = self._automation_safe_text(
            spec.get("run_at", ""),
            field="run_at",
            maximum=96,
            allow_empty=True,
            single_line=True,
        ).strip() or None
        local_time = self._automation_safe_text(
            spec.get("local_time", ""),
            field="local_time",
            maximum=16,
            allow_empty=True,
            single_line=True,
        ).strip() or None
        media_policy = self._automation_safe_text(
            spec.get("media_policy"),
            field="media_policy",
            maximum=32,
            single_line=True,
        ).strip()
        automation_id = self._automation_id(
            spec.get("automation_id"), optional=True
        )
        refs = spec.get("skill_refs", [])
        if (
            not isinstance(refs, (list, tuple))
            or len(refs) > 16
            or any(not isinstance(ref, str) for ref in refs)
        ):
            raise ChannelRuntimeError("automation_spec_invalid")
        safe_refs = tuple(
            self._automation_safe_text(
                ref,
                field="skill_ref",
                maximum=96,
                single_line=True,
            ).strip()
            for ref in refs
        )
        try:
            item = self.automation_worker.upsert_automation(
                session_id=sid,
                name=name,
                schedule_kind=schedule_kind,
                prompt_text=prompt_text,
                timezone_name=timezone_name,
                local_time=local_time,
                run_at=run_at,
                skill_refs=safe_refs,
                media_policy=media_policy,
                automation_id=automation_id,
                created_by="weiwo_agent_confirmed",
            )
            return {
                "ok": True,
                "automation": self._automation_dto(item, detailed=True),
                "automation_id": str(item.id),
            }
        except Exception as error:
            self._raise_automation_error(error)
            raise AssertionError("unreachable")

    def automation_pause(self, session_id: str, automation_id: str) -> dict[str, Any]:
        sid = self._require_session(session_id, writable=True)
        safe_id = self._automation_id(automation_id)
        try:
            item = self.automation_worker.pause_automation(sid, safe_id)
            return {"ok": True, "automation": self._automation_dto(item, detailed=False)}
        except Exception as error:
            self._raise_automation_error(error)
            raise AssertionError("unreachable")

    def automation_resume(self, session_id: str, automation_id: str) -> dict[str, Any]:
        sid = self._reserve_owned_mutation(session_id)
        try:
            return self._automation_resume_reserved(sid, automation_id)
        finally:
            self.release_secret_submission(sid)

    def _automation_resume_reserved(
        self, session_id: str, automation_id: str
    ) -> dict[str, Any]:
        sid = self._require_session(session_id, writable=True)
        safe_id = self._automation_id(automation_id)
        try:
            item = self.automation_worker.resume_automation(sid, safe_id)
            return {"ok": True, "automation": self._automation_dto(item, detailed=False)}
        except Exception as error:
            self._raise_automation_error(error)
            raise AssertionError("unreachable")

    def automation_delete(self, session_id: str, automation_id: str) -> dict[str, Any]:
        sid = self._require_session(session_id, writable=True)
        safe_id = self._automation_id(automation_id)
        try:
            deleted = bool(self.automation_worker.delete_automation(sid, safe_id))
            if not deleted:
                raise ChannelRuntimeError("automation_not_found")
            return {"ok": True, "automation_id": safe_id, "deleted": True}
        except Exception as error:
            self._raise_automation_error(error)
            raise AssertionError("unreachable")

    async def _submit_automation_invocation(self, invocation: Any) -> dict[str, Any]:
        hook = self._console_submit_system_turn
        if hook is None:
            return {"status": "unavailable"}
        try:
            sid = self._require_session(invocation.session_id, writable=True)
            run_id = self._automation_id(invocation.automation_run_id)
            system_text = invocation.system_text
            # Name, Prompt and Skill refs were separately validated before
            # persistence; repeat the secret/location fence on the rendered
            # envelope in case storage was populated by an older caller.  V1
            # deliberately does not embed opaque UUIDs in this text, and its
            # durable automation IDs are segmented below eight characters.
            if (
                not isinstance(system_text, str)
                or not system_text.strip()
                or len(system_text) > 100_000
                or "\x00" in system_text
                or ChannelRuntime.token_matches(system_text)
                or _PAIRING_CODE_RE.search(system_text)
                or _AUTOMATION_CHAT_ID_RE.search(system_text)
                or _AUTOMATION_LOCAL_PATH_RE.search(system_text)
            ):
                return {"status": "unavailable"}
            policy = str(invocation.media_policy)
            if policy not in {"none", "explicit"}:
                return {"status": "unavailable"}
            result = hook(sid, run_id, system_text, policy)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return {"status": "unavailable"}
        if isinstance(result, str) and result.strip():
            return {"status": "started", "turn_id": result.strip()}
        if not isinstance(result, Mapping):
            return {"status": "unavailable"}
        turn_id = result.get("turn_id")
        if result.get("ok") is True and isinstance(turn_id, str) and turn_id:
            return {"status": "started", "turn_id": turn_id}
        reason = str(result.get("reason_code") or "")
        if reason == "busy":
            return {"status": "busy"}
        return {"status": "unavailable"}

    def _automation_session_archived(self, session_id: str) -> bool:
        try:
            self._require_session(session_id, writable=True)
            return False
        except ChannelRuntimeError:
            return True

    def _inspect_automation_terminal(self, run: Any) -> dict[str, Any] | None:
        turn_id = getattr(run, "turn_id", None)
        if not isinstance(turn_id, str) or not turn_id:
            return None
        try:
            automation = self.channel_store.automation(run.automation_id)
            sid = self._require_session(automation.session_id)
        except Exception:
            return None

        reader = getattr(self.console_store, "read_events_forward", None)
        if callable(reader):
            events: list[Mapping[str, Any]] = []
            cursor = 0
            while True:
                page = reader(sid, after_id=cursor, limit=500)
                if not isinstance(page, list) or not page:
                    break
                events.extend(event for event in page if isinstance(event, Mapping))
                ids = [
                    event.get("id")
                    for event in page
                    if isinstance(event, Mapping)
                    and isinstance(event.get("id"), int)
                    and not isinstance(event.get("id"), bool)
                ]
                next_cursor = max(ids, default=cursor)
                if next_cursor <= cursor or len(page) < 500:
                    break
                cursor = next_cursor
        else:
            try:
                raw = self.console_store.read_events(sid, after_id=0, limit=1_000_000)
            except Exception:
                return None
            events = [event for event in raw if isinstance(event, Mapping)]

        saw_evidence = False
        final_event_id: int | None = None
        terminal_event: Mapping[str, Any] | None = None
        for event in events:
            if event.get("type") == "agent_final" and event.get("turn_id") == turn_id:
                saw_evidence = True
                event_id = event.get("id")
                if isinstance(event_id, int) and not isinstance(event_id, bool):
                    final_event_id = event_id
            if (
                event.get("type") == "task_update"
                and event.get("task_id") == turn_id
            ):
                saw_evidence = True
                if event.get("status") == "done":
                    terminal_event = event
        if terminal_event is None:
            return {"terminal": False} if saw_evidence else None
        ok = terminal_event.get("ok") is True
        if ok:
            error_class = None
        elif terminal_event.get("cancelled") is True:
            error_class = "console_turn_cancelled"
        else:
            error_class = "console_turn_failed"
        return {
            "terminal": True,
            "ok": ok,
            "final_event_id": final_event_id,
            "error_class": error_class,
        }

    def bind_console_hooks(
        self,
        *,
        emit: Callable[..., Any],
        is_busy: Callable[[str], Any],
        submit_system_turn: Callable[..., Any],
        on_turn_terminal: Callable[..., Any],
    ) -> None:
        if not all(
            callable(value)
            for value in (emit, is_busy, submit_system_turn, on_turn_terminal)
        ):
            raise TypeError("console automation hooks must be callable")
        self._console_channel_emit = emit
        self._console_is_busy = is_busy
        self._console_submit_system_turn = submit_system_turn
        self._console_on_turn_terminal = on_turn_terminal
        self.automation_worker.bind_hooks(
            is_busy=is_busy,
            submit_system_turn=self._submit_automation_invocation,
            inspect_terminal=self._inspect_automation_terminal,
            is_archived=self._automation_session_archived,
        )
        self._automation_hooks_bound = True

    def automation_turn_terminal(
        self,
        automation_run_id: str,
        turn_id: str,
        ok: bool,
        final_event_id: int | None = None,
        error_class: str | None = None,
    ) -> None:
        safe_run_id = self._automation_id(automation_run_id)
        safe_turn_id = self._automation_id(turn_id)
        if isinstance(final_event_id, bool) or (
            final_event_id is not None
            and (not isinstance(final_event_id, int) or final_event_id < 0)
        ):
            raise ChannelRuntimeError("automation_terminal_invalid")
        if error_class is not None and (
            not isinstance(error_class, str)
            or _SAFE_ACTION_RE.fullmatch(error_class) is None
        ):
            error_class = "console_turn_failed"
        try:
            self.automation_worker.on_turn_terminal(
                safe_run_id,
                safe_turn_id,
                bool(ok),
                final_event_id=final_event_id,
                error_class=error_class,
            )
        except Exception as error:
            self._raise_automation_error(error)

    # -- Console lifecycle and restart fences -----------------------
    @staticmethod
    def _safe_pause_result(result: Any, session_ids: tuple[str, ...]) -> dict[str, Any]:
        payload: Mapping[str, Any] = {}
        converter = getattr(result, "as_safe_dict", None)
        if callable(converter):
            try:
                converted = converter()
                if isinstance(converted, Mapping):
                    payload = converted
            except Exception:
                payload = {}
        safe_sids = payload.get("safe_paused_sids")
        if not isinstance(safe_sids, list) or any(
            not isinstance(item, str) or _SAFE_SID_RE.fullmatch(item) is None
            for item in safe_sids
        ):
            safe_sids = list(session_ids)
        response: dict[str, Any] = {
            "ok": True,
            "safe_paused_sids": list(dict.fromkeys(safe_sids)),
        }
        for name in (
            "paused_binding_count",
            "paused_automation_count",
            "cancelled_outbox_count",
            "uncertain_outbox_count",
            "cancelled_automation_run_count",
        ):
            value = payload.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                response[name] = value
        return response

    def _assert_lifecycle_not_reserved(self, session_ids: tuple[str, ...]) -> None:
        with self._secret_reservation_lock:
            if any(sid in self._secret_reservations for sid in session_ids):
                raise ChannelRuntimeError("channel_lifecycle_busy")
            # This in-process fence closes the check→pause window: a Token
            # handoff cannot reserve after the archive decision but before the
            # Console JSON commit.  The durable archived bit takes over across
            # restarts; restore or an explicit resume releases this marker.
            self._lifecycle_frozen_sessions.update(session_ids)

    def _release_lifecycle_freeze(self, session_ids: tuple[str, ...]) -> None:
        with self._secret_reservation_lock:
            for sid in session_ids:
                self._lifecycle_frozen_sessions.discard(sid)

    def _intentional_pause(
        self, session_ids: tuple[str, ...], *, reason: str
    ) -> dict[str, Any]:
        method = getattr(self.channel_store, "intentional_pause_sessions", None)
        if not callable(method):
            raise ChannelRuntimeError("channel_lifecycle_unavailable")
        try:
            result = method(session_ids, reason=reason)
        except ChannelRuntimeError:
            raise
        except Exception:
            raise ChannelRuntimeError("channel_lifecycle_failed") from None
        return self._safe_pause_result(result, session_ids)

    def prepare_session_archive(self, session_id: str) -> dict[str, Any]:
        """Durably stop one session before Console marks it archived."""
        sid = self._require_session(session_id)
        session_ids = (sid,)
        self._assert_lifecycle_not_reserved(session_ids)
        try:
            return self._intentional_pause(session_ids, reason="archive")
        except Exception:
            self._release_lifecycle_freeze(session_ids)
            raise

    def prepare_workspace_archive(
        self, workspace_id: str, session_ids: Any
    ) -> dict[str, Any]:
        """Freeze every supplied workspace member in one Channel transaction."""
        if (
            not isinstance(workspace_id, str)
            or not workspace_id
            or self.token_matches(workspace_id)
        ):
            raise ChannelRuntimeError("channel_workspace_not_found")
        workspace_store = getattr(self.console_store, "workspace_store", None)
        require = getattr(workspace_store, "require", None)
        if not callable(require):
            raise ChannelRuntimeError("channel_lifecycle_unavailable")
        try:
            require(workspace_id, active_only=False)
        except Exception:
            raise ChannelRuntimeError("channel_workspace_not_found") from None
        if isinstance(session_ids, (str, bytes)):
            raw_session_ids = (session_ids,)
        else:
            try:
                raw_session_ids = tuple(session_ids)
            except TypeError:
                raise ChannelRuntimeError("channel_lifecycle_invalid") from None
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_sid in raw_session_ids:
            sid = self._require_session(raw_sid)
            meta = self.console_store.get_meta(sid)
            if not isinstance(meta, Mapping) or str(
                meta.get("workspace_id") or GENERAL_WORKSPACE_ID
            ) != workspace_id:
                raise ChannelRuntimeError("channel_workspace_session_mismatch")
            if sid not in seen:
                seen.add(sid)
                normalized.append(sid)
        sids = tuple(normalized)
        self._assert_lifecycle_not_reserved(sids)
        try:
            return self._intentional_pause(sids, reason="workspace_archived")
        except Exception:
            self._release_lifecycle_freeze(sids)
            raise

    def assert_session_delete_allowed(self, session_id: str) -> dict[str, Any]:
        """Return secret-free durable blockers instead of guessing cleanup."""
        sid = self._require_session(session_id)
        blockers: list[str] = []
        try:
            # Freeze new drafts, Token/reverify work, resumes and automation
            # mutations before taking the blocker snapshot.  A clean result
            # keeps this marker until Console removes the session; a blocked or
            # failed result releases it so the user can resolve the blockers.
            self._assert_lifecycle_not_reserved((sid,))
        except ChannelRuntimeError as error:
            if error.reason_code == "channel_lifecycle_busy":
                return {
                    "ok": False,
                    "reason_code": "channel_session_delete_blocked",
                    "blockers": ["channel_mutation"],
                }
            raise
        method = getattr(self.channel_store, "hard_delete_blockers", None)
        if not callable(method):
            self._release_lifecycle_freeze((sid,))
            raise ChannelRuntimeError("channel_lifecycle_unavailable")
        try:
            result = method(sid)
            converter = getattr(result, "as_safe_dict", None)
            payload = converter() if callable(converter) else {}
        except Exception:
            self._release_lifecycle_freeze((sid,))
            raise ChannelRuntimeError("channel_lifecycle_failed") from None
        if isinstance(payload, Mapping) and isinstance(payload.get("blockers"), list):
            for item in payload["blockers"]:
                if not isinstance(item, Mapping):
                    continue
                kind = item.get("kind")
                count = item.get("count")
                if (
                    not isinstance(kind, str)
                    or _SAFE_ACTION_RE.fullmatch(kind) is None
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 1
                ):
                    continue
                blockers.append(kind)
        if blockers:
            self._release_lifecycle_freeze((sid,))
            return {
                "ok": False,
                "reason_code": "channel_session_delete_blocked",
                "blockers": list(dict.fromkeys(blockers)),
            }
        return {"ok": True, "blockers": []}

    def on_session_restore(self, session_id: str) -> dict[str, Any]:
        """Restore Console visibility only; delivery remains intentionally paused."""
        sid = self._require_session(session_id)
        self._release_lifecycle_freeze((sid,))
        return {
            "ok": True,
            "session_id": sid,
            "channel_remains_paused": True,
        }

    def _startup_archive_fence(self) -> dict[str, Any]:
        """Reconcile archives created while the Telegram feature was disabled."""
        list_sessions = getattr(self.console_store, "list_sessions", None)
        if not callable(list_sessions):
            # Narrow test facades predate workspace lifecycle.  The production
            # ConsoleStore always supplies this method.
            return {"safe_paused_sids": []}
        try:
            raw_sessions = list_sessions()
            sessions = [item for item in raw_sessions if isinstance(item, Mapping)]
            workspace_store = getattr(self.console_store, "workspace_store", None)
            list_workspaces = getattr(workspace_store, "list", None)
            archived_workspaces: set[str] = set()
            if callable(list_workspaces):
                archived_workspaces = {
                    str(item.get("id"))
                    for item in list_workspaces(include_archived=True)
                    if isinstance(item, Mapping)
                    and bool(item.get("archived"))
                    and isinstance(item.get("id"), str)
                }
        except Exception:
            raise ChannelRuntimeError("channel_lifecycle_scan_failed") from None

        workspace_groups: dict[str, list[str]] = {}
        archived_sessions: list[str] = []
        for meta in sessions:
            sid = meta.get("id")
            if (
                not isinstance(sid, str)
                or _SAFE_SID_RE.fullmatch(sid) is None
                or self.token_matches(sid)
            ):
                continue
            workspace_id = str(meta.get("workspace_id") or GENERAL_WORKSPACE_ID)
            if workspace_id in archived_workspaces:
                workspace_groups.setdefault(workspace_id, []).append(sid)
            elif bool(meta.get("archived")):
                archived_sessions.append(sid)

        paused: list[str] = []
        for workspace_id in sorted(workspace_groups):
            sids = tuple(dict.fromkeys(workspace_groups[workspace_id]))
            result = self._intentional_pause(sids, reason="workspace_archived")
            paused.extend(result["safe_paused_sids"])
        direct_sids = tuple(dict.fromkeys(archived_sessions))
        if direct_sids:
            result = self._intentional_pause(direct_sids, reason="archive")
            paused.extend(result["safe_paused_sids"])
        return {"safe_paused_sids": list(dict.fromkeys(paused))}

    async def _fault_startup_bot(
        self, bot: Any, *, status: BotStatus, error_code: str
    ) -> None:
        try:
            await asyncio.to_thread(
                self.channel_store.fault_pause_bot,
                bot.id,
                status=status,
                error_code=error_code,
                expected_credential_revision=bot.credential_revision,
                expected_telegram_bot_id=bot.telegram_bot_id,
            )
        except ChannelNotFound:
            # A concurrent hard removal/disconnect owns the newer generation.
            return
        except ChannelConflict:
            # A stale verifier may lose to a newer credential CAS.  Continue
            # only if the fresh generation is already non-deliverable; an
            # active/awaiting replacement must be verified by a fresh startup.
            try:
                fresh = await asyncio.to_thread(self.channel_store.bot, bot.id)
            except ChannelNotFound:
                return
            except Exception:
                raise ChannelRuntimeError("channel_registry_unavailable") from None
            if fresh.status in {
                BotStatus.DISCONNECTED,
                BotStatus.PAUSED,
                BotStatus.POLL_CONFLICT,
                BotStatus.CREDENTIAL_INVALID,
                BotStatus.CREDENTIAL_UNAVAILABLE,
            }:
                return
            raise ChannelRuntimeError("channel_registry_changed") from None
        except Exception:
            # Detecting an invalid credential or changed identity and then
            # failing to persist the pause is a startup-fatal condition.
            raise ChannelRuntimeError("channel_registry_unavailable") from None

    async def _startup_reverify_bot(self, bot: Any) -> None:
        credential_id = getattr(bot, "credential_id", None)
        if not isinstance(credential_id, str) or _CREDENTIAL_ID_RE.fullmatch(
            credential_id
        ) is None:
            await self._fault_startup_bot(
                bot,
                status=BotStatus.CREDENTIAL_UNAVAILABLE,
                error_code="credential_unavailable",
            )
            return
        token = ""
        try:
            try:
                token = await asyncio.to_thread(self.secret_store.get, credential_id)
            except Exception:
                await self._fault_startup_bot(
                    bot,
                    status=BotStatus.CREDENTIAL_UNAVAILABLE,
                    error_code="credential_unavailable",
                )
                return
            try:
                identity = await self._get_me(token)
                bot_id, _username = self._safe_identity(identity)
            except TelegramCredentialInvalid:
                await self._fault_startup_bot(
                    bot,
                    status=BotStatus.CREDENTIAL_INVALID,
                    error_code="credential_invalid",
                )
                return
            except TelegramPollConflict:
                await self._fault_startup_bot(
                    bot,
                    status=BotStatus.POLL_CONFLICT,
                    error_code="poll_conflict",
                )
                return
            except TelegramError:
                # Preserve the permanent binding but fail closed for delivery
                # until an explicit successful reverify proves the identity.
                await self._fault_startup_bot(
                    bot,
                    status=BotStatus.CREDENTIAL_UNAVAILABLE,
                    error_code="credential_unavailable",
                )
                return
            except Exception:
                await self._fault_startup_bot(
                    bot,
                    status=BotStatus.CREDENTIAL_UNAVAILABLE,
                    error_code="credential_unavailable",
                )
                return
            if bot.telegram_bot_id is not None and str(bot.telegram_bot_id) != bot_id:
                await self._fault_startup_bot(
                    bot,
                    status=BotStatus.PAUSED,
                    error_code="telegram_identity_changed",
                )
                return
            try:
                await asyncio.to_thread(
                    self.channel_store.recover_bot_after_verification,
                    bot.id,
                    expected_credential_revision=bot.credential_revision,
                    expected_telegram_bot_id=bot.telegram_bot_id,
                )
            except Exception:
                return
        finally:
            token = ""

    async def _startup_reverify_bots(
        self, *, skip_session_ids: set[str] | None = None
    ) -> int:
        if not callable(getattr(self.telegram_adapter, "get_me", None)):
            return 0
        skipped = skip_session_ids or set()
        try:
            bots = await asyncio.to_thread(self.channel_store.bots)
        except Exception:
            raise ChannelRuntimeError("channel_registry_unavailable") from None
        candidates = [
            bot
            for bot in bots
            if getattr(bot, "status", None) in _STARTUP_REVERIFY_STATUSES
            and getattr(bot, "draft_session_id", None) not in skipped
            and getattr(bot, "last_error_code", None) != "session_archived"
        ]
        for bot in candidates:
            await self._startup_reverify_bot(bot)
        if candidates:
            try:
                self.pairing.signal()
            except Exception:
                pass
        return len(candidates)

    def _wall_time_seconds(self) -> float:
        value = self._wall_clock()
        timestamp = getattr(value, "timestamp", None)
        return float(timestamp()) if callable(timestamp) else float(value)

    async def reconcile_credential_store(self) -> int:
        """Delete only old, legal, unreferenced ciphertext metadata entries."""
        iter_metadata = getattr(self.secret_store, "iter_metadata", None)
        referenced_ids = getattr(self.channel_store, "referenced_credential_ids", None)
        delete = getattr(self.secret_store, "delete", None)
        if not callable(iter_metadata) or not callable(referenced_ids) or not callable(delete):
            return 0
        try:
            referenced = await asyncio.to_thread(referenced_ids)
            metadata = await asyncio.to_thread(lambda: list(iter_metadata()))
            now = self._wall_time_seconds()
        except Exception:
            return 0
        deleted = 0
        for item in metadata:
            credential_id = getattr(item, "credential_id", None)
            mtime = getattr(item, "mtime", None)
            if (
                not isinstance(credential_id, str)
                or _CREDENTIAL_ID_RE.fullmatch(credential_id) is None
                or credential_id in referenced
                or not isinstance(mtime, (int, float))
                or isinstance(mtime, bool)
                or now - float(mtime) <= self._credential_orphan_grace_seconds
            ):
                continue
            try:
                # Re-read references immediately before deletion.  Combined
                # with the one-hour grace, this closes the put-before-CAS race
                # without ever enumerating or decrypting plaintext Tokens.
                current_references = await asyncio.to_thread(referenced_ids)
                if credential_id in current_references:
                    continue
                await asyncio.to_thread(delete, credential_id)
            except Exception:
                continue
            deleted += 1
        return deleted

    async def _maintenance_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._maintenance_interval_seconds
                )
            except asyncio.TimeoutError:
                try:
                    await self.retry_credential_cleanup()
                    await self.reconcile_credential_store()
                except Exception:
                    # Maintenance is deliberately best-effort and fail-closed:
                    # failed cleanup retains ciphertext/queue records for retry.
                    continue

    async def start(self) -> None:
        """Start background channel services for the application lifespan."""
        if self._background_lock is None:
            self._background_lock = asyncio.Lock()
        async with self._background_lock:
            if self._adapter_closed:
                raise ChannelRuntimeError("channel_runtime_closed")
            if self._background_tasks:
                maintenance_healthy = (
                    self._maintenance_task is None
                    or not self._maintenance_task.done()
                )
                if (
                    all(not task.done() for task in self._background_tasks)
                    and maintenance_healthy
                ):
                    return
                # A partially failed generation is not healthy.  Settle the
                # survivors before rebuilding all loops as one generation.
                if self._background_stop is not None:
                    self._background_stop.set()
                for task in self._background_tasks:
                    task.cancel()
                if self._maintenance_task is not None:
                    self._maintenance_task.cancel()
                await asyncio.gather(
                    *self._background_tasks,
                    *(() if self._maintenance_task is None else (self._maintenance_task,)),
                    return_exceptions=True,
                )
                self._background_tasks = ()
                self._background_stop = None
                self._maintenance_task = None
                cleanup_failed = False
                if self._automation_started:
                    try:
                        await self.automation_worker.stop()
                    except Exception:
                        cleanup_failed = True
                    else:
                        self._automation_started = False
                if self._pairing_started:
                    try:
                        await self.pairing.stop()
                    except Exception:
                        cleanup_failed = True
                    else:
                        self._pairing_started = False
                try:
                    await asyncio.to_thread(
                        self.channel_store.recover_sending_outbox,
                        error_class="restart_recovery",
                    )
                except Exception:
                    cleanup_failed = True
                if cleanup_failed:
                    raise ChannelRuntimeError("channel_restart_cleanup_failed")

            try:
                # Feature-off periods may contain newly archived Console state.
                # Fence it before recovery, verification, polling or claims.
                archive_result = await asyncio.to_thread(
                    self._startup_archive_fence
                )
                archived_sids = set(archive_result.get("safe_paused_sids", []))

                # Resolve every ambiguous pre-crash HTTP attempt before any new
                # delivery claim is possible.
                await self.outbox_worker.startup_recover(force=True)
                await self.retry_credential_cleanup()
                await self.reconcile_credential_store()
                await self._startup_reverify_bots(
                    skip_session_ids=archived_sids
                )

                self._pairing_started = True
                await self.pairing.start()
                if self._automation_hooks_bound:
                    self._automation_started = True
                    await self.automation_worker.start()

                stop_event = asyncio.Event()
                self._background_stop = stop_event
                self._background_tasks = (
                    asyncio.create_task(
                        self.projector.run_forever(stop_event),
                        name="guanlan-channel-projector",
                    ),
                    asyncio.create_task(
                        self.outbox_worker.run_forever(stop_event),
                        name="guanlan-channel-outbox",
                    ),
                )
                self._maintenance_task = asyncio.create_task(
                    self._maintenance_loop(stop_event),
                    name="guanlan-channel-maintenance",
                )
            except BaseException:
                # A failed start never leaves a poller, scheduler or ambiguous
                # delivery claim behind.  Keep the adapter open so lifespan
                # construction may be retried deliberately.
                if self._background_stop is not None:
                    self._background_stop.set()
                tasks = self._background_tasks
                maintenance = self._maintenance_task
                self._background_stop = None
                self._background_tasks = ()
                self._maintenance_task = None
                for task in tasks:
                    task.cancel()
                if maintenance is not None:
                    maintenance.cancel()
                if tasks or maintenance is not None:
                    await asyncio.gather(
                        *tasks,
                        *(() if maintenance is None else (maintenance,)),
                        return_exceptions=True,
                    )
                if self._automation_started:
                    try:
                        await self.automation_worker.stop()
                    except Exception:
                        pass
                    else:
                        self._automation_started = False
                if self._pairing_started:
                    try:
                        await self.pairing.stop()
                    except Exception:
                        pass
                    else:
                        self._pairing_started = False
                try:
                    await asyncio.to_thread(
                        self.channel_store.recover_sending_outbox,
                        error_class="startup_recovery",
                    )
                except Exception:
                    pass
                raise

    async def stop(self) -> None:
        """Finish the shutdown fence before making cancellation observable."""
        task = asyncio.create_task(self._stop_once())
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.done() and task.cancelled():
                    raise
                cancelled = True
                continue
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _stop_once(self) -> None:
        """Stop every subsystem even when an earlier shutdown step fails."""
        if self._background_lock is None:
            self._background_lock = asyncio.Lock()
        async with self._background_lock:
            stop_event = self._background_stop
            tasks = self._background_tasks
            maintenance = self._maintenance_task
            self._background_stop = None
            self._background_tasks = ()
            self._maintenance_task = None
            if stop_event is not None:
                stop_event.set()
            for task in tasks:
                task.cancel()
            if maintenance is not None:
                maintenance.cancel()

            failed = False
            cancelled = False

            async def settle(operation: Any) -> bool:
                nonlocal failed, cancelled
                try:
                    result = operation()
                    if inspect.isawaitable(result):
                        await result
                    return True
                except asyncio.CancelledError:
                    cancelled = True
                    return False
                except Exception:
                    failed = True
                    return False

            if self._automation_started:
                if await settle(self.automation_worker.stop):
                    self._automation_started = False

            if tasks or maintenance is not None:
                await settle(
                    lambda: asyncio.gather(
                        *tasks,
                        *(() if maintenance is None else (maintenance,)),
                        return_exceptions=True,
                    )
                )

            # Final shutdown fence: every claim coroutine has settled, so no
            # pre-crash ``sending`` row can be blindly resent on restart.
            await settle(
                lambda: asyncio.to_thread(
                    self.channel_store.recover_sending_outbox,
                    error_class="shutdown_recovery",
                )
            )

            if self._pairing_started:
                if await settle(self.pairing.stop):
                    self._pairing_started = False

            close = getattr(self.telegram_adapter, "aclose", None)
            if not callable(close):
                close = getattr(self.telegram_adapter, "close", None)
            if callable(close) and not self._adapter_closed:
                self._adapter_closed = await settle(close)

            if cancelled:
                raise asyncio.CancelledError
            if failed:
                raise ChannelRuntimeError("channel_shutdown_failed")

    async def replace_token(
        self, session_id: str, draft_id: str, token: str, *, before_commit: Any = None
    ):
        def target() -> tuple[Any, str | None]:
            sid = self._require_session(session_id, writable=True)
            bot, _binding = self._bot_for_session(sid, writable=True)
            if not isinstance(draft_id, str) or bot.id != draft_id:
                raise ChannelRuntimeError("telegram_token_needs_draft")
            intent = self.channel_store.replacement_intent_details(sid)
            intent_session = (
                sid
                if intent is not None
                and intent[0] == bot.id
                and int(intent[1]) == int(bot.credential_revision)
                else None
            )
            return bot, intent_session

        bot, replacement_intent_session = await asyncio.to_thread(target)
        return await self._install_verified_token(
            session_id,
            bot,
            token,
            replacing=True,
            before_commit=before_commit,
            replacement_intent_session=replacement_intent_session,
        )

    def pause(self, session_id: str):
        self._require_session(session_id, writable=True)
        try:
            self.channel_store.set_session_manual_pause(session_id, paused=True)
        except (ChannelStoreError, ValueError, ChannelConflict):
            raise ChannelRuntimeError("channel_pause_failed") from None
        return self.status_for_session(session_id)

    def resume(self, session_id: str):
        sid = self._reserve_owned_mutation(session_id)
        try:
            return self._resume_reserved(sid)
        finally:
            self.release_secret_submission(sid)

    def _resume_reserved(self, session_id: str):
        self._require_session(session_id, writable=True)
        try:
            # ``read_events`` has tail semantics.  Resetting both Channel
            # cursors to the actual persisted tail in the same SQLite
            # transaction that activates the binding prevents paused-period
            # messages from being backfilled.
            console_lock = getattr(self.console_store, "_lock", None)
            with console_lock if hasattr(console_lock, "__enter__") else nullcontext():
                tail = self.console_store.read_events(session_id, after_id=0, limit=1)
                tail_event_id = 0
                if tail:
                    candidate = tail[-1].get("id")
                    if isinstance(candidate, int) and not isinstance(candidate, bool):
                        tail_event_id = max(0, candidate)
                self.channel_store.set_session_manual_pause(
                    session_id,
                    paused=False,
                    resume_from_event_id=tail_event_id,
                )
        except (ChannelConflict, InvalidTransition):
            raise ChannelRuntimeError("channel_resume_not_allowed") from None
        except (ChannelStoreError, ValueError):
            raise ChannelRuntimeError("channel_resume_failed") from None
        self._release_lifecycle_freeze((session_id,))
        return self.status_for_session(session_id)

    def disconnect(self, session_id: str):
        self._require_session(session_id, writable=True)
        try:
            bot, old_credential_id = self.channel_store.disconnect_session(session_id)
            if old_credential_id:
                self._cleanup_credential(
                    old_credential_id,
                    bot_account_id=bot.id,
                    reason="disconnect_cleanup_failed",
                )
        except (ChannelStoreError, ValueError, ChannelConflict):
            raise ChannelRuntimeError("channel_disconnect_failed") from None
        self._release_lifecycle_freeze((session_id,))
        self.pairing.signal()
        return self.status_for_session(session_id)

    def _media_publication_generation(self, session_id: str):
        sid = self._require_session(session_id, writable=True)
        try:
            binding = self.channel_store.binding_for_session(sid, live_only=True)
            if binding is None:
                raise ChannelRuntimeError("channel_not_connected")
            if binding.status is not BindingStatus.ACTIVE:
                raise ChannelRuntimeError("channel_paused")
            bot = self.channel_store.bot(binding.bot_account_id)
            if bot.status is not BotStatus.ACTIVE:
                raise ChannelRuntimeError("channel_not_active")
        except ChannelRuntimeError:
            raise
        except (ChannelStoreError, ValueError):
            raise ChannelRuntimeError("channel_not_connected") from None
        return sid, binding

    def _validate_media_publication_input(
        self, media_id: object, caption: object,
    ) -> tuple[str, str]:
        if not isinstance(media_id, str) or MEDIA_ID_RE.fullmatch(media_id) is None:
            raise ChannelRuntimeError("telegram_media_not_found")
        if (
            not isinstance(caption, str)
            or len(caption) > _MAX_MEDIA_CAPTION_CHARS
            or "\x00" in caption
        ):
            raise ChannelRuntimeError("telegram_caption_invalid")
        if (
            self.token_matches(caption)
            or _PAIRING_CODE_RE.search(caption)
            or caption_contains_location(caption)
        ):
            raise ChannelRuntimeError("telegram_caption_rejected")
        return media_id, caption

    def media_publication_confirmation_preview(
        self,
        session_id: str,
        media_id: str,
        caption: str = "",
    ) -> dict[str, Any]:
        """Freeze the exact recipient generation rendered on a confirm card."""
        self._validate_media_publication_input(media_id, caption)
        _sid, binding = self._media_publication_generation(session_id)
        return {
            "binding_id": binding.id,
            "binding_paired_at": binding.paired_at,
            "recipient": {
                "user_id": str(binding.telegram_user_id),
                "username": self._text(binding.telegram_username, limit=64),
                "display_name": self._text(
                    binding.telegram_display_name, limit=256
                ),
            },
        }

    def publish_media(
        self,
        session_id: str,
        media_id: str,
        caption: str = "",
        confirmation_ticket: str = "",
    ) -> dict[str, Any]:
        """Authorize one current-session MediaStore image for projection.

        This method deliberately does not enqueue or send anything.  Its exact
        artifact is persisted by Console only after the Agent tool's ordinary
        confirmation gate succeeds; the projector then creates the durable
        outbox batch.  The worker re-loads the same opaque id before delivery.
        """
        media_id, caption = self._validate_media_publication_input(
            media_id, caption
        )

        # This proof is minted by Console only after the user approves the
        # exact media-id/caption pair.  It is consumed before any mutable state
        # or private media access, and never enters an event, artifact, or DB.
        sid, binding = self._media_publication_generation(session_id)
        confirmation_subject = media_confirmation_subject(
            media_id,
            caption,
            binding.id,
            binding.paired_at,
        )
        if not self.ticket_store.consume(
            confirmation_ticket,
            session_id,
            _MEDIA_CONFIRM_ACTION,
            confirmation_subject,
        ):
            raise ChannelRuntimeError("telegram_publish_confirmation_required")
        loader = getattr(self.media_store, "load_for_session", None)
        if not callable(loader):
            raise ChannelRuntimeError("channel_capability_unavailable")
        try:
            # Validation reads immutable private bytes now.  Bytes and paths
            # never enter this return value or Channel storage.
            loader(media_id, sid)
        except Exception:
            raise ChannelRuntimeError("telegram_media_not_found") from None

        try:
            permit_id = self.channel_store.create_media_publish_permit(
                sid,
                media_id,
                caption_sha256(caption),
                expected_binding_id=binding.id,
                expected_paired_at=binding.paired_at,
                ttl_seconds=_MEDIA_PERMIT_TTL_SECONDS,
            )
        except (ChannelStoreError, ValueError):
            # The active binding/bot generation is atomically rechecked after
            # the potentially slow MediaStore read.  A concurrent pause,
            # disconnect, or recipient change therefore creates no authority.
            raise ChannelRuntimeError("channel_not_active") from None

        artifact = {
            "kind": "telegram_media_publish",
            "page": None,
            "channel": "telegram",
            "payload": {"media_id": media_id, "caption": caption},
            "ref": permit_id,
        }
        return {
            "ok": True,
            "content": "图片已通过当前对话所有权校验，等待可靠投递。",
            "artifact": artifact,
        }

    def issue_media_publication_confirmation(
        self,
        session_id: str,
        media_id: str,
        caption: str = "",
        expected_binding_id: str = "",
        expected_paired_at: str = "",
    ) -> str:
        """Mint one in-memory proof for an already user-approved exact call."""
        media_id, caption = self._validate_media_publication_input(
            media_id, caption
        )
        sid, binding = self._media_publication_generation(session_id)
        if (
            not isinstance(expected_binding_id, str)
            or not isinstance(expected_paired_at, str)
            or not expected_binding_id
            or not expected_paired_at
            or not hmac.compare_digest(binding.id, expected_binding_id)
            or not hmac.compare_digest(binding.paired_at, expected_paired_at)
        ):
            raise ChannelRuntimeError("telegram_recipient_changed")
        return self.ticket_store.issue(
            sid,
            _MEDIA_CONFIRM_ACTION,
            media_confirmation_subject(
                media_id,
                caption,
                binding.id,
                binding.paired_at,
            ),
        )

    def resolve_delivery(self, session_id: str, item_id: str, action: str):
        self._require_session(session_id, writable=True)
        if action not in {"assume_sent", "resend", "drop", "retry"}:
            raise ChannelRuntimeError("invalid_delivery_resolution")
        try:
            item = self.channel_store.outbox(item_id)
            binding = self.channel_store.binding(item.binding_id)
            if binding.session_id != session_id:
                raise ChannelRuntimeError("channel_delivery_not_found")
            self.channel_store.resolve_outbox(item.id, action)
        except ChannelRuntimeError:
            raise
        except (ChannelStoreError, ValueError):
            raise ChannelRuntimeError("channel_delivery_not_found") from None
        return self.status_for_session(session_id)

    async def retry_credential_cleanup(self) -> int:
        """Retry queued opaque deletions and remove only completed obligations."""
        try:
            items = await asyncio.to_thread(self.channel_store.credential_cleanup_items)
        except Exception:
            return 0
        completed = 0
        for credential_id, bot_account_id, reason in items:
            result = await self._shield_critical(
                asyncio.to_thread(
                    self._cleanup_credential,
                    credential_id,
                    bot_account_id=bot_account_id or "bot_cleanup",
                    reason=reason,
                )
            )
            if result == "deleted":
                completed += 1
        return completed

    # -- redacted DTO -------------------------------------------------
    @staticmethod
    def _text(value: object, *, limit: int = 256) -> str | None:
        if not isinstance(value, str):
            return None
        # Inspect before truncation: a secret just beyond the visible boundary
        # is still an invalid source field, not a reason to treat it as safe.
        original = value.strip()
        if not original or ChannelRuntime.token_matches(original):
            return None
        return original[:limit]

    def _automations_for_session(self, session_id: str) -> list[Any]:
        method = getattr(self.channel_store, "automations_for_session", None)
        if callable(method):
            return list(method(session_id))
        # Task 1 intentionally shipped only single-record automation access.
        # Use its own configured connection only as a temporary compatibility
        # reader, returning no free-form payload beyond the stable DTO fields.
        connect = getattr(self.channel_store, "connect", None)
        get_one = getattr(self.channel_store, "automation", None)
        if not callable(connect) or not callable(get_one):
            return []
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT id FROM automations WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        except Exception:
            return []
        finally:
            conn.close()
        result: list[Any] = []
        for row in rows:
            try:
                result.append(get_one(row[0]))
            except Exception:
                continue
        return result

    def status_for_session(self, session_id: str) -> dict[str, Any]:
        sid = self._require_session(session_id)
        draft = self.channel_store.current_draft(sid)
        live_binding = self.channel_store.binding_for_session(sid, live_only=True)
        historical_binding = None
        binding = live_binding
        bot = None
        if live_binding is not None:
            try:
                bot = self.channel_store.bot(live_binding.bot_account_id)
            except ChannelStoreError:
                bot = None
        if bot is None:
            bot = draft
        if bot is None:
            historical_binding = self.channel_store.binding_for_session(sid, live_only=False)
            binding = historical_binding
            if historical_binding is not None:
                try:
                    bot = self.channel_store.bot(historical_binding.bot_account_id)
                except ChannelStoreError:
                    bot = None

        replacement_target_id = None
        try:
            replacement_target_id = self.channel_store.replacement_intent_for_session(sid)
        except Exception:
            replacement_target_id = None
        replacement_bot = None
        if replacement_target_id is not None:
            try:
                replacement_bot = self.channel_store.bot(replacement_target_id)
            except ChannelStoreError:
                replacement_target_id = None
        # An intent overlays the UI's secure Token card but never replaces the
        # live delivery target.  Its local bot ID is intentionally the only
        # externally visible replacement handle.
        display_draft = replacement_bot or draft

        pending_requests: list[Any] = []
        pairing_bot = draft or replacement_bot
        if pairing_bot is not None:
            try:
                pending_requests = self.channel_store.pairing_requests_for(
                    pairing_bot.id, status=PairingStatus.PENDING
                )
            except ChannelStoreError:
                pending_requests = []

        deliveries: list[Any] = []
        try:
            session_reader = getattr(self.channel_store, "outbox_for_session", None)
            if callable(session_reader):
                deliveries = session_reader(sid)
            elif binding is not None:
                deliveries = self.channel_store.outbox_for(binding.id)
        except ChannelStoreError:
            deliveries = []
        counts = {
            state: sum(1 for item in deliveries if getattr(item.state, "value", item.state) == state)
            for state in ("pending", "retry_wait", "uncertain", "terminal")
        }
        warnings: list[dict[str, str]] = []
        bot_status = getattr(bot.status, "value", bot.status) if bot is not None else None
        safe_last_error = self._text(getattr(bot, "last_error_code", None), limit=96)
        binding_pause_reason = self._text(
            getattr(live_binding, "pause_reason", None), limit=96
        )
        identity_changed = (
            safe_last_error == "telegram_identity_changed"
            or binding_pause_reason in {"bot_identity_changed", "telegram_identity_changed"}
        )
        if identity_changed:
            warnings.append({"error_code": "telegram_identity_changed"})
        if bot_status in {
            BotStatus.CREDENTIAL_INVALID.value,
            BotStatus.CREDENTIAL_UNAVAILABLE.value,
            BotStatus.POLL_CONFLICT.value,
        }:
            warnings.append({"error_code": safe_last_error or str(bot_status)})
        cleanup_pending = False
        if bot is not None:
            try:
                cleanup_pending = bool(self.channel_store.credential_cleanup_for_bot(bot.id))
            except Exception:
                cleanup_pending = False
        if cleanup_pending or bot_status == BotStatus.CREDENTIAL_CLEANUP_PENDING.value:
            warnings.append({"error_code": "credential_cleanup_pending"})

        binding_status = getattr(live_binding.status, "value", live_binding.status) if live_binding else None
        if bot_status in {
            BotStatus.CREDENTIAL_INVALID.value,
            BotStatus.CREDENTIAL_UNAVAILABLE.value,
            BotStatus.POLL_CONFLICT.value,
        } or identity_changed:
            view_state = "needs_reconnect"
        elif counts["uncertain"]:
            view_state = "uncertain"
        elif counts["terminal"]:
            view_state = "needs_reconnect"
        elif counts["retry_wait"]:
            view_state = "retrying"
        elif binding_status == BindingStatus.PAUSED.value or bot_status == BotStatus.PAUSED.value:
            view_state = "paused"
        elif pending_requests:
            view_state = "pairing_pending"
        elif bot_status == BotStatus.AWAITING_PAIRING.value:
            view_state = "awaiting_pairing"
        elif replacement_bot is not None or bot_status == BotStatus.DRAFT.value:
            view_state = "awaiting_token"
        elif binding_status == BindingStatus.ACTIVE.value:
            view_state = "active"
        elif bot_status == BotStatus.DISCONNECTED.value and counts["terminal"]:
            # A clean disconnect is intentionally quiet, but unresolved
            # terminal deliveries still require a human decision.
            view_state = "needs_reconnect"
        else:
            view_state = "none"

        recipient = None
        if live_binding is not None:
            recipient = {
                "user_id": str(live_binding.telegram_user_id),
                "username": self._text(live_binding.telegram_username, limit=64),
                "display_name": self._text(live_binding.telegram_display_name, limit=256),
            }
        visible_deliveries = _status_delivery_window(deliveries)
        recent = [
            {
                "outbox_id": item.id,
                "kind": getattr(item.kind, "value", item.kind),
                "state": getattr(item.state, "value", item.state),
                "media_id": item.media_id,
                "created_at": item.created_at,
                "sent_at": item.sent_at,
                "attempts": int(item.attempts),
                "error_class": self._text(item.last_error_class, limit=96),
            }
            for item in visible_deliveries
        ]
        automations = [
            {
                "automation_id": item.id,
                "name": self._text(item.name, limit=128) or "",
                "schedule_kind": getattr(item.schedule_kind, "value", item.schedule_kind),
                "next_run_at": item.next_run_at,
                "last_run_at": item.last_run_at,
                "status": (
                    str(item.last_status)
                    if str(item.last_status) in {"blocked", "error"}
                    else "enabled" if bool(item.enabled) else "paused"
                ),
                "pause_reason": self._text(item.pause_reason, limit=96),
            }
            for item in self._automations_for_session(sid)
        ]
        return {
            "available": True,
            "session_id": sid,
            "view_state": view_state,
            "draft": None if display_draft is None else {"draft_id": display_draft.id},
            "bot": None if bot is None else {
                "display_name": self._text(bot.display_name, limit=128) or "",
                "username": self._text(bot.telegram_username, limit=64),
            },
            "recipient": recipient,
            "pairing_requests": [
                {
                    "request_id": request.id,
                    "user_id": str(request.telegram_user_id),
                    "username": self._text(request.telegram_username, limit=64),
                    "display_name": self._text(request.telegram_display_name, limit=256),
                    "code_hint": self._text(request.code_hint, limit=8),
                    "created_at": request.created_at,
                    "expires_at": request.expires_at,
                }
                for request in pending_requests
            ],
            "counts": counts,
            "warnings": warnings,
            "recent_deliveries": recent,
            "automations": automations,
        }

    def summary_for_session(self, session_id: str) -> dict[str, Any]:
        """Return the same redacted contract for session-list decoration."""
        return self.status_for_session(session_id)


__all__ = [
    "ActionTicketStore",
    "ChannelRuntime",
    "ChannelRuntimeError",
    "ChannelSecretShapeError",
]
