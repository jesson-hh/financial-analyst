from __future__ import annotations
from typing import Any, Dict, Optional
import pandas as pd
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.data.loader_factory import get_default_loader
from financial_analyst.data import f10_corpus
from financial_analyst.data import stock_basic


def _safe_float(val) -> Optional[float]:
    """Coerce to float; return None for None/NaN/non-numeric values."""
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
    f10_valuation: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    industry: Optional[str] = None
    area: Optional[str] = None
    market: Optional[str] = None
    list_date: Optional[str] = None


class QuoteFetcher(SubAgent[QuoteOutput]):
    NAME = "quote-fetcher"
    OUTPUT_SCHEMA = QuoteOutput

    def __init__(self, memory_root, loader=None):
        super().__init__(memory_root=memory_root)
        self._loader = loader

    def _get_loader(self):
        return self._loader or get_default_loader()

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        code = inputs["code"]
        asof = inputs["asof_date"]
        from datetime import datetime, timedelta
        end_dt = datetime.strptime(asof, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=120)
        loader = self._get_loader()
        quote = loader.fetch_quote(code, start_dt.strftime("%Y-%m-%d"), asof)
        db = loader.fetch_daily_basic(code, (end_dt - timedelta(days=20)).strftime("%Y-%m-%d"), asof)

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
            total_mv = _safe_float(row.get("total_mv"))
            circ_mv = _safe_float(row.get("circ_mv"))
            out.update({
                "pe": _safe_float(row.get("pe_ttm")),
                "pb": _safe_float(row.get("pb")),
                "ps": _safe_float(row.get("ps_ttm")),
                "dv": _safe_float(row.get("dv_ttm")),
                "mv_yi": total_mv / 10000 if total_mv is not None else None,
                "circ_mv_yi": circ_mv / 10000 if circ_mv is not None else None,
                "turnover_rate": _safe_float(row.get("turnover_rate")),
            })
        # F10 兜底:daily_basic 缺失/为空时,用确定性 F10 估值(灭②市值误判)
        if out.get("mv_yi") is None:
            try:
                v = f10_corpus.load_facts(code, asof).valuation
            except Exception:
                v = None
            if v:
                price = out["price"]
                ts = v.get("total_shares")
                fs = v.get("float_shares")
                bvps = v.get("bvps")
                if ts:
                    out["mv_yi"] = round(ts * price / 1e8, 4)
                if fs:
                    out["circ_mv_yi"] = round(fs * price / 1e8, 4)
                if bvps:
                    out["pb"] = round(price / bvps, 4)
                out["f10_valuation"] = v   # 透传真营收/净利/ROE 供下游
        # 本票身份注入(供所有下游 agent 在 prompt 里看到本票名称/行业/地区)
        try:
            b = stock_basic.get_basic(code)
        except Exception:
            b = None
        if b:
            out.update({
                "name": b.get("name"),
                "industry": b.get("industry"),
                "area": b.get("area"),
                "market": b.get("market"),
                "list_date": b.get("list_date"),
            })
        return out
