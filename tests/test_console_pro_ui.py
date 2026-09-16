# -*- coding: utf-8 -*-
"""帷幄 Pro、运行中转向与研究空间前端协议/缓存版本守护。"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui" / "console"


def _read(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def test_console_send_carries_closed_thinking_mode_field():
    src = _read("console-data.jsx")
    assert "async function wwSend(sid, text, thinkingMode)" in src
    assert "thinking_mode: thinkingMode === 'pro' ? 'pro' : 'standard'" in src


def test_console_busy_send_uses_atomic_steer_and_exposes_idempotent_cancel():
    data = _read("console-data.jsx")
    app = _read("console-app.jsx")
    assert "function wwSteer(sid, text, thinkingMode)" in data
    assert "'/console/steer'" in data
    assert "function wwCancel(sid)" in data
    assert "'/console/cancel'" in data
    assert "http_status: r.status" in data  # HTTP 202 is accepted through Response.ok
    assert "steer: wwSteer, cancel: wwCancel" in data
    assert "const steered = await WW.steer(requestSid, text, thinkingMode)" in app
    assert "const cancelled = await WW.cancel(requestSid)" in app
    busy_branch = app.split("if (state.busy) {", 1)[1].split(
        "dispatch({ type: 'optimisticTurn'", 1)[0]
    assert "WW.steer" in busy_branch
    assert "WW.send" not in busy_branch  # steer 成功后绝不再补 /send


def test_console_steer_sse_and_composer_show_safe_transition_without_double_submit():
    data = _read("console-data.jsx")
    thread = _read("console-thread.jsx")
    assert "status === 'steering'" in data
    assert "status === 'cancelling' ? 'stopping'" in data
    assert "ev.type === 'control_event'" in data
    assert "status === 'cancelled'" in data
    assert "awaitingReplacement" in data
    assert "if (!t || submitting || archived)" in thread
    assert "if (!t || state.busy" not in thread
    assert "输入新方向，Enter 将安全打断并转向" in thread
    assert "调整方向 ↵" in thread
    assert "正在安全停止当前任务" in thread
    assert "已按新方向继续" in thread
    assert 'role="status" aria-live="polite"' in thread
    assert "停止本轮" in thread and "onStop={stop}" in thread
    assert "disabled={archived}" in thread
    assert "disabled={archived || !!submitting}" in thread


def test_console_stream_generation_rejects_stale_session_callbacks():
    data = _read("console-data.jsx")
    app = _read("console-app.jsx")
    assert "function wwConnect(sid, dispatch, generation)" in data
    assert "sid, generation: streamGeneration" in data
    assert "const streamGenRef = React.useRef(0)" in app
    assert "const generation = ++streamGenRef.current" in app
    assert "WW.connect(sid, dispatchLive, generation)" in app
    assert "a.sid !== s.sid" in app
    assert "a.generation" in app and "s.streamGeneration" in app


def test_console_runtime_and_http_races_have_one_authoritative_owner():
    data = _read("console-data.jsx")
    app = _read("console-app.jsx")
    assert "runtime: d.runtime || null" in data
    assert "const controlRaw = String(runtime.control_status" in app
    assert "runtime.cancel_requested" in app
    assert "n.busy = running" in app
    assert "runtime.pending_confirm_turn_id" in app
    assert "if (pendingTurnId)" in app and "n.busy = true" in app
    assert "requestIsCurrent(requestSid, requestGeneration)" in app
    assert "source !== 'local'" in app and "requestToken !== a.requestToken" in app
    assert "type: 'optimisticTurn'" in app
    assert "<WwThread key={state.sid || 'no-session'}" in app
    assert "if (!steered || !steered.ok)" in app
    assert "if (!cancelled || !cancelled.ok)" in app


def test_console_confirm_can_atomically_steer_without_keyboard_accidents():
    thread = _read("console-thread.jsx")
    assert "function ConfirmModal({ confirm, onChoice, onStop, onSteer })" in thread
    assert "const [steerDraft, setSteerDraft]" in thread
    assert "const r = await onSteer(text)" in thread
    assert "target.tagName === 'INPUT'" in thread
    assert "target.tagName === 'TEXTAREA'" in thread
    assert "target.isContentEditable" in thread
    assert "key === 'escape'" in thread and "resolve('n')" in thread
    assert "onSteer={onSend}" in thread
    assert "key={state.confirm.turn_id || 'confirm'}" in thread


def test_console_turn_scopes_interruption_and_nonterminal_errors():
    data = _read("console-data.jsx")
    thread = _read("console-thread.jsx")
    assert "let currentTurnId = null" in data
    assert "turnId: eventTurnId || currentTurnId" in data
    assert "const cancelledTurnId = ev.task_id || ev.turn_id || null" in data
    assert "row.turnId === cancelledTurnId" in data
    assert "items[i].turnId === cancelledTurnId" in data
    assert "candidate.turnId === eventTurnId" in data
    assert "ev.terminal !== false" in data
    assert "ev.terminal === false" in data and "source: 'task_update'" in data
    assert "ev.terminal === false ? 'warning' : 'error'" in data
    assert "m.interrupted" in thread and "本段因调整方向而中止" in thread


def test_console_composer_exposes_standard_pro_and_persists_choice():
    thread = _read("console-thread.jsx")
    app = _read("console-app.jsx")
    assert "function WwThinkingMode" in thread
    assert "{btn('standard', '标准')}" in thread
    assert "{btn('pro', '✦ Pro')}" in thread
    assert "DeepSeek-V4-Flash" in thread
    assert "DeepSeek-V4-Pro" in thread
    assert "PRO · V4-PRO · MAX" in thread
    assert "deepseek-reasoner" not in thread
    assert "thinkingMode={thinkingMode}" in app
    assert "guanlan:ww:thinking-mode:v1" in app


def test_console_html_bumps_every_changed_jsx_asset():
    html = _read("观澜 · 帷幄.html")
    assert "console-report-card.jsx?v=20260813d" in html
    assert 'console-rail.jsx?v=20260819a1' in html
    assert 'console-memory.jsx?v=20260823memory5' in html
    assert 'console-workspace-dock.jsx?v=20260905agent1' in html
    for asset in ("console-data.jsx", "console-thread.jsx"):
        assert f'{asset}?v=20260905agent1' in html
    assert 'console-app.jsx?v=20260905agent1' in html
    assert 'console-bench.jsx?v=20260905agent1' in html


def test_console_moves_session_plan_to_the_thread_task_panel():
    rail = _read("console-rail.jsx")
    thread = _read("console-thread.jsx")
    app = _read("console-app.jsx")
    assert "任务计划</h3>" not in rail
    assert "state.plan.map" not in rail
    assert "本会话计划" in thread
    assert "plan.map" in thread
    assert "计划 · " in thread and "执行 · " in thread
    assert "plan: meta && Array.isArray(meta.plan) ? meta.plan : []" in app


def test_console_only_mounts_review_report_outside_the_sidebar():
    html = _read("观澜 · 帷幄.html")
    rail = _read("console-rail.jsx")
    thread = _read("console-thread.jsx")
    report = _read("console-report-card.jsx")
    assert "console-plan-approval-card.jsx" not in html
    assert "console-recommendation-card.jsx" not in html
    assert "WwReviewReportCard" not in rail
    assert "WwPlanApprovalCard" not in rail
    assert "WwRecommendationCard" not in rail
    assert "<WwReviewReportCard />" in thread
    assert "复盘日报" in report and "陈旧" in report


def test_console_workspace_tree_and_session_scoping_contract():
    data = _read("console-data.jsx")
    rail = _read("console-rail.jsx")
    app = _read("console-app.jsx")
    assert "async function wwWorkspaces()" in data
    assert "'/console/workspaces'" in data
    assert "workspace_id: workspaceId || WW_GENERAL_WORKSPACE.id" in data
    assert "workspace_id: wid" not in data  # no hard-coded cross-space reassignment
    assert "研究空间" in rail and "通用研究" in data
    assert "m.workspace_id || generalId" in rail
    assert "onUpdate(m.id, { workspace_id: wid })" in rail
    assert "activeWorkspaceId" in app and "workspace={activeWorkspace}" in app


def test_console_memory_center_has_scoped_governance_and_safe_mutations():
    html = _read("观澜 · 帷幄.html")
    memory = _read("console-memory.jsx")
    dock = _read("console-workspace-dock.jsx")
    app = _read("console-app.jsx")
    assert "console-memory.jsx" in html
    assert "['memory', '记忆']" in dock and "研究 · 记忆" in dock
    assert "<WwMemoryCenter workspaceId={wid} sessionId={sessionId}" in dock
    assert "sessionId={state.sid}" in app
    assert "'/console/memories?'" in memory
    assert "workspace_id: wid" in memory and "session_id: sid" in memory
    assert all(label in memory for label in ("全局", "当前研究空间", "本会话", "归档"))
    assert "搜索内容或主题键" in memory
    assert "记忆主题键" in memory and "记忆内容" in memory
    assert "确认删除？" in memory and "永久删除" in memory
    assert "pinned: !item.pinned" in memory
    assert "不会被自动收敛到归档" in memory and "旧标的" in memory
    assert "'/copy'" in memory and "复制到全局" in memory and "复制到研究空间" in memory and "复制到本会话" in memory
    archive_card = memory.split("function WwMemoryCard", 1)[1].split("function WwMemoryCenter", 1)[0]
    assert "!item.readOnly" in archive_card and "只读原件" in archive_card
    assert "归档原件不可编辑、置顶或删除" in archive_card
    assert "item.editable" in archive_card and "item.deletable" in archive_card and "item.canPin" in archive_card
    assert "if (!item.canCopy) return null" in memory
    scope_normalizer = memory.split("function wwMemoryScope", 1)[1].split("function wwMemoryRows", 1)[0]
    assert "raw === 'global_archive'" in scope_normalizer and "row && row.archived" in scope_normalizer
    assert "row.read_only" not in scope_normalizer
    assert "etag: item.etag || undefined" in memory


def test_console_memory_insights_are_explainable_reversible_and_non_destructive():
    memory = _read("console-memory.jsx")
    assert "'/console/memory-insights?'" in memory
    assert "'/console/memory-insights/'" in memory and "'/dismiss?'" in memory
    assert "include_archived: 'true'" in memory and "include_dismissed: '1'" in memory
    assert all(text in memory for text in ("记忆健康", "需处理", "建议关注", "可优化"))
    assert "证据与影响范围" in memory and "impactDescription" in memory
    assert "定位" in memory and "已高亮建议关联的" in memory
    assert "记忆体检暂不可用，不影响下方记忆管理" in memory
    assert "正在积累使用证据，不会误判长期未使用" in memory
    assert "治理建议只辅助核对，不会自动改写或删除记忆" in memory
    assert "已忽略" in memory and "恢复建议" in memory
    assert "dismissed: dismissed !== false" in memory
    assert "reloadInsights();" in memory
    assert "hasOpenSeverityCounts" in memory
    assert "setConfirmDeleteId(memoryId)" in memory
    insight_action = memory.split("const onInsightAction", 1)[1].split("const visibleRows", 1)[0]
    assert "method: 'DELETE'" not in insight_action
    assert "onPin(item)" in insight_action and "onCopy(item, targetScope)" in insight_action
    insight_panel = memory.split("function WwMemoryInsightsPanel", 1)[1].split("function WwMemoryCard", 1)[0]
    assert "showDismissed" in insight_panel and "onDismiss(insight, false)" in insight_panel


def test_console_session_archive_restore_is_soft_and_visible():
    data = _read("console-data.jsx")
    rail = _read("console-rail.jsx")
    app = _read("console-app.jsx")
    thread = _read("console-thread.jsx")
    assert "function wwArchiveSession" in data and "function wwRestoreSession" in data
    assert "wwSessionLifecycle(sid, 'archive')" in data
    assert "wwSessionLifecycle(sid, 'restore')" in data
    assert "archiveSession: wwArchiveSession" in data
    assert "restoreSession: wwRestoreSession" in data
    assert "已归档对话" in rail and "恢复到原研究空间" in rail
    assert "activeSessions = sessions.filter(m => !m.archived)" in rail
    assert "onArchiveSession" in app and "onRestoreSession" in app
    assert "activeSessionArchived" in app
    assert "sessionArchived={activeSessionArchived}" in app
    assert "该对话已归档 · 全部历史仍保留" in thread
    assert "该对话已归档——恢复后可继续" in thread


def test_console_external_folder_uses_desktop_grant_not_raw_path():
    data = _read("console-data.jsx")
    rail = _read("console-rail.jsx")
    assert "choose_research_folder" in data
    assert "payload.folder_grant = folder.grant" in rail
    assert "payload.root_path" not in rail
    assert "当前浏览器不具备目录授权能力" in data


def test_console_workspace_dock_is_honest_and_stage_keeps_tree():
    dock = _read("console-workspace-dock.jsx")
    app = _read("console-app.jsx")
    bench = _read("console-bench.jsx")
    assert all(label in dock for label in ("总览", "研究", "文件", "Agent", "盯盘"))
    assert "文件浏览能力尚未接入" in dock
    assert "代码 Agent 运行服务尚未接入" in dock
    assert "Agent 模式更新失败" in dock and 'role="status"' in dock
    assert "WwWorkspaceStage" in dock and "← 返回对话" in dock and "独立窗口 ↗" in dock
    assert "<WwRail" in app and "<WwWorkspaceStage" in app
    assert "const wsId = workspaceId || state.sid || 'rw_general'" in bench
    assert "GL.handoff(art.channel, art.payload, wsId)" in bench


def test_embedded_workflow_can_return_to_the_current_conversation():
    dock = _read("console-workspace-dock.jsx")
    bench = _read("console-bench.jsx")
    event = "guanlan:ww:back-to-conversation"
    assert event in bench and "if (onClose) onClose()" in bench
    assert "frame.contentWindow === event.source" in bench
    assert event in dock and "if (onBack) onBack()" in dock
    assert "frameRef.current.contentWindow !== event.source" in dock


def test_console_workspace_watch_projection_is_scoped_and_compact():
    data = _read("console-data.jsx")
    dock = _read("console-workspace-dock.jsx")
    assert "function wwWorkspaceSeats(wid, limit)" in data
    assert "'/seats?limit=' + encodeURIComponent(n)" in data
    assert "workspaceSeats: wwWorkspaceSeats" in data
    assert "function wwCanonicalSecurityCode(value)" in data
    assert "function WwWorkspaceWatch" in dock
    assert "真实证据" in dock and "判断节拍" in dock and "思考模式" in dock
    assert "最新行情快照" in dock and "最新判断" in dock and "判断时间线" in dock
    assert "条件' + (d.side || '单')" in dock and "triggerText" in dock
    for field in ("return_1m_pct", "return_5m_pct", "return_15m_pct", "return_30m_pct", "day_high", "day_low"):
        assert field in dock
    assert "实际 {wwWatchTf(snapshotFreq)}" in dock
    assert "完整 K 线、策略编辑与条件单留在落子" in dock
    assert "CandleChart" not in dock
    assert "scopeMismatch" in dock and "数据已拦截" in dock
    assert "通用研究没有单票盯盘" in dock
    assert "当前落子盯盘只支持 A 股 / 北交所证券" in dock
    assert "归档空间只展示既有配置与研判" in dock


def test_console_workspace_watch_rejects_stale_space_requests():
    dock = _read("console-workspace-dock.jsx")
    assert "const watchRequestSeqRef = React.useRef(0)" in dock
    assert "const requestedWid = wid" in dock
    assert "const watchSeq = key === 'watch' ? ++watchRequestSeqRef.current" in dock
    assert "watchSeq === watchRequestSeqRef.current && widRef.current === requestedWid" in dock
    assert "if (tab === 'watch') watchRequestSeqRef.current += 1" in dock
    assert "window.WW.workspaceSeats(requestedWid, 50)" in dock


def test_console_seats_stage_uses_code_deep_link_and_isolated_handoff():
    dock = _read("console-workspace-dock.jsx")
    app = _read("console-app.jsx")
    thread = _read("console-thread.jsx")
    assert "&code=" in dock and "&focus=" in dock
    assert "window.GL.handoff('cockpit'" in dock
    assert "React.useEffect(() => { handoffSeats();" not in dock
    assert "code={stage.code}" in app and "focus={stage.focus}" in app
    assert "workspaceId={stage.workspaceId || activeWorkspaceId}" in app
    assert "const stageWorkspaceId = stage && (stage.workspaceId || activeWorkspaceId)" in app
    assert "readOnly={stageReadOnly}" in app
    assert "workspaceId: generalId, global: true" in app
    sentry_focus = app.split("const onSentryFocus = (it) => {", 1)[1].split("};", 1)[0]
    assert "activeWorkspaceId" not in sentry_focus
    assert "openPage('seats')" not in sentry_focus
    assert "独立落子" in thread


def test_console_backend_managed_seat_bind_never_creates_random_legacy_strategy():
    app = _read("console-app.jsx")
    managed = app.split("if (p.backendManaged === true) {", 1)[1].split(
        "} else if (code", 1
    )[0]
    assert "p.strategy" in managed
    assert "GL.put({ ...strategy" in managed
    assert "Date.now" not in managed and "Math.random" not in managed
    assert "controlPlane" not in managed  # strategy mirror only; assignment remains backend truth


def test_console_workspace_files_open_real_read_only_preview():
    data = _read("console-data.jsx")
    dock = _read("console-workspace-dock.jsx")
    assert "function wwWorkspaceFile(wid, path)" in data
    assert "'/file?path=' + encodeURIComponent(path || '')" in data
    assert "workspaceFile: wwWorkspaceFile" in data
    assert "window.WW.workspaceFile(requestedWid, requestedPath)" in dock
    assert "preview.content" in dock and "sha256.slice(0, 8)" in dock
    assert "const seq = ++fileRequestSeqRef.current" in dock
    assert "current.wid === requestedWid && current.path === requestedPath" in dock
    assert "setInterval(() => loadFile(false), 12000)" in dock
    assert "clearInterval(timer)" in dock
    assert "onCloseFile={invalidateFilePreview}" in dock


def test_console_workspace_asset_workflow_deep_links_into_stage():
    data = _read("console-data.jsx")
    dock = _read("console-workspace-dock.jsx")
    app = _read("console-app.jsx")
    bench = _read("console-bench.jsx")
    assert "function wwWorkspaceAsset(wid, assetId)" in data
    assert "a.kind === 'workflow' && a.ref" in dock
    assert "{ page: 'factor', load: String(a.ref) }" in dock
    assert "{ page: 'factor', asset: String(a.ref || a.id) }" in dock
    assert "&load=" in dock
    assert "&asset=" in dock
    assert "const [stage, setStage]" in app
    assert "load={stage.load} asset={stage.asset}" in app
    assert "art.payload.asset_id" in bench and "&asset=" in bench
    assert "art.channel && !assetId" in bench


def test_shared_workspace_bench_keys_artifact_identity_by_session():
    bench = _read("console-bench.jsx")
    assert "const sidPrefix = String(state.sid || '') + ':'" in bench
    assert "const identity = sidPrefix + String(a.evId)" in bench
    assert "[state.sid, state.artifacts.length]" in bench


def test_archived_workspaces_are_history_only_and_stage_clears_on_space_switch():
    dock = _read("console-workspace-dock.jsx")
    rail = _read("console-rail.jsx")
    app = _read("console-app.jsx")
    assert "const activeRows = rows.filter(w => !w.archived)" in rail
    assert "w.archived ? ' · 已归档' : ''" in rail
    assert "{!w.archived && <span title=\"在此空间新建对话\"" in rail
    assert "stageWorkspaceRef.current !== activeWorkspaceId" in app
    assert "setStage(null)" in app
    assert "只读历史" in dock
    assert "归档空间保留原目录信息，不再提供目录绑定或更换" in dock
    assert "onClick={archived ? undefined" in dock
    assert "归档空间不能更改 Agent 模式；下方运行记录仍可查看" in dock
    assert "workspaceAssets(requestedWid)" in dock and "workspaceAgentRuns(requestedWid)" in dock
    assert "disabled={archived}" in _read("console-thread.jsx")
    assert "stageWorkspace && stageWorkspace.archived" in app
    assert "readOnly={stageReadOnly}" in app


def test_console_media_image_uses_only_backend_opaque_id_and_has_complete_card_states():
    data = _read("console-data.jsx")
    thread = _read("console-thread.jsx")
    normalizer = data.split("function wwNormalizeMediaImage(payload)", 1)[1].split(
        "// 单事件折叠进状态", 1
    )[0]
    assert "ev.artifact.kind === 'media_image'" in data
    assert "p.media_id" in normalizer
    assert "p.path" not in normalizer and "p.url" not in normalizer and "p.src" not in normalizer
    assert "'/console/media/' + encodeURIComponent(id)" in data
    assert "mediaUrl: wwMediaUrl" in data
    assert "function WwMediaImageCard({ item })" in thread
    assert "window.WW.mediaUrl(item.mediaId)" in thread
    assert "download={item.name}" in thread
    assert "alt={item.alt}" in thread
    assert "图片加载中" in thread and "图片加载失败" in thread
    assert 'role="dialog"' in thread and 'aria-modal="true"' in thread
    assert "item.width" in thread and "item.height" in thread
    assert "userSelect: 'text'" in thread
