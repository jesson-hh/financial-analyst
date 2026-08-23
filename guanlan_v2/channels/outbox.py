"""Reliable, strictly ordered Telegram delivery from the durable outbox."""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from guanlan_v2.channels.media_policy import caption_contains_location
from guanlan_v2.channels.models import BotStatus, OutboxState
from guanlan_v2.channels.secrets import SecretUnavailable
from guanlan_v2.channels.store import ChannelConflict, OutboxClaim
from guanlan_v2.channels.telegram import (
    TelegramCredentialInvalid,
    TelegramProtocolError,
    TelegramRetryable,
    TelegramTargetUnavailable,
    TelegramUncertain,
)


UTC = timezone.utc
log = logging.getLogger(__name__)

LEASE_SECONDS = 30
IDLE_SECONDS = 0.5
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_INITIAL_COOLDOWN_SECONDS = 60.0
CIRCUIT_MAX_COOLDOWN_SECONDS = 900.0
MAX_MEDIA_BYTES = 10 * 1024 * 1024
MAX_PHOTO_CAPTION = 900
_MEDIA_FILENAMES = {
    "image/png": "image.png",
    "image/jpeg": "image.jpg",
    "image/webp": "image.webp",
}


@dataclass
class _CircuitState:
    failures: int = 0
    open_until: datetime | None = None
    cooldown_seconds: float = CIRCUIT_INITIAL_COOLDOWN_SECONDS
    half_open_inflight: bool = False


