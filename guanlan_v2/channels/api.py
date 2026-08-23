"""Local-only, redacted HTTP control surface for outbound Channels.

This module intentionally parses every Channel request body itself.  A bot
token must never travel through FastAPI's validation error formatter, because
that formatter can include the rejected input in a 422 response.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from guanlan_v2.channels.runtime import ChannelRuntime, ChannelRuntimeError
from guanlan_v2.console.request_security import trusted_channel_request


_MAX_TOKEN_BODY_BYTES = 16 * 1024
_MAX_ACTION_BODY_BYTES = 8 * 1024
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SAFE_ACTION_RE = re.compile(r"^[a-z][a-z0-9:_-]{0,95}$")
_SAFE_ERROR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_VIEW_STATES = frozenset(
    {
        "awaiting_token",
        "awaiting_pairing",
        "pairing_pending",
        "active",
        "paused",
        "retrying",
        "uncertain",
        "needs_reconnect",
        "none",
    }
)
_AUTOMATION_STATES = frozenset({"enabled", "paused", "blocked", "error"})
_DELIVERY_STATES = frozenset(
    {"pending", "sending", "sent", "retry_wait", "uncertain", "terminal", "cancelled"}
)
_ACTION_ALIASES = {
    "submit-token": "telegram:submit-token",
    "telegram:submit-token": "telegram:submit-token",
    "pause": "telegram:pause",
    "telegram:pause": "telegram:pause",
    "resume": "telegram:resume",
    "telegram:resume": "telegram:resume",
    "reverify": "telegram:reverify",
    "telegram:reverify": "telegram:reverify",
    "replace-token": "telegram:replace-token",
    "telegram:replace-token": "telegram:replace-token",
    "disconnect": "telegram:disconnect",
    "telegram:disconnect": "telegram:disconnect",
}
_RESOLUTION_ACTIONS = frozenset({"assume_sent", "resend", "drop", "retry"})
_INVALID = object()
_TOO_LARGE = object()
_RUNTIME_RESOLUTION_FAILED = object()


def _error(reason_code: str, status_code: int = 400) -> JSONResponse:
    """Return an intentionally small, input-free failure body."""
    return JSONResponse({"ok": False, "reason_code": reason_code}, status_code=status_code)


def _available_false_status(session_id: str) -> dict[str, Any]:
    """Stable feature-gate fallback; it never includes a failure repr."""
    return {
        "available": False,
        "session_id": _safe_identifier(session_id) or "",
        "view_state": "none",
        "draft": None,
        "bot": None,
        "recipient": None,
        "pairing_requests": [],
        "counts": {"pending": 0, "retry_wait": 0, "uncertain": 0, "terminal": 0},
        "warnings": [],
        "recent_deliveries": [],
        "automations": [],
    }


async def _read_json_object(request: Request, *, maximum: int) -> object:
    """Read an object body with a hard streaming cap and no validation echo."""
    data = bytearray()
    try:
        async for chunk in request.stream():
            if not isinstance(chunk, bytes) or len(data) + len(chunk) > maximum:
                return _TOO_LARGE
            data.extend(chunk)
    except Exception:
        return _INVALID
    try:
        decoded = json.loads(bytes(data))
    except Exception:
        return _INVALID
    return decoded if isinstance(decoded, dict) else _INVALID


def _runtime_from(source: Any) -> Any | None:
    """Resolve an optional runtime without confusing failure with feature-off."""
    if source is None:
        return None
    try:
        resolved = source() if callable(source) else source
    except Exception:
        return _RUNTIME_RESOLUTION_FAILED
    # A supplied resolver returning None is a failed injected dependency, not
    # the backwards-compatible ``channel_runtime=None`` feature gate.
    return _RUNTIME_RESOLUTION_FAILED if resolved is None else resolved


async def _call_sync(runtime: Any, name: str, *args: Any) -> Any:
    """Run a synchronous runtime/store operation away from the event loop."""
    method = getattr(runtime, name, None)
    if not callable(method):
        raise ChannelRuntimeError("channel_unavailable")
    result = await asyncio.to_thread(method, *args)
    return await result if inspect.isawaitable(result) else result


async def _call_async(runtime: Any, name: str, *args: Any) -> Any:
    """Call the runtime's network-aware method without serialising its input."""
    method = getattr(runtime, name, None)
    if not callable(method):
        raise ChannelRuntimeError("channel_unavailable")
    result = method(*args)
    return await result if inspect.isawaitable(result) else result


