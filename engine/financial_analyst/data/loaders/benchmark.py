"""Benchmark-index close-series loader.

Carries a benchmark (CSI 300 by default) parallel series alongside the
``PanelData`` panel, so alphas like ``gtja149`` (downside beta vs index)
can compute relative-to-benchmark statistics.

Usage::

    bench_loader = BenchmarkLoader(loader=qlib_loader, benchmark="csi300")
    panel = PanelData.from_loader(
        ohlcv_loader, codes, start, end,
        benchmark_loader=bench_loader,
    )
    # panel.benchmark_close → Series indexed by (datetime, code),
    # repeating the same value across codes at each date.

Default benchmark: ``csi300`` (SH000300). Override via ``--benchmark``
in CLI or ``FA_BENCHMARK`` env var.
"""
from __future__ import annotations
import os
from typing import Optional

import pandas as pd


class BenchmarkLoader:
    """Fetch benchmark close series and broadcast to (date, code) panel."""

    BENCHMARK_CODES = {
        "csi300":  "SH000300",
        "csi500":  "SH000905",
        "csi800":  "SH000906",
        "csi1000": "SH000852",
        "zz500":   "SH000905",   # alias
        "sse":     "SH000001",   # 上证综指
        "szse":    "SZ399001",   # 深证成指
    }

    DEFAULT_BENCHMARK_ENV = "FA_BENCHMARK"

    def __init__(self, loader, benchmark: Optional[str] = None):
        """
        Parameters
        ----------
        loader : BaseLoader
            The underlying market-data loader (e.g., QlibBinaryLoader,
            TushareLoader). Must support ``fetch_quote(code, start, end)``.
        benchmark : str
            One of the keys in ``BENCHMARK_CODES``, or a raw Qlib-format
            code like ``SH000300``. Default reads ``FA_BENCHMARK`` env
            var, then falls back to ``"csi300"``.
        """
        if benchmark is None:
            benchmark = os.environ.get(self.DEFAULT_BENCHMARK_ENV, "csi300")
        self._loader = loader
        self._benchmark_key = benchmark
        self._benchmark_code = self.BENCHMARK_CODES.get(benchmark, benchmark)

    @property
    def benchmark_code(self) -> str:
        return self._benchmark_code

    @property
    def benchmark_key(self) -> str:
        return self._benchmark_key

    def fetch_close(self, start: str, end: str) -> pd.Series:
        """Return single-level datetime-indexed close series for the benchmark."""
        df = self._loader.fetch_quote(self._benchmark_code, start, end, freq="day")
        if df is None or df.empty:
            raise RuntimeError(
                f"BenchmarkLoader: empty close series for {self._benchmark_code} "
                f"({start} → {end}). Check loader provides this index."
            )
        if "close" not in df.columns:
            raise RuntimeError(
                f"BenchmarkLoader: fetch_quote returned no 'close' column for "
                f"{self._benchmark_code}. Got columns: {list(df.columns)}"
            )
        # Normalise to a single-level datetime index regardless of loader output shape
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index(level="code" if "code" in df.index.names else 0, drop=True)
        close = df["close"]
        close.index.name = "datetime"
        return close

    def broadcast_to_panel_index(
        self, close: pd.Series, panel_index: pd.MultiIndex,
    ) -> pd.Series:
        """Repeat the benchmark close at each (datetime, code) in panel_index.

        Stocks share the same benchmark close at each date, so we can
        broadcast by joining on datetime.
        """
        if "datetime" not in panel_index.names:
            raise ValueError(
                "BenchmarkLoader.broadcast: panel_index must have a 'datetime' level."
            )
        # Build a (datetime → close) lookup
        date_map = close.to_dict()
        dt_level = panel_index.get_level_values("datetime")
        values = [date_map.get(d, float("nan")) for d in dt_level]
        return pd.Series(values, index=panel_index, name="benchmark_close")
