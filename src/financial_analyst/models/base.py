from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseModel(ABC):
    @abstractmethod
    def predict(self, code: str, asof: str) -> Dict[str, float]: ...

    @abstractmethod
    def metadata(self) -> Dict[str, Any]: ...
