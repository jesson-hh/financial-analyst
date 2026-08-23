from __future__ import annotations

import inspect
import logging
import traceback

import httpx
import pytest

from guanlan_v2.channels.telegram import (
    TelegramAdapter,
    TelegramCredentialInvalid,
    TelegramPollConflict,
    TelegramProtocolError,
    TelegramRetryable,
    TelegramTargetUnavailable,
    TelegramUncertain,
)


TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _adapter(handler):
    return TelegramAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.anyio
async def test_successful_methods_use_fixed_official_host_and_safe_dtos():
    seen: list[tuple[httpx.URL, bytes, dict[str, str]]] = []

    async def handler(request):
        body = await request.aread()
        seen.append((request.url, body, dict(request.headers)))
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"id": 42, "username": "safe_bot", "first_name": "Safe"},
                },
            )
        if method == "getUpdates":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 7,
                            "message": {
                                "message_id": 8,
                                "text": "pair me",
                                "chat": {"id": 9, "type": "private"},
                                "from": {
                                    "id": 10,
                                    "username": "alice",
                                    "first_name": "Alice",
                                },
                            },
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 11}})

    adapter = _adapter(handler)
    try:
        me = await adapter.get_me(TOKEN)
        updates = await adapter.get_updates(TOKEN, offset=7, timeout=15)
        text_result = await adapter.send_message(
            TOKEN, chat_id="9", text="plain <text>"
        )
        photo_result = await adapter.send_photo(
            TOKEN,
            chat_id="9",
            data=b"safe-image-bytes",
            filename="chart.png",
            mime_type="image/png",
            caption="safe caption",
        )
    finally:
        await adapter.aclose()

    assert me.bot_id == "42"
    assert me.username == "safe_bot"
    assert updates[0].update_id == 7
    assert updates[0].message is not None
    assert updates[0].message.chat_id == "9"
    assert updates[0].message.user_id == "10"
    assert text_result.message_id == "11"
    assert photo_result.message_id == "11"
    assert all(
        url.scheme == "https" and url.host == "api.telegram.org" for url, _, _ in seen
    )
    assert [url.path.rsplit("/", 1)[-1] for url, _, _ in seen] == [
        "getMe",
        "getUpdates",
        "sendMessage",
        "sendPhoto",
    ]
    assert b"parse_mode" not in seen[2][1]
    assert b"parse_mode" not in seen[3][1]
    assert b"chart.png" in seen[3][1]
    assert "base_url" not in inspect.signature(TelegramAdapter).parameters
    assert TOKEN not in repr(me)
    assert TOKEN not in repr(updates)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "payload", "operation", "expected"),
    [
        (
            401,
            {"ok": False, "error_code": 401, "description": "Unauthorized"},
            "get_me",
            TelegramCredentialInvalid,
        ),
        (
            403,
            {"ok": False, "error_code": 403, "description": "Forbidden"},
            "send",
            TelegramTargetUnavailable,
        ),
        (
            400,
            {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
            "send",
            TelegramTargetUnavailable,
        ),
        (
            409,
            {"ok": False, "error_code": 409, "description": "Conflict"},
            "updates",
            TelegramPollConflict,
        ),
        (
            500,
            {"ok": False, "error_code": 500, "description": "Internal"},
            "send",
            TelegramRetryable,
        ),
    ],
)
async def test_http_and_api_errors_are_safely_classified(
    status, payload, operation, expected
):
    async def handler(request):
        return httpx.Response(status, json=payload)

    adapter = _adapter(handler)
    try:
        with pytest.raises(expected) as caught:
            if operation == "get_me":
                await adapter.get_me(TOKEN)
            elif operation == "updates":
                await adapter.get_updates(TOKEN)
            else:
                await adapter.send_message(TOKEN, chat_id="9", text="hello")
    finally:
        await adapter.aclose()

    rendered = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(caught.value)
    assert TOKEN not in rendered
    assert caught.value.__cause__ is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "retry_after, expected", [(17, 17), (0, 1), (9999, 3600), ("bad", None)]
)
async def test_rate_limit_retry_after_is_safely_bounded(retry_after, expected):
    async def handler(request):
        return httpx.Response(
            429,
            json={
                "ok": False,
                "error_code": 429,
                "parameters": {"retry_after": retry_after},
            },
        )

    adapter = _adapter(handler)
    try:
        with pytest.raises(TelegramRetryable) as caught:
            await adapter.send_message(TOKEN, chat_id="9", text="hello")
    finally:
        await adapter.aclose()
    assert caught.value.retry_after == expected


