from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from litellm import acompletion

from financial_analyst._config import find_config

# v1.9.6: 统一 AsyncOpenAI 多 base_url 架构 (替代旧 litellm 单 client).
#
# 起因: Clash fake-ip 模式下 DNS 把 *.aliyuncs.com / api.deepseek.com 都解析到
# 198.18.0.x 段, 走 litellm 默认 client 会被 Clash 接管走海外节点 → 10s timeout.
# 修法: provider 按"网络出口策略"分桶, 每桶一个 httpx.AsyncClient.
#
# 三种 network_profile:
#   - domestic    : trust_env=False, 不走系统 proxy. 给国内站 (qwen/dashscope).
#   - intl_clash  : 显式 proxy=HTTPS_PROXY (default 127.0.0.1:7890) + verify=False
#                   (Clash MITM 替换 cert). 给 deepseek / openai / openrouter.
#   - intl_system : trust_env=True, 让 httpx 自己读系统代理. 给少数无 MITM 环境.
#
# OpenAI 兼容 provider (qwen/deepseek/openai/openrouter) 全走 AsyncOpenAI + base_url
# 切. 非兼容 provider (anthropic) 保留旧 litellm 路径.

_OPENAI_COMPAT_PROVIDERS = {"qwen", "deepseek", "openai", "openrouter"}
_PROVIDER_CLIENTS: Dict[str, Any] = {}  # cache: f"{provider}:{base_url}" -> AsyncOpenAI


def _build_http_client(network_profile: str):
    """Construct httpx.AsyncClient per network profile.

    Env overrides:
    - HTTPS_PROXY: proxy URL for intl_clash (default http://127.0.0.1:7890).
    - FA_INTL_VERIFY: 'true' to enforce SSL verify for intl_clash (default
      False because Clash MITM replaces the cert chain).
    """
    import httpx
    if network_profile == "domestic":
        return httpx.AsyncClient(trust_env=False, timeout=120)
    if network_profile == "intl_clash":
        proxy = os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:7890"
        verify_env = os.environ.get("FA_INTL_VERIFY", "false").lower()
        verify = verify_env in ("true", "1", "yes")
        return httpx.AsyncClient(
            trust_env=False, proxy=proxy, verify=verify, timeout=120,
        )
    if network_profile == "intl_system":
        return httpx.AsyncClient(trust_env=True, timeout=120)
    return httpx.AsyncClient(timeout=120)


def _get_openai_compat_client(provider: str, api_key: str, base_url: str,
                               network_profile: str):
    """Lazy singleton AsyncOpenAI for OpenAI-compatible providers."""
    cache_key = f"{provider}:{base_url}"
    if cache_key not in _PROVIDER_CLIENTS:
        from openai import AsyncOpenAI
        _PROVIDER_CLIENTS[cache_key] = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=_build_http_client(network_profile),
        )
    return _PROVIDER_CLIENTS[cache_key]


def _reset_provider_clients():
    """Test helper: clear the per-provider client cache."""
    _PROVIDER_CLIENTS.clear()


