from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from litellm import acompletion

from financial_analyst._config import find_config


def _is_transient(exc: BaseException) -> bool:
    """瞬时可重试错误:网络/超时/5xx/429。鉴权(401)/请求错(400)不重试(重试也白搭)。
    按类名+status_code 判定,避免硬依赖 openai/httpx 的异常类(litellm 路径类型不同)。"""
    if isinstance(exc, asyncio.CancelledError):
        return False
    name = type(exc).__name__
    if name in {
        "APITimeoutError", "APIConnectionError", "InternalServerError",
        "RateLimitError", "ServiceUnavailableError",
        "Timeout", "ConnectError", "ConnectTimeout", "ReadTimeout",
        "ReadError", "RemoteProtocolError", "PoolTimeout", "WriteTimeout",
    }:
        return True
    code = getattr(exc, "status_code", None)
    return isinstance(code, int) and (code >= 500 or code == 429)

# v1.9.6: unified AsyncOpenAI multi-base_url architecture (replaces the old litellm single client).
#
# Why: under Clash fake-ip mode, DNS resolves *.aliyuncs.com / api.deepseek.com all into
# the 198.18.0.x range, so litellm's default client gets hijacked by Clash through overseas
# nodes → 10s timeout. Fix: bucket providers by "network egress strategy", one
# httpx.AsyncClient per bucket.
#
# Three network_profiles:
#   - domestic    : trust_env=False, bypass system proxy. For domestic CN endpoints (qwen/dashscope).
#   - intl_clash  : explicit proxy=HTTPS_PROXY (default 127.0.0.1:7890) + verify=False
#                   (Clash MITM replaces the cert). For deepseek / openai / openrouter.
#   - intl_system : trust_env=True, let httpx read the system proxy itself. For the rare no-MITM env.
#
# OpenAI-compatible providers (qwen/deepseek/openai/openrouter) all go through
# AsyncOpenAI + base_url switching. Non-compatible providers (anthropic) keep the
# old litellm path.

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
        # v1.x: prompt tokens served from DeepSeek's automatic prefix cache
        # (billed ~0.1x). Lets us SEE that the fixed tool-schema prefix is
        # cached across a turn's tool loop — i.e. re-sending the 8.6KB schema
        # every iteration is near-free, not a leak. Subset of total_prompt_tokens.
        self.total_cached_tokens: int = 0
        self.n_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def cache_hit_rate(self) -> float:
        """Share of prompt tokens served from cache (0.0–1.0). 0 if no calls."""
        return (self.total_cached_tokens / self.total_prompt_tokens
                if self.total_prompt_tokens else 0.0)

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
        new.total_cached_tokens = self.total_cached_tokens
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
            # Cache-hit prompt tokens. Robust across shapes:
            #   - OpenAI/DeepSeek standard: usage.prompt_tokens_details.cached_tokens
            #   - DeepSeek raw top-level:   usage.prompt_cache_hit_tokens
            cached = 0
            details = get("prompt_tokens_details")
            if details is not None:
                cached = ((details.get("cached_tokens") if isinstance(details, dict)
                           else getattr(details, "cached_tokens", 0)) or 0)
            if not cached:
                cached = get("prompt_cache_hit_tokens") or 0
            self.total_cached_tokens += int(cached)
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
        # dict-style access — litellm.ModelResponse is dict-like, but AsyncOpenAI
        # ChatCompletion is a pydantic model and not subscriptable. Dump to dict to keep compatibility.
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
        # 瞬时错误(SSL hiccup / 超时 / 5xx / 429)指数退避重试。此前 chat() 零重试,
        # 任何抖动直接冒泡 → seats/decide、各研报 SubAgent、wisdom 等单次失败即判失败
        # (重试只在交互式 buddy 循环有)。鉴权/请求错(401/400)不重试,立即抛。
        attempts = max(0, int(self.config.get("max_retries", 2))) + 1
        last_exc: Optional[BaseException] = None
        for i in range(attempts):
            try:
                if self.provider in _OPENAI_COMPAT_PROVIDERS:
                    return await self._chat_openai_compat(
                        messages, tools, response_format, temperature,
                    )
                return await self._chat_litellm(
                    messages, tools, response_format, temperature,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if i >= attempts - 1 or not _is_transient(e):
                    raise
                await asyncio.sleep(0.6 * (3 ** i))   # 0.6s, 1.8s
        assert last_exc is not None
        raise last_exc
