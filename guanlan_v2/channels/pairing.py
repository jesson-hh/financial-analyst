"""Private-chat Telegram pairing with durable one-to-one ownership.

Telegram updates terminate in this module.  They can create a metadata-only
pairing challenge while a Bot is awaiting pairing, or advance the durable
offset after binding; they are never appended to Console and never reach an
Agent factory.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import os
import secrets
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from guanlan_v2.channels.models import BotStatus, PairingStatus
from guanlan_v2.channels.secrets import SecretUnavailable
from guanlan_v2.channels.store import (
    ChannelConflict,
    ChannelNotFound,
    ChannelStoreError,
)
from guanlan_v2.channels.telegram import (
    TelegramCredentialInvalid,
    TelegramError,
    TelegramPollConflict,
    TelegramProtocolError,
    TelegramRetryable,
    TelegramTargetUnavailable,
    TelegramUncertain,
)
from guanlan_v2.console.workspaces import GENERAL_WORKSPACE_ID


UTC = timezone.utc
PAIRING_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PAIRING_CODE_LENGTH = 8
PAIRING_TTL_SECONDS = 60 * 60
POLL_LEASE_SECONDS = 60
POLL_RENEW_SECONDS = 20
POLL_RECONCILE_SECONDS = 2
POLL_TIMEOUT_SECONDS = 15
_POLLABLE_STATUSES = frozenset(
    {BotStatus.AWAITING_PAIRING, BotStatus.ACTIVE, BotStatus.PAUSED}
)
_CODE_MESSAGE_PREFIX = (
    "观澜 Telegram 配对码（1 小时内有效）：\n"
)
_CODE_MESSAGE_SUFFIX = (
    "\n请回到电脑端确认。若收到多条配对消息，以最新一条为准。"
)
_CAPACITY_MESSAGE = (
    "当前待处理配对请求已满（最多 3 个）。请先回到电脑端处理后再私聊。"
)


class PairingError(RuntimeError):
    """Public, structured pairing failure that retains no message or code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _StopPoller(RuntimeError):
    pass


class _PollOutcome(Enum):
    PROGRESSED = "progressed"
    IDLE = "idle"
    RETRYABLE = "retryable"


def generate_pairing_code() -> str:
    """Return one cryptographically random, human-readable 8-character code."""
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def derive_pairing_digest(code: str, salt: bytes) -> bytes:
    """Derive the fixed scrypt digest stored by the pairing registry."""
    if (
        not isinstance(code, str)
        or len(code) != PAIRING_CODE_LENGTH
        or any(character not in PAIRING_ALPHABET for character in code)
    ):
        raise ValueError("pairing code has an invalid shape")
    raw_salt = bytes(salt)
    if len(raw_salt) != 16:
        raise ValueError("pairing salt must be exactly 16 bytes")
    return hashlib.scrypt(
        code.encode("ascii"),
        salt=raw_salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )


