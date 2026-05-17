from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List
import pandas as pd


class BaseLoader(ABC):
    @abstractmethod
    def fetch_quote(self, code: str, start: str, end: str) -> pd.DataFrame: ...

    @abstractmethod
    def fetch_daily_basic(self, code: str, start: str, end: str) -> pd.DataFrame: ...

    @abstractmethod
    def fetch_financials(self, code: str) -> pd.DataFrame: ...

    @abstractmethod
    def fetch_news(self, code: str, days: int = 30) -> List[Dict]: ...

    def supports(self, market: str) -> bool:
        return False
