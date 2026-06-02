"""Task 5 — WatchAgent single-stock advisor tests (LLM mocked).

`WatchAgent` mirrors `backtest.decision.DecisionAgent`'s client + cache pattern
but takes a single-stock `WatchContext` and returns a `WatchRec`.

Mock pattern is the same one `tests/test_backtest_decision.py` uses:
`client.chat` is an `AsyncMock` returning
`{"choices":[{"message":{"content": <json-str>}}]}` — the exact dict shape
`LLMClient.chat` produces via `response.model_dump()`.

pytest-asyncio runs in ``asyncio_mode = auto`` (see pyproject) so plain
``async def test_*`` functions are awaited automatically — no decorator needed.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

from financial_analyst.watch.agent import WatchAgent
from financial_analyst.watch.models import WatchContext, WatchItem, WatchRec


def _ctx(kind="breakout_high"):
    return WatchContext(
        code="SH600519",
        name="贵州茅台",
        now_ts="2026-06-02 10:05:00",
        trigger={"kind": kind, "detail": "末根突破前高 1.2%", "metric": 0.012},
        realtime={"price": 1750.0, "change_pct": 2.1, "vol_ratio": 1.8,
                  "high": 1760.0, "low": 1720.0},
        bars_5min=[
            {"datetime": "2026-06-02 09:35:00", "open": 1700.0, "high": 1710.0,
             "low": 1698.0, "close": 1705.0, "vol": 1200.0},
            {"datetime": "2026-06-02 10:05:00", "open": 1740.0, "high": 1760.0,
             "low": 1738.0, "close": 1755.0, "vol": 3400.0},
        ],
        factors_eod={"RSI": 62.0, "MACD": "金叉", "MA20": 1680.0},
        news_today=["公司拟提价 5%"],
        item=WatchItem(code="SH600519", avg_cost=1600.0, stop_loss=1650.0),
    )


def _chat_returning(payload_dict):
    """An AsyncMock chat returning the LLMClient.model_dump() dict shape."""
    return AsyncMock(return_value={
        "choices": [{"message": {"content": json.dumps(payload_dict)}}]
    })


_GOOD = {"action": "add", "target_price": 1800.0, "stop_loss": 1650.0,
         "reason": "放量突破前高确认, 量比 1.8 配合", "confidence": 0.6}


# ==========================================================================
# happy path — mock client returns fixed JSON → WatchRec parsed correctly
# ==========================================================================
async def test_decide_one_parses_rec():
    client = AsyncMock()
    client.chat = _chat_returning(_GOOD)
    agent = WatchAgent(client=client, knowledge="")

    ctx = _ctx(kind="breakout_high")
    rec = await agent.decide_one(ctx)

    assert isinstance(rec, WatchRec)
    assert rec.action == "add"
    assert rec.code == "SH600519"
    # trigger_kind is taken from the context, NOT from the LLM payload
    assert rec.trigger_kind == "breakout_high"
    assert rec.target_price == 1800.0
    assert rec.stop_loss == 1650.0
    assert rec.confidence == 0.6
    assert rec.reason  # non-empty, threaded from LLM
    assert rec.ts == ctx.now_ts
    assert rec.error == ""
    # the LLM was actually consulted exactly once
    assert client.chat.call_count == 1
    assert agent.n_calls == 1


# ==========================================================================
# error path — client raises → WatchRec(action="hold", error!=""), no crash
# ==========================================================================
async def test_decide_one_llm_error_falls_back_to_hold():
    client = AsyncMock()
    client.chat = AsyncMock(side_effect=RuntimeError("boom: upstream 502"))
    agent = WatchAgent(client=client, knowledge="")

    ctx = _ctx(kind="stop_break")
    rec = await agent.decide_one(ctx)

    assert isinstance(rec, WatchRec)
    assert rec.action == "hold"          # safe default
    assert rec.error != ""               # error captured, not raised
    assert "boom" in rec.error
    assert rec.code == "SH600519"
    assert rec.trigger_kind == "stop_break"
    assert rec.ts == ctx.now_ts


# ==========================================================================
# malformed JSON → also hold (never raises on bad model output)
# ==========================================================================
async def test_decide_one_bad_json_falls_back_to_hold():
    client = AsyncMock()
    client.chat = AsyncMock(return_value={
        "choices": [{"message": {"content": "this is not json"}}]
    })
    agent = WatchAgent(client=client, knowledge="")

    rec = await agent.decide_one(_ctx())
    assert rec.action == "hold"
    assert rec.error != ""
    assert rec.trigger_kind == "breakout_high"


# ==========================================================================
# bad/unknown action from LLM → downgraded to hold (WatchRec validation safe)
# ==========================================================================
async def test_decide_one_unknown_action_downgraded_to_hold():
    client = AsyncMock()
    client.chat = _chat_returning(
        {"action": "moon", "target_price": 1, "stop_loss": 1,
         "reason": "r", "confidence": 0.9})
    agent = WatchAgent(client=client, knowledge="")

    rec = await agent.decide_one(_ctx())
    # an out-of-vocab action must not blow up WatchRec.__post_init__
    assert rec.action == "hold"
    assert isinstance(rec, WatchRec)


# ==========================================================================
# _build_messages — single-stock advisor prompt shape (pure, no LLM)
# ==========================================================================
def test_build_messages_single_stock_shape():
    agent = WatchAgent(client=AsyncMock(), knowledge="")
    msgs = agent._build_messages(_ctx(kind="volume_surge"))
    assert isinstance(msgs, list) and len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    sys = msgs[0]["content"]
    user = msgs[1]["content"]
    # advisor口吻 + JSON 契约 in system
    for token in ("action", "target_price", "stop_loss", "reason", "confidence"):
        assert token in sys
    # single-stock context is rendered into the user message
    assert "SH600519" in user
    assert "贵州茅台" in user
    assert "volume_surge" in user        # trigger kind is threaded into prompt
    # advisor is single-stock: NO portfolio fields like cash / nav / 候选池
    assert "候选池" not in user
    assert "现金" not in user


# ==========================================================================
# A — 知识注入: 项目验证守则进 system prompt
# ==========================================================================
def test_build_messages_injects_knowledge():
    from financial_analyst.watch.agent import build_messages
    msgs = build_messages(_ctx(), knowledge="反转是核心; super_distr 减仓")
    sys = msgs[0]["content"]
    assert "必守策略知识" in sys
    assert "反转是核心" in sys
    assert "super_distr" in sys
    # knowledge goes into SYSTEM, never the user message
    assert "必守策略知识" not in msgs[1]["content"]


def test_build_messages_empty_knowledge_is_generic():
    from financial_analyst.watch.agent import build_messages, _SYSTEM
    msgs = build_messages(_ctx(), knowledge="")
    assert msgs[0]["content"] == _SYSTEM        # byte-identical generic prompt


def test_agent_knowledge_disabled_when_empty():
    agent = WatchAgent(client=AsyncMock(), knowledge="")
    sys = agent._build_messages(_ctx())[0]["content"]
    assert "必守策略知识" not in sys


def test_agent_injects_explicit_knowledge():
    agent = WatchAgent(client=AsyncMock(), knowledge="游资博弈票 模型失效")
    sys = agent._build_messages(_ctx())[0]["content"]
    assert "游资博弈票" in sys


def test_agent_lazy_loads_bundled_knowledge():
    """knowledge=None → 懒加载 fa memory (bundled seed 必有内容) → 注入成功。"""
    from financial_analyst.watch.knowledge import _reset_cache_for_tests
    _reset_cache_for_tests()
    agent = WatchAgent(client=AsyncMock())      # None → auto-load
    sys = agent._build_messages(_ctx())[0]["content"]
    assert "必守策略知识" in sys
    assert ("反转" in sys or "super_distr" in sys)
    _reset_cache_for_tests()


# ==========================================================================
# ① 回灌 advisor — 把命中率统计 (advisor 自己的历史战绩) 注入 prompt
# ==========================================================================
def test_build_messages_injects_hitrate_context():
    from financial_analyst.watch.agent import build_messages
    msgs = build_messages(_ctx(), knowledge="",
                          hitrate_context="## 你的历史推荐战绩\n- 全局: 命中率 58%")
    sys = msgs[0]["content"]
    assert "历史推荐战绩" in sys
    assert "58%" in sys
    assert "历史推荐战绩" not in msgs[1]["content"]   # goes to SYSTEM, not user


def test_agent_lazy_loads_hitrate(monkeypatch):
    """hitrate=None → 懒加载 outcome log → compute_hitrate → 注入本触发+全局战绩。"""
    import pandas as pd
    rows = pd.DataFrame([
        {"ts": "2026-05-20 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
         "action": "buy", "verdict": "wrong", "return_t1": -0.01, "return_t5": -0.03},
        {"ts": "2026-05-21 10:30:00", "code": "SH600519", "trigger_kind": "breakout_high",
         "action": "buy", "verdict": "correct", "return_t1": 0.02, "return_t5": 0.05},
    ])
    monkeypatch.setattr("financial_analyst.watch.outcome.load_outcomes",
                        lambda *a, **k: rows)
    agent = WatchAgent(client=AsyncMock(), knowledge="")     # hitrate=None → lazy-load
    sys = agent._build_messages(_ctx(kind="breakout_high"))[0]["content"]
    assert "历史推荐战绩" in sys
    assert "本触发 breakout_high" in sys


def test_agent_hitrate_disabled_when_empty_dict():
    agent = WatchAgent(client=AsyncMock(), knowledge="", hitrate={})
    sys = agent._build_messages(_ctx())[0]["content"]
    assert "历史推荐战绩" not in sys
