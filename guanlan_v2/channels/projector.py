"""Fail-closed projection from durable Console events into the Channel outbox."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from guanlan_v2.channels.models import BindingStatus, OutboundPart
from guanlan_v2.channels.store import ChannelConflict, ChannelNotFound


log = logging.getLogger(__name__)

PROJECTABLE_EVENT_TYPES = frozenset({"user_msg", "agent_final"})
DEFAULT_PAGE_SIZE = 200
MAX_TELEGRAM_TEXT_PART = 4000
_TOKEN_SHAPE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:bot)?[0-9]{5,16}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_PAIRING_CODE_SHAPE_RE = re.compile(
    r"(?<![0-9A-Z])[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}(?![0-9A-Z])"
)


def split_outbound_text(
    text: str,
    *,
    max_chars: int = MAX_TELEGRAM_TEXT_PART,
) -> tuple[str, ...]:
    """Split text deterministically without changing or dropping characters.

    Paragraph, line, and whitespace boundaries are preferred in that order;
    text with no usable boundary is split at the Unicode character boundary.
    The deliberately conservative 4,000-character ceiling leaves room below
    Telegram's current protocol limit.
    """
    if not isinstance(text, str) or not text:
        return ()
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    result: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        hard_end = min(start + max_chars, length)
        if hard_end == length:
            result.append(text[start:hard_end])
            break
        window = text[start:hard_end]
        cut = 0
        for boundary in ("\n\n", "\n"):
            position = window.rfind(boundary)
            if position >= 0:
                cut = position + len(boundary)
                break
        if cut == 0:
            for position in range(len(window) - 1, -1, -1):
                if window[position].isspace():
                    cut = position + 1
                    break
        if cut <= 0:
            cut = len(window)
        result.append(text[start : start + cut])
        start += cut
    return tuple(part for part in result if part)


class ConsoleMessageProjector:
    """Advance one binding cursor over an explicit, tiny event whitelist."""

    def __init__(
        self,
        console_store: Any,
        channel_store: Any,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        idle_seconds: float = 0.5,
        lease_seconds: int = 30,
    ) -> None:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
            raise ValueError("page_size must be a positive integer")
        if not isinstance(idle_seconds, (int, float)) or idle_seconds <= 0:
            raise ValueError("idle_seconds must be positive")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) \
                or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        self.console_store = console_store
        self.channel_store = channel_store
        self.page_size = int(page_size)
        self.idle_seconds = float(idle_seconds)
        self.lease_seconds = int(lease_seconds)

    @staticmethod
    def _parts_for_event(event: Mapping[str, Any]) -> tuple[OutboundPart, ...]:
        if event.get("channel_projection") == "never":
            return ()
        if event.get("type") not in PROJECTABLE_EVENT_TYPES:
            return ()
        text = event.get("text")
        if not isinstance(text, str) or not text:
            return ()
        # Token/card ingress and pairing-code user messages are marked never by
        # Console.  Repeat the guard here so a legacy or hand-written event can
        # never strand secret material in Channel SQLite.
        if _TOKEN_SHAPE_RE.search(text) or _PAIRING_CODE_SHAPE_RE.search(text):
            return ()
        return tuple(
            OutboundPart(kind="text", payload_part=index, text=part)
            for index, part in enumerate(split_outbound_text(text))
        )

    def project_once(self, binding_id: str) -> int:
        """Project at most one oldest-first page and return advanced events."""
        try:
            owner = self.channel_store.acquire_projection_lease(
                binding_id,
                lease_seconds=self.lease_seconds,
            )
        except ChannelNotFound:
            return 0
        if owner is None:
            return 0
        try:
            # Re-read only after acquisition.  A competing generation may have
            # advanced the cursor before this owner won the durable lease.
            binding = self.channel_store.binding(binding_id)
            if binding.status is not BindingStatus.ACTIVE:
                return 0
            events = self.console_store.read_events_forward(
                binding.session_id,
                after_id=max(
                    int(binding.projection_cursor_event_id),
                    int(binding.sync_from_event_id),
                ),
                limit=self.page_size,
            )
            advanced = 0
            cursor = int(binding.projection_cursor_event_id)
            renew_after = time.monotonic() + max(1.0, self.lease_seconds / 3.0)
            for raw_event in events:
                if time.monotonic() >= renew_after:
                    if not self.channel_store.renew_projection_lease(
                        binding.id,
                        lease_owner=owner,
                        lease_seconds=self.lease_seconds,
                    ):
                        break
                    renew_after = time.monotonic() + max(
                        1.0,
                        self.lease_seconds / 3.0,
                    )
                if not isinstance(raw_event, Mapping):
                    continue
                event_id = raw_event.get("id")
                if (
                    isinstance(event_id, bool)
                    or not isinstance(event_id, int)
                    or event_id <= cursor
                ):
                    continue
                try:
                    self.channel_store.project_event(
                        binding.id,
                        source_event_id=event_id,
                        parts=self._parts_for_event(raw_event),
                        projection_owner=owner,
                    )
                except (ChannelConflict, ChannelNotFound):
                    # Pause/disconnect/lease expiry raced this page.  A future
                    # owner starts from the durable cursor.
                    break
                cursor = event_id
                advanced += 1
            return advanced
        finally:
            try:
                self.channel_store.release_projection_lease(
                    binding_id,
                    lease_owner=owner,
                )
            except Exception:
                pass

    def project_all_once(self) -> int:
        total = 0
        for binding in self.channel_store.live_bindings():
            if binding.status is BindingStatus.ACTIVE:
                try:
                    total += self.project_once(binding.id)
                except Exception as error:
                    log.warning(
                        "channel binding projection failed: %s",
                        type(error).__name__,
                    )
        return total

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                pass_task = asyncio.create_task(asyncio.to_thread(self.project_all_once))
                try:
                    progressed = await asyncio.shield(pass_task)
                except asyncio.CancelledError as cancelled:
                    # Cancelling ``to_thread`` only cancels its awaiter.  Wait
                    # for the current page's SQLite transactions to settle so
                    # Runtime.stop has a real durability boundary.
                    while not pass_task.done():
                        try:
                            await asyncio.shield(pass_task)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not pass_task.cancelled():
                        pass_task.exception()
                    raise cancelled
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Provider/user payloads are never logged.  The exception class
                # is enough for local health diagnostics.
                log.warning("channel projector pass failed: %s", type(error).__name__)
                progressed = 0
            if progressed:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.idle_seconds)
            except TimeoutError:
                pass


__all__ = [
    "ConsoleMessageProjector",
    "DEFAULT_PAGE_SIZE",
    "MAX_TELEGRAM_TEXT_PART",
    "PROJECTABLE_EVENT_TYPES",
    "split_outbound_text",
]
