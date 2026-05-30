"""Qlib binary file loader for local Qlib data directories.

Reads Qlib's standard layout::

    <provider_uri>/calendars/day.txt            — sorted trading dates, one per line
    <provider_uri>/calendars/5min.txt           — sorted 5-min timestamps, one per line
    <provider_uri>/instruments/all.txt          — code<TAB>start_date<TAB>end_date
    <provider_uri>/features/<code_lower>/<field>.day.bin
    <provider_uri>/features/<code_lower>/<field>.5min.bin
                — 4-byte float32 start_index header + float32 value array

Zero network, microsecond reads.  Use when you have a local Qlib data directory.

``provider_uri`` can be:

* ``str``  — a single day-data root, e.g. ``"G:/stocks/stock_data/cn_data"``
* ``dict`` — freq → root mapping, e.g.::

      {
          "day":  "G:/stocks/stock_data/cn_data",
          "5min": "G:/stocks/stock_data/cn_data_5min",
      }

  The ``"day"`` key is mandatory; ``"5min"`` / ``"1min"`` are optional.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd

from financial_analyst.data.loaders.base import BaseLoader


# ---------------------------------------------------------------------------
# Field maps
# ---------------------------------------------------------------------------

# BaseLoader output field → Qlib binary filename stem (without suffix).
# "vol" in output matches TushareLoader convention; the .bin file is "volume".
QUOTE_FIELD_MAP: Dict[str, str] = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "vol": "volume",
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

# Calendar file and binary suffix per freq
_CALENDAR_FILE: Dict[str, str] = {
    "day": "day.txt",
    "5min": "5min.txt",
    "1min": "1min.txt",
}
_BIN_SUFFIX: Dict[str, str] = {
    "day": "day",
    "5min": "5min",
    "1min": "1min",
}


class QlibBinaryLoader(BaseLoader):
    """Read OHLCV + daily_basic from local Qlib binary data directories.

    Parameters
    ----------
    provider_uri:
        Either a single path string (day data only) or a dict mapping freq
        names to root paths.  The ``"day"`` key is always required.

    Examples
    --------
    Backward-compatible (day only)::

        loader = QlibBinaryLoader("G:/stocks/stock_data/cn_data")

    Multi-freq::

        loader = QlibBinaryLoader({
            "day":  "G:/stocks/stock_data/cn_data",
            "5min": "G:/stocks/stock_data/cn_data_5min",
        })
    """

    def __init__(self, provider_uri: Union[str, dict]) -> None:
        if isinstance(provider_uri, str):
            self._roots: Dict[str, Path] = {"day": Path(provider_uri)}
        elif isinstance(provider_uri, dict):
            self._roots = {k: Path(v) for k, v in provider_uri.items()}
        else:
            raise ValueError("provider_uri must be a str or dict")

        if "day" not in self._roots:
            raise ValueError(
                "provider_uri dict must include a 'day' root "
                f"(got keys: {list(self._roots)})"
            )
        if not self._roots["day"].exists():
            raise ValueError(
                f"Qlib provider_uri does not exist: {self._roots['day']}"
            )
        # 5min/1min roots are checked lazily; they may legitimately be absent
        # (rotating window — only ~7 days retained).

        self._calendars: Dict[str, List[pd.Timestamp]] = {}
        import threading
        self._calendar_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_calendar(self, freq: str = "day") -> List[pd.Timestamp]:
        """Return the calendar for *freq*, loading from disk on first call (thread-safe)."""
        cached = self._calendars.get(freq)
        if cached is not None:
            return cached
        with self._calendar_lock:
            cached = self._calendars.get(freq)   # 双重检查
            if cached is not None:
                return cached
            if freq not in self._roots:
                raise ValueError(
                    f"freq={freq!r} not configured in provider_uri "
                    f"(available: {list(self._roots)})"
                )
            cal_fname = _CALENDAR_FILE.get(freq)
            if cal_fname is None:
                raise ValueError(f"Unknown freq: {freq!r}")
            cal_path = self._roots[freq] / "calendars" / cal_fname
            with open(cal_path, "r", encoding="utf-8") as f:
                stamps = [pd.Timestamp(line.strip()) for line in f if line.strip()]
            self._calendars[freq] = stamps
            return stamps

    @staticmethod
    def _code_to_dir(code: str) -> str:
        """Convert stock code to Qlib directory name (lowercase, no dots).

        Examples
        --------
        ``SH600519`` → ``sh600519``
        ``SZ000858`` → ``sz000858``
        """
        return code.strip().lower().replace(".", "")

    def _read_bin(
        self, code: str, field: str, freq: str = "day"
    ) -> Optional[pd.Series]:
        """Read one ``.bin`` file and return a Series indexed by timestamp.

        Returns ``None`` if the file doesn't exist (field not collected for
        this stock, stock not in directory, or freq root not configured).

        Binary format:
          - 4 bytes: float32 encoding of an integer ``start_index``
          - remainder: float32 value array
        """
        if freq not in self._roots:
            return None
        suffix = _BIN_SUFFIX[freq]
        bin_path = (
            self._roots[freq]
            / "features"
            / self._code_to_dir(code)
            / f"{field}.{suffix}.bin"
        )
        if not bin_path.exists():
            return None
        with open(bin_path, "rb") as f:
            header = f.read(4)
            if len(header) < 4:
                return None
            start_index = int(struct.unpack("<f", header)[0])
            data = np.frombuffer(f.read(), dtype=np.float32)

        cal = self._load_calendar(freq)
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

    def _slice(self, series: pd.Series, start: str, end: str, freq: str = "day") -> pd.Series:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        # For intraday freqs, if the user passes a date-only string we want to
        # include all bars within that date, so extend end to end-of-day.
        if freq != "day" and end_ts.time() == pd.Timestamp("00:00:00").time():
            end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        return series.loc[(series.index >= start_ts) & (series.index <= end_ts)]

    def _build_df(
        self,
        code: str,
        field_map: Dict[str, str],
        start: str,
        end: str,
        freq: str = "day",
    ) -> pd.DataFrame:
        """Read multiple fields, slice by date range, return DataFrame."""
        result_cols: Dict[str, pd.Series] = {}
        for out_field, bin_field in field_map.items():
            s = self._read_bin(code, bin_field, freq=freq)
            if s is not None:
                result_cols[out_field] = self._slice(s, start, end, freq=freq)
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

    def fetch_quote(
        self, code: str, start: str, end: str, freq: str = "day"
    ) -> pd.DataFrame:
        """Fetch OHLCV bars at the requested frequency.

        Parameters
        ----------
        freq:
            ``'day'``, ``'5min'``, or ``'1min'``.  If the freq root is not
            configured (e.g. ``provider_uri`` was a plain string), returns an
            empty DataFrame without raising.
        """
        if freq not in self._roots:
            # Gracefully empty — caller should treat missing 5min as optional.
            return pd.DataFrame()
        return self._build_df(code, QUOTE_FIELD_MAP, start, end, freq=freq)

    def fetch_daily_basic(self, code: str, start: str, end: str) -> pd.DataFrame:
        # daily_basic fields only exist at the day frequency in this Qlib layout.
        return self._build_df(code, DAILY_BASIC_FIELD_MAP, start, end, freq="day")

    def fetch_financials(self, code: str) -> pd.DataFrame:
        # Qlib binary format doesn't store financials; return empty DataFrame.
        # Future: read from <provider_uri>/../parquet/financials.parquet
        return pd.DataFrame()

    def fetch_news(self, code: str, days: int = 30) -> List[Dict]:
        return []
