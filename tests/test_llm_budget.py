# -*- coding: utf-8 -*-
"""单元一 Task1:座席预算字段(max_tokens/timeout)解析 + chat() 透传。
红线:不带预算字段的座席 kwargs 不得出现 max_tokens/timeout 键(现有缝逐字节不变)。"""
import asyncio

import pytest

from financial_analyst.llm import client as C

CFG = {
    "default_provider": "deepseek", "default_model": "deepseek-v4-flash",
    "default_thinking": False,
    "providers": {"deepseek": {"api_key_env": "X_KEY", "base_url": "http://local",
                               "network_profile": "domestic",
                               "models": ["deepseek-v4-flash", "deepseek-v4-pro"]}},
    "agent_overrides": {
        "rerank": {"provider": "deepseek", "model": "deepseek-v4-flash",
                   "thinking": True, "reasoning_effort": "high",
                   "max_tokens": 8192, "timeout": 300},
        "buddy": {"provider": "deepseek", "model": "deepseek-v4-flash",
                  "thinking": False},
        "buddy-pro": {"provider": "deepseek", "model": "deepseek-v4-pro",
                      "thinking": True, "reasoning_effort": "max",
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
    assert c.model == "deepseek-v4-flash"
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


def test_explicit_retry_policy_disables_openai_sdk_hidden_retries(monkeypatch):
    requests = []
    options = []

    class _RetryAwareFakeOpenAI(_FakeOpenAI):
        def with_options(self, **kwargs):
            options.append(kwargs)
            return self

    client = _RetryAwareFakeOpenAI(requests)
    monkeypatch.setattr(C, "_get_openai_compat_client", lambda *a, **k: client)
    c = C.LLMClient(
        provider="deepseek", model="deepseek-v4-flash", config=CFG)

    dispatches = []
    asyncio.run(c.chat(
        [{"role": "user", "content": "hi"}], max_retries=0,
        on_dispatch=lambda: dispatches.append("http")))

    assert options == [{"max_retries": 0}]
    assert dispatches == ["http"]
    assert len(requests) == 1


def test_with_overrides_carries_budget(fake_openai):
    c = C.LLMClient.for_agent("rerank").with_overrides(model="deepseek-v4-pro")
    assert c.default_max_tokens == 8192 and c.default_timeout == 300.0
    assert c.default_thinking is True and c.default_reasoning_effort == "max"


def test_with_overrides_can_change_thinking_profile(fake_openai):
    c = C.LLMClient.for_agent("rerank").with_overrides(
        model="deepseek-v4-pro", thinking=True, reasoning_effort="max")
    assert c.default_thinking is True and c.default_reasoning_effort == "max"
    asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    kw = fake_openai[-1]
    assert kw["model"] == "deepseek-v4-pro"
    assert kw["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kw["reasoning_effort"] == "max"
    assert "temperature" not in kw


def test_raw_model_switch_from_pro_to_flash_clears_thinking_profile(fake_openai):
    pro = C.LLMClient.for_agent("buddy-pro")
    flash = pro.with_overrides(model="deepseek-v4-flash")
    assert flash.default_thinking is False
    assert flash.default_reasoning_effort is None
    assert flash.default_max_tokens is None and flash.default_timeout is None
    asyncio.run(flash.chat([{"role": "user", "content": "hi"}]))
    kw = fake_openai[-1]
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kw
    assert kw["temperature"] == 0.2


def test_raw_model_switch_from_flash_to_pro_applies_full_profile(fake_openai):
    flash = C.LLMClient.for_agent("buddy")
    pro = flash.with_overrides(model="deepseek-v4-pro")
    assert pro.default_thinking is True
    assert pro.default_reasoning_effort == "max"
    assert pro.default_max_tokens == 8192 and pro.default_timeout == 300.0


def test_standard_v4_flash_explicitly_disables_thinking(fake_openai):
    c = C.LLMClient.for_agent("buddy")
    asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    kw = fake_openai[-1]
    assert kw["model"] == "deepseek-v4-flash"
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kw
    assert kw["temperature"] == 0.2


def test_pro_v4_passes_enabled_max_thinking_without_temperature(fake_openai):
    c = C.LLMClient.for_agent("buddy-pro")
    assert c.default_thinking is True
    assert c.default_reasoning_effort == "max"
    asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    kw = fake_openai[-1]
    assert kw["model"] == "deepseek-v4-pro"
    assert kw["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kw["reasoning_effort"] == "max"
    assert "temperature" not in kw
