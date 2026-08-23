# -*- coding: utf-8 -*-
"""Telegram 通道新增独立 UI 组件的静态安全与文案契约。

接线由 Task 9 的其他步骤完成；这里不假设旧 JSX/HTML 已经改动。
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui" / "console"


def _read(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def test_channel_module_exports_frozen_public_component_surface():
    src = _read("console-channel.jsx")
    expected = (
        "WwChannelStatusBadge",
        "WwChannelActionCard",
        "WwSecureTokenCard",
        "WwChannelDrawer",
        "WwDeliveryRow",
        "WwChannelConfirmDetails",
        "wwChannelViewState",
    )
    for name in expected:
        assert f"window.{name} = {name};" in src
    assert "function wwChannelUiSafeFailure" in src
    assert "function wwChannelSafeFailure" not in src


def test_channel_view_state_uses_server_truth_and_exact_product_copy():
    src = _read("console-channel.jsx")
    for text in (
        "Telegram · 待配置",
        "Telegram · 等待配对",
        "Telegram · 配对请求待确认",
        "已连接 Telegram",
        "Telegram · 重试中",
        "Telegram · 投递待确认",
        "Telegram · 需要重新连接",
        "Telegram · 已暂停",
        "Telegram · 状态暂不可达",
    ):
        assert text in src
    view = src.split("function wwChannelViewState(status)", 1)[1].split(
        "function wwChannelFormatTime", 1
    )[0]
    assert "source.view_state" in view
    assert "source.available === true" in view
    assert "source.counts" not in view
    assert "source.warnings" not in view
    assert "'已连接 Telegram · ' + botName" in view
    assert "source.transient_status_error === true || !!source.status_error" in view
    assert "baseLabel + ' · 状态暂不可达'" in view
    assert "hasLiveRecipient && (key === 'active'" in view


def test_secure_token_is_component_local_password_and_always_cleared():
    src = _read("console-channel.jsx")
    card = src.split("function WwSecureTokenCard(", 1)[1].split(
        "function WwChannelActionCard", 1
    )[0]
    before_card = src.split("function WwSecureTokenCard(", 1)[0]
    after_card = src.split("function WwChannelActionCard", 1)[1]

    assert "const [token, setToken] = React.useState('')" in card
    assert "type=\"password\"" in card
    assert "autoComplete=\"off\"" in card
    assert "spellCheck={false}" in card
    assert "value={token}" in card
    assert "const secret = token.trim()" in card
    assert "const reactId = React.useId()" in card
    assert "htmlFor={inputId}" in card and "id={inputId}" in card
    assert 'id="ww-channel-token-input"' not in card
    assert "await onSubmit(safeDraftId, secret)" in card
    assert re.search(r"finally\s*\{[^}]*setToken\(''\)", card, re.S)
    assert card.count("setToken('')") == 2  # draft 切换 + 单一 finally 收口
    assert "const [token," not in before_card
    assert "const [token," not in after_card


def test_token_card_never_routes_secret_through_conversation_or_storage():
    src = _read("console-channel.jsx")
    forbidden = (
        "wwDeriveItems",
        "WW.send",
        "WW.steer",
        "localStorage",
        "sessionStorage",
        "console.log",
        "console.error",
        "indexedDB",
    )
    for text in forbidden:
        assert text not in src
    assert "不会进入对话、历史记录或浏览器存储" in src


def test_action_card_is_status_driven_and_archive_safe():
    src = _read("console-channel.jsx")
    card = src.split("function WwChannelActionCard(", 1)[1].split(
        "const WW_CHANNEL_DELIVERY_COPY", 1
    )[0]
    assert "view.key === 'awaiting_token'" in card
    assert "<WwSecureTokenCard" in card
    assert "disabled={archived || view.statusUnavailable}" in card
    assert "Telegram 可能已收到内容" in card
    assert "已停止盲目重试" in card
    assert "恢复对话后才能管理通道" in card


def test_delivery_rows_require_second_confirmation_and_never_claim_optimistic_success():
    src = _read("console-channel.jsx")
    row = src.split("function WwDeliveryRow(", 1)[1].split(
        "function WwChannelConfirmDetails", 1
    )[0]
    for text in (
        "视为已送达并继续",
        "重新发送并接受可能重复",
        "放弃本条并继续",
        "修复后重试",
    ):
        assert text in row
    assert "setConfirmAction(row[0])" in row
    assert "role=\"alertdialog\"" in row
    assert "await onResolve(outboxId, selected[0])" in row
    assert "if (typeof onRefresh === 'function') await onRefresh()" in row
    assert "setState" not in row
    assert "item.state =" not in row
    assert "正在更新真实投递状态" in row


def test_channel_drawer_has_safe_management_but_no_credential_reveal():
    src = _read("console-channel.jsx")
    drawer = src.split("function WwChannelDrawer(", 1)[1].split(
        "window.WwChannelStatusBadge", 1
    )[0]
    assert "role=\"dialog\"" in drawer and "aria-modal=\"true\"" in drawer
    for action in ("pause", "resume", "reverify", "replace-token", "disconnect"):
        assert f"manage('{action}')" in drawer
    assert "action === 'disconnect' && manageConfirm !== 'disconnect'" in drawer
    assert "确认解除连接？" in drawer
    assert "const hasChannelTarget = !!source.draft || view.connected" in drawer
    assert "source.bot || view.connected" not in drawer
    assert "disabled={manageDisabled || !hasChannelTarget}" in drawer
    assert "disabled={manageDisabled || !view.connected}" in drawer
    assert "取消连接设置" in drawer
    assert "帷幄对话历史不会删除" in drawer
    assert "const manageDisabled = archived" in drawer
    assert "disabled={archived || view.statusUnavailable}" in drawer
    assert "onResolveDelivery, onResolve" in drawer
    assert "onResolve={resolveDelivery}" in drawer
    assert "替换凭证只会打开新的安全提交卡" in drawer
    assert "view.statusUnavailable || !!manageBusy" in drawer
    assert "disabled={archived || view.statusUnavailable}" in drawer
    assert "查看 Token" not in src
    assert "credential_id" not in src


def test_confirmation_details_allowlist_public_fields_without_generic_dump():
    src = _read("console-channel.jsx")
    details = src.split("function WwChannelConfirmDetails(", 1)[1].split(
        "function WwChannelDrawer", 1
    )[0]
    assert "function WwChannelConfirmDetails({ publicDetails, toolName })" in src
    assert "publicDetails && typeof publicDetails === 'object'" in details
    assert "JSON.stringify" not in details
    assert "Object.entries" not in details
    assert "source.token" not in details
    assert "source.args" not in details
    for field in (
        "bot_display_name",
        "telegram_user_id",
        "binding_policy",
        "media_id",
        "caption",
        "automation_id",
        "prompt_text",
        "skill_refs",
    ):
        assert f"source.{field}" in details
    assert "本次确认仅对上述当前接收者有效" in details
    assert "const exactCaption = typeof source.caption === 'string' ? source.caption : ''" in details
    assert "['发送说明', exactCaption, 'caption']" in details
    assert "text(source.caption, 900)" not in details
    assert "ww-channel-confirm-row--' + row[2]" in details
    assert "const exactPrompt = typeof source.prompt_text === 'string' ? source.prompt_text : ''" in details
    assert "['任务内容', exactPrompt, 'prompt']" in details
    assert "['单次执行时间', text(source.run_at, 96)]" in details
    assert "['当地执行时间', text(source.local_time, 32)]" in details
    assert "未使用内置 Skill" in details
    assert "仅允许发送同一轮新生成且已校验的图片" in details
    assert "不允许自动发送图片" in details
    assert "source.prompt_summary" not in details


def test_channel_drawer_lists_only_current_status_automations_with_run_state():
    src = _read("console-channel.jsx")
    drawer = src.split("function WwChannelDrawer(", 1)[1].split(
        "window.WwChannelStatusBadge", 1
    )[0]
    assert "Array.isArray(source.automations) ? source.automations : []" in drawer
    for field in (
        "automation_id",
        "name",
        "schedule_kind",
        "status",
        "next_run_at",
        "last_run_at",
        "pause_reason",
    ):
        assert f"item.{field}" in drawer
    for text in (
        "自动发送日程",
        "下次运行",
        "最近运行",
        "暂停原因",
        "日程只属于当前对话，不能移动到其他对话。",
        "还没有定时任务。",
    ):
        assert text in drawer
    assert "item.session_id" not in drawer
    assert "onMoveAutomation" not in drawer
    assert "moveAutomation" not in drawer
    assert "prompt_text" not in drawer


def test_channel_css_is_isolated_and_responsive_above_workspace_dock():
    css = _read("console-channel.css")
    assert ".ww-channel-drawer" in css
    assert "width: 420px" in css
    assert "inset: 44px 0 0" in css
    assert "z-index: 72" in css
    assert "@media (min-width: 1440px)" in css
    assert "@media (max-width: 1120px)" in css
    assert "@media (max-width: 760px)" in css
    assert all(token in css for token in ("var(--paper)", "var(--ink)", "var(--yin)", "var(--dai)", "var(--jin)"))
    assert not re.search(r"(^|[},]\s*)(body|html|#root|\.ww-workspace-|\.ww-sess)", css)


def test_channel_assets_load_after_data_before_consumers_with_fresh_versions():
    html = _read("观澜 · 帷幄.html")
    assert 'console-channel.css?v=20260823a1' in html
    ordered = (
        'console-data.jsx?v=20260823a1',
        'console-channel.jsx?v=20260823a1',
        'console-rail.jsx?v=20260823a1',
        'console-thread.jsx?v=20260823a1',
        'console-app.jsx?v=20260823a1',
    )
    positions = [html.index(asset) for asset in ordered]
    assert positions == sorted(positions)


def test_app_status_pull_has_sid_generation_and_latest_sequence_guards():
    app = _read("console-app.jsx")
    assert "const [channelBySid, setChannelBySid] = React.useState({})" in app
    assert "const [channelDrawerOpen, setChannelDrawerOpen] = React.useState(false)" in app
    assert "const channelRequestSeqRef = React.useRef(0)" in app
    pull = app.split("const pullChannel = async (sid, generation)", 1)[1].split(
        "const openChannel", 1
    )[0]
    assert "const sequence = ++channelRequestSeqRef.current" in pull
    assert "requestIsCurrent(sid, generation)" in pull
    assert "sequence !== channelRequestSeqRef.current" in pull
    assert "result.transient_status_error === true" in pull
    assert "sid === state.sid ? sessionChannel : null" in pull
    assert "status_error: result.status_error" in pull
    assert "prior.transient_status_error === true" in app
    assert "prior.available !== true" in app
    assert "next[row.id] = { ...summary, ok: false" in app
    assert "const sid = streamSidRef.current; const generation = streamGenRef.current" in app
    assert "setInterval(() => pullChannel(sid, generation), 2000)" in app
    assert "return () => clearInterval(iv)" in app


def test_shared_text_dispatch_intercepts_token_before_archive_busy_send_or_steer():
    app = _read("console-app.jsx")
    send = app.split("const onSend = async (text) =>", 1)[1].split(
        "const onChannelSubmitToken", 1
    )[0]
    detector = send.index("WW.looksLikeTelegramToken(text)")
    assert detector < send.index("if (activeSessionArchived)")
    assert detector < send.index("if (state.busy)")
    assert detector < send.index("WW.steer(requestSid, text, thinkingMode)")
    assert detector < send.index("WW.send(sendSid, text, thinkingMode)")
    secret_branch = send[detector:send.index("if (activeSessionArchived)")]
    assert "WW.send" not in secret_branch and "WW.steer" not in secret_branch
    assert "secret_handled: true, clear_input: true" in secret_branch
    assert "finally" in secret_branch and "text = ''" in secret_branch
    assert "reason_code: 'channel_draft_required'" in secret_branch
    assert "const staleChannelResult = () => ({ ok: false" in app


def test_thread_keeps_action_card_outside_event_items_and_clears_secret_inputs():
    thread = _read("console-thread.jsx")
    assert "function ConfirmModal({ confirm, onChoice, onStop, onSteer })" in thread
    assert "function WwMediaImageCard({ item })" in thread
    assert "if (r && r.clear_input === true) setSteerDraft('')" in thread
    assert "if (r && (r.ok || r.clear_input === true)) setDraft('')" in thread
    assert "channelStatus.recent_deliveries" in thread
    assert "deliveryByMediaId[delivery.media_id]" in thread
    assert "!deliveryByMediaId[delivery.media_id]" not in thread
    assert "delivery: deliveryByMediaId[item.mediaId] || null" in thread
    assert "const [secretFeedback, setSecretFeedback] = React.useState(null)" in thread
    assert "r && r.secret_handled === true" in thread
    assert "channel_draft_required" in thread
    assert "ww-channel-composer-feedback" in thread
    assert "!channelDrawerOpen && <WwChannelActionCard" in thread
    steer = thread.split("const steerTurn = async () =>", 1)[1].split(
        "React.useEffect(() =>", 1
    )[0]
    assert "r && r.secret_handled === true" in steer
    assert "setSteerDraft('')" in steer and "setSending('')" in steer
    assert "当前工具确认仍待处理" in steer
    assert "setSecretNotice({ ...notice" in steer
    assert "secretNotice.ok ? '✓ ' : '✗ '" in thread
    render = thread.split("{itemsWithDelivery.map(m => {", 1)[1]
    assert render.index("})}") < render.index("<WwChannelActionCard")
    assert "<WwMediaImageCard key={m.id} item={m}" in render
    assert "发送图片到 Telegram" not in thread.split(
        "function WwMediaImageCard({ item })", 1
    )[1].split("// ── ToolChain", 1)[0]


def test_channel_confirmation_is_public_details_only_and_generic_args_are_exclusive():
    thread = _read("console-thread.jsx")
    modal = thread.split("function ConfirmModal({ confirm, onChoice, onStop, onSteer })", 1)[1].split(
        "// ── Launcher", 1
    )[0]
    dedicated = '<WwChannelConfirmDetails publicDetails={c.public_details} toolName={c.tool} />'
    assert dedicated in modal
    assert "details={details}" not in modal
    assert "{isChannelConfirm ? (" in modal
    assert ") : (" in modal
    assert "JSON.stringify(c.args, null, 1)" in modal
    assert modal.index(dedicated) < modal.index("JSON.stringify(c.args, null, 1)")


def test_channel_drawers_are_mutually_exclusive_and_archive_is_read_only():
    app = _read("console-app.jsx")
    open_report = app.split("const openReport = async (item) =>", 1)[1].split(
        "const dispatchLive", 1
    )[0]
    assert open_report.index("setChannelDrawerOpen(false)") < open_report.index("setDrawer({")
    open_channel = app.split("const openChannel = () =>", 1)[1].split(
        "React.useEffect", 1
    )[0]
    assert open_channel.index("setDrawer(null)") < open_channel.index("setChannelDrawerOpen(true)")
    attach = app.split("const attach = (sid) =>", 1)[1].split(
        "const requestIsCurrent", 1
    )[0]
    assert "setDrawer(null)" in attach and "setChannelDrawerOpen(false)" in attach
    assert "archived={activeSessionArchived || !!(activeWorkspace && activeWorkspace.archived)}" in app
    assert "归档对话只读，请先恢复后再管理通道" in app


def test_rail_is_compact_and_full_connected_name_lives_in_session_bar():
    rail = _read("console-rail.jsx")
    thread = _read("console-thread.jsx")
    channel = _read("console-channel.jsx")
    assert "m.id === state.sid && channelBySid && channelBySid[m.id]" in rail
    assert "m.channel_summary" in rail
    assert "<WwChannelStatusBadge status={channel} compact={true} />" in rail
    assert "channelBotName" not in rail and "telegramLabel &&" not in rail
    assert "<WwChannelStatusBadge status={channelStatus} onOpen={onOpenChannel} />" in thread
    assert "'已连接 Telegram · ' + botName" in channel