def historical_pairing_code_matches(
    channel_store: Any,
    session_id: str,
    candidates: Any,
    *,
    overflow: bool = False,
    maximum_candidates: int = 16,
    maximum_derivations: int = 64,
) -> set[str]:
    """Exactly match legacy Console windows against durable request digests.

    Old Console builds could miss a pairing code adjacent to an ASCII
    character and therefore wrote no provenance.  This migration helper never
    guesses from the public two-character hint: the hint only prunes work, and
    every returned code passes the original scrypt digest.  All request states
    are eligible because approval, dismissal and expiry do not make the code
    public.  Bounds fail closed before attacker-controlled history can amplify
    expensive scrypt work.
    """

    if not isinstance(session_id, str) or not session_id:
        raise PairingError("pairing_history_resolution_unavailable")
    if not isinstance(candidates, (set, frozenset, list, tuple)):
        raise PairingError("pairing_history_resolution_unavailable")
    normalized = {
        str(code) for code in candidates
        if isinstance(code, str)
        and len(code) == PAIRING_CODE_LENGTH
        and all(character in PAIRING_ALPHABET for character in code)
    }
    if len(normalized) > maximum_candidates:
        raise PairingError("pairing_history_resolution_unavailable")
    if overflow:
        try:
            history_probe = getattr(
                channel_store, "has_pairing_history_for_session", None,
            )
            has_history = (
                bool(history_probe(session_id))
                if callable(history_probe)
                else bool(channel_store.pairing_requests_for_session(session_id))
            )
        except Exception:
            raise PairingError("pairing_history_resolution_unavailable") from None
        if has_history:
            raise PairingError("pairing_history_resolution_unavailable")
        return set()
    if not normalized:
        return set()
    try:
        bounded_query = getattr(
            channel_store, "pairing_requests_for_session_hints", None,
        )
        requests = (
            bounded_query(
                session_id,
                {code[-2:] for code in normalized},
                maximum_rows=maximum_derivations + 1,
            )
            if callable(bounded_query)
            else channel_store.pairing_requests_for_session(session_id)
        )
    except Exception:
        raise PairingError("pairing_history_resolution_unavailable") from None
    if not requests:
        return set()

    matches: set[str] = set()
    derivations = 0
    for request in requests:
        hint = str(getattr(request, "code_hint", "") or "")
        eligible_codes = (
            [code for code in normalized if code.endswith(hint)]
            if len(hint) == 2
            else list(normalized)
        )
        for code in eligible_codes:
            derivations += 1
            if derivations > maximum_derivations:
                raise PairingError("pairing_history_resolution_unavailable")
            try:
                candidate_digest = derive_pairing_digest(
                    code, request.code_salt,
                )
            except (TypeError, ValueError):
                raise PairingError("pairing_history_resolution_unavailable") from None
            if hmac.compare_digest(candidate_digest, request.code_digest):
                matches.add(code)
    return matches


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _finish_on_cancel(operation: Any) -> Any:
    """Finish send→durable-commit before making cancellation observable."""
    task = asyncio.create_task(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done() and task.cancelled():
                # The child operation itself was cancelled; repeatedly
                # shielding an already-cancelled task would spin forever.
                raise
            cancelled = True
            continue
    if cancelled:
        raise asyncio.CancelledError
    return result


class TelegramPairingService:
    """One-way pairing boundary and computer-side approval service."""

    def __init__(
        self,
        *,
        console_store: Any,
        channel_store: Any,
        secret_store: Any,
        telegram_adapter: Any,
        clock: Callable[[], datetime] | None = None,
        reserve_mutation: Callable[[str], bool] | None = None,
        release_mutation: Callable[[str], None] | None = None,
    ) -> None:
        self.console_store = console_store
        self.channel_store = channel_store
        self.secret_store = secret_store
        self.telegram_adapter = telegram_adapter
        self._clock = clock or (lambda: datetime.now(UTC))
        self._reserve_mutation = reserve_mutation
        self._release_mutation = release_mutation
        # A challenge can already be visible in Telegram for the short window
        # between provider acceptance and the durable pairing-request commit.
        # Console admission queries this thread-safe counter so that window is
        # treated as sensitive without classifying the entire long-lived
        # ``awaiting_pairing`` state as if every uppercase word were a code.
        self._challenge_inflight_lock = threading.Lock()
        self._challenge_inflight: dict[str, int] = {}
        self.supervisor = PairingSupervisor(self)

    def _begin_challenge_delivery(self, session_id: str) -> None:
        with self._challenge_inflight_lock:
            self._challenge_inflight[session_id] = (
                self._challenge_inflight.get(session_id, 0) + 1
            )

    def _end_challenge_delivery(self, session_id: str) -> None:
        with self._challenge_inflight_lock:
            remaining = self._challenge_inflight.get(session_id, 0) - 1
            if remaining > 0:
                self._challenge_inflight[session_id] = remaining
            else:
                self._challenge_inflight.pop(session_id, None)

    def challenge_delivery_inflight(self, session_id: object) -> bool:
        if not isinstance(session_id, str) or not session_id:
            return False
        with self._challenge_inflight_lock:
            return self._challenge_inflight.get(session_id, 0) > 0

    @staticmethod
    def _safe_bot_identity(identity: Any) -> tuple[str, str | None]:
        bot_id = getattr(identity, "bot_id", None)
        username = getattr(identity, "username", None)
        if not isinstance(bot_id, (str, int)) or not str(bot_id).isdigit():
            raise PairingError("telegram_protocol_error")
        if username is not None and not isinstance(username, str):
            username = None
        return str(bot_id), username

    def _require_writable_session(self, session_id: object) -> str:
        if not isinstance(session_id, str) or not session_id:
            raise PairingError("pairing_not_found")
        meta = self.console_store.get_meta(session_id)
        if not isinstance(meta, dict):
            raise PairingError("pairing_not_found")
        if bool(meta.get("archived")):
            raise PairingError("channel_session_read_only")
        workspace_store = getattr(self.console_store, "workspace_store", None)
        require = getattr(workspace_store, "require", None)
        if not callable(require):
            raise PairingError("channel_session_read_only")
        try:
            workspace_id = str(meta.get("workspace_id") or GENERAL_WORKSPACE_ID)
            require(workspace_id, active_only=True)
        except Exception:
            raise PairingError("channel_session_read_only") from None
        return session_id

    def _actual_console_tail(self, session_id: str) -> int:
        """Read the last actually persisted event, tolerating reserved ID gaps."""
        if not isinstance(self.console_store.get_meta(session_id), dict):
            raise PairingError("pairing_not_found")
        cursor = 0
        while True:
            events = self.console_store.read_events_forward(
                session_id, after_id=cursor, limit=200
            )
            actual_ids = [
                event.get("id")
                for event in events
                if isinstance(event, dict)
                and isinstance(event.get("id"), int)
                and not isinstance(event.get("id"), bool)
                and event.get("id") > cursor
            ]
            if not actual_ids:
                return cursor
            next_cursor = max(actual_ids)
            if next_cursor <= cursor:
                return cursor
            cursor = next_cursor
            if len(events) < 200:
                return cursor

    async def _fault_owned(
        self,
        bot_id: str,
        *,
        owner: str,
        revision: int,
        status: BotStatus,
        error_code: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                self.channel_store.fault_poll_bot,
                bot_id,
                owner=owner,
                credential_revision=revision,
                status=status,
                error_code=error_code,
            )
        finally:
            self.supervisor.signal()

    async def poll_once(self, bot_account_id: str) -> bool:
        """Test/admin hook: acquire, poll one batch, then release immediately."""
        try:
            bot = await asyncio.to_thread(self.channel_store.bot, bot_account_id)
        except Exception:
            return False
        if bot.status not in _POLLABLE_STATUSES or not bot.credential_id:
            return False
        owner = f"pair_once_{uuid.uuid4().hex}"
        acquired = await asyncio.to_thread(
            self.channel_store.acquire_poll_lease,
            bot.id,
            owner=owner,
            credential_revision=bot.credential_revision,
            lease_seconds=POLL_LEASE_SECONDS,
        )
        if not acquired:
            return False
        try:
            outcome = await self._poll_owned_once(
                bot.id, owner=owner, revision=bot.credential_revision
            )
            return outcome is _PollOutcome.PROGRESSED
        except _StopPoller:
            return False
        finally:
            await asyncio.to_thread(
                self.channel_store.release_poll_lease,
                bot.id,
                owner=owner,
                credential_revision=bot.credential_revision,
            )

    async def _poll_owned_once(
        self, bot_account_id: str, *, owner: str, revision: int
    ) -> _PollOutcome:
        try:
            bot = await asyncio.to_thread(self.channel_store.bot, bot_account_id)
        except Exception:
            raise _StopPoller() from None
        if (
            bot.status not in _POLLABLE_STATUSES
            or int(bot.credential_revision) != int(revision)
            or bot.poll_owner != owner
            or not bot.credential_id
        ):
            raise _StopPoller()
        token = ""
        try:
            try:
                token = await asyncio.to_thread(self.secret_store.get, bot.credential_id)
            except (SecretUnavailable, KeyError):
                await self._fault_owned(
                    bot.id,
                    owner=owner,
                    revision=revision,
                    status=BotStatus.CREDENTIAL_UNAVAILABLE,
                    error_code="credential_unavailable",
                )
                raise _StopPoller() from None
            except Exception:
                await self._fault_owned(
                    bot.id,
                    owner=owner,
                    revision=revision,
                    status=BotStatus.CREDENTIAL_UNAVAILABLE,
                    error_code="credential_unavailable",
                )
                raise _StopPoller() from None
            try:
                updates = await _maybe_await(
                    self.telegram_adapter.get_updates(
                        token,
                        offset=int(bot.update_offset),
                        timeout=POLL_TIMEOUT_SECONDS,
                    )
                )
            except TelegramPollConflict:
                await self._fault_owned(
                    bot.id,
                    owner=owner,
                    revision=revision,
                    status=BotStatus.POLL_CONFLICT,
                    error_code="poll_conflict",
                )
                raise _StopPoller() from None
            except TelegramCredentialInvalid:
                await self._fault_owned(
                    bot.id,
                    owner=owner,
                    revision=revision,
                    status=BotStatus.CREDENTIAL_INVALID,
                    error_code="credential_invalid",
                )
                raise _StopPoller() from None
            except (TelegramRetryable, TelegramProtocolError, TelegramTargetUnavailable):
                return _PollOutcome.RETRYABLE
            except TelegramError:
                return _PollOutcome.RETRYABLE

            progressed = False
            for update in sorted(tuple(updates), key=lambda item: int(item.update_id)):
                try:
                    fresh = await asyncio.to_thread(self.channel_store.bot, bot.id)
                except Exception:
                    raise _StopPoller() from None
                if (
                    fresh.poll_owner != owner
                    or int(fresh.credential_revision) != int(revision)
                    or fresh.status not in _POLLABLE_STATUSES
                ):
                    raise _StopPoller()
                if int(update.update_id) < int(fresh.update_offset):
                    continue
                if fresh.status in {BotStatus.ACTIVE, BotStatus.PAUSED}:
                    advanced = await asyncio.to_thread(
                        self.channel_store.advance_poll_update,
                        fresh.id,
                        next_offset=int(update.update_id) + 1,
                        owner=owner,
                        credential_revision=revision,
                    )
                    if not advanced:
                        raise _StopPoller()
                    progressed = True
                    continue
                accepted = await self._handle_awaiting_update(
                    fresh, update, owner=owner, revision=revision
                )
                if not accepted:
                    # Never advance beyond a retryable gap in the same batch.
                    return _PollOutcome.RETRYABLE
                progressed = True
            return _PollOutcome.PROGRESSED if progressed else _PollOutcome.IDLE
        finally:
            token = ""  # shorten the local plaintext reference lifetime

    async def _handle_awaiting_update(
        self, bot: Any, update: Any, *, owner: str, revision: int
    ) -> bool:
        message = getattr(update, "message", None)
        update_id = int(getattr(update, "update_id"))
        if (
            message is None
            or getattr(message, "chat_type", None) != "private"
            or getattr(message, "user_id", None) is None
            or str(getattr(message, "user_id")) != str(getattr(message, "chat_id", ""))
        ):
            advanced = await asyncio.to_thread(
                self.channel_store.advance_poll_update,
                bot.id,
                next_offset=update_id + 1,
                owner=owner,
                credential_revision=revision,
            )
            if not advanced:
                raise _StopPoller()
            return True

        try:
            preflight = await asyncio.to_thread(
                self.channel_store.pairing_update_preflight,
                bot.id,
                telegram_update_id=update_id,
                telegram_chat_id=str(message.chat_id),
                owner=owner,
                credential_revision=revision,
            )
        except (ChannelConflict, ChannelStoreError, ValueError):
            raise _StopPoller() from None
        if preflight == "consumed":
            return True
        return await _finish_on_cancel(
            self._send_and_commit_challenge(
                bot,
                update,
                preflight=preflight,
                owner=owner,
                revision=revision,
            )
        )

    async def _advance_after_definite_result(
        self, bot_id: str, update_id: int, *, owner: str, revision: int
    ) -> None:
        advanced = await asyncio.to_thread(
            self.channel_store.advance_poll_update,
            bot_id,
            next_offset=update_id + 1,
            owner=owner,
            credential_revision=revision,
        )
        if not advanced:
            raise _StopPoller()

    async def _send_and_commit_challenge(
        self,
        bot: Any,
        update: Any,
        *,
        preflight: str,
        owner: str,
        revision: int,
    ) -> bool:
        message = update.message
        update_id = int(update.update_id)
        token = ""
        code = ""
        outbound_text = ""
        salt = b""
        digest = b""
        challenge_session_id = ""
        try:
            token = await asyncio.to_thread(self.secret_store.get, bot.credential_id)
            if preflight == "full":
                outbound_text = _CAPACITY_MESSAGE
                salt = b""
                digest = b""
            else:
                code = generate_pairing_code()
                salt = os.urandom(16)
                digest = await asyncio.to_thread(derive_pairing_digest, code, salt)
                outbound_text = f"{_CODE_MESSAGE_PREFIX}{code}{_CODE_MESSAGE_SUFFIX}"
                challenge_session_id = str(bot.draft_session_id or "")
                if not challenge_session_id:
                    raise _StopPoller()
                self._begin_challenge_delivery(challenge_session_id)
            try:
                await _maybe_await(
                    self.telegram_adapter.send_message(
                        token, chat_id=str(message.chat_id), text=outbound_text
                    )
                )
                send_accepted = True
            except TelegramUncertain:
                # The provider may have accepted the message.  Persisting the
                # digest makes a received code usable and avoids blind resend.
                send_accepted = True
            except TelegramRetryable:
                return False
            except TelegramCredentialInvalid:
                await self._advance_after_definite_result(
                    bot.id, update_id, owner=owner, revision=revision
                )
                await self._fault_owned(
                    bot.id,
                    owner=owner,
                    revision=revision,
                    status=BotStatus.CREDENTIAL_INVALID,
                    error_code="credential_invalid",
                )
                raise _StopPoller() from None
            except TelegramPollConflict:
                await self._advance_after_definite_result(
                    bot.id, update_id, owner=owner, revision=revision
                )
                await self._fault_owned(
                    bot.id,
                    owner=owner,
                    revision=revision,
                    status=BotStatus.POLL_CONFLICT,
                    error_code="poll_conflict",
                )
                raise _StopPoller() from None
            except (TelegramTargetUnavailable, TelegramProtocolError):
                await self._advance_after_definite_result(
                    bot.id, update_id, owner=owner, revision=revision
                )
                return True
            except TelegramError:
                return False

            if not send_accepted:  # defensive, all branches above decide it
                return False
            if preflight == "full":
                await self._advance_after_definite_result(
                    bot.id, update_id, owner=owner, revision=revision
                )
                return True
            await asyncio.to_thread(
                self.channel_store.commit_sent_pairing_update,
                bot_account_id=bot.id,
                session_id=str(bot.draft_session_id),
                telegram_update_id=update_id,
                telegram_user_id=str(message.user_id),
                telegram_chat_id=str(message.chat_id),
                telegram_username=getattr(message, "username", None),
                telegram_display_name=getattr(message, "display_name", None),
                code_salt=salt,
                code_digest=digest,
                code_hint=code[-2:],
                owner=owner,
                credential_revision=revision,
            )
            return True
        except (SecretUnavailable, KeyError):
            await self._fault_owned(
                bot.id,
                owner=owner,
                revision=revision,
                status=BotStatus.CREDENTIAL_UNAVAILABLE,
                error_code="credential_unavailable",
            )
            raise _StopPoller() from None
        except Exception:
            if token:
                raise
            await self._fault_owned(
                bot.id,
                owner=owner,
                revision=revision,
                status=BotStatus.CREDENTIAL_UNAVAILABLE,
                error_code="credential_unavailable",
            )
            raise _StopPoller() from None
        finally:
            if challenge_session_id:
                self._end_challenge_delivery(challenge_session_id)
            token = ""
            code = ""
            outbound_text = ""
            salt = b""
            digest = b""

    async def approve_pairing(
        self, session_id: str, pairing_request_id: str, code: str
    ):
        """Reserve the session across DPAPI/getMe/recheck/SQLite approval."""
        sid = self._require_writable_session(session_id)
        reserved = False
        if self._reserve_mutation is not None:
            try:
                reserved = bool(self._reserve_mutation(sid))
            except Exception:
                raise PairingError("pairing_busy") from None
            if not reserved:
                raise PairingError("pairing_busy")
        try:
            return await self._approve_pairing_reserved(
                sid, pairing_request_id, code
            )
        finally:
            if reserved and self._release_mutation is not None:
                try:
                    self._release_mutation(sid)
                except Exception:
                    pass

    async def _approve_pairing_reserved(
        self, session_id: str, pairing_request_id: str, code: str
    ):
        """Verify outside SQLite, then atomically approve the permanent binding."""
        sid = self._require_writable_session(session_id)
        if not isinstance(pairing_request_id, str) or not pairing_request_id:
            raise PairingError("pairing_not_found")
        if (
            not isinstance(code, str)
            or len(code) != PAIRING_CODE_LENGTH
            or any(character not in PAIRING_ALPHABET for character in code)
        ):
            raise PairingError("pairing_code_invalid")
        try:
            request = await asyncio.to_thread(
                self.channel_store.pairing_request, pairing_request_id
            )
            bot = await asyncio.to_thread(self.channel_store.bot, request.bot_account_id)
        except Exception:
            raise PairingError("pairing_not_found") from None
        if request.session_id != sid or bot.draft_session_id != sid:
            raise PairingError("pairing_not_found")
        if request.status not in {PairingStatus.PENDING, PairingStatus.APPROVED}:
            raise PairingError("pairing_not_pending")
        if int(request.issue_credential_revision) != int(bot.credential_revision):
            raise PairingError("pairing_conflict")
        if not bot.credential_id or not bot.telegram_bot_id:
            raise PairingError("credential_unavailable")
        try:
            supplied_digest = await asyncio.to_thread(
                derive_pairing_digest, code, request.code_salt
            )
        except ValueError:
            raise PairingError("pairing_code_invalid") from None
        token = ""
        try:
            try:
                token = await asyncio.to_thread(self.secret_store.get, bot.credential_id)
            except Exception:
                try:
                    await asyncio.to_thread(
                        self.channel_store.fault_pause_bot,
                        bot.id,
                        status=BotStatus.CREDENTIAL_UNAVAILABLE,
                        error_code="credential_unavailable",
                        expected_credential_revision=bot.credential_revision,
                        expected_telegram_bot_id=bot.telegram_bot_id,
                    )
                except Exception:
                    pass
                raise PairingError("credential_unavailable") from None
            try:
                identity = await _maybe_await(self.telegram_adapter.get_me(token))
                verified_bot_id, _username = self._safe_bot_identity(identity)
            except TelegramCredentialInvalid:
                try:
                    await asyncio.to_thread(
                        self.channel_store.fault_pause_bot,
                        bot.id,
                        status=BotStatus.CREDENTIAL_INVALID,
                        error_code="credential_invalid",
                        expected_credential_revision=bot.credential_revision,
                        expected_telegram_bot_id=bot.telegram_bot_id,
                    )
                except Exception:
                    pass
                raise PairingError("telegram_credential_invalid") from None
            except TelegramPollConflict:
                raise PairingError("telegram_poll_conflict") from None
            except TelegramError:
                raise PairingError("telegram_verification_failed") from None
            if verified_bot_id != str(bot.telegram_bot_id):
                raise PairingError("telegram_identity_changed")

            async def commit_approval():
                # DPAPI/getMe may have taken seconds.  Recheck archive/workspace
                # state before deriving the actual persisted Console boundary,
                # and once more immediately before the SQLite decision.
                await asyncio.to_thread(self._require_writable_session, sid)
                cursor = await asyncio.to_thread(self._actual_console_tail, sid)
                await asyncio.to_thread(self._require_writable_session, sid)
                return await asyncio.to_thread(
                    self.channel_store.approve_pairing_request,
                    request.id,
                    session_id=sid,
                    sync_from_event_id=cursor,
                    expected_credential_revision=bot.credential_revision,
                    expected_telegram_bot_id=verified_bot_id,
                    expected_code_digest=supplied_digest,
                    require_private=True,
                )

            try:
                # Cancellation cannot release Runtime's shared session
                # reservation while the SQLite worker may still commit.
                binding = await _finish_on_cancel(commit_approval())
            except ChannelConflict as error:
                safe_reason = str(error)
                if "code" in safe_reason:
                    raise PairingError("pairing_code_invalid") from None
                raise PairingError("pairing_conflict") from None
            except (ChannelNotFound, ChannelStoreError, ValueError):
                raise PairingError("pairing_conflict") from None
            self.supervisor.signal()
            return binding
        finally:
            token = ""

    async def resolve_pairing_code(self, session_id: str, code: str):
        """Resolve, but do not approve, one exact current-session challenge.

        Agent tools intentionally do not receive request, Bot, chat, or user
        identifiers.  We derive every candidate digest (never select by the
        two-character display hint), and return only when exactly one
        unexpired current-generation challenge matches.
        """
        sid = self._require_writable_session(session_id)
        if (
            not isinstance(code, str)
            or len(code) != PAIRING_CODE_LENGTH
            or any(character not in PAIRING_ALPHABET for character in code)
        ):
            raise PairingError("pairing_code_invalid")
        try:
            draft = await asyncio.to_thread(self.channel_store.current_draft, sid)
            binding = await asyncio.to_thread(
                self.channel_store.binding_for_session, sid, live_only=True
            )
            if draft is not None:
                bot = draft
            elif binding is not None:
                bot = await asyncio.to_thread(
                    self.channel_store.bot, binding.bot_account_id
                )
            else:
                raise PairingError("pairing_code_invalid")
            requests = await asyncio.to_thread(
                self.channel_store.pairing_requests_for, bot.id
            )
        except PairingError:
            raise
        except Exception:
            raise PairingError("pairing_code_invalid") from None

        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            now = now.replace(tzinfo=UTC)
        now = now.astimezone(UTC)
        eligible = []
        for request in requests:
            if (
                request.session_id != sid
                or request.status not in {PairingStatus.PENDING, PairingStatus.APPROVED}
                or int(request.issue_credential_revision)
                != int(bot.credential_revision)
            ):
                continue
            try:
                expires_at = datetime.fromisoformat(
                    request.expires_at[:-1] + "+00:00"
                    if request.expires_at.endswith("Z")
                    else request.expires_at
                ).astimezone(UTC)
            except (TypeError, ValueError):
                continue
            # Expiry is deliberately checked before the expensive scrypt.
            # Production issuance permits at most three pending requests plus
            # one idempotent approved request for the live binding.
            if expires_at <= now:
                continue
            eligible.append(request)
            if len(eligible) > 4:
                raise PairingError("pairing_code_invalid")
        matches = []
        for request in eligible:
            try:
                candidate = await asyncio.to_thread(
                    derive_pairing_digest, code, request.code_salt
                )
            except (TypeError, ValueError):
                continue
            if hmac.compare_digest(candidate, request.code_digest):
                matches.append(request)
        if len(matches) != 1:
            raise PairingError("pairing_code_invalid")
        return matches[0]

    async def matching_historical_pairing_codes(
        self,
        session_id: str,
        candidates: Any,
        overflow: bool = False,
    ) -> set[str]:
        """Resolve legacy DLP candidates without recipient or code disclosure."""

        sid = self._require_writable_session(session_id)
        return await asyncio.to_thread(
            historical_pairing_code_matches,
            self.channel_store,
            sid,
            candidates,
            overflow=bool(overflow),
        )

    async def approve_pairing_code(self, session_id: str, code: str):
        """Approve the exact challenge resolved from the current session."""
        request = await self.resolve_pairing_code(session_id, code)
        return await self.approve_pairing(session_id, request.id, code)

    def dismiss_pairing(self, session_id: str, pairing_request_id: str):
        try:
            sid = self._require_writable_session(session_id)
            result = self.channel_store.dismiss_pairing_for_session(
                sid, pairing_request_id
            )
        except PairingError:
            raise
        except Exception:
            raise PairingError("pairing_not_found") from None
        self.supervisor.signal()
        return result

    async def start(self) -> None:
        await self.supervisor.start()

    async def stop(self) -> None:
        await self.supervisor.stop()

    def signal(self) -> None:
        self.supervisor.signal()


class PairingSupervisor:
    """Reconcile one generation-fenced poller per eligible Bot account."""

    def __init__(
        self,
        service: TelegramPairingService,
        *,
        reconcile_seconds: float = POLL_RECONCILE_SECONDS,
        renew_seconds: float = POLL_RENEW_SECONDS,
    ) -> None:
        self.service = service
        self.reconcile_seconds = float(reconcile_seconds)
        self.renew_seconds = float(renew_seconds)
        self._task: asyncio.Task[Any] | None = None
        self._pollers: dict[tuple[str, int], asyncio.Task[Any]] = {}
        self._retiring: set[asyncio.Task[Any]] = set()
        self._signal_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._prestart_signal = False
        self._state_lock = threading.Lock()

    @property
    def poller_keys(self) -> frozenset[tuple[str, int]]:
        return frozenset(self._pollers)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        self._signal_event = asyncio.Event()
        with self._state_lock:
            if self._prestart_signal:
                self._signal_event.set()
                self._prestart_signal = False
        self._task = asyncio.create_task(self._run(), name="telegram-pairing-supervisor")

    def signal(self) -> None:
        with self._state_lock:
            loop = self._loop
            event = self._signal_event
            if loop is None or event is None or loop.is_closed():
                self._prestart_signal = True
                return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            with self._state_lock:
                self._prestart_signal = True

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        pollers = tuple(set(self._pollers.values()) | self._retiring)
        for poller in pollers:
            poller.cancel()
        if pollers:
            await asyncio.gather(*pollers, return_exceptions=True)
        self._pollers.clear()
        self._retiring.clear()
        with self._state_lock:
            self._loop = None
            self._signal_event = None

    async def _run(self) -> None:
        try:
            while True:
                await self._reconcile()
                event = self._signal_event
                if event is None:
                    return
                event.clear()
                try:
                    await asyncio.wait_for(
                        event.wait(), timeout=self.reconcile_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    async def _reconcile(self) -> None:
        try:
            bots = await asyncio.to_thread(self.service.channel_store.bots)
        except Exception:
            return
        desired = {
            (bot.id, int(bot.credential_revision))
            for bot in bots
            if bot.status in _POLLABLE_STATUSES and bool(bot.credential_id)
        }
        retiring: list[asyncio.Task[Any]] = []
        for key, task in tuple(self._pollers.items()):
            if task.done() or key not in desired:
                if not task.done():
                    task.cancel()
                    retiring.append(task)
                    self._retiring.add(task)
                else:
                    try:
                        task.result()
                    except (asyncio.CancelledError, Exception):
                        pass
                self._pollers.pop(key, None)
        if retiring:
            try:
                await asyncio.gather(*retiring, return_exceptions=True)
            finally:
                for task in retiring:
                    if task.done():
                        self._retiring.discard(task)
        for key in sorted(desired):
            if key in self._pollers:
                continue
            bot_id, revision = key
            self._pollers[key] = asyncio.create_task(
                self._run_poller(bot_id, revision),
                name=f"telegram-pairing-{bot_id}-{revision}",
            )

    async def _renew_loop(
        self,
        bot_id: str,
        revision: int,
        owner: str,
        lease_lost: asyncio.Event,
    ) -> None:
        try:
            while True:
                await asyncio.sleep(self.renew_seconds)
                renewed = await asyncio.to_thread(
                    self.service.channel_store.renew_poll_lease,
                    bot_id,
                    owner=owner,
                    credential_revision=revision,
                    lease_seconds=POLL_LEASE_SECONDS,
                )
                if not renewed:
                    lease_lost.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # A crashed renewer must fence the main poll loop immediately;
            # silently ending this task would keep issuing getUpdates until
            # the 60-second lease expires and another process takes over.
            lease_lost.set()

    async def _run_poller(self, bot_id: str, revision: int) -> None:
        owner = f"pair_{uuid.uuid4().hex}"
        acquired = False
        renewer: asyncio.Task[Any] | None = None
        lease_lost = asyncio.Event()
        backoff = 2.0
        try:
            acquired = await asyncio.to_thread(
                self.service.channel_store.acquire_poll_lease,
                bot_id,
                owner=owner,
                credential_revision=revision,
                lease_seconds=POLL_LEASE_SECONDS,
            )
            if not acquired:
                return
            renewer = asyncio.create_task(
                self._renew_loop(bot_id, revision, owner, lease_lost)
            )
            while not lease_lost.is_set():
                try:
                    outcome = await self.service._poll_owned_once(
                        bot_id, owner=owner, revision=revision
                    )
                except _StopPoller:
                    return
                if outcome is _PollOutcome.PROGRESSED:
                    backoff = 2.0
                    continue
                if outcome is _PollOutcome.IDLE:
                    # getUpdates is a healthy long poll.  An empty response is
                    # not a transport failure and must never grow the retry
                    # backoff to five minutes.
                    backoff = 2.0
                    try:
                        await asyncio.wait_for(lease_lost.wait(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass
                    continue
                try:
                    await asyncio.wait_for(lease_lost.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(300.0, backoff * 2.0)
        except asyncio.CancelledError:
            raise
        finally:
            if renewer is not None:
                renewer.cancel()
                await asyncio.gather(renewer, return_exceptions=True)
            if acquired:
                await asyncio.to_thread(
                    self.service.channel_store.release_poll_lease,
                    bot_id,
                    owner=owner,
                    credential_revision=revision,
                )


__all__ = [
    "PAIRING_ALPHABET",
    "PAIRING_CODE_LENGTH",
    "PAIRING_TTL_SECONDS",
    "POLL_LEASE_SECONDS",
    "POLL_RENEW_SECONDS",
    "PairingError",
    "PairingSupervisor",
    "TelegramPairingService",
    "derive_pairing_digest",
    "generate_pairing_code",
    "historical_pairing_code_matches",
]
