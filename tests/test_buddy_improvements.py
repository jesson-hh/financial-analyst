"""Tests for v1.7.5 buddy improvements:
  - news staleness warning
  - /mode + /model persistence
  - LLM token/cost accounting
  - fund-flow cross-day diff
  - stock_brief unified tool
"""
from __future__ import annotations
import datetime as dt
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ===== Task #1: news staleness ==============================================

from financial_analyst.buddy.tools import _news_staleness_note


def test_staleness_none_when_fresh():
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [{"ts": now}]
    assert _news_staleness_note(rows) is None


def test_staleness_warns_when_old():
    old = (dt.datetime.now() - dt.timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [{"ts": old}]
    note = _news_staleness_note(rows)
    assert note is not None
    assert "数据偏旧" in note


def test_staleness_uses_freshest_row():
    old = (dt.datetime.now() - dt.timedelta(hours=40)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [{"ts": old}, {"ts": fresh}]
    # freshest is now → no warning
    assert _news_staleness_note(rows) is None


def test_staleness_handles_missing_or_bad_ts():
    assert _news_staleness_note([{"ts": ""}]) is None
    assert _news_staleness_note([{}]) is None
    assert _news_staleness_note([{"ts": "garbage"}]) is None


def test_staleness_handles_date_only_ts():
    old_date = (dt.datetime.now() - dt.timedelta(days=3)).strftime("%Y-%m-%d")
    note = _news_staleness_note([{"ts": old_date}])
    assert note is not None


# ===== Task #2: prefs persistence ===========================================

from financial_analyst.buddy.app import BuddyApp


def test_mode_change_persists_and_reloads():
    """Changing mode writes buddy.yaml; a fresh app reads it back."""
    app = BuddyApp()
    assert app.permission_mode == "default"
    app._handle_slash("/mode safe")
    assert app.permission_mode == "safe"
    # New instance should restore safe
    app2 = BuddyApp()
    assert app2.permission_mode == "safe"


def test_model_change_persists_and_reloads():
    app = BuddyApp()
    app._handle_slash("/model anthropic/claude-opus-4-7")
    assert app.model == "claude-opus-4-7"
    app2 = BuddyApp()
    assert app2.provider == "anthropic"
    assert app2.model == "claude-opus-4-7"
    assert app2.agent._client.model == "claude-opus-4-7"


def test_prefs_missing_file_uses_defaults():
    """No buddy.yaml → defaults stand, no crash."""
    app = BuddyApp()
    assert app.permission_mode == "default"


def test_prefs_ignores_invalid_model():
    """A persisted model no longer in config is ignored (defaults stand)."""
    import yaml
    prefs = BuddyApp._prefs_path()
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text(yaml.safe_dump({
        "permission_mode": "auto",
        "provider": "qwen",
        "model": "nonexistent-model-xyz",
    }), encoding="utf-8")
    app = BuddyApp()
    # mode restored
    assert app.permission_mode == "auto"
    # invalid model NOT applied — stays at default
    assert app.model != "nonexistent-model-xyz"


# ===== Task #4: token accounting ============================================

import asyncio
from financial_analyst.llm.client import LLMClient


def _client():
    # v1.9.6: anthropic 是当前仅存的 litellm fallback provider, 这俩测试 mock
    # acompletion 函数. qwen/deepseek/openai/openrouter 都改走 AsyncOpenAI
    # 直连了 (绕 litellm), 用 qwen mock acompletion 不生效.
    return LLMClient(provider="anthropic", model="claude-opus-4-7", config={"providers": {}})


@pytest.mark.asyncio
async def test_chat_accumulates_token_usage():
    client = _client()
    fake_resp = {"choices": [{"message": {"content": "hi"}}],
                 "usage": {"prompt_tokens": 100, "completion_tokens": 30}}

    async def fake_acompletion(**kw):
        return fake_resp
    with patch("financial_analyst.llm.client.acompletion", side_effect=fake_acompletion):
        await client.chat(messages=[{"role": "user", "content": "x"}])
        await client.chat(messages=[{"role": "user", "content": "y"}])
    assert client.total_prompt_tokens == 200
    assert client.total_completion_tokens == 60
    assert client.total_tokens == 260
    assert client.n_calls == 2


def test_with_overrides_carries_token_counts():
    client = _client()
    client.total_prompt_tokens = 500
    client.total_completion_tokens = 120
    client.n_calls = 3
    switched = client.with_overrides(provider="anthropic", model="claude-opus-4-7")
    assert switched.total_prompt_tokens == 500
    assert switched.total_completion_tokens == 120
    assert switched.n_calls == 3


@pytest.mark.asyncio
async def test_chat_survives_missing_usage():
    """Some providers omit usage; chat shouldn't crash."""
    client = _client()

    async def fake_acompletion(**kw):
        return {"choices": [{"message": {"content": "hi"}}]}  # no usage
    with patch("financial_analyst.llm.client.acompletion", side_effect=fake_acompletion):
        await client.chat(messages=[{"role": "user", "content": "x"}])
    assert client.n_calls == 0  # nothing counted, no crash


def test_status_line_shows_tokens_after_calls():
    app = BuddyApp()
    app.agent._client.total_prompt_tokens = 1500
    app.agent._client.total_completion_tokens = 400
    app.agent._client.n_calls = 2
    text = app._get_status_ansi().value
    assert "tok" in text
    assert "calls" in text


# ===== Task #5: fund-flow cross-day diff ====================================

from financial_analyst.buddy.tools import _parse_cn_amount, _fmt_yi
from financial_analyst.data.news_db import NewsDB


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as d:
        db = NewsDB(path=Path(d) / "t.sqlite")
        yield db
        db.close()


def test_parse_cn_amount():
    assert _parse_cn_amount("1.69亿") == pytest.approx(1.69e8)
    assert _parse_cn_amount("-3254.51万") == pytest.approx(-3254.51e4)
    assert _parse_cn_amount("100") == 100.0
    assert _parse_cn_amount("") is None
    assert _parse_cn_amount("--") is None
    assert _parse_cn_amount(None) is None
    assert _parse_cn_amount("garbage") is None


def test_fmt_yi():
    assert _fmt_yi(1.69e8) == "1.69亿"
    assert "万" in _fmt_yi(-3254e4)
    assert _fmt_yi(None) == "?"


def test_query_history_returns_snapshots_newest_first(tmp_db):
    tmp_db.upsert_ths_fund_flow([
        {"target": "gegu", "code": "600519", "name": "茅台", "main_net": "1.5亿",
         "snapshot_ts": "2026-05-20 09:00:00"},
    ])
    tmp_db.upsert_ths_fund_flow([
        {"target": "gegu", "code": "600519", "name": "茅台", "main_net": "2.1亿",
         "snapshot_ts": "2026-05-21 09:00:00"},
    ])
    hist = tmp_db.query_ths_fund_flow_history("600519", target="gegu")
    assert len(hist) == 2
    assert hist[0]["snapshot_ts"] == "2026-05-21 09:00:00"  # newest first


def test_fund_flow_change_tool_computes_diff(tmp_db, monkeypatch):
    class _NoClose:
        def __init__(self, r): self._r = r
        def __getattr__(self, n): return getattr(self._r, n)
        def close(self): pass
    tmp_db.upsert_ths_fund_flow([
        {"target": "gegu", "code": "600519", "name": "茅台", "main_net": "1.5亿",
         "change_pct": "1.2%", "snapshot_ts": "2026-05-20 09:00:00"},
    ])
    tmp_db.upsert_ths_fund_flow([
        {"target": "gegu", "code": "600519", "name": "茅台", "main_net": "2.1亿",
         "change_pct": "2.0%", "snapshot_ts": "2026-05-21 09:00:00"},
    ])
    monkeypatch.setattr("financial_analyst.data.news_db.NewsDB",
                        lambda *a, **kw: _NoClose(tmp_db))
    from financial_analyst.buddy.tools import _tool_fund_flow_change
    result = _tool_fund_flow_change("600519", target="gegu")
    assert not result.is_error
    # 1.5亿 → 2.1亿 = +0.6亿 increase
    assert "↑增" in result.content
    assert "茅台" in result.content


def test_fund_flow_change_tool_single_snapshot(tmp_db, monkeypatch):
    class _NoClose:
        def __init__(self, r): self._r = r
        def __getattr__(self, n): return getattr(self._r, n)
        def close(self): pass
    tmp_db.upsert_ths_fund_flow([
        {"target": "gegu", "code": "600519", "name": "茅台", "main_net": "1.5亿",
         "snapshot_ts": "2026-05-21 09:00:00"},
    ])
    monkeypatch.setattr("financial_analyst.data.news_db.NewsDB",
                        lambda *a, **kw: _NoClose(tmp_db))
    from financial_analyst.buddy.tools import _tool_fund_flow_change
    result = _tool_fund_flow_change("600519")
    assert "仅 1 个快照" in result.content


def test_fund_flow_change_tool_no_data(tmp_db, monkeypatch):
    class _NoClose:
        def __init__(self, r): self._r = r
        def __getattr__(self, n): return getattr(self._r, n)
        def close(self): pass
    monkeypatch.setattr("financial_analyst.data.news_db.NewsDB",
                        lambda *a, **kw: _NoClose(tmp_db))
    from financial_analyst.buddy.tools import _tool_fund_flow_change
    result = _tool_fund_flow_change("999999")
    assert "无" in result.content


def test_fund_flow_change_registered():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    assert "fund_flow_change" in {t.name for t in TOOL_REGISTRY}


# ===== Task #6: stock_brief unified tool ====================================


def test_stock_brief_registered():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    assert "stock_brief" in {t.name for t in TOOL_REGISTRY}


def test_stock_brief_aggregates_sections(monkeypatch):
    """stock_brief calls the underlying tool helpers and stitches their
    output. We stub each helper to confirm aggregation + resilience.

    v1.9.4+ 注: stock_brief 优先调 TencentQuoteCollector (实时), 失败才 fallback
    到 _tool_ask_quote (EOD). 测试必须 mock 两条路径都失败 + 让 EOD 走 mock.
    """
    from financial_analyst.buddy import tools
    from financial_analyst.buddy.tools import ToolResult

    # 强制 Tencent 实时路径不可用, 走 EOD fallback (_tool_ask_quote)
    class _FailTencent:
        def quote(self, code): return None    # 返 None → 走 fallback
    monkeypatch.setattr(
        "financial_analyst.data.collectors.tencent_quote.TencentQuoteCollector",
        lambda: _FailTencent(),
    )
    monkeypatch.setattr(tools, "_tool_ask_quote",
                        lambda code: ToolResult(f"{code}: close=1280"))
    monkeypatch.setattr(tools, "_tool_industry_show",
                        lambda code: ToolResult(f"{code}: 白酒"))
    monkeypatch.setattr(tools, "_tool_chain_for",
                        lambda code: ToolResult("primary product: 白酒"))
    monkeypatch.setattr(tools, "_tool_stocks_show",
                        lambda code, tail=600: ToolResult("上次评级 4 星"))

    class _FakeDB:
        def query_news(self, **kw): return [
            {"ts": "2026-05-21 09:00:00", "title": "茅台提价"}]
        def query_social_posts(self, *a, **kw): return [
            {"author": "大V", "content": "看好"}]
        def query_ths_fund_flow_history(self, *a, **kw): return [
            {"main_net": "1.5亿", "change_pct": "1.2%", "snapshot_ts": "2026-05-21 09:00"}]
        def close(self): pass
    monkeypatch.setattr("financial_analyst.data.news_db.NewsDB", lambda *a, **kw: _FakeDB())

    result = tools._tool_stock_brief("SH600519")
    c = result.content
    assert "行情" in c and "close=1280" in c
    assert "白酒" in c
    assert "产业链" in c
    assert "茅台提价" in c
    assert "看好" in c
    assert "1.5亿" in c
    assert "上次研报" in c


def test_stock_brief_resilient_to_section_failure(monkeypatch):
    """If quote fails, the brief still returns other sections.

    v1.9.4+: 要让 Tencent 路径也挂, 才会走 _tool_ask_quote fallback.
    """
    from financial_analyst.buddy import tools
    from financial_analyst.buddy.tools import ToolResult

    # Tencent 抛错 (而非返 None) → 进 except → 输出 "取价失败"
    class _BoomTencent:
        def quote(self, code): raise RuntimeError("tencent down")
    monkeypatch.setattr(
        "financial_analyst.data.collectors.tencent_quote.TencentQuoteCollector",
        lambda: _BoomTencent(),
    )

    def boom(code): raise RuntimeError("loader down")
    monkeypatch.setattr(tools, "_tool_ask_quote", boom)
    monkeypatch.setattr(tools, "_tool_industry_show",
                        lambda code: ToolResult("白酒"))
    monkeypatch.setattr(tools, "_tool_chain_for",
                        lambda code: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(tools, "_tool_stocks_show",
                        lambda code, tail=600: ToolResult("No timeline file"))

    class _FakeDB:
        def query_news(self, **kw): return []
        def query_social_posts(self, *a, **kw): return []
        def query_ths_fund_flow_history(self, *a, **kw): return []
        def close(self): pass
    monkeypatch.setattr("financial_analyst.data.news_db.NewsDB", lambda *a, **kw: _FakeDB())

    result = tools._tool_stock_brief("SH600519")
    # quote failed but brief still produced + has industry
    assert "取价失败" in result.content
    assert "白酒" in result.content


# ===== Task #11: system prompt routing guidance =============================

from financial_analyst.buddy.agent import _build_system_prompt


def test_system_prompt_mentions_new_tools():
    """The behaviour guidance must route to the post-v1.5 tools, not just
    the original 13."""
    p = _build_system_prompt()
    for tool in ("stock_brief", "realtime_quote", "alert_add",
                 "ths_fund_flow", "fund_flow_change", "iwencai_search"):
        assert tool in p, f"system prompt missing routing for {tool}"


def test_system_prompt_has_routing_cheatsheet():
    p = _build_system_prompt()
    assert "routing" in p.lower() or "cheat" in p.lower()
    # stock_brief explicitly preferred over manual chaining
    assert "stock_brief" in p
    assert "宽泛" in p or "broad" in p.lower()


def test_system_prompt_handles_staleness():
    p = _build_system_prompt()
    assert "数据偏旧" in p


def test_system_prompt_lists_all_tools():
    """tool_list still dynamically enumerates the full registry."""
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    p = _build_system_prompt()
    # every registered tool name should appear in the rendered prompt
    for t in TOOL_REGISTRY:
        assert t.name in p


# ===== v1.8.3: CJK-aware table alignment ====================================

from financial_analyst.buddy.tools import _disp_w, _pad


def test_disp_w_counts_cjk_as_two():
    assert _disp_w("茅台") == 4          # 2 hanzi × 2 cols
    assert _disp_w("ABC") == 3           # ascii = 1 col each
    assert _disp_w("茅台A") == 5         # 2+2+1
    assert _disp_w("") == 0
    assert _disp_w(123) == 3             # non-str coerced


def test_pad_aligns_to_display_width():
    # "茅台" is 4 display cols → pad to 8 means +4 spaces
    out = _pad("茅台", 8)
    assert _disp_w(out) == 8
    assert out == "茅台    "
    # ascii pads by char count == display width
    assert _pad("ABCDEF", 8) == "ABCDEF  "


def test_pad_no_truncate_when_too_long():
    # over-width input is left as-is (never cut)
    out = _pad("超长板块名称示例", 4)
    assert "超长板块名称示例" in out


def test_pad_columns_line_up_mixed():
    """A Chinese name column and an ascii code column padded to the same
    display width must produce equal-width cells."""
    cell_cn = _pad("贵州茅台", 12)
    cell_en = _pad("SH600519", 12)
    assert _disp_w(cell_cn) == _disp_w(cell_en) == 12
