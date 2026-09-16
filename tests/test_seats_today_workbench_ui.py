"""落子「今日」页可收起操作台的静态交付契约。

这些检查刻意围绕组件边界和可访问性属性，而不是整段 JSX 文本：布局文案或
样式微调不应让测试失效，但不能把条件单轮询随抽屉一起卸载。
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "ui" / "seats" / "luozi-app.jsx").read_text(encoding="utf-8")
PANELS = (ROOT / "ui" / "seats" / "luozi-panels.jsx").read_text(encoding="utf-8")
HTML = (ROOT / "ui" / "seats" / "观澜 · 落子.html").read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    assert start in text, f"missing start marker: {start}"
    tail = text.split(start, 1)[1]
    assert end in tail, f"missing end marker after {start}: {end}"
    return tail.split(end, 1)[0]


def _asset_version(asset: str) -> str:
    match = re.search(rf'src="{re.escape(asset)}\?v=([^"&]+)', HTML)
    assert match, f"{asset} must carry a cache-busting version"
    return match.group(1)


def test_today_replaces_the_permanent_rail_with_one_accessible_workbench() -> None:
    # The replay inspector may keep its existing rail; the Today page must not keep
    # the second always-on 372px rail that used to squeeze the chart.
    assert APP.count("width: 372") <= 1

    assert APP.count('aria-controls="luozi-workbench"') == 1
    assert 'id="luozi-workbench"' in APP
    assert 'data-testid="luozi-workbench"' in APP
    assert re.search(r'id="luozi-workbench"[^>]+role="(?:dialog|complementary)"', APP)
    assert 'aria-label="落子操作台"' in APP

    # One trigger opens a single window; its internal navigation owns the tools.
    assert 'role="tablist"' in APP
    assert 'role="tab"' in APP
    assert "const workbenchTabs = [['judge', '研判'], ['order', '条件单'], ['trail', '记录']];" in APP
    for panel, label in (("judge", "研判"), ("order", "条件单"), ("trail", "记录")):
        assert f'data-workbench-panel="{panel}"' in APP
        assert label in APP


def test_live_workers_stay_mounted_and_closed_state_is_css_only() -> None:
    # Every panel exists once in the Today tree.  Visibility changes on wrappers;
    # it is not `open && <Panel />`, which would cancel an in-flight judgement or
    # the condition-order 8-second watcher.
    assert APP.count("<JudgeCard ") == 1
    assert APP.count("<OrderWatchPanel ") == 1
    for component in ("JudgeCard", "OrderWatchPanel"):
        assert not re.search(
            rf"(?:workbenchOpen|activeTool|workbenchTab)[^\n]{{0,160}}"
            rf"(?:&&|\?)\s*<{component}\b",
            APP,
        )

    assert "visibility:" in APP
    assert "pointerEvents:" in APP
    assert "aria-hidden=" in APP


def test_order_reference_and_record_have_unambiguous_ownership() -> None:
    judge = _between(
        APP,
        'data-workbench-panel="judge"',
        'data-workbench-panel="order"',
    )
    order = _between(
        APP,
        'data-workbench-panel="order"',
        'data-workbench-panel="trail"',
    )
    trail = APP.split('data-workbench-panel="trail"', 1)[1]

    assert "<JudgeCard " in judge
    assert "<OrderWatchPanel " not in judge
    assert "<DecisionTrail " not in judge

    assert "<OrderWatchPanel " in order
    assert "<OrderbookTicksPanel " in order
    assert "行情参考" in order
    assert "<details" in order

    assert "<DecisionTrail " in trail
    assert "<OrderWatchPanel " not in trail


def test_judgement_no_longer_duplicates_the_record_timeline() -> None:
    judge_card = _between(PANELS, "function JudgeCard(", "function DecisionTrail(")
    assert "lzFetchDecisionsTimeline" not in judge_card
    assert "<DecisionHistory" not in judge_card
    assert "rows.slice(" not in judge_card
    assert "⏱ 全部" not in judge_card

    # The sole historical owner remains the read-only record component.
    decision_trail = _between(PANELS, "function DecisionTrail(", "Object.assign(window")
    assert "lzFetchDecisionsTimeline" in decision_trail
    assert "只读" in decision_trail


def test_record_refreshes_after_hidden_judgement_or_order_activity() -> None:
    # The record panel stays mounted too.  A bump from judgement/order completion
    # and opening the Record tab must refresh it instead of showing its first fetch
    # forever.
    decision_trail = _between(PANELS, "function DecisionTrail(", "Object.assign(window")
    signature = decision_trail.split(")", 1)[0]
    assert "active" in signature
    assert "bump" in signature
    assert re.search(r"\[(?:[^\]]*,\s*)?code[^\]]*active[^\]]*bump[^\]]*\]", decision_trail)
    assert "onDecisionComplete" in APP
    assert "onRecordChange" in APP


def test_condition_trigger_badges_and_chart_marks_are_current_context_only() -> None:
    # A trigger from another stock/strategy must never appear on the current K line
    # or light the current operation button.
    assert "const currentOrderTriggerPrefix = String(code || '') + '·' + String(curSid || '');" in APP
    assert "const currentOrderTriggers = orderTriggers.filter" in APP
    assert "const currentOrderTriggered = currentOrderTriggers.length > 0;" in APP
    assert "triggers={page === 'today' ? currentOrderTriggers : []}" in APP
    assert "triggers={orderTriggers}" not in APP


def test_workbench_assets_share_a_new_cache_generation() -> None:
    app_version = _asset_version("luozi-app.jsx")
    panels_version = _asset_version("luozi-panels.jsx")
    assert app_version == panels_version
    assert app_version == "20260831evidence2"
