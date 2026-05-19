"""PanelData — wraps a MultiIndex (datetime, code) DataFrame so alpha
formulas can pull standard fields with a uniform API.

The underlying DataFrame must have:

* MultiIndex with levels ``("datetime", "code")``, datetime sorted ascending
* columns: ``open, high, low, close, volume, vwap, amount``
  (``vwap`` and ``amount`` synthesised if missing — vwap as
  ``(high+low+close)/3``, amount as ``close * volume``).

Operators (``rank``, ``ts_rank``, ``delta``, …) live in ``operators.py``
and consume / return same-shape pd.Series.
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd


class PanelData:
    def __init__(self, df: pd.DataFrame):
        if not isinstance(df.index, pd.MultiIndex) or df.index.nlevels != 2:
            raise ValueError(
                "PanelData requires a 2-level MultiIndex; got "
                f"{type(df.index).__name__} with {getattr(df.index, 'nlevels', '?')} levels"
            )
        # Normalise level names so operators can rely on them.
        df = df.copy()
        df.index = df.index.set_names(["datetime", "code"])
        df = df.sort_index()

        # Normalise common column aliases (Tushare ``vol`` etc.) → canonical.
        _COL_ALIASES = {"vol": "volume", "amt": "amount"}
        df = df.rename(columns={k: v for k, v in _COL_ALIASES.items() if k in df.columns and v not in df.columns})

        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"PanelData missing required columns: {sorted(missing)}")

        if "vwap" not in df.columns:
            df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3.0
        if "amount" not in df.columns:
            df["amount"] = df["close"] * df["volume"]

        self.df = df

    # --- single-field accessors -------------------------------------------
    @property
    def open(self) -> pd.Series:
        return self.df["open"]

    @property
    def high(self) -> pd.Series:
        return self.df["high"]

    @property
    def low(self) -> pd.Series:
        return self.df["low"]

    @property
    def close(self) -> pd.Series:
        return self.df["close"]

    @property
    def volume(self) -> pd.Series:
        return self.df["volume"]

    @property
    def vwap(self) -> pd.Series:
        return self.df["vwap"]

    @property
    def amount(self) -> pd.Series:
        return self.df["amount"]

    @property
    def returns(self) -> pd.Series:
        """One-period returns, computed per-code so we don't bleed across stocks.

        ``fill_method=None`` keeps NaNs from forward-filling across gaps — a
        suspended trading day must not pretend to be a zero-return day.
        """
        return self.df["close"].groupby(level="code").pct_change(fill_method=None)

    # --- panel meta --------------------------------------------------------
    def codes(self) -> pd.Index:
        return self.df.index.get_level_values("code").unique()

    def dates(self) -> pd.Index:
        return self.df.index.get_level_values("datetime").unique()

    def n_dates(self) -> int:
        return len(self.dates())

    def n_codes(self) -> int:
        return len(self.codes())

    def __repr__(self) -> str:
        return (
            f"PanelData(n_codes={self.n_codes()}, n_dates={self.n_dates()}, "
            f"date_range=[{self.dates().min()}, {self.dates().max()}])"
        )

    # --- builder -----------------------------------------------------------
    @classmethod
    def from_loader(
        cls,
        loader,
        codes: list[str],
        start: str,
        end: str,
        freq: str = "day",
    ) -> "PanelData":
        """Pull each code's quote panel from a BaseLoader and stitch into
        a single MultiIndex DataFrame. Codes that fail are skipped with a
        warning rather than raising — alpha bench should tolerate partial
        universes.
        """
        frames = []
        skipped: list[tuple[str, str]] = []
        for code in codes:
            try:
                df = loader.fetch_quote(code, start, end, freq=freq)
            except Exception as e:
                skipped.append((code, str(e)[:80]))
                continue
            if df is None or len(df) == 0:
                skipped.append((code, "empty"))
                continue
            # Ensure single-level datetime index
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level="code" if "code" in df.index.names else 0, drop=True)
            df = df.copy()
            df["code"] = code
            df = df.set_index("code", append=True)
            df.index = df.index.set_names(["datetime", "code"])
            frames.append(df)
        if not frames:
            raise RuntimeError(
                f"PanelData.from_loader: no codes loaded successfully "
                f"(tried {len(codes)}, skipped {len(skipped)})"
            )
        panel = pd.concat(frames).sort_index()
        if skipped:
            import logging
            logging.getLogger("financial_analyst.zoo").warning(
                "PanelData.from_loader skipped %d/%d codes; first: %s",
                len(skipped), len(codes), skipped[:3],
            )
        return cls(panel)
