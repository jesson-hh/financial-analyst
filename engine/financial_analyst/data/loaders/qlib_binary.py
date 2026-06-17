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

# Precomputed technical-indicator fields (day freq only; same .bin layout as quote).
# Surfaced so DSL formulas can use exact RSI/MACD/Amihud/momentum instead of reconstructing.
TECH_FIELD_MAP: Dict[str, str] = {
    "rsi_14": "rsi_14",            # Wilder RSI(14)
    "macd_signal": "macd_signal",  # MACD 信号线
    "amihud_20": "amihud_20",      # Amihud 非流动性(20日)
    "mom_20": "mom_20",            # 20日动量
    "mom_60": "mom_60",            # 60日动量
    "mom_120": "mom_120",          # 120日动量
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

# Financial-statement output fields (derived ratios + raw stocks), PIT-stamped at
# the announcement date (ann_date). Quality: roe/roa/net_margin; Growth (period-
# matched YoY on YTD-cumulative flows): rev_yoy/np_yoy; Leverage: debt_ratio;
# Per-share: eps; raw stocks for composable ratios: net_income/revenue/total_equity/cfo.
FINANCIAL_FIELDS: tuple = (
    "roe", "roa", "net_margin", "rev_yoy", "np_yoy",
    "debt_ratio", "eps", "net_income", "revenue", "total_equity", "cfo",
)


def _normalize_vol_units(df: "pd.DataFrame") -> "pd.DataFrame":
    """单位自检校准(2026-06-12 量纲跳变修复):vol 应为「股」、amount 应为「元」。

    历史增量批次混入 tushare 风格单位(vol=手,或 vol=手+amount=千元),致 K 线 VOL
    柱视觉消失、turnover 量比因子虚高 ~100 倍。用 amount/close 隐含股数自检:
    r=(amount/close)/vol —— ≈1 正常;∈[50,200] → vol×100;∈[0.05,0.2] → vol×100
    且 amount×1000。正常 bar 的 r≈VWAP/close∈[0.9,1.1],与检测带无重叠零误伤。
    NaN/零量/缺列原样;任何异常原样返回(校准绝不挡数据)。
    """
    try:
        if df is None or len(df) == 0:
            return df
        for col in ("close", "vol", "amount"):
            if col not in df.columns:
                return df
        c, v, a = df["close"], df["vol"], df["amount"]
        ok = c.notna() & v.notna() & a.notna() & (c > 0) & (v > 0) & (a > 0)
        if not bool(ok.any()):
            return df
        r = pd.Series(float("nan"), index=df.index, dtype="float64")
        r[ok] = (a[ok] / c[ok]) / v[ok]
        hand = ok & r.between(50.0, 200.0)            # vol=手
        dual = ok & r.between(0.05, 0.2)              # vol=手 且 amount=千元
        if bool(hand.any()):
            df.loc[hand, "vol"] = v[hand] * 100.0
        if bool(dual.any()):
            df.loc[dual, "vol"] = v[dual] * 100.0
            df.loc[dual, "amount"] = a[dual] * 1000.0
        return df
    except Exception:  # noqa: BLE001 — 校准自身故障绝不挡数据
        return df


def _to_tushare_code(code: str) -> str:
    """Qlib code (``SH600519`` / ``SZ000001``) → Tushare ts_code
    (``600519.SH`` / ``000001.SZ``). Values already in ts_code form pass through."""
    c = str(code).strip().upper()
    if "." in c:
        return c
    if len(c) > 2 and c[:2] in ("SH", "SZ", "BJ"):
        return f"{c[2:]}.{c[:2]}"
    return c


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
        # Lazily-built financial-statement index ({ts_code: ann_date-indexed frame});
        # read once from parquet, then per-code lookups are O(1). Thread-safe because
        # _merge_financials fans the per-code fetch out over a thread pool.
        self._fin_cache: Optional[Dict[str, "pd.DataFrame"]] = None
        self._fin_lock = threading.Lock()

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
        df = _normalize_vol_units(self._build_df(code, QUOTE_FIELD_MAP, start, end, freq=freq))
        if freq != "day":
            df = self._crosscheck_intraday_vol(code, df)
        return df

    def _crosscheck_intraday_vol(self, code: str, df: pd.DataFrame) -> pd.DataFrame:
        """分钟 bar 的第二道量纲校准(2026-06-12):amount 整日全缺时 r 自检失明
        (``_normalize_vol_units`` 需 amount>0 才能算 (amount/close)/vol),此时改用
        **日线交叉定标**——日线 vol 已校准为「股」,若分钟 bar 当日合计恰为其
        1/50~1/200(即「手」批次),整日 vol ×100。日线缺该日(如盘中今日)或
        比值≈1(本就是股)→ 原样不动;任何异常原样返回(校准绝不挡数据)。"""
        try:
            if df is None or len(df) == 0:
                return df
            for col in ("vol", "trade_date"):
                if col not in df.columns:
                    return df
            day = df["trade_date"].astype(str).str[:10]
            amt_ok = (
                (df["amount"].notna() & (df["amount"] > 0))
                if "amount" in df.columns else pd.Series(False, index=df.index)
            )
            sus = [d for d, idx in df.groupby(day).groups.items()
                   if not bool(amt_ok.loc[idx].any())]
            if not sus:
                return df
            ref = self.fetch_quote(code, min(sus), max(sus), "day")
            if ref is None or len(ref) == 0 or "vol" not in ref.columns:
                return df
            ref_vol = {str(td)[:10]: float(v) for td, v in zip(ref["trade_date"], ref["vol"])
                       if v == v and v > 0}
            for d in sus:
                m = day == d
                s = float(df.loc[m, "vol"].sum())
                dv = ref_vol.get(d)
                if s > 0 and dv and 50.0 <= (dv / s) <= 200.0:
                    df.loc[m, "vol"] = df.loc[m, "vol"] * 100.0
            return df
        except Exception:  # noqa: BLE001 — 校准自身故障绝不挡数据
            return df

    def fetch_daily_basic(self, code: str, start: str, end: str) -> pd.DataFrame:
        # daily_basic fields only exist at the day frequency in this Qlib layout.
        return self._build_df(code, DAILY_BASIC_FIELD_MAP, start, end, freq="day")

    def fetch_tech(self, code: str, start: str, end: str) -> pd.DataFrame:
        """Precomputed technical indicators (RSI/MACD/Amihud/momentum) — day freq only.
        Same graceful contract as fetch_daily_basic: a missing .bin → absent column → NaN."""
        return self._build_df(code, TECH_FIELD_MAP, start, end, freq="day")

    def fetch_financials(self, code: str) -> pd.DataFrame:
        """Return point-in-time financial-statement fields for one code, indexed
        by announcement date (``ann_date``, datetime). Columns = FINANCIAL_FIELDS
        (derived ratios + raw stocks). Empty DataFrame when financials are
        unavailable for this code.

        Each row is effective from its ann_date (publication day), so a downstream
        as-of/ffill alignment onto the trading calendar never leaks future
        fundamentals (no look-ahead). Reads three Tushare-style parquet tables
        under ``<day_root>/../parquet/financial/`` — built once, then cached."""
        idx = self._load_financials_all()
        if not idx:
            return pd.DataFrame()
        g = idx.get(_to_tushare_code(code))
        return g.copy() if g is not None else pd.DataFrame()

    def _load_financials_all(self) -> Dict[str, "pd.DataFrame"]:
        """Thread-safe lazy accessor for the per-ts_code financial index.
        Empty dict when the parquet directory is absent/unreadable (honest
        degrade: callers then get NaN columns rather than a crash)."""
        cached = self._fin_cache
        if cached is not None:
            return cached
        with self._fin_lock:
            if self._fin_cache is None:
                self._fin_cache = self._build_financials_index()
            return self._fin_cache

    def _build_financials_index(self) -> Dict[str, "pd.DataFrame"]:
        """Read profit/balance/cash parquet once, derive ratios + period-matched
        YoY, and group by ts_code into ann_date-indexed frames. Any failure →
        ``{}`` (financials simply absent)."""
        import logging
        log = logging.getLogger("financial_analyst.loader")
        fin_dir = self._roots["day"].parent / "parquet" / "financial"
        try:
            def _read(name: str, cols: list) -> pd.DataFrame:
                df = pd.read_parquet(fin_dir / name, columns=cols)
                for c in ("ts_code", "ann_date", "end_date"):
                    if c in df.columns:
                        df[c] = df[c].astype(str)
                # strict PIT: one row per (code, period) = earliest announcement
                # (ignore later restatements → value known as of original ann_date,
                #  never look-ahead).
                df = df.sort_values(["ts_code", "end_date", "ann_date"]).drop_duplicates(
                    ["ts_code", "end_date"], keep="first")
                return df

            prof = _read("profit_sheet.parquet",
                         ["ts_code", "ann_date", "end_date", "revenue", "n_income", "basic_eps"])
            bal = _read("balance_sheet.parquet",
                        ["ts_code", "ann_date", "end_date", "total_assets", "total_liab",
                         "total_hldr_eqy_exc_min_int"])
            cash = _read("cash_flow.parquet",
                         ["ts_code", "ann_date", "end_date", "n_cashflow_act"])

            for col in ("revenue", "n_income", "basic_eps"):
                prof[col] = pd.to_numeric(prof[col], errors="coerce")
            for col in ("total_assets", "total_liab", "total_hldr_eqy_exc_min_int"):
                bal[col] = pd.to_numeric(bal[col], errors="coerce")
            cash["n_cashflow_act"] = pd.to_numeric(cash["n_cashflow_act"], errors="coerce")

            # Period-matched YoY on YTD-cumulative flows: Q_t vs the same quarter
            # one year earlier (sidesteps the YTD seasonality of a naive diff).
            yr = prof["end_date"].str[:4].astype(int)
            prev_end = (yr - 1).astype(str) + prof["end_date"].str[4:]
            rev_lk = prof.set_index(["ts_code", "end_date"])["revenue"]
            ni_lk = prof.set_index(["ts_code", "end_date"])["n_income"]
            pidx = pd.MultiIndex.from_arrays([prof["ts_code"].to_numpy(), prev_end.to_numpy()])
            with np.errstate(divide="ignore", invalid="ignore"):
                prof["rev_yoy"] = prof["revenue"].to_numpy() / rev_lk.reindex(pidx).to_numpy() - 1.0
                prof["np_yoy"] = prof["n_income"].to_numpy() / ni_lk.reindex(pidx).to_numpy() - 1.0

            bal_k = bal.drop(columns=["ann_date"]).rename(
                columns={"total_hldr_eqy_exc_min_int": "total_equity"})
            cash_k = cash.drop(columns=["ann_date"])
            m = prof.merge(bal_k, on=["ts_code", "end_date"], how="left").merge(
                cash_k, on=["ts_code", "end_date"], how="left")

            with np.errstate(divide="ignore", invalid="ignore"):
                m["roe"] = m["n_income"] / m["total_equity"]
                m["roa"] = m["n_income"] / m["total_assets"]
                m["net_margin"] = m["n_income"] / m["revenue"]
                m["debt_ratio"] = m["total_liab"] / m["total_assets"]
            m["eps"] = m["basic_eps"]
            m["net_income"] = m["n_income"]
            m["cfo"] = m["n_cashflow_act"]
            m["ann_dt"] = pd.to_datetime(m["ann_date"], format="%Y%m%d", errors="coerce")
            m = m.dropna(subset=["ann_dt"]).replace([np.inf, -np.inf], np.nan)

            cols = list(FINANCIAL_FIELDS)
            out: Dict[str, pd.DataFrame] = {}
            for ts, g in m.groupby("ts_code"):
                g = g.sort_values(["ann_dt", "end_date"]).drop_duplicates(
                    "ann_dt", keep="last").set_index("ann_dt")
                out[ts] = g[cols]
            log.info("financials index built: %d codes from %s", len(out), fin_dir)
            return out
        except Exception as e:  # honest degrade — no financials rather than a crash
            log.warning("financials index unavailable under %s: %s", fin_dir, e)
            return {}

    def fetch_news(self, code: str, days: int = 30) -> List[Dict]:
        return []
