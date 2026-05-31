"""EtfQuoteFetcher — tier-1 ETF quote + meta data agent."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from pydantic import BaseModel

from financial_analyst.agent.base import SubAgent


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class EtfQuoteOutput(BaseModel):
    code: str
    asof_date: str
    close: Optional[float] = None
    # meta
    name: Optional[str] = None
    m_fee: Optional[float] = None
    c_fee: Optional[float] = None
    total_fee: Optional[float] = None
    benchmark: Optional[str] = None
    index_code: Optional[str] = None
    fund_type: Optional[str] = None
    # derived price/technical
    ret_5d: Optional[float] = None
    ret_20d: Optional[float] = None
    ret_60d: Optional[float] = None
    ma5: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    volatility: Optional[float] = None
    volume_ratio: Optional[float] = None


class EtfQuoteFetcher(SubAgent[EtfQuoteOutput]):
    NAME = "etf-quote-fetcher"
    OUTPUT_SCHEMA = EtfQuoteOutput

    def __init__(self, memory_root: Path, loader=None):
        super().__init__(memory_root=memory_root)
        self._loader = loader

    def _get_loader(self):
        if self._loader is not None:
            return self._loader
        from financial_analyst.data.loaders.etf import ETFLoader
        return ETFLoader()

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        code = inputs["code"]
        asof = inputs["asof_date"]
        end_dt = datetime.strptime(asof, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=120)
        loader = self._get_loader()

        quote = loader.fetch_etf_quote(
            code, start_dt.strftime("%Y-%m-%d"), asof
        )
        meta = loader.fetch_etf_meta(code)

        out: Dict[str, Any] = {"code": code, "asof_date": asof}

        # meta fields
        out.update({
            "name": meta.get("name"),
            "m_fee": _safe_float(meta.get("m_fee")),
            "c_fee": _safe_float(meta.get("c_fee")),
            "total_fee": _safe_float(meta.get("total_fee")),
            "benchmark": meta.get("benchmark"),
            "index_code": meta.get("index_code"),
            "fund_type": meta.get("fund_type"),
        })

        if quote is None or quote.empty:
            return out

        close = quote["close"].astype(float)
        n = len(close)

        out["close"] = float(close.iloc[-1])
        out["ret_5d"] = float(close.iloc[-1] / close.iloc[-6] - 1) if n >= 6 else None
        out["ret_20d"] = float(close.iloc[-1] / close.iloc[-21] - 1) if n >= 21 else None
        out["ret_60d"] = float(close.iloc[-1] / close.iloc[-61] - 1) if n >= 61 else None
        out["ma5"] = float(close.iloc[-min(5, n):].mean())
        out["ma20"] = float(close.iloc[-20:].mean()) if n >= 20 else None
        out["ma60"] = float(close.iloc[-60:].mean()) if n >= 60 else None
        out["volatility"] = float(close.pct_change().iloc[-20:].std()) if n >= 20 else None

        if "vol" in quote.columns and n >= 20:
            vol = quote["vol"].astype(float)
            out["volume_ratio"] = float(vol.iloc[-1] / vol.iloc[-20:].mean())
        else:
            out["volume_ratio"] = None

        return out
