from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List
import pandas as pd


class BaseLoader(ABC):
    @abstractmethod
    def fetch_quote(self, code: str, start: str, end: str, freq: str = "day") -> pd.DataFrame:
        """Fetch OHLCV bars.

        Parameters
        ----------
        code:   Stock code, e.g. ``'SH600519'``.
        start:  Start date string ``'YYYY-MM-DD'``.
        end:    End date string ``'YYYY-MM-DD'``.
        freq:   Bar frequency: ``'day'``, ``'5min'``, or ``'1min'``.
                Implementations that do not support a given freq should return
                an empty DataFrame rather than raising.  Default ``'day'`` for
                backward compatibility.
        """

    @abstractmethod
    def fetch_daily_basic(self, code: str, start: str, end: str) -> pd.DataFrame: ...

    @abstractmethod
    def fetch_financials(self, code: str) -> pd.DataFrame: ...

    @abstractmethod
    def fetch_news(self, code: str, days: int = 30) -> List[Dict]: ...

    def supports(self, market: str) -> bool:
        return False