def load_llm_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load llm.yaml from the standard lookup chain.

    See ``financial_analyst._config.find_config`` for the search order
    (user override → cwd/config → bundled default).
    """
    cfg_path = find_config("llm.yaml", explicit=path)
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class LLMClient:
    def __init__(self, provider: str, model: str, config: Dict[str, Any]):
        self.provider = provider
        self.model = model
        self.config = config
        # v1.7.5: cumulative token accounting for the buddy status line.
        # Carried across /model switches via with_overrides().
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.n_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @classmethod
    def for_agent(cls, agent_name: str, config_path: Optional[Path] = None) -> "LLMClient":
        config = load_llm_config(config_path)
        override = config.get("agent_overrides", {}).get(agent_name, {})
        provider = override.get("provider", config["default_provider"])
        model = override.get("model", config["default_model"])
        return cls(provider=provider, model=model, config=config)

    def with_overrides(self, provider: Optional[str] = None,
                        model: Optional[str] = None) -> "LLMClient":
        """Return a new LLMClient with the same config but a different
        provider/model. Used by the buddy ``/model`` slash command to
        live-switch the LLM without reloading config from disk.

        Cumulative token counters carry over so the session total spans
        model switches."""
        new = LLMClient(
            provider=provider or self.provider,
            model=model or self.model,
            config=self.config,
        )
        new.total_prompt_tokens = self.total_prompt_tokens
        new.total_completion_tokens = self.total_completion_tokens
        new.n_calls = self.n_calls
        return new

    def list_models(self) -> Dict[str, List[str]]:
        """Return ``{provider: [model, ...]}`` from the loaded config.
        Source of truth for the ``/model`` picker."""
        out: Dict[str, List[str]] = {}
        for prov, cfg in (self.config.get("providers") or {}).items():
            out[prov] = list(cfg.get("models") or [])
        return out

    def _model_string(self) -> str:
        """litellm model string. Only used on the litellm fallback path
        (non-OpenAI-compatible providers)."""
        if self.provider == "anthropic":
            return f"anthropic/{self.model}"
        if self.provider == "openai":
            return self.model
        if self.provider == "qwen":
            return f"openai/{self.model}"
        if self.provider == "deepseek":
            return f"deepseek/{self.model}"
        return f"{self.provider}/{self.model}"

    def _accumulate_usage(self, response: Any) -> None:
        """Update self.total_prompt_tokens / total_completion_tokens / n_calls
        from a response object (works for both OpenAI SDK and litellm shapes)."""
        try:
            usage = None
            if isinstance(response, dict):
                usage = response.get("usage")
            else:
                usage = getattr(response, "usage", None)
            if usage is None:
                return
            get = (lambda k: usage.get(k) if isinstance(usage, dict)
                   else getattr(usage, k, None))
            pt = get("prompt_tokens") or 0
            ct = get("completion_tokens") or 0
            self.total_prompt_tokens += int(pt)
            self.total_completion_tokens += int(ct)
            self.n_calls += 1
        except Exception:
            pass

    async def _chat_openai_compat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        response_format: Optional[Dict[str, Any]],
        temperature: float,
    ) -> Any:
        """OpenAI-compatible providers (qwen / deepseek / openai / openrouter).
        Goes through AsyncOpenAI + provider-specific http_client."""
        provider_cfg = self.config.get("providers", {}).get(self.provider, {})
        api_key_env = provider_cfg.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        base_url = provider_cfg.get("base_url", "")
        network_profile = provider_cfg.get("network_profile", "intl_system")
        client = _get_openai_compat_client(
            self.provider, api_key, base_url, network_profile,
        )
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if response_format is not None:
            kwargs["response_format"] = response_format
        response = await client.chat.completions.create(**kwargs)
        self._accumulate_usage(response)
        # v1.9.6: 21+ callers do response["choices"][0]["message"]["content"]
        # dict-style access — litellm.ModelResponse 是 dict-like, AsyncOpenAI
        # ChatCompletion 是 pydantic 不 subscriptable. dump 成 dict 保持兼容.
        return response.model_dump()

    async def _chat_litellm(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        response_format: Optional[Dict[str, Any]],
        temperature: float,
    ) -> Any:
        """Fallback path via litellm for non-OpenAI-compatible providers
        (e.g. anthropic)."""
        kwargs: Dict[str, Any] = {
            "model": self._model_string(),
            "messages": messages,
            "temperature": temperature,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if response_format is not None:
            kwargs["response_format"] = response_format

        provider_cfg = self.config.get("providers", {}).get(self.provider, {})
        if "base_url" in provider_cfg:
            kwargs["api_base"] = provider_cfg["base_url"]
        api_key_env = provider_cfg.get("api_key_env")
        if api_key_env:
            api_key = os.environ.get(api_key_env)
            if api_key:
                kwargs["api_key"] = api_key

        response = await acompletion(**kwargs)
        self._accumulate_usage(response)
        return response

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.2,
    ) -> Any:
        if self.provider in _OPENAI_COMPAT_PROVIDERS:
            return await self._chat_openai_compat(
                messages, tools, response_format, temperature,
            )
        return await self._chat_litellm(
            messages, tools, response_format, temperature,
        )
