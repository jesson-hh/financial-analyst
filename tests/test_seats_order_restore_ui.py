from pathlib import Path
import json
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "ui" / "seats" / "luozi-data.jsx").read_text(encoding="utf-8")
PANELS = (ROOT / "ui" / "seats" / "luozi-panels.jsx").read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_order_restore_reads_only_persisted_orders_and_matches_stock_and_strategy():
    helper = _between(
        DATA,
        "function latestSeatOrderFromTimeline",
        "async function fetchLatestSeatOrder",
    )
    fetcher = _between(
        DATA,
        "async function fetchLatestSeatOrder",
        "function seatOrderIdentity",
    )

    assert "x.kind === 'order'" in helper
    assert "codeKey(x.code) === wantCode" in helper
    assert "String(x.strategy_id || '').trim() === wantStrategy" in helper
    assert "seatOrderIsActive(x.validity, x.asof, x.ts, now)" in helper
    assert "&kind=order&limit=300&exclude_runs=1" in fetcher
    assert "order: order" in fetcher


def test_order_restore_rebuilds_the_full_persisted_card_without_fabricating_context():
    helper = _between(
        DATA,
        "function latestSeatOrderFromTimeline",
        "async function fetchLatestSeatOrder",
    )

    for field in ("side", "triggers", "logic", "stop", "take", "note", "validity"):
        assert f"{field}:" in helper
    assert "model_name:" in helper
    assert "asof:" in helper
    assert "ctx: null" in helper
    assert "restored: true" in helper


def test_panel_restores_on_stock_or_strategy_change_without_recalling_order_agent():
    restore_effect = _between(
        PANELS,
        "// 今日/复盘切页会卸载本面板。",
        "const SEATCN",
    )

    assert "window.lzFetchLatestSeatOrder(code, seat)" in restore_effect
    assert "setOrder(res.order)" in restore_effect
    assert "setOtf(res.order.tf)" in restore_effect
    assert "}, [code, seat]);" in restore_effect
    assert "lzSeatOrder" not in restore_effect
    assert "未用其他股票或策略的记录代替" in PANELS


def test_restored_order_uses_a_stable_fire_identity_to_avoid_repeat_alerts():
    identity = _between(DATA, "function seatOrderIdentity", "// ───────── 研报")
    live_watch = _between(PANELS, "// 同一张单在当前浏览器只发一次讯。", "const o = order && order.order;")

    assert "rec.strategy_id || strategyId" in identity
    assert "o.triggers" in identity
    assert "window.lzSeatOrderIdentity(order, code, seat)" in live_watch
    assert "localStorage.getItem(firedKey)" in live_watch
    assert "localStorage.setItem(firedKey" in live_watch
    assert PANELS.count("window.lzSeatOrderIsActive(o2.validity, order.asof, order.ts)") >= 2
    assert "每轮请求前再验一次" in live_watch


def test_order_restore_behavior_for_validity_and_strategy_matching():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable; static UI contracts above still run")

    active_fn = "function seatOrderIsActive" + DATA.split(
        "function seatOrderIsActive", 1
    )[1].split("\n\n// 条件单详情恢复", 1)[0]
    latest_fn = "function latestSeatOrderFromTimeline" + DATA.split(
        "function latestSeatOrderFromTimeline", 1
    )[1].split("\n\nasync function fetchLatestSeatOrder", 1)[0]
    identity_fn = "function seatOrderIdentity" + DATA.split(
        "function seatOrderIdentity", 1
    )[1].split("\n\n// ───────── 研报", 1)[0]
    rows = [
        {
            "id": "ordinary",
            "kind": "decide",
            "code": "SZ300750",
            "strategy_id": "s-current",
            "asof": "2026-08-14 10:00",
            "validity": "3日有效",
        },
        {
            "id": "wrong-strategy",
            "kind": "order",
            "code": "SZ300750",
            "strategy_id": "s-other",
            "asof": "2026-08-14 10:00",
            "validity": "3日有效",
            "side": "卖出",
        },
        {
            "id": "expired-july",
            "kind": "order",
            "code": "SZ300750",
            "strategy_id": "s-current",
            "asof": "2026-07-11 10:00",
            "validity": "3日有效",
            "side": "卖出",
        },
        {
            "id": "today-valid",
            "kind": "order",
            "code": "SZ300750",
            "strategy_id": "s-current",
            "asof": "2026-08-14 10:00",
            "validity": "3日有效",
            "side": "买入",
            "triggers": [{"kind": "price", "op": ">=", "value": 401}],
        },
        {
            "id": "today-valid-older",
            "kind": "order",
            "code": "SZ300750",
            "strategy_id": "s-current",
            "asof": "2026-08-14 09:00",
            "validity": "3日有效",
            "side": "观望",
        },
        {
            "id": "wrong-stock",
            "kind": "order",
            "code": "SH600519",
            "strategy_id": "s-current",
            "asof": "2026-08-14 10:00",
            "validity": "3日有效",
            "side": "买入",
        },
    ]
    script = (
        active_fn
        + "\n"
        + latest_fn
        + "\n"
        + identity_fn
        + "\nconst rows = "
        + json.dumps(rows, ensure_ascii=False)
        + ";\n"
        + "const good = latestSeatOrderFromTimeline(rows, '300750', 's-current', '2026-08-14');\n"
        + "const oldOnly = latestSeatOrderFromTimeline([rows[2]], '300750', 's-current', '2026-08-14');\n"
        + "const wrongOnly = latestSeatOrderFromTimeline([rows[1]], '300750', 's-current', '2026-08-14');\n"
        + "const wrongStock = latestSeatOrderFromTimeline([rows[5]], '300750', 's-current', '2026-08-14');\n"
        + "const unknown = seatOrderIsActive('长期', '2026-08-14', null, '2026-08-14');\n"
        + "const expiredAfterMidnight = seatOrderIsActive('今日有效', '2026-08-14', '2026-08-14T09:00:00', '2026-08-15');\n"
        + "const holidayRow = {id:'holiday-live',kind:'order',code:'SZ300750',strategy_id:'s-current',asof:'2026-08-13 15:00',ts:'2026-08-14T09:00:00',validity:'今日有效',side:'买入',triggers:[]};\n"
        + "const holidayRestored = latestSeatOrderFromTimeline([holidayRow], '300750', 's-current', '2026-08-14');\n"
        + "const fresh = {code:'SZ300750', strategy_id:'s-current', asof:'2026-08-14 10:00', tf:'day', order:{side:'买入', triggers:[{kind:'price',op:'>=',value:401}], logic:'AND', stop:null, take:null}};\n"
        + "console.log(JSON.stringify({good: good && good.decision_id, oldOnly, wrongOnly, wrongStock, unknown, expiredAfterMidnight, holidayRestored: holidayRestored && holidayRestored.decision_id, sameIdentity: seatOrderIdentity(fresh, '300750', 's-current') === seatOrderIdentity(good, '300750', 's-current')}));\n"
    )
    result = subprocess.run(
        [node, "-e", script], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    assert json.loads(result.stdout) == {
        "good": "today-valid",
        "oldOnly": None,
        "wrongOnly": None,
        "wrongStock": None,
        "unknown": False,
        "expiredAfterMidnight": False,
        "holidayRestored": "holiday-live",
        "sameIdentity": True,
    }
