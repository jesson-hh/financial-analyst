from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from litellm import acompletion

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm.yaml"


def load_llm_config(path: Optional[Path] = None) -> Dict[str, Any]:
    cfg_path = path or DEFAULT_CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class LLMClient:
    def __init__(self, provider: str, model: str, config: Dict[str, Any]):
        self.provider = provider
        self.model = model
        self.config = config

    @classmethod
    def for_agent(cls, agent_name: str, config_path: Optional[Path] = None) -> "LLMClient":
        config = load_llm_config(config_path)
        override = config.get("agent_overrides", {}).get(agent_name, {})
        provider = override.get("provider", config["default_provider"])
        model = override.get("model", config["default_model"])
        return cls(provider=provider, model=model, config=config)

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

        return await acompletion(**kwargs)
