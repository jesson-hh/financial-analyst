# -*- coding: utf-8 -*-
"""Task 9 Channel data-client security and route contract guards."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "ui" / "console" / "console-data.jsx"


def _source() -> str:
    return DATA.read_text(encoding="utf-8")


def _run_node(program: str) -> dict:
    source = _source()
    script = f"""
const vm = require('vm');
const source = {json.dumps(source)};
const calls = [];
const context = {{
  window: {{ GUANLAN_BACKEND: '' }},
  fetch: async (url, options = {{}}) => {{
    calls.push({{ url, options }});
    return context.nextResponse(url, options, calls.length);
  }},
  EventSource: function EventSource() {{}},
  console,
}};
context.nextResponse = async () => ({{ ok: true, status: 200, json: async () => ({{ ok: true }}) }});
vm.createContext(context);
vm.runInContext(source, context);
(async () => {{
  {program}
}})().catch((error) => {{ process.stderr.write(String(error && error.stack || error)); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-"], input=script, text=True, encoding="utf-8",
        capture_output=True, check=True
    )
    return json.loads(completed.stdout)


def test_channel_data_exports_exact_narrow_api_and_has_no_browser_secret_storage():
    src = _source()
    for declaration in (
        "async function wwChannelStatus(sid)",
        "async function wwChannelRequestToken(sid, action, subjectId)",
        "async function wwTelegramSubmitToken(sid, draftId, token, requestToken)",
        "async function wwChannelManage(sid, action, requestToken)",
        "async function wwChannelResolveDelivery(sid, itemId, action, requestToken)",
        "async function wwChannelConfirm(sid, turnId, toolName, choice)",
        "function wwLooksLikeTelegramToken(text)",
    ):
        assert declaration in src
    for export in (
        "channelStatus: wwChannelStatus",
        "channelRequestToken: wwChannelRequestToken",
        "telegramSubmitToken: wwTelegramSubmitToken",
        "channelManage: wwChannelManage",
        "channelResolveDelivery: wwChannelResolveDelivery",
        "channelConfirm: wwChannelConfirm",
        "looksLikeTelegramToken: wwLooksLikeTelegramToken",
    ):
        assert export in src
    assert "Object.assign(window" in src
    assert "localStorage" not in src
    assert "sessionStorage" not in src
    assert "console.log" not in src


def test_channel_routes_are_closed_mapped_encoded_and_token_is_body_only():
    src = _source()
    assert "WW_CHANNEL_MANAGE_ACTIONS" in src
    assert "WW_CHANNEL_RESOLUTION_ACTIONS" in src
    assert "WW_CHANNEL_TICKET_ACTIONS" in src
    assert "encodeURIComponent(safeSid)" in src
    assert "encodeURIComponent(safeItemId)" in src
    submit = src.split("async function wwTelegramSubmitToken", 1)[1].split(
        "async function wwChannelManage", 1
    )[0]
    assert "'/console/channels/telegram/token'" in submit
    assert "token: secret" in submit
    assert "finally" in submit and "secret = ''" in submit and "requestBody = null" in submit
    assert "token)" not in submit.split("fetch(", 1)[1].split("{", 1)[0]


def test_channel_confirm_uses_instance_ticket_and_clears_it_in_finally():
    src = _source()
    confirm = src.split("async function wwConfirm(confirm, choice)", 1)[1].split(
        "async function wwChannelConfirm", 1
    )[0]
    assert "'/console/confirm/request-token'" in confirm
    assert "turn_id: turnId, confirm_id: confirmId" in confirm
    assert "body.request_token = requestToken" in confirm
    assert "finally" in confirm
    assert "delete body.request_token" in confirm
    assert "requestToken = ''" in confirm
    channel_confirm = src.split("async function wwChannelConfirm", 1)[1].split(
        "async function wwSessions", 1
    )[0]
    assert "WW_CHANNEL_CONFIRM_TOOLS[toolName]" in channel_confirm
    assert "String(pending.confirm_id || '')" in channel_confirm
    assert "pending.action_ticket_required = true" in channel_confirm


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_channel_data_runtime_rejects_open_actions_and_normalizes_status_404():
    result = _run_node(
        """
const WW = context.window.WW;
context.nextResponse = async () => ({ ok: false, status: 404, json: async () => ({ detail: 'Not Found' }) });
const status = await WW.channelStatus('cs:one');
const before = calls.length;
const rejected = await WW.channelManage('cs:one', 'anything', 'A'.repeat(32));
const tokenShape = WW.looksLikeTelegramToken('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_123456');
const ordinary = WW.looksLikeTelegramToken('Alpha:0.3 and Beta=1.2');
process.stdout.write(JSON.stringify({ status, rejected, tokenShape, ordinary, calls, before }));
"""
    )
    assert result["status"]["available"] is False
    assert result["status"]["view_state"] == "none"
    assert result["calls"][0]["url"].endswith("/console/channels/session/cs%3Aone")
    assert result["before"] == len(result["calls"])
    assert result["rejected"]["reason_code"] == "channel_action_invalid"
    assert result["tokenShape"] is True
    assert result["ordinary"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_channel_status_distinguishes_feature_off_from_transient_failure():
    result = _run_node(
        """
const WW = context.window.WW;
context.nextResponse = async () => ({
  ok: false, status: 503,
  json: async () => ({ available: false, error_code: 'channel_backend_busy', reason: 'busy' }),
});
const transient = await WW.channelStatus('cs_one');
context.nextResponse = async () => ({
  ok: true, status: 200, json: async () => ({ available: false }),
});
const explicitOff = await WW.channelStatus('cs_one');
process.stdout.write(JSON.stringify({ transient, explicitOff }));
"""
    )
    assert result["transient"]["available"] is False
    assert result["transient"]["transient_status_error"] is True
    assert result["transient"]["status_error"] == "channel_backend_busy"
    assert result["explicitOff"]["available"] is False
    assert "transient_status_error" not in result["explicitOff"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_management_ticket_omits_private_bot_subject_but_owned_actions_require_subject():
    result = _run_node(
        """
const WW = context.window.WW;
context.nextResponse = async () => ({
  ok: true, status: 200,
  json: async () => ({ ok: true, request_token: 'T'.repeat(43) }),
});
const managed = await WW.channelRequestToken('cs_one', 'telegram:pause', null);
const afterManage = calls.length;
const missingDraft = await WW.channelRequestToken('cs_one', 'telegram:submit-token', null);
const missingDelivery = await WW.channelRequestToken('cs_one', 'telegram:resolve:retry', null);
process.stdout.write(JSON.stringify({ managed, afterManage, missingDraft, missingDelivery, calls }));
"""
    )
    assert result["managed"]["ok"] is True
    assert result["afterManage"] == len(result["calls"]) == 1
    assert json.loads(result["calls"][0]["options"]["body"]) == {
        "action": "telegram:pause"
    }
    assert result["missingDraft"]["reason_code"] == "channel_ticket_request_invalid"
    assert result["missingDelivery"]["reason_code"] == "channel_ticket_request_invalid"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_channel_data_runtime_secret_submit_and_confirmation_use_only_safe_routes():
    result = _run_node(
        """
const WW = context.window.WW;
const botToken = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_123456';
const ticket = 'T'.repeat(43);
context.nextResponse = async (_url, _options, index) => ({
  ok: true, status: 200,
  json: async () => index === 2 ? ({ ok: true, request_token: ticket }) : ({ ok: true }),
});
const submitted = await WW.telegramSubmitToken('cs_one', 'bot_draft', botToken, ticket);
const confirmed = await WW.channelConfirm(
  'cs_one',
  { turn_id: 'turn_1', confirm_id: 'confirm_1', tool: 'ww_telegram_pause', action_ticket_required: true },
  'ww_telegram_pause', 'y'
);
const beforeInvalid = calls.length;
const invalid = await WW.channelConfirm('cs_one', 'turn_1', 'ww_telegram_pause', 'y');
process.stdout.write(JSON.stringify({ submitted, confirmed, invalid, beforeInvalid, calls, botToken }));
"""
    )
    assert result["submitted"]["ok"] is True
    assert result["confirmed"]["ok"] is True
    assert result["invalid"]["reason_code"] == "channel_confirm_instance_required"
    assert result["beforeInvalid"] == len(result["calls"])
    submit_call, ticket_call, confirm_call = result["calls"]
    assert submit_call["url"].endswith("/console/channels/telegram/token")
    assert result["botToken"] not in submit_call["url"]
    assert json.loads(submit_call["options"]["body"])["token"] == result["botToken"]
    assert ticket_call["url"].endswith("/console/confirm/request-token")
    assert json.loads(ticket_call["options"]["body"]) == {
        "turn_id": "turn_1",
        "confirm_id": "confirm_1",
    }
    confirm_body = json.loads(confirm_call["options"]["body"])
    assert confirm_body["request_token"] == "T" * 43
    assert confirm_body["confirm_id"] == "confirm_1"
