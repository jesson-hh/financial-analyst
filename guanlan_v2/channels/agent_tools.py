"""Session-scoped Agent controls for the outbound Channel subsystem.

This module is deliberately narrower than the local HTTP management surface:
the model never selects a Console session, Bot account, Telegram chat, user, or
local file.  Those capabilities come only from the trusted turn ContextVars.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import re
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Mapping

from guanlan_v2.channels.media_policy import (
    ATTACHMENT_ID_RE,
    DOCUMENT_ID_RE,
    MEDIA_ID_RE,
    MEDIA_PERMIT_ID_RE,
    caption_contains_location,
    media_call_key,
)
from guanlan_v2.channels.security import TELEGRAM_TOKEN_SCAN_RE

_SAFE_REASON_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_TOKEN_RE = TELEGRAM_TOKEN_SCAN_RE
_ASYNC_TOOL_TIMEOUT_SECONDS = 180.0
_MAX_MEDIA_CAPTION_CHARS = 50_000


class _ChannelToolInvocationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ChannelToolContext:
    """Compatibility carrier used by focused tests and non-Console callers."""

    runtime: Any
    owner_loop: asyncio.AbstractEventLoop | None
    owner_thread_id: int | None

    @classmethod
    def capture(cls, runtime: Any) -> "ChannelToolContext":
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        return cls(
            runtime=runtime,
            owner_loop=loop,
            owner_thread_id=threading.get_ident() if loop is not None else None,
        )


# The Console sets these for one turn and resets them in ``finally``.  Buddy
# runs synchronous tools through ``asyncio.to_thread``, which copies the
# ContextVar context into the worker.  Async Channel operations are handed back
# to the owning ASGI loop with ``run_coroutine_threadsafe``; a new event loop is
# never created around the shared Telegram client.
CTX_CHANNEL_RUNTIME: contextvars.ContextVar = contextvars.ContextVar(
    "weiwo_channel_runtime", default=None
)
CTX_CHANNEL_OWNER_LOOP: contextvars.ContextVar = contextvars.ContextVar(
    "weiwo_channel_owner_loop", default=None
)
CTX_CHANNEL_OWNER_THREAD: contextvars.ContextVar = contextvars.ContextVar(
    "weiwo_channel_owner_thread", default=None
)
CTX_MEDIA_PUBLICATION_TICKETS: contextvars.ContextVar = contextvars.ContextVar(
    "weiwo_media_publication_tickets", default=None
)


def stash_media_publication_ticket(
    media_id: str,
    caption: str,
    ticket: str,
) -> bool:
    """Store one proof in the current turn without exposing it to tool args."""
    mapping = CTX_MEDIA_PUBLICATION_TICKETS.get(None)
    if (
        not isinstance(mapping, dict)
        or not isinstance(media_id, str)
        or ATTACHMENT_ID_RE.fullmatch(media_id) is None
        or not isinstance(caption, str)
        or caption_contains_location(caption)
        or not isinstance(ticket, str)
        or not ticket
        or len(ticket) > 512
    ):
        return False
    mapping[media_call_key(media_id, caption)] = ticket
    return True


def _pop_media_publication_ticket(media_id: str, caption: str) -> str | None:
    mapping = CTX_MEDIA_PUBLICATION_TICKETS.get(None)
    if not isinstance(mapping, dict):
        return None
    value = mapping.pop(media_call_key(media_id, caption), None)
    return value if isinstance(value, str) and value else None


def validated_telegram_publish_args(
    args: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """Return the exact safe media request or fail closed without echoing it."""
    if not isinstance(args, Mapping) or not set(args).issubset(
        {"media_id", "caption"}
    ):
        return None
    if "media_id" not in args:
        return None
    media_id = args.get("media_id")
    caption = args.get("caption", "")
    if not isinstance(media_id, str) or MEDIA_ID_RE.fullmatch(media_id) is None:
        return None
    if (
        not isinstance(caption, str)
        or len(caption) > _MAX_MEDIA_CAPTION_CHARS
        or "\x00" in caption
        or _TOKEN_RE.search(caption)
        or caption_contains_location(caption)
    ):
        return None
    return media_id, caption


def validated_telegram_document_publish_args(
    args: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    if not isinstance(args, Mapping) or not set(args).issubset(
        {"document_id", "caption"}
    ):
        return None
    if "document_id" not in args:
        return None
    document_id = args.get("document_id")
    caption = args.get("caption", "")
    if (
        not isinstance(document_id, str)
        or DOCUMENT_ID_RE.fullmatch(document_id) is None
        or not isinstance(caption, str)
        or len(caption) > _MAX_MEDIA_CAPTION_CHARS
        or "\x00" in caption
        or _TOKEN_RE.search(caption)
        or caption_contains_location(caption)
    ):
        return None
    return document_id, caption


def public_telegram_publish_artifact(value: object) -> dict[str, Any] | None:
    """Return the only Telegram media artifact safe for Console/SSE storage."""
    if not isinstance(value, Mapping) or set(value) != {
        "kind", "page", "channel", "payload", "ref"
    }:
        return None
    validated = validated_telegram_publish_args(
        value.get("payload") if isinstance(value.get("payload"), Mapping) else None
    )
    permit_id = value.get("ref")
    if (
        value.get("kind") != "telegram_media_publish"
        or value.get("page") is not None
        or value.get("channel") != "telegram"
        or validated is None
        or not isinstance(permit_id, str)
        or MEDIA_PERMIT_ID_RE.fullmatch(permit_id) is None
    ):
        return None
    media_id, caption = validated
    return {
        "kind": "telegram_media_publish",
        "page": None,
        "channel": "telegram",
        "payload": {"media_id": media_id, "caption": caption},
        "ref": permit_id,
    }


def public_telegram_document_publish_artifact(
    value: object,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "kind", "page", "channel", "payload", "ref"
    }:
        return None
    validated = validated_telegram_document_publish_args(
        value.get("payload") if isinstance(value.get("payload"), Mapping) else None
    )
    permit_id = value.get("ref")
    if (
        value.get("kind") != "telegram_document_publish"
        or value.get("page") is not None
        or value.get("channel") != "telegram"
        or validated is None
        or not isinstance(permit_id, str)
        or MEDIA_PERMIT_ID_RE.fullmatch(permit_id) is None
    ):
        return None
    document_id, caption = validated
    return {
        "kind": "telegram_document_publish",
        "page": None,
        "channel": "telegram",
        "payload": {"document_id": document_id, "caption": caption},
        "ref": permit_id,
    }


def _failure(reason_code: str, content: str) -> dict[str, Any]:
    return {"ok": False, "reason_code": reason_code, "content": content}


_CONTEXT_FAILURE = {
    "ok": False,
    "reason_code": "channel_context_unavailable",
    "content": "该操作只能在当前帷幄对话中执行。",
}
_CAPABILITY_FAILURE = {
    "ok": False,
    "reason_code": "channel_capability_unavailable",
    "content": "该通道能力尚未接通，当前不能执行。",
}


def _safe_reason(error: BaseException, fallback: str) -> str:
    candidate = getattr(error, "reason_code", None)
    if isinstance(candidate, str) and _SAFE_REASON_RE.fullmatch(candidate):
        return candidate
    return fallback


def _current_context() -> tuple[str, Any, asyncio.AbstractEventLoop | None, int | None] | None:
    # Lazy import avoids a module cycle: console.tools imports CHANNEL_TOOL_TABLE
    # only after its legacy declaration table has been built.
    from guanlan_v2.console import tools as console_tools

    sid = console_tools.CTX_SID.get(None)
    raw_runtime = CTX_CHANNEL_RUNTIME.get(None)
    if isinstance(raw_runtime, ChannelToolContext):
        runtime = raw_runtime.runtime
        loop = raw_runtime.owner_loop
        owner_thread_id = raw_runtime.owner_thread_id
    else:
        runtime = raw_runtime
        loop = CTX_CHANNEL_OWNER_LOOP.get(None)
        owner_thread_id = CTX_CHANNEL_OWNER_THREAD.get(None)
    if not isinstance(sid, str) or not sid or runtime is None:
        return None
    return sid, runtime, loop, owner_thread_id


def _finish_awaitable(
    value: Any,
    *,
    owner_loop: asyncio.AbstractEventLoop | None,
    owner_thread_id: int | None,
) -> Any:
    if not inspect.isawaitable(value):
        return value
    if (
        owner_loop is None
        or owner_loop.is_closed()
        or not owner_loop.is_running()
        or owner_thread_id == threading.get_ident()
    ):
        close = getattr(value, "close", None)
        if callable(close):
            close()
        raise _ChannelToolInvocationError("channel_context_unavailable")
    future = asyncio.run_coroutine_threadsafe(value, owner_loop)
    try:
        return future.result(timeout=_ASYNC_TOOL_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        future.cancel()
        # The runtime may shield a credential/approval commit from caller
        # cancellation.  Timeout therefore means unknown outcome, never a
        # definite failure and never an invitation to blindly retry.
        raise _ChannelToolInvocationError("channel_operation_unknown") from None


def _invoke(
    method_name: str,
    *args: Any,
    unavailable_if_missing: bool = False,
    session_scoped: bool = True,
) -> Any:
    current = _current_context()
    if current is None:
        return dict(_CONTEXT_FAILURE)
    sid, runtime, owner_loop, owner_thread_id = current
    method = getattr(runtime, method_name, None)
    if not callable(method):
        return dict(_CAPABILITY_FAILURE if unavailable_if_missing else _CONTEXT_FAILURE)
    try:
        # Every session-owned operation receives the trusted CTX_SID first;
        # schemas never let the model name another conversation.  The stable
        # Skill registry is the sole global read and intentionally has no sid.
        result = method(sid, *args) if session_scoped else method(*args)
        return _finish_awaitable(
            result, owner_loop=owner_loop, owner_thread_id=owner_thread_id
        )
    except Exception as error:
        reason = _safe_reason(error, "channel_operation_failed")
        if reason == "channel_operation_unknown":
            return _failure(
                reason,
                "操作结果暂时无法确认；请先读取当前连接状态，不要直接重试。",
            )
        return _failure(reason, "通道操作未完成，请查看当前连接状态后重试。")


def _status() -> dict[str, Any] | None:
    result = _invoke("status_for_session")
    return result if isinstance(result, dict) and "ok" not in result else None


def _agent_status_view(status: Mapping[str, Any]) -> dict[str, Any]:
    """Return status facts that are safe to place in the model ToolResult.

    Telegram profile names and usernames are inbound, attacker-controlled text.
    They remain available to the local HTTP confirmation UI after sanitization,
    but are never returned by Agent tools.  This prevents a later status query
    from becoming a delayed prompt-injection channel.
    """

    def nonnegative_int(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    state = str(status.get("view_state") or "none")
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", state) is None:
        state = "none"
    counts = status.get("counts") if isinstance(status.get("counts"), Mapping) else {}
    result: dict[str, Any] = {
        "available": bool(status.get("available")),
        "view_state": state,
        "bot_configured": isinstance(status.get("bot"), Mapping),
        "recipient_bound": isinstance(status.get("recipient"), Mapping),
        "counts": {
            key: nonnegative_int(counts.get(key))
            for key in ("pending", "retry_wait", "uncertain", "terminal")
        },
    }

    requests: list[dict[str, str]] = []
    raw_requests = status.get("pairing_requests")
    for item in (raw_requests if isinstance(raw_requests, list) else [])[:10]:
        if not isinstance(item, Mapping):
            continue
        request_id = str(item.get("request_id") or "")
        expires_at = str(item.get("expires_at") or "")
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", request_id) is None:
            continue
        request: dict[str, str] = {"request_id": request_id}
        if re.fullmatch(r"[0-9T:+.-]{1,64}", expires_at):
            request["expires_at"] = expires_at
        requests.append(request)
    result["pairing_requests"] = requests

    warnings = status.get("warnings")
    result["warnings"] = [
        {"error_code": code}
        for item in (warnings if isinstance(warnings, list) else [])[:5]
        if isinstance(item, Mapping)
        and (code := str(item.get("error_code") or ""))
        and _SAFE_REASON_RE.fullmatch(code) is not None
    ]
    return result


def _status_content(status: Mapping[str, Any], *, prefix: str = "Telegram") -> str:
    state = str(status.get("view_state") or "none")
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", state) is None:
        state = "none"
    lines = [f"{prefix} 状态：{state}。"]
    if isinstance(status.get("recipient"), Mapping):
        lines.append("已绑定一个私聊接收者；身份信息仅在本地确认卡显示。")

    counts = status.get("counts") if isinstance(status.get("counts"), Mapping) else {}
    def nonnegative_int(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    safe_counts = {
        key: nonnegative_int(counts.get(key))
        for key in ("pending", "retry_wait", "uncertain", "terminal")
    }
    lines.append(
        "投递摘要：待发送 {pending} · 重试中 {retry_wait} · 状态未知 "
        "{uncertain} · 终止 {terminal}。".format(**safe_counts)
    )

    warnings = status.get("warnings")
    warning_codes = [
        str(item.get("error_code") or "")[:96]
        for item in (warnings if isinstance(warnings, list) else [])[:5]
        if isinstance(item, Mapping) and item.get("error_code")
    ]
    if warning_codes:
        lines.append("告警：" + "、".join(warning_codes) + "。")

    recent = status.get("recent_deliveries")
    safe_recent = [
        item for item in (recent if isinstance(recent, list) else [])
        if isinstance(item, Mapping)
    ][-3:]
    if safe_recent:
        lines.append("最近投递（新→旧）：")
        for item in reversed(safe_recent):
            outbox_id = str(item.get("outbox_id") or "")[:80]
            kind = str(item.get("kind") or "message")[:32]
            delivery_state = str(item.get("state") or "unknown")[:32]
            attempts = nonnegative_int(item.get("attempts"))
            error_class = str(item.get("error_class") or "")[:96]
            detail = f" · {error_class}" if error_class else ""
            lines.append(
                f"- {outbox_id or '未编号'} · {kind} · {delivery_state} · "
                f"尝试 {attempts} 次{detail}"
            )
    return "\n".join(lines)


def _success_status(content: str) -> dict[str, Any]:
    status = _status()
    if status is None:
        # The mutation already returned without error.  A secondary status
        # refresh failure must not turn a completed permanent binding or
        # disconnect into a false retryable failure.
        return {
            "ok": True,
            "content": content + " 操作已完成，但当前状态暂时无法刷新；请稍后查询状态，不要盲目重试。",
            "warning_code": "channel_status_unavailable",
            "status": None,
        }
    return {
        "ok": True,
        "content": content + " " + _status_content(status),
        "status": _agent_status_view(status),
    }


def telegram_connect_impl(display_name: str = "") -> dict[str, Any]:
    if not isinstance(display_name, str) or not display_name.strip():
        return _failure(
            "telegram_display_name_required",
            "请先让用户明确提供这个 Bot 在观澜中的显示名称；不得自行起名或使用默认名。",
        )
    result = _invoke("create_draft", display_name.strip())
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return _success_status(
        "连接草稿已创建；请让用户通过安全输入卡提交密钥，模型不能读取或复述密钥。"
    )


def telegram_status_impl() -> dict[str, Any]:
    status = _status()
    if status is None:
        current = _current_context()
        if current is None:
            return dict(_CONTEXT_FAILURE)
        return _failure("channel_status_unavailable", "当前 Telegram 状态不可用。")
    return {
        "ok": True,
        "content": _status_content(status),
        "status": _agent_status_view(status),
    }


def telegram_pair_status_impl() -> dict[str, Any]:
    status = _status()
    if status is None:
        current = _current_context()
        if current is None:
            return dict(_CONTEXT_FAILURE)
        return _failure("channel_status_unavailable", "当前配对状态不可用。")
    agent_status = _agent_status_view(status)
    safe_requests = agent_status["pairing_requests"]
    count = len(safe_requests)
    lines = [f"当前有 {count} 个待确认的私聊配对请求。"]
    for item in safe_requests[:10]:
        if not isinstance(item, Mapping):
            continue
        request_id = str(item.get("request_id") or "")[:160]
        expires_at = str(item.get("expires_at") or "")[:64]
        expiry = f" · 过期 {expires_at}" if expires_at else ""
        lines.append(f"- {request_id}{expiry}")
    return {
        "ok": True,
        "content": "\n".join(lines),
        "pairing_requests": safe_requests,
    }


def telegram_pair_approve_impl(code: str = "") -> dict[str, Any]:
    result = _invoke("approve_pairing_code", code)
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return _success_status("已建立永久的一对一 Telegram 绑定。")


def telegram_pair_dismiss_impl(pairing_request_id: str = "") -> dict[str, Any]:
    result = _invoke("dismiss_pairing", pairing_request_id)
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return _success_status("已忽略当前 Bot 下指定的待配对请求。")


def telegram_pause_impl() -> dict[str, Any]:
    result = _invoke("pause")
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return _success_status("Telegram 镜像已暂停。")


def telegram_resume_impl() -> dict[str, Any]:
    result = _invoke("resume")
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return _success_status("Telegram 镜像已恢复。")


def telegram_disconnect_impl() -> dict[str, Any]:
    result = _invoke("disconnect")
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return _success_status("Telegram 永久绑定已解除；历史对话不会被删除。")


def telegram_publish_impl(media_id: str = "", caption: str = "") -> dict[str, Any]:
    # Validate the deliberately narrow Agent surface before it reaches the
    # runtime.  In particular, paths and URLs can never be interpreted as a
    # file capability: the only accepted handle is an opaque MediaStore id.
    current = _current_context()
    if current is None:
        return dict(_CONTEXT_FAILURE)
    if not callable(getattr(current[1], "publish_media", None)):
        return dict(_CAPABILITY_FAILURE)
    if not isinstance(media_id, str) or MEDIA_ID_RE.fullmatch(media_id) is None:
        return _failure(
            "telegram_media_invalid",
            "只能发送当前对话中已生成的媒体编号，不能传入路径或 URL。",
        )
    validated = validated_telegram_publish_args(
        {"media_id": media_id, "caption": caption}
    )
    if validated is None:
        return _failure("telegram_caption_invalid", "图片说明格式不合法。")
    media_id, caption = validated
    confirmation_ticket = _pop_media_publication_ticket(media_id, caption)
    if confirmation_ticket is None:
        return _failure(
            "telegram_publish_confirmation_required",
            "本次图片发送缺少当前确认，请重新发起并由用户确认。",
        )
    result = _invoke(
        "publish_media",
        media_id,
        caption,
        confirmation_ticket,
        unavailable_if_missing=True,
    )
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    if not isinstance(result, dict):
        return dict(_CAPABILITY_FAILURE)

    # Runtime owns session/binding/media verification.  Accept only its exact
    # non-path-bearing publication envelope, then rebuild the public result so
    # an injected runtime cannot smuggle internal fields into Console events.
    candidate = result.get("artifact") if "artifact" in result else result
    expected_payload = {"media_id": media_id, "caption": caption}
    artifact = public_telegram_publish_artifact(candidate)
    if artifact is None or artifact["payload"] != expected_payload:
        return _failure(
            "telegram_media_publish_invalid",
            "Telegram 图片发布结果无法验证，本次未进入发送队列。",
        )
    return {
        "ok": True,
        "content": f"已将媒体编号 {media_id} 加入 Telegram 发送队列。",
        "artifact": artifact,
    }


def telegram_document_publish_impl(
    document_id: str = "", caption: str = "",
) -> dict[str, Any]:
    current = _current_context()
    if current is None:
        return dict(_CONTEXT_FAILURE)
    if not callable(getattr(current[1], "publish_document", None)):
        return dict(_CAPABILITY_FAILURE)
    validated = validated_telegram_document_publish_args(
        {"document_id": document_id, "caption": caption}
    )
    if validated is None:
        return _failure(
            "telegram_document_invalid",
            "只能发送当前对话中已校验的文件编号，不能传入路径或 URL。",
        )
    document_id, caption = validated
    confirmation_ticket = _pop_media_publication_ticket(document_id, caption)
    if confirmation_ticket is None:
        return _failure(
            "telegram_publish_confirmation_required",
            "本次文件发送缺少当前确认，请重新发起并由用户确认。",
        )
    result = _invoke(
        "publish_document",
        document_id,
        caption,
        confirmation_ticket,
        unavailable_if_missing=True,
    )
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    if not isinstance(result, dict):
        return dict(_CAPABILITY_FAILURE)
    candidate = result.get("artifact") if "artifact" in result else result
    expected_payload = {"document_id": document_id, "caption": caption}
    artifact = public_telegram_document_publish_artifact(candidate)
    if artifact is None or artifact["payload"] != expected_payload:
        return _failure(
            "telegram_document_publish_invalid",
            "Telegram 文件发布结果无法验证，本次未进入发送队列。",
        )
    return {
        "ok": True,
        "content": f"已将文件编号 {document_id} 加入 Telegram 发送队列。",
        "artifact": artifact,
    }


def automation_skills_impl() -> dict[str, Any]:
    result = _invoke(
        "automation_skills",
        unavailable_if_missing=True,
        session_scoped=False,
    )
    return result if isinstance(result, dict) else dict(_CAPABILITY_FAILURE)


def automation_list_impl() -> dict[str, Any]:
    result = _invoke("automation_list", unavailable_if_missing=True)
    return result if isinstance(result, dict) else dict(_CAPABILITY_FAILURE)


def automation_upsert_impl(
    name: str = "",
    schedule_kind: str = "",
    run_at: str = "",
    local_time: str = "",
    timezone: str = "Asia/Hong_Kong",
    prompt_text: str = "",
    skill_refs: list[str] | None = None,
    media_policy: str = "none",
    automation_id: str = "",
) -> dict[str, Any]:
    spec = {
        "name": name,
        "schedule_kind": schedule_kind,
        "run_at": run_at,
        "local_time": local_time,
        "timezone": timezone,
        "prompt_text": prompt_text,
        "skill_refs": list(skill_refs or []),
        "media_policy": media_policy,
        "automation_id": automation_id,
    }
    result = _invoke("automation_upsert", spec, unavailable_if_missing=True)
    return result if isinstance(result, dict) else dict(_CAPABILITY_FAILURE)


def automation_pause_impl(automation_id: str = "") -> dict[str, Any]:
    result = _invoke("automation_pause", automation_id, unavailable_if_missing=True)
    return result if isinstance(result, dict) else dict(_CAPABILITY_FAILURE)


def automation_resume_impl(automation_id: str = "") -> dict[str, Any]:
    result = _invoke("automation_resume", automation_id, unavailable_if_missing=True)
    return result if isinstance(result, dict) else dict(_CAPABILITY_FAILURE)


def automation_delete_impl(automation_id: str = "") -> dict[str, Any]:
    result = _invoke("automation_delete", automation_id, unavailable_if_missing=True)
    return result if isinstance(result, dict) else dict(_CAPABILITY_FAILURE)


def public_tool_args(tool_name: str, args: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the only Console-event-safe view of Channel tool arguments.

    Buddy's private tool-call protocol may retain a user-supplied pairing code
    during its one-hour lifetime, but Console events, audit, and API DTOs may
    not.  Dedicated channel UI renders ``public_details`` instead.
    """
    if tool_name not in CHANNEL_TOOL_NAMES:
        return dict(args or {})
    source = args if isinstance(args, Mapping) else {}
    if tool_name == "ww_telegram_connect":
        value = _safe_preview_text(source.get("display_name"), limit=128)
        return {"display_name": value} if value else {}
    if tool_name == "ww_telegram_pair_dismiss":
        value = _safe_preview_text(source.get("pairing_request_id"), limit=160)
        return {"pairing_request_id": value} if value else {}
    if tool_name == "ww_telegram_publish":
        validated = validated_telegram_publish_args(source)
        return {"media_id": validated[0]} if validated is not None else {}
    if tool_name == "ww_telegram_publish_document":
        validated = validated_telegram_document_publish_args(source)
        return {"document_id": validated[0]} if validated is not None else {}
    if tool_name == "ww_automation_upsert":
        limits = {
            "automation_id": 160,
            "name": 128,
            "schedule_kind": 32,
            "run_at": 64,
            "local_time": 16,
            "timezone": 64,
            "media_policy": 32,
        }
        public = {
            key: text
            for key, limit in limits.items()
            if (text := _safe_preview_text(source.get(key), limit=limit))
        }
        refs = source.get("skill_refs")
        if isinstance(refs, list):
            safe_refs = [
                text
                for item in refs[:16]
                if (text := _safe_preview_text(item, limit=96))
            ]
            if safe_refs:
                public["skill_refs"] = safe_refs
        return public
    if tool_name in {
        "ww_automation_pause", "ww_automation_resume", "ww_automation_delete"
    }:
        value = _safe_preview_text(source.get("automation_id"), limit=160)
        return {"automation_id": value} if value else {}
    # Pair approval deliberately exposes no ``code`` here.  Read-only and
    # parameterless controls also need no generic argument rendering.
    return {}


