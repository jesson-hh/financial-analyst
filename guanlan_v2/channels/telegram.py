"""Narrow, redacted HTTP adapter for Telegram's Bot API.

The official HTTPS origin is deliberately not configurable.  This module has
no Console, Agent, filesystem, or recipient-routing dependencies.
"""

from __future__ import annotations

import logging
import re
import traceback
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Mapping

import httpx

from guanlan_v2.channels.security import (
    TELEGRAM_TOKEN_SCAN_RE,
    contains_pairing_code,
    contains_telegram_token,
)


_OFFICIAL_API_ORIGIN = "https://api.telegram.org"
_TOKEN_PATTERN = TELEGRAM_TOKEN_SCAN_RE
_STRICT_TOKEN_PATTERN = re.compile(r"^[0-9]{5,16}:[A-Za-z0-9_-]{20,}$")
_CHAT_ID_PATTERN = re.compile(r"^-?[0-9]+$")
_ALLOWED_PHOTO_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_ALLOWED_DOCUMENT_MIME_TYPES = frozenset({
    "application/pdf",
    "text/markdown",
    "text/plain",
    "text/csv",
    "text/tab-separated-values",
    "application/json",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})
_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
_KNOWN_HTTP_LOGGERS = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.connection_pool",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.sync",
    "httpcore.async",
)


def redact_telegram_tokens(value: str) -> str:
    """Remove ordinary token shapes and ``bot<token>`` URL path segments."""
    if not isinstance(value, str):
        return "[REDACTED]"
    return _TOKEN_PATTERN.sub("[REDACTED_TELEGRAM_TOKEN]", value)


def _redact_log_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_telegram_tokens(value)
    if isinstance(value, bytes):
        try:
            return redact_telegram_tokens(value.decode("utf-8", "replace")).encode(
                "utf-8"
            )
        except Exception:
            return b"[REDACTED]"
    if isinstance(value, tuple):
        return tuple(_redact_log_value(item) for item in value)
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    if isinstance(value, dict):
        return {
            _redact_log_value(key): _redact_log_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (int, float, bool, type(None))):
        return value
    try:
        rendered = str(value)
    except Exception:
        return "[REDACTED]"
    safe = redact_telegram_tokens(rendered)
    return safe if safe != rendered else value


