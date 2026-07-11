# -*- coding: utf-8 -*-
"""_call_llm_json 座席化:agent 参数透传 for_agent;座席超时 > 调用方 fallback。"""
import asyncio

from guanlan_v2.screen import llm as sl


class _FakeClient:
    def __init__(self, default_timeout=None):
        self.provider, self.model = "deepseek", "deepseek-reasoner"
        self.total_tokens, self.default_timeout = 7, default_timeout

    async def chat(self, messages, response_format=None, temperature=0.2):
        return {"choices": [{"message": {"content": '{"x": 1}'}}]}


def test_effective_timeout_prefers_seat():
    assert sl._effective_timeout(300.0, 45.0) == 305.0   # 座席超时 +5s 缓冲(让 SDK 先抛)
    assert sl._effective_timeout(None, 45.0) == 45.0     # 无座席预算=旧行为逐字节不变


def test_call_llm_json_passes_agent(monkeypatch):
    seen = {}

    def _fa(agent_name, config_path=None):
        seen["agent"] = agent_name
        return _FakeClient(default_timeout=300.0)

    import financial_analyst.llm.client as ec
    monkeypatch.setattr(ec.LLMClient, "for_agent", staticmethod(_fa))
    r = asyncio.run(sl._call_llm_json("s", "u", agent="rerank"))
    assert seen["agent"] == "rerank"
    assert r["ok"] is True and r["model"] == "deepseek/deepseek-reasoner"


def test_call_llm_json_default_agent_is_screen(monkeypatch):
    seen = {}

    def _fa(agent_name, config_path=None):
        seen["agent"] = agent_name
        return _FakeClient()

    import financial_analyst.llm.client as ec
    monkeypatch.setattr(ec.LLMClient, "for_agent", staticmethod(_fa))
    asyncio.run(sl._call_llm_json("s", "u"))
    assert seen["agent"] == "screen"
