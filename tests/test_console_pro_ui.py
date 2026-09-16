# -*- coding: utf-8 -*-
"""帷幄 Pro 思考模式的前端协议/缓存版本守护。"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui" / "console"


def _read(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def test_console_send_carries_closed_thinking_mode_field():
    src = _read("console-data.jsx")
    assert "async function wwSend(sid, text, thinkingMode)" in src
    assert "thinking_mode: thinkingMode === 'pro' ? 'pro' : 'standard'" in src


def test_console_confirmation_posts_and_resolves_only_the_exact_instance():
    src = _read("console-data.jsx")
    html = _read("观澜 · 帷幄.html")
    assert "if (!turnId || !confirmId)" in src
    assert "const body = { turn_id: turnId, confirm_id: confirmId, choice };" in src
    assert "String(s.confirm.confirm_id || '') === String(ev.confirm_id || '')" in src
    assert "String(s.confirm.tool || '') === String(ev.tool || '')" in src
    assert "console-data.jsx?v=20260909targeted1" in html


def test_console_composer_exposes_standard_pro_and_persists_choice():
    thread = _read("console-thread.jsx")
    app = _read("console-app.jsx")
    assert "function WwThinkingMode" in thread
    assert "{btn('standard', '标准')}" in thread
    assert "{btn('pro', '✦ Pro')}" in thread
    assert "服务端标准研究档位" in thread
    assert "服务端深度研究档位" in thread
    assert "PRO · 深度研究" in thread
    assert "DeepSeek-V4" not in thread
    assert "deepseek-reasoner" not in thread
    assert "thinkingMode={thinkingMode}" in app
    assert "guanlan:ww:thinking-mode:v1" in app


def test_console_html_bumps_every_changed_jsx_asset():
    html = _read("观澜 · 帷幄.html")
    expected = {
        "console-data.jsx": "20260909targeted1",
        "console-report-card.jsx": "20260813d",
        "console-rail.jsx": "20260828doc1",
        "console-thread.jsx": "20260912review1",
        "console-workspace-dock.jsx": "20260909targeted1",
        "console-app.jsx": "20260905agent1",
    }
    for asset, version in expected.items():
        assert f'{asset}?v={version}' in html


def test_console_renders_stable_report_evidence_from_snapshot_ledger():
    data = _read("console-data.jsx")
    app = _read("console-app.jsx")
    thread = _read("console-thread.jsx")
    assert "evidence: []" in data
    assert "ev.type === 'evidence_update'" in data
    assert "evidence: Array.isArray(d.evidence) ? d.evidence : []" in data
    assert "evidence: Array.isArray(a.evidence) ? a.evidence : []" in app
    assert "function EvidenceCite" in thread
    assert "[R-[0-9A-F]{12}" in thread
    assert "renderChatMarkdown(m.text, state.evidence)" in thread


def test_confirmation_precheck_does_not_mislabel_operational_notices_as_conflicts():
    thread = _read("console-thread.jsx")
    assert "执行前提示 · {c.precheck.length} 项" in thread
    assert "叙述与数据矛盾" not in thread


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


def test_console_removes_embedded_workbench_and_keeps_inline_results():
    html = _read("观澜 · 帷幄.html")
    data = _read("console-data.jsx")
    app = _read("console-app.jsx")
    thread = _read("console-thread.jsx")
    assert "console-bench.jsx" not in html
    assert "WwBench" not in app
    assert "benchOpen" not in app and "benchClosed" not in app and "benchW" not in app
    assert "const cols = stage ? (railW + 'px 1fr')" in app
    assert "gridTemplateColumns: cols" in app
    assert "<WwWorkspaceStage" in app
    assert "<WwWorkspaceDock" in app
    assert "function WwLauncher" not in thread and "<WwLauncher" not in thread
    assert "activatedPages" not in thread and "onOpenPage" not in thread
    # WW_PAGES 是现行工作空间 Stage 的导航注册表，不属于已移除的右侧 Bench 状态。
    assert "const WW_PAGES = {" in data and "PAGES: WW_PAGES" in data
    assert "activated:" not in data and "artifacts:" not in data
    assert "window.WwBench" not in data and "function WwBench" not in data
    assert "ToolChain" in thread and "WwReportCard" in thread
    assert "../seats/观澜 · 落子.html" in thread


def test_seat_bind_survives_snapshot_without_reopening_a_side_panel():
    app = _read("console-app.jsx")
    thread = _read("console-thread.jsx")
    assert "function applySeatBindEvent(sid, ev)" in app
    assert "guanlan:ww:seat-bind-applied:v1:" in app
    assert "(state.events || []).forEach(ev => applySeatBindEvent(state.sid, ev))" in app
    assert "GL.put({" in app and "type: 'strategy'" in app
    assert "applySeatBind(a.ev.artifact.payload" not in app
    assert "window.GL.handoff('cockpit'" in app
    assert "href={seatsHref}" in thread