class OutboxWorker:
    """Claim one head per binding and deliver independent bindings in parallel."""

    def __init__(
        self,
        *,
        channel_store: Any,
        secret_store: Any,
        telegram_adapter: Any,
        media_store: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        random_uniform: Callable[[float, float], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        lease_seconds: int = LEASE_SECONDS,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) \
                or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        if not isinstance(idle_seconds, (int, float)) or idle_seconds <= 0:
            raise ValueError("idle_seconds must be positive")
        self.channel_store = channel_store
        self.secret_store = secret_store
        self.telegram_adapter = telegram_adapter
        self.media_store = media_store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uniform = random_uniform or random.uniform
        self._sleep = sleeper or asyncio.sleep
        self.lease_seconds = int(lease_seconds)
        self.idle_seconds = float(idle_seconds)
        self._circuits: dict[str, _CircuitState] = {}
        self._startup_done = False
        self._startup_lock: asyncio.Lock | None = None

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def retry_delay(self, attempts: int, retry_after: int | None = None) -> float:
        if retry_after is not None:
            return float(max(1, int(retry_after)))
        count = max(1, int(attempts))
        delay = min(300.0, 2.0 * (2 ** (count - 1)))
        return delay * (1.0 + float(self._uniform(0.0, 0.2)))

    def _circuit_allows(self, bot_account_id: str, now: datetime) -> bool:
        state = self._circuits.setdefault(bot_account_id, _CircuitState())
        if state.open_until is None:
            return True
        if now < state.open_until:
            return False
        if state.half_open_inflight:
            return False
        state.half_open_inflight = True
        return True

    def _cancel_unused_probe(self, bot_account_id: str) -> None:
        state = self._circuits.get(bot_account_id)
        if state is not None and state.open_until is not None:
            state.half_open_inflight = False

    def _circuit_success(self, bot_account_id: str) -> None:
        self._circuits[bot_account_id] = _CircuitState()

    def _circuit_retryable(self, bot_account_id: str, now: datetime) -> None:
        state = self._circuits.setdefault(bot_account_id, _CircuitState())
        if state.open_until is not None and state.half_open_inflight:
            state.cooldown_seconds = min(
                CIRCUIT_MAX_COOLDOWN_SECONDS,
                max(CIRCUIT_INITIAL_COOLDOWN_SECONDS, state.cooldown_seconds * 2.0),
            )
            state.open_until = now + timedelta(seconds=state.cooldown_seconds)
            state.half_open_inflight = False
            state.failures = CIRCUIT_FAILURE_THRESHOLD
            return
        state.failures += 1
        if state.failures >= CIRCUIT_FAILURE_THRESHOLD:
            state.cooldown_seconds = CIRCUIT_INITIAL_COOLDOWN_SECONDS
            state.open_until = now + timedelta(seconds=state.cooldown_seconds)
            state.half_open_inflight = False

    def circuit_snapshot(self, bot_account_id: str) -> dict[str, Any]:
        state = self._circuits.get(bot_account_id, _CircuitState())
        return {
            "failures": state.failures,
            "open_until": state.open_until,
            "cooldown_seconds": state.cooldown_seconds,
            "half_open_inflight": state.half_open_inflight,
        }

    async def _settled_thread(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any):
        """Await a thread call to completion even when this task is cancelled."""
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise cancelled

    async def startup_recover(self, *, force: bool = False) -> int:
        if self._startup_done and not force:
            return 0
        if self._startup_lock is None:
            self._startup_lock = asyncio.Lock()
        async with self._startup_lock:
            if self._startup_done and not force:
                return 0
            recovered = await self._settled_thread(
                self.channel_store.recover_sending_outbox,
                error_class="startup_recovery",
                now=self._now(),
            )
            self._startup_done = True
            return int(recovered)

    async def _audit(self, event_kind: str, claim: OutboxClaim, error_class: str | None = None):
        try:
            await asyncio.to_thread(
                self.channel_store.audit,
                event_kind,
                session_id=claim.source_session_id,
                bot_account_id=claim.bot_account_id,
                binding_id=claim.binding_id,
                outbox_id=claim.id,
                error_class=error_class,
            )
        except Exception:
            # Delivery state is authoritative; diagnostics must never make a
            # successful CAS look failed or cause a duplicate retry.
            pass

    def _finish_kwargs(self, claim: OutboxClaim) -> dict[str, Any]:
        return {
            "lease_owner": str(claim.lease_owner),
            "bot_account_id": claim.bot_account_id,
            "telegram_chat_id": claim.telegram_chat_id,
            "credential_id": claim.credential_id,
            "credential_revision": claim.credential_revision,
        }

    async def _finish(
        self,
        claim: OutboxClaim,
        state: OutboxState,
        *,
        error_class: str | None = None,
        next_attempt_at: datetime | None = None,
        telegram_message_id: str | None = None,
    ):
        return await self._settled_thread(
            self.channel_store.finish_outbox_claim,
            claim.id,
            state=state,
            error_class=error_class,
            next_attempt_at=next_attempt_at,
            telegram_message_id=telegram_message_id,
            now=self._now(),
            **self._finish_kwargs(claim),
        )

    async def _mark_uncertain(self, claim: OutboxClaim, error_class: str):
        return await self._settled_thread(
            self.channel_store.mark_outbox_claim_uncertain,
            claim.id,
            lease_owner=str(claim.lease_owner),
            error_class=error_class,
            now=self._now(),
        )

    async def _fault_pause_credential(
        self,
        claim: OutboxClaim,
        *,
        status: BotStatus,
        error_code: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                self.channel_store.fault_pause_bot,
                claim.bot_account_id,
                status=status,
                error_code=error_code,
                expected_credential_revision=claim.credential_revision,
            )
        except Exception:
            # A replacement generation wins.  Never fault-pause it based on an
            # older Token's result.
            pass

    async def _renew_loop(self, claim: OutboxClaim) -> None:
        interval = max(1.0, self.lease_seconds / 3.0)
        while True:
            await asyncio.sleep(interval)
            now = self._now()
            renewed = await asyncio.to_thread(
                self.channel_store.renew_outbox_claim,
                claim.id,
                lease_owner=str(claim.lease_owner),
                lease_until=now + timedelta(seconds=self.lease_seconds),
                bot_account_id=claim.bot_account_id,
                telegram_chat_id=claim.telegram_chat_id,
                credential_id=claim.credential_id,
                credential_revision=claim.credential_revision,
                now=now,
            )
            if not renewed:
                return

    async def deliver_claim(self, claim: OutboxClaim) -> None:
        send_started = False
        retryable_recorded = False
        renewal: asyncio.Task | None = None
        token = ""
        media_bytes: bytes | None = None
        try:
            current = await asyncio.to_thread(
                self.channel_store.outbox_claim_is_current, claim, now=self._now()
            )
            if not current:
                await self._mark_uncertain(claim, "credential_generation_changed")
                return
            # Media integrity/ownership verification may read and hash up to
            # 10 MB.  Renew from the start of local preparation so a slow disk
            # cannot expire the claim before the provider call even begins.
            renewal = asyncio.create_task(self._renew_loop(claim))
            media_metadata: Mapping[str, Any] | None = None
            if claim.kind == "media":
                if (
                    self.media_store is None
                    or claim.media_authorization_version != 1
                    or not isinstance(claim.media_id, str)
                    or not claim.media_id
                    or claim.text is not None
                    or (
                        claim.caption is not None
                        and (
                            not isinstance(claim.caption, str)
                            or len(claim.caption) > MAX_PHOTO_CAPTION
                            or "\x00" in claim.caption
                            or caption_contains_location(claim.caption)
                        )
                    )
                ):
                    completed = await self._finish(
                        claim,
                        OutboxState.TERMINAL,
                        error_class="invalid_media_delivery",
                    )
                    if completed is not None:
                        await self._audit(
                            "outbox_terminal", claim, "invalid_media_delivery"
                        )
                    return
                loaded = await asyncio.to_thread(
                    self.media_store.load_for_session,
                    claim.media_id,
                    claim.source_session_id,
                )
                if (
                    not isinstance(loaded, tuple)
                    or len(loaded) != 2
                    or not isinstance(loaded[0], Mapping)
                    or not isinstance(loaded[1], bytes)
                ):
                    raise ValueError("invalid media store response")
                media_metadata, media_bytes = loaded
                mime = media_metadata.get("mime")
                if (
                    media_metadata.get("id") != claim.media_id
                    or mime not in _MEDIA_FILENAMES
                    or not media_bytes
                    or len(media_bytes) > MAX_MEDIA_BYTES
                ):
                    raise ValueError("invalid media store response")
            elif claim.kind != "text" or not isinstance(claim.text, str) or not claim.text:
                completed = await self._finish(
                    claim, OutboxState.TERMINAL, error_class="unsupported_outbound_kind"
                )
                if completed is not None:
                    await self._audit("outbox_terminal", claim, "unsupported_outbound_kind")
                return

            token = await asyncio.to_thread(self.secret_store.get, claim.credential_id)
            # Close the DPAPI/replacement race again immediately before the
            # provider call.  Completion is fenced a third time.
            current = await asyncio.to_thread(
                self.channel_store.outbox_claim_is_current, claim, now=self._now()
            )
            if not current:
                await self._mark_uncertain(claim, "credential_generation_changed")
                return
            send_started = True
            if claim.kind == "media":
                # Metadata was revalidated above and the name is selected from
                # a closed map; neither a source path nor the private blob name
                # crosses the provider boundary.
                assert media_metadata is not None and media_bytes is not None
                mime = str(media_metadata["mime"])
                result = await self.telegram_adapter.send_photo(
                    token,
                    chat_id=claim.telegram_chat_id,
                    data=media_bytes,
                    filename=_MEDIA_FILENAMES[mime],
                    mime_type=mime,
                    caption=claim.caption,
                )
            else:
                assert isinstance(claim.text, str)
                result = await self.telegram_adapter.send_message(
                    token,
                    chat_id=claim.telegram_chat_id,
                    text=claim.text,
                )
            message_id = getattr(result, "message_id", None)
            if not isinstance(message_id, str) or not message_id.isdigit():
                raise TelegramUncertain()
            completed = await self._finish(
                claim,
                OutboxState.SENT,
                telegram_message_id=message_id,
            )
            if completed is None:
                await self._mark_uncertain(claim, "delivery_fence_changed")
                return
            self._circuit_success(claim.bot_account_id)
            await self._audit("outbox_sent", claim)
        except asyncio.CancelledError:
            if send_started:
                await self._mark_uncertain(claim, "send_cancelled")
                await self._audit("outbox_uncertain", claim, "send_cancelled")
            raise
        except SecretUnavailable:
            completed = await self._finish(
                claim, OutboxState.TERMINAL, error_class="credential_unavailable"
            )
            if completed is None:
                await self._mark_uncertain(claim, "credential_generation_changed")
            else:
                await self._fault_pause_credential(
                    claim,
                    status=BotStatus.CREDENTIAL_UNAVAILABLE,
                    error_code="credential_unavailable",
                )
                await self._audit("outbox_terminal", claim, "credential_unavailable")
        except TelegramCredentialInvalid:
            completed = await self._finish(
                claim, OutboxState.TERMINAL, error_class="credential_invalid"
            )
            if completed is None:
                await self._mark_uncertain(claim, "credential_generation_changed")
            else:
                await self._fault_pause_credential(
                    claim,
                    status=BotStatus.CREDENTIAL_INVALID,
                    error_code="credential_invalid",
                )
                await self._audit("outbox_terminal", claim, "credential_invalid")
        except TelegramRetryable as error:
            now = self._now()
            delay = self.retry_delay(claim.attempts, error.retry_after)
            completed = await self._finish(
                claim,
                OutboxState.RETRY_WAIT,
                error_class="retryable",
                next_attempt_at=now + timedelta(seconds=delay),
            )
            if completed is None:
                await self._mark_uncertain(claim, "delivery_fence_changed")
            else:
                self._circuit_retryable(claim.bot_account_id, now)
                retryable_recorded = True
                await self._audit("outbox_retry_wait", claim, "retryable")
        except TelegramUncertain:
            completed = await self._finish(
                claim, OutboxState.UNCERTAIN, error_class="uncertain"
            )
            if completed is None:
                await self._mark_uncertain(claim, "delivery_fence_changed")
            await self._audit("outbox_uncertain", claim, "uncertain")
        except (TelegramTargetUnavailable, TelegramProtocolError) as error:
            error_class = (
                "target_unavailable"
                if isinstance(error, TelegramTargetUnavailable)
                else "protocol_error"
            )
            completed = await self._finish(
                claim, OutboxState.TERMINAL, error_class=error_class
            )
            if completed is None:
                await self._mark_uncertain(claim, "delivery_fence_changed")
            else:
                await self._audit("outbox_terminal", claim, error_class)
        except Exception:
            # Once the provider call starts, an unknown failure has ambiguous
            # delivery outcome.  Before it starts, it is a local terminal fault.
            if send_started:
                await self._mark_uncertain(claim, "unknown_send_outcome")
                await self._audit("outbox_uncertain", claim, "unknown_send_outcome")
            else:
                completed = await self._finish(
                    claim, OutboxState.TERMINAL, error_class="local_delivery_error"
                )
                if completed is None:
                    await self._mark_uncertain(claim, "delivery_fence_changed")
                else:
                    await self._audit("outbox_terminal", claim, "local_delivery_error")
        finally:
            token = ""
            media_bytes = None
            # A half-open probe must end on every outcome, including ambiguous
            # or terminal delivery and task cancellation.  Retryable handling
            # already reopens the circuit; this is an idempotent final fence.
            if retryable_recorded:
                self._cancel_unused_probe(claim.bot_account_id)
            else:
                self._circuit_success(claim.bot_account_id)
            if renewal is not None:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)

    async def _claim_binding(self, binding_id: str, now: datetime) -> OutboxClaim | None:
        task = asyncio.create_task(
            asyncio.to_thread(
                self.channel_store.claim_next_outbox,
                binding_id,
                lease_until=now + timedelta(seconds=self.lease_seconds),
                now=now,
            )
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            # The SQLite transaction cannot be cancelled.  Observe its result
            # and close any lease it committed before propagating shutdown.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            claim = (
                task.result()
                if not task.cancelled() and task.exception() is None
                else None
            )
            if claim is not None:
                await self._mark_uncertain(claim, "claim_cancelled")
            raise cancelled

    async def run_once(self) -> int:
        bindings = await asyncio.to_thread(self.channel_store.delivery_bindings)
        claims: list[OutboxClaim] = []
        try:
            for binding in bindings:
                now = self._now()
                if not self._circuit_allows(binding.bot_account_id, now):
                    continue
                try:
                    claim = await self._claim_binding(binding.id, now)
                except (ChannelConflict, ValueError):
                    claim = None
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    log.warning(
                        "channel outbox claim failed: %s", type(error).__name__
                    )
                    claim = None
                if claim is None:
                    self._cancel_unused_probe(binding.bot_account_id)
                    continue
                claims.append(claim)
        except asyncio.CancelledError:
            if claims:
                await asyncio.gather(
                    *(self._mark_uncertain(claim, "claim_batch_cancelled") for claim in claims),
                    return_exceptions=True,
                )
            raise
        if claims:
            await asyncio.gather(*(self.deliver_claim(claim) for claim in claims))
        return len(claims)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        # The single Runtime supervisor performs startup recovery once before
        # launching any worker.  A worker pass must never reinterpret another
        # live worker's valid lease as a crashed process.
        while not stop_event.is_set():
            try:
                progressed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("channel outbox pass failed: %s", type(error).__name__)
                progressed = 0
            if progressed:
                await asyncio.sleep(0)
                continue
            await self._sleep(self.idle_seconds)


__all__ = [
    "CIRCUIT_FAILURE_THRESHOLD",
    "CIRCUIT_INITIAL_COOLDOWN_SECONDS",
    "CIRCUIT_MAX_COOLDOWN_SECONDS",
    "IDLE_SECONDS",
    "LEASE_SECONDS",
    "MAX_MEDIA_BYTES",
    "MAX_PHOTO_CAPTION",
    "OutboxWorker",
]
