from __future__ import annotations
from typing import Any, Dict, Type
from financial_analyst.models.base import BaseModel


class ModelRegistry:
    _registry: Dict[str, Type[BaseModel]] = {}
    _instances: Dict[str, BaseModel] = {}

    @classmethod
    def register(cls, name: str, model_cls: Type[BaseModel]) -> None:
        if name in cls._registry:
            raise ValueError(f"Model '{name}' already registered")
        cls._registry[name] = model_cls

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()
        cls._instances.clear()

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def get_instance(cls, name: str, **init_kwargs: Any) -> BaseModel:
        if name not in cls._instances:
            cls._instances[name] = cls._registry[name](**init_kwargs)
        return cls._instances[name]

    @classmethod
    def predict_all(cls, code: str, asof: str) -> Dict[str, Dict[str, float]]:
        return {name: cls.get_instance(name).predict(code, asof) for name in cls._registry}