def _safe_preview_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    if not text or _TOKEN_RE.search(text):
        return "[内容已隐藏]" if text else ""
    return text[:limit]


def _exact_confirm_text(value: object) -> str:
    """Keep an approved automation prompt byte-for-byte for human review.

    Unlike list/status DTOs this value is shown only in the dedicated pending
    confirmation card.  Returning a truncated or whitespace-folded version
    would authorize content the user did not actually see.  Secret-shaped
    input is still fail-closed by the Console argument guard; this second
    check keeps direct/non-Console callers from accidentally rendering it.
    """
    if not isinstance(value, str) or "\x00" in value:
        return ""
    if _TOKEN_RE.search(value):
        return "[内容已隐藏]"
    return value


_CONFIRM_LABELS: dict[str, tuple[str, str, str]] = {
    "ww_telegram_pair_approve": (
        "telegram_pairing", "确认永久绑定 Telegram", "确认永久绑定"
    ),
    "ww_telegram_pair_dismiss": (
        "telegram_pairing", "确认忽略 Telegram 配对请求", "确认忽略"
    ),
    "ww_telegram_pause": ("telegram_channel", "确认暂停 Telegram", "确认暂停"),
    "ww_telegram_resume": ("telegram_channel", "确认恢复 Telegram", "确认恢复"),
    "ww_telegram_disconnect": (
        "telegram_channel", "确认解除 Telegram 永久绑定", "确认解除绑定"
    ),
    "ww_telegram_publish": (
        "telegram_publish", "确认发送图片到 Telegram", "确认发送图片"
    ),
    "ww_telegram_publish_document": (
        "telegram_publish", "确认发送文件到 Telegram", "确认发送文件"
    ),
    "ww_automation_upsert": (
        "automation", "确认定时任务", "确认保存定时任务"
    ),
    "ww_automation_pause": ("automation", "确认暂停定时任务", "确认暂停"),
    "ww_automation_resume": ("automation", "确认恢复定时任务", "确认恢复"),
    "ww_automation_delete": ("automation", "确认删除定时任务", "确认删除"),
}