def _safe_reason(exc: BaseException, fallback: str) -> str:
    candidate = getattr(exc, "reason_code", None)
    if isinstance(candidate, str) and _SAFE_ERROR_RE.fullmatch(candidate):
        return candidate
    return fallback


def _safe_identifier(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not _SAFE_ID_RE.fullmatch(value)
        or ChannelRuntime.token_matches(value)
    ):
        return None
    return value


def _safe_text(value: object, *, limit: int) -> str | None:
    """Allow presentation text only if it is bounded and cannot be a token."""
    if not isinstance(value, str):
        return None
    original = value.strip()
    if not original or ChannelRuntime.token_matches(original):
        return None
    return original[:limit]


def _stable_status(value: object, session_id: str, *, available: bool = True) -> dict[str, Any]:
    """Project only v1 fields, protecting the API if a future runtime regresses."""
    source: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    draft = source.get("draft") if isinstance(source.get("draft"), Mapping) else None
    bot = source.get("bot") if isinstance(source.get("bot"), Mapping) else None
    recipient = source.get("recipient") if isinstance(source.get("recipient"), Mapping) else None

    safe_draft_id = _safe_identifier(draft.get("draft_id")) if draft else None
    safe_bot = None
    if bot is not None:
        safe_bot = {
            "display_name": _safe_text(bot.get("display_name"), limit=128) or "",
            "username": _safe_text(bot.get("username"), limit=64),
        }
    safe_recipient = None
    if recipient is not None:
        user_id = _safe_identifier(str(recipient.get("user_id"))) if recipient.get("user_id") is not None else None
        if user_id is not None:
            safe_recipient = {
                "user_id": user_id,
                "username": _safe_text(recipient.get("username"), limit=64),
                "display_name": _safe_text(recipient.get("display_name"), limit=256),
            }

    raw_counts = source.get("counts") if isinstance(source.get("counts"), Mapping) else {}
    counts = {
        name: max(0, int(raw_counts.get(name, 0))) if isinstance(raw_counts.get(name, 0), int) else 0
        for name in ("pending", "retry_wait", "uncertain", "terminal")
    }

    pairing_requests: list[dict[str, Any]] = []
    raw_pairings = source.get("pairing_requests")
    if isinstance(raw_pairings, list):
        for item in raw_pairings[:50]:
            if not isinstance(item, Mapping):
                continue
            request_id = _safe_identifier(item.get("request_id"))
            user_id = _safe_identifier(str(item.get("user_id"))) if item.get("user_id") is not None else None
            if request_id is None or user_id is None:
                continue
            pairing_requests.append(
                {
                    "request_id": request_id,
                    "user_id": user_id,
                    "username": _safe_text(item.get("username"), limit=64),
                    "display_name": _safe_text(item.get("display_name"), limit=256),
                    "code_hint": _safe_text(item.get("code_hint"), limit=8),
                    "created_at": _safe_text(item.get("created_at"), limit=64),
                    "expires_at": _safe_text(item.get("expires_at"), limit=64),
                }
            )

    warnings: list[dict[str, str]] = []
    raw_warnings = source.get("warnings")
    if isinstance(raw_warnings, list):
        for item in raw_warnings[:20]:
            code = item.get("error_code") if isinstance(item, Mapping) else None
            if isinstance(code, str) and _SAFE_ERROR_RE.fullmatch(code):
                warnings.append({"error_code": code})

    deliveries: list[dict[str, Any]] = []
    raw_deliveries = source.get("recent_deliveries")
    if isinstance(raw_deliveries, list):
        for item in raw_deliveries[:20]:
            if not isinstance(item, Mapping):
                continue
            outbox_id = _safe_identifier(item.get("outbox_id"))
            kind = item.get("kind")
            state = item.get("state")
            if outbox_id is None or kind not in {"text", "media"} or state not in _DELIVERY_STATES:
                continue
            media_id = item.get("media_id")
            deliveries.append(
                {
                    "outbox_id": outbox_id,
                    "kind": kind,
                    "state": state,
                    "media_id": _safe_identifier(media_id) if media_id is not None else None,
                    "created_at": _safe_text(item.get("created_at"), limit=64),
                    "sent_at": _safe_text(item.get("sent_at"), limit=64),
                    "attempts": max(0, int(item.get("attempts", 0))) if isinstance(item.get("attempts", 0), int) else 0,
                    "error_class": _safe_text(item.get("error_class"), limit=96),
                }
            )

    automations: list[dict[str, Any]] = []
    raw_automations = source.get("automations")
    if isinstance(raw_automations, list):
        for item in raw_automations[:50]:
            if not isinstance(item, Mapping):
                continue
            automation_id = _safe_identifier(item.get("automation_id"))
            status = item.get("status")
            if automation_id is None or status not in _AUTOMATION_STATES:
                continue
            automations.append(
                {
                    "automation_id": automation_id,
                    "name": _safe_text(item.get("name"), limit=128) or "",
                    "schedule_kind": _safe_text(item.get("schedule_kind"), limit=32) or "",
                    "next_run_at": _safe_text(item.get("next_run_at"), limit=64),
                    "last_run_at": _safe_text(item.get("last_run_at"), limit=64),
                    "status": status,
                    "pause_reason": _safe_text(item.get("pause_reason"), limit=96),
                }
            )

    state = source.get("view_state")
    return {
        "available": bool(available and source.get("available", True)),
        "session_id": _safe_identifier(session_id) or "",
        "view_state": state if state in _VIEW_STATES else "none",
        "draft": {"draft_id": safe_draft_id} if safe_draft_id is not None else None,
        "bot": safe_bot,
        "recipient": safe_recipient,
        "pairing_requests": pairing_requests,
        "counts": counts,
        "warnings": warnings,
        "recent_deliveries": deliveries,
        "automations": automations,
    }


