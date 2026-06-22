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
# Precomputed technical indicators (day freq; loaded via loader.fetch_tech if available).
_TECH_FIELDS = ("rsi_14", "macd_signal", "amihud_20", "mom_20", "mom_60", "mom_120")

# W1b: financial-statement fields (quality/growth/leverage), merged PIT (as-of
# announcement date) onto the daily panel. See QlibBinaryLoader.FINANCIAL_FIELDS.
_FINANCIAL_FIELDS = (
    "roe", "roa", "net_margin", "rev_yoy", "np_yoy",
    "debt_ratio", "eps", "net_income", "revenue", "total_equity", "cfo",
)

# 资金面:东财五档日频净流入(主力/超大/大/中/小单的净额与净占比)。
# day 频 EOD 可见(visible_ts = trade_date 23:59:59),与 volume/amount 同口径放置。
_FUND_FLOW_FIELDS = (
    "main_net_amount", "main_net_pct",
    "super_large_net_amount", "super_large_net_pct",
    "large_net_amount", "large_net_pct",
    "medium_net_amount", "medium_net_pct",
    "small_net_amount", "small_net_pct",
)


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


def _merge_tech(panel: pd.DataFrame, loader, codes: list, start: str, end: str) -> None:
    """Merge precomputed technical indicators (rsi_14/macd_signal/amihud_20/mom_*) onto the
    (datetime, code) panel in place. Mirrors _merge_daily_basic; loaders without ``fetch_tech``
    (or missing .bin) simply yield NaN columns (honest contract). New panel columns are then
    auto-exposed as DSL fields via compile_factor's column setdefault — no whitelist edit needed."""
    if not hasattr(loader, "fetch_tech"):
        return

    def _one(code):
        try:
            tb = loader.fetch_tech(code, start, end)
        except Exception:
            return None
        if tb is None or len(tb) == 0:
            return None
        tb = tb.copy()
        if "trade_date" in tb.columns:
            tb = tb.set_index("trade_date")
        try:
            tb.index = pd.DatetimeIndex(tb.index)
        except Exception:
            return None
        tb["code"] = code
        tb = tb.set_index("code", append=True)
        tb.index = tb.index.set_names(["datetime", "code"])
        keep = [c for c in _TECH_FIELDS if c in tb.columns]
        return tb[keep] if keep else None

    _workers = min(16, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=_workers) as _ex:
        frames = [f for f in _ex.map(_one, codes) if f is not None]
    if not frames:
        return
    tb_all = pd.concat(frames)
    tb_all = tb_all[~tb_all.index.duplicated(keep="last")]
    for col in _TECH_FIELDS:
        if col in tb_all.columns:
            panel[col] = tb_all[col].reindex(panel.index)


def _merge_financials(panel: pd.DataFrame, loader, codes: list, start: str, end: str) -> None:
    """Merge each code's PIT financial-statement fields onto the (datetime, code)
    panel in place, as-of the announcement date — no look-ahead.

    Financials are sparse (quarterly, stamped at ann_date); they are forward-filled
    onto the dense trading calendar so every (date, code) carries the most recently
    *announced* report. Missing data / a loader without financials simply yields
    NaN columns (same honest contract as ``_merge_daily_basic``)."""
    # Per-code trading dates from the panel (computed once).
    code_level = panel.index.get_level_values("code")
    dt_level = panel.index.get_level_values("datetime")
    _idx = pd.DataFrame({"code": code_level, "dt": dt_level})
    dates_by_code = {
        c: pd.DatetimeIndex(sub["dt"].unique()).sort_values()
        for c, sub in _idx.groupby("code", sort=False)
    }

    def _one(code):
        try:
            fin = loader.fetch_financials(code)
        except Exception:
            return None
        if fin is None or len(fin) == 0:
            return None
        dts = dates_by_code.get(code)
        if dts is None or len(dts) == 0:
            return None
        try:
            fin = fin.copy()
            fin.index = pd.DatetimeIndex(fin.index)
            fin = fin[~fin.index.duplicated(keep="last")].sort_index()
            # PIT as-of-backward: each trading date gets the most recent report
            # announced on or before it (method="ffill" on sorted indexes).
            aligned = fin.reindex(dts, method="ffill")
        except Exception:
            return None
        aligned.index = pd.MultiIndex.from_product([dts, [code]], names=["datetime", "code"])
        return aligned

    _workers = min(16, (os.cpu_count() or 4) * 2)
    with ThreadPoolExecutor(max_workers=_workers) as _ex:
        frames = [f for f in _ex.map(_one, codes) if f is not None]
    import logging
    logging.getLogger("financial_analyst.zoo").debug(
        "financials merged for %d/%d codes", len(frames), len(codes),
    )
    if not frames:
        return
    fin_all = pd.concat(frames)
    fin_all = fin_all[~fin_all.index.duplicated(keep="last")]
    # align by (datetime, code); codes/dates absent from financials become NaN
    for col in _FINANCIAL_FIELDS:
        if col in fin_all.columns:
            panel[col] = fin_all[col].reindex(panel.index)


