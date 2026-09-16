"""落子 Pro·10席事件深研 UI 的静态契约。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "ui" / "seats" / "luozi-app.jsx").read_text(encoding="utf-8")
PANELS = (ROOT / "ui" / "seats" / "luozi-panels.jsx").read_text(encoding="utf-8")
HTML = (ROOT / "ui" / "seats" / "观澜 · 落子.html").read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_deep_authorization_uses_one_explicit_backend_abi() -> None:
    helper = _between(APP, "async function appDeepAuthRequest", "// 全局总闸")
    assert "'/seats/watch/assignments/'" in helper
    assert "'/deep-authorization'" in helper
    assert "'/seats/watch/deep-authorizations/'" in helper
    assert "expected_revision:" in helper
    assert "max_runs: 1" in helper
    assert "max_llm_invocations: 20" in helper
    assert "reason: '当日盯盘10席深研'" in helper
    assert "confirm_text:" in helper
    assert "{ reason: '用户撤销' }" in helper
    assert "Promise.all" not in helper


def test_reading_status_never_grants_authorization() -> None:
    read_effect = _between(APP, "const persistedPeriod", "const refreshDeepAuth")
    assert "appDeepAuthRequest(meta.code, 'read')" in read_effect
    assert "'authorize'" not in read_effect
    assert "window.prompt('请逐字输入以下服务端确认文字" in APP
    assert "entered !== deepAuth.requiredConfirmText" in APP
    assert "appDeepAuthRequest(meta.code, 'authorize', deepAuth, entered)" in APP
    assert "confirm_text: String(confirmText" in APP
    assert "仅研究、不交易；本次授权最多 1 次深研 / 20 次模型调用" in APP
    assert "选择 Pro 或保存配置都不会自动授权" in APP
    assert "session_label" in APP
    assert "授权下一交易日" in APP
    assert "授权本交易时段" in APP


def test_period_ineligible_state_is_visible_and_cannot_authorize() -> None:
    assert "eligible: bool(raw.eligible, envelope.eligible)" in APP
    assert "periodSupported:" in APP
    assert "raw.effective_evidence_tf" in APP
    assert "supportedPeriods" in APP
    assert "raw.assignment_revision, envelope.assignment_revision" in APP
    assert "global_deep_budget" in APP
    assert "全局深研余量" in APP
    assert "deepAuth.eligible === false" in APP
    assert "deepAuth.periodSupported === false" in APP
    assert "周期资格" in APP
    assert "10席深研支持周期" in APP
    assert "不会退回日线" in APP
    assert "分钟周期不回退日线" in APP


def test_every_unsaved_assignment_field_blocks_authorization() -> None:
    draft = _between(APP, "const draftDirty", "useEffect(() =>")
    for token in (
        "enabled !== !!assignment.enabled",
        "strategyId",
        "evidenceTF",
        "cadence",
        "thinking",
    ):
        assert token in draft
    assert "|| deepAuth.eligible === false || deepAuth.periodSupported === false || draftDirty" in APP
    assert "授权只绑定已落盘配置" in APP
    assert "已预授权 · 待生效" in APP
    assert "thinking_mode_not_deep" in APP
    assert "unsupported_evidence_tf_for_10_agent" in APP
    assert "assignment_session_already_consumed" in APP


def test_manual_single_model_and_automatic_ten_seat_copy_are_distinct() -> None:
    assert PANELS.count("modeBtn('fast', '本次快速研判'") == 2
    assert PANELS.count("modeBtn('deep', '本次单模型深思'") == 2
    assert "modeBtn('deep', 'Pro'" not in PANELS
    assert "快 · V4" not in PANELS
    assert '<option value="fast">快速研判</option>' in APP
    assert '<option value="deep">Pro · 10席事件深研</option>' in APP
    assert "自动研判 · {assignment.thinking_mode === 'deep' ? 'Pro·10席事件深研' : '快速研判'}" in PANELS


def test_orchestrated_timeline_is_marked_without_inventing_old_fields() -> None:
    trace = _between(PANELS, "function lzDeepTraceMeta", "// ───────── 证据链渲染")
    assert "source || '').toLowerCase() !== 'orchestrated'" in trace
    assert "pipeline.luozi_deep_decide_reduced" in trace
    assert "pipeline.luozi_deep_decide" in trace
    assert "full" in trace and "degraded" in trace
    assert "escalation_triggers" in trace and "escalation_digest" in trace
    assert "node_summaries" in trace and "agent_summaries" in trace
    assert "evidence_warnings" in trace
    assert "证据缺口" in PANELS
    assert "confidence_band" in trace
    assert "深研置信 · {meta.confidenceBand}" in trace
    assert "此记录未包含触发或节点摘要字段" in trace
    assert "节点摘要 · 该记录未返回（兼容旧记录）" in trace
    # 历史只由 DecisionHistory（旧实时卡入口）和统一「记录」拥有；
    # JudgeCard 不再复制一条时间线。
    assert PANELS.count("<DeepTraceBadge row={r} />") >= 2
    assert "<DeepTraceDetails row={dec} />" in PANELS


def test_degraded_deep_attempt_is_audit_only_not_a_kline_decision() -> None:
    assert "rows.filter(r => r.kind === 'decide')" in APP
    assert "rows.filter(r => r.kind !== 'order')" not in APP
    assert "r.kind === 'deep_attempt'" in PANELS
    assert "深研降级" in PANELS
    assert "深研尝试" in PANELS
    assert "deepAttempt ? '深研尝试'" in PANELS
    assert "r.deep_status === 'degraded' ? '降级'" in PANELS


def test_html_cache_keys_include_pro_deep_ui_revision() -> None:
    assert "luozi-panels.jsx?v=20260831evidence2" in HTML
    assert "luozi-app.jsx?v=20260831evidence2" in HTML