async def _status(runtime: Any, session_id: str) -> dict[str, Any]:
    try:
        return _stable_status(await _call_sync(runtime, "status_for_session", session_id), session_id)
    except Exception:
        return _available_false_status(session_id)


def _ticket_value(payload: Mapping[str, Any]) -> str | None:
    for name in ("request_token", "action_ticket", "ticket"):
        value = payload.get(name)
        if isinstance(value, str) and 0 < len(value) <= 512:
            return value
    return None


async def _consume_ticket(
    runtime: Any,
    payload: Mapping[str, Any],
    sid: str,
    action: str,
    subject_id: str,
) -> bool:
    ticket = _ticket_value(payload)
    if ticket is None:
        return False
    try:
        return bool(
            await _call_sync(runtime, "consume_action_ticket", ticket, sid, action, subject_id)
        )
    except Exception:
        return False


def _canonical_action(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    direct = _ACTION_ALIASES.get(value)
    if direct is not None:
        return direct
    prefix = "telegram:resolve:"
    if value.startswith(prefix) and value[len(prefix):] in _RESOLUTION_ACTIONS:
        return value
    return None


def build_channel_router(channel_runtime: Any = None) -> APIRouter:
    """Build the optional Channel router without expanding the general CORS surface."""
    router = APIRouter(prefix="/console/channels", tags=["channels"])
    # Compatibility-only fallback for narrow test facades predating the
    # Runtime-owned reservation API.  Production ChannelRuntime always owns
    # this state so Console and the dedicated endpoint share it.
    legacy_secret_reservations: set[str] = set()

    def runtime() -> Any | None:
        return _runtime_from(channel_runtime)

    def trusted(request: Request) -> JSONResponse | None:
        return None if trusted_channel_request(request) else _error("channel_origin_forbidden", 403)

    def unavailable(current: Any) -> bool:
        return current is None or current is _RUNTIME_RESOLUTION_FAILED

    async def writable(current: Any, sid: str) -> JSONResponse | None:
        """Reject archived sessions/workspaces before ticket/network/write work."""
        method = getattr(current, "assert_session_mutable", None)
        if not callable(method):
            # Compatibility for narrow injected test/runtime facades.  The
            # production ChannelRuntime always supplies the durable gate.
            return None
        try:
            await _call_sync(current, "assert_session_mutable", sid)
        except Exception as exc:
            return _error(_safe_reason(exc, "channel_session_read_only"), 409)
        return None

    async def reserve_secret_submission(
        current: Any, sid: str
    ) -> tuple[bool, JSONResponse | None]:
        """Claim a nonblocking per-session Token handoff before getMe."""
        method = getattr(current, "reserve_secret_submission", None)
        if callable(method):
            try:
                acquired = bool(await _call_sync(current, "reserve_secret_submission", sid))
            except Exception as exc:
                return False, _error(_safe_reason(exc, "channel_session_read_only"), 409)
            if not acquired:
                return False, _error("telegram_token_submission_busy", 409)
            return True, None
        # Old focused fakes have no Console-facing lifecycle methods.  Keep
        # their own endpoint serialised without weakening production sharing.
        if sid in legacy_secret_reservations:
            return False, _error("telegram_token_submission_busy", 409)
        legacy_secret_reservations.add(sid)
        return True, None

    async def release_secret_submission(current: Any, sid: str) -> None:
        method = getattr(current, "release_secret_submission", None)
        if callable(method):
            try:
                await _call_sync(current, "release_secret_submission", sid)
            except Exception:
                # A leaked reservation is safer than making a concurrent
                # secret handoff invisible; Runtime's concrete release cannot
                # fail under normal operation.
                pass
            return
        legacy_secret_reservations.discard(sid)

    async def owned_subject(
        current: Any,
        sid: str,
        action: str,
        supplied: object,
    ) -> tuple[str | None, JSONResponse | None]:
        """Resolve ownership server-side; browser IDs are equality assertions."""
        supplied_id = _safe_identifier(supplied) if supplied is not None else None
        if supplied is not None and supplied_id is None:
            return None, _error("channel_action_invalid")
        if action.startswith("telegram:resolve:"):
            if supplied_id is None:
                return None, _error("channel_action_invalid")
            try:
                belongs = await _call_sync(current, "delivery_belongs_to_session", sid, supplied_id)
            except Exception as exc:
                return None, _error(_safe_reason(exc, "channel_action_invalid"), 409)
            if not bool(belongs):
                return None, _error("channel_action_invalid", 409)
            return supplied_id, None

        resolver = getattr(current, "action_subject_for_session", None)
        if not callable(resolver):
            # Legacy fakes remain usable, but production never signs an
            # arbitrary browser-supplied subject.
            return supplied_id, None if supplied_id is not None else _error("channel_action_invalid")
        try:
            actual = await _call_sync(current, "action_subject_for_session", sid, action)
        except Exception as exc:
            return None, _error(_safe_reason(exc, "channel_action_invalid"), 409)
        actual_id = _safe_identifier(actual)
        if actual_id is None or (supplied_id is not None and supplied_id != actual_id):
            return None, _error("channel_action_invalid", 409)
        return actual_id, None

    @router.get("/session/{sid}")
    async def channel_status(request: Request, sid: str):
        denied = trusted(request)
        if denied is not None:
            return denied
        if _safe_identifier(sid) is None:
            return _error("channel_status_invalid")
        current = runtime()
        if unavailable(current):
            return JSONResponse(_available_false_status(sid), status_code=503)
        status = await _status(current, sid)
        return JSONResponse(status, status_code=200 if status["available"] else 503)

    @router.post("/session/{sid}/request-token")
    async def request_token(request: Request, sid: str):
        denied = trusted(request)
        if denied is not None:
            return denied
        payload = await _read_json_object(request, maximum=_MAX_ACTION_BODY_BYTES)
        if payload is _TOO_LARGE or payload is _INVALID:
            return _error("channel_ticket_request_invalid")
        assert isinstance(payload, dict)
        action = _canonical_action(payload.get("action"))
        supplied_subject = payload.get("subject_id", payload.get("draft_id"))
        if action is None or _safe_identifier(sid) is None:
            return _error("channel_ticket_request_invalid")
        current = runtime()
        if unavailable(current):
            return _error("channel_unavailable", 503)
        denied_mutation = await writable(current, sid)
        if denied_mutation is not None:
            return denied_mutation
        subject_id, subject_error = await owned_subject(current, sid, action, supplied_subject)
        if subject_error is not None:
            return subject_error
        assert subject_id is not None
        try:
            issued = await _call_sync(current, "issue_action_ticket", sid, action, subject_id)
        except Exception as exc:
            return _error(_safe_reason(exc, "channel_ticket_unavailable"), 503)
        if not isinstance(issued, str) or not issued:
            return _error("channel_ticket_unavailable", 503)
        return {
            "ok": True,
            "request_token": issued,
            "ticket": issued,
            "action": action,
            "subject_id": subject_id,
            "expires_in_seconds": 120,
        }

    @router.post("/telegram/token")
    async def submit_telegram_token(request: Request):
        denied = trusted(request)
        if denied is not None:
            return denied
        payload: object = await _read_json_object(request, maximum=_MAX_TOKEN_BODY_BYTES)
        token = ""
        try:
            if payload is _TOO_LARGE or payload is _INVALID:
                return _error("telegram_token_submission_invalid")
            assert isinstance(payload, dict)
            sid = _safe_identifier(payload.get("sid"))
            draft_id = _safe_identifier(payload.get("draft_id"))
            supplied = payload.get("token")
            if sid is None or draft_id is None or not isinstance(supplied, str) or not supplied:
                return _error("telegram_token_submission_invalid")
            if len(supplied) > 1024:
                return _error("telegram_token_submission_invalid")
            token = supplied
            current = runtime()
            if unavailable(current):
                return _error("channel_unavailable", 503)
            denied_mutation = await writable(current, sid)
            if denied_mutation is not None:
                return denied_mutation
            try:
                matches = await _call_sync(current, "extract_telegram_tokens", token)
            except Exception:
                return _error("telegram_token_submission_invalid")
            if not isinstance(matches, (tuple, list)) or len(matches) != 1 or token != matches[0]:
                return _error("telegram_token_submission_invalid")
            try:
                resolver = getattr(current, "token_submission_target", None)
                if callable(resolver):
                    target = await _call_sync(current, "token_submission_target", sid)
                    draft = target[0] if isinstance(target, tuple) and target else None
                else:
                    draft = await _call_sync(current, "current_draft", sid)
            except Exception as exc:
                return _error(_safe_reason(exc, "telegram_token_needs_draft"), 409)
            if draft is None or getattr(draft, "id", None) != draft_id:
                return _error("telegram_token_needs_draft", 409)
            reserved, reservation_error = await reserve_secret_submission(current, sid)
            if reservation_error is not None:
                return reservation_error
            assert reserved
            try:
                if not await _consume_ticket(
                    current, payload, sid, "telegram:submit-token", draft_id
                ):
                    return _error("telegram_token_secure_submit_required", 403)
                try:
                    await _call_async(current, "submit_token", sid, draft_id, token)
                except Exception as exc:
                    return _error(_safe_reason(exc, "telegram_token_submission_failed"), 409)
                try:
                    # Unlike /console/send this records no Agent turn.  The sole
                    # durable trace is a projection-suppressed placeholder.
                    await _call_sync(current, "record_secret_placeholder", sid)
                except Exception:
                    # The credential was already installed.  Reporting a safe
                    # partial success is more honest than returning a generic
                    # failure that would prompt a non-replayable retry.
                    return {
                        "ok": True,
                        "secret_submitted": True,
                        "secret_placeholder_recorded": False,
                        "status": await _status(current, sid),
                    }
                return {"ok": True, "secret_submitted": True, "status": await _status(current, sid)}
            finally:
                await release_secret_submission(current, sid)
        finally:
            # This is reference shortening, not a claim of memory zeroisation.
            token = ""
            payload = None

    async def _session_action(
        request: Request,
        sid: str,
        *,
        action: str,
        method: str | None,
        asynchronous: bool = False,
    ):
        denied = trusted(request)
        if denied is not None:
            return denied
        payload = await _read_json_object(request, maximum=_MAX_ACTION_BODY_BYTES)
        if payload is _TOO_LARGE or payload is _INVALID:
            return _error("channel_action_invalid")
        assert isinstance(payload, dict)
        supplied_subject = payload.get("subject_id", payload.get("draft_id"))
        if _safe_identifier(sid) is None:
            return _error("channel_action_invalid")
        current = runtime()
        if unavailable(current):
            return _error("channel_unavailable", 503)
        denied_mutation = await writable(current, sid)
        if denied_mutation is not None:
            return denied_mutation
        subject_id, subject_error = await owned_subject(current, sid, action, supplied_subject)
        if subject_error is not None:
            return subject_error
        assert subject_id is not None
        if not await _consume_ticket(current, payload, sid, action, subject_id):
            return _error("channel_action_ticket_required", 403)
        try:
            if method is not None:
                if asynchronous:
                    await _call_async(current, method, sid)
                else:
                    await _call_sync(current, method, sid)
        except Exception as exc:
            return _error(_safe_reason(exc, "channel_action_failed"), 409)
        return {"ok": True, "status": await _status(current, sid)}

    @router.post("/session/{sid}/pause")
    async def pause(request: Request, sid: str):
        return await _session_action(request, sid, action="telegram:pause", method="pause")

    @router.post("/session/{sid}/resume")
    async def resume(request: Request, sid: str):
        return await _session_action(request, sid, action="telegram:resume", method="resume")

    @router.post("/session/{sid}/reverify")
    async def reverify(request: Request, sid: str):
        return await _session_action(
            request, sid, action="telegram:reverify", method="reverify", asynchronous=True
        )

    @router.post("/session/{sid}/replace-token")
    async def replace_token(request: Request, sid: str):
        # Raw tokens go only to /telegram/token.  This ticket-protected action
        # creates a durable replacement intent/secure-card state first.
        return await _session_action(
            request, sid, action="telegram:replace-token", method="prepare_replacement"
        )

    @router.post("/session/{sid}/disconnect")
    async def disconnect(request: Request, sid: str):
        return await _session_action(
            request, sid, action="telegram:disconnect", method="disconnect"
        )

    @router.post("/deliveries/{item_id}/resolve")
    async def resolve_delivery(request: Request, item_id: str):
        denied = trusted(request)
        if denied is not None:
            return denied
        payload = await _read_json_object(request, maximum=_MAX_ACTION_BODY_BYTES)
        if payload is _TOO_LARGE or payload is _INVALID:
            return _error("channel_action_invalid")
        assert isinstance(payload, dict)
        sid = _safe_identifier(payload.get("sid"))
        resolution = payload.get("action")
        explicit_subject = payload.get("subject_id")
        if (
            sid is None
            or not isinstance(resolution, str)
            or resolution not in _RESOLUTION_ACTIONS
            or _safe_identifier(item_id) is None
            or (explicit_subject is not None and explicit_subject != item_id)
        ):
            return _error("channel_action_invalid")
        current = runtime()
        if unavailable(current):
            return _error("channel_unavailable", 503)
        denied_mutation = await writable(current, sid)
        if denied_mutation is not None:
            return denied_mutation
        try:
            belongs = await _call_sync(current, "delivery_belongs_to_session", sid, item_id)
        except Exception as exc:
            return _error(_safe_reason(exc, "channel_action_invalid"), 409)
        if not bool(belongs):
            return _error("channel_action_invalid", 409)
        ticket_action = f"telegram:resolve:{resolution}"
        if not await _consume_ticket(current, payload, sid, ticket_action, item_id):
            return _error("channel_action_ticket_required", 403)
        try:
            await _call_sync(current, "resolve_delivery", sid, item_id, resolution)
        except Exception as exc:
            return _error(_safe_reason(exc, "channel_action_failed"), 409)
        return {"ok": True, "status": await _status(current, sid)}

    return router


__all__ = ["build_channel_router"]