def confirm_metadata(
    tool_name: str,
    args: Mapping[str, Any] | None,
    status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fixed-shape, secret-free preview for dedicated confirm UI."""
    labels = _CONFIRM_LABELS.get(tool_name)
    if labels is None:
        return {}
    confirm_kind, title, approve_label = labels
    source = dict(args or {})
    safe_status = status if isinstance(status, Mapping) else {}
    bot = safe_status.get("bot") if isinstance(safe_status.get("bot"), Mapping) else {}
    details: dict[str, Any] = {
        "bot_display_name": _safe_preview_text(bot.get("display_name"), limit=128),
        "bot_username": _safe_preview_text(bot.get("username"), limit=64),
    }

    if tool_name in {"ww_telegram_pair_approve", "ww_telegram_pair_dismiss"}:
        requests = safe_status.get("pairing_requests")
        candidates = [item for item in requests or [] if isinstance(item, Mapping)]
        selected: Mapping[str, Any] = {}
        if tool_name == "ww_telegram_pair_approve":
            code = source.get("code")
            hint = code[-2:] if isinstance(code, str) and len(code) >= 2 else ""
            selected = next(
                (item for item in candidates if str(item.get("code_hint") or "") == hint),
                {},
            )
            details["code_hint"] = hint
        else:
            request_id = source.get("pairing_request_id")
            selected = next(
                (item for item in candidates if item.get("request_id") == request_id),
                {},
            )
        details.update({
            "pairing_request_id": _safe_preview_text(selected.get("request_id"), limit=160),
            "telegram_username": _safe_preview_text(selected.get("username"), limit=64),
            "telegram_user_id": _safe_preview_text(selected.get("user_id"), limit=32),
            "telegram_display_name": _safe_preview_text(selected.get("display_name"), limit=128),
            "requested_at": _safe_preview_text(selected.get("created_at"), limit=64),
            "expires_at": _safe_preview_text(selected.get("expires_at"), limit=64),
            "binding_policy": "本对话与此 Bot/私聊永久一对一绑定",
        })
    elif tool_name in {"ww_telegram_publish", "ww_telegram_publish_document"}:
        is_document = tool_name == "ww_telegram_publish_document"
        validated = (
            validated_telegram_document_publish_args(source)
            if is_document
            else validated_telegram_publish_args(source)
        )
        recipient = (
            safe_status.get("recipient")
            if isinstance(safe_status.get("recipient"), Mapping)
            else {}
        )
        attachment = (
            safe_status.get("attachment")
            if isinstance(safe_status.get("attachment"), Mapping)
            else {}
        )
        details.update({
            "media_id": validated[0] if validated is not None and not is_document else "",
            "document_id": validated[0] if validated is not None and is_document else "",
            # This is intentionally exact rather than a whitespace-folded or
            # truncated preview: the authority below binds the same bytes.
            "caption": validated[1] if validated is not None else "",
            "caption_length": len(validated[1]) if validated is not None else 0,
            "media_policy": (
                "只发送当前对话附件仓中已校验的文件"
                if is_document
                else "只发送当前对话附件仓中已校验的图片"
            ),
            "attachment_name": _safe_preview_text(attachment.get("name"), limit=160),
            "attachment_mime": _safe_preview_text(attachment.get("mime"), limit=160),
            "attachment_size": (
                int(attachment.get("size"))
                if isinstance(attachment.get("size"), int)
                and not isinstance(attachment.get("size"), bool)
                and 0 <= int(attachment.get("size")) <= 100 * 1024 * 1024
                else None
            ),
            "attachment_kind": "document" if is_document else "image",
            "telegram_username": _safe_preview_text(
                recipient.get("username"), limit=64
            ),
            "telegram_user_id": _safe_preview_text(
                recipient.get("user_id"), limit=32
            ),
            "telegram_display_name": _safe_preview_text(
                recipient.get("display_name"), limit=128
            ),
            "recipient_generation_policy": "本次确认仅对当前接收者代次有效",
        })
    elif tool_name.startswith("ww_automation_"):
        refs = source.get("skill_refs")
        details.update({
            "automation_id": _safe_preview_text(source.get("automation_id"), limit=160),
            "name": _safe_preview_text(source.get("name"), limit=128),
            "schedule_kind": _safe_preview_text(source.get("schedule_kind"), limit=32),
            "run_at": _safe_preview_text(source.get("run_at"), limit=64),
            "local_time": _safe_preview_text(source.get("local_time"), limit=16),
            "timezone": _safe_preview_text(source.get("timezone"), limit=64),
            # Exact prompt text is required: confirmation must cover the same
            # content later persisted and run by the automation worker.
            "prompt_text": _exact_confirm_text(source.get("prompt_text")),
            "skill_refs": [
                _safe_preview_text(item, limit=96)
                for item in (refs if isinstance(refs, list) else [])
                if _safe_preview_text(item, limit=96)
            ],
            "media_policy": _safe_preview_text(source.get("media_policy"), limit=32),
        })
    return {
        "confirm_kind": confirm_kind,
        "action_ticket_required": True,
        "title": title,
        "approve_label": approve_label,
        "public_details": details,
    }


_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def _object_schema(
    properties: Mapping[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        result["required"] = list(required)
    return result


_AUTOMATION_ID_SCHEMA = _object_schema(
    {"automation_id": {"type": "string", "description": "当前对话中的定时任务编号"}},
    ["automation_id"],
)


CHANNEL_TOOL_TABLE = [
    {
        "name": "ww_telegram_connect",
        "description": "为当前帷幄对话创建 Telegram 连接草稿；display_name 必须逐字来自本轮用户消息，模型不得自行起名或使用默认名，密钥另走安全卡。",
        "input_schema": _object_schema(
            {"display_name": {
                "type": "string",
                "description": "本轮用户逐字给出的自定义 Bot 名称",
                "minLength": 1,
                "maxLength": 128,
                "pattern": r".*\S.*",
            }},
            ["display_name"],
        ),
        "impl": telegram_connect_impl,
        "cost": "instant",
        "confirm": False,
        "reachable": [],
    },
    {
        "name": "ww_telegram_status",
        "description": "读取当前对话的 Telegram 连接与投递摘要。",
        "input_schema": _EMPTY_SCHEMA,
        "impl": telegram_status_impl,
        "cost": "instant",
        "confirm": False,
        "reachable": [],
    },
    {
        "name": "ww_telegram_pair_status",
        "description": "读取当前 Bot 的待确认私聊配对请求。",
        "input_schema": _EMPTY_SCHEMA,
        "impl": telegram_pair_status_impl,
        "cost": "instant",
        "confirm": False,
        "reachable": [],
    },
    {
        "name": "ww_telegram_pair_approve",
        "description": "用用户提供的临时配对码确认当前对话的永久一对一绑定。",
        "input_schema": _object_schema(
            {"code": {"type": "string", "description": "Bot 私聊中显示的 8 位临时配对码"}},
            ["code"],
        ),
        "impl": telegram_pair_approve_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_telegram_pair_dismiss",
        "description": "忽略当前 Bot 下一个错误或恶意的待配对请求。",
        "input_schema": _object_schema(
            {"pairing_request_id": {"type": "string", "description": "待配对请求编号"}},
            ["pairing_request_id"],
        ),
        "impl": telegram_pair_dismiss_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_telegram_pause",
        "description": "暂停当前对话的 Telegram 出站镜像。",
        "input_schema": _EMPTY_SCHEMA,
        "impl": telegram_pause_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_telegram_resume",
        "description": "恢复当前对话中人为暂停的 Telegram 出站镜像。",
        "input_schema": _EMPTY_SCHEMA,
        "impl": telegram_resume_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_telegram_disconnect",
        "description": "解除当前对话的 Telegram 永久绑定，不删除帷幄历史。",
        "input_schema": _EMPTY_SCHEMA,
        "impl": telegram_disconnect_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_telegram_publish",
        "description": "把当前对话 MediaStore 中已校验的图片显式发送到唯一绑定。",
        "input_schema": _object_schema(
            {
                "media_id": {
                    "type": "string",
                    "pattern": r"^mi_[0-9a-f]{32}$",
                    "description": "当前对话的不透明媒体编号；不能传路径或 URL",
                },
                "caption": {"type": "string", "description": "可选图片说明"},
            },
            ["media_id"],
        ),
        "impl": telegram_publish_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_telegram_publish_document",
        "description": "把当前对话附件仓中已校验的文件显式发送到唯一 Telegram 绑定。",
        "input_schema": _object_schema(
            {
                "document_id": {
                    "type": "string",
                    "pattern": r"^do_[0-9a-f]{32}$",
                    "description": "当前对话的不透明文件编号；不能传路径或 URL",
                },
                "caption": {"type": "string", "description": "可选文件说明"},
            },
            ["document_id"],
        ),
        "impl": telegram_document_publish_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_automation_skills",
        "description": "列出通道定时任务可引用的内置 Skill。",
        "input_schema": _EMPTY_SCHEMA,
        "impl": automation_skills_impl,
        "cost": "instant",
        "confirm": False,
        "reachable": [],
    },
    {
        "name": "ww_automation_list",
        "description": "列出当前对话的定时任务与下次运行摘要。",
        "input_schema": _EMPTY_SCHEMA,
        "impl": automation_list_impl,
        "cost": "instant",
        "confirm": False,
        "reachable": [],
    },
    {
        "name": "ww_automation_upsert",
        "description": "为当前对话创建或修改定时任务；保存前完整复述时间、时区、内容、Skill 与媒体策略。",
        "input_schema": _object_schema(
            {
                "automation_id": {"type": "string", "description": "修改既有任务时填写"},
                "name": {"type": "string", "description": "任务名称"},
                "schedule_kind": {
                    "type": "string",
                    "enum": ["once", "daily", "weekdays", "trading_days"],
                },
                "run_at": {"type": "string", "description": "once 使用的带时区 ISO 时间"},
                "local_time": {"type": "string", "description": "循环日程的 HH:MM"},
                "timezone": {"type": "string", "description": "IANA 时区，例如 Asia/Hong_Kong"},
                "prompt_text": {"type": "string", "description": "到点后在当前对话执行的研究要求"},
                "skill_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 16,
                    "description": "ww_automation_skills 返回的稳定 Skill 编号",
                },
                "media_policy": {
                    "type": "string",
                    "enum": ["none", "explicit"],
                    "description": "是否允许发送同一轮新生成且已校验的图片",
                },
            },
            ["name", "schedule_kind", "timezone", "prompt_text", "media_policy"],
        ),
        "impl": automation_upsert_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_automation_pause",
        "description": "暂停当前对话中的一个定时任务。",
        "input_schema": _AUTOMATION_ID_SCHEMA,
        "impl": automation_pause_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_automation_resume",
        "description": "恢复当前对话中的一个定时任务。",
        "input_schema": _AUTOMATION_ID_SCHEMA,
        "impl": automation_resume_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
    {
        "name": "ww_automation_delete",
        "description": "删除当前对话中的一个定时任务。",
        "input_schema": _AUTOMATION_ID_SCHEMA,
        "impl": automation_delete_impl,
        "cost": "instant",
        "confirm": True,
        "reachable": [],
    },
]

CHANNEL_TOOL_NAMES = frozenset(row["name"] for row in CHANNEL_TOOL_TABLE)
CHANNEL_CONFIRM_TOOL_NAMES = frozenset(_CONFIRM_LABELS)


__all__ = [
    "CHANNEL_CONFIRM_TOOL_NAMES",
    "CHANNEL_TOOL_NAMES",
    "CHANNEL_TOOL_TABLE",
    "CTX_CHANNEL_OWNER_LOOP",
    "CTX_CHANNEL_OWNER_THREAD",
    "CTX_CHANNEL_RUNTIME",
    "CTX_MEDIA_PUBLICATION_TICKETS",
    "ChannelToolContext",
    "confirm_metadata",
    "public_telegram_document_publish_artifact",
    "public_telegram_publish_artifact",
    "public_tool_args",
    "stash_media_publication_ticket",
    "validated_telegram_document_publish_args",
    "validated_telegram_publish_args",
]
