from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from litellm import acompletion

from financial_analyst._config import find_config


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
        if self.provider == "anthropic":
            return f"anthropic/{self.model}"
        if self.provider == "openai":
            return self.model
        if self.provider == "qwen":
            return f"openai/{self.model}"
        if self.provider == "deepseek":
            return f"deepseek/{self.model}"
        return f"{self.provider}/{self.model}"

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
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
        # v1.7.5: accumulate token usage for the buddy status line.
        try:
            usage = None
            if isinstance(response, dict):
                usage = response.get("usage")
            else:
                usage = getattr(response, "usage", None)
            if usage is not None:
                get = (lambda k: usage.get(k) if isinstance(usage, dict)
                       else getattr(usage, k, None))
                pt = get("prompt_tokens") or 0
                ct = get("completion_tokens") or 0
                self.total_prompt_tokens += int(pt)
                self.total_completion_tokens += int(ct)
                self.n_calls += 1
        except Exception:
            pass
        return response
