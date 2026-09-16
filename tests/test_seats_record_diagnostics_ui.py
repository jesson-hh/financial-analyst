"""落子历史记录诊断与今日手动 quote 接线的静态 UI 契约。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "ui" / "seats" / "luozi-app.jsx").read_text(encoding="utf-8")
PANELS = (ROOT / "ui" / "seats" / "luozi-panels.jsx").read_text(encoding="utf-8")
HTML = (ROOT / "ui" / "seats" / "观澜 · 落子.html").read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    assert start in text, f"missing start marker: {start}"
    tail = text.split(start, 1)[1]
    assert end in tail, f"missing end marker after {start}: {end}"
    return tail.split(end, 1)[0]


def test_record_diagnostics_show_provenance_quality_and_refs_without_actions() -> None:
    block = _between(PANELS, "function SeatRecordDiagnostics", "// 仅 source=orchestrated")
    for token in (
        "evidence_source", "data_asof", "data_quality", "quality_flagged_n",
        "complete_n", "data_digest", "data_ref", "factors_std",
        "factor_diagnostics", "ref_resolution", "ref_diagnostics", "recipe_factors",
        "card_names", "workspace_history_ref", "pa_features", "deep_status",
    ):
        assert token in block
    external_helper = _between(
        PANELS, "function lzRecordExternalEvidenceSummary", "// 历史记录的只读诊断面",
    )
    for token in ("external_evidence", "evidence_diagnostics", "anchor_update", "item_count"):
        assert token in external_helper
    for copy in (
        "总数", "完整", "异常", "因子 / 引用诊断", "价量形态诊断（确定性记录）",
        "source ·", "deep_status ·", "evidence_source ·", "data_asof ·",
        "外部证据审计（只读）", "bundle digest",
    ):
        assert copy in block
    assert "['news', 'orderbook', 'ticks', 'auction']" in PANELS

    # 0 是合法诊断值；不得用 truthiness 把“异常 0 根”显示成未知。
    assert "value !== null && value !== undefined && value !== ''" in PANELS
    assert "lzRecordHasValue(qFlagged) ? qFlagged : '—'" in block
    assert "slice(0, 12)" in block
    assert "lzRecordShort(dataRefRaw" in block

    # 展示组件只读；不得从历史卡触发研判、网络请求或交易动作。
    assert "fetch(" not in block
    assert "lzSeatDecide" not in block
    assert "onClick" not in block
    assert PANELS.count("<SeatRecordDiagnostics row={r} />") >= 2


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_external_evidence_audit_summary_is_bounded_and_never_opens_raw_items() -> None:
    helper_js = PANELS[
        PANELS.index("function lzDeepText"):
        PANELS.index("// 历史记录的只读诊断面")
    ]
    script = helper_js + r"""
const summary = lzRecordExternalEvidenceSummary({
  external_evidence: {
    status: 'available', asof: '2026-08-31T14:47:05+08:00',
    digest: '1234567890abcdef1234567890abcdef',
    prompt: 'PROMPT-MUST-NOT-LEAK', items: [{title: 'ITEM-MUST-NOT-LEAK'}],
    sections: {
      news: {status: 'available', item_count: 3, source: 'pit_news', items: ['SECRET-NEWS']},
      orderbook: {status: 'empty', item_count: 0, source: ''},
      ticks: {status: 'unavailable', source: 'tencent_ticks', error_codes: ['timeout']},
      auction: 'malformed-section-must-not-be-inferred',
      microstructure: {items: ['SECRET-MICRO']},
    },
  },
  evidence_diagnostics: {external_evidence: {
    mode: 'live', anchor_update: 'advanced_verified_capture', ignored_raw: 'SECRET-DIAG',
  }},
});
process.stdout.write(JSON.stringify({summary, legacy: lzRecordExternalEvidenceSummary({})}));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", script], check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    result = json.loads(completed.stdout)
    assert result["legacy"] is None
    assert result["summary"] == {
        "status": "available",
        "mode": "live",
        "asof": "2026-08-31T14:47:05+08:00",
        "digest": "1234567890ab…",
        "digestRaw": "1234567890abcdef1234567890abcdef",
        "anchorUpdate": "advanced_verified_capture",
        "sections": [
            {"key": "news", "status": "available", "itemCount": "3", "source": "pit_news"},
            {"key": "orderbook", "status": "empty", "itemCount": "0", "source": "—"},
            {"key": "ticks", "status": "unavailable", "itemCount": "—", "source": "tencent_ticks"},
            {"key": "auction", "status": "—", "itemCount": "—", "source": "—"},
        ],
    }
    serialized = json.dumps(result["summary"], ensure_ascii=False)
    for secret in ("PROMPT-MUST-NOT-LEAK", "ITEM-MUST-NOT-LEAK", "SECRET-NEWS",
                   "SECRET-MICRO", "SECRET-DIAG", "malformed-section"):
        assert secret not in serialized

    helper_block = _between(
        PANELS, "function lzRecordExternalEvidenceSummary", "// 历史记录的只读诊断面",
    )
    assert ".items" not in helper_block
    assert ".prompt" not in helper_block
    assert "fetch(" not in helper_block
    assert "lzSeatDecide" not in helper_block


