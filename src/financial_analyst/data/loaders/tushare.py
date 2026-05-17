from __future__ import annotations
import os
from typing import Dict, List, Optional
import pandas as pd
import tushare as ts
from financial_analyst.data.loaders.base import BaseLoader


class TushareLoader(BaseLoader):
    def __init__(self, token: Optional[str] = None):
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        token = token or os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise ValueError("TUSHARE_TOKEN missing (env or constructor)")
        ts.set_token(token)
        self._pro = ts.pro_api()

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
        df = self._pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        return self._normalize(df)

    def fetch_daily_basic(self, code: str, start: str, end: str) -> pd.DataFrame:
        ts_code = self._to_tushare_code(code)
        start = start.replace("-", "")
        end = end.replace("-", "")
        df = self._pro.daily_basic(
            ts_code=ts_code, start_date=start, end_date=end,
            fields="ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv,turnover_rate",
        )
        return self._normalize(df)

    def fetch_financials(self, code: str) -> pd.DataFrame:
        ts_code = self._to_tushare_code(code)
        df = self._pro.fina_indicator(ts_code=ts_code)
        return df.sort_values("end_date", ascending=False).reset_index(drop=True) if df is not None else df

    def fetch_news(self, code: str, days: int = 30) -> List[Dict]:
        return []