class TelegramTokenRedactionFilter(logging.Filter):
    """Sanitize messages, formatting args, and exception text in-place."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact_log_value(record.msg)
            record.args = _redact_log_value(record.args)
            if record.exc_info:
                exc_type, error, _tb = record.exc_info
                rendered = "".join(
                    traceback.format_exception_only(exc_type, error)
                ).strip()
                safe_error = RuntimeError(redact_telegram_tokens(rendered))
                record.exc_info = (RuntimeError, safe_error, None)
                record.exc_text = None
            if record.stack_info:
                record.stack_info = redact_telegram_tokens(record.stack_info)
        except Exception:
            # If a custom object resists sanitizing, replace every free-form
            # surface rather than allowing the original LogRecord through.
            record.msg = "redacted logging record"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_REDACTION_FILTER = TelegramTokenRedactionFilter()


def _attach_filter(target: logging.Filterer) -> None:
    if not any(
        isinstance(item, TelegramTokenRedactionFilter) for item in target.filters
    ):
        target.addFilter(_REDACTION_FILTER)


def install_telegram_log_redaction() -> None:
    """Harden currently configured HTTP and root logging surfaces."""
    root = logging.getLogger()
    _attach_filter(root)
    for handler in tuple(root.handlers):
        _attach_filter(handler)

    names = set(_KNOWN_HTTP_LOGGERS)
    for name in logging.Logger.manager.loggerDict:
        if name == "httpx" or name == "httpcore" or name.startswith("httpcore."):
            names.add(name)
    for name in names:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        _attach_filter(logger)
        for handler in tuple(logger.handlers):
            _attach_filter(handler)


class TelegramError(RuntimeError):
    """Base class whose message cannot retain provider or request details."""

    error_class = "telegram_error"

    def __init__(self, *_unsafe_details: object) -> None:
        super().__init__(self.error_class)


class TelegramRetryable(TelegramError):
    error_class = "retryable"

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__()
        self.retry_after = retry_after


class TelegramUncertain(TelegramError):
    error_class = "uncertain"


class TelegramCredentialInvalid(TelegramError):
    error_class = "credential_invalid"


class TelegramTargetUnavailable(TelegramError):
    error_class = "target_unavailable"


class TelegramPollConflict(TelegramError):
    error_class = "poll_conflict"


class TelegramProtocolError(TelegramError):
    error_class = "protocol_error"


@dataclass(frozen=True, slots=True)
class TelegramBotIdentity:
    bot_id: str
    username: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramInboundMessage:
    message_id: str
    chat_id: str
    chat_type: str
    user_id: str | None
    username: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramUpdate:
    update_id: int
    message: TelegramInboundMessage | None = None


@dataclass(frozen=True, slots=True)
class TelegramSendResult:
    message_id: str


def _safe_optional_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    # Profile fields are untrusted inbound presentation data.  Keep them
    # single-line for the local UI and never persist a secret-shaped value.
    normalized = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", value).split()).strip()
    if (
        not normalized
        or contains_telegram_token(normalized)
        or contains_pairing_code(normalized)
    ):
        return None
    return normalized[:max_length] or None


def _numeric_id(value: object, *, allow_negative: bool = False) -> str:
    if isinstance(value, bool):
        raise TelegramProtocolError()
    if isinstance(value, int):
        rendered = str(value)
    elif isinstance(value, str):
        rendered = value
    else:
        raise TelegramProtocolError()
    pattern = _CHAT_ID_PATTERN if allow_negative else re.compile(r"^[0-9]+$")
    if not pattern.fullmatch(rendered):
        raise TelegramProtocolError()
    return rendered


def _display_name(record: Mapping[str, Any]) -> str | None:
    pieces = [
        safe
        for value in (record.get("first_name"), record.get("last_name"))
        if (safe := _safe_optional_text(value, max_length=128)) is not None
    ]
    if not pieces:
        return None
    return _safe_optional_text(" ".join(pieces), max_length=256)


def _bounded_retry_after(payload: Mapping[str, Any] | None) -> int | None:
    if payload is None:
        return None
    parameters = payload.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    raw = parameters.get("retry_after")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return min(3600, max(1, raw))


class TelegramAdapter:
    """Async Telegram Bot API client with frozen origin and safe failures."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        install_telegram_log_redaction()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()

    @staticmethod
    def _url(token: str, method: str) -> str:
        if not isinstance(token, str) or not _STRICT_TOKEN_PATTERN.fullmatch(token):
            raise TelegramCredentialInvalid()
        # Keeping this composition here, with a private fixed origin, prevents
        # callers from redirecting credentials to an arbitrary host.
        return f"{_OFFICIAL_API_ORIGIN}/bot{token}/{method}"

    @staticmethod
    def _safe_chat_id(chat_id: object) -> str:
        try:
            return _numeric_id(chat_id, allow_negative=True)
        except TelegramProtocolError:
            raise TelegramTargetUnavailable() from None

    @staticmethod
    def _classify_transport(
        operation: str, error: httpx.TransportError
    ) -> TelegramError:
        if isinstance(
            error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
        ):
            return TelegramRetryable()
        ambiguous = isinstance(
            error,
            (
                httpx.WriteTimeout,
                httpx.ReadTimeout,
                httpx.ReadError,
                httpx.RemoteProtocolError,
            ),
        )
        if operation == "send" and ambiguous:
            return TelegramUncertain()
        if operation == "send":
            return TelegramUncertain()
        return TelegramRetryable()

    @staticmethod
    def _bad_success(operation: str) -> TelegramError:
        return TelegramUncertain() if operation == "send" else TelegramProtocolError()

    @staticmethod
    def _parse_json(response: httpx.Response) -> Mapping[str, Any] | None:
        try:
            payload = response.json()
        except Exception:
            return None
        return payload if isinstance(payload, Mapping) else None

    @staticmethod
    def _api_error(
        response: httpx.Response,
        payload: Mapping[str, Any] | None,
        *,
        method: str,
    ) -> TelegramError | None:
        status = int(response.status_code)
        raw_code = payload.get("error_code") if payload is not None else None
        code = (
            raw_code
            if isinstance(raw_code, int) and not isinstance(raw_code, bool)
            else status
        )
        description = payload.get("description") if payload is not None else None
        description_lower = description.lower() if isinstance(description, str) else ""

        if status == 401 or code == 401:
            return TelegramCredentialInvalid()
        if status == 403 or code == 403 or "chat not found" in description_lower:
            return TelegramTargetUnavailable()
        if (status == 409 or code == 409) and method == "getUpdates":
            return TelegramPollConflict()
        if status == 429 or code == 429:
            return TelegramRetryable(_bounded_retry_after(payload))
        if status >= 500 or code >= 500:
            return TelegramRetryable()
        if not 200 <= status < 300:
            return TelegramProtocolError()
        if payload is not None and payload.get("ok") is False:
            return TelegramProtocolError()
        return None

    async def _call(
        self,
        token: str,
        method: str,
        *,
        operation: str,
        data: Mapping[str, object] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        request_timeout: httpx.Timeout | float | None = None,
    ) -> Mapping[str, Any]:
        url = self._url(token, method)
        classified_error: TelegramError | None = None
        response: httpx.Response | None = None
        try:
            response = await self._client.post(
                url,
                data=data,
                files=files,
                timeout=request_timeout,
            )
        except httpx.TransportError as error:
            classified_error = self._classify_transport(operation, error)
        except Exception:
            classified_error = (
                TelegramUncertain() if operation == "send" else TelegramProtocolError()
            )
        finally:
            # This only shortens the local reference lifetime; Python and the
            # HTTP stack do not promise in-memory zeroization.
            url = ""

        # Raise after leaving the transport exception handler.  Besides
        # ``from None`` suppressing display, this avoids retaining the raw
        # request exception as ``__context__`` on our public safe exception.
        if classified_error is not None:
            raise classified_error from None
        if response is None:  # defensive: every normal branch set one above
            raise self._bad_success(operation) from None

        payload = self._parse_json(response)
        explicit_error = self._api_error(response, payload, method=method)
        if explicit_error is not None:
            raise explicit_error from None
        if payload is None or payload.get("ok") is not True or "result" not in payload:
            raise self._bad_success(operation) from None
        return payload

    async def get_me(self, token: str) -> TelegramBotIdentity:
        payload = await self._call(
            token,
            "getMe",
            operation="read",
            request_timeout=httpx.Timeout(20.0),
        )
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise TelegramProtocolError() from None
        try:
            return TelegramBotIdentity(
                bot_id=_numeric_id(result.get("id")),
                username=_safe_optional_text(result.get("username"), max_length=64),
                display_name=_display_name(result),
            )
        except TelegramError:
            raise TelegramProtocolError() from None

    async def get_updates(
        self,
        token: str,
        *,
        offset: int | None = None,
        timeout: int = 15,
    ) -> tuple[TelegramUpdate, ...]:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 0 <= timeout <= 50
        ):
            raise TelegramProtocolError()
        form: dict[str, object] = {"timeout": str(timeout)}
        if offset is not None:
            if isinstance(offset, bool) or not isinstance(offset, int):
                raise TelegramProtocolError()
            form["offset"] = str(offset)
        payload = await self._call(
            token,
            "getUpdates",
            operation="read",
            data=form,
            request_timeout=httpx.Timeout(
                connect=10.0,
                read=max(20.0, float(timeout) + 5.0),
                write=10.0,
                pool=10.0,
            ),
        )
        raw_updates = payload.get("result")
        if not isinstance(raw_updates, list):
            raise TelegramProtocolError() from None
        parsed: list[TelegramUpdate] = []
        try:
            for raw_update in raw_updates:
                if not isinstance(raw_update, Mapping):
                    raise TelegramProtocolError()
                update_id = raw_update.get("update_id")
                if isinstance(update_id, bool) or not isinstance(update_id, int):
                    raise TelegramProtocolError()
                raw_message = raw_update.get("message")
                message: TelegramInboundMessage | None = None
                if isinstance(raw_message, Mapping):
                    raw_chat = raw_message.get("chat")
                    raw_sender = raw_message.get("from")
                    if not isinstance(raw_chat, Mapping):
                        raise TelegramProtocolError()
                    sender = raw_sender if isinstance(raw_sender, Mapping) else {}
                    sender_id = sender.get("id")
                    message = TelegramInboundMessage(
                        message_id=_numeric_id(raw_message.get("message_id")),
                        chat_id=_numeric_id(raw_chat.get("id"), allow_negative=True),
                        chat_type=_safe_optional_text(
                            raw_chat.get("type"), max_length=32
                        )
                        or "unknown",
                        user_id=(
                            _numeric_id(sender_id) if sender_id is not None else None
                        ),
                        username=_safe_optional_text(
                            sender.get("username"), max_length=64
                        ),
                        display_name=_display_name(sender),
                    )
                parsed.append(TelegramUpdate(update_id=update_id, message=message))
        except TelegramError:
            raise TelegramProtocolError() from None
        return tuple(parsed)

    @staticmethod
    def _send_result(payload: Mapping[str, Any]) -> TelegramSendResult:
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise TelegramUncertain() from None
        try:
            return TelegramSendResult(message_id=_numeric_id(result.get("message_id")))
        except TelegramError:
            raise TelegramUncertain() from None

    async def send_message(
        self, token: str, *, chat_id: str, text: str
    ) -> TelegramSendResult:
        if not isinstance(text, str) or not text or len(text) > 4096:
            raise TelegramProtocolError()
        payload = await self._call(
            token,
            "sendMessage",
            operation="send",
            data={"chat_id": self._safe_chat_id(chat_id), "text": text},
            request_timeout=httpx.Timeout(20.0),
        )
        return self._send_result(payload)

    async def send_photo(
        self,
        token: str,
        *,
        chat_id: str,
        data: bytes,
        filename: str,
        mime_type: str,
        caption: str | None = None,
    ) -> TelegramSendResult:
        if not isinstance(data, bytes) or not data or len(data) > _MAX_PHOTO_BYTES:
            raise TelegramProtocolError()
        if (
            not isinstance(filename, str)
            or not filename
            or len(filename) > 128
            or filename != PurePath(filename).name
            or any(
                character in filename for character in ("/", "\\", "\x00", "\r", "\n")
            )
        ):
            raise TelegramProtocolError()
        if mime_type not in _ALLOWED_PHOTO_MIME_TYPES:
            raise TelegramProtocolError()
        if caption is not None and (
            not isinstance(caption, str) or len(caption) > 1024 or "\x00" in caption
        ):
            raise TelegramProtocolError()
        form: dict[str, object] = {"chat_id": self._safe_chat_id(chat_id)}
        if caption:
            form["caption"] = caption
        payload = await self._call(
            token,
            "sendPhoto",
            operation="send",
            data=form,
            files={"photo": (filename, data, mime_type)},
            request_timeout=httpx.Timeout(30.0),
        )
        return self._send_result(payload)

    async def send_document(
        self,
        token: str,
        *,
        chat_id: str,
        data: bytes,
        filename: str,
        mime_type: str,
        caption: str | None = None,
    ) -> TelegramSendResult:
        if not isinstance(data, bytes) or not data or len(data) > _MAX_DOCUMENT_BYTES:
            raise TelegramProtocolError()
        if (
            not isinstance(filename, str)
            or not filename
            or len(filename) > 160
            or filename != PurePath(filename).name
            or any(
                character in filename for character in ("/", "\\", "\x00", "\r", "\n")
            )
        ):
            raise TelegramProtocolError()
        if mime_type not in _ALLOWED_DOCUMENT_MIME_TYPES:
            raise TelegramProtocolError()
        if caption is not None and (
            not isinstance(caption, str) or len(caption) > 1024 or "\x00" in caption
        ):
            raise TelegramProtocolError()
        form: dict[str, object] = {
            "chat_id": self._safe_chat_id(chat_id),
            "disable_content_type_detection": "true",
        }
        if caption:
            form["caption"] = caption
        payload = await self._call(
            token,
            "sendDocument",
            operation="send",
            data=form,
            files={"document": (filename, data, mime_type)},
            request_timeout=httpx.Timeout(45.0),
        )
        return self._send_result(payload)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "TelegramAdapter",
    "TelegramBotIdentity",
    "TelegramCredentialInvalid",
    "TelegramError",
    "TelegramInboundMessage",
    "TelegramPollConflict",
    "TelegramProtocolError",
    "TelegramRetryable",
    "TelegramSendResult",
    "TelegramTargetUnavailable",
    "TelegramTokenRedactionFilter",
    "TelegramUncertain",
    "TelegramUpdate",
    "install_telegram_log_redaction",
    "redact_telegram_tokens",
]