def test_today_manual_judge_sends_only_the_quote_allowlist() -> None:
    assert "const JUDGE_QUOTE_FIELD_ALIASES = [" in PANELS
    for mapping in (
        "['price', ['price']]", "['open', ['open']]", "['high', ['high']]",
        "['low', ['low']]", "['asof', ['asof']]", "['volume', ['volume']]",
        "['amount', ['amount']]", "['turnover_rate', ['turnover_rate', 'turnoverRate']]",
        "['vol_ratio', ['vol_ratio', 'volRatio']]", "['fresh', ['fresh']]",
        "['source', ['source']]",
    ):
        assert mapping in PANELS
    helper = _between(PANELS, "function lzJudgeQuoteEvidence", "function _judgeNow")
    assert "JUDGE_QUOTE_FIELD_ALIASES.forEach" in helper
    assert "Object.prototype.hasOwnProperty.call(quote, key)" in helper
    assert "quote.fresh !== true || !quote.asof || !quote.source" in helper

    judge = _between(PANELS, "function JudgeCard(", "// ───────── 统一记录")
    assert "const liveQuote = lzJudgeQuoteEvidence(quote);" in judge
    assert "...(liveQuote ? { quote: liveQuote } : {})" in judge
    assert "quote={quote}" in APP

    # 旧 DecisionCard 承担回放详情，其 payload 不得被顺手接入当前页面 quote。
    replay = _between(PANELS, "function DecisionCard(", "// ───────── 研判卡")
    assert "quote:" not in replay


def test_intraday_manual_anchor_keeps_seconds_for_quote_pit_ordering() -> None:
    helper = _between(PANELS, "function _judgeNow", "function JudgeCard")
    assert "p(d.getSeconds())" in helper
    assert re.search(r"p\(d\.getMinutes\(\)\)\s*\+\s*':'\s*\+\s*p\(d\.getSeconds\(\)\)", helper)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_history_single_decision_never_sends_page_regime_but_live_keeps_it() -> None:
    helper = _between(
        PANELS, "function lzDecisionRegimeForPayload", "function DecisionCard(",
    )
    script = "function lzDecisionRegimeForPayload" + helper + r"""
process.stdout.write(JSON.stringify({
  replayCurrent: lzDecisionRegimeForPayload(true, 'CURRENT-PAGE-REGIME'),
  replayExample: lzDecisionRegimeForPayload(true, '示例·震荡'),
  liveCurrent: lzDecisionRegimeForPayload(false, '真·当前修复'),
  liveMissing: lzDecisionRegimeForPayload(false, undefined),
}));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", script], check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    assert json.loads(completed.stdout) == {
        "replayCurrent": None,
        "replayExample": None,
        "liveCurrent": "真·当前修复",
        "liveMissing": None,
    }

    replay = _between(PANELS, "function DecisionCard(", "// ───────── 研判卡")
    assert "history_replay: mode === 'backtest'" in replay
    assert "regime: lzDecisionRegimeForPayload(mode === 'backtest', regimeNow)" in replay
    assert "regime: regimeNow" not in replay

    # 「今日」页实时手动研判原通路不受回放断点影响。
    today = _between(PANELS, "function JudgeCard(", "// ───────── 统一记录")
    assert "regime: regime || null" in today


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_diagnostic_objects_and_quote_aliases_are_visible_and_bounded() -> None:
    """Exercise the actual dependency-free helpers, not only their source spelling."""
    diag_js = PANELS[PANELS.index("function lzDeepText"):PANELS.index("// 历史记录的只读诊断面")]
    quote_js = PANELS[PANELS.index("const JUDGE_QUOTE_FIELD_ALIASES"):PANELS.index("function JudgeCard")]
    script = diag_js + "\n" + quote_js + r"""
class FixedDate extends Date {
  constructor(...args) {
    if (args.length) super(...args);
    else super(2026, 7, 31, 14, 47, 5);
  }
}
globalThis.Date = FixedDate;
const factor = lzRecordDiagnosticSummary({
  status: 'partial', error_type: 'KeyError', missing_fields: ['turnover_20'],
  derived_fields: ['amount'], ignored_raw: 'X'.repeat(1000),
}, 'factor');
const ref = lzRecordDiagnosticSummary({
  status: 'resolved_partial', requested: ['A', 'B'], resolved: ['A'],
  unresolved: ['B'], errors: [{error_kind: 'LookupError'}], ignored_raw: 'Y'.repeat(1000),
}, 'ref');
const quote = lzJudgeQuoteEvidence({
  price: 364.71, open: 366.6, high: 367.33, low: 357.3,
      asof: '20260831144705', volume: 851300, amount: 3081234.5,
      turnoverRate: 0.55, volRatio: 0.89, fresh: true, source: 'tencent_realtime_quote',
  transportSecret: 'must-not-pass',
});
process.stdout.write(JSON.stringify({
  factor, ref, quote, intraday: _judgeNow('5min'), daily: _judgeNow('day'),
}));
"""
    completed = subprocess.run(
        [shutil.which("node") or "node", "-e", script], check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    result = json.loads(completed.stdout)
    assert result["factor"] == "status partial · error_type KeyError · missing 1 · derived 1"
    assert result["ref"] == (
        "status resolved_partial · requested 2 · resolved 1 · unresolved 1 · errors 1"
    )
    assert "X" not in result["factor"] and "Y" not in result["ref"]
    assert result["quote"] == {
        "price": 364.71, "open": 366.6, "high": 367.33, "low": 357.3,
        "asof": "20260831144705", "volume": 851300, "amount": 3081234.5,
        "turnover_rate": 0.55, "vol_ratio": 0.89, "fresh": True,
        "source": "tencent_realtime_quote",
    }
    assert "transportSecret" not in result["quote"]
    assert result["intraday"] == "2026-08-31T14:47:05"
    assert result["daily"] == "2026-08-31"


def test_record_diagnostics_assets_share_a_fresh_cache_generation() -> None:
    assert HTML.count("20260831evidence2") == 3
    assert "luozi-panels.jsx?v=20260831evidence2" in HTML
    assert "luozi-app.jsx?v=20260831evidence2" in HTML
