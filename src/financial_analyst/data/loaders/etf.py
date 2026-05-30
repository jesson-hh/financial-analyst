"""ETFLoader — read API for the ETF data layer (Task 7).

Reads parquet files produced by earlier ETF data tasks:
  etf_basic, etf_nav, etf_share, etf_holdings, etf_div, etf_index, etf_spot

and the Qlib binary files under ``cn_data_etf`` for OHLCV quotes.

Code-format convention
----------------------
* ``etf_basic / etf_nav / etf_share / etf_holdings / etf_div / etf_index``
  store **Tushare format** ``510300.SH``  (written via ``_to_tushare_code``).
* ``etf_spot`` stores **Qlib format** ``SH510300``  (written by etf_spot.py).

So all parquet lookups except ``etf_spot`` go through ``self._ts(code)``
(→ ``510300.SH``), while ``etf_spot`` is queried with the raw qlib ``code``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from financial_analyst.data.loaders.tushare import TushareLoader
from financial_analyst.data.paths import get_data_paths


class ETFLoader:
    """Read-only API for ETF data stored in parquet + Qlib binary files.

    Parameters
    ----------
    parquet_root:
        Directory that contains ``etf_*.parquet`` files.  Defaults to
        ``get_data_paths().parquet_root``.
    etf_qlib_uri:
        Root of the ``cn_data_etf`` Qlib binary directory.  Defaults to
        ``get_data_paths().qlib_etf``.
    """

    def __init__(
        self,
        parquet_root: Optional[Path] = None,
        etf_qlib_uri: Optional[Path] = None,
    ) -> None:
        p = get_data_paths()
        self.parquet_root = Path(parquet_root or p.parquet_root)
        self.etf_qlib_uri = Path(etf_qlib_uri or p.qlib_etf)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pq(self, name: str) -> pd.DataFrame:
        """Load a named parquet file; return empty DataFrame if missing."""
        f = self.parquet_root / f"{name}.parquet"
        return pd.read_parquet(f) if f.exists() else pd.DataFrame()

    def _ts(self, code: str) -> str:
        """Convert qlib code to Tushare format: SH510300 -> 510300.SH."""
        return TushareLoader._to_tushare_code(code)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_etf_quote(
        self, code: str, start: str, end: str, freq: str = "day"
    ) -> pd.DataFrame:
        """Fetch OHLCV bars from the ETF Qlib binary store.

        Returns an empty DataFrame if ``etf_qlib_uri`` does not exist
        (e.g. in unit-test tmp_path fixtures).
        """
        if not self.etf_qlib_uri.exists():
            return pd.DataFrame()
        from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
        loader = QlibBinaryLoader(provider_uri=str(self.etf_qlib_uri))
        return loader.fetch_quote(code, start, end, freq)

    def fetch_etf_meta(self, code: str) -> dict:
        """Return fund metadata from ``etf_basic.parquet``.

        Returns an empty dict if the parquet is missing or the code is absent.
        Computes ``total_fee = m_fee + c_fee`` (management + custodian fee).
        """
        b = self._pq("etf_basic")
        if b.empty or "ts_code" not in b.columns:
            return {}
        row = b[b["ts_code"] == self._ts(code)]
        if row.empty:
            return {}
        r = row.iloc[0]
        mf = float(r.get("m_fee") or 0)
        cf = float(r.get("c_fee") or 0)
        return {
            "name": r.get("name"),
            "m_fee": mf,
            "c_fee": cf,
            "total_fee": round(mf + cf, 10),
            "benchmark": r.get("benchmark"),
            "index_code": r.get("index_code"),
            "fund_type": r.get("fund_type"),
            "invest_type": r.get("invest_type"),
        }

    def fetch_etf_premium_discount(self, code: str) -> dict:
        """Return latest realtime premium/discount from ``etf_spot.parquet``.

        ``etf_spot`` stores codes in **Qlib format** (``SH510300``), so we
        query with the raw ``code`` — no ``_ts()`` conversion.

        Returns ``{"realtime_premium_discount_pct": None}`` if unavailable.
        """
        s = self._pq("etf_spot")
        row = s[s["ts_code"] == code] if not s.empty and "ts_code" in s.columns else pd.DataFrame()
        rt = float(row.iloc[0]["premium_discount_pct"]) if not row.empty else None
        return {"realtime_premium_discount_pct": rt}

    def fetch_etf_nav(
        self,
        code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Return NAV time series from ``etf_nav.parquet``, sorted by date.

        Optionally filtered to ``[start, end]`` inclusive (string dates).
        """
        n = self._pq("etf_nav")
        if n.empty or "ts_code" not in n.columns:
            return pd.DataFrame()
        n = n[n["ts_code"] == self._ts(code)]
        if n.empty:
            return n.reset_index(drop=True)
        if "nav_date" in n.columns:
            n = n.sort_values("nav_date")
        if start:
            n = n[n["nav_date"] >= start]
        if end:
            n = n[n["nav_date"] <= end]
        return n.reset_index(drop=True)

    def fetch_etf_holdings(self, code: str, top_n: int = 10) -> dict:
        """Return latest-quarter top-N holdings from ``etf_holdings.parquet``.

        Returns ``{"end_date": None, "holdings": []}`` when no data.
        Holdings list is sorted descending by ``stk_mkv_ratio`` (or ``mkv``
        if the ratio column is absent).
        """
        h = self._pq("etf_holdings")
        if h.empty or "ts_code" not in h.columns:
            return {"end_date": None, "holdings": []}
        h = h[h["ts_code"] == self._ts(code)]
        if h.empty:
            return {"end_date": None, "holdings": []}
        latest = h["end_date"].max()
        h = h[h["end_date"] == latest]
        sort_col = "stk_mkv_ratio" if "stk_mkv_ratio" in h.columns else "mkv"
        h = h.sort_values(sort_col, ascending=False).head(top_n)
        holdings = [
            {
                "symbol": r.get("symbol"),
                "mkv": r.get("mkv"),
                "ratio": r.get("stk_mkv_ratio"),
            }
            for _, r in h.iterrows()
        ]
        return {"end_date": latest, "holdings": holdings}

    def fetch_etf_flow(self, code: str, lookback: int = 20) -> dict:
        """Return share-change (creation/redemption) and AUM from parquet data.

        ``latest_share_change`` = most recent day's fd_share minus the
        previous day (positive = net creation, negative = net redemption).
        ``aum_latest`` = latest fd_share × latest unit_nav (if nav available).
        ``share_series`` = numpy array of the last ``lookback+1`` fd_share values.

        Returns ``{"latest_share_change": None, "aum_latest": None}`` when
        ``etf_share`` is absent or lacks the ``fd_share`` column.
        """
        sh = self._pq("etf_share")
        if sh.empty or "ts_code" not in sh.columns:
            return {"latest_share_change": None, "aum_latest": None}
        sh = sh[sh["ts_code"] == self._ts(code)]
        if sh.empty or "fd_share" not in sh.columns:
            return {"latest_share_change": None, "aum_latest": None}
        sh = sh.sort_values("trade_date").tail(lookback + 1)
        shares = sh["fd_share"].astype(float).to_numpy()
        change = float(shares[-1] - shares[-2]) if len(shares) >= 2 else 0.0
        # AUM = latest fd_share * latest unit_nav (joined from nav parquet)
        nav = self.fetch_etf_nav(code)
        unit = None
        if not nav.empty and "unit_nav" in nav.columns:
            unit = float(nav.iloc[-1]["unit_nav"])
        # AUM unit: fd_share is in 万份, unit_nav in 元 → product is 万元
        aum = float(shares[-1] * unit) if unit is not None else None
        return {
            "latest_share_change": change,
            "aum_latest": aum,
            "aum_unit": "wan_yuan",
            "share_series": shares.tolist(),
        }

    def fetch_tracking_error(self, code: str, window: int = 60) -> dict:
        """Compute annualised tracking error vs. the benchmark index.

        Requires ``etf_basic`` (for ``index_code``), ``etf_nav`` (fund NAV
        returns), and ``etf_index`` (benchmark close prices).

        Returns ``{"tracking_error_annualized": None, "reason": <str>}``
        when any required data is unavailable.

        Date-join is done by string key (both nav_date and trade_date are
        Tushare YYYYMMDD strings) so that independent date sets in NAV and
        index are aligned correctly before computing return differences.
        """
        meta = self.fetch_etf_meta(code)
        idx = meta.get("index_code")
        nav = self.fetch_etf_nav(code)
        if nav.empty or not idx:
            return {"tracking_error_annualized": None, "reason": "no nav or index_code"}
        ei = self._pq("etf_index")
        if ei.empty:
            return {"tracking_error_annualized": None, "reason": "no etf_index"}
        ei = ei[ei["ts_code"] == idx]
        if ei.empty:
            return {"tracking_error_annualized": None, "reason": "index not in etf_index"}
        nav_s = nav.dropna(subset=["nav_date", "unit_nav"]).copy()
        nav_s = nav_s.assign(_d=nav_s["nav_date"].astype(str)).set_index("_d")["unit_nav"].astype(float).sort_index()
        idx_s = ei.dropna(subset=["trade_date", "close"]).copy()
        idx_s = idx_s.assign(_d=idx_s["trade_date"].astype(str)).set_index("_d")["close"].astype(float).sort_index()
        merged = pd.DataFrame({"nav": nav_s, "idx": idx_s}).dropna().sort_index().tail(window + 1)
        if len(merged) < 6:
            return {"tracking_error_annualized": None, "reason": "insufficient overlap"}
        nav_ret = merged["nav"].pct_change().dropna().to_numpy()
        idx_ret = merged["idx"].pct_change().dropna().to_numpy()
        diff = nav_ret - idx_ret
        te = float(np.std(diff, ddof=1) * np.sqrt(252))
        return {"tracking_error_annualized": te, "window": int(len(diff))}
