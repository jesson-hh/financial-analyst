from __future__ import annotations
import os
from typing import Dict, List, Optional
import pandas as pd
import requests
from financial_analyst.data.loaders.base import BaseLoader

TUSHARE_URL = "http://api.tushare.pro"


class TushareLoader(BaseLoader):
    """Tushare data loader using raw requests (bypasses the ``tushare`` Python
    library, which round-robins between api.tushare.pro and api.waditu.com and
    times out behind corporate proxies). Forces HTTP to avoid HTTPS interception.
    """

    def __init__(self, token: Optional[str] = None, url: str = TUSHARE_URL, timeout: int = 30):
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        token = token or os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise ValueError("TUSHARE_TOKEN missing (env or constructor)")
        self._token = token
        self._url = url
        self._timeout = timeout
        self._pro = self._make_pro_shim()

    def _query(self, api_name: str, fields: str = "", **params) -> pd.DataFrame:
        req = {"api_name": api_name, "token": self._token, "params": params}
        if fields:
            req["fields"] = fields
        r = requests.post(self._url, json=req, timeout=self._timeout)
        d = r.json()
        if d.get("code") != 0:
            raise Exception(f"tushare {api_name} failed: {d.get('msg', '')}")
        return pd.DataFrame(d["data"]["items"], columns=d["data"]["fields"])

    def _make_pro_shim(self):
        """Returns a shim object exposing tushare-library-shaped methods.
        Kept for back-compat with existing tests that patch ``loader._pro.daily``.
        """
        loader = self

        class _ProShim:
            def daily(self, **kw):
                return loader._query("daily", **kw)

            def daily_basic(self, **kw):
                fields = kw.pop("fields", "")
                return loader._query("daily_basic", fields=fields, **kw)

            def fina_indicator(self, **kw):
                return loader._query("fina_indicator", **kw)

        return _ProShim()

    @staticmethod
    def _to_tushare_code(code: str) -> str:
        code = code.strip().upper()
        if "." in code:
            return code
        prefix, body = code[:2], code[2:]
        suffix = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}.get(prefix, prefix)
        return f"{body}.{suffix}"

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        if "trade_date" in df.columns:
            df = df.copy()
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
        return df

    def supports(self, market: str) -> bool:
        return market == "a_share"

    def fetch_quote(self, code: str, start: str, end: str) -> pd.DataFrame:
        ts_code = self._to_tushare_code(code)
        start = start.replace("-", "")
        end = end.replace("-", "")
        df = self._query("daily", ts_code=ts_code, start_date=start, end_date=end)
        return self._normalize(df)

    def fetch_daily_basic(self, code: str, start: str, end: str) -> pd.DataFrame:
        ts_code = self._to_tushare_code(code)
        start = start.replace("-", "")
        end = end.replace("-", "")
        df = self._query(
            "daily_basic",
            fields="ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv,turnover_rate",
            ts_code=ts_code, start_date=start, end_date=end,
        )
        return self._normalize(df)

    def fetch_financials(self, code: str) -> pd.DataFrame:
        ts_code = self._to_tushare_code(code)
        df = self._query("fina_indicator", ts_code=ts_code)
        if df is None or df.empty:
            return df
        return df.sort_values("end_date", ascending=False).reset_index(drop=True)

    def fetch_news(self, code: str, days: int = 30) -> List[Dict]:
        return []
