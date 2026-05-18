"""Example: minimal CSV-backed loader.

If you don't want to use Tushare or build a Qlib data directory, you can
provide a loader that reads OHLCV directly from CSV files. The factor-computer
and quote-fetcher sub-agents will use it as long as it implements BaseLoader.

To use:
    >>> from examples.custom_loader_csv_only import SimpleCsvLoader
    >>> loader = SimpleCsvLoader(csv_dir="G:/my_data")
    >>> # In your agent's _get_loader override, return this loader instance.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import pandas as pd
from financial_analyst.data.loaders.base import BaseLoader


class SimpleCsvLoader(BaseLoader):
    """Reads one CSV per stock, e.g. csv_dir/<code>.csv with columns:
    trade_date, open, high, low, close, vol, amount.

    No daily_basic / financials — those return empty DataFrames.
    """

    def __init__(self, csv_dir: str):
        self.csv_dir = Path(csv_dir)
        if not self.csv_dir.exists():
            raise ValueError(f"CSV dir does not exist: {csv_dir}")

    def fetch_quote(self, code: str, start: str, end: str, freq: str = "day") -> pd.DataFrame:
        if freq != "day":
            return pd.DataFrame()
        path = self.csv_dir / f"{code.upper()}.csv"
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        mask = (df["trade_date"] >= pd.Timestamp(start)) & (df["trade_date"] <= pd.Timestamp(end))
        return df.loc[mask].reset_index(drop=True)

    def fetch_daily_basic(self, code: str, start: str, end: str) -> pd.DataFrame:
        return pd.DataFrame()  # no valuation data in this minimal loader

    def fetch_financials(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_news(self, code: str, days: int = 30) -> List[Dict]:
        return []

    def supports(self, market: str) -> bool:
        return market == "a_share"
