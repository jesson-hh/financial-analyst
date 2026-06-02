"""P2 丁 + 戊 — DecisionAgent cache + JSON-fallback tests (LLM mocked).

* 丁 cache: first ``decide`` calls ``client.chat`` once and writes cache; the
  same input hits cache (call_count stays 1, n_calls not incremented); changing
  candidates changes the prompt text → key changes → chat called again.
* 戊 fallback: non-JSON content → conservative empty Decision + warning, never
  raises; an out-of-candidate code (LLM hallucination) is dropped with a warning.

Mock pattern is copied from ``tests/integration/test_memory_mode.py:132-154``:
``client.chat`` is an ``AsyncMock`` returning
``{"choices":[{"message":{"content": <json-str>}}]}`` — the exact shape
``LLMClient.chat`` produces via ``response.model_dump()``.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from financial_analyst.backtest.decision import (
    Decision,
    DecisionAgent,
    DecisionCache,
    DecisionInput,
)
from financial_analyst.backtest.pit_reader import VisibleInfo


def _visible(date="2026-03-16"):
    return VisibleInfo(
        date=date, as_of="09:25:00", boundary_ts=f"{date}T09:25:00",
        news=[], events=[], policy=[],
        market_eod_prev={"prev_trade_date": "2026-03-13", "pct_up_5d": None,
                         "median_ret_5d": None, "median_ret_20d": None},
    )


def _inp(candidates):
    return DecisionInput(
        date="2026-03-16", as_of="09:25", visible=_visible(),
        candidates=list(candidates),
        rev20_rank={c: 0.5 for c in candidates},
        holdings={}, cash=1_000_000.0, nav=1_000_000.0,
    )


def _chat_returning(payload_dict):
    """An AsyncMock chat that returns the LLMClient.model_dump() dict shape."""
    return AsyncMock(return_value={
        "choices": [{"message": {"content": json.dumps(payload_dict)}}]
    })


_GOOD = {"market_view": "neutral", "decisions": [], "warnings": []}


# ==========================================================================
# 丁 — cache hit avoids a second LLM call; changing input re-runs
# ==========================================================================
async def test_cache_hit_no_llm(tmp_path):
    client = AsyncMock()
    client.chat = _chat_returning(_GOOD)
    cache = DecisionCache(tmp_path / "dc")
    agent = DecisionAgent(client=client, cache=cache)

    inp = _inp(["SH600519", "SZ000858"])
    d1 = await agent.decide(inp)
    assert isinstance(d1, Decision)
    assert client.chat.call_count == 1
    assert agent.n_calls == 1

    # identical input → cache hit, no new chat, n_calls unchanged
    d2 = await agent.decide(_inp(["SH600519", "SZ000858"]))
    assert client.chat.call_count == 1
    assert agent.n_calls == 1
    assert d2.market_view == d1.market_view

    # changing candidates changes the prompt text → key changes → re-run
    d3 = await agent.decide(_inp(["SH600519"]))
    assert client.chat.call_count == 2
    assert agent.n_calls == 2
    assert isinstance(d3, Decision)


# ==========================================================================
# 丁b — cache persists across agent instances sharing the same dir
# ==========================================================================
async def test_cache_shared_across_agents(tmp_path):
    cache_dir = tmp_path / "shared"
    c1 = AsyncMock(); c1.chat = _chat_returning(_GOOD)
    a1 = DecisionAgent(client=c1, cache=DecisionCache(cache_dir))
    await a1.decide(_inp(["SH600519"]))
    assert c1.chat.call_count == 1

    # a fresh agent + fresh cache object on the same dir → hit, no chat
    c2 = AsyncMock(); c2.chat = _chat_returning(_GOOD)
    a2 = DecisionAgent(client=c2, cache=DecisionCache(cache_dir))
    await a2.decide(_inp(["SH600519"]))
    assert c2.chat.call_count == 0
    assert a2.n_calls == 0


# ==========================================================================
# 戊 — malformed JSON → conservative empty Decision + warning, no raise
# ==========================================================================
async def test_bad_json_fallback(tmp_path):
    client = AsyncMock()
    client.chat = AsyncMock(return_value={
        "choices": [{"message": {"content": "not json at all"}}]
    })
    agent = DecisionAgent(client=client, cache=DecisionCache(tmp_path / "dc"))
    d = await agent.decide(_inp(["SH600519"]))
    assert isinstance(d, Decision)
    assert d.decisions == []
    assert "decision_parse_failed" in d.warnings
    # the raw error payload is preserved for audit
    assert d.raw.get("_error") == "json"


# ==========================================================================
# 戊b — fenced ```json ... ``` is unwrapped before parsing
# ==========================================================================
async def test_fenced_json_unwrapped(tmp_path):
    fenced = "```json\n" + json.dumps({
        "market_view": "ok",
        "decisions": [{"code": "SH600519", "action": "buy",
                       "target_price": 1800, "stop_loss": 1600,
                       "weight_pct": 20, "reason": "r"}],
        "warnings": [],
    }) + "\n```"
    client = AsyncMock()
    client.chat = AsyncMock(return_value={
        "choices": [{"message": {"content": fenced}}]})
    agent = DecisionAgent(client=client, cache=DecisionCache(tmp_path / "dc"))
    d = await agent.decide(_inp(["SH600519"]))
    assert d.market_view == "ok"
    assert len(d.decisions) == 1
    assert d.decisions[0].code == "SH600519"
    assert d.decisions[0].action == "buy"


# ==========================================================================
# 戊c — out-of-candidate code is dropped (hallucination guard) + warning
# ==========================================================================
async def test_out_of_candidate_leg_dropped(tmp_path):
    payload = {
        "market_view": "v",
        "decisions": [
            {"code": "SH600519", "action": "buy", "target_price": 1,
             "stop_loss": 1, "weight_pct": 10, "reason": "in"},
            {"code": "SZ123456", "action": "buy", "target_price": 1,
             "stop_loss": 1, "weight_pct": 10, "reason": "hallucinated"},
        ],
        "warnings": [],
    }
    client = AsyncMock()
    client.chat = _chat_returning(payload)
    agent = DecisionAgent(client=client, cache=DecisionCache(tmp_path / "dc"))
    d = await agent.decide(_inp(["SH600519"]))  # only SH600519 is a candidate
    codes = [leg.code for leg in d.decisions]
    assert codes == ["SH600519"]
    assert any("SZ123456" in w or "out_of_candidate" in w for w in d.warnings)


# ==========================================================================
# 戊d — unknown action downgraded to hold; bad numerics → 0.0
# ==========================================================================
async def test_unknown_action_and_bad_numbers(tmp_path):
    payload = {
        "market_view": "v",
        "decisions": [
            {"code": "SH600519", "action": "YOLO", "target_price": "n/a",
             "stop_loss": None, "weight_pct": "20%", "reason": "r"},
        ],
        "warnings": [],
    }
    client = AsyncMock()
    client.chat = _chat_returning(payload)
    agent = DecisionAgent(client=client, cache=DecisionCache(tmp_path / "dc"))
    d = await agent.decide(_inp(["SH600519"]))
    assert len(d.decisions) == 1
    leg = d.decisions[0]
    assert leg.action == "hold"          # YOLO → hold
    assert leg.target_price == 0.0       # "n/a" → 0.0
    assert leg.stop_loss == 0.0          # None → 0.0
    assert leg.weight_pct == 0.0         # "20%" → 0.0
