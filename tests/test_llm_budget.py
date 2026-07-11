# -*- coding: utf-8 -*-
"""单元一 Task1:座席预算字段(max_tokens/timeout)解析 + chat() 透传。
红线:不带预算字段的座席 kwargs 不得出现 max_tokens/timeout 键(现有缝逐字节不变)。"""
import asyncio

import pytest

from financial_analyst.llm import client as C

CFG = {
    "default_provider": "deepseek", "default_model": "deepseek-chat",
    "providers": {"deepseek": {"api_key_env": "X_KEY", "base_url": "http://local",
                               "network_profile": "domestic",
                               "models": ["deepseek-chat", "deepseek-reasoner"]}},
    "agent_overrides": {
        "rerank": {"provider": "deepseek", "model": "deepseek-reasoner",
                   "max_tokens": 8192, "timeout": 300},
    },
}


class _FakeResp:
    def model_dump(self):
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}


class _FakeCompletions:
    def __init__(self, rec):
        self._rec = rec

    async def create(self, **kw):
        self._rec.append(kw)
        return _FakeResp()


class _FakeChat:
    def __init__(self, rec):
        self.completions = _FakeCompletions(rec)


class _FakeOpenAI:
    def __init__(self, rec):
        self.chat = _FakeChat(rec)


@pytest.fixture()
def fake_openai(monkeypatch):
    rec = []
    monkeypatch.setattr(C, "_get_openai_compat_client",
                        lambda *a, **k: _FakeOpenAI(rec))
    monkeypatch.setattr(C, "load_llm_config", lambda path=None: CFG)
    return rec


def test_for_agent_parses_budget_fields(fake_openai):
    c = C.LLMClient.for_agent("rerank")
    assert c.model == "deepseek-reasoner"
    assert c.default_max_tokens == 8192
    assert c.default_timeout == 300.0


def test_for_agent_without_budget_is_none(fake_openai):
    c = C.LLMClient.for_agent("screen")
    assert c.default_max_tokens is None and c.default_timeout is None


def test_chat_passes_seat_budget(fake_openai):
    c = C.LLMClient.for_agent("rerank")
    asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    kw = fake_openai[-1]
    assert kw["max_tokens"] == 8192 and kw["timeout"] == 300.0


def test_chat_without_budget_omits_keys(fake_openai):
    c = C.LLMClient.for_agent("screen")
    asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    kw = fake_openai[-1]
    assert "max_tokens" not in kw and "timeout" not in kw


def test_explicit_arg_beats_seat_default(fake_openai):
    c = C.LLMClient.for_agent("rerank")
    asyncio.run(c.chat([{"role": "user", "content": "hi"}], max_tokens=1024, timeout=60))
    kw = fake_openai[-1]
    assert kw["max_tokens"] == 1024 and kw["timeout"] == 60.0


def test_with_overrides_carries_budget(fake_openai):
    c = C.LLMClient.for_agent("rerank").with_overrides(model="deepseek-chat")
    assert c.default_max_tokens == 8192 and c.default_timeout == 300.0
