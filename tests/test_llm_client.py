import pytest
from unittest.mock import AsyncMock, patch
from financial_analyst.llm.client import LLMClient, load_llm_config

def test_load_llm_config(tmp_path):
    cfg = tmp_path / "llm.yaml"
    cfg.write_text("default_provider: anthropic\ndefault_model: claude-opus-4-7\nproviders: {}\nagent_overrides: {}\n")
    config = load_llm_config(cfg)
    assert config["default_provider"] == "anthropic"

def test_client_for_agent_uses_override(tmp_path, monkeypatch):
    cfg = tmp_path / "llm.yaml"
    cfg.write_text(
        "default_provider: anthropic\n"
        "default_model: claude-opus-4-7\n"
        "providers: {anthropic: {api_key_env: ANTHROPIC_API_KEY, models: []}}\n"
        "agent_overrides:\n  news-reader:\n    provider: qwen\n    model: qwen-plus\n"
    )
    client = LLMClient.for_agent("news-reader", config_path=cfg)
    assert client.provider == "qwen"
    assert client.model == "qwen-plus"

def test_client_for_agent_falls_back_to_default(tmp_path):
    cfg = tmp_path / "llm.yaml"
    cfg.write_text(
        "default_provider: anthropic\n"
        "default_model: claude-opus-4-7\n"
        "providers: {anthropic: {api_key_env: ANTHROPIC_API_KEY, models: []}}\n"
        "agent_overrides: {}\n"
    )
    client = LLMClient.for_agent("bull-advocate", config_path=cfg)
    assert client.provider == "anthropic"
    assert client.model == "claude-opus-4-7"

@pytest.mark.asyncio
async def test_chat_calls_litellm(tmp_path, monkeypatch):
    cfg = tmp_path / "llm.yaml"
    cfg.write_text(
        "default_provider: anthropic\n"
        "default_model: claude-opus-4-7\n"
        "providers: {anthropic: {api_key_env: ANTHROPIC_API_KEY, models: []}}\n"
        "agent_overrides: {}\n"
    )
    client = LLMClient.for_agent("bull-advocate", config_path=cfg)
    fake_response = {"choices": [{"message": {"content": "hello"}}]}
    with patch("financial_analyst.llm.client.acompletion", AsyncMock(return_value=fake_response)) as m:
        result = await client.chat(messages=[{"role": "user", "content": "hi"}])
        m.assert_awaited_once()
        assert result["choices"][0]["message"]["content"] == "hello"
