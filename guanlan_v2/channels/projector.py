"""Fail-closed projection from durable Console events into the Channel outbox."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

from guanlan_v2.channels.media_policy import (
    DOCUMENT_ID_RE,
    MEDIA_ID_RE,
    MEDIA_PERMIT_ID_RE,
    caption_contains_location,
)
from guanlan_v2.channels.models import BindingStatus, OutboundPart
from guanlan_v2.channels.security import TELEGRAM_TOKEN_SCAN_RE
from guanlan_v2.channels.security import (
    PAIRING_CODE_RUN_RE,
    PAIRING_CODE_SCAN_RE,
)
from guanlan_v2.channels.store import ChannelConflict, ChannelNotFound


log = logging.getLogger(__name__)

PROJECTABLE_EVENT_TYPES = frozenset({"user_msg", "agent_final", "tool_result"})
DEFAULT_PAGE_SIZE = 200
MAX_TELEGRAM_TEXT_PART = 4000
MAX_TELEGRAM_PHOTO_CAPTION = 900
TELEGRAM_MEDIA_TOOL = "ww_telegram_publish"
TELEGRAM_MEDIA_ARTIFACT_KIND = "telegram_media_publish"
TELEGRAM_DOCUMENT_TOOL = "ww_telegram_publish_document"
TELEGRAM_DOCUMENT_ARTIFACT_KIND = "telegram_document_publish"
_TOKEN_SHAPE_RE = TELEGRAM_TOKEN_SCAN_RE
_CHANNEL_SECRET_SCAN_VERSION = 2
_PAIRING_PROVENANCE_TYPE = "channel_secret_provenance"
_PAIRING_PROVENANCE_KIND = "telegram_pairing_code"
_PAIRING_PROVENANCE_VERSION = 1
_MAX_PAIRING_CODE_CANDIDATES_PER_PAGE = 16


class _UnsafePairingProvenance(RuntimeError):
    """Stop projection when a secret marker cannot be proved internally valid."""


def _iter_pairing_code_windows(value: str):
    for run in PAIRING_CODE_RUN_RE.finditer(value):
        for start in range(run.start(), run.end() - 7):
            yield start, value[start:start + 8]


def _pairing_provenance_index(
    events: list[Mapping[str, Any]],
) -> dict[int, bool]:
    """Map classified source events to secret/non-secret without storing codes.

    The Console migration marker contains only source ids and exact byte spans.
    Validate those spans against the immutable source event before trusting a
    marker.  A non-empty marker suppresses the complete originating event,
    matching the current admission boundary; an empty marker records that an
    ordinary legacy word such as BACKTEST was checked and must stay public.
    """

    events_by_id: dict[int, Mapping[str, Any]] = {}
    markers: list[Mapping[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise _UnsafePairingProvenance("invalid Console event")
        event_id = event.get("id")
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
            raise _UnsafePairingProvenance("invalid Console event id")
        if event_id in events_by_id:
            raise _UnsafePairingProvenance("duplicate Console event id")
        events_by_id[event_id] = event
        if event.get("type") == _PAIRING_PROVENANCE_TYPE:
            markers.append(event)

    classified: dict[int, bool] = {}
    for marker in markers:
        marker_id = int(marker["id"])
        sources = marker.get("sources")
        if (
            marker.get("channel_projection") != "never"
            or marker.get("channel_secret_kind") != _PAIRING_PROVENANCE_KIND
            or marker.get("migration_version") != _PAIRING_PROVENANCE_VERSION
            or not isinstance(sources, list)
            or len(sources) > 64
        ):
            raise _UnsafePairingProvenance("invalid pairing provenance marker")
        for raw_source in sources:
            if not isinstance(raw_source, Mapping) or set(raw_source) != {
                "source_event_id", "spans",
            }:
                raise _UnsafePairingProvenance("invalid pairing provenance source")
            source_id = raw_source.get("source_event_id")
            spans = raw_source.get("spans")
            if (
                isinstance(source_id, bool)
                or not isinstance(source_id, int)
                or source_id < 1
                or source_id >= marker_id
                or not isinstance(spans, list)
                or len(spans) > _MAX_PAIRING_CODE_CANDIDATES_PER_PAGE
            ):
                raise _UnsafePairingProvenance("invalid pairing provenance source")
            secret = bool(spans)
            if source_id in classified and classified[source_id] is not secret:
                raise _UnsafePairingProvenance("conflicting pairing provenance")
            if not secret:
                classified[source_id] = False
                continue
            source = events_by_id.get(source_id)
            text = source.get("text") if isinstance(source, Mapping) else None
            if (
                not isinstance(source, Mapping)
                or source.get("type") != "user_msg"
                or not isinstance(text, str)
            ):
                raise _UnsafePairingProvenance("pairing source is unavailable")
            previous_end = -1
            for raw_span in spans:
                if (
                    not isinstance(raw_span, list)
                    or len(raw_span) != 2
                    or isinstance(raw_span[0], bool)
                    or isinstance(raw_span[1], bool)
                    or not isinstance(raw_span[0], int)
                    or not isinstance(raw_span[1], int)
                ):
                    raise _UnsafePairingProvenance("invalid pairing provenance span")
                start, end = raw_span
                if (
                    start < 0
                    or end != start + 8
                    or end > len(text)
                    or start < previous_end
                    or PAIRING_CODE_SCAN_RE.fullmatch(text[start:end]) is None
                ):
                    raise _UnsafePairingProvenance("invalid pairing provenance span")
                previous_end = end
            classified[source_id] = True
    return classified


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
        # One full provenance validation per durable event horizon.  Paging a
        # long backlog then reuses the code-free id index instead of turning
        # projection into history-size x page-count work.
        self._provenance_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _parts_for_event(event: Mapping[str, Any]) -> tuple[OutboundPart, ...]:
        return ConsoleMessageProjector._projection_for_event(event)[0]

    @staticmethod
    def _projection_for_event(
        event: Mapping[str, Any],
    ) -> tuple[tuple[OutboundPart, ...], str | None]:
        if event.get("channel_projection") == "never":
            return (), None
        event_type = event.get("type")
        if event_type not in PROJECTABLE_EVENT_TYPES:
            return (), None
        if event_type == "tool_result":
            return ConsoleMessageProjector._media_projection_for_event(event)
        text = event.get("text")
        if not isinstance(text, str) or not text:
            return (), None
        # Token/card ingress is rejected again here because a Token is a
        # globally recognizable credential.  Pairing codes are contextual:
        # Console marks the exact known user event ``channel_projection=never``
        # and redacts the same exact value from the Agent stream.  Treating any
        # eight-character uppercase word as a code would drop ordinary analysis
        # such as BACKTEST or TELEGRAM.
        if _TOKEN_SHAPE_RE.search(text):
            return (), None
        return (
            tuple(
                OutboundPart(kind="text", payload_part=index, text=part)
                for index, part in enumerate(split_outbound_text(text))
            ),
            None,
        )

    @staticmethod
    def _media_projection_for_event(
        event: Mapping[str, Any],
    ) -> tuple[tuple[OutboundPart, ...], str | None]:
        """Project only the explicit, successful Telegram media capability.

        Ordinary workspace ``media_image`` artifacts remain conversation-only.
        Requiring the exact capability envelope keeps future tool artifacts
        fail-closed instead of accidentally turning them into outbound sends.
        """
        tool = event.get("tool")
        if tool not in {TELEGRAM_MEDIA_TOOL, TELEGRAM_DOCUMENT_TOOL} \
                or event.get("ok") is not True:
            return (), None
        is_document = tool == TELEGRAM_DOCUMENT_TOOL
        artifact = event.get("artifact")
        if not isinstance(artifact, Mapping):
            return (), None
        if set(artifact) != {"kind", "page", "channel", "payload", "ref"}:
            return (), None
        if (
            artifact.get("kind") != (
                TELEGRAM_DOCUMENT_ARTIFACT_KIND
                if is_document else TELEGRAM_MEDIA_ARTIFACT_KIND
            )
            or artifact.get("page") is not None
            or artifact.get("channel") != "telegram"
        ):
            return (), None
        payload = artifact.get("payload")
        id_key = "document_id" if is_document else "media_id"
        if not isinstance(payload, Mapping) or set(payload) != {id_key, "caption"}:
            return (), None
        media_id = payload.get(id_key)
        caption = payload.get("caption")
        permit_id = artifact.get("ref")
        if (
            not isinstance(media_id, str)
            or (
                DOCUMENT_ID_RE.fullmatch(media_id) is None
                if is_document
                else MEDIA_ID_RE.fullmatch(media_id) is None
            )
            or not isinstance(permit_id, str)
            or MEDIA_PERMIT_ID_RE.fullmatch(permit_id) is None
            or not isinstance(caption, str)
            or "\x00" in caption
            or _TOKEN_SHAPE_RE.search(caption)
            or caption_contains_location(caption)
        ):
            return (), None

        # Telegram allows a longer caption today, but a conservative ceiling
        # gives the product room for future presentation metadata.  When the
        # caption is longer, every leading character becomes an ordinary,
        # durably ordered text item; the final <=900 characters stay attached
        # to the image.  Joining text parts and the final caption is lossless.
        leading = ""
        photo_caption: str | None = caption or None
        if len(caption) > MAX_TELEGRAM_PHOTO_CAPTION:
            leading = caption[:-MAX_TELEGRAM_PHOTO_CAPTION]
            photo_caption = caption[-MAX_TELEGRAM_PHOTO_CAPTION:]
        text_parts = split_outbound_text(leading)
        result = [
            OutboundPart(kind="text", payload_part=index, text=text)
            for index, text in enumerate(text_parts)
        ]
        result.append(
            OutboundPart(
                kind="media",
                payload_part=len(result),
                media_id=media_id,
                caption=photo_caption,
            )
        )
        return tuple(result), permit_id

    @staticmethod
    def _outbound_text_values(parts: tuple[OutboundPart, ...]):
        combined: list[str] = []
        for part in parts:
            if isinstance(part.text, str) and part.text:
                combined.append(part.text)
            if isinstance(part.caption, str) and part.caption:
                combined.append(part.caption)
        if combined:
            # Chunking is deliberately lossless.  Scan the logical payload so
            # a secret cannot hide across two 4,000-character Telegram parts.
            yield "".join(combined)

    def _provenance_for_session(self, session_id: str) -> dict[int, bool]:
        """Incrementally validate only newly appended provenance markers."""

        meta = self.console_store.get_meta(session_id)
        if not isinstance(meta, Mapping):
            raise _UnsafePairingProvenance("Console session is unavailable")
        horizon = meta.get("next_event_id")
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise _UnsafePairingProvenance("invalid Console event horizon")
        through_id = horizon - 1
        state = self._provenance_cache.setdefault(
            session_id, {"cursor": 0, "index": {}},
        )
        cursor = state.get("cursor")
        index = state.get("index")
        if (
            isinstance(cursor, bool)
            or not isinstance(cursor, int)
            or cursor < 0
            or not isinstance(index, dict)
        ):
            raise _UnsafePairingProvenance("invalid provenance cache")
        if cursor >= through_id:
            return dict(index)

        markers: list[Mapping[str, Any]] = []
        iterator = getattr(self.console_store, "iter_events_forward", None)
        if not callable(iterator):
            raise _UnsafePairingProvenance("incremental Console reader unavailable")
        for event in iterator(
            session_id, after_id=cursor, through_id=through_id,
        ):
            if event.get("type") == _PAIRING_PROVENANCE_TYPE:
                markers.append(event)
        if markers:
            source_ids = {
                raw_source.get("source_event_id")
                for marker in markers
                for raw_source in (
                    marker.get("sources")
                    if isinstance(marker.get("sources"), list)
                    else ()
                )
                if isinstance(raw_source, Mapping)
                and isinstance(raw_source.get("spans"), list)
                and raw_source.get("spans")
            }
            source_reader = getattr(self.console_store, "read_events_by_ids", None)
            if not callable(source_reader):
                raise _UnsafePairingProvenance("Console source reader unavailable")
            sources = source_reader(session_id, source_ids)
            new_index = _pairing_provenance_index([
                *sources.values(), *markers,
            ])
            for source_id, secret in new_index.items():
                if source_id in index and index[source_id] is not secret:
                    raise _UnsafePairingProvenance("conflicting pairing provenance")
                index[source_id] = secret
        # ``next_event_id`` may include crash-reserved gaps; advancing to the
        # captured horizon is safe because later appends always receive a
        # strictly larger id.
        state["cursor"] = through_id
        return dict(index)

    def _legacy_secret_source_ids(
        self,
        session_id: str,
        projected: list[tuple[Mapping[str, Any], tuple[OutboundPart, ...]]],
    ) -> set[int]:
        """Resolve exact old pairing-code echoes before any page item commits.

        Current events are classified at Console ingress/egress.  Old builds
        could miss an exact code when it touched another ASCII character.  A
        durable marker wins when present; otherwise all actually projectable
        strings (user, Agent final, and approved media caption) are checked in
        one bounded batch against the original scrypt digests.  Resolving the
        complete page before its first outbox write keeps failure atomic.
        """

        if not any(
            PAIRING_CODE_RUN_RE.search(value)
            for _event, parts in projected
            for value in self._outbound_text_values(parts)
        ):
            return set()
        provenance = self._provenance_for_session(session_id)
        secret_sources = {
            source_id for source_id, secret in provenance.items() if secret
        }
        candidates: set[str] = set()
        candidates_by_source: dict[int, set[str]] = {}
        occurrences = 0
        for event, parts in projected:
            event_id = event.get("id")
            if not isinstance(event_id, int) or isinstance(event_id, bool):
                raise _UnsafePairingProvenance("invalid projectable event id")
            if event_id in provenance:
                continue
            # New user events have already been contextually classified.  All
            # non-user outbound values still receive exact fallback so an old
            # Agent echo or media caption cannot bypass the migration marker.
            if (
                event.get("type") == "user_msg"
                and event.get("channel_secret_scan_version")
                == _CHANNEL_SECRET_SCAN_VERSION
            ):
                continue
            source_candidates: set[str] = set()
            source_occurrences = 0
            for value in self._outbound_text_values(parts):
                for _start, candidate in _iter_pairing_code_windows(value):
                    source_occurrences += 1
                    if (
                        occurrences + source_occurrences
                        > _MAX_PAIRING_CODE_CANDIDATES_PER_PAGE
                    ):
                        # Advancing the cursor while dropping only this
                        # uncertain event is fail-closed without letting 17
                        # ordinary legacy words stall the binding forever.
                        secret_sources.add(event_id)
                        source_candidates.clear()
                        break
                    source_candidates.add(candidate)
                if event_id in secret_sources:
                    break
            if source_candidates:
                occurrences += source_occurrences
                candidates.update(source_candidates)
                candidates_by_source[event_id] = source_candidates
        if candidates:
            from guanlan_v2.channels.pairing import historical_pairing_code_matches

            matches = historical_pairing_code_matches(
                self.channel_store,
                session_id,
                candidates,
                overflow=False,
            )
            secret_sources.update(
                event_id for event_id, source_candidates in candidates_by_source.items()
                if source_candidates.intersection(matches)
            )
        return secret_sources

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
            projected: list[
                tuple[Mapping[str, Any], tuple[OutboundPart, ...]]
            ] = []
            for raw_event in events:
                if not isinstance(raw_event, Mapping):
                    continue
                parts, _media_permit_id = self._projection_for_event(raw_event)
                projected.append((raw_event, parts))
            legacy_secret_source_ids = self._legacy_secret_source_ids(
                binding.session_id, projected,
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
                    parts, media_permit_id = self._projection_for_event(raw_event)
                    if event_id in legacy_secret_source_ids:
                        parts, media_permit_id = (), None
                    self.channel_store.project_event(
                        binding.id,
                        source_event_id=event_id,
                        parts=parts,
                        projection_owner=owner,
                        media_permit_id=media_permit_id,
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
    "MAX_TELEGRAM_PHOTO_CAPTION",
    "PROJECTABLE_EVENT_TYPES",
    "TELEGRAM_MEDIA_ARTIFACT_KIND",
    "TELEGRAM_MEDIA_TOOL",
    "split_outbound_text",
]
