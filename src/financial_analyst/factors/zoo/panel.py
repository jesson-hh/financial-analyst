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
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import numpy as np
import pandas as pd

_DAILY_BASIC_FIELDS = ("pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate")


def _merge_daily_basic(panel: pd.DataFrame, loader, codes: list, start: str, end: str) -> None:
    """Merge each code's daily_basic fields onto the (datetime, code) panel in place.
    Robust to real-loader shape (trade_date column) and stub shape (datetime index).
    Guarded: missing data / a loader without it simply yields NaN columns."""
    def _one(code):
        try:
            db = loader.fetch_daily_basic(code, start, end)
        except Exception:
            return None
        if db is None or len(db) == 0:
            return None
        db = db.copy()
        if "trade_date" in db.columns:
            db = db.set_index("trade_date")
        try:
            db.index = pd.DatetimeIndex(db.index)
        except Exception:
            return None
        db["code"] = code
        db = db.set_index("code", append=True)
        db.index = db.index.set_names(["datetime", "code"])
        keep = [c for c in _DAILY_BASIC_FIELDS if c in db.columns]
        return db[keep] if keep else None

    # Parallel I/O (same rationale as from_loader): cut the per-code daily_basic
    # fetch loop with a thread pool.
    _workers = min(16, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=_workers) as _ex:
        frames = [f for f in _ex.map(_one, codes) if f is not None]
    n_ok = len(frames)
    import logging
    logging.getLogger("financial_analyst.zoo").debug(
        "daily_basic merged for %d/%d codes", n_ok, len(codes),
    )
    if not frames:
        return
    db_all = pd.concat(frames)
    db_all = db_all[~db_all.index.duplicated(keep="last")]
    # align by (datetime, code); codes/dates absent from daily_basic become NaN
    for col in _DAILY_BASIC_FIELDS:
        if col in db_all.columns:
            panel[col] = db_all[col].reindex(panel.index)


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
    def industry(self) -> pd.Series:
        """Industry classification per (date, code). Returns a Series of
        ``"未知"`` (unknown) for every cell if no IndustryLoader was passed
        in. Used by ``indneutralize`` for cross-sectional industry-demean.
        """
        if "industry" in self.df.columns:
            return self.df["industry"]
        return pd.Series("未知", index=self.df.index, dtype=str)

    @property
    def benchmark_close(self) -> pd.Series:
        """Benchmark-index close per (date, code), repeating the same
        value across codes at each date. Used by ``gtja149`` and other
        benchmark-relative alphas. Returns NaN-filled series when no
        BenchmarkLoader was passed to ``from_loader``.
        """
        if "benchmark_close" in self.df.columns:
            return self.df["benchmark_close"]
        return pd.Series(float("nan"), index=self.df.index, dtype=float)

    @property
    def benchmark_returns(self) -> pd.Series:
        """1-period benchmark returns. Same value across codes at each date."""
        bc = self.benchmark_close
        return bc.groupby(level="code", group_keys=False).pct_change(fill_method=None)

    def _optional_col(self, name: str) -> pd.Series:
        """Return column ``name`` if present, else an all-NaN Series on the panel
        index. Used by optional daily_basic fundamental fields."""
        if name in self.df.columns:
            return self.df[name]
        return pd.Series(float("nan"), index=self.df.index, dtype=float)

    @property
    def pe_ttm(self) -> pd.Series:
        """市盈率 TTM. NaN when daily_basic absent."""
        return self._optional_col("pe_ttm")

    @property
    def pb(self) -> pd.Series:
        """市净率. NaN when daily_basic absent."""
        return self._optional_col("pb")

    @property
    def ps_ttm(self) -> pd.Series:
        """市销率 TTM. NaN when daily_basic absent."""
        return self._optional_col("ps_ttm")

    @property
    def dv_ttm(self) -> pd.Series:
        """股息率 (%). NaN when daily_basic absent."""
        return self._optional_col("dv_ttm")

    @property
    def total_mv(self) -> pd.Series:
        """总市值 (万元)."""
        return self._optional_col("total_mv")

    @property
    def circ_mv(self) -> pd.Series:
        """流通市值 (万元)."""
        return self._optional_col("circ_mv")

    @property
    def turnover_rate(self) -> pd.Series:
        """换手率 (%)."""
        return self._optional_col("turnover_rate")

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
        industry_loader=None,
        benchmark_loader=None,
    ) -> "PanelData":
        """Pull each code's quote panel from a BaseLoader and stitch into
        a single MultiIndex DataFrame. Codes that fail are skipped with a
        warning rather than raising — alpha bench should tolerate partial
        universes.

        When ``industry_loader`` is supplied (an ``IndustryLoader``
        instance), the panel additionally carries an ``industry`` column
        mapping every (date, code) pair to the stock's industry. This
        enables the ``indneutralize`` operator and the alpha101 alphas
        that consume it.
        """
        def _load_one(code):
            try:
                df = loader.fetch_quote(code, start, end, freq=freq)
            except Exception as e:
                return None, (code, str(e)[:80])
            if df is None or len(df) == 0:
                return None, (code, "empty")
            # Ensure single-level datetime index
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level="code" if "code" in df.index.names else 0, drop=True)
            df = df.copy()
            # Real loaders (e.g. QlibBinaryLoader) return a positional RangeIndex
            # with the date held in a ``trade_date`` column. Promote it to the
            # datetime index so the panel's datetime level carries real timestamps:
            # cross-sectional alignment (ragged A-share calendars) and the
            # daily_basic merge below both key on it. Stub loaders that already
            # date-index (no trade_date column) are left untouched.
            if "trade_date" in df.columns:
                df = df.set_index("trade_date")
                df.index = pd.DatetimeIndex(df.index)
                df = df[~df.index.duplicated(keep="last")]
            df["code"] = code
            df = df.set_index("code", append=True)
            df.index = df.index.set_names(["datetime", "code"])
            return df, None

        # Parallel I/O: file reads release the GIL, so threads cut the 868-code
        # sequential load ~6.6x (85s→13s). Order is restored by sort_index below.
        frames = []
        skipped: list[tuple[str, str]] = []
        _workers = min(16, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=_workers) as _ex:
            for _df, _skip in _ex.map(_load_one, codes):
                if _skip is not None:
                    skipped.append(_skip)
                else:
                    frames.append(_df)
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

        if industry_loader is not None:
            ind_map = industry_loader.get_map(codes)
            # Map per (date, code) — industry repeats across dates for each code
            panel["industry"] = panel.index.get_level_values("code").map(ind_map).fillna(
                industry_loader.UNKNOWN_INDUSTRY
            )

        if benchmark_loader is not None:
            try:
                close = benchmark_loader.fetch_close(start, end)
                panel["benchmark_close"] = benchmark_loader.broadcast_to_panel_index(
                    close, panel.index,
                )
            except Exception as e:
                import logging
                logging.getLogger("financial_analyst.zoo").warning(
                    "BenchmarkLoader failed (skipping benchmark column): %s", e,
                )

        # SP-B.1b: merge daily_basic fundamentals (day freq only — daily_basic is day-only)
        if freq == "day":
            _merge_daily_basic(panel, loader, codes, start, end)

        return cls(panel)
