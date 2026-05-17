from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.factors.core import compute_factors
from financial_analyst.factors.whale import compute_whale_signals
from financial_analyst.factors.sentiment import score_board, compute_vol_regime
from financial_analyst.data.loaders.tushare import TushareLoader


class FactorOutput(BaseModel):
    code: str
    asof_date: str
    factor_scores: Dict[str, float]
    whale_signals: Dict[str, Any]
    board_score: Dict[str, Any]
    vol_regime: Dict[str, Any]


class FactorComputer(SubAgent[FactorOutput]):
    NAME = "factor-computer"
    OUTPUT_SCHEMA = FactorOutput

    def __init__(self, memory_root, loader=None):
        super().__init__(memory_root=memory_root)
        self._loader = loader

    def _get_loader(self):
        return self._loader or TushareLoader()

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        code, asof = inputs["code"], inputs["asof_date"]
        end_dt = datetime.strptime(asof, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=180)
        loader = self._get_loader()
        quote = loader.fetch_quote(code, start_dt.strftime("%Y-%m-%d"), asof)
        db = loader.fetch_daily_basic(code, start_dt.strftime("%Y-%m-%d"), asof)

        factors = compute_factors(quote)
        whale = compute_whale_signals(quote)
        tr_latest = float(db["turnover_rate"].iloc[-1]) if db is not None and not db.empty else 5.0
        mv_yi = float(db["total_mv"].iloc[-1]) / 10000 if (db is not None and not db.empty and "total_mv" in db.columns) else 200.0
        board = score_board(quote, turnover_rate=tr_latest, market_cap_yi=mv_yi)
        regime = compute_vol_regime(
            close_day=quote["close"],
            turnover_rate_day=(db["turnover_rate"] if db is not None and not db.empty else quote["vol"] / quote["vol"].mean() * 5),
        )

        return {
            "code": code, "asof_date": asof,
            "factor_scores": {k: (float(v) if v is not None and v == v else 0.0) for k, v in factors.items()},
            "whale_signals": whale,
            "board_score": board,
            "vol_regime": regime,
        }
