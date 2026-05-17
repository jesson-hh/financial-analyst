from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List


class KnowledgeBase(ABC):
    @abstractmethod
    def query(self, query: str, top_k: int = 5) -> List[Dict]: ...

    @abstractmethod
    def get_related(self, code: str) -> List[Dict]: ...
