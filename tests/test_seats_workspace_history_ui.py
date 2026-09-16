"""落子研判的工作空间历史摘要 UI 契约。"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PANELS = (ROOT / "ui" / "seats" / "luozi-panels.jsx").read_text(encoding="utf-8")
APP = (ROOT / "ui" / "seats" / "luozi-app.jsx").read_text(encoding="utf-8")
HTML = (ROOT / "ui" / "seats" / "观澜 · 落子.html").read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_manual_decisions_no_longer_send_legacy_research() -> None:
    assert "research: rcp.research" not in PANELS
    assert "lzFetchReportStatus" not in PANELS
    assert "引用 · 研报观点" not in PANELS
    assert "研报生成中" not in PANELS
    assert "当日浮出研报(PIT 按日)" not in PANELS
    assert "📄" not in PANELS
    assert "history_replay: mode === 'backtest'" in PANELS


def test_immediate_response_shows_only_a_bounded_condensed_summary() -> None:
    assert 'const LZ_WORKSPACE_HISTORY_UI_MAX = 3;' in PANELS
    assert "raw.slice(0, LZ_WORKSPACE_HISTORY_UI_MAX)" in PANELS
    assert 'label="工作空间历史摘要·仅背景"' in PANELS
    assert PANELS.count(
        "<WorkspaceHistorySummary history={decide.workspace_history_items} />"
    ) == 2
    assert "不以条数作为判断依据" in PANELS


def test_historical_rows_only_render_safe_reference_metadata() -> None:
    reference = _between(
        PANELS,
        "function WorkspaceHistoryReference",
        "function lzDeepText",
    )
    assert "使用 {meta.count} 条" in reference
    assert "digest" in reference
    assert "workspace_id" not in reference
    assert ".items" not in reference
    assert ".text" not in reference
    assert "旧版研报引用·已停用" in reference
    assert "workspace_history_ref: res.workspace_history_ref || null" in APP
    assert "workspace_history_ref: r.workspace_history_ref || null" in APP
    assert "research: r.research" not in APP
    run_mapping = _between(APP, "setRunDecs(rows.map", "}));")
    assert "legacy_research:" in run_mapping
    assert "Array.isArray(r.research) && r.research.length > 0" in run_mapping
    assert re.search(r"(?m)^\s*research\s*:", run_mapping) is None


def test_copy_and_cache_keys_match_the_new_evidence_source() -> None:
    assert 'title="真调 LLM：配方卡/因子+工作空间摘要+市况' in PANELS
    assert "综合配方、工作空间历史摘要与市况研判" in PANELS
    assert "工作空间历史摘要使用记录" in APP
    assert "当日浮出研报" not in APP
    assert "luozi-panels.jsx?v=20260831evidence2" in HTML
    assert "luozi-app.jsx?v=20260831evidence2" in HTML
