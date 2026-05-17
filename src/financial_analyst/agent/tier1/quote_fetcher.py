from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.data.loaders.tushare import TushareLoader


class QuoteOutput(BaseModel):
    code: str
    asof_date: str
    price: float
    pe: Optional[float] = None
    pb: Optional[float] = None
    ps: Optional[float] = None
    dv: Optional[float] = None
    mv_yi: Optional[float] = None
    circ_mv_yi: Optional[float] = None
    turnover_rate: Optional[float] = None
    ret_5d: Optional[float] = None
    ret_20d: Optional[float] = None
    ret_60d: Optional[float] = None
    ma5: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    volatility_20d: Optional[float] = None
    volume_ratio: Optional[float] = None


class QuoteFetcher(SubAgent[QuoteOutput]):
    NAME = "quote-fetcher"
    OUTPUT_SCHEMA = QuoteOutput

    def __init__(self, memory_root, loader=None):
        super().__init__(memory_root=memory_root)
        self._loader = loader

    def _get_loader(self):
        return self._loader or TushareLoader()

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        code = inputs["code"]
        asof = inputs["asof_date"]
        from datetime import datetime, timedelta
        end_dt = datetime.strptime(asof, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=120)
        loader = self._get_loader()
        quote = loader.fetch_quote(code, start_dt.strftime("%Y-%m-%d"), asof)
        db = loader.fetch_daily_basic(code, (end_dt - timedelta(days=5)).strftime("%Y-%m-%d"), asof)

        if quote is None or quote.empty:
            raise ValueError(f"no quote data for {code}")

        close = quote["close"]
        out = {
            "code": code,
            "asof_date": asof,
            "price": float(close.iloc[-1]),
            "ret_5d": float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else None,
            "ret_20d": float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else None,
            "ret_60d": float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) >= 61 else None,
            "ma5": float(close.iloc[-5:].mean()),
            "ma20": float(close.iloc[-20:].mean()) if len(close) >= 20 else None,
            "ma60": float(close.iloc[-60:].mean()) if len(close) >= 60 else None,
            "volatility_20d": float(close.pct_change().iloc[-20:].std()) if len(close) >= 20 else None,
            "volume_ratio": float(quote["vol"].iloc[-1] / quote["vol"].iloc[-20:].mean()) if len(quote) >= 20 else None,
        }
        if db is not None and not db.empty:
            row = db.iloc[-1]
            out.update({
                "pe": float(row["pe_ttm"]) if "pe_ttm" in row and row["pe_ttm"] is not None else None,
                "pb": float(row["pb"]) if "pb" in row else None,
                "ps": float(row["ps_ttm"]) if "ps_ttm" in row else None,
                "dv": float(row["dv_ttm"]) if "dv_ttm" in row else None,
                "mv_yi": float(row["total_mv"]) / 100000 if "total_mv" in row else None,
                "circ_mv_yi": float(row["circ_mv"]) / 100000 if "circ_mv" in row else None,
                "turnover_rate": float(row["turnover_rate"]) if "turnover_rate" in row else None,
            })
        return out