_TRANSPORT_EXCEPTIONS = [
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.WriteTimeout,
    httpx.ReadTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
]


@pytest.mark.anyio
@pytest.mark.parametrize("exc_type", _TRANSPORT_EXCEPTIONS)
async def test_read_operations_never_report_uncertain(exc_type):
    async def handler(request):
        raise exc_type(f"transport failure at {request.url}", request=request)

    adapter = _adapter(handler)
    try:
        with pytest.raises((TelegramRetryable, TelegramProtocolError)) as caught:
            await adapter.get_updates(TOKEN)
    finally:
        await adapter.aclose()

    assert not isinstance(caught.value, TelegramUncertain)
    assert TOKEN not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exc_type", "expected"),
    [
        (httpx.ConnectError, TelegramRetryable),
        (httpx.ConnectTimeout, TelegramRetryable),
        (httpx.PoolTimeout, TelegramRetryable),
        (httpx.WriteTimeout, TelegramUncertain),
        (httpx.ReadTimeout, TelegramUncertain),
        (httpx.ReadError, TelegramUncertain),
        (httpx.RemoteProtocolError, TelegramUncertain),
    ],
)
async def test_send_transport_errors_are_classified_by_delivery_certainty(
    exc_type, expected
):
    async def handler(request):
        raise exc_type(f"transport failure at {request.url}", request=request)

    adapter = _adapter(handler)
    try:
        with pytest.raises(expected) as caught:
            await adapter.send_message(TOKEN, chat_id="9", text="hello")
    finally:
        await adapter.aclose()

    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(caught.value)
    assert TOKEN not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("get_me", TelegramProtocolError),
        ("updates", TelegramProtocolError),
        ("send_message", TelegramUncertain),
        ("send_photo", TelegramUncertain),
    ],
)
@pytest.mark.parametrize(
    "bad_response", [b"not-json", b"[]", b'{"ok":true,"result":{}}']
)
async def test_bad_success_responses_are_classified_by_operation(
    operation, expected, bad_response
):
    async def handler(request):
        return httpx.Response(
            200, content=bad_response, headers={"content-type": "application/json"}
        )

    adapter = _adapter(handler)
    try:
        with pytest.raises(expected):
            if operation == "get_me":
                await adapter.get_me(TOKEN)
            elif operation == "updates":
                await adapter.get_updates(TOKEN)
            elif operation == "send_photo":
                await adapter.send_photo(
                    TOKEN,
                    chat_id="9",
                    data=b"image",
                    filename="image.png",
                    mime_type="image/png",
                )
            else:
                await adapter.send_message(TOKEN, chat_id="9", text="hello")
    finally:
        await adapter.aclose()


@pytest.mark.anyio
async def test_token_redaction_covers_root_httpx_httpcore_args_and_exception(caplog):
    caplog.set_level(logging.DEBUG)

    async def handler(request):
        url = str(request.url)
        logging.getLogger().warning("root saw %s", url)
        logging.getLogger("httpx").warning("httpx saw %s", request.url)
        logging.getLogger("httpcore.connection").warning("httpcore saw %s", url)
        try:
            raise RuntimeError("failed at " + url)
        except RuntimeError:
            logging.getLogger("guanlan.test").exception("application transport failure")
        return httpx.Response(200, json={"ok": True, "result": {"id": 42}})

    adapter = _adapter(handler)
    try:
        await adapter.get_me(TOKEN)
    finally:
        await adapter.aclose()

    assert caplog.records
    for record in caplog.records:
        rendered_exc = ""
        if record.exc_info:
            rendered_exc = "".join(traceback.format_exception(*record.exc_info))
        assert TOKEN not in record.getMessage()
        assert TOKEN not in repr(record.args)
        assert TOKEN not in rendered_exc
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    assert (
        logging.getLogger("httpcore.connection").getEffectiveLevel() >= logging.WARNING
    )
