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
from dataclasses import dataclass
from typing import Any

from guanlan_v2.channels.models import (
    BindingStatus,
    BotStatus,
    PairingStatus,
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
    ) -> None:
        self.console_store = console_store
        self.channel_store = channel_store
        self.secret_store = secret_store
        self.telegram_adapter = telegram_adapter
        self.media_store = media_store
        self.ticket_store = ticket_store or ActionTicketStore()
        # A reservation is deliberately just durable-process ownership state,
        # not a lock held across getMe/DPAPI/SQLite awaits.  It lets Console
        # archive/delete and the dedicated Token endpoint see the same narrow
        # in-flight handoff without serialising unrelated sessions.
        self._secret_reservation_lock = threading.Lock()
        self._secret_reservations: set[str] = set()

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
            if sid in self._secret_reservations:
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
        return await self._shield_critical(
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
        return await asyncio.to_thread(self.status_for_session, session_id)

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
        self._require_session(session_id, writable=True)
        try:
            self.channel_store.set_session_manual_pause(session_id, paused=False)
        except (ChannelConflict, InvalidTransition):
            raise ChannelRuntimeError("channel_resume_not_allowed") from None
        except (ChannelStoreError, ValueError):
            raise ChannelRuntimeError("channel_resume_failed") from None
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
        return self.status_for_session(session_id)

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
        if binding is not None:
            try:
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
            for item in deliveries[-20:]
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
