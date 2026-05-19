"""Industry classifier loader — maps A-share stock codes to industry
classifications (e.g., 申万 level-1: "白酒", "银行", "通信设备").

Used by ``factors.zoo.operators.indneutralize`` to industry-demean alpha
values cross-sectionally, which unlocks the ~22 alpha101 alphas that
use ``IndClass.subindustry`` and improves cross-sectional signal quality
for all volatility / momentum alphas with industry exposure.

Source: Tushare ``stock_basic(fields='ts_code,name,industry')``. The
result is cached to ``~/.financial-analyst/cache/industry_map.parquet``
so subsequent panels load in milliseconds.

Refresh weekly via ``financial-analyst industry refresh``.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd


def _cache_dir() -> Path:
    p = Path.home() / ".financial-analyst" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def industry_map_path() -> Path:
    return _cache_dir() / "industry_map.parquet"


class IndustryLoader:
    """Lookup industry classification for a stock code.

    Cache layout (parquet)::

        code         | industry    | name         | refreshed_at
        SH600519     | 白酒        | 贵州茅台     | 2026-05-19
        SZ000858     | 白酒        | 五粮液       | 2026-05-19
        ...

    Codes are stored in Qlib convention (``SH600519``); Tushare's
    ``ts_code`` (``600519.SH``) is translated on refresh.
    """

    UNKNOWN_INDUSTRY = "未知"

    def __init__(self, cache_path: Optional[Path] = None):
        self._cache_path = Path(cache_path) if cache_path else industry_map_path()
        self._df: Optional[pd.DataFrame] = None

    def _load_cache(self) -> pd.DataFrame:
        if self._df is None:
            if self._cache_path.exists():
                self._df = pd.read_parquet(self._cache_path)
            else:
                self._df = pd.DataFrame(columns=["code", "industry", "name", "refreshed_at"])
        return self._df

    def get(self, code: str) -> str:
        """Return industry for one code; ``UNKNOWN_INDUSTRY`` if absent."""
        df = self._load_cache()
        match = df[df["code"] == code]
        if match.empty:
            return self.UNKNOWN_INDUSTRY
        return str(match["industry"].iloc[0])

    def get_map(self, codes: Iterable[str]) -> Dict[str, str]:
        """Bulk lookup. Returns ``{code: industry}`` with UNKNOWN_INDUSTRY
        for codes not in the cache.
        """
        df = self._load_cache()
        sub = df[df["code"].isin(codes)]
        out: Dict[str, str] = {c: self.UNKNOWN_INDUSTRY for c in codes}
        for _, row in sub.iterrows():
            out[row["code"]] = row["industry"]
        return out

    def refresh_from_tushare(self) -> int:
        """Pull the full A-share industry map from Tushare and persist.

        Returns the number of codes written.
        """
        import requests
        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError(
                "TUSHARE_TOKEN not set. Add it to .env or env var first."
            )

        # Use the raw HTTP endpoint that the existing TushareLoader uses (the
        # round-robin in the official `tushare` package times out on Windows).
        url = "http://api.tushare.pro"
        payload = {
            "api_name": "stock_basic",
            "token": token,
            "params": {
                "exchange": "",
                "list_status": "L",  # only listed (not delisted)
            },
            "fields": "ts_code,name,industry,market,list_date",
        }
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Tushare returned error: {data.get('msg')}")

        fields = data["data"]["fields"]
        items = data["data"]["items"]
        df = pd.DataFrame(items, columns=fields)

        # Translate ts_code (e.g. "600519.SH") → Qlib code ("SH600519")
        def _to_qlib(ts_code: str) -> str:
            if "." not in ts_code:
                return ts_code
            num, ex = ts_code.split(".")
            return f"{ex.upper()}{num}"

        df["code"] = df["ts_code"].map(_to_qlib)
        df["industry"] = df["industry"].fillna(self.UNKNOWN_INDUSTRY)
        df["refreshed_at"] = pd.Timestamp.now().strftime("%Y-%m-%d")
        out = df[["code", "industry", "name", "refreshed_at"]].copy()

        # Atomic write: tmp + rename
        tmp = self._cache_path.with_suffix(".tmp.parquet")
        out.to_parquet(tmp, index=False)
        tmp.replace(self._cache_path)

        # Invalidate in-memory cache
        self._df = None
        return len(out)

    def stats(self) -> Dict[str, int]:
        df = self._load_cache()
        if df.empty:
            return {"n_codes": 0, "n_industries": 0}
        return {
            "n_codes": int(len(df)),
            "n_industries": int(df["industry"].nunique()),
            "n_unknown": int((df["industry"] == self.UNKNOWN_INDUSTRY).sum()),
        }
