from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Type
from financial_analyst.agent.base import SubAgent


class SubAgentRegistry:
    _registry: Dict[str, Type[SubAgent]] = {}

    @classmethod
    def register(cls, name: str, agent_cls: Type[SubAgent]) -> None:
        if name in cls._registry:
            raise ValueError(f"Sub-agent '{name}' already registered")
        cls._registry[name] = agent_cls

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def build(cls, name: str, memory_root: Path, **kwargs: Any) -> SubAgent:
        if name not in cls._registry:
            raise KeyError(f"Unknown sub-agent: {name}")
        return cls._registry[name](memory_root=memory_root, **kwargs)