def _apply_fund_flow(panel: pd.DataFrame, ff_df) -> None:
    """把长格式东财资金流 ``ff_df`` 精确合并到 (datetime, code) 面板,IN PLACE。

    精确 (trade_date, code) 匹配 —— **不 ffill**(资金流是当日流量,缺失日保持
    NaN,绝不沿用陈旧流量)。无论 ff_df 是否为空,10 个 ``_FUND_FLOW_FIELDS`` 列
    都会建出(未匹配处 NaN),使 DSL 因子求值为 NaN 而非 NameError。PIT:数据当日
    EOD 可见,与 volume/amount 放置口径一致,不看未来。"""
    for col in _FUND_FLOW_FIELDS:
        panel[col] = np.nan
    if ff_df is None or len(ff_df) == 0:
        return
    ff = ff_df.copy()
    # .astype(str) 先字符串化:对 int 日期(20260617,Task 2 读 parquet 可能给)防被当
    # 纳秒解析致整列错位全 NaN;对 datetime/str 列同样正确(20260617→'20260617'→日期)。
    ff["__dt"] = pd.to_datetime(ff["trade_date"].astype(str))
    ff = ff.set_index(["__dt", "code"])
    ff.index = ff.index.set_names(["datetime", "code"])
    ff = ff[~ff.index.duplicated(keep="last")]
    for col in _FUND_FLOW_FIELDS:
        if col in ff.columns:
            panel[col] = ff[col].reindex(panel.index)


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

    # --- financial-statement fields (W1b, PIT as-of ann_date; NaN when absent) ---
    @property
    def roe(self) -> pd.Series:
        """净资产收益率 = 净利润(累计) / 归母股东权益(报告期累计口径, 未年化)."""
        return self._optional_col("roe")

    @property
    def roa(self) -> pd.Series:
        """总资产收益率 = 净利润(累计) / 总资产."""
        return self._optional_col("roa")

    @property
    def net_margin(self) -> pd.Series:
        """净利率 = 净利润(累计) / 营业收入(累计)."""
        return self._optional_col("net_margin")

    @property
    def rev_yoy(self) -> pd.Series:
        """营业收入同比增速 (同期对比, 季度口径)."""
        return self._optional_col("rev_yoy")

    @property
    def np_yoy(self) -> pd.Series:
        """净利润同比增速 (同期对比, 季度口径)."""
        return self._optional_col("np_yoy")

    @property
    def debt_ratio(self) -> pd.Series:
        """资产负债率 = 总负债 / 总资产."""
        return self._optional_col("debt_ratio")

    @property
    def eps(self) -> pd.Series:
        """基本每股收益 (累计, 元)."""
        return self._optional_col("eps")

    @property
    def net_income(self) -> pd.Series:
        """净利润 (累计, 元) — 原始量, 供与 total_mv 组合成盈利收益率等."""
        return self._optional_col("net_income")

    @property
    def revenue(self) -> pd.Series:
        """营业收入 (累计, 元) — 原始量."""
        return self._optional_col("revenue")

    @property
    def total_equity(self) -> pd.Series:
        """归母股东权益 (元) — 原始量, 供与 total_mv 组合成账面市值比等."""
        return self._optional_col("total_equity")

    @property
    def cfo(self) -> pd.Series:
        """经营活动现金流净额 (累计, 元) — 供与 net_income 组合成现金含量等."""
        return self._optional_col("cfo")

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
            # 精算技术指标(RSI/MACD/Amihud/动量)— loader 有 fetch_tech 才生效,缺则 NaN。
            _merge_tech(panel, loader, codes, start, end)
            # W1b: merge financial-statement fields (quarterly, PIT as-of ann_date).
            _merge_financials(panel, loader, codes, start, end)

        return cls(panel)
