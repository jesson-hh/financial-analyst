"""Qlib binary file loader for local Qlib data directories.

Reads Qlib's standard layout::

    <provider_uri>/calendars/day.txt           — sorted trading dates, one per line
    <provider_uri>/instruments/all.txt         — code<TAB>start_date<TAB>end_date
    <provider_uri>/features/<code_lower>/<field>.day.bin
                — 4-byte float32 start_index header + float32 value array

Zero network, microsecond reads. Use when you have a local Qlib data directory.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from financial_analyst.data.loaders.base import BaseLoader


# Map BaseLoader-expected output fields → Qlib binary filenames (without .day.bin suffix).
# fetch_quote output uses "vol" (matching TushareLoader convention); the .bin file is "volume".
QUOTE_FIELD_MAP: Dict[str, str] = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "vol": "volume",       # TushareLoader outputs "vol"; binary file is "volume"
    "amount": "amount",
}

DAILY_BASIC_FIELD_MAP: Dict[str, str] = {
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "ps_ttm": "ps_ttm",
    "dv_ttm": "dv_ttm",
    "total_mv": "total_mv",
    "circ_mv": "circ_mv",
    "turnover_rate": "turnover_rate",
}


class QlibBinaryLoader(BaseLoader):
    """Read daily OHLCV + daily_basic from a local Qlib binary data directory.

    Parameters
    ----------
    provider_uri:
        Root path of the Qlib provider, e.g. ``G:/stocks/stock_data/cn_data``.
    """

    def __init__(self, provider_uri: str) -> None:
        self.root = Path(provider_uri)
        if not self.root.exists():
            raise ValueError(f"Qlib provider_uri does not exist: {provider_uri}")
        self._calendar: Optional[List[pd.Timestamp]] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_calendar(self) -> List[pd.Timestamp]:
        if self._calendar is not None:
            return self._calendar
        cal_path = self.root / "calendars" / "day.txt"
        with open(cal_path, "r", encoding="utf-8") as f:
            dates = [pd.Timestamp(line.strip()) for line in f if line.strip()]
        self._calendar = dates
        return dates

    @staticmethod
    def _code_to_dir(code: str) -> str:
        """Convert stock code to Qlib directory name (lowercase, no dots).

        Examples
        --------
        ``SH600519`` → ``sh600519``
        ``SZ000858`` → ``sz000858``
        """
        return code.strip().lower().replace(".", "")

    def _read_bin(self, code: str, field: str) -> Optional[pd.Series]:
        """Read one ``.bin`` file and return a Series indexed by trade_date.

        Returns ``None`` if the file doesn't exist (field not collected for
        this stock or stock not present in the data directory).

        The binary format is:
          - 4 bytes: float32 encoding of an integer ``start_index`` (position
            in the calendar where this stock's data begins)
          - remainder: float32 array of values
        """
        bin_path = (
            self.root / "features" / self._code_to_dir(code) / f"{field}.day.bin"
        )
        if not bin_path.exists():
            return None
        with open(bin_path, "rb") as f:
            header = f.read(4)
            if len(header) < 4:
                return None
            # start_index is stored as float32 but holds an integer value
            start_index = int(struct.unpack("<f", header)[0])
            data = np.frombuffer(f.read(), dtype=np.float32)

        cal = self._load_calendar()
        end_index = start_index + len(data)
        dates = cal[start_index:end_index]
        n = min(len(dates), len(data))
        if n == 0:
            return None
        return pd.Series(
            data[:n].astype(float),
            index=pd.DatetimeIndex(dates[:n]),
            name=field,
        )

    def _slice(self, series: pd.Series, start: str, end: str) -> pd.Series:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        return series.loc[(series.index >= start_ts) & (series.index <= end_ts)]

    def _build_df(
        self, code: str, field_map: Dict[str, str], start: str, end: str
    ) -> pd.DataFrame:
        """Read multiple fields, slice by date range, return DataFrame."""
        result_cols: Dict[str, pd.Series] = {}
        for out_field, bin_field in field_map.items():
            s = self._read_bin(code, bin_field)
            if s is not None:
                result_cols[out_field] = self._slice(s, start, end)
        if not result_cols:
            return pd.DataFrame()
        df = pd.DataFrame(result_cols)
        df.index.name = "trade_date"
        return df.reset_index()

    # ------------------------------------------------------------------
    # BaseLoader interface
    # ------------------------------------------------------------------

    def supports(self, market: str) -> bool:
        return market == "a_share"

    def fetch_quote(self, code: str, start: str, end: str) -> pd.DataFrame:
        return self._build_df(code, QUOTE_FIELD_MAP, start, end)

    def fetch_daily_basic(self, code: str, start: str, end: str) -> pd.DataFrame:
        return self._build_df(code, DAILY_BASIC_FIELD_MAP, start, end)

    def fetch_financials(self, code: str) -> pd.DataFrame:
        # Qlib binary format doesn't store financials; return empty DataFrame.
        # Future: read from <provider_uri>/../parquet/financials.parquet
        return pd.DataFrame()

    def fetch_news(self, code: str, days: int = 30) -> List[Dict]:
        return []
